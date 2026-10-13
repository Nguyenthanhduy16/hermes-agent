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

PERMISSIONS = frozenset(('members', 'conversations', 'memories'))


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
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS admin_accounts (
              username TEXT PRIMARY KEY, password TEXT NOT NULL, permissions TEXT NOT NULL,
              active INTEGER NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_sessions (
              hash TEXT PRIMARY KEY, username TEXT, csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_login_attempts (username TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_auth_audit (
              id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, at REAL NOT NULL);
        ''')
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

    def create_account(self, username, password, permissions, *, replace=False):
        if not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{2,47}', username):
            raise AdminError('Tên đăng nhập gồm 3–48 chữ thường, số, dấu chấm, gạch ngang hoặc gạch dưới.')
        permissions = frozenset(permissions)
        if not permissions or not permissions <= PERMISSIONS:
            raise AdminError('Chọn quyền members, conversations hoặc memories.')
        encoded = password_hash(password)
        with self.transaction() as db:
            if db.execute('SELECT 1 FROM admin_accounts WHERE username=?', (username,)).fetchone() and not replace:
                raise AdminError('Tài khoản đã tồn tại. Chỉ dùng --replace khi cần đặt lại quyền/mật khẩu.', 409)
            db.execute('INSERT INTO admin_accounts VALUES (?,?,?,?,?) ON CONFLICT(username) DO UPDATE SET '
                       'password=excluded.password, permissions=excluded.permissions, active=1,updated=excluded.updated',
                       (username, encoded, json.dumps(sorted(permissions)), 1, self.clock()))
            db.execute('DELETE FROM admin_sessions WHERE username=?', (username,))
            db.execute('INSERT INTO admin_auth_audit(actor,action,at) VALUES (?,?,?)',
                       ('local-operator', 'account_configured:' + username, self.clock()))

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
            row = self.db.execute('SELECT a.username,a.permissions FROM admin_sessions s JOIN admin_accounts a '
                                  'ON a.username=s.username WHERE s.hash=? AND s.username=? AND s.csrf=? '
                                  'AND s.expires>? AND a.active=1',
                                  (session.get('hash'), session.get('username'), session.get('csrf'), self.clock())).fetchone()
        if row is None:
            raise AdminError('Vui lòng đăng nhập Admin.', 401)
        user = dict(username=row['username'], permissions=json.loads(row['permissions']))
        if permission and permission not in user['permissions']:
            raise AdminError('Tài khoản chưa có quyền xem hoặc thay đổi mục này.', 403)
        return user

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
            if not current or current['password'] != account['password']:
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

    def logout(self, session):
        with self.transaction() as db:
            db.execute('DELETE FROM admin_sessions WHERE hash=?', (session['hash'],))
