"""Real HTTP boundary tests over loopback, with disposable synthetic stores."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import gzip
import hashlib
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from hermes_zalo.admin_auth import AdminAuth, AdminError
from hermes_zalo.admin_config import AdminConfig
from hermes_zalo.admin_web import AdminApplication, AdminServer, COOKIE
from hermes_zalo.conversations import ConversationStore
from hermes_zalo.events import IncomingMessage
from hermes_zalo.registration import RegistrationStore


class AdminHttpTests(unittest.TestCase):
    PASSWORD = 'Synthetic-password-for-tests-123'

    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.now = time.time()
        self.auth = AdminAuth(root / 'auth.sqlite3', clock=lambda: self.now)
        self.registrations = RegistrationStore(root / 'registrations.sqlite3')
        self.message = IncomingMessage('private-member', 'synthetic-member', 'test-message', 'PRIVATE', 'Mình thích trả lời ngắn.')
        person = SimpleNamespace(phone='0900000001', name='Thành viên mô phỏng', source_key='synthetic',
                                 fingerprint='synthetic-person', name_unique=True)
        self.member = self.registrations.request(self.message, person, auto_approve=True)
        self.conversations = ConversationStore(root / 'history.sqlite3')
        self.conversations.record_incoming(self.message, self.member.member_id)
        self.conversations.record_outgoing(self.message, 'Mình hiểu rồi.', delivery_key='test-reply')
        self.conversations.complete_turn(self.message)
        self.work = SimpleNamespace(registration=SimpleNamespace(store=self.registrations),
                                    settings=SimpleNamespace(allowed_groups=frozenset({'allowed-group'})))
        self.app = AdminApplication(self.work, self.conversations, self.auth)
        self.members = SimpleNamespace(list_members=AsyncMock(return_value={'items': [{'name': 'Synthetic member'}], 'total': 1}),
                                       set_locked=AsyncMock(return_value={'ok': True}), audit=AsyncMock(return_value={'items': []}))
        self.app.members = self.members
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()
        def run():
            asyncio.set_event_loop(self.loop)
            self.loop.call_soon(ready.set)
            self.loop.run_forever()
        self.loop_thread = threading.Thread(target=run, name='admin-test-loop')
        self.loop_thread.start()
        self.assertTrue(ready.wait(3))
        self.server = AdminServer(self.app, self.loop, AdminConfig(enabled=True, conversations=True, port=0))
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.http_thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), name='admin-test-http')
        self.http_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.http_thread.join(3)
        self.server.server_close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(3)
        self.assertFalse(self.http_thread.is_alive())
        self.assertFalse(self.loop_thread.is_alive())
        self.loop.close()
        self.auth.close()
        self.conversations.close()
        self.registrations.close()
        self.temp.cleanup()

    def request(self, method, path, *, client=None, data=None, origin=True, csrf=True, headers=None, raw=None):
        outgoing = dict(headers or {})
        if client and client.get('cookie'):
            outgoing.setdefault('Cookie', f"{COOKIE}={client['cookie']}")
        if origin is not False and (method == 'POST' or isinstance(origin, str)):
            outgoing['Origin'] = self.origin if origin is True else origin
        if method == 'POST':
            outgoing.setdefault('Content-Type', 'application/json')
            if csrf and client:
                outgoing['X-CSRF-Token'] = client.get('csrf', '') if csrf is True else csrf
            body = raw if raw is not None else json.dumps(data or {}).encode()
        else:
            body = None
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=outgoing)
            response = connection.getresponse()
            response_headers = dict(response.getheaders())
            payload = response.read()
            parsed = json.loads(payload) if response_headers.get('Content-Type', '').startswith('application/json') else payload.decode()
            if client is not None and 'Set-Cookie' in response_headers:
                cookie = SimpleCookie(response_headers['Set-Cookie'])
                client['cookie'] = cookie[COOKIE].value
            if client is not None and isinstance(parsed, dict) and 'csrf' in parsed:
                client['csrf'] = parsed['csrf']
            return response.status, parsed, response_headers
        finally:
            connection.close()

    def client(self, permissions=None, username='operator'):
        client = {}
        self.assertEqual(200, self.request('GET', '/api/session', client=client)[0])
        if permissions is not None:
            self.auth.create_account(username, self.PASSWORD, permissions)
            status, _, _ = self.request('POST', '/api/login', client=client,
                                        data={'username': username, 'password': self.PASSWORD})
            self.assertEqual(200, status)
        return client

    def test_registration_correction_requires_members_csrf_and_exact_payload(self):
        preview = '/api/member/registration/preview'
        confirm = '/api/member/registration/confirm'
        payload = dict(member_id=self.member.member_id, phone='0900000002', reason='Xác minh đúng chủ tài khoản')
        self.members.preview_registration = AsyncMock(return_value={'preview_token': 'p' * 32})
        self.members.confirm_registration = AsyncMock(return_value={'ok': True})
        anonymous = self.client()
        reader = self.client(['conversations'], 'reader')
        operator = self.client(['members'])
        for path, data in ((preview, payload), (confirm, {'preview_token': 'p' * 32, 'confirm': True})):
            for client, status in ((anonymous, 401), (reader, 403)):
                self.assertEqual(status, self.request('POST', path, client=client, data=data)[0])
            self.assertEqual(403, self.request('POST', path, client=operator, data=data, csrf=False)[0])
            self.assertEqual(403, self.request('POST', path, client=operator, data=data, origin='https://untrusted.invalid')[0])
            self.assertEqual(400, self.request('POST', path + '?extra=1', client=operator, data=data)[0])
            self.assertEqual(400, self.request('POST', path, client=operator, data=dict(data, role='faculty'))[0])
        self.members.preview_registration.assert_not_awaited()
        self.members.confirm_registration.assert_not_awaited()
        self.assertEqual(200, self.request('POST', preview, client=operator, data=payload)[0])
        self.members.preview_registration.assert_awaited_once_with(self.member.member_id, '0900000002',
                                                                  payload['reason'], 'web:operator')
        self.assertEqual(400, self.request('POST', confirm, client=operator,
            data={'preview_token': 'p' * 32, 'confirm': False})[0])
        self.assertEqual(200, self.request('POST', confirm, client=operator,
            data={'preview_token': 'p' * 32, 'confirm': True})[0])
        guard = self.members.confirm_registration.await_args.kwargs['authorize']
        self.auth.create_account('operator', self.PASSWORD, [], replace=True)
        with self.assertRaises(AdminError):
            guard()

    def test_registration_reset_requires_members_csrf_confirmation_and_exact_payload(self):
        path = '/api/member/registration/reset'
        payload = dict(member_id=self.member.member_id, request_id=self.member.id, expected_revision=1,
                       reason='Cho thành viên đăng ký lại đúng thông tin', confirm=True)
        self.members.reset_registration = AsyncMock(return_value={'ok': True, 'status': 'reset'})
        anonymous = self.client()
        reader = self.client(['conversations'], 'reader')
        operator = self.client(['members'])
        for client, status in ((anonymous,401),(reader,403)):
            self.assertEqual(status,self.request('POST',path,client=client,data=payload)[0])
        for options in ({'csrf':False},{'origin':'https://untrusted.invalid'}):
            self.assertEqual(403,self.request('POST',path,client=operator,data=payload,**options)[0])
        for data in ({},dict(payload,confirm=False),dict(payload,user_id='other-user')):
            self.assertEqual(400,self.request('POST',path,client=operator,data=data)[0])
        self.assertEqual(400,self.request('POST',path+'?extra=1',client=operator,data=payload)[0])
        self.members.reset_registration.assert_not_awaited()
        self.assertEqual(200,self.request('POST',path,client=operator,data=payload)[0])
        call=self.members.reset_registration.await_args
        self.assertEqual((self.member.member_id,self.member.id,1,payload['reason'],'web:operator'),call.args)
        self.assertTrue(call.kwargs['confirm'])
        self.auth.create_account('operator',self.PASSWORD,[],replace=True)
        with self.assertRaises(AdminError):
            call.kwargs['authorize']()

    def test_password_change_checks_session_origin_csrf_and_clears_cookie(self):
        client = self.client([])
        payload = dict(current_password=self.PASSWORD, new_password='Synthetic-new-password-123',
                       confirm_password='Synthetic-new-password-123')
        for path, options, status in (
            ('/api/password/change', {'csrf': False}, 403),
            ('/api/password/change', {'origin': 'https://untrusted.invalid'}, 403),
            ('/api/password/change?username=someone', {}, 400),
        ):
            self.assertEqual(status, self.request('POST', path, client=client, data=payload, **options)[0])
        anonymous = self.client()
        self.assertEqual(401, self.request('POST', '/api/password/change', client=anonymous, data=payload)[0])
        other = dict(client)
        status, result, headers = self.request('POST', '/api/password/change', client=client, data=payload)
        self.assertEqual(200, status)
        self.assertEqual({'ok': True}, result)
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual('', client['cookie'])
        self.assertEqual(401, self.request('GET', '/api/members', client=other)[0])
        self.request('GET', '/api/session', client=client)
        self.assertEqual(200, self.request('POST', '/api/login', client=client,
            data={'username': 'operator', 'password': payload['new_password']})[0])

    def test_password_change_requires_current_password_and_does_not_logout_on_typo(self):
        client = self.client([])
        status, result, _ = self.request('POST', '/api/password/change', client=client,
            data=dict(current_password='Wrong-current-password', new_password='Synthetic-new-password-123',
                      confirm_password='Synthetic-new-password-123'))
        self.assertEqual(400, status)
        self.assertEqual('Your current password is incorrect.', result['error'])
        self.assertIsNotNone(self.request('GET', '/api/session', client=client)[1]['user'])

    def test_no_data_before_login_static_page_is_safe_and_cookie_is_protected(self):
        self.assertEqual(401, self.request('GET', '/api/history')[0])
        anon = self.client()
        for path in ('/api/members', '/api/history', '/api/memories', '/api/audit'):
            self.assertEqual(401, self.request('GET', path, client=anon)[0])
        status, page, headers = self.request('GET', '/')
        self.assertEqual(200, status)
        self.assertNotIn(self.member.name, page)
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertEqual('DENY', headers['X-Frame-Options'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        _, _, headers = self.request('GET', '/api/session', client={})
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.members.list_members.assert_not_awaited()

    def test_console_operations_require_owner_role_and_cannot_mutate(self):
        anonymous = self.client()
        reader = self.client(['conversations'], 'reader')
        self.auth.create_account('owner', self.PASSWORD, [], role='faculty')
        owner = self.client()
        self.assertEqual(200, self.request('POST', '/api/login', client=owner,
            data={'username': 'owner', 'password': self.PASSWORD})[0])
        snapshots = []
        self.app._assistant = SimpleNamespace(snapshot=lambda section: snapshots.append(section) or
            {'section': section, 'available': False, 'items': [], 'source': 'synthetic'})
        self.app._controls = self.app._assistant
        for section in ('overview', 'runtime', 'skills', 'routines', 'usage', 'users'):
            path = '/api/assistant/' + section
            self.assertEqual(401, self.request('GET', path, client=anonymous)[0])
            self.assertEqual(403, self.request('GET', path, client=reader)[0])
            self.assertEqual(200, self.request('GET', path, client=owner)[0])
            self.assertEqual(405, self.request('POST', path, client=owner, data={})[0])
            self.assertEqual(400, self.request('GET', path + '?home=/other', client=owner)[0])
        self.assertEqual(6, len(snapshots))
        # Owner status never implicitly grants access to conversation contents.
        self.assertEqual(403, self.request('GET', '/api/assistant/sessions', client=owner)[0])
        self.assertEqual(403, self.request('GET', '/api/history', client=owner)[0])

    def test_standalone_console_keeps_conversation_permission_and_static_boundary(self):
        self.app.work = None
        self.app.runtime_home = Path(self.temp.name) / 'missing-runtime'
        self.assertTrue(self.request('GET', '/api/session', client={})[1]['runtime_history'])
        reader = self.client(['conversations'], 'runtime-reader')
        status, body, _ = self.request('GET', '/api/assistant/sessions', client=reader)
        self.assertEqual(200, status)
        self.assertFalse(body['available'])
        self.assertFalse(self.app.runtime_home.exists())
        for path in ('/api/assistant/history', '/api/assistant/sessions?home=/other'):
            self.assertEqual(400, self.request('GET', path, client=reader)[0])
        self.assertEqual(405, self.request('POST', '/api/assistant/sessions', client=reader, data={})[0])
        self.assertEqual(503, self.request('GET', '/api/memories', client=reader)[0])
        for path, text in (('/', 'Agent Console'), ('/legacy.html', 'LabCoach'),
                           ('/static/console.js', 'runtime_history'), ('/static/console.css', '.sidebar')):
            status, content, headers = self.request('GET', path)
            self.assertEqual(200, status)
            self.assertIn(text, content)
            self.assertEqual('no-store' if path in ('/', '/legacy.html') else 'no-cache', headers['Cache-Control'])
        self.assertEqual(404, self.request('GET', '/static/../console-admin.sqlite3')[0])

    def test_management_workspace_routes_enforce_owner_filters_and_csrf(self):
        from hermes_zalo.admin_workspace import WorkspaceAdmin
        self.app.mode = 'demo'
        home = Path(self.temp.name) / 'untouched-home'
        self.app._workspace = WorkspaceAdmin(home, mode='demo', environ={})
        self.auth.create_account('owner', self.PASSWORD, [], role='faculty')
        owner = self.client()
        self.assertEqual(200, self.request('POST', '/api/login', client=owner,
            data={'username': 'owner', 'password': self.PASSWORD})[0])
        reader = self.client(['conversations'], 'reader')
        for section in ('models', 'logs', 'plugins', 'mcp', 'channels', 'webhooks', 'pairing',
                        'profiles', 'config', 'keys', 'documentation', 'files', 'chat', 'kanban'):
            path = '/api/assistant/' + section
            self.assertEqual(403, self.request('GET', path, client=reader)[0], section)
            self.assertEqual(200, self.request('GET', path, client=owner)[0], section)
            self.assertEqual(400, self.request('GET', path + '?home=/other', client=owner)[0], section)
            if section not in ('chat', 'kanban'):
                self.assertEqual(405, self.request('POST', path, client=owner, data={})[0], section)
        for section, data in (
            ('kanban', {'action': 'create', 'title': 'Review report', 'confirm': True}),
            ('chat', {'message': 'Xin chào', 'history': []}),
        ):
            path = '/api/assistant/' + section
            self.assertEqual(403, self.request('POST', path, client=reader, data=data)[0])
            self.assertEqual(403, self.request('POST', path, client=owner, data=data, csrf=False)[0])
            self.assertEqual(200, self.request('POST', path, client=owner, data=data)[0])
        self.assertFalse(home.exists())
        self.assertEqual(403, self.request('GET', '/api/history', client=owner)[0])

    def test_console_kanban_persists_and_conflicts_through_http(self):
        from hermes_zalo.admin_workspace import WorkspaceAdmin
        home = Path(self.temp.name) / 'console-home'
        self.app._workspace = WorkspaceAdmin(home, environ={})
        self.auth.create_account('owner', self.PASSWORD, [], role='faculty')
        owner = self.client()
        self.request('POST', '/api/login', client=owner,
            data={'username': 'owner', 'password': self.PASSWORD})
        path = '/api/assistant/kanban'
        status, body, _ = self.request('POST', path, client=owner,
            data={'action': 'create', 'title': 'Nghiên cứu', 'confirm': True})
        self.assertEqual(200, status)
        item = body['item']
        self.assertEqual('Nghiên cứu', item['title'])
        change = {'action': 'update', 'id': item['id'], 'expected_revision': item['revision'],
                  'status': 'review', 'confirm': True}
        self.assertEqual(200, self.request('POST', path, client=owner, data=change)[0])
        self.assertEqual(409, self.request('POST', path, client=owner, data=change)[0])
        self.app._workspace = WorkspaceAdmin(home, environ={})
        status, board, _ = self.request('GET', path, client=owner)
        self.assertEqual(200, status)
        self.assertEqual('review', board['items'][0]['status'])
        self.assertEqual(2, board['items'][0]['revision'])

    def test_management_actions_require_owner_csrf_revision_and_secret_free_audit(self):
        from hermes_zalo.admin_controls import ControlsAdmin
        self.app._controls = ControlsAdmin(Path(self.temp.name) / 'controls-home', environ={})
        self.auth.create_account('owner', self.PASSWORD, [], role='faculty')
        owner = self.client()
        self.request('POST', '/api/login', client=owner, data={'username': 'owner', 'password': self.PASSWORD})
        reader = self.client(['conversations'], 'reader')
        status, snapshot, _ = self.request('GET', '/api/assistant/models', client=owner)
        self.assertEqual(200, status)
        descriptor = next(item for item in snapshot['actions'] if item['id'] == 'save_main')
        payload = dict(action='save_main', expected_revision=descriptor['revision'], confirm=True,
                       values={'model': 'vendor/synthetic-model', 'provider': 'auto'})
        path = '/api/assistant/models/action'
        self.assertEqual(403, self.request('POST', path, client=reader, data=payload)[0])
        self.assertEqual(403, self.request('POST', path, client=owner, data=payload, csrf=False)[0])
        self.assertEqual(405, self.request('GET', path, client=owner)[0])
        self.assertEqual(400, self.request('POST', path + '?home=/other', client=owner, data=payload)[0])
        self.assertEqual(200, self.request('POST', path, client=owner, data=payload)[0])
        self.assertEqual(409, self.request('POST', path, client=owner, data=payload)[0])
        status, snapshot, _ = self.request('GET', '/api/assistant/keys', client=owner)
        self.assertEqual(200, status)
        descriptor = next(item for item in snapshot['actions'] if item['id'] == 'set_key' and item['target'] == 'OPENAI_API_KEY')
        secret = 'synthetic-private-key-never-in-response-123456'
        status, result, _ = self.request('POST', '/api/assistant/keys/action', client=owner, data=dict(
            action='set_key', expected_revision=descriptor['revision'], confirm=True,
            values=dict(variable='OPENAI_API_KEY', value=secret)))
        self.assertEqual(200, status)
        self.assertNotIn(secret, json.dumps(result))
        snapshot = self.request('GET', '/api/assistant/keys', client=owner)[1]
        self.assertNotIn(secret, json.dumps(snapshot))
        audit = '\n'.join(str(tuple(row)) for row in self.auth.db.execute('SELECT * FROM admin_auth_audit'))
        self.assertNotIn(secret, audit)
        self.assertNotIn('vendor/synthetic-model', audit)
        self.assertIn('console_management_changed', audit)

    def test_automation_management_actions_work_through_http(self):
        from hermes_zalo.admin_controls import ControlsAdmin
        self.app._controls = ControlsAdmin(Path(self.temp.name) / 'automation-home', environ={})
        self.auth.create_account('owner', self.PASSWORD, [], role='faculty')
        owner = self.client()
        self.request('POST', '/api/login', client=owner, data={'username': 'owner', 'password': self.PASSWORD})
        for section, id, values in (
            ('skills', 'create_skill', dict(name='research-brief', description='Summarize sources',
                category='', new_category='research', instructions='Read sources and produce a concise summary.')),
            ('routines', 'create_routine', dict(name='Daily research', schedule='0 9 * * *',
                prompt='Summarize new research.', skills='', repeat=None, paused=True)),
        ):
            status, snapshot, _ = self.request('GET', '/api/assistant/' + section, client=owner)
            self.assertEqual(200, status)
            descriptor = next(item for item in snapshot['actions'] if item['id'] == id)
            status, result, _ = self.request('POST', '/api/assistant/' + section + '/action', client=owner,
                data=dict(action=id, expected_revision=descriptor['revision'], confirm=True, values=values))
            self.assertEqual(200, status, result)
            self.assertTrue(result['ok'])

    def test_demo_credentials_cannot_read_real_runtime_by_direct_api(self):
        self.app.mode = 'demo'
        reader = self.client(['conversations'], 'demo-reader')
        with patch('hermes_zalo.admin_runtime_history.RuntimeHistory') as runtime:
            for path in ('/api/assistant/sessions', '/api/assistant/history?session_id=real-session'):
                status, body, _ = self.request('GET', path, client=reader)
                self.assertEqual(200, status)
                self.assertEqual('demo', body['source'])
                self.assertEqual([], body['items'])
            runtime.assert_not_called()

    def test_revoked_sessions_cannot_receive_snapshots_that_finish_after_revocation(self):
        self.auth.create_account('owner', self.PASSWORD, ['conversations'], role='faculty')
        owner = self.client()
        self.request('POST', '/api/login', client=owner,
                     data={'username': 'owner', 'password': self.PASSWORD})
        live, _ = self.auth.session(owner['cookie'])
        self.app._assistant = SimpleNamespace(snapshot=lambda section:
            self.auth.logout(live) or {'private': 'must not be returned'})
        status, body, _ = self.request('GET', '/api/assistant/usage', client=owner)
        self.assertEqual(401, status)
        self.assertNotIn('private', body)

    def test_login_rotates_cookie_and_csrf_then_logout_invalidates_it(self):
        self.auth.create_account('operator', self.PASSWORD, {'members'})
        client = self.client()
        before = dict(client)
        status, response, _ = self.request('POST', '/api/login', client=client,
                                          data={'username': 'operator', 'password': self.PASSWORD})
        self.assertEqual(200, status)
        self.assertNotEqual(before, client)
        self.assertEqual('operator', response['user']['username'])
        self.assertEqual(401, self.request('GET', '/api/members', client=before)[0])
        self.assertEqual(200, self.request('GET', '/api/members', client=client)[0])
        old = dict(client)
        self.assertEqual(200, self.request('POST', '/api/logout', client=client)[0])
        self.assertEqual('', client['cookie'])
        self.assertEqual(401, self.request('GET', '/api/members', client=old)[0])

    def test_csrf_and_explicit_confirmation_required_before_mutation(self):
        client = self.client({'members'})
        for token in (False, 'wrong-token'):
            status, _, _ = self.request('POST', '/api/member/lock', client=client,
                                       csrf=token, data={'member_id': self.member.member_id, 'locked': True, 'confirm': True})
            self.assertEqual(403, status)
        self.members.set_locked.assert_not_awaited()
        self.assertEqual(400, self.request('POST', '/api/member/link/confirm', client=client, data={'token': 'test'})[0])
        self.assertEqual(200, self.request('POST', '/api/member/lock', client=client,
            data={'member_id': self.member.member_id, 'locked': True, 'expected_revision': 2, 'confirm': True})[0])
        self.members.set_locked.assert_awaited_once_with(self.member.member_id, True, 'web:operator', 2, confirm=True)

    def test_role_boundaries_private_history_and_memory_versions_require_both_permissions(self):
        memory = self.conversations.save_memory({'content': 'Thông tin riêng', 'member_id': self.member.member_id, 'scope': 'personal'}, 'fixture')
        path = '/api/memory/versions?' + urlencode({'id': memory['id']})
        for username, permissions in (('memberadmin', {'members'}), ('memoryonly', {'memories'}), ('historyonly', {'conversations'})):
            with self.subTest(permissions=permissions):
                client = self.client(permissions, username)
                if 'conversations' not in permissions:
                    self.assertEqual(403, self.request('GET', '/api/history', client=client)[0])
                if 'members' not in permissions:
                    self.assertEqual(403, self.request('GET', '/api/members', client=client)[0])
                self.assertEqual(403, self.request('GET', path, client=client)[0])
                self.assertEqual(403, self.request('GET', '/api/audit', client=client)[0])
        both = self.client({'conversations', 'memories'}, 'curator')
        status, versions, _ = self.request('GET', path, client=both)
        self.assertEqual(200, status)
        self.assertEqual(memory['content'], versions['items'][0]['snapshot']['content'])

    def test_unknown_origin_host_and_cross_site_requests_are_rejected(self):
        client = self.client({'members'})
        for method in ('GET', 'POST'):
            self.assertEqual(403, self.request(method, '/api/members', client=client, origin='https://untrusted.invalid')[0])
            self.assertEqual(403, self.request(method, '/api/members', client=client, headers={'Host': 'untrusted.invalid'})[0])
        self.assertEqual(403, self.request('POST', '/api/logout', client=client, origin=False)[0])
        self.assertEqual(403, self.request('GET', '/api/members', client=client, headers={'Sec-Fetch-Site': 'cross-site'})[0])
        self.members.list_members.assert_not_awaited()
        self.assertEqual(200, self.request('GET', '/api/members', client=client, origin=self.origin)[0])

    def test_proxy_secret_protects_static_session_login_and_authenticated_requests(self):
        secret = 'synthetic-server-only-proxy-secret-123456'
        client = self.client({'members'})
        self.server.config = replace(self.server.config, proxy_secret=secret)
        expected = {'error': 'Request not authorized.'}
        for method, path in (('GET', '/'), ('GET', '/static/admin.js'), ('GET', '/static/admin.css'),
                             ('GET', '/api/session'), ('POST', '/api/login'),
                             ('GET', '/api/members'), ('POST', '/api/member/lock')):
            for supplied in (None, 'incorrect-proxy-secret', 'x' * 257):
                with self.subTest(method=method, path=path, supplied_length=len(supplied or '')):
                    outgoing = {} if supplied is None else {'X-LabCoach-Admin-Proxy': supplied}
                    status, body, headers = self.request(method, path, client=client, headers=outgoing)
                    self.assertEqual(403, status)
                    self.assertEqual(expected, body)
                    self.assertNotIn(secret, json.dumps({'body': body, 'headers': headers}))
        self.members.list_members.assert_not_awaited()
        self.members.set_locked.assert_not_awaited()

    def test_matching_proxy_secret_preserves_login_permission_origin_and_csrf_boundaries(self):
        secret = 'synthetic-server-only-proxy-secret-123456'
        self.server.config = replace(self.server.config, proxy_secret=secret)
        headers = {'X-LabCoach-Admin-Proxy': secret}
        client = {}
        self.assertEqual(200, self.request('GET', '/', headers=headers)[0])
        status, body, response_headers = self.request('GET', '/api/session', client=client, headers=headers)
        self.assertEqual(200, status)
        self.assertNotIn(secret, json.dumps({'body': body, 'headers': response_headers}))
        self.assertEqual(401, self.request('GET', '/api/members', client=client, headers=headers)[0])
        self.auth.create_account('operator', self.PASSWORD, {'members'})
        self.assertEqual(200, self.request('POST', '/api/login', client=client, headers=headers,
            data={'username': 'operator', 'password': self.PASSWORD})[0])
        self.assertEqual(200, self.request('GET', '/api/members', client=client, headers=headers)[0])
        self.assertEqual(403, self.request('GET', '/api/history', client=client, headers=headers)[0])
        self.assertEqual(403, self.request('POST', '/api/logout', client=client, headers=headers, csrf=False)[0])
        self.assertEqual(403, self.request('POST', '/api/logout', client=client, headers=headers,
                                         origin='https://untrusted.invalid')[0])
        self.assertEqual(200, self.request('POST', '/api/logout', client=client, headers=headers)[0])

    def test_duplicate_proxy_headers_are_rejected(self):
        secret = 'synthetic-server-only-proxy-secret-123456'
        self.server.config = replace(self.server.config, proxy_secret=secret)
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.putrequest('GET', '/api/session')
            connection.putheader('X-LabCoach-Admin-Proxy', secret)
            connection.putheader('X-LabCoach-Admin-Proxy', secret)
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(403, response.status)
            self.assertEqual({'error': 'Request not authorized.'}, json.loads(response.read()))
            self.assertIsNone(response.getheader('Set-Cookie'))
        finally:
            connection.close()

    def test_unknown_account_and_bad_password_share_error_and_rate_limit(self):
        client = self.client()
        self.auth.create_account('operator', self.PASSWORD, {'members'})
        _, nonexistent, _ = self.request('POST', '/api/login', client=client,
                                       data={'username': 'unknown-user', 'password': self.PASSWORD})
        for _ in range(5):
            status, bad_password, _ = self.request('POST', '/api/login', client=client,
                                                  data={'username': 'operator', 'password': 'wrong-password'})
            self.assertEqual(401, status)
            self.assertEqual(nonexistent, bad_password)
        self.assertEqual(429, self.request('POST', '/api/login', client=client,
                                          data={'username': 'operator', 'password': self.PASSWORD})[0])
        self.now += 601
        self.assertEqual(200, self.request('POST', '/api/login', client=client,
                                          data={'username': 'operator', 'password': self.PASSWORD})[0])

    def test_password_reset_revokes_cookie_and_already_captured_session_snapshot(self):
        client = self.client({'members'})
        old_session, _ = self.auth.session(client['cookie'])
        self.auth.create_account('operator', self.PASSWORD + '-new', {'members'}, replace=True)
        self.assertEqual(401, self.request('GET', '/api/members', client=client)[0])
        with self.assertRaises(AdminError):
            self.auth.user(old_session, 'members')
        self.members.list_members.assert_not_awaited()
        fake = dict(old_session, hash='invented-session-hash')
        with self.assertRaises(AdminError):
            self.auth.user(fake)

    def test_expired_or_logged_out_session_cannot_authorize_or_start_login(self):
        client = self.client({'members'})
        session, _ = self.auth.session(client['cookie'])
        self.now = session['expires'] + 1
        with self.assertRaises(AdminError):
            self.auth.user(session)
        with self.assertRaises(AdminError):
            self.auth.login(session, 'operator', self.PASSWORD)
        self.assertEqual(401, self.request('GET', '/api/members', client=client)[0])

    def test_concurrent_stale_context_writes_have_one_winner(self):
        client = self.client({'conversations'})
        context = self.conversations.context(self.message.session_key)
        data = {'session_key': self.message.session_key, 'expected_revision': context['revision'], 'confirm': True}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.request, 'POST', '/api/context/reset', client=dict(client), data=data) for _ in range(2)]
            statuses = sorted(future.result()[0] for future in futures)
        self.assertEqual([200, 409], statuses)
        self.assertEqual(context['revision'] + 1, self.conversations.context(self.message.session_key)['revision'])

    def test_memory_edit_accepts_revision_versions_and_stale_edit_conflicts(self):
        client = self.client({'conversations', 'memories'})
        data = {'scope': 'personal', 'member_id': self.member.member_id, 'content': 'Trả lời ngắn.'}
        status, memory, _ = self.request('POST', '/api/memory/save', client=client, data=data)
        self.assertEqual(200, status)
        update = dict(data, id=memory['id'], expected_revision=memory['revision'], content='Trả lời ngắn, có nguồn.')
        status, updated, _ = self.request('POST', '/api/memory/save', client=client, data=update)
        self.assertEqual(200, status)
        self.assertEqual(memory['revision'] + 1, updated['revision'])
        self.assertEqual(409, self.request('POST', '/api/memory/save', client=client, data=update)[0])
        _, versions, _ = self.request('GET', '/api/memory/versions?' + urlencode({'id': memory['id']}), client=client)
        self.assertEqual(2, versions['total'])

    def test_memory_group_scope_requires_confirmation_and_existing_group(self):
        client = self.client({'conversations', 'memories'})
        data = {'scope': 'group', 'chat_id': 'allowed-group', 'content': 'Thông tin dùng chung.'}
        self.assertEqual(400, self.request('POST', '/api/memory/save', client=client, data=data)[0])
        self.assertEqual(403, self.request('POST', '/api/memory/save', client=client,
                                          data=dict(data, chat_id='unknown-group', confirm_shared=True))[0])
        status, memory, _ = self.request('POST', '/api/memory/save', client=client, data=dict(data, confirm_shared=True))
        self.assertEqual(200, status)
        self.assertEqual('allowed-group', memory['group_id'])

    def test_malformed_or_duplicate_json_rejected_without_private_exception_output(self):
        client = self.client({'members'})
        for raw in (b'{"locked":true,"locked":false}', b'{"locked":NaN}', b'[]', b'not-json'):
            self.assertEqual(400, self.request('POST', '/api/member/lock', client=client, raw=raw)[0])
        self.assertEqual(415, self.request('POST', '/api/member/lock', client=client,
                                          headers={'Content-Type': 'text/plain'})[0])
        self.members.list_members.side_effect = RuntimeError('PRIVATE_DATABASE_SECRET')
        status, error, _ = self.request('GET', '/api/members', client=client)
        self.assertEqual(503, status)
        self.assertNotIn('PRIVATE_DATABASE_SECRET', json.dumps(error))

    def test_login_reset_during_password_hash_does_not_issue_session(self):
        client = self.client()
        self.auth.create_account('operator', self.PASSWORD, {'members'})
        session, _ = self.auth.session(client['cookie'])
        from hermes_zalo.admin_auth import password_matches
        def revoke_while_hashing(password, stored):
            matched = password_matches(password, stored)
            self.auth.logout(session)
            return matched
        with patch('hermes_zalo.admin_auth.password_matches', side_effect=revoke_while_hashing):
            self.assertEqual(401, self.request('POST', '/api/login', client=client,
                data={'username': 'operator', 'password': self.PASSWORD})[0])
        self.assertEqual(0, self.auth.db.execute('SELECT COUNT(*) FROM admin_sessions WHERE username IS NOT NULL').fetchone()[0])

    def test_invalid_password_input_never_authenticates_via_timing_placeholder(self):
        client = self.client()
        self.auth.create_account('operator', 'invalid-password', {'members'})
        for bad in (None, 123, [], 'short'):
            status, _, _ = self.request('POST', '/api/login', client=client,
                                        data={'username': 'operator', 'password': bad})
            self.assertEqual(401, status)
        self.assertEqual(200, self.request('POST', '/api/login', client=client,
                                          data={'username': 'operator', 'password': 'invalid-password'})[0])

    def test_static_asset_paths_match_html_and_reject_traversal(self):
        for path, mime in (('/static/admin.js', 'text/javascript'), ('/static/admin.css', 'text/css')):
            status, body, headers = self.request('GET', path)
            self.assertEqual(200, status)
            self.assertGreater(len(body), 100)
            self.assertTrue(headers['Content-Type'].startswith(mime))
        self.assertEqual(404, self.request('GET', '/static/../admin_auth.py')[0])

    def test_source_message_filter_is_exact_combined_and_requires_conversation_permission(self):
        source = self.conversations.history()['items'][0]
        denied = self.client({'members'}, 'memberadmin')
        query = urlencode({'message_id': source['id']})
        self.assertEqual(403, self.request('GET', '/api/history?' + query, client=denied)[0])
        reader = self.client({'conversations'}, 'reader')
        status, history, _ = self.request('GET', '/api/history?' + query, client=reader)
        self.assertEqual(200, status)
        self.assertEqual([source['id']], [item['id'] for item in history['items']])
        _, history, _ = self.request('GET', '/api/history?' + query + '&member_id=someone-else', client=reader)
        self.assertEqual(0, history['total'])
        for bad in ('', '0', '-1', '1.5', 'invalid'):
            self.assertEqual(400, self.request('GET', '/api/history?' + urlencode({'message_id': bad}), client=reader)[0])

    def test_memory_expiry_from_ui_requires_offset_and_is_stored_as_timestamp(self):
        client = self.client({'conversations', 'memories'})
        expiry = '2099-10-13T23:59:59+07:00'
        data = {'scope': 'personal', 'member_id': self.member.member_id, 'content': 'Trả lời ngắn.', 'expires': expiry}
        status, memory, _ = self.request('POST', '/api/memory/save', client=client, data=data)
        self.assertEqual(200, status)
        self.assertEqual(datetime.fromisoformat(expiry).timestamp(), memory['expires'])
        for invalid in ('2099-10-13T23:59:59', 'bad-date'):
            self.assertEqual(400, self.request('POST', '/api/memory/save', client=client, data=dict(data, expires=invalid))[0])

    def test_context_read_and_mutation_responses_redact_memories_without_memory_permission(self):
        memory = self.conversations.save_memory({'content': 'MEMORY-ONLY-PRIVATE-CONTENT', 'member_id': self.member.member_id,
                                                 'scope': 'personal'}, 'fixture')
        self.conversations.prepare_context(self.message, self.member.member_id)
        reader = self.client({'conversations'}, 'reader')
        query = urlencode({'session_key': self.message.session_key})
        status, context, _ = self.request('GET', '/api/context?' + query, client=reader)
        self.assertEqual(200, status)
        for action in (None, 'reset', 'rebuild'):
            if action:
                _, context, _ = self.request('POST', '/api/context/' + action, client=reader, data={
                    'session_key': self.message.session_key, 'expected_revision': context['revision'], 'confirm': True})
            self.assertTrue(context['memories_redacted'])
            self.assertEqual([], context['memories'])
            self.assertEqual([], context['included_memory_ids'])
            self.assertNotIn(memory['id'], json.dumps(context))
            self.assertNotIn(memory['content'], json.dumps(context))
        curator = self.client({'conversations', 'memories'}, 'curator')
        _, context, _ = self.request('GET', '/api/context?' + query, client=curator)
        self.assertFalse(context['memories_redacted'])
        self.assertEqual(memory['content'], context['memories'][0]['content'])

    def test_ticket_routes_require_explicit_permission_and_keep_csrf_boundary(self):
        tickets = SimpleNamespace(list_tickets=AsyncMock(return_value={'enabled': True, 'items': [], 'total': 0}),
                                  detail=AsyncMock(return_value={'enabled': True, 'ticket': {'id': 'DC-TEST'}}),
                                  action=AsyncMock(return_value={'enabled': True, 'ticket': {'status': 'completed'}}))
        self.app.tickets = tickets
        denied = self.client({'members'}, 'memberadmin')
        for path in ('/api/tickets', '/api/ticket?id=DC-TEST'):
            self.assertEqual(403, self.request('GET', path, client=denied)[0])
        payload = dict(id='DC-TEST', action='approve', expected_version=2, reason='', confirm=True)
        self.assertEqual(403, self.request('POST', '/api/ticket/action', client=denied, data=payload)[0])
        tickets.list_tickets.assert_not_awaited()
        tickets.action.assert_not_awaited()
        reviewer = self.client({'tickets'}, 'reviewer')
        self.assertEqual(200, self.request('GET', '/api/tickets?q=alpha&status=pending_manager&page=2', client=reviewer)[0])
        self.assertEqual(200, self.request('GET', '/api/ticket?id=DC-TEST', client=reviewer)[0])
        self.assertEqual(403, self.request('GET', '/api/history', client=reviewer)[0])
        for csrf in (False, 'bad-token'):
            self.assertEqual(403, self.request('POST', '/api/ticket/action', client=reviewer, data=payload, csrf=csrf)[0])
        self.assertEqual(403, self.request('POST', '/api/ticket/action', client=reviewer, data=payload, origin='https://other.invalid')[0])
        tickets.action.assert_not_awaited()
        self.assertEqual(200, self.request('POST', '/api/ticket/action', client=reviewer, data=payload)[0])
        tickets.action.assert_awaited_once()
        for path in ('/api/tickets?member_id=forged', '/api/ticket?id=DC-TEST&actor=forged'):
            self.assertEqual(400, self.request('GET', path, client=reviewer)[0])
        self.auth.create_account('reviewer', self.PASSWORD, [], replace=True)
        self.assertEqual(401, self.request('GET', '/api/tickets', client=reviewer)[0])


    def raw(self, method, path, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, path, headers=dict(headers or {}))
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    NAVIGATE = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-User': '?1', 'Accept': 'text/html,application/xhtml+xml'}

    def test_cross_site_link_opens_page_but_api_and_writes_stay_strict(self):
        # Regression: clicking the console link in e-mail/Zalo returned raw JSON 403.
        for path, text in (('/', 'Agent Console'), ('/legacy.html', 'LabCoach')):
            for headers in (self.NAVIGATE, {'Accept': 'text/html'}, {}):
                status, response_headers, body = self.raw('GET', path, {**headers, 'Sec-Fetch-Site': 'cross-site'}
                                                          if headers else headers)
                self.assertEqual(200, status, (path, headers))
                self.assertTrue(response_headers['Content-Type'].startswith('text/html'))
                self.assertIn(text, body.decode())
                self.assertNotIn('Set-Cookie', response_headers)
        status, headers, _ = self.raw('HEAD', '/', self.NAVIGATE)
        self.assertEqual(200, status)
        # Cross-site embedding, subresource loads and every API call stay rejected.
        client = self.client({'members'})
        cookie = {'Cookie': f"{COOKIE}={client['cookie']}"}
        for method, path, headers in (
                ('GET', '/', {**self.NAVIGATE, 'Sec-Fetch-Dest': 'iframe'}),
                ('GET', '/', {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'no-cors', 'Sec-Fetch-Dest': 'script'}),
                ('GET', '/static/console.js', {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'no-cors',
                                               'Sec-Fetch-Dest': 'script'}),
                ('GET', '/api/session', self.NAVIGATE),
                ('GET', '/api/members', {**self.NAVIGATE, **cookie}),
                ('GET', '/', {**self.NAVIGATE, 'Origin': 'https://untrusted.invalid'}),
                ('GET', '/', {**self.NAVIGATE, 'Host': 'untrusted.invalid'})):
            with self.subTest(path=path, headers=headers):
                status, response_headers, _ = self.raw(method, path, headers)
                self.assertEqual(403, status)
                self.assertNotIn('Set-Cookie', response_headers)
        self.assertEqual(403, self.request('POST', '/api/logout', client=client,
                                           headers={k: v for k, v in self.NAVIGATE.items()})[0])
        self.assertEqual(403, self.request('POST', '/api/logout', client=client, origin='https://untrusted.invalid',
                                           headers=dict(self.NAVIGATE))[0])
        self.members.list_members.assert_not_awaited()
        self.assertEqual(200, self.request('GET', '/api/members', client=client)[0])

    def test_browser_navigation_errors_render_html_while_api_errors_stay_json(self):
        status, headers, body = self.raw('GET', '/missing-page', self.NAVIGATE)
        self.assertEqual(404, status)
        self.assertTrue(headers['Content-Type'].startswith('text/html'))
        self.assertIn('Open the Hermes Agent Console', body.decode())
        self.assertNotIn('<script', body.decode())
        self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
        status, headers, body = self.raw('GET', '/', {**self.NAVIGATE, 'Host': 'untrusted.invalid'})
        self.assertEqual(403, status)
        self.assertIn('This request host is not allowed.', body.decode())
        for path, extra in (('/missing-page', {}), ('/api/missing', self.NAVIGATE)):
            status, headers, body = self.raw('GET', path, extra)
            self.assertEqual(404 if path == '/missing-page' else 403, status)
            self.assertTrue(headers['Content-Type'].startswith('application/json'))
            self.assertIn('error', json.loads(body))

    def test_health_endpoint_is_minimal_unauthenticated_and_probe_friendly(self):
        status, headers, body = self.raw('GET', '/healthz')
        self.assertEqual((200, {'status': 'ok'}), (status, json.loads(body)))
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertNotIn('Set-Cookie', headers)
        # Docker/Railway probes use their own Host; behind Vercel no proxy key exists.
        secret = 'synthetic-server-only-proxy-secret-123456'
        self.server.config = replace(self.server.config, proxy_secret=secret)
        for extra in ({'Host': 'healthcheck.railway.app'}, {'Host': 'localhost'}, {}):
            self.assertEqual(200, self.raw('GET', '/healthz?probe=1', extra)[0])
        self.assertEqual(403, self.raw('GET', '/', {})[0])
        status, _, body = self.raw('HEAD', '/healthz')
        self.assertEqual((200, b''), (status, body))
        self.assertEqual(405, self.request('POST', '/healthz', client={})[0])
        with patch.object(self.auth, 'ping', side_effect=RuntimeError('database disk image is malformed')):
            status, _, body = self.raw('GET', '/healthz')
        self.assertEqual((503, {'status': 'unavailable'}), (status, json.loads(body)))
        self.assertNotIn(b'malformed', body)

    def test_static_assets_revalidate_with_etag_gzip_and_security_headers(self):
        status, headers, plain = self.raw('GET', '/static/console.js')
        self.assertEqual(200, status)
        self.assertEqual('no-cache', headers['Cache-Control'])
        self.assertEqual('Accept-Encoding', headers['Vary'])
        self.assertNotIn('Content-Encoding', headers)
        etag = headers['ETag']
        self.assertRegex(etag, r'^"[0-9a-f]{32}"$')
        for name, expected in (('Permissions-Policy', 'camera=()'), ('Cross-Origin-Opener-Policy', 'same-origin'),
                               ('Content-Security-Policy', "object-src 'none'"), ('X-Robots-Tag', 'noindex'),
                               ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer')):
            self.assertIn(expected, headers[name])
        self.assertNotIn('Strict-Transport-Security', headers)
        status, headers, body = self.raw('GET', '/static/console.js', {'If-None-Match': etag})
        self.assertEqual((304, b''), (status, body))
        self.assertEqual(etag, headers['ETag'])
        status, headers, body = self.raw('GET', '/static/console.js', {'Accept-Encoding': 'br, gzip;q=0.8'})
        self.assertEqual('gzip', headers['Content-Encoding'])
        self.assertEqual(plain, gzip.decompress(body))
        self.assertLess(len(body), len(plain))
        self.assertNotEqual(etag, headers['ETag'])
        self.assertEqual(304, self.raw('GET', '/static/console.js', {'Accept-Encoding': 'gzip',
                                                                      'If-None-Match': 'W/' + headers['ETag']})[0])
        self.assertNotIn('Content-Encoding', self.raw('GET', '/static/console.js', {'Accept-Encoding': 'gzip;q=0'})[1])
        status, headers, _ = self.raw('GET', '/', {'Accept-Encoding': 'gzip'})
        self.assertEqual(('no-store', 'gzip'), (headers['Cache-Control'], headers['Content-Encoding']))
        # API responses are never compressed or cached and HEAD is not an API verb.
        _, headers, _ = self.raw('GET', '/api/session', {'Accept-Encoding': 'gzip'})
        self.assertNotIn('Content-Encoding', headers)
        self.assertEqual('no-store', headers['Cache-Control'])
        status, headers, _ = self.raw('HEAD', '/api/session')
        self.assertEqual(405, status)
        self.assertNotIn('Set-Cookie', headers)

    def test_served_html_versions_assets_and_versioned_urls_are_immutable(self):
        import re
        from hermes_zalo.admin_web import CONSOLE_EXTENSIONS
        for page, expected in (('/', ['console.css', 'console.js']), ('/legacy.html', ['admin.css', 'admin.js'])):
            _, headers, body = self.raw('GET', page)
            html = body.decode()
            self.assertEqual('no-store', headers['Cache-Control'])
            self.assertNotRegex(html, r'(?:src|href)="/static/[^"?]+\.(?:js|css)"', 'every asset tag is versioned')
            urls = re.findall(r'(?:src|href)="(/static/[^"]+\?v=[0-9a-f]{16})"', html)
            for name in expected:
                self.assertTrue(any(url.startswith('/static/' + name + '?v=') for url in urls), name)
            for url in urls:
                with self.subTest(url=url):
                    status, asset_headers, content = self.raw('GET', url)
                    self.assertEqual(200, status)
                    self.assertEqual('public, max-age=31536000, immutable', asset_headers['Cache-Control'])
                    self.assertEqual(url.rsplit('=', 1)[1], hashlib.sha256(content).hexdigest()[:16])
                    gz = self.raw('GET', url, {'Accept-Encoding': 'gzip'})[1]
                    self.assertEqual('public, max-age=31536000, immutable', gz['Cache-Control'])
        # Every registered extension that ships a file is versioned in the console page.
        html = self.raw('GET', '/')[2].decode()
        for name in CONSOLE_EXTENSIONS:
            self.assertRegex(html, r'/static/console-' + name + r'\.js\?v=[0-9a-f]{16}')
        # A stale or forged version (e.g. a cached page from before an upgrade) must revalidate.
        for url in ('/static/console.js?v=0000000000000000', '/static/console.js?v=', '/static/console.js'):
            self.assertEqual('no-cache', self.raw('GET', url)[1]['Cache-Control'])
        self.assertEqual(400, self.raw('GET', '/static/console.js?v=a&v=b')[0])

    def test_https_deployment_sends_hsts_and_secure_cookie(self):
        self.server.config = replace(self.server.config, public_url=self.origin.replace('http://', 'https://'))
        _, headers, _ = self.raw('GET', '/healthz')
        self.assertEqual('max-age=31536000', headers['Strict-Transport-Security'])


class DefaultPasswordTests(unittest.TestCase):
    DEMO = 'LabCoach-Demo-2026'

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'admin.sqlite3'

    def tearDown(self):
        self.temp.cleanup()

    def test_live_store_rejects_setting_or_using_the_demo_password(self):
        live = AdminAuth(self.path)
        try:
            for password in (self.DEMO, ' labcoach-demo-2026 '):
                with self.assertRaises(AdminError):
                    live.create_account('owner', password, ['members'], role='faculty')
            live.create_account('owner', 'Synthetic-owner-password-1', ['members'], role='faculty')
            self.assertTrue(live.has_account('faculty'))
            self.assertEqual([], live.default_password_accounts())
        finally:
            live.close()
        # A store written by the synthetic demo (or copied from it) stays unusable live.
        demo = AdminAuth(self.path, allow_default_passwords=True)
        try:
            demo.create_account('copied', self.DEMO, ['members'])
            self.assertEqual([], demo.default_password_accounts())
            session, _ = demo.session('', create=True)
            demo.login(session, 'copied', self.DEMO)
        finally:
            demo.close()
        live = AdminAuth(self.path)
        try:
            self.assertEqual(['copied'], live.default_password_accounts())
            session, _ = live.session('', create=True)
            with self.assertRaises(AdminError) as raised:
                live.login(session, 'copied', self.DEMO)
            self.assertEqual(403, raised.exception.status)
            session, _ = live.session('', create=True)
            session, _ = live.login(session, 'owner', 'Synthetic-owner-password-1')
            with self.assertRaises(AdminError):
                live.change_password(session, dict(current_password='Synthetic-owner-password-1',
                                                   new_password=self.DEMO, confirm_password=self.DEMO))
        finally:
            live.close()

    def test_cli_refuses_live_start_with_demo_password_and_first_run_needs_a_terminal(self):
        from hermes_zalo import admin_cli
        with self.assertRaises(AdminError):
            admin_cli.first_run_owner(self.path, interactive=False)
        with self.assertRaises(AdminError):
            admin_cli.live_preflight(self.path)
        demo = AdminAuth(self.path, allow_default_passwords=True)
        demo.create_account('owner', self.DEMO, ['members'], role='faculty')
        demo.close()
        self.assertFalse(admin_cli.first_run_owner(self.path, interactive=False))
        with self.assertRaises(AdminError) as raised:
            admin_cli.live_preflight(self.path)
        self.assertIn('owner', str(raised.exception))
        self.assertNotIn(self.DEMO, str(raised.exception))
        # Never load the developer's real .env into this process (it leaks into later tests).
        with patch('getpass.getpass', return_value='Synthetic-owner-password-2'), \
                patch.object(admin_cli, 'load_env'), patch.dict(os.environ, clear=False):
            self.assertEqual(0, admin_cli.main(['create-console-account', '--auth-db', str(self.path),
                                                '--username', 'owner', '--replace']))
        admin_cli.live_preflight(self.path)

    def test_first_run_creates_full_owner_once(self):
        from hermes_zalo import admin_cli
        with patch('getpass.getpass', return_value='Synthetic-owner-password-3'), \
                patch('builtins.input', return_value=''):
            self.assertTrue(admin_cli.first_run_owner(self.path, interactive=True))
        self.assertFalse(admin_cli.first_run_owner(self.path, interactive=False))
        auth = AdminAuth(self.path)
        try:
            session, _ = auth.session('', create=True)
            session, _ = auth.login(session, 'owner', 'Synthetic-owner-password-3')
            user = auth.user(session)
            self.assertEqual('faculty', user['role'])
            self.assertIn('conversations', user['permissions'])
        finally:
            auth.close()
        admin_cli.live_preflight(self.path)

    def test_healthcheck_skips_when_console_is_off_and_probes_when_on(self):
        from hermes_zalo import admin_cli
        self.assertEqual(0, admin_cli.healthcheck({}))
        self.assertEqual(0, admin_cli.healthcheck({'ZALO_ADMIN_ENABLED': 'true', 'ZALO_CONVERSATIONS_ENABLED': 'true',
                                                   'BOT_START_ENABLED': 'false'}))
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            free = sock.getsockname()[1]
        self.assertEqual(1, admin_cli.healthcheck({'ZALO_ADMIN_ENABLED': 'true', 'ZALO_CONVERSATIONS_ENABLED': 'true',
                                                   'ZALO_ADMIN_PORT': str(free)}, timeout=1))



class GatewayRouteSyncTests(unittest.TestCase):
    def test_vercel_gateway_allowlist_matches_registered_console_sections(self):
        # The Vercel gateway forwards only allowlisted paths/filters; a new
        # section must be added there or it silently 404s in production.
        import re
        from hermes_zalo import console_sections
        source = (Path(__file__).resolve().parents[1] / 'deploy/vercel-admin/lib/gateway.js').read_text()
        routes = {path: (method, re.findall(r"'([^']+)'", query)) for path, method, query in re.findall(
            r"'(/api/assistant/[a-z0-9-]+(?:/action)?)': \{ method: '(GET|POST)'[^}]*?query: \[([^\]]*)\]", source)}
        for name in console_sections.names():
            section = console_sections.get(name)
            with self.subTest(section=name):
                self.assertIn('/api/assistant/' + name, routes)
                method, query = routes['/api/assistant/' + name]
                self.assertEqual(('GET', sorted(section.query_keys)), (method, sorted(query)))
                if section.action is not None:
                    self.assertEqual(('POST', []), routes.get('/api/assistant/' + name + '/action'))
                else:
                    self.assertNotIn('/api/assistant/' + name + '/action', routes)


if __name__ == '__main__':
    unittest.main()
