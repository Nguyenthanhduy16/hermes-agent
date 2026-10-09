"""Loopback-only web server with session authentication and CSRF protection.

This server is for local development and the explicit test workbook. Public
hosting, TLS/reverse-proxy trust and organization SSO require a separate rollout.
"""
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import secrets
from urllib.parse import urlsplit

from .service import TicketError, digest, valid_password

COOKIE = 'labcoach_tickets'


class TicketsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, service):
        self.service = service
        super().__init__(('127.0.0.1', port), Handler)
        self.allowed_hosts = {f'127.0.0.1:{self.server_port}', f'localhost:{self.server_port}'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'LabCoach'

    def log_message(self, *args):
        # Neither request URLs nor user-provided content belong in stdout logs.
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    @property
    def service(self):
        return self.server.service

    def _reply(self, status, body, mime='application/json; charset=utf-8', cookie=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', f'{COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
        self.end_headers()
        self.wfile.write(body)

    def _host(self):
        # Also prevents DNS-rebinding of a local demo into a remotely usable API.
        if self.headers.get('Host') not in self.server.allowed_hosts:
            raise TicketError('Địa chỉ truy cập không được phép.', 403)
        if len(self.path) > 500:
            raise TicketError('Đường dẫn quá dài.')

    def _session(self, create=False):
        store, now = self.service.store, self.service.clock()
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookies[COOKIE].value if COOKIE in cookies else ''
        except Exception:
            token = ''
        row = store.db.execute('SELECT * FROM ticket_sessions WHERE hash=? AND expires>?', (digest(token), now)).fetchone()
        if row:
            return dict(row), None
        if not create:
            raise TicketError('Phiên đăng nhập hết hạn; vui lòng đăng nhập lại.', 401)
        token = secrets.token_urlsafe(32)
        session = dict(hash=digest(token), account=None, csrf=secrets.token_urlsafe(32), expires=now + 8 * 3600)
        with store.transaction() as db:
            db.execute('DELETE FROM ticket_sessions WHERE expires<=?', (now,))
            db.execute('INSERT INTO ticket_sessions VALUES (:hash,:account,:csrf,:expires)', session)
        return session, token

    def _session_body(self, session):
        user = self.service.store.account(session['account']) if session['account'] else None
        accounts = []
        if self.service.mode == 'demo':
            accounts = [dict(r) for r in self.service.store.db.execute('SELECT id,name,role FROM ticket_accounts WHERE active=1 ORDER BY id')]
        return dict(mode=self.service.mode, user=user, csrf=session['csrf'],
                    requires_manager=self.service.require_manager, demo_accounts=accounts)

    def _rotate(self, old, account):
        token = secrets.token_urlsafe(32)
        session = dict(hash=digest(token), account=account, csrf=secrets.token_urlsafe(32), expires=self.service.clock() + 8 * 3600)
        with self.service.store.transaction() as db:
            db.execute('DELETE FROM ticket_sessions WHERE hash=?', (old['hash'],))
            db.execute('INSERT INTO ticket_sessions VALUES (:hash,:account,:csrf,:expires)', session)
        return session, token

    def _login(self, body, session):
        store = self.service.store
        if self.service.mode == 'demo':
            username = body.get('account_id')
            if username not in ('demo-a', 'demo-b', 'demo-c', 'demo-manager') or not store.account(username):
                raise TicketError('Tài khoản mô phỏng không hợp lệ.')
        else:
            username, password = body.get('username', ''), body.get('password', '')
            if not isinstance(username, str) or len(username) > 60 or not isinstance(password, str) or len(password) > 256:
                raise TicketError('Thông tin đăng nhập không hợp lệ.', 401)
            now = self.service.clock()
            with store.transaction() as db:
                db.execute('DELETE FROM ticket_login_failures WHERE at<?', (now - 600,))
            # Global cap plus per-account cap also covers username guessing.
            count = store.db.execute('SELECT COUNT(*) FROM ticket_login_failures WHERE username=?', (username,)).fetchone()[0]
            total = store.db.execute('SELECT COUNT(*) FROM ticket_login_failures').fetchone()[0]
            if count >= 5 or total >= 25:
                raise TicketError('Đăng nhập sai nhiều lần; vui lòng thử lại sau 10 phút.', 429)
            row = store.db.execute('SELECT password FROM ticket_accounts WHERE id=? AND active=1', (username,)).fetchone()
            if not row or not valid_password(password, row['password']):
                with store.transaction() as db:
                    db.execute('INSERT INTO ticket_login_failures VALUES (?,?)', (now, username))
                raise TicketError('Tên đăng nhập hoặc mật khẩu chưa đúng.', 401)
            with store.transaction() as db:
                db.execute('DELETE FROM ticket_login_failures WHERE username=?', (username,))
        new, token = self._rotate(session, username)
        self._reply(200, self._session_body(new), cookie=token)

    def do_GET(self):
        try:
            self._host()
            path = urlsplit(self.path).path
            static = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            static.update({'/static' + route: asset for route, asset in list(static.items()) if route != '/'})
            if path in static:
                filename, mime = static[path]
                self._reply(200, files('hermes_zalo.tickets').joinpath('static', filename).read_bytes(), mime)
                return
            with self.service.lock:
                if path == '/api/session':
                    session, token = self._session(create=True)
                    self._reply(200, self._session_body(session), cookie=token)
                    return
                session, _ = self._session()
                user = self.service._user(self.service.store.account(session['account']))
                if path == '/api/bootstrap':
                    self._reply(200, self.service.bootstrap(user))
                elif path.startswith('/api/tickets/') and len(path.split('/')) == 4:
                    self._reply(200, self.service.detail(user, path.split('/')[-1]))
                else:
                    raise TicketError('Không tìm thấy nội dung.', 404)
        except TicketError as exc:
            self._reply(exc.status, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self._reply(500, {'error': 'Chưa tải được dữ liệu; vui lòng thử lại hoặc báo quản lý.'})

    def do_POST(self):
        try:
            self._host()
            if self.headers.get('Origin') != 'http://' + self.headers.get('Host', ''):
                raise TicketError('Nguồn thao tác chưa hợp lệ; hãy mở lại trang Tickets.', 403)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise TicketError('Định dạng yêu cầu chưa hợp lệ.', 415)
            if self.headers.get('Transfer-Encoding'):
                raise TicketError('Định dạng yêu cầu chưa được hỗ trợ.')
            length = self.headers.get('Content-Length', '')
            if not length.isdecimal() or not 0 < int(length) <= 8192:
                raise TicketError('Nội dung yêu cầu quá dài hoặc không hợp lệ.', 413)
            try:
                body = json.loads(self.rfile.read(int(length)).decode('utf-8'))
            except (ValueError, UnicodeError):
                raise TicketError('Nội dung yêu cầu không hợp lệ.') from None
            if not isinstance(body, dict):
                raise TicketError('Nội dung yêu cầu phải là một đối tượng.')
            with self.service.lock:
                session, _ = self._session()
                csrf = self.headers.get('X-CSRF-Token', '')
                if not csrf.isascii() or not secrets.compare_digest(csrf, session['csrf']):
                    raise TicketError('Phiên thao tác đã thay đổi; vui lòng tải lại trang.', 403)
                path = urlsplit(self.path).path
                if path == '/api/login':
                    self._login(body, session)
                    return
                if path == '/api/logout':
                    new, token = self._rotate(session, None)
                    self._reply(200, self._session_body(new), cookie=token)
                    return
                user = self.service._user(self.service.store.account(session['account']))
                if path == '/api/preview':
                    a, b = body.get('assignment_a'), body.get('assignment_b')
                    if not isinstance(a, str) or not isinstance(b, str):
                        raise TicketError('Hãy chọn đủ hai ca muốn đổi.')
                    result = self.service.preview(user, a, b)
                elif path == '/api/tickets':
                    result = self.service.create(user, body.get('preview_token'), body.get('reason', ''), body.get('idempotency_key'))
                elif path.startswith('/api/tickets/') and len(path.split('/')) == 5:
                    _, _, _, ticket, action = path.split('/')
                    result = self.service.action(user, ticket, action, body.get('version'))
                else:
                    raise TicketError('Không tìm thấy thao tác.', 404)
                self._reply(200, result)
        except TicketError as exc:
            self._reply(exc.status, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self._reply(500, {'error': 'Chưa xác nhận được kết quả. Hãy tải lại yêu cầu trước khi thao tác tiếp.'})
