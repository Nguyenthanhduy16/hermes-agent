"""Real local HTTP boundary tests; all schedules/auth are synthetic."""
from pathlib import Path
import http.client
import tempfile
import threading
import unittest

import httpx

from hermes_zalo.tickets.cli import process_lock
from hermes_zalo.tickets.service import DemoAssignments, TicketError, TicketService, TicketStore
from hermes_zalo.tickets.web import TicketsServer


class TicketsWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TicketStore(Path(self.tmp.name) / 'tickets.sqlite3')
        self.provider = DemoAssignments(self.store)
        self.service = TicketService(self.store, self.provider)
        self.server = TicketsServer(0, self.service)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.client = httpx.Client(base_url=self.url, timeout=3)
        self.csrf = self.client.get('/api/session').json()['csrf']

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.store.close()
        self.tmp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers={'Origin': self.url, 'X-CSRF-Token': self.csrf})

    def login(self, account):
        response = self.post('/api/login', {'account_id': account})
        self.assertEqual(200, response.status_code, response.text)
        self.csrf = response.json()['csrf']
        return response

    def create(self):
        self.login('demo-a')
        preview = self.post('/api/preview', {'assignment_a': 'CA-11', 'assignment_b': 'CA-21'})
        self.assertEqual(200, preview.status_code, preview.text)
        response = self.post('/api/tickets', {'preview_token': preview.json()['preview_token'],
                                             'reason': 'Lý do thử', 'idempotency_key': 'unique-submit-123456'})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_http_full_two_people_manager_flow_and_cookie_rotation(self):
        before = self.client.cookies.get('labcoach_tickets')
        ticket = self.create()
        self.assertNotEqual(before, self.client.cookies.get('labcoach_tickets'))
        self.assertEqual('pending_peer', ticket['status'])
        self.login('demo-b')
        accepted = self.post(f"/api/tickets/{ticket['id']}/accept", {'version': ticket['version']}).json()
        self.assertEqual('pending_manager', accepted['status'])
        self.login('demo-manager')
        done = self.post(f"/api/tickets/{ticket['id']}/approve", {'version': accepted['version']}).json()
        self.assertEqual('completed', done['status'])
        roster = self.client.get('/api/bootstrap').json()['assignments']
        self.assertEqual('coach-b', next(a for a in roster if a['id'] == 'CA-11')['member_id'])
        self.assertEqual('coach-a', next(a for a in roster if a['id'] == 'CA-21')['member_id'])
        duplicate = self.post(f"/api/tickets/{ticket['id']}/approve", {'version': accepted['version']})
        self.assertEqual(409, duplicate.status_code)

    def test_unauthenticated_forbidden_ids_and_arbitrary_user_claims(self):
        self.assertEqual(401, self.client.get('/api/bootstrap').status_code)
        ticket = self.create()
        self.login('demo-c')
        self.assertEqual([], self.client.get('/api/bootstrap').json()['tickets'])
        hidden = self.client.get('/api/tickets/' + ticket['id'])
        self.assertEqual(404, hidden.status_code)
        self.assertNotIn('Lý do thử', hidden.text)
        attempt = self.post(f"/api/tickets/{ticket['id']}/accept", {'version': ticket['version'], 'user_id': 'demo-b'})
        self.assertEqual(404, attempt.status_code)
        self.assertEqual(403, self.post('/api/preview', {'assignment_a': 'CA-11', 'assignment_b': 'CA-21'}).status_code)

    def test_origin_csrf_host_and_session_revocation(self):
        self.assertEqual(403, self.client.post('/api/login', json={'account_id': 'demo-a'}, headers={'Origin': self.url}).status_code)
        self.assertEqual(403, self.client.post('/api/login', json={'account_id': 'demo-a'}, headers={
            'Origin': 'https://outside.invalid', 'X-CSRF-Token': self.csrf}).status_code)
        self.assertEqual(403, self.client.get('/api/session', headers={'Host': 'outside.invalid'}).status_code)
        self.assertEqual(403, self.client.post('/api/login', json={'account_id': 'demo-a'}, headers=[
            (b'Origin', self.url.encode('ascii')), (b'X-CSRF-Token', b'\xe9')]).status_code)
        self.login('demo-a')
        old_csrf = self.csrf
        cookie = self.client.cookies.get('labcoach_tickets')
        out = self.post('/api/logout', {})
        self.assertIsNone(out.json()['user'])
        self.assertIn('HttpOnly', out.headers['set-cookie'])
        self.assertIn('SameSite=Strict', out.headers['set-cookie'])
        self.assertEqual(401, self.client.get('/api/bootstrap', headers={'Cookie': 'labcoach_tickets=' + cookie}).status_code)
        self.assertEqual(403, self.client.post('/api/login', json={'account_id': 'demo-b'}, headers={
            'Origin': self.url, 'X-CSRF-Token': old_csrf}).status_code)

    def test_malformed_input_and_paths_do_not_leak_files_or_secrets(self):
        for path in ('/.env', '/secrets/private.json', '/../service.py', '/api/tickets/unknown'):
            response = self.client.get(path)
            self.assertIn(response.status_code, (401, 404))
            self.assertNotIn('sqlite3', response.text)
        self.assertEqual(400, self.post('/api/login', []).status_code)
        # The server rejects the declared size before reading the body. Sending
        # that body concurrently can cause Windows to reset the closing socket
        # before HTTPX receives the 413 response; inspect the header rejection.
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.putrequest('POST', '/api/login')
            connection.putheader('Origin', self.url)
            connection.putheader('Content-Type', 'application/json')
            connection.putheader('Content-Length', '9000')
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(413, response.status)
            response.read()
        finally:
            connection.close()
        self.assertEqual(415, self.client.post('/api/login', content='account_id=demo-a', headers={'Origin': self.url}).status_code)
        self.login('demo-a')
        self.assertEqual(400, self.post('/api/preview', {'assignment_a': [], 'assignment_b': {}}).status_code)

    def test_real_test_mode_uses_passwords_and_never_demo_impersonation(self):
        self.service.mode = 'sheets_test'
        self.store.add_account('coach-real', 'Người thử', 'verified:person', 'member', 'a-good-test-password')
        session = self.client.get('/api/session').json()
        self.assertEqual([], session['demo_accounts'])
        self.assertEqual(401, self.post('/api/login', {'account_id': 'demo-manager'}).status_code)
        valid = self.post('/api/login', {'username': 'coach-real', 'password': 'a-good-test-password'})
        self.assertEqual(200, valid.status_code)
        self.csrf = valid.json()['csrf']
        self.assertEqual('verified:person', valid.json()['user']['member_id'])
        with self.store.transaction() as db:
            db.execute('UPDATE ticket_accounts SET active=0 WHERE id=?', ('coach-real',))
        self.assertEqual(401, self.client.get('/api/bootstrap').status_code)

    def test_password_bruteforce_has_bounded_attempts(self):
        self.service.mode = 'sheets_test'
        for _ in range(5):
            self.assertEqual(401, self.post('/api/login', {'username': 'unknown', 'password': 'bad'}).status_code)
        self.assertEqual(429, self.post('/api/login', {'username': 'unknown', 'password': 'bad'}).status_code)

    def test_only_one_process_can_hold_store_lock(self):
        path = Path(self.tmp.name) / 'server.lock'
        with process_lock(path):
            with self.assertRaises(TicketError):
                with process_lock(path):
                    self.fail('second server acquired lock')
        with process_lock(path):
            pass


if __name__ == '__main__':
    unittest.main()
