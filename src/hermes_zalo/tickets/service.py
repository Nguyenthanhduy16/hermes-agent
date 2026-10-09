"""Durable, explicit two-party shift exchange with read-back reconciliation."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from .providers import TicketsSourceError, TicketsWriteUncertain

VN = timezone(timedelta(hours=7))
CONFIRM_TTL_SECONDS = 15 * 60
ACTIVE = ('pending_peer', 'pending_manager', 'applying', 'uncertain')
PENDING = ('pending_peer', 'pending_manager')
LABELS = {'pending_peer': 'Chờ người đổi cùng xác nhận', 'pending_manager': 'Chờ quản lý duyệt',
          'applying': 'Đang cập nhật lịch', 'uncertain': 'Cần kiểm tra kết quả',
          'completed': 'Đã đổi ca', 'rejected': 'Đã từ chối', 'cancelled': 'Đã hủy',
          'expired': 'Đã hết hạn', 'stale': 'Lịch hoặc quyền đã thay đổi'}
FIELDS = ('id', 'member_id', 'member_name', 'start', 'end', 'course', 'room', 'period')
LIVE_TEST_NOTICE = 'THỬ TRÊN SHEETS THẬT: đổi cột Lab Coach; chỉ ca tương lai; chưa kiểm tra trùng lịch.'


class TicketError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def stamp(ts):
    return datetime.fromtimestamp(ts, VN).isoformat(timespec='seconds')


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise TicketError('Mật khẩu cần từ 12 đến 256 ký tự.')
    salt = salt or secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + key


def valid_password(password, hashed):
    try:
        return hmac.compare_digest(password_hash(password, hashed.split(':')[0]), hashed)
    except (TicketError, ValueError):
        return False


class TicketStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS ticket_accounts (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
              member_id TEXT NOT NULL, password TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            CREATE UNIQUE INDEX IF NOT EXISTS ticket_member_account ON ticket_accounts(member_id)
              WHERE member_id <> '' AND active=1;
            CREATE TABLE IF NOT EXISTS ticket_sessions (
              hash TEXT PRIMARY KEY, account TEXT, csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ticket_previews (
              hash TEXT PRIMARY KEY, account TEXT NOT NULL, body TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS exchange_tickets (
              id TEXT PRIMARY KEY, creator TEXT NOT NULL, recipient TEXT NOT NULL,
              status TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
              created REAL NOT NULL, expires REAL NOT NULL, reason TEXT NOT NULL,
              idem TEXT NOT NULL, preview TEXT NOT NULL, target TEXT NOT NULL,
              require_manager INTEGER NOT NULL, UNIQUE(creator,idem));
            CREATE TABLE IF NOT EXISTS exchange_claims (
              assignment TEXT PRIMARY KEY, ticket TEXT NOT NULL REFERENCES exchange_tickets(id));
            CREATE TABLE IF NOT EXISTS exchange_events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, ticket TEXT NOT NULL,
              at REAL NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ticket_login_failures (at REAL NOT NULL, username TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS demo_assignments (id TEXT PRIMARY KEY, body TEXT NOT NULL);
        ''')

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def account(self, username):
        row = self.db.execute('SELECT id,name,role,member_id FROM ticket_accounts WHERE id=? AND active=1',
                              (username,)).fetchone()
        return dict(row) if row else None

    def add_account(self, username, name, member_id, role, password):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', username) or role not in ('member', 'manager'):
            raise TicketError('Tên đăng nhập hoặc vai trò chưa hợp lệ.')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise TicketError('Cần tên hiển thị hợp lệ.')
        if (role == 'member' and not member_id) or not re.fullmatch(r'[A-Za-z0-9_.:-]{0,80}', member_id):
            raise TicketError('Thành viên cần mã định danh đã xác minh.')
        hashed = password_hash(password)
        try:
            with self.transaction() as db:
                db.execute('INSERT INTO ticket_accounts VALUES (?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET '
                           'name=excluded.name,role=excluded.role,member_id=excluded.member_id,password=excluded.password,active=1',
                           (username, name.strip(), role, member_id, hashed))
                db.execute('DELETE FROM ticket_sessions WHERE account=?', (username,))
        except sqlite3.IntegrityError:
            raise TicketError('Mã thành viên đã liên kết với một tài khoản khác.') from None

    def close(self):
        self.db.close()


class DemoAssignments:
    """Persistent fake roster. Seed once; restart never restores old ownership."""
    source_key = 'offline-demo-v1'

    def __init__(self, store, now=None):
        self.store = store
        with store.transaction() as db:
            if db.execute('SELECT 1 FROM demo_assignments LIMIT 1').fetchone():
                return
            day = datetime.fromtimestamp(now or time.time(), VN).replace(hour=8, minute=0, second=0, microsecond=0)
            people = [('demo-a', 'coach-a', 'An · Thành viên A'), ('demo-b', 'coach-b', 'Bình · Thành viên B'),
                      ('demo-c', 'coach-c', 'Chi · Thành viên C')]
            for i, (_, member, name) in enumerate(people):
                for j in range(2):
                    start = day + timedelta(days=2 + i * 2 + j)
                    a = dict(id=f'CA-{i+1}{j+1}', member_id=member, member_name=name,
                             start=start.isoformat(), end=(start + timedelta(hours=3)).isoformat(),
                             course=['Lập trình Python', 'Thiết kế web', 'Dữ liệu cơ bản'][i],
                             room=f'Lab {201+i}', period='Sáng')
                    db.execute('INSERT INTO demo_assignments VALUES (?,?)', (a['id'], packed(a)))
            for username, member, name in people:
                db.execute('INSERT OR IGNORE INTO ticket_accounts VALUES (?,?,?,?,?,1)',
                           (username, name, 'member', member, '!demo'))
            db.execute('INSERT OR IGNORE INTO ticket_accounts VALUES (?,?,?,?,?,1)',
                       ('demo-manager', 'Minh · Quản lý', 'manager', '', '!demo'))

    def list_assignments(self):
        return [json.loads(r[0]) for r in self.store.db.execute('SELECT body FROM demo_assignments ORDER BY id')]

    def swap(self, a, b, ticket_id):
        with self.store.transaction() as db:
            for old, other in ((a, b), (b, a)):
                row = db.execute('SELECT body FROM demo_assignments WHERE id=?', (old['id'],)).fetchone()
                if not row or json.loads(row[0]) != old:
                    raise TicketsSourceError('Lịch mẫu đã thay đổi; cần tạo yêu cầu mới.')
            for old, other in ((a, b), (b, a)):
                changed = dict(old, member_id=other['member_id'], member_name=other['member_name'])
                db.execute('UPDATE demo_assignments SET body=? WHERE id=?', (packed(changed), old['id']))

    def close(self):
        pass


class TicketService:
    def __init__(self, store, provider, *, mode='demo', require_manager=True, clock=time.time):
        self.store, self.provider = store, provider
        self.mode, self.require_manager, self.clock = mode, require_manager, clock
        self.lock = store.lock
        self.test_members = ()
        if mode == 'lab_live_test':
            settings = getattr(provider, 'settings', None)
            self.test_members = getattr(settings, 'test_members', ())
            if require_manager or not getattr(settings, 'test_mode', False) or len(set(self.test_members)) != 2:
                raise TicketError('lab_live_test cần đúng hai thành viên đã xác minh và không dùng bước quản lý duyệt.')
        # Include the exact destination in every preview/ticket; a config change
        # cannot redirect an already approved write.
        self.target = getattr(provider, 'source_key', None)
        if self.target is None:
            cfg = provider.settings
            self.target = f'sheets:{cfg.spreadsheet_id}:{cfg.tab}'
        with store.transaction() as db:
            rows = db.execute("SELECT id FROM exchange_tickets WHERE status='applying'").fetchall()
            for row in rows:
                db.execute("UPDATE exchange_tickets SET status='uncertain',version=version+1 WHERE id=?", (row['id'],))
                self._event(db, row['id'], 'Hệ thống', 'Dịch vụ khởi động lại; cần đối soát trước khi kết luận.')

    def _event(self, db, ticket, actor, text):
        db.execute('INSERT INTO exchange_events(ticket,at,actor,text) VALUES (?,?,?,?)',
                   (ticket, self.clock(), actor, text))

    def _user(self, user):
        current = self.store.account(user['id']) if isinstance(user, dict) and 'id' in user else None
        if not current:
            raise TicketError('Vui lòng đăng nhập lại.', 401)
        if self.mode == 'lab_live_test' and (current['role'] != 'member' or current['name'] not in self.test_members):
            raise TicketError('Tài khoản không thuộc hai thành viên được phép thử trên lịch thật.', 403)
        return current

    def _source(self):
        try:
            rows = self.provider.list_assignments()
        except TicketsSourceError as exc:
            raise TicketError(str(exc), 503) from None
        result = {}
        for row in rows:
            if not isinstance(row, dict) or any(not isinstance(row.get(f), str) or not row[f].strip() for f in FIELDS):
                raise TicketError('Lịch thiếu thông tin ca; cần bổ sung đủ mã và giờ bắt đầu/kết thúc.', 409)
            if row['id'] in result:
                raise TicketError('Lịch có mã phân công trùng; cần quản lý sửa dữ liệu.', 409)
            try:
                a, b = datetime.fromisoformat(row['start']), datetime.fromisoformat(row['end'])
                if a.tzinfo is None or b.tzinfo is None or b <= a:
                    raise ValueError()
            except ValueError:
                raise TicketError('Cần giờ ca chính xác kèm múi giờ để kiểm tra trùng lịch.', 409) from None
            result[row['id']] = {f: row[f] for f in FIELDS}
        return result

    def _validate_pair(self, user, a, b, rows):
        if a['id'] == b['id'] or a['member_id'] == b['member_id']:
            raise TicketError('Hãy chọn hai ca của hai thành viên khác nhau.')
        if user['member_id'] != a['member_id']:
            raise TicketError('Bạn chỉ được đề nghị đổi ca thuộc về mình.', 403)
        if self.mode == 'lab_live_test' and {a['member_name'], b['member_name']} != set(self.test_members):
            raise TicketError('Chế độ thử chỉ đổi ca giữa đúng hai thành viên đã cấu hình.', 403)
        for assignment in (a, b):
            if not self.is_future(assignment):
                raise TicketError('Chỉ đổi ca chưa bắt đầu. Ca hôm nay chưa rõ giờ bắt đầu cũng không được chọn.', 409)
            if not self.store.db.execute('SELECT 1 FROM ticket_accounts WHERE member_id=? AND active=1',
                                         (assignment['member_id'],)).fetchone():
                raise TicketError('Một thành viên chưa có tài khoản được quản lý xác minh.', 409)
        if self.mode != 'lab_live_test':
            self._check_conflicts(a, b, rows)
        # Some sources also contain read-only duties (for example Key Coach).
        # Revalidate those at preview, submit and final approval as well.
        validate = getattr(self.provider, 'validate_pair', None)
        if validate is not None:
            try:
                validate(a, b)
            except TicketsSourceError as exc:
                raise TicketError(str(exc), 503) from None

    def _future_deadline(self, assignment):
        start = datetime.fromisoformat(assignment['start']).astimezone(VN)
        if self.mode == 'lab_live_test' and not re.fullmatch(
                r'(Sáng|Chiều|Tối) (?:[01]\d|2[0-3]):[0-5]\d\s*[–-]\s*(?:[01]\d|2[0-3]):[0-5]\d',
                assignment['period']):
            # Unknown hours are not inferred. Midnight is a conservative
            # selection deadline, never a displayed or asserted shift hour.
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp()

    def is_future(self, assignment):
        """True only while the assignment is known not to have started."""
        return self._future_deadline(assignment) > self.clock()

    def _check_conflicts(self, a, b, rows):
        # Validate the resulting full roster, including simultaneous roles in one class.
        excluded = {a['id'], b['id']}
        for destination, owner in ((a, b), (b, a)):
            start, end = [datetime.fromisoformat(destination[f]) for f in ('start', 'end')]
            for row in rows.values():
                if row['id'] not in excluded and row['member_id'] == owner['member_id']:
                    x, y = [datetime.fromisoformat(row[f]) for f in ('start', 'end')]
                    if start < y and end > x:
                        raise TicketError('Sau khi đổi, một thành viên bị trùng giờ với ca khác.', 409)

    def _bindings(self, a, b):
        return {item['member_id']: [dict(row) for row in self.store.db.execute(
            'SELECT id,member_id FROM ticket_accounts WHERE member_id=? AND active=1', (item['member_id'],))]
                for item in (a, b)}

    def _expire(self):
        with self.store.transaction() as db:
            for row in db.execute("SELECT id,body,expires FROM exchange_tickets WHERE status IN ('pending_peer','pending_manager')").fetchall():
                body = json.loads(row['body'])
                past = any(not self.is_future(body[key]) for key in ('a', 'b'))
                if row['expires'] <= self.clock() or past:
                    # Legacy live-test requests may have a 48-hour deadline
                    # beyond the shift date. New future-only rules apply to
                    # those pending requests too; uncertain writes stay intact.
                    self._transition(db, row['id'], 'expired', 'Hệ thống',
                                     'Ca không còn thuộc tương lai; yêu cầu hết hạn, lịch không thay đổi.' if past else
                                     'Yêu cầu đã hết hạn; lịch không thay đổi.')
            db.execute('DELETE FROM ticket_previews WHERE expires<?', (self.clock() - 86400,))

    def _transition(self, db, ticket, status, actor, message):
        db.execute('UPDATE exchange_tickets SET status=?,version=version+1 WHERE id=?', (status, ticket))
        if status not in ACTIVE:
            db.execute('DELETE FROM exchange_claims WHERE ticket=?', (ticket,))
        self._event(db, ticket, actor, message)

    def _row(self, user, ticket):
        row = self.store.db.execute('SELECT * FROM exchange_tickets WHERE id=?', (ticket,)).fetchone()
        if not row or not (user['role'] == 'manager' or row['creator'] == user['id'] or row['recipient'] == user['member_id']):
            raise TicketError('Không tìm thấy yêu cầu trong phạm vi được phép.', 404)
        return row

    def detail(self, user, ticket):
        with self.lock:
            user = self._user(user)
            self._expire()
            row = self._row(user, ticket)
            body = json.loads(row['body'])
            actions = []
            if row['status'] in PENDING:
                if row['creator'] == user['id']:
                    actions.append('cancel')
                if row['recipient'] == user['member_id']:
                    actions.append('reject')
                    if row['status'] == 'pending_peer':
                        actions.insert(0, 'accept')
                if user['role'] == 'manager':
                    if 'reject' not in actions:
                        actions.append('reject')
                    if row['status'] == 'pending_manager' and user['id'] != row['creator'] and user['member_id'] != row['recipient']:
                        actions.insert(0, 'approve')
            involved = row['creator'] == user['id'] or row['recipient'] == user['member_id']
            if row['status'] == 'uncertain' and (user['role'] == 'manager' or not row['require_manager'] and involved):
                actions.append('reconcile')
            events = [dict(at=stamp(e['at']), actor=e['actor'], text=e['text']) for e in
                      self.store.db.execute('SELECT * FROM exchange_events WHERE ticket=? ORDER BY seq', (ticket,))]
            return dict(id=row['id'], version=row['version'], status=row['status'], status_label=LABELS[row['status']],
                        created_at=stamp(row['created']), expires_at=stamp(row['expires']), requester_id=row['creator'],
                        recipient_member_id=row['recipient'], a=body['a'], b=body['b'], reason=row['reason'],
                        actions=actions, events=events, message=events[-1]['text'] if events else '')

    def bootstrap(self, user):
        with self.lock:
            user = self._user(user)
            self._expire()
            source_error = None
            try:
                assignments = list(self._source().values())
            except TicketError as exc:
                assignments, source_error = [], str(exc)
            ids = [r['id'] for r in self.store.db.execute('SELECT id FROM exchange_tickets WHERE creator=? OR recipient=? OR ?=1 ORDER BY created DESC',
                                                        (user['id'], user['member_id'], int(user['role'] == 'manager')))]
            members = [dict(id=r['member_id'], name=r['name']) for r in self.store.db.execute(
                "SELECT member_id,name FROM ticket_accounts WHERE active=1 AND member_id<>'' ORDER BY name")]
            return dict(user=user, mode=self.mode, requires_manager=self.require_manager, assignments=assignments,
                        members=members, tickets=[self.detail(user, i) for i in ids], source_error=source_error)

    def preview(self, user, assignment_a, assignment_b):
        with self.lock:
            user = self._user(user)
            self._expire()
            rows = self._source()
            if assignment_a not in rows or assignment_b not in rows:
                raise TicketError('Không tìm thấy hai ca được chọn; hãy tải lại danh sách.', 409)
            a, b = rows[assignment_a], rows[assignment_b]
            self._validate_pair(user, a, b, rows)
            if self.store.db.execute('SELECT 1 FROM exchange_claims WHERE assignment IN (?,?)', (a['id'], b['id'])).fetchone():
                raise TicketError('Một ca đang có yêu cầu khác chưa xử lý xong.', 409)
            token = secrets.token_urlsafe(32)
            expires = min(self.clock() + CONFIRM_TTL_SECONDS,
                          self._future_deadline(a), self._future_deadline(b))
            if expires <= self.clock():
                raise TicketError('Một ca đã bắt đầu trong lúc kiểm tra; hãy chọn ca tương lai khác.', 409)
            body = dict(a=a, b=b, target=self.target, require_manager=self.require_manager, bindings=self._bindings(a, b))
            with self.store.transaction() as db:
                db.execute('INSERT INTO ticket_previews VALUES (?,?,?,?)', (digest(token), user['id'], packed(body), expires))
            return dict(a=a, b=b, preview_token=token, expires_at=stamp(expires), requires_manager=self.require_manager)

    def create(self, user, preview_token, reason, idempotency_key, *, on_created=None):
        with self.lock:
            user = self._user(user)
            if not isinstance(reason, str) or len(reason) > 600 or not isinstance(preview_token, str):
                raise TicketError('Lý do tối đa 600 ký tự; cần bản xem trước hợp lệ.')
            if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', idempotency_key):
                raise TicketError('Mã lần gửi chưa hợp lệ; hãy tải lại trang.')
            old = self.store.db.execute('SELECT * FROM exchange_tickets WHERE creator=? AND idem=?', (user['id'], idempotency_key)).fetchone()
            if old:
                if old['preview'] != digest(preview_token) or old['reason'] != reason.strip():
                    raise TicketError('Lần gửi này đã dùng cho nội dung khác.', 409)
                return self.detail(user, old['id'])
            self._expire()
            preview = self.store.db.execute('SELECT * FROM ticket_previews WHERE hash=? AND account=?', (digest(preview_token), user['id'])).fetchone()
            if not preview or preview['expires'] <= self.clock():
                raise TicketError('Bản xem trước đã hết hạn; vui lòng xem lại hai ca.', 409)
            body = json.loads(preview['body'])
            if body['target'] != self.target or body['require_manager'] != self.require_manager:
                raise TicketError('Cấu hình đã thay đổi; cần xem trước lại.', 409)
            rows = self._source()
            if any(rows.get(body[k]['id']) != body[k] for k in ('a', 'b')):
                raise TicketError('Lịch đã thay đổi; hãy xem trước và gửi lại.', 409)
            self._validate_pair(user, body['a'], body['b'], rows)
            if body.get('bindings') != self._bindings(body['a'], body['b']):
                raise TicketError('Liên kết tài khoản đã thay đổi; cần xem trước lại.', 409)
            ticket = 'DC-' + secrets.token_hex(5).upper()
            deadline = min(self.clock() + 48 * 3600,
                           *(self._future_deadline(body[k]) for k in ('a', 'b')))
            if deadline <= self.clock():
                raise TicketError('Một ca đã bắt đầu trong lúc kiểm tra; hãy chọn ca tương lai khác.', 409)
            try:
                with self.store.transaction() as db:
                    db.execute('INSERT INTO exchange_tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (ticket, user['id'], body['b']['member_id'], 'pending_peer', 1, packed(body), self.clock(), deadline,
                                reason.strip(), idempotency_key, digest(preview_token), self.target, int(self.require_manager)))
                    for k in ('a', 'b'):
                        db.execute('INSERT INTO exchange_claims VALUES (?,?)', (body[k]['id'], ticket))
                    self._event(db, ticket, user['name'], 'Đã xem trước và gửi đề nghị đổi hai ca; chờ người đổi cùng xác nhận.')
                    if on_created is not None:
                        # The local outbox must commit with the request. The
                        # callback does no network IO and starts no transaction.
                        on_created(db, ticket)
            except sqlite3.IntegrityError:
                raise TicketError('Một ca đã có yêu cầu khác; hãy tải lại danh sách.', 409) from None
            return self.detail(user, ticket)

    def _still_valid(self, row):
        body = json.loads(row['body'])
        creator = self.store.account(row['creator'])
        if (not creator or row['target'] != self.target or bool(row['require_manager']) != self.require_manager or
                body.get('bindings') != self._bindings(body['a'], body['b'])):
            raise TicketError('Quyền hoặc bảng nhận đã thay đổi; cần tạo yêu cầu mới.', 409)
        rows = self._source()
        if any(rows.get(body[k]['id']) != body[k] for k in ('a', 'b')):
            raise TicketError('Lịch đã thay đổi; cần xem lại và tạo yêu cầu mới.', 409)
        self._validate_pair(creator, body['a'], body['b'], rows)
        return body

    def action(self, user, ticket, action, version):
        with self.lock:
            user = self._user(user)
            detail = self.detail(user, ticket)
            row = self._row(user, ticket)
            if action not in detail['actions']:
                raise TicketError('Thao tác không còn hợp lệ hoặc bạn chưa được cấp quyền.', 409)
            if type(version) is not int or version != row['version']:
                raise TicketError('Yêu cầu đã thay đổi; hãy tải lại trước khi xác nhận.', 409)
            if action == 'reconcile':
                return self._reconcile(user, row)
            if action in ('cancel', 'reject'):
                with self.store.transaction() as db:
                    self._transition(db, ticket, 'cancelled' if action == 'cancel' else 'rejected', user['name'],
                                     'Đã hủy yêu cầu; lịch không thay đổi.' if action == 'cancel' else 'Đã từ chối; lịch không thay đổi.')
                return self.detail(user, ticket)
            try:
                body = self._still_valid(row)
            except TicketError as exc:
                if exc.status != 503:
                    with self.store.transaction() as db:
                        self._transition(db, ticket, 'stale', 'Hệ thống', str(exc))
                    return self.detail(user, ticket)
                raise
            if action == 'accept' and row['require_manager']:
                with self.store.transaction() as db:
                    self._transition(db, ticket, 'pending_manager', user['name'], 'Đã đồng ý nội dung đổi ca; chờ quản lý duyệt.')
                return self.detail(user, ticket)
            # Commit the intent BEFORE crossing the external write boundary.
            with self.store.transaction() as db:
                self._transition(db, ticket, 'applying', user['name'],
                                 'Đã xác nhận; bắt đầu cập nhật hai phân công.')
            try:
                self.provider.swap(body['a'], body['b'], ticket)
            except TicketsWriteUncertain:
                with self.store.transaction() as db:
                    self._transition(db, ticket, 'uncertain', 'Hệ thống', 'Chưa rõ kết quả ghi; cần đối soát. Không tự ghi lại.')
                return self.detail(user, ticket)
            except TicketsSourceError as exc:
                with self.store.transaction() as db:
                    self._transition(db, ticket, 'stale', 'Hệ thống', str(exc))
                return self.detail(user, ticket)
            except Exception:
                with self.store.transaction() as db:
                    self._transition(db, ticket, 'uncertain', 'Hệ thống', 'Chưa xác minh được kết quả; cần đối soát, không tự ghi lại.')
                return self.detail(user, ticket)
            return self._reconcile(user, self._row(user, ticket))

    def _reconcile(self, user, row):
        # Read-only: even an unchanged sheet after timeout must not trigger a retry.
        matched = False
        if row['target'] == self.target:
            try:
                rows = self._source()
                body = json.loads(row['body'])
                a, b = body['a'], body['b']
                normalize = getattr(self.provider, 'reconciliation_snapshot', None)
                if self.mode == 'lab_live_test' and normalize is not None:
                    # Old test snapshots used midnight placeholders even for
                    # explicit period hours. Normalize only the local read-back
                    # comparison, never stored proposals or write validation.
                    a, b = normalize(a), normalize(b)
                expected = [dict(a, member_id=b['member_id'], member_name=b['member_name']),
                            dict(b, member_id=a['member_id'], member_name=a['member_name'])]
                matched = all(rows.get(item['id']) == item for item in expected)
                if matched:
                    if self.mode != 'lab_live_test':
                        self._check_conflicts(a, b, rows)
                    validate_result = getattr(self.provider, 'validate_result', None)
                    if validate_result is not None:
                        validate_result(a, b)
            except (TicketError, TicketsSourceError):
                matched = False
        with self.store.transaction() as db:
            self._transition(db, row['id'], 'completed' if matched else 'uncertain', 'Hệ thống',
                             {'demo': 'Đã đọc lại và xác nhận đổi đúng hai ca trên dữ liệu mô phỏng.',
                              'sheets_test': 'Đã đọc lại và xác nhận đổi đúng hai ca trên Google Sheets thử nghiệm.',
                              'lab_live': 'Đã đọc lại và xác nhận đổi đúng hai Lab Coach trên lịch hiện có.',
                              'lab_live_test': LIVE_TEST_NOTICE + ' Đã đọc lại và xác nhận đổi đúng hai ô Lab Coach.'}[self.mode] if matched else
                             'Chưa đối chiếu được đúng cả hai ca. Giữ yêu cầu để kiểm tra; không tự ghi lại.')
        return self.detail(user, row['id'])
