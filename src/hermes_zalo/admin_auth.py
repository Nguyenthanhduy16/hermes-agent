"""Separate administrator identities; Zalo registration never grants web access."""
from contextlib import contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

PERMISSIONS = frozenset(('members', 'conversations', 'memories', 'issues', 'tickets'))
ROLES = frozenset(('admin', 'faculty'))


class AdminError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise AdminError('Mật khẩu cần từ 12 đến 256 ký tự.')
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 400_000).hex()


def password_matches(password, stored):
    valid_input = isinstance(password, str) and 12 <= len(password) <= 256
    if not valid_input:
        password = 'invalid-password'
    try:
        salt, _ = stored.split(':', 1)
        matched = hmac.compare_digest(password_hash(password, salt), stored)
        return matched and valid_input
    except (ValueError, TypeError):
        return False


class AdminAuth:
    def __init__(self, path, clock=time.time):
        self.clock, self.lock = clock, threading.RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.create_function('casefold', 1, lambda value: str(value).casefold(), deterministic=True)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS admin_accounts (
              username TEXT PRIMARY KEY, password TEXT NOT NULL, permissions TEXT NOT NULL,
              active INTEGER NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_sessions (
              hash TEXT PRIMARY KEY, username TEXT, csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_login_attempts (username TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_password_attempts (username TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_auth_audit (
              id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, at REAL NOT NULL);
        ''')
        # Additive migration: existing identities retain their permissions and
        # are ordinary admins. Faculty can only be designated by an operator.
        with self.transaction() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(admin_accounts)')}
            for column, definition in (
                ('role', "TEXT NOT NULL DEFAULT 'admin'"),
                ('display_name', "TEXT NOT NULL DEFAULT ''"),
                ('revision', 'INTEGER NOT NULL DEFAULT 1'),
            ):
                if column not in columns:
                    db.execute(f'ALTER TABLE admin_accounts ADD COLUMN {column} {definition}')
            db.execute("UPDATE admin_accounts SET display_name=username WHERE display_name=''")
            audit_columns = {row['name'] for row in db.execute('PRAGMA table_info(admin_auth_audit)')}
            if 'target' not in audit_columns:
                db.execute("ALTER TABLE admin_auth_audit ADD COLUMN target TEXT NOT NULL DEFAULT ''")
            if 'details' not in audit_columns:
                db.execute("ALTER TABLE admin_auth_audit ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")
        self._dummy = password_hash(secrets.token_urlsafe(24))

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

    def close(self):
        with self.lock:
            self.db.close()

    @staticmethod
    def _username(username):
        if not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{2,47}', username):
            raise AdminError('Tên đăng nhập gồm 3–48 chữ thường, số, dấu chấm, gạch ngang hoặc gạch dưới.')
        return username

    @staticmethod
    def _permissions(permissions):
        if not isinstance(permissions, (list, tuple, set, frozenset)) or any(not isinstance(value, str) for value in permissions):
            raise AdminError('Quyền phải là danh sách members, conversations, memories, issues hoặc tickets.')
        if len(permissions) != len(set(permissions)) or not set(permissions) <= PERMISSIONS:
            raise AdminError('Quyền không hợp lệ hoặc bị lặp.')
        return sorted(permissions)

    @staticmethod
    def _display_name(name, username):
        if name is None:
            return username
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or any(ord(char) < 32 for char in name):
            raise AdminError('Tên hiển thị cần từ 1 đến 100 ký tự, trên một dòng.')
        return name.strip()

    def _audit(self, db, actor, action, username, before=None, after=None):
        # Never include password hashes, session keys or CSRF values in audit.
        detail = dict(before=before, after=after)
        db.execute('INSERT INTO admin_auth_audit(actor,action,at,target,details) VALUES (?,?,?,?,?)',
                   (actor, action, self.clock(), username, json.dumps(detail, ensure_ascii=False)))

    @staticmethod
    def _public_account(row):
        if row is None:
            raise AdminError('Không tìm thấy tài khoản quản trị.', 404)
        return dict(username=row['username'], display_name=row['display_name'], role=row['role'],
                    permissions=json.loads(row['permissions']), active=bool(row['active']),
                    version=row['revision'], updated=row['updated'])

    @staticmethod
    def _protect_faculty(db, current, role, active):
        if current and current['role'] == 'faculty' and current['active'] and (role != 'faculty' or not active):
            count = db.execute("SELECT COUNT(*) FROM admin_accounts WHERE role='faculty' AND active=1").fetchone()[0]
            if count <= 1:
                raise AdminError('Phải giữ ít nhất một Giảng viên đang hoạt động.', 409)

    def create_account(self, username, password, permissions, *, replace=False, role='admin', display_name=None):
        """Trusted local operator only; this is not exposed as a web endpoint."""
        username = self._username(username)
        permissions = self._permissions(permissions)
        name = self._display_name(display_name, username)
        if role not in ROLES:
            raise AdminError('Vai trò phải là admin hoặc faculty.')
        encoded = password_hash(password)
        with self.transaction() as db:
            current = db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone()
            if current and not replace:
                raise AdminError('Tài khoản đã tồn tại. Chỉ dùng --replace khi cần đặt lại quyền/mật khẩu.', 409)
            self._protect_faculty(db, current, role, True)
            if current and display_name is None:
                name = current['display_name']
            db.execute('INSERT INTO admin_accounts(username,password,permissions,active,updated,role,display_name,revision) '
                       'VALUES (?,?,?,?,?,?,?,1) ON CONFLICT(username) DO UPDATE SET '
                       'password=excluded.password, permissions=excluded.permissions, active=1,updated=excluded.updated, '
                       'role=excluded.role,display_name=excluded.display_name,revision=admin_accounts.revision+1',
                       (username, encoded, json.dumps(permissions), 1, self.clock(), role, name))
            db.execute('DELETE FROM admin_sessions WHERE username=?', (username,))
            configured = db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone()
            self._audit(db, 'local-operator', 'account_configured:' + username, username,
                        self._public_account(current) if current else None, self._public_account(configured))

    def grant_permissions(self, username, permissions):
        """Explicit local grant to one account, preserving passwords and role."""
        username = self._username(username)
        permissions = self._permissions(permissions)
        with self.transaction() as db:
            current = db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone()
            before = self._public_account(current)
            if not current['active']:
                raise AdminError('Tài khoản đang bị khóa; không cấp thêm quyền cho tài khoản này.', 409)
            grants = sorted(set(before['permissions']) | set(permissions))
            if grants == sorted(before['permissions']):
                return before
            db.execute('UPDATE admin_accounts SET permissions=?,revision=revision+1,updated=? WHERE username=?',
                       (json.dumps(grants), self.clock(), username))
            db.execute('DELETE FROM admin_sessions WHERE username=?', (username,))
            after = self._public_account(db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone())
            self._audit(db, 'local-operator', 'permissions_granted', username, before, after)
            return after

    def designate_faculty(self, username):
        """Explicit local promotion, preserving the account's existing grants."""
        username = self._username(username)
        with self.transaction() as db:
            current = db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone()
            before = self._public_account(current)
            if not current['active']:
                raise AdminError('Tài khoản đang bị khóa; dùng create-account --role faculty --replace để khôi phục.', 409)
            if current['role'] == 'faculty':
                return before
            db.execute("UPDATE admin_accounts SET role='faculty',revision=revision+1,updated=? WHERE username=?",
                       (self.clock(), username))
            db.execute('DELETE FROM admin_sessions WHERE username=?', (username,))
            after = self._public_account(db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone())
            self._audit(db, 'local-operator', 'faculty_designated', username, before, after)
            return after

    def session(self, token, *, create=False):
        now = self.clock()
        with self.transaction() as db:
            db.execute('DELETE FROM admin_sessions WHERE expires<=?', (now,))
            row = db.execute('SELECT * FROM admin_sessions WHERE hash=?', (digest(token or ''),)).fetchone()
            if row:
                return dict(row), None
            if not create:
                raise AdminError('Phiên đăng nhập hết hạn; vui lòng đăng nhập lại.', 401)
            token = secrets.token_urlsafe(32)
            row = dict(hash=digest(token), username=None, csrf=secrets.token_urlsafe(32), expires=now + 3600)
            db.execute('INSERT INTO admin_sessions VALUES (:hash,:username,:csrf,:expires)', row)
            return row, token

    def user(self, session, permission=None):
        with self.lock:
            # A handler can already be queued on the gateway loop when an
            # operator resets a password or the browser logs out. Never treat
            # its old username snapshot as continuing authentication.
            return self._user(self.db, session, permission)

    def _user(self, db, session, permission=None):
        row = db.execute('SELECT a.username,a.permissions,a.role,a.display_name FROM admin_sessions s JOIN admin_accounts a '
                                  'ON a.username=s.username WHERE s.hash=? AND s.username=? AND s.csrf=? '
                                  'AND s.expires>? AND a.active=1',
                                  (session.get('hash'), session.get('username'), session.get('csrf'), self.clock())).fetchone()
        if row is None:
            raise AdminError('Vui lòng đăng nhập Admin.', 401)
        user = dict(username=row['username'], permissions=json.loads(row['permissions']),
                    role=row['role'], display_name=row['display_name'])
        if permission and permission not in user['permissions'] and not (permission == 'issues' and user['role'] == 'faculty'):
            raise AdminError('Tài khoản chưa có quyền xem hoặc thay đổi mục này.', 403)
        return user

    def _faculty(self, db, session):
        user = self._user(db, session)
        if user['role'] != 'faculty':
            raise AdminError('Chỉ Giảng viên được quản lý tài khoản Admin.', 403)
        return user

    def list_accounts(self, session, query='', page=1):
        if not isinstance(query, str) or len(query) > 100:
            raise AdminError('Nội dung tìm kiếm tối đa 100 ký tự.')
        if isinstance(page, bool) or not isinstance(page, (str, int)) or not str(page).isdigit() or not 1 <= int(page) <= 10000:
            raise AdminError('Trang không hợp lệ.')
        page = int(page)
        with self.lock:
            self._faculty(self.db, session)
            # instr treats percent/underscore literally rather than SQL wildcards.
            where = 'WHERE instr(casefold(username),casefold(?))>0 OR instr(casefold(display_name),casefold(?))>0'
            params = (query.strip(), query.strip())
            total = self.db.execute('SELECT COUNT(*) FROM admin_accounts ' + where, params).fetchone()[0]
            rows = self.db.execute('SELECT * FROM admin_accounts ' + where + ' ORDER BY username LIMIT 25 OFFSET ?',
                                   (*params, (page - 1) * 25)).fetchall()
            return dict(items=[self._public_account(row) for row in rows], total=total, page=page, page_size=25)

    def _account_detail(self, db, username):
        result = self._public_account(db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone())
        rows = db.execute('SELECT actor,action,at,details FROM admin_auth_audit WHERE target=? ORDER BY id DESC LIMIT 100',
                          (username,)).fetchall()
        result['audit'] = [dict(actor=row['actor'], action=row['action'], at=row['at'],
                                details=json.loads(row['details'])) for row in rows]
        return result

    def account_detail(self, session, username):
        username = self._username(username)
        with self.lock:
            self._faculty(self.db, session)
            return self._account_detail(self.db, username)

    def manage_account(self, session, data):
        # Check authorization before any validation or expensive password work.
        with self.lock:
            self._faculty(self.db, session)
        allowed = {'operation', 'username', 'display_name', 'permissions', 'active', 'password', 'version', 'role', 'confirm'}
        if not isinstance(data, dict) or not set(data) <= allowed:
            raise AdminError('Tham số quản lý tài khoản không hợp lệ.')
        if data.get('confirm') is not True:
            raise AdminError('Cần xác nhận thay đổi tài khoản.')
        operation = data.get('operation')
        if operation not in ('create', 'update'):
            raise AdminError('Chọn thao tác create hoặc update.')
        username = self._username(data.get('username'))
        permissions = self._permissions(data.get('permissions'))
        name = self._display_name(data.get('display_name'), username)
        active = data.get('active', True)
        if type(active) is not bool:
            raise AdminError('Trạng thái tài khoản không hợp lệ.')
        version = data.get('version')
        if operation == 'update' and (type(version) is not int or version < 1):
            raise AdminError('Cần phiên bản tài khoản đang hiển thị.')
        encoded = None
        if 'password' in data:
            encoded = password_hash(data['password'])
        if operation == 'create' and encoded is None:
            raise AdminError('Cần mật khẩu cho tài khoản mới.')
        with self.transaction() as db:
            actor = self._faculty(db, session)
            current = db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone()
            if operation == 'create' and current:
                raise AdminError('Tên đăng nhập đã tồn tại.', 409)
            if operation == 'update':
                if not current:
                    raise AdminError('Không tìm thấy tài khoản quản trị.', 404)
                if current['revision'] != version:
                    raise AdminError('Tài khoản đã thay đổi; tải lại trước khi lưu.', 409)
            role = current['role'] if current else 'admin'
            if data.get('role', role) != role:
                raise AdminError('Vai trò Giảng viên chỉ được chỉ định bằng công cụ quản trị trên server.', 403)
            self._protect_faculty(db, current, role, active)
            before = self._public_account(current) if current else None
            if current:
                db.execute('UPDATE admin_accounts SET password=?,permissions=?,active=?,display_name=?,updated=?, '
                           'revision=revision+1 WHERE username=?',
                           (encoded or current['password'], json.dumps(permissions), int(active), name, self.clock(), username))
            else:
                db.execute('INSERT INTO admin_accounts(username,password,permissions,active,updated,role,display_name,revision) '
                           'VALUES (?,?,?,?,?,?,?,1)', (username, encoded, json.dumps(permissions), int(active), self.clock(), role, name))
            # Removing sessions invalidates captured snapshots as well as cookies.
            db.execute('DELETE FROM admin_sessions WHERE username=?', (username,))
            after = self._public_account(db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone())
            self._audit(db, 'web:' + actor['username'], 'account_' + ('created' if operation == 'create' else 'updated'),
                        username, before, after)
            return self._account_detail(db, username)

    def issue_operators(self, session):
        with self.lock:
            self._user(self.db, session, 'issues')
            rows = self.db.execute('SELECT username,display_name,role,permissions FROM admin_accounts WHERE active=1 ORDER BY username').fetchall()
            return dict(items=[dict(username=row['username'], display_name=row['display_name'], role=row['role'])
                for row in rows if row['role'] == 'faculty' or 'issues' in json.loads(row['permissions'])])

    def login(self, session, username, password):
        if not isinstance(username, str) or len(username) > 48:
            raise AdminError('Tên đăng nhập hoặc mật khẩu không đúng.', 401)
        now = self.clock()
        with self.transaction() as db:
            self._live_session(db, session)
            db.execute('DELETE FROM admin_login_attempts WHERE at<=?', (now - 600,))
            own = db.execute('SELECT COUNT(*) FROM admin_login_attempts WHERE username=?', (username,)).fetchone()[0]
            total = db.execute('SELECT COUNT(*) FROM admin_login_attempts').fetchone()[0]
            if own >= 5 or total >= 30:
                raise AdminError('Đăng nhập sai nhiều lần; thử lại sau 10 phút.', 429)
            # Count attempts before hashing to keep concurrent guesses bounded.
            db.execute('INSERT INTO admin_login_attempts VALUES (?,?)', (username, now))
            account = db.execute('SELECT * FROM admin_accounts WHERE username=? AND active=1', (username,)).fetchone()
        valid = password_matches(password, account['password'] if account else self._dummy)
        if not account or not valid:
            raise AdminError('Tên đăng nhập hoặc mật khẩu không đúng.', 401)
        token = secrets.token_urlsafe(32)
        new = dict(hash=digest(token), username=username, csrf=secrets.token_urlsafe(32), expires=now + 8 * 3600)
        with self.transaction() as db:
            self._live_session(db, session)
            current = db.execute('SELECT * FROM admin_accounts WHERE username=? AND active=1', (username,)).fetchone()
            if not current or current['revision'] != account['revision'] or current['password'] != account['password']:
                raise AdminError('Tài khoản đã thay đổi; đăng nhập lại.', 401)
            db.execute('DELETE FROM admin_sessions WHERE hash=?', (session['hash'],))
            db.execute('INSERT INTO admin_sessions VALUES (:hash,:username,:csrf,:expires)', new)
            db.execute('DELETE FROM admin_login_attempts WHERE username=?', (username,))
            db.execute('INSERT INTO admin_auth_audit(actor,action,at) VALUES (?,?,?)',
                       ('web:' + username, 'login', now))
        return new, token

    def _live_session(self, db, session):
        if not db.execute('SELECT 1 FROM admin_sessions WHERE hash=? AND csrf=? AND expires>?',
                          (session.get('hash'), session.get('csrf'), self.clock())).fetchone():
            raise AdminError('Phiên đăng nhập hết hạn; tải lại trang để tiếp tục.', 401)

    def change_password(self, session, data):
        """An authenticated account can change only its own password."""
        with self.lock:
            self._user(self.db, session)
        if not isinstance(data, dict) or set(data) != {'current_password', 'new_password', 'confirm_password'}:
            raise AdminError('Chỉ nhập mật khẩu hiện tại, mật khẩu mới và xác nhận mật khẩu mới.')
        new_password = data['new_password']
        if not isinstance(new_password, str) or not 12 <= len(new_password) <= 256:
            raise AdminError('Mật khẩu mới cần từ 12 đến 256 ký tự.')
        if not isinstance(data['confirm_password'], str) or data['confirm_password'] != new_password:
            raise AdminError('Hai lần nhập mật khẩu mới chưa khớp.')
        now = self.clock()
        with self.transaction() as db:
            user = self._user(db, session)
            username = user['username']
            db.execute('DELETE FROM admin_password_attempts WHERE at<=?', (now - 600,))
            attempts = db.execute('SELECT COUNT(*) FROM admin_password_attempts WHERE username=?', (username,)).fetchone()[0]
            if attempts >= 5:
                raise AdminError('Đã thử đổi mật khẩu nhiều lần; thử lại sau 10 phút.', 429)
            # Commit the attempt before password work so concurrent guesses
            # and renewed login sessions cannot bypass the account limit.
            db.execute('INSERT INTO admin_password_attempts VALUES (?,?)', (username, now))
            account = db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone()
        if not password_matches(data['current_password'], account['password']):
            raise AdminError('Mật khẩu hiện tại chưa đúng.')
        if data['current_password'] == new_password:
            raise AdminError('Chọn mật khẩu mới khác mật khẩu hiện tại.')
        encoded = password_hash(new_password)
        with self.transaction() as db:
            self._user(db, session)
            current = db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone()
            if current['revision'] != account['revision'] or current['password'] != account['password']:
                raise AdminError('Tài khoản đã thay đổi; đăng nhập lại.', 401)
            before = self._public_account(current)
            db.execute('UPDATE admin_accounts SET password=?,revision=revision+1,updated=? WHERE username=?',
                       (encoded, self.clock(), username))
            db.execute('DELETE FROM admin_sessions WHERE username=?', (username,))
            db.execute('DELETE FROM admin_password_attempts WHERE username=?', (username,))
            after = self._public_account(db.execute('SELECT * FROM admin_accounts WHERE username=?', (username,)).fetchone())
            self._audit(db, 'web:' + username, 'password_changed', username, before, after)
        return {'ok': True}

    def logout(self, session):
        with self.transaction() as db:
            db.execute('DELETE FROM admin_sessions WHERE hash=?', (session['hash'],))
