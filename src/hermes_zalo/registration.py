"""Private, administrator-approved roster registrations in a bot-scoped DB.

The caller supplies a freshly validated roster person. An approved record is
the durable member grant; this store never changes static administrator ACLs,
reads Sheets, sends messages, or binds Tickets itself.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid


class RegistrationError(ValueError):
    """Bounded messages safe to show without echoing identities or phone numbers."""


@dataclass(frozen=True)
class RegistrationRecord:
    id: str
    member_id: str
    user_id: str = field(repr=False)
    chat_id: str = field(repr=False)
    phone: str = field(repr=False)
    name: str
    source_key: str = field(repr=False)
    fingerprint: str = field(repr=False)
    name_unique: bool
    status: str
    created: float
    updated: float


class RegistrationStore:
    def __init__(self, path, *, admins=frozenset(), clock=time.time):
        if isinstance(admins, str) or any(not isinstance(admin, str) or not admin for admin in admins):
            raise RegistrationError('Danh sách quản trị đăng ký chưa hợp lệ.')
        self.admins, self.clock = frozenset(admins), clock
        self.lock = threading.RLock()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS registration_members (
                member_id TEXT PRIMARY KEY, source_key TEXT NOT NULL, phone TEXT NOT NULL,
                UNIQUE(source_key,phone));
            CREATE TABLE IF NOT EXISTS registration_requests (
                id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES registration_members(member_id),
                user_id TEXT NOT NULL, chat_id TEXT NOT NULL, phone TEXT NOT NULL,
                name TEXT NOT NULL, source_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                name_unique INTEGER NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, request_key TEXT UNIQUE NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS registration_active_user ON registration_requests(user_id)
                WHERE status IN ('pending','approved');
            CREATE UNIQUE INDEX IF NOT EXISTS registration_active_chat ON registration_requests(chat_id)
                WHERE status IN ('pending','approved');
            CREATE UNIQUE INDEX IF NOT EXISTS registration_active_member ON registration_requests(member_id)
                WHERE status IN ('pending','approved');
            CREATE TABLE IF NOT EXISTS registration_receipts (
                message_key TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES registration_requests(id));
            INSERT OR IGNORE INTO registration_receipts SELECT request_key,id FROM registration_requests;
            CREATE TABLE IF NOT EXISTS registration_attempts (
                message_key TEXT PRIMARY KEY, user_id TEXT NOT NULL, at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS registration_attempt_times ON registration_attempts(at);
            CREATE TABLE IF NOT EXISTS registration_events (
                id INTEGER PRIMARY KEY, request_id TEXT NOT NULL,
                actor TEXT NOT NULL, action TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS registration_member_controls (
                member_id TEXT PRIMARY KEY REFERENCES registration_members(member_id),
                locked INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0,
                updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS registration_admin_audit (
                id INTEGER PRIMARY KEY, member_id TEXT NOT NULL, actor TEXT NOT NULL,
                action TEXT NOT NULL, at REAL NOT NULL, before_json TEXT NOT NULL,
                after_json TEXT NOT NULL);
        ''')

    @contextmanager
    def _transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self):
        with self.lock:
            self.db.close()

    @staticmethod
    def _message(message):
        if (getattr(message, 'chat_type', None) != 'PRIVATE'
                or any(not isinstance(getattr(message, key, None), str)
                       or not 1 <= len(getattr(message, key)) <= 200
                       or any(ord(char) < 32 for char in getattr(message, key))
                       for key in ('user_id', 'chat_id', 'message_id'))):
            raise RegistrationError('Đăng ký và xem thông tin cá nhân chỉ dùng trong tin nhắn riêng với bot.')
        value = [message.user_id, message.chat_id, message.message_id]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _person(person):
        try:
            values = {key: person[key] if isinstance(person, dict) else getattr(person, key)
                      for key in ('phone', 'name', 'source_key', 'fingerprint', 'name_unique')}
        except (KeyError, AttributeError, TypeError):
            raise RegistrationError('Cần tra đúng một người trong danh sách đăng ký trước khi gửi yêu cầu.') from None
        if (not isinstance(values['phone'], str) or not re.fullmatch(r'0[0-9]{9}', values['phone'])
                or type(values['name_unique']) is not bool
                or any(not isinstance(values[key], str) or not values[key].strip()
                       or len(values[key]) > maximum or any(ord(char) < 32 for char in values[key])
                       for key, maximum in (('name', 160), ('source_key', 1024), ('fingerprint', 256)))):
            raise RegistrationError('Thông tin đăng ký từ bảng chưa hợp lệ; nhờ quản trị kiểm tra.')
        return values

    @staticmethod
    def _record(row):
        if row is None:
            return None
        values = {key: row[key] for key in RegistrationRecord.__dataclass_fields__}
        values['name_unique'] = bool(values['name_unique'])
        return RegistrationRecord(**values)

    def _admin(self, admin):
        if not isinstance(admin, str) or admin not in self.admins:
            raise RegistrationError('Bạn chưa được cấp quyền quản trị đăng ký.')

    @staticmethod
    def _request_id(request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'DK-[A-Fa-f0-9]{8}', request_id):
            raise RegistrationError('Mã đăng ký chưa hợp lệ; hãy lấy đúng mã trong danh sách chờ duyệt.')
        return request_id.upper()

    def check_attempt(self, message):
        """Reserve one lookup attempt before any network request; replay is free."""
        message_key = self._message(message)
        with self._transaction() as db:
            if db.execute('SELECT 1 FROM registration_attempts WHERE message_key=?', (message_key,)).fetchone():
                return
            boundary = self.clock() - 3600
            total = db.execute('SELECT COUNT(*) FROM registration_attempts WHERE at>?', (boundary,)).fetchone()[0]
            own = db.execute('SELECT COUNT(*) FROM registration_attempts WHERE user_id=? AND at>?',
                             (message.user_id, boundary)).fetchone()[0]
            if own >= 5 or total >= 60:
                raise RegistrationError('Đã đạt giới hạn tra đăng ký. Vui lòng thử lại sau một giờ.')
            db.execute('INSERT INTO registration_attempts VALUES (?,?,?)', (message_key, message.user_id, self.clock()))

    def request(self, message, person, *, auto_approve=False):
        if type(auto_approve) is not bool:
            raise RegistrationError('Chế độ duyệt đăng ký chưa hợp lệ.')
        message_key, person = self._message(message), self._person(person)
        with self._transaction() as db:
            if self._locked_route(db, message.user_id, message.chat_id):
                raise RegistrationError('Quyền dùng bot đang bị khóa; nhờ quản trị kiểm tra.')
            old = db.execute('SELECT r.* FROM registration_requests r JOIN registration_receipts receipt '
                             'ON receipt.request_id=r.id WHERE receipt.message_key=?', (message_key,)).fetchone()
            if old:
                if (any(old[key] != person[key] for key in ('phone', 'name', 'source_key', 'fingerprint'))
                        or bool(old['name_unique']) != person['name_unique']):
                    raise RegistrationError('Tin nhắn này đã dùng cho nội dung khác; chưa thay đổi đăng ký.')
                return self._finish_request(db, old, auto_approve)
            current = db.execute("SELECT * FROM registration_requests WHERE status IN ('pending','approved') "
                                 'AND (user_id=? OR chat_id=?)', (message.user_id, message.chat_id)).fetchone()
            if current:
                if (current['user_id'] == message.user_id and current['chat_id'] == message.chat_id
                        and all(current[key] == person[key] for key in ('phone', 'name', 'source_key', 'fingerprint'))
                        and bool(current['name_unique']) == person['name_unique']):
                    db.execute('INSERT INTO registration_receipts VALUES (?,?)', (message_key, current['id']))
                    return self._finish_request(db, current, auto_approve)
                raise RegistrationError('Tài khoản hoặc cuộc trò chuyện đã có đăng ký. Nhờ quản trị kiểm tra trước khi thay đổi.')
            member = db.execute('SELECT member_id FROM registration_members WHERE source_key=? AND phone=?',
                                (person['source_key'], person['phone'])).fetchone()
            member_id = member[0] if member else 'member-' + uuid.uuid4().hex
            control = db.execute('SELECT locked FROM registration_member_controls WHERE member_id=?',
                                 (member_id,)).fetchone()
            if control and control['locked']:
                raise RegistrationError('Quyền dùng bot đang bị khóa; nhờ quản trị kiểm tra.')
            if db.execute("SELECT 1 FROM registration_requests WHERE member_id=? AND status IN ('pending','approved')",
                          (member_id,)).fetchone():
                raise RegistrationError('Thông tin này đang có đăng ký khác. Nhờ quản trị kiểm tra.')
            if not member:
                db.execute('INSERT INTO registration_members VALUES (?,?,?)',
                           (member_id, person['source_key'], person['phone']))
            reset_source = self._reset_route(db, message.user_id, message.chat_id)
            for _ in range(64):
                request_id = 'DK-' + secrets.token_hex(4).upper()
                if not db.execute('SELECT 1 FROM registration_requests WHERE id=?', (request_id,)).fetchone():
                    break
            else:
                raise RegistrationError('Chưa cấp được mã đăng ký; vui lòng thử lại sau.')
            # A reset must not hide its replacement if the host clock moved
            # backwards. Request order is also used for admission and locks.
            latest_created = db.execute('SELECT MAX(created) FROM registration_requests '
                                        'WHERE user_id=? OR chat_id=? OR member_id=?',
                                        (message.user_id, message.chat_id, member_id)).fetchone()[0] or 0
            now = max(self.clock(), latest_created)
            db.execute('INSERT INTO registration_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (request_id, member_id, message.user_id, message.chat_id, person['phone'], person['name'],
                        person['source_key'], person['fingerprint'], int(person['name_unique']), 'pending', now, now, message_key))
            db.execute('INSERT INTO registration_receipts VALUES (?,?)', (message_key, request_id))
            if reset_source:
                db.execute('INSERT INTO registration_events(request_id,actor,action,at) VALUES (?,?,?,?)',
                           (request_id, 'system:registration-reset', 'identity_reset_to', now))
                self._audit_admin(db, member_id, 'system:registration-reset', 'identity_reset_to',
                                  {'request_id': reset_source}, {'request_id': request_id, 'member_id': member_id})
            self._bump_revision(db, member_id)
            row = db.execute('SELECT * FROM registration_requests WHERE id=?', (request_id,)).fetchone()
            return self._finish_request(db, row, auto_approve)

    def _finish_request(self, db, row, auto_approve):
        """Approve only inside the conflict-checked request transaction.

        The flag is operator configuration, never a chat argument. Explicit
        administrator rejection/revocation still requires manual reapproval.
        Only an explicit web-admin reset replaces that terminal decision with
        ``reset``; receipts for the old request remain terminal forever.
        """
        if not auto_approve or row['status'] != 'pending':
            return self._record(row)
        for key in ('user_id', 'chat_id', 'member_id'):
            previous = db.execute(
                f"SELECT status,user_id,chat_id FROM registration_requests WHERE {key}=? AND status<>'pending' "
                'ORDER BY updated DESC,rowid DESC LIMIT 1', (row[key],)).fetchone()
            if previous and previous['status'] in ('rejected', 'revoked'):
                return self._record(row)
            if previous and previous['status'] == 'reset' and (
                    previous['user_id'] != row['user_id'] or previous['chat_id'] != row['chat_id']):
                # An administrator resets the captured route, not a blanket
                # permission for another Zalo account to take over its member.
                return self._record(row)
        return self._decision(db, row, 'approved', 'system:workload-auto', action='auto_approved')

    def status(self, message):
        self._message(message)
        with self.lock:
            return self._record(self.db.execute('SELECT * FROM registration_requests WHERE user_id=? AND chat_id=? '
                                               'ORDER BY created DESC,rowid DESC LIMIT 1',
                                               (message.user_id, message.chat_id)).fetchone())

    def approved(self, message):
        record = self.status(message)
        return record if record is not None and record.status == 'approved' and not self.route_locked(message.user_id, message.chat_id) else None

    @staticmethod
    def _locked_route(db, user_id, chat_id=None):
        # Historical routes stay blocked too: a new registration cannot bypass
        # a lock by selecting another phone or changing the private chat route.
        # An explicitly confirmed administrator correction/reset starts a new identity
        # boundary. Locks on the wrong, revoked person must no longer affect the
        # corrected route. Ordinary revoke/re-register never creates this event,
        # and current or later member locks continue to apply independently to
        # both the user and private-chat dimensions (including group admission).
        return bool(db.execute('''
            WITH correction_boundaries AS (
                SELECT COALESCE(MAX(CASE WHEN r.user_id=? THEN r.rowid END),0) AS user_boundary,
                       COALESCE(MAX(CASE WHEN r.chat_id=? THEN r.rowid END),0) AS chat_boundary
                FROM registration_requests r JOIN registration_events e ON e.request_id=r.id
                WHERE e.action IN ('identity_corrected_to','identity_reset_to')
            )
            SELECT 1 FROM registration_requests r JOIN registration_member_controls c ON c.member_id=r.member_id
                CROSS JOIN correction_boundaries b
            WHERE c.locked=1 AND ((r.user_id=? AND r.rowid>=b.user_boundary)
                OR (? IS NOT NULL AND r.chat_id=? AND r.rowid>=b.chat_boundary)) LIMIT 1
            ''', (user_id, chat_id, user_id, chat_id, chat_id)).fetchone())

    def route_locked(self, user_id, chat_id=None):
        with self.lock:
            return self._locked_route(self.db, user_id, chat_id)

    @staticmethod
    def _reset_route(db, user_id, chat_id):
        """A fresh message may replace an explicitly reset, unchanged DM route."""
        user = db.execute('SELECT id,status,chat_id FROM registration_requests WHERE user_id=? '
                          'ORDER BY created DESC,rowid DESC LIMIT 1', (user_id,)).fetchone()
        if not user or user['status'] != 'reset' or user['chat_id'] != chat_id:
            return None
        chat = db.execute('SELECT id FROM registration_requests WHERE chat_id=? '
                          'ORDER BY created DESC,rowid DESC LIMIT 1', (chat_id,)).fetchone()
        return user['id'] if chat and chat['id'] == user['id'] else None

    def member_control(self, member_id):
        with self.lock:
            row = self.db.execute('SELECT locked,revision,updated FROM registration_member_controls WHERE member_id=?',
                                  (member_id,)).fetchone()
            return dict(row) | {'locked': bool(row['locked'])} if row else {'locked': False, 'revision': 0, 'updated': 0}

    def latest_member(self, member_id):
        with self.lock:
            return self._record(self.db.execute('SELECT * FROM registration_requests WHERE member_id=? '
                                               'ORDER BY created DESC,rowid DESC LIMIT 1', (member_id,)).fetchone())

    def latest_members(self):
        with self.lock:
            return [self._record(row) for row in self.db.execute(
                'SELECT r.* FROM registration_requests r WHERE r.rowid=(SELECT r2.rowid FROM registration_requests r2 '
                'WHERE r2.member_id=r.member_id ORDER BY r2.created DESC,r2.rowid DESC LIMIT 1) ORDER BY r.name,r.member_id')]

    def _bump_revision(self, db, member_id):
        db.execute('INSERT INTO registration_member_controls VALUES (?,0,1,?) ON CONFLICT(member_id) '
                   'DO UPDATE SET revision=revision+1,updated=excluded.updated', (member_id, self.clock()))

    def _audit_admin(self, db, member_id, actor, action, before, after):
        db.execute('INSERT INTO registration_admin_audit(member_id,actor,action,at,before_json,after_json) '
                   'VALUES (?,?,?,?,?,?)', (member_id, actor, action, self.clock(),
                                           json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))

    @staticmethod
    def _correction_actor_reason(actor, reason):
        # The web caller checks the current members capability. This label is
        # only audit attribution, never a way to grant registration-admin ACLs.
        if not isinstance(actor, str) or not re.fullmatch(r'web:[A-Za-z0-9_.@-]{1,100}', actor):
            raise RegistrationError('Phiên quản trị chưa hợp lệ.')
        if (not isinstance(reason, str) or not 5 <= len(reason.strip()) <= 300
                or any(ord(char) < 32 for char in reason)
                or re.search(r'(?:\d[\s().+\-]*){9,}', reason)):
            raise RegistrationError('Nhập lý do từ 5–300 ký tự, không ghi số điện thoại vào lý do.')
        return reason.strip()

    def _correction_snapshot(self, db, record, person):
        if not isinstance(record, RegistrationRecord):
            raise RegistrationError('Thông tin đăng ký cần sửa chưa hợp lệ.')
        current = self._record(db.execute('SELECT * FROM registration_requests WHERE id=?', (record.id,)).fetchone())
        if current != record or record.status not in ('pending', 'approved'):
            raise RegistrationError('Đăng ký đã thay đổi hoặc không còn hiệu lực; tải lại trước khi sửa.')
        for key in ('member_id', 'user_id', 'chat_id'):
            latest = db.execute(f'SELECT id FROM registration_requests WHERE {key}=? '
                                'ORDER BY created DESC,rowid DESC LIMIT 1', (getattr(record, key),)).fetchone()
            if latest is None or latest['id'] != record.id:
                raise RegistrationError('Tài khoản hoặc chat đã có đăng ký mới hơn; chưa thay đổi liên kết.')
        if person['source_key'] != record.source_key:
            raise RegistrationError('Nguồn Workload đã thay đổi; cần xác minh nguồn trước khi sửa đăng ký.')
        if not person['name_unique']:
            raise RegistrationError('Tên trùng trong Workload; cần mã phân biệt và ánh xạ lịch trước khi sửa đăng ký.')
        if person['phone'] == record.phone:
            raise RegistrationError('Số điện thoại này đã thuộc đăng ký đang xem; không cần chuyển danh tính.')
        if self._locked_route(db, record.user_id, record.chat_id):
            raise RegistrationError('Tài khoản đang bị khóa; cần quản trị kiểm tra và mở khóa trước khi sửa đăng ký.')
        source_control = self.member_control(record.member_id)
        target = db.execute('SELECT member_id FROM registration_members WHERE source_key=? AND phone=?',
                            (person['source_key'], person['phone'])).fetchone()
        target_id = target['member_id'] if target else None
        target_control = self.member_control(target_id) if target_id else {'locked': False, 'revision': 0, 'updated': 0}
        if target_control['locked']:
            raise RegistrationError('Thành viên đúng đang bị khóa; cần quản trị kiểm tra và mở khóa trước khi sửa đăng ký.')
        if db.execute("SELECT 1 FROM registration_requests WHERE id<>? AND status IN ('pending','approved') "
                      'AND (user_id=? OR chat_id=? OR member_id=?)',
                      (record.id, record.user_id, record.chat_id, target_id)).fetchone():
            raise RegistrationError('Người được chọn đang có đăng ký khác; chưa ghi đè hoặc chuyển tài khoản của người khác.')
        target_latest = self._record(db.execute('SELECT * FROM registration_requests WHERE member_id=? '
                                               'ORDER BY created DESC,rowid DESC LIMIT 1', (target_id,)).fetchone()) if target_id else None
        # The preview is private process state. Keep full phones out of it too:
        # fingerprints bind all input fields, and only masked data is audited.
        digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return dict(source_request_id=record.id, source_member_id=record.member_id,
                    source_control=source_control, target_member_id=target_id, target_control=target_control,
                    source_digest=digest(vars(record)), person_digest=digest(person),
                    target_digest=digest(vars(target_latest)) if target_latest else None)

    def correction_snapshot(self, record, person):
        """Validate a web correction without writes; caller must freshly read Workload.

        The source is an immutable request captured by the administrator's
        preview. A correction keeps its observed user/chat route, and transfers
        no history, identity keys, administrator rights or group grants.
        """
        person = self._person(person)
        with self.lock:
            return self._correction_snapshot(self.db, record, person)

    def _reset_snapshot(self, db, record):
        if not isinstance(record, RegistrationRecord):
            raise RegistrationError('Thông tin đăng ký cần xóa chưa hợp lệ.')
        current = self._record(db.execute('SELECT * FROM registration_requests WHERE id=?', (record.id,)).fetchone())
        if current != record or record.status not in ('pending', 'approved', 'rejected', 'revoked'):
            raise RegistrationError('Đăng ký đã thay đổi hoặc đã xóa; tải lại trước khi thực hiện.')
        for key in ('member_id', 'user_id', 'chat_id'):
            latest = db.execute(f'SELECT id FROM registration_requests WHERE {key}=? '
                                'ORDER BY created DESC,rowid DESC LIMIT 1', (getattr(record, key),)).fetchone()
            if latest is None or latest['id'] != record.id:
                raise RegistrationError('Tài khoản hoặc chat đã có đăng ký mới hơn; chưa xóa đăng ký.')
        control = self.member_control(record.member_id)
        if control['locked'] or self._locked_route(db, record.user_id, record.chat_id):
            raise RegistrationError('Tài khoản đang bị khóa; cần quản trị kiểm tra và mở khóa trước khi xóa đăng ký.')
        if db.execute("SELECT 1 FROM registration_requests WHERE id<>? AND status IN ('pending','approved') "
                      'AND (user_id=? OR chat_id=? OR member_id=?)',
                      (record.id, record.user_id, record.chat_id, record.member_id)).fetchone():
            raise RegistrationError('Liên kết có đăng ký khác đang dùng; chưa xóa đăng ký.')
        return dict(source_request_id=record.id, source_member_id=record.member_id, source_control=control,
                    source_digest=hashlib.sha256(json.dumps(vars(record), sort_keys=True, ensure_ascii=False).encode()).hexdigest())

    def reset_snapshot(self, record):
        """Validate the exact current registration without disclosing its phone."""
        with self.lock:
            return self._reset_snapshot(self.db, record)

    def reset_registration(self, actor, record, expected_snapshot, reason):
        """Withdraw a registration and permit a fresh self-registration message.

        The caller must authenticate members permission, clear ancillary
        bindings/context while holding runtime locks, then run this transaction
        last. This retains stable identities, history and message receipts; it
        never grants access, removes Workload rows or changes administrator ACLs.
        """
        reason = self._correction_actor_reason(actor, reason)
        if not isinstance(expected_snapshot, dict):
            raise RegistrationError('Cần xem trước đăng ký trước khi xác nhận xóa.')
        with self._transaction() as db:
            if self._reset_snapshot(db, record) != expected_snapshot:
                raise RegistrationError('Thông tin vừa được cập nhật; tải lại trước khi xóa đăng ký.')
            now = max(self.clock(), record.created, record.updated)
            db.execute("UPDATE registration_requests SET status='reset',updated=? WHERE id=?", (now, record.id))
            db.execute('INSERT INTO registration_events(request_id,actor,action,at) VALUES (?,?,?,?)',
                       (record.id, actor, 'registration_reset', now))
            self._bump_revision(db, record.member_id)
            self._audit_admin(db, record.member_id, actor, 'registration_reset',
                              {'request_id': record.id, 'member_id': record.member_id,
                               'phone_masked': '******' + record.phone[-4:], 'status': record.status},
                              {'request_id': record.id, 'member_id': record.member_id,
                               'status': 'reset', 'reason': reason})
            return self._record(db.execute('SELECT * FROM registration_requests WHERE id=?', (record.id,)).fetchone())

    def correct_registration(self, actor, record, person, expected_snapshot, reason):
        """Atomically revoke a wrong identity and approve a new request for its route.

        HTTP authentication, current members permission, fresh roster lookup
        and ancillary Tickets/context removal are the caller's responsibility.
        The caller must hold the bot's runtime locks during those removals and
        call this transaction last. Old records/member IDs remain immutable.
        """
        reason = self._correction_actor_reason(actor, reason)
        person = self._person(person)
        if not isinstance(expected_snapshot, dict):
            raise RegistrationError('Cần xem trước thay đổi đăng ký trước khi xác nhận.')
        with self._transaction() as db:
            if self._correction_snapshot(db, record, person) != expected_snapshot:
                raise RegistrationError('Thông tin vừa được cập nhật; tạo bản xem trước mới trước khi sửa đăng ký.')
            target_id = expected_snapshot['target_member_id'] or 'member-' + uuid.uuid4().hex
            if expected_snapshot['target_member_id'] is None:
                db.execute('INSERT INTO registration_members VALUES (?,?,?)',
                           (target_id, person['source_key'], person['phone']))
            for _ in range(64):
                request_id = 'DK-' + secrets.token_hex(4).upper()
                if not db.execute('SELECT 1 FROM registration_requests WHERE id=?', (request_id,)).fetchone():
                    break
            else:
                raise RegistrationError('Chưa cấp được mã đăng ký; vui lòng thử lại sau.')
            # Preserve ordering even when the host clock moves backwards.
            latest_created = db.execute('SELECT MAX(created) FROM registration_requests WHERE member_id=?',
                                        (target_id,)).fetchone()[0] or 0
            now = max(self.clock(), record.created, latest_created)
            request_key = 'admin-correction:' + uuid.uuid4().hex
            db.execute("UPDATE registration_requests SET status='revoked',updated=? WHERE id=?", (now, record.id))
            db.execute('INSERT INTO registration_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (request_id, target_id, record.user_id, record.chat_id, person['phone'], person['name'],
                        person['source_key'], person['fingerprint'], int(person['name_unique']), 'approved', now, now, request_key))
            db.execute('INSERT INTO registration_receipts VALUES (?,?)', (request_key, request_id))
            for item_id, member_id, action in ((record.id, record.member_id, 'identity_corrected_from'),
                                              (request_id, target_id, 'identity_corrected_to')):
                db.execute('INSERT INTO registration_events(request_id,actor,action,at) VALUES (?,?,?,?)',
                           (item_id, actor, action, now))
                self._bump_revision(db, member_id)
                self._audit_admin(db, member_id, actor, action,
                                  {'request_id': record.id, 'member_id': record.member_id,
                                   'phone_masked': '******' + record.phone[-4:], 'status': record.status},
                                  {'request_id': request_id, 'member_id': target_id,
                                   'phone_masked': '******' + person['phone'][-4:], 'status': 'approved', 'reason': reason})
            return self._record(db.execute('SELECT * FROM registration_requests WHERE id=?', (request_id,)).fetchone())

    def user_state(self, user_id):
        """Local admission lookup; caller must enforce the returned DM route."""
        if not isinstance(user_id, str) or not 1 <= len(user_id) <= 200:
            raise RegistrationError('Mã tài khoản chưa hợp lệ.')
        with self.lock:
            return self._record(self.db.execute('SELECT * FROM registration_requests WHERE user_id=? '
                                               'ORDER BY created DESC,rowid DESC LIMIT 1', (user_id,)).fetchone())

    def get_for_admin(self, admin, request_id):
        self._admin(admin)
        request_id = self._request_id(request_id)
        with self.lock:
            row = self.db.execute('SELECT * FROM registration_requests WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise RegistrationError('Không tìm thấy đăng ký phù hợp.')
            return self._record(row)

    def pending(self, admin, *, page=1, limit=10):
        self._admin(admin)
        if type(page) is not int or not 1 <= page <= 10000 or type(limit) is not int or not 1 <= limit <= 25:
            raise RegistrationError('Trang hoặc số đăng ký mỗi trang chưa hợp lệ.')
        with self.lock:
            return [self._record(row) for row in self.db.execute(
                "SELECT * FROM registration_requests WHERE status='pending' ORDER BY created,rowid LIMIT ? OFFSET ?",
                (limit, (page - 1) * limit))]

    def members(self, admin, *, source_key=None, page=1, limit=5):
        """Admin-only, paged approved members; no additional ACL to synchronize."""
        self._admin(admin)
        if type(page) is not int or not 1 <= page <= 10000 or type(limit) is not int or not 1 <= limit <= 25:
            raise RegistrationError('Trang hoặc số thành viên mỗi trang chưa hợp lệ.')
        if source_key is not None and (not isinstance(source_key, str) or not source_key):
            raise RegistrationError('Nguồn danh sách thành viên chưa hợp lệ.')
        query = "SELECT * FROM registration_requests WHERE status='approved'"
        params = []
        if source_key is not None:
            query += ' AND source_key=?'
            params.append(source_key)
        query += ' ORDER BY created,rowid LIMIT ? OFFSET ?'
        with self.lock:
            return [self._record(row) for row in self.db.execute(query, [*params, limit, (page - 1) * limit])]

    def approved_records(self, source_key):
        """Internal runtime snapshot; never expose this list to a member."""
        if not isinstance(source_key, str) or not source_key:
            raise RegistrationError('Nguồn danh sách thành viên chưa hợp lệ.')
        with self.lock:
            return [self._record(row) for row in self.db.execute(
                "SELECT * FROM registration_requests WHERE status='approved' AND source_key=? ORDER BY created,rowid",
                (source_key,))]

    def _decision(self, db, row, status, admin, *, action=None):
        now = max(self.clock(), row['created'], row['updated'])
        db.execute('UPDATE registration_requests SET status=?,updated=? WHERE id=?', (status, now, row['id']))
        db.execute('INSERT INTO registration_events(request_id,actor,action,at) VALUES (?,?,?,?)',
                   (row['id'], admin, action or status, now))
        self._bump_revision(db, row['member_id'])
        self._audit_admin(db, row['member_id'], admin, action or status,
                          {'status': row['status']}, {'status': status})
        return self._record(db.execute('SELECT * FROM registration_requests WHERE id=?', (row['id'],)).fetchone())

    def approve(self, admin, request_id, person):
        self._admin(admin)
        request_id, person = self._request_id(request_id), self._person(person)
        with self._transaction() as db:
            row = db.execute('SELECT * FROM registration_requests WHERE id=?', (request_id,)).fetchone()
            if row is None or row['status'] not in ('pending', 'approved'):
                raise RegistrationError('Không tìm thấy đăng ký đang chờ duyệt phù hợp.')
            if (any(row[key] != person[key] for key in ('phone', 'name', 'source_key', 'fingerprint'))
                    or bool(row['name_unique']) != person['name_unique']):
                raise RegistrationError('Danh sách nguồn đã thay đổi; cần kiểm tra và gửi lại đăng ký trước khi duyệt.')
            if row['status'] == 'approved':
                return self._record(row)
            conflicts = db.execute("SELECT 1 FROM registration_requests WHERE id<>? AND status IN ('pending','approved') "
                                   'AND (user_id=? OR chat_id=? OR member_id=?)',
                                   (row['id'], row['user_id'], row['chat_id'], row['member_id'])).fetchone()
            if conflicts:
                raise RegistrationError('Đăng ký xung đột với liên kết đang dùng; chưa cấp quyền.')
            return self._decision(db, row, 'approved', admin)

    def reject(self, admin, request_id):
        self._admin(admin)
        request_id = self._request_id(request_id)
        with self._transaction() as db:
            row = db.execute('SELECT * FROM registration_requests WHERE id=?', (request_id,)).fetchone()
            if row is None or row['status'] not in ('pending', 'rejected'):
                raise RegistrationError('Không tìm thấy đăng ký đang chờ duyệt phù hợp.')
            return self._record(row) if row['status'] == 'rejected' else self._decision(db, row, 'rejected', admin)

    def revoke(self, admin, member_id):
        self._admin(admin)
        if not isinstance(member_id, str) or not re.fullmatch(r'member-[a-f0-9]{32}', member_id):
            raise RegistrationError('Mã thành viên chưa hợp lệ.')
        with self._transaction() as db:
            row = db.execute('SELECT * FROM registration_requests WHERE member_id=? ORDER BY created DESC,rowid DESC LIMIT 1',
                             (member_id,)).fetchone()
            if row is None:
                raise RegistrationError('Không tìm thấy thành viên đã đăng ký.')
            if row['status'] == 'revoked':
                return self._record(row)
            return self._decision(db, row, 'revoked', admin)
