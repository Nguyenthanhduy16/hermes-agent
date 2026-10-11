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

    def request(self, message, person):
        message_key, person = self._message(message), self._person(person)
        with self._transaction() as db:
            old = db.execute('SELECT r.* FROM registration_requests r JOIN registration_receipts receipt '
                             'ON receipt.request_id=r.id WHERE receipt.message_key=?', (message_key,)).fetchone()
            if old:
                if (any(old[key] != person[key] for key in ('phone', 'name', 'source_key', 'fingerprint'))
                        or bool(old['name_unique']) != person['name_unique']):
                    raise RegistrationError('Tin nhắn này đã dùng cho nội dung khác; chưa thay đổi đăng ký.')
                return self._record(old)
            current = db.execute("SELECT * FROM registration_requests WHERE status IN ('pending','approved') "
                                 'AND (user_id=? OR chat_id=?)', (message.user_id, message.chat_id)).fetchone()
            if current:
                if (current['user_id'] == message.user_id and current['chat_id'] == message.chat_id
                        and all(current[key] == person[key] for key in ('phone', 'name', 'source_key', 'fingerprint'))
                        and bool(current['name_unique']) == person['name_unique']):
                    db.execute('INSERT INTO registration_receipts VALUES (?,?)', (message_key, current['id']))
                    return self._record(current)
                raise RegistrationError('Tài khoản hoặc cuộc trò chuyện đã có đăng ký. Nhờ quản trị kiểm tra trước khi thay đổi.')
            member = db.execute('SELECT member_id FROM registration_members WHERE source_key=? AND phone=?',
                                (person['source_key'], person['phone'])).fetchone()
            member_id = member[0] if member else 'member-' + uuid.uuid4().hex
            if db.execute("SELECT 1 FROM registration_requests WHERE member_id=? AND status IN ('pending','approved')",
                          (member_id,)).fetchone():
                raise RegistrationError('Thông tin này đang có đăng ký khác. Nhờ quản trị kiểm tra.')
            if not member:
                db.execute('INSERT INTO registration_members VALUES (?,?,?)',
                           (member_id, person['source_key'], person['phone']))
            for _ in range(64):
                request_id = 'DK-' + secrets.token_hex(4).upper()
                if not db.execute('SELECT 1 FROM registration_requests WHERE id=?', (request_id,)).fetchone():
                    break
            else:
                raise RegistrationError('Chưa cấp được mã đăng ký; vui lòng thử lại sau.')
            now = self.clock()
            db.execute('INSERT INTO registration_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (request_id, member_id, message.user_id, message.chat_id, person['phone'], person['name'],
                        person['source_key'], person['fingerprint'], int(person['name_unique']), 'pending', now, now, message_key))
            db.execute('INSERT INTO registration_receipts VALUES (?,?)', (message_key, request_id))
            return self._record(db.execute('SELECT * FROM registration_requests WHERE id=?', (request_id,)).fetchone())

    def status(self, message):
        self._message(message)
        with self.lock:
            return self._record(self.db.execute('SELECT * FROM registration_requests WHERE user_id=? AND chat_id=? '
                                               'ORDER BY created DESC,rowid DESC LIMIT 1',
                                               (message.user_id, message.chat_id)).fetchone())

    def approved(self, message):
        record = self.status(message)
        return record if record is not None and record.status == 'approved' else None

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

    def _decision(self, db, row, status, admin):
        db.execute('UPDATE registration_requests SET status=?,updated=? WHERE id=?', (status, self.clock(), row['id']))
        db.execute('INSERT INTO registration_events(request_id,actor,action,at) VALUES (?,?,?,?)',
                   (row['id'], admin, status, self.clock()))
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
