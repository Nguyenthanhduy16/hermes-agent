"""Private, same-process administration API; all mutations run on the bot loop.

External binding is opt-in and requires a single configured HTTPS origin behind
a TLS proxy. Forwarded headers are never trusted for authentication or origin.
"""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta
import gzip
import hashlib
import html
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import os
import re
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

from .admin_auth import AdminAuth, AdminError
from .admin_members import AdminMemberError, MemberAdmin
from .admin_tickets import TicketAdmin
from .backup_service import BackupHTTPError, BackupService
from .conversations import ConversationConflict, ConversationError
from .issue_management import IssueError
from .reminders import VIETNAM_TZ
from .tickets.service import TicketError
from .tickets.providers import TicketsAccessDenied, TicketsSourceError

COOKIE = 'labcoach_admin'
CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                           "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
                           "object-src 'none'")
SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
    'Content-Security-Policy': CONTENT_SECURITY_POLICY,
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
    'Cross-Origin-Opener-Policy': 'same-origin', 'Cross-Origin-Resource-Policy': 'same-origin',
    'X-Robots-Tag': 'noindex, nofollow',
}
# Unauthenticated liveness probe for Docker/Railway/systemd. It reports only
# whether the HTTP worker and the bot event loop respond; never data or config.
HEALTH_PATH = '/healthz'
STATIC_ASSETS = {
    '/': ('console.html', 'text/html'),
    '/legacy.html': ('index.html', 'text/html'),
    '/admin.js': ('admin.js', 'text/javascript'),
    '/admin.css': ('admin.css', 'text/css'),
    '/static/admin.js': ('admin.js', 'text/javascript'),
    '/static/admin.css': ('admin.css', 'text/css'),
    '/static/console.js': ('console.js', 'text/javascript'),
    '/static/console.css': ('console.css', 'text/css'),
}
# Served HTML gets ?v=<content hash> on every same-origin script/style tag, so
# those exact URLs can be cached for a year and an upgrade still loads new code.
ASSET_REFERENCE = re.compile(r'(\s(?:src|href)=")(/static/[A-Za-z0-9._-]+\.(?:js|css))(")')
IMMUTABLE = 'public, max-age=31536000, immutable'
_GZIP_CACHE = {}
_GZIP_LOCK = threading.Lock()


def asset_table():
    table = dict(STATIC_ASSETS)
    # Feature scripts for console_sections; a missing file is simply not served.
    for name, mime in CONSOLE_EXTENSION_ASSETS:
        table['/static/' + name] = (name, mime)
    return table


def asset_bytes(name):
    # Hermes loads this package below its plugin namespace. The
    # sealed gateway environment has no top-level hermes_zalo.
    asset = files(__package__).joinpath('admin_static', name)
    return asset.read_bytes() if asset.is_file() else None


def asset_version(raw):
    return hashlib.sha256(raw).hexdigest()[:16]


def versioned_html(raw, table):
    """Append each referenced asset's content hash; unknown or missing paths stay as written."""
    def version(match):
        entry = table.get(match.group(2))
        content = asset_bytes(entry[0]) if entry and entry[1] != 'text/html' else None
        if content is None:
            return match.group(0)
        return match.group(1) + match.group(2) + '?v=' + asset_version(content) + match.group(3)
    return ASSET_REFERENCE.sub(version, raw.decode('utf-8')).encode('utf-8')


def gzipped(raw, tag):
    """Compressed public static bytes, memoised by content hash (bounded)."""
    with _GZIP_LOCK:
        cached = _GZIP_CACHE.get(tag)
    if cached is None:
        cached = gzip.compress(raw, compresslevel=6, mtime=0)
        with _GZIP_LOCK:
            if len(_GZIP_CACHE) >= 64:
                _GZIP_CACHE.clear()
            _GZIP_CACHE[tag] = cached
    return cached


def accepts_gzip(header):
    for item in (header or '').split(','):
        name, _, params = item.strip().partition(';')
        if name.strip().lower() != 'gzip':
            continue
        key, _, value = params.replace(' ', '').lower().partition('=')
        try:
            return key != 'q' or float(value) > 0
        except ValueError:
            return False
    return False


def error_page(status, message):
    """Minimal, script-free page for a browser navigation that cannot be served."""
    text = html.escape(message)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Hermes Agent Console</title></head><body><main><h1>{status}</h1><p>{text}</p>'
            '<p><a href="/">Open the Hermes Agent Console</a></p></main></body></html>').encode()
# Feature assets for console_sections (see console.html). Fixed allowlist.
CONSOLE_EXTENSIONS = ('observe', 'bench', 'integrations', 'operations', 'briefing')
CONSOLE_EXTENSION_ASSETS = tuple((f'console-{name}.{ext}', mime) for name in CONSOLE_EXTENSIONS
                                 for ext, mime in (('js', 'text/javascript'), ('css', 'text/css')))


def number(value, default=1):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = int(value if value not in (None, '') else default)
        if not 1 <= result <= 1000000:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise AdminError('Invalid page number.') from None


def boundary(value, *, end=False):
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=VIETNAM_TZ)
        return (parsed + timedelta(days=1) if end else parsed).timestamp()
    except (TypeError, ValueError):
        raise AdminError('Dates must use year-month-day format.') from None


class AdminApplication:
    def __init__(self, work, conversations, auth, *, mode='local'):
        self.work, self.conversations, self.auth, self.mode = work, conversations, auth, mode
        self.members = MemberAdmin(work, conversations=conversations)
        self.tickets = TicketAdmin(work, auth)
        self._assistant = None
        self._management = None
        self._workspace = None
        self._controls = None

    def _workspace_service(self):
        if self._workspace is None:
            from .admin_assistant import AssistantAdmin
            from .admin_workspace import WorkspaceAdmin
            home = getattr(self, 'runtime_home', None) or getattr(self._assistant, 'home', None)
            home = AssistantAdmin(self.work, self.conversations, mode=self.mode, home=home).home
            self._workspace = WorkspaceAdmin(home, mode=self.mode)
        return self._workspace

    def _require_owner(self, session):
        current = self.auth.user(session)
        if current['role'] != 'faculty':
            raise AdminError('Only the system owner can manage agent operations.', 403)
        return current

    def _controls_service(self):
        if self._controls is None:
            from .admin_controls import ControlsAdmin
            self._controls = ControlsAdmin(self._workspace_service().home, mode=self.mode)
        return self._controls

    def _section_context(self):
        from .admin_assistant import AssistantAdmin
        from .console_sections import ConsoleContext
        if self._assistant is None:
            self._assistant = AssistantAdmin(self.work, self.conversations, mode=self.mode,
                                             home=getattr(self, 'runtime_home', None))
        return ConsoleContext(home=self._workspace_service().home, mode=self.mode,
                              assistant_dir=self._assistant.assistant_dir)

    async def _extension_route(self, section, pieces, method, query, data, session, actor):
        """Feature sections registered in console_sections; owner-only, audited actions."""
        from .console_sections import clean_query
        self._require_owner(session)
        context = self._section_context()
        if len(pieces) == 1:
            if method != 'GET':
                raise AdminError('Use the confirmed action endpoint to make changes.', 405)
            result = await asyncio.to_thread(section.snapshot, context, clean_query(section, query))
            self._require_owner(session)
            return result
        if pieces[1] != 'action' or section.action is None:
            raise AdminError('Assistant management page not found.', 404)
        if method != 'POST':
            raise AdminError('Management actions require POST.', 405)
        if query:
            raise AdminError('Management actions do not accept query parameters.')
        result = await asyncio.to_thread(section.action, context, data)
        current = self._require_owner(session)
        with self.auth.transaction() as db:
            # Section and action id only: never values, prompts, endpoints or credentials.
            self.auth._audit(db, actor, 'console_management_changed', current['username'],
                             after={'section': section.name, 'action': str(data.get('action'))[:80]})
        return result

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
        result['member_name'] = 'Sender not verified'
        if registrations and issue.get('member_id'):
            with registrations.lock:
                row = registrations.db.execute('SELECT name FROM registration_requests WHERE member_id=? '
                    'ORDER BY updated DESC LIMIT 1', (issue['member_id'],)).fetchone()
            if row:
                result['member_name'] = row['name']
        return result

    def _account_issue_counts(self, session):
        current = self.auth.user(session)
        if current['role'] != 'faculty':
            raise AdminError('Only the system owner can manage administrator accounts.', 403)
        store = getattr(self.work, '_managed_issue_store', None)
        # IssueStore already authorizes faculty by role. Account management
        # does not require an additional, explicitly granted issues capability.
        return store.counts_by_assignee(current) if store else {}

    async def route(self, method, path, query, data, session):
        actor = 'web:' + self.auth.user(session)['username']
        if path.startswith('/api/assistant/'):
            from . import console_sections
            pieces = path.removeprefix('/api/assistant/').split('/')
            extension = console_sections.get(pieces[0]) if len(pieces) in (1, 2) else None
            if extension is not None:
                return await self._extension_route(extension, pieces, method, query, data, session, actor)
            from .admin_controls import CONTROL_SECTIONS
            if len(pieces) == 2 and pieces[1] == 'action' and pieces[0] in CONTROL_SECTIONS:
                self._require_owner(session)
                if method != 'POST':
                    raise AdminError('Management actions require POST.', 405)
                if query:
                    raise AdminError('Management actions do not accept query parameters.')
                result = await asyncio.to_thread(self._controls_service().action, pieces[0], data,
                    authorize=lambda: self._require_owner(session))
                current = self._require_owner(session)
                with self.auth.transaction() as db:
                    # No values, prompts, names, endpoints or credentials in audit data.
                    self.auth._audit(db, actor, 'console_management_changed', current['username'],
                        after={'section': pieces[0], 'action': data['action']})
                return result
            if len(pieces) == 1 and pieces[0] in CONTROL_SECTIONS:
                self._require_owner(session)
                if method != 'GET':
                    raise AdminError('Use the confirmed management action endpoint to make changes.', 405)
                if query:
                    raise AdminError('Unsupported management filter.')
                result = await asyncio.to_thread(self._controls_service().snapshot, pieces[0])
                self._require_owner(session)
                return result
        if path in ('/api/assistant/files', '/api/assistant/kanban', '/api/assistant/chat'):
            current = self._require_owner(session)
            service = self._workspace_service()
            if path.endswith('/files'):
                if method != 'GET':
                    raise AdminError('Workspace files are read-only.', 405)
                if set(query) - {'path'}:
                    raise AdminError('Unsupported workspace filter.')
                result = await asyncio.to_thread(service.files, query.get('path', ''))
            else:
                if query:
                    raise AdminError('This operation does not accept query parameters.')
                if method not in ('GET', 'POST'):
                    raise AdminError('Unsupported request method.', 405)
                if path.endswith('/kanban'):
                    if method == 'GET':
                        result = await asyncio.to_thread(service.kanban)
                    else:
                        result = await asyncio.to_thread(service.task_action, data, actor,
                            authorize=lambda: self._require_owner(session))
                elif method == 'GET':
                    result = service.chat_status()
                else:
                    result = await service.chat(data)
            self._require_owner(session)
            if method == 'POST':
                with self.auth.transaction() as db:
                    self.auth._audit(db, actor, 'console_task_' + data.get('action', 'change')
                        if path.endswith('/kanban') else 'console_chat_sent', current['username'],
                        after={'id': result.get('id') or result.get('item', {}).get('id')})
            return result
        if path in ('/api/assistant/sessions', '/api/assistant/history'):
            self.auth.user(session, 'conversations')
            if method != 'GET':
                raise AdminError('Runtime history is read-only.', 405)
            allowed = {'page'} if path.endswith('/sessions') else {'page', 'session_id'}
            if set(query) - allowed or path.endswith('/history') and not query.get('session_id'):
                raise AdminError('Invalid runtime history filter.')
            page = number(query.get('page'))
            if self.mode == 'demo':
                # Demo credentials must never inspect the actual bot home, even
                # when a caller bypasses the UI and requests this API directly.
                return dict(available=False, source='demo', items=[], total=0, page=page, limit=20,
                            message='The demo uses synthetic managed conversations and never reads the real runtime.')
            from .admin_assistant import AssistantAdmin
            from .admin_runtime_history import RuntimeHistory
            home = getattr(self, 'runtime_home', None)
            reader = RuntimeHistory(AssistantAdmin(self.work, self.conversations, mode=self.mode, home=home).home)
            result = await asyncio.to_thread(reader.read, page=page,
                                             session_id=query.get('session_id'))
            current = self.auth.user(session, 'conversations')
            if query.get('session_id') and result.get('available'):
                # Audit the access, never the message content, before releasing
                # a runtime transcript to the authenticated browser.
                with self.auth.transaction() as db:
                    self.auth._audit(db, actor, 'runtime_history_viewed', current['username'],
                                     after={'session_id': query['session_id']})
            return result
        if path.startswith('/api/assistant/'):
            self._require_owner(session)
            section = path.removeprefix('/api/assistant/')
            from .admin_management import ManagementAdmin, SECTIONS as MANAGEMENT_SECTIONS
            if section in MANAGEMENT_SECTIONS:
                if method != 'GET':
                    raise AdminError('This management page is read-only.', 405)
                if set(query) - ({'q', 'level', 'file', 'component', 'lines'} if section == 'logs' else set()):
                    raise AdminError('Unsupported management filter.')
                if self._management is None:
                    self._management = ManagementAdmin(self._workspace_service().home, mode=self.mode)
                result = await asyncio.to_thread(self._management.snapshot, section, query or None)
                self._require_owner(session)
                return result
            if section not in ('overview', 'runtime', 'skills', 'routines', 'usage', 'users'):
                raise AdminError('Assistant management page not found.', 404)
            if method != 'GET':
                raise AdminError('Operations metadata is read-only.', 405)
            if query:
                raise AdminError('Invalid operations filter.')
            if self._assistant is None:
                from .admin_assistant import AssistantAdmin
                self._assistant = AssistantAdmin(self.work, self.conversations, mode=self.mode)
            result = await asyncio.to_thread(self._assistant.snapshot, section)
            if self.auth.user(session)['role'] != 'faculty':
                raise AdminError('Your operations permissions changed. Please sign in again.', 403)
            return result
        if self.work is None and not path.startswith('/api/admin/'):
            raise AdminError('This feature requires the managed bot data store. Standalone mode supports Hermes runtime and history.', 503)
        if path in ('/api/admin/accounts', '/api/admin/account', '/api/admin/account/save'):
            # Authentication module checks faculty again inside account transactions.
            if method == 'GET' and path == '/api/admin/accounts':
                result = self.auth.list_accounts(session, query=query.get('q', ''), page=number(query.get('page')))
                counts = self._account_issue_counts(session)
                for row in result['items']:
                    row['open_issues'] = counts.get(row['username'], 0)
                return result
            if method == 'GET' and path == '/api/admin/account':
                result = self.auth.account_detail(session, query.get('username', ''))
                counts = self._account_issue_counts(session)
                return dict(result, open_issues=counts.get(result['username'], 0))
            if method == 'POST' and path == '/api/admin/account/save':
                return self.auth.manage_account(session, data)
        if path in ('/api/tickets', '/api/ticket', '/api/ticket/action'):
            self.auth.user(session, 'tickets')
            if method == 'GET' and path == '/api/tickets':
                if set(query) - {'q', 'status', 'page'}:
                    raise AdminError('Invalid shift exchange filter.')
                return await self.tickets.list_tickets(session, query=query.get('q', ''),
                    status=query.get('status', ''), page=number(query.get('page')))
            if method == 'GET' and path == '/api/ticket':
                if set(query) != {'id'}:
                    raise AdminError('A shift exchange request ID is required.')
                return await self.tickets.detail(session, query.get('id'))
            if method == 'POST' and path == '/api/ticket/action':
                if query:
                    raise AdminError('Invalid shift exchange approval parameters.')
                return await self.tickets.action(session, data)
        if path in ('/api/issues', '/api/issue', '/api/issue/operators', '/api/issue/action', '/api/issue/notification/retry'):
            current = self.auth.user(session, 'issues')
            if not self.work.managed_issues_ready:
                raise AdminError('Issue management is disabled or its data migration checks are incomplete.', 503)
            store = self.work.managed_issues
            if method == 'GET' and path == '/api/issues':
                if set(query) - {'kind', 'status', 'member_id', 'assignee', 'q', 'page'}:
                    raise AdminError('Invalid issue filter.')
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
                    raise AdminError('Confirm the specific notification before resending it.')
                store.retry_notification(data['id'], current)
                return {'ok': True}
            if method == 'POST' and path == '/api/issue/action':
                if data.get('confirm') is not True:
                    raise AdminError('Confirm the issue action.')
                payload = dict(data)
                identity, action, revision = payload.pop('id', None), payload.pop('action', None), payload.pop('revision', None)
                payload.pop('confirm', None)
                if 'target_actor' in payload:
                    raise AdminError('Recipient identity must be verified by the server.', 403)
                if action == 'assign':
                    operator = next((item for item in self.auth.issue_operators(session)['items']
                                     if item['username'] == payload.get('assignee')), None)
                    if operator is None:
                        raise AdminError('The recipient is locked or does not have issue permissions.', 409)
                    # Only active, explicitly authorized accounts returned by auth.
                    payload['target_actor'] = dict(operator, permissions=['issues'], active=True)
                if action == 'status' and 'public_reply' in payload:
                    if 'text' in payload:
                        raise AdminError('Duplicate reply fields.')
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
                    raise AdminError('Confirm the account link change.')
                return await self.members.confirm_link(data.get('token'), actor)
            if method == 'POST' and path == '/api/member/registration/preview':
                if query or set(data) != {'member_id', 'phone', 'reason'}:
                    raise AdminError('Invalid registration correction details.')
                result = await self.members.preview_registration(data['member_id'], data['phone'], data['reason'], actor)
                self.auth.user(session, 'members')
                return result
            if method == 'POST' and path == '/api/member/registration/confirm':
                if query or set(data) != {'preview_token', 'confirm'} or data['confirm'] is not True:
                    raise AdminError('Confirm that the account owner has been verified.')
                return await self.members.confirm_registration(data['preview_token'], actor,
                    authorize=lambda: self.auth.user(session, 'members'))
            if method == 'POST' and path == '/api/member/registration/reset':
                if (query or set(data) != {'member_id', 'request_id', 'expected_revision', 'reason', 'confirm'} or
                        data['confirm'] is not True):
                    raise AdminError('Confirm the specific registration to reset.')
                return await self.members.reset_registration(data['member_id'], data['request_id'],
                    data['expected_revision'], data['reason'], actor, confirm=True,
                    authorize=lambda: self.auth.user(session, 'members'))
        if path in ('/api/sessions', '/api/history', '/api/context', '/api/context/reset', '/api/context/rebuild'):
            self.auth.user(session, 'conversations')
            if method == 'GET' and path == '/api/sessions':
                return self.conversations.sessions(member_id=query.get('member_id') or None,
                    chat_id=query.get('chat_id') or None, page=number(query.get('page')))
            if method == 'GET' and path == '/api/history':
                message_id = query.get('message_id')
                if message_id is not None and (not message_id.isascii() or not message_id.isdigit()
                                               or not 1 <= int(message_id) <= 9223372036854775807):
                    raise AdminError('Invalid message ID.')
                return self.conversations.history(session_key=query.get('session_key') or None,
                    member_id=query.get('member_id') or None, chat_id=query.get('chat_id') or None,
                    start=boundary(query.get('start')), end=boundary(query.get('end'), end=True),
                    page=number(query.get('page')), message_id=int(message_id) if message_id is not None else None)
            if method == 'GET' and path == '/api/context':
                return self._context_view(self.conversations.context(query.get('session_key')), session)
            if method == 'POST' and path in ('/api/context/reset', '/api/context/rebuild'):
                if data.get('confirm') is not True:
                    raise AdminError('Confirm the conversation context change.')
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
                        raise AdminError('Expiry must use an ISO date and time with a timezone, for example +07:00.') from None
                member = payload.get('member_id')
                store = self.work.registration.store
                if member and (not store or not store.db.execute(
                        'SELECT 1 FROM registration_members WHERE member_id=?', (member,)).fetchone()):
                    raise AdminError('The permanent member ID was not found.')
                if payload.get('scope') == 'group':
                    group = payload.get('group_id') or payload.get('chat_id')
                    if group not in self.work.settings.allowed_groups:
                        raise AdminError('This group is not authorized to use the bot.', 403)
                    payload['group_id'] = group
                    payload.pop('chat_id', None)
                return self.conversations.save_memory(payload, actor, expected_revision)
            if method == 'POST' and path == '/api/memory/stop':
                if data.get('confirm') is not True:
                    raise AdminError('Confirm that this memory should stop being used.')
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
        raise AdminError('Feature not found.', 404)


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, app, loop, config, *, backup_service=None):
        self.app, self.loop, self.config = app, loop, config
        self.backup_service = backup_service or BackupService.from_mapping({})
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
    server_version = 'HermesAdmin'
    sys_version = ''

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, status, body, mime='application/json; charset=utf-8', cookie=None, extra_headers=None,
              cache='no-store'):
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
        if status >= 400 and isinstance(body, dict) and 'error' in body and self.is_navigation():
            # A person following a link should see a page, never raw JSON.
            raw, mime = error_page(status, body['error']), 'text/html; charset=utf-8'
        self.send_response(status)
        headers = {'Cache-Control': cache, **SECURITY_HEADERS}
        if status != 304:
            headers = {'Content-Type': mime, 'Content-Length': str(len(raw)), **headers}
        for name, value in headers.items():
            self.send_header(name, value)
        if cookie is not None:
            secure = '; Secure' if self.server.config.public_url else ''
            self.send_header('Set-Cookie', f'{COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={28800 if cookie else 0}{secure}')
        if self.server.config.public_url:
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != 'HEAD' and status != 304:
            self.wfile.write(raw)

    def is_navigation(self):
        """A top-level browser page load (or a client without fetch metadata asking for HTML)."""
        if self.command not in ('GET', 'HEAD') or urlsplit(self.path).path.startswith('/api/'):
            return False
        mode, dest = self.headers.get('Sec-Fetch-Mode'), self.headers.get('Sec-Fetch-Dest')
        if mode is not None or dest is not None:
            return mode == 'navigate' and dest in (None, 'document')
        return 'text/html' in (self.headers.get('Accept') or '')

    def static_asset(self, name, mime, table, query):
        raw = asset_bytes(name)
        if raw is None:
            raise AdminError('Page not found.', 404)
        if mime == 'text/html':
            raw = versioned_html(raw, table)
        tag = hashlib.sha256(raw).hexdigest()[:32]
        headers = {'Vary': 'Accept-Encoding'}
        if len(raw) >= 1024 and accepts_gzip(self.headers.get('Accept-Encoding')):
            raw, tag = gzipped(raw, tag), tag + '-gz'
            headers['Content-Encoding'] = 'gzip'
        headers['ETag'] = '"' + tag + '"'
        # Scripts/styles are public code without data. The exact ?v=<hash> URL
        # written into the page is immutable; any other URL (no or stale v)
        # revalidates, so an upgrade never mixes with stale JS.
        # HTML stays no-store: no back/forward cache restores a page after logout.
        if mime == 'text/html':
            cache = 'no-store'
        elif query.get('v') == tag[:16]:  # tag is the identity sha256, set before gzip
            cache = IMMUTABLE
        else:
            cache = 'no-cache'
        matches = [item.strip().removeprefix('W/') for item in (self.headers.get('If-None-Match') or '').split(',')]
        if headers['ETag'] in matches:
            headers.pop('Content-Encoding', None)
            return self.reply(304, b'', cache=cache, extra_headers=headers)
        return self.reply(200, raw, mime + '; charset=utf-8', cache=cache, extra_headers=headers)

    def health(self):
        if self.command not in ('GET', 'HEAD'):
            return self.reply(405, {'status': 'method-not-allowed'}, extra_headers={'Allow': 'GET, HEAD'})
        try:
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), self.server.loop).result(timeout=3)
            self.server.app.auth.ping()
        except Exception:
            return self.reply(503, {'status': 'unavailable'})
        return self.reply(200, {'status': 'ok'})

    def backup_download(self):
        # A single exact machine endpoint bypasses the Vercel proxy secret.
        # It still requires its own secret, canonical host and no browser Origin.
        if self.path != '/api/backup':
            raise BackupHTTPError('Invalid backup path.', 400)
        if self.command != 'GET':
            raise BackupHTTPError('Backup requests only support GET.', 405)
        if (self.headers.get_all('Host') != [self.headers.get('Host')]
                or self.headers.get('Host') not in self.server.hosts):
            raise BackupHTTPError('This request host is not allowed.', 403)
        if any(self.headers.get_all(name) for name in ('Origin', 'Sec-Fetch-Site', 'Sec-Fetch-Mode', 'Sec-Fetch-Dest', 'Cookie')):
            raise BackupHTTPError('Backup requests are restricted to the configured service.', 403)
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get_all('Transfer-Encoding') or lengths not in ([], ['0']):
            raise BackupHTTPError('Backup requests must not contain a body.', 400)
        download = self.server.backup_service.download(self.headers.get_all('X-LabCoach-Backup-Key', []))
        return self.reply(200, download.body, 'application/octet-stream', extra_headers={
            'Content-Disposition': 'attachment; filename="labcoach-backup.lcbak"',
            'X-LabCoach-Backup-SHA256': download.sha256,
        })

    def request_data(self):
        # The optional gateway credential belongs only to server-to-server
        # requests. It does not replace browser sessions, Origin or CSRF checks.
        secret = self.server.config.proxy_secret
        if secret:
            supplied = self.headers.get_all('X-LabCoach-Admin-Proxy', [])
            if (len(supplied) != 1 or len(supplied[0]) > 256 or not secrets.compare_digest(
                    supplied[0].encode('utf-8'), secret.encode('utf-8'))):
                raise AdminError('Request not authorized.', 403)
        if (self.headers.get_all('Host') != [self.headers.get('Host')]
                or self.headers.get('Host') not in self.server.hosts):
            raise AdminError('This request host is not allowed.', 403)
        if len(self.path) > 2500:
            raise AdminError('The request path is too long.', 414)
        parts = urlsplit(self.path)
        if parts.scheme or parts.netloc:
            raise AdminError('Invalid request path.')
        origins = self.headers.get_all('Origin', [])
        # A link from e-mail/Zalo is a cross-site top-level navigation. Public
        # pages (no data; the cookie stays SameSite=Strict) may load or show a
        # friendly error. Every /api/ request, frame, subresource and POST stays strict.
        page_load = self.is_navigation()
        if (len(origins) > 1 or origins and origins[0] not in self.server.origins
                or (self.headers.get('Sec-Fetch-Site') == 'cross-site' and not page_load)):
            raise AdminError('This request origin is not allowed.', 403)
        parsed = parse_qs(parts.query, keep_blank_values=True, max_num_fields=20)
        if any(len(v) != 1 for v in parsed.values()):
            raise AdminError('Duplicate request parameters.')
        query = {k: v[0] for k, v in parsed.items()}
        if self.command == 'POST':
            if self.headers.get('Origin') not in self.server.origins:
                raise AdminError('This request origin is not allowed.', 403)
            if self.headers.get('Transfer-Encoding'):
                raise AdminError('Unsupported transfer encoding.')
            if self.headers.get_content_type() != 'application/json':
                raise AdminError('A JSON request body is required.', 415)
            if len(self.headers.get_all('Content-Length', [])) != 1:
                raise AdminError('The request content length is required.')
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= size <= 32768:
                raise AdminError('The request body is too large.', 413)
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
                raise AdminError('Invalid JSON request body.')
        else:
            data = {}
        return parts.path, query, data

    def dispatch(self):
        self._body_consumed = False
        try:
            request_path = urlsplit(self.path).path
            if request_path == HEALTH_PATH:
                # Exempt from Host/proxy checks so local and platform probes work.
                return self.health()
            if request_path == '/api/backup':
                return self.backup_download()
            path, query, data = self.request_data()
            static_assets = asset_table()
            if self.command in ('GET', 'HEAD') and path in static_assets:
                return self.static_asset(*static_assets[path], static_assets, query)
            if self.command == 'HEAD':
                raise AdminError('Method not supported.', 405)
            if not path.startswith('/api/'):
                raise AdminError('Page not found.', 404)
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookie[COOKIE].value if COOKIE in cookie else ''
            auth = self.server.app.auth
            session, new_cookie = auth.session(token, create=self.command == 'GET' and path == '/api/session')
            if self.command == 'POST' and not secrets.compare_digest(
                    self.headers.get('X-CSRF-Token', ''), session['csrf']):
                raise AdminError('Invalid session confirmation. Reload this page.', 403)
            if path == '/api/session' and self.command == 'GET':
                try:
                    user = auth.user(session)
                except AdminError:
                    user = None
                return self.reply(200, dict(user=user, csrf=session['csrf'], mode=self.server.app.mode,
                                           runtime_history=self.server.app.work is None), cookie=new_cookie)
            if path == '/api/login' and self.command == 'POST':
                session, token = auth.login(session, data.get('username'), data.get('password'))
                return self.reply(200, dict(user=auth.user(session), csrf=session['csrf'], mode=self.server.app.mode,
                                           runtime_history=self.server.app.work is None), cookie=token)
            if path == '/api/logout' and self.command == 'POST':
                auth.logout(session)
                return self.reply(200, {'ok': True}, cookie='')
            if path == '/api/password/change' and self.command == 'POST':
                if query:
                    raise AdminError('Invalid password change parameters.')
                # Password hashing runs on the bounded HTTP worker, keeping
                # the bot loop available while auth rechecks the live session.
                result = auth.change_password(session, data)
                return self.reply(200, result, cookie='')
            auth.user(session)
            future = asyncio.run_coroutine_threadsafe(
                self.server.app.route(self.command, path, query, data, session), self.server.loop)
            try:
                result = future.result(timeout=45)
            except FutureTimeout:
                future.cancel()
                raise AdminError('The operation result is uncertain. Reload the data before trying again.', 503) from None
            self.reply(200, result)
        except BackupHTTPError as error:
            self.reply(error.status, {'error': str(error)}, extra_headers=(
                {'Retry-After': str(error.retry_after)} if error.retry_after is not None else None))
        except AdminError as error:
            self.reply(error.status, {'error': str(error)})
        except IssueError as error:
            self.reply(error.status, {'error': str(error)})
        except TicketError as error:
            self.reply(error.status, {'error': str(error)})
        except TicketsAccessDenied as error:
            self.reply(403, {'error': str(error)})
        except TicketsSourceError as error:
            self.reply(503, {'error': str(error)})
        except ConversationConflict as error:
            self.reply(409, {'error': str(error)})
        except (AdminMemberError, ConversationError) as error:
            self.reply(400, {'error': str(error)})
        except (ValueError, TypeError, KeyError):
            self.reply(400, {'error': 'Invalid request data.'})
        except Exception:
            self.reply(503, {'error': 'The request could not be completed. Reload the data or check configuration; do not automatically retry a write.'})

    do_GET = dispatch
    do_POST = dispatch
    do_HEAD = dispatch


class AdminRuntime:
    def __init__(self, work, conversations, path, config, *, mode='local', backup_values=None):
        # The published demo password works only in the synthetic demo.
        self.auth = AdminAuth(path, allow_default_passwords=mode == 'demo')
        self.app = AdminApplication(work, conversations, self.auth, mode=mode)
        try:
            self.backup_service = BackupService.from_mapping({} if mode == 'demo' else (
                os.environ if backup_values is None else backup_values))
            self.server = AdminServer(self.app, asyncio.get_running_loop(), config, backup_service=self.backup_service)
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
