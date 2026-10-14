"""Private, same-process administration API; all mutations run on the bot loop.

External binding is opt-in and requires a single configured HTTPS origin behind
a TLS proxy. Forwarded headers are never trusted for authentication or origin.
"""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

from .admin_auth import AdminAuth, AdminError
from .admin_members import AdminMemberError, MemberAdmin
from .conversations import ConversationConflict, ConversationError
from .issue_management import IssueError
from .reminders import VIETNAM_TZ

COOKIE = 'labcoach_admin'


def number(value, default=1):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = int(value if value not in (None, '') else default)
        if not 1 <= result <= 1000000:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise AdminError('Số trang không hợp lệ.') from None


def boundary(value, *, end=False):
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=VIETNAM_TZ)
        return (parsed + timedelta(days=1) if end else parsed).timestamp()
    except (TypeError, ValueError):
        raise AdminError('Ngày cần có dạng năm-tháng-ngày.') from None


class AdminApplication:
    def __init__(self, work, conversations, auth, *, mode='local'):
        self.work, self.conversations, self.auth, self.mode = work, conversations, auth, mode
        self.members = MemberAdmin(work)

    def _context_view(self, result, session):
        if 'memories' in self.auth.user(session)['permissions']:
            return dict(result, memories_redacted=False)
        result = dict(result, memories=[], included_memory_ids=[], memories_redacted=True)
        if result.get('last_prepared'):
            result['last_prepared'] = dict(result['last_prepared'], included_memory_ids=[])
        return result

    def _issue_labels(self, issue):
        result = dict(issue)
        registrations = self.work.registration.store
        result['member_name'] = 'Chưa xác minh người gửi'
        if registrations and issue.get('member_id'):
            with registrations.lock:
                row = registrations.db.execute('SELECT name FROM registration_requests WHERE member_id=? '
                    'ORDER BY updated DESC LIMIT 1', (issue['member_id'],)).fetchone()
            if row:
                result['member_name'] = row['name']
        return result

    async def route(self, method, path, query, data, session):
        actor = 'web:' + self.auth.user(session)['username']
        if path in ('/api/admin/accounts', '/api/admin/account', '/api/admin/account/save'):
            # Authentication module checks faculty again inside account transactions.
            if method == 'GET' and path == '/api/admin/accounts':
                result = self.auth.list_accounts(session, query=query.get('q', ''), page=number(query.get('page')))
                counts = self.work._managed_issue_store.counts_by_assignee(self.auth.user(session, 'issues'))
                for row in result['items']:
                    row['open_issues'] = counts.get(row['username'], 0)
                return result
            if method == 'GET' and path == '/api/admin/account':
                result = self.auth.account_detail(session, query.get('username', ''))
                counts = self.work._managed_issue_store.counts_by_assignee(self.auth.user(session, 'issues'))
                return dict(result, open_issues=counts.get(result['username'], 0))
            if method == 'POST' and path == '/api/admin/account/save':
                return self.auth.manage_account(session, data)
        if path in ('/api/issues', '/api/issue', '/api/issue/operators', '/api/issue/action', '/api/issue/notification/retry'):
            current = self.auth.user(session, 'issues')
            if not self.work.managed_issues_ready:
                raise AdminError('Quản lý Issue chưa bật hoặc chưa hoàn tất kiểm tra chuyển dữ liệu.', 503)
            store = self.work.managed_issues
            if method == 'GET' and path == '/api/issues':
                if set(query) - {'kind', 'status', 'member_id', 'assignee', 'q', 'page'}:
                    raise AdminError('Bộ lọc Issue không hợp lệ.')
                result = store.list_issues(current, **dict(query, page=number(query.get('page'))))
                result['items'] = [self._issue_labels(item) for item in result['items']]
                return result
            if method == 'GET' and path == '/api/issue':
                result = store.detail(query.get('id'), current)
                result['issue'] = self._issue_labels(result['issue'])
                return result
            if method == 'GET' and path == '/api/issue/operators':
                return self.auth.issue_operators(session)
            if method == 'POST' and path == '/api/issue/notification/retry':
                if set(data) != {'id', 'confirm'} or data.get('confirm') is not True or type(data.get('id')) is not int:
                    raise AdminError('Cần xác nhận đúng thông báo gửi lại.')
                store.retry_notification(data['id'], current)
                return {'ok': True}
            if method == 'POST' and path == '/api/issue/action':
                if data.get('confirm') is not True:
                    raise AdminError('Cần xác nhận thao tác Issue.')
                payload = dict(data)
                identity, action, revision = payload.pop('id', None), payload.pop('action', None), payload.pop('revision', None)
                payload.pop('confirm', None)
                if 'target_actor' in payload:
                    raise AdminError('Không được tự khai danh tính người nhận.', 403)
                if action == 'assign':
                    operator = next((item for item in self.auth.issue_operators(session)['items']
                                     if item['username'] == payload.get('assignee')), None)
                    if operator is None:
                        raise AdminError('Người nhận đã bị khóa hoặc chưa có quyền Issue.', 409)
                    # Only active, explicitly authorized accounts returned by auth.
                    payload['target_actor'] = dict(operator, permissions=['issues'], active=True)
                if action == 'status' and 'public_reply' in payload:
                    if 'text' in payload:
                        raise AdminError('Phản hồi bị lặp.')
                    payload['text'] = payload.pop('public_reply')
                current = self.auth.user(session, 'issues')
                return store.act(identity, current, action, revision, **payload)
        if path.startswith('/api/member'):
            self.auth.user(session, 'members')
            if method == 'GET' and path == '/api/members':
                return await self.members.list_members(query=query.get('q', ''), status=query.get('status', ''),
                    link=query.get('link', ''), page=number(query.get('page')))
            if method == 'GET' and path == '/api/member':
                return await self.members.detail(query.get('id', ''))
            if method == 'POST' and path == '/api/member/lock':
                return await self.members.set_locked(data.get('member_id'), data.get('locked'), actor,
                    data.get('expected_revision'), confirm=data.get('confirm') is True)
            if method == 'POST' and path == '/api/member/link/preview':
                return await self.members.preview_link(data.get('member_id'), data.get('target_account_id'), actor)
            if method == 'POST' and path == '/api/member/link/confirm':
                if data.get('confirm') is not True:
                    raise AdminError('Cần xác nhận thay đổi liên kết.')
                return await self.members.confirm_link(data.get('token'), actor)
        if path in ('/api/sessions', '/api/history', '/api/context', '/api/context/reset', '/api/context/rebuild'):
            self.auth.user(session, 'conversations')
            if method == 'GET' and path == '/api/sessions':
                return self.conversations.sessions(member_id=query.get('member_id') or None,
                    chat_id=query.get('chat_id') or None, page=number(query.get('page')))
            if method == 'GET' and path == '/api/history':
                message_id = query.get('message_id')
                if message_id is not None and (not message_id.isascii() or not message_id.isdigit()
                                               or not 1 <= int(message_id) <= 9223372036854775807):
                    raise AdminError('Mã tin nhắn không hợp lệ.')
                return self.conversations.history(session_key=query.get('session_key') or None,
                    member_id=query.get('member_id') or None, chat_id=query.get('chat_id') or None,
                    start=boundary(query.get('start')), end=boundary(query.get('end'), end=True),
                    page=number(query.get('page')), message_id=int(message_id) if message_id is not None else None)
            if method == 'GET' and path == '/api/context':
                return self._context_view(self.conversations.context(query.get('session_key')), session)
            if method == 'POST' and path in ('/api/context/reset', '/api/context/rebuild'):
                if data.get('confirm') is not True:
                    raise AdminError('Cần xác nhận thay đổi ngữ cảnh.')
                action = self.conversations.reset_context if path.endswith('/reset') else self.conversations.rebuild_context
                return self._context_view(action(data.get('session_key'), actor, data.get('expected_revision')), session)
        if path in ('/api/memories', '/api/memory/save', '/api/memory/stop', '/api/memory/versions'):
            self.auth.user(session, 'memories')
            # Memories can reproduce private messages even after the source is hidden.
            self.auth.user(session, 'conversations')
            if method == 'GET' and path == '/api/memories':
                return self.conversations.list_memories(member_id=query.get('member_id') or None,
                                                       page=number(query.get('page')))
            if method == 'GET' and path == '/api/memory/versions':
                return self.conversations.memory_versions(query.get('id'))
            if method == 'POST' and path == '/api/memory/save':
                payload = dict(data)
                expected_revision = payload.pop('expected_revision', None)
                if isinstance(payload.get('expires'), str):
                    try:
                        expires = datetime.fromisoformat(payload['expires'])
                        if expires.tzinfo is None or expires.utcoffset() is None:
                            raise ValueError()
                        payload['expires'] = expires.timestamp()
                    except (ValueError, OverflowError):
                        raise AdminError('Hạn sử dụng cần ngày giờ ISO kèm múi giờ, ví dụ +07:00.') from None
                member = payload.get('member_id')
                store = self.work.registration.store
                if member and (not store or not store.db.execute(
                        'SELECT 1 FROM registration_members WHERE member_id=?', (member,)).fetchone()):
                    raise AdminError('Không tìm thấy mã thành viên cố định.')
                if payload.get('scope') == 'group':
                    group = payload.get('group_id') or payload.get('chat_id')
                    if group not in self.work.settings.allowed_groups:
                        raise AdminError('Nhóm này chưa được cấp quyền dùng bot.', 403)
                    payload['group_id'] = group
                    payload.pop('chat_id', None)
                return self.conversations.save_memory(payload, actor, expected_revision)
            if method == 'POST' and path == '/api/memory/stop':
                if data.get('confirm') is not True:
                    raise AdminError('Cần xác nhận ngừng sử dụng ghi nhớ.')
                return self.conversations.stop_memory(data.get('id'), actor, data.get('expected_revision'))
        if method == 'GET' and path == '/api/audit':
            if query.get('kind') == 'members':
                self.auth.user(session, 'members')
                return await self.members.audit(query.get('member_id'), page=number(query.get('page')))
            self.auth.user(session, 'conversations')
            # This combined audit includes memory before/after snapshots.
            # Apply the same visibility rule as memory history itself.
            self.auth.user(session, 'memories')
            return self.conversations.audit(member_id=query.get('member_id') or None,
                session_key=query.get('session_key') or None, page=number(query.get('page')))
        raise AdminError('Không tìm thấy chức năng.', 404)


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, app, loop, config):
        self.app, self.loop, self.config = app, loop, config
        self._slots = threading.BoundedSemaphore(16)
        super().__init__((config.host, config.port), Handler)
        self.origins = {config.public_url} if config.public_url else {
            'http://127.0.0.1:' + str(self.server_port), 'http://localhost:' + str(self.server_port)}
        self.hosts = {urlsplit(origin).netloc for origin in self.origins}

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # Request bodies and upstream errors must never enter HTTP error logs.
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'LabCoach'
    sys_version = ''

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, status, body, mime='application/json; charset=utf-8', cookie=None):
        # Closing a rejected POST with unread bytes can turn an intended 403
        # into a TCP reset on Windows. Consume only the same bounded body size
        # accepted by the endpoint; never drain chunked or oversized streams.
        if status >= 400 and self.command == 'POST' and not getattr(self, '_body_consumed', False):
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) == 1 and lengths[0].isascii() and lengths[0].isdigit() and not self.headers.get('Transfer-Encoding'):
                size = int(lengths[0])
                if 0 <= size <= 32768:
                    self.rfile.read(size)
                    self._body_consumed = True
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        for name, value in {
            'Content-Type': mime, 'Content-Length': str(len(raw)), 'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        }.items():
            self.send_header(name, value)
        if cookie is not None:
            secure = '; Secure' if self.server.config.public_url else ''
            self.send_header('Set-Cookie', f'{COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={28800 if cookie else 0}{secure}')
        if self.server.config.public_url:
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        self.end_headers()
        self.wfile.write(raw)

    def request_data(self):
        # The optional gateway credential belongs only to server-to-server
        # requests. It does not replace browser sessions, Origin or CSRF checks.
        secret = self.server.config.proxy_secret
        if secret:
            supplied = self.headers.get_all('X-LabCoach-Admin-Proxy', [])
            if (len(supplied) != 1 or len(supplied[0]) > 256 or not secrets.compare_digest(
                    supplied[0].encode('utf-8'), secret.encode('utf-8'))):
                raise AdminError('Yêu cầu không được phép.', 403)
        if (self.headers.get_all('Host') != [self.headers.get('Host')]
                or self.headers.get('Host') not in self.server.hosts):
            raise AdminError('Địa chỉ truy cập không được phép.', 403)
        if len(self.path) > 2500:
            raise AdminError('Đường dẫn quá dài.', 414)
        origins = self.headers.get_all('Origin', [])
        if (len(origins) > 1 or origins and origins[0] not in self.server.origins
                or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
            raise AdminError('Nguồn yêu cầu không được phép.', 403)
        parts = urlsplit(self.path)
        if parts.scheme or parts.netloc:
            raise AdminError('Đường dẫn không hợp lệ.')
        parsed = parse_qs(parts.query, keep_blank_values=True, max_num_fields=20)
        if any(len(v) != 1 for v in parsed.values()):
            raise AdminError('Tham số bị lặp.')
        query = {k: v[0] for k, v in parsed.items()}
        if self.command == 'POST':
            if self.headers.get('Origin') not in self.server.origins:
                raise AdminError('Nguồn yêu cầu không được phép.', 403)
            if self.headers.get('Transfer-Encoding'):
                raise AdminError('Kiểu truyền không hỗ trợ.')
            if self.headers.get_content_type() != 'application/json':
                raise AdminError('Cần gửi nội dung JSON.', 415)
            if len(self.headers.get_all('Content-Length', [])) != 1:
                raise AdminError('Thiếu độ dài yêu cầu.')
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= size <= 32768:
                raise AdminError('Nội dung quá dài.', 413)
            def pairs(items):
                result = {}
                for key, value in items:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result
            raw = self.rfile.read(size)
            self._body_consumed = True
            data = json.loads(raw, object_pairs_hook=pairs,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
            if not isinstance(data, dict):
                raise AdminError('Nội dung JSON không hợp lệ.')
        else:
            data = {}
        return parts.path, query, data

    def dispatch(self):
        self._body_consumed = False
        try:
            path, query, data = self.request_data()
            if self.command == 'GET' and path in ('/', '/admin.js', '/admin.css', '/static/admin.js', '/static/admin.css'):
                name = 'index.html' if path == '/' else path.rsplit('/', 1)[-1]
                mime = {'index.html': 'text/html', 'admin.js': 'text/javascript', 'admin.css': 'text/css'}[name]
                # Hermes loads this package below its plugin namespace. The
                # sealed gateway environment has no top-level hermes_zalo.
                return self.reply(200, files(__package__).joinpath('admin_static', name).read_bytes(), mime + '; charset=utf-8')
            if not path.startswith('/api/'):
                raise AdminError('Không tìm thấy trang.', 404)
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookie[COOKIE].value if COOKIE in cookie else ''
            auth = self.server.app.auth
            session, new_cookie = auth.session(token, create=self.command == 'GET' and path == '/api/session')
            if self.command == 'POST' and not secrets.compare_digest(
                    self.headers.get('X-CSRF-Token', ''), session['csrf']):
                raise AdminError('Phiên xác nhận không hợp lệ; tải lại trang.', 403)
            if path == '/api/session' and self.command == 'GET':
                try:
                    user = auth.user(session)
                except AdminError:
                    user = None
                return self.reply(200, dict(user=user, csrf=session['csrf'], mode=self.server.app.mode), cookie=new_cookie)
            if path == '/api/login' and self.command == 'POST':
                session, token = auth.login(session, data.get('username'), data.get('password'))
                return self.reply(200, dict(user=auth.user(session), csrf=session['csrf'], mode=self.server.app.mode), cookie=token)
            if path == '/api/logout' and self.command == 'POST':
                auth.logout(session)
                return self.reply(200, {'ok': True}, cookie='')
            auth.user(session)
            future = asyncio.run_coroutine_threadsafe(
                self.server.app.route(self.command, path, query, data, session), self.server.loop)
            try:
                result = future.result(timeout=45)
            except FutureTimeout:
                future.cancel()
                raise AdminError('Thao tác chưa xác định kết quả. Tải lại dữ liệu trước khi thử lại.', 503) from None
            self.reply(200, result)
        except AdminError as error:
            self.reply(error.status, {'error': str(error)})
        except IssueError as error:
            self.reply(error.status, {'error': str(error)})
        except ConversationConflict as error:
            self.reply(409, {'error': str(error)})
        except (AdminMemberError, ConversationError) as error:
            self.reply(400, {'error': str(error)})
        except (ValueError, TypeError, KeyError):
            self.reply(400, {'error': 'Nội dung yêu cầu không hợp lệ.'})
        except Exception:
            self.reply(503, {'error': 'Chưa xử lý được. Tải lại dữ liệu hoặc kiểm tra cấu hình; không tự thử lại thao tác ghi.'})

    do_GET = dispatch
    do_POST = dispatch


class AdminRuntime:
    def __init__(self, work, conversations, path, config, *, mode='local'):
        self.auth = AdminAuth(path)
        self.app = AdminApplication(work, conversations, self.auth, mode=mode)
        try:
            self.server = AdminServer(self.app, asyncio.get_running_loop(), config)
        except BaseException:
            self.auth.close()
            raise
        self.thread = threading.Thread(target=self.server.serve_forever, name='labcoach-admin', daemon=True)
        self.thread.start()

    async def close(self):
        await asyncio.to_thread(self.server.shutdown)
        await asyncio.to_thread(self.thread.join, 3)
        self.server.server_close()
        self.auth.close()
