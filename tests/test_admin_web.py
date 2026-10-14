"""Real HTTP boundary tests over loopback, with disposable synthetic stores."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
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
        self.assertEqual('Mật khẩu hiện tại chưa đúng.', result['error'])
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
        expected = {'error': 'Yêu cầu không được phép.'}
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
            self.assertEqual({'error': 'Yêu cầu không được phép.'}, json.loads(response.read()))
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


if __name__ == '__main__':
    unittest.main()
