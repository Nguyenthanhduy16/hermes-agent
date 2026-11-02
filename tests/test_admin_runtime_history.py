import json
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from hermes_zalo.admin_auth import AdminAuth, AdminError
from hermes_zalo.admin_cli import console_paths, main
from hermes_zalo.admin_runtime_history import RuntimeHistory, _text


class RuntimeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def seed(self):
        db = sqlite3.connect(self.home / 'state.db')
        db.executescript('''
            CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,started_at REAL,title TEXT,
                hidden INTEGER DEFAULT 0,message_count INTEGER);
            CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,
                timestamp REAL,_compressed_summary INTEGER DEFAULT 0);
            INSERT INTO sessions VALUES ('tele-1','telegram',100,'Tổng hợp công việc',0,6);
            INSERT INTO sessions VALUES ('hidden-1','internal',101,'Hidden private session',1,1);
        ''')
        for index, (role, content, summary) in enumerate([
            ('system', 'system prompt private', 0),
            ('tool', 'tool result secret', 0),
            ('assistant', 'private summary', 1),
            ('user', 'api_key=synthetic-secret-value', 0),
            ('assistant', json.dumps([{'type': 'text', 'text': 'Kết quả phân tích'},
                                     {'type': 'image_url', 'image_url': 'private-image-payload'}]), 0),
            ('user', '[{"type":"image_url",' + 'x' * 18000, 0),
        ], 1):
            db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', ('%d' % index, 'tele-1', role, content, 100 + index, summary))
        db.commit()
        db.close()

    def test_missing_home_is_reported_without_creating_data(self):
        home = self.home / 'not-created'
        result = RuntimeHistory(home).read()
        self.assertFalse(result['available'])
        self.assertIsNone(result['total'])
        self.assertFalse(home.exists())

    def test_sessions_hide_internal_entries_and_never_return_prompts(self):
        self.seed()
        result = RuntimeHistory(self.home).read()
        self.assertEqual(1, result['total'])
        self.assertEqual('tele-1', result['items'][0]['runtime_session_id'])
        self.assertEqual('telegram', result['items'][0]['source'])
        self.assertNotIn('private', json.dumps(result))

    def test_messages_are_bound_to_session_and_only_show_sanitized_chat_text(self):
        self.seed()
        before = {p.name: p.read_bytes() for p in self.home.iterdir()}
        result = RuntimeHistory(self.home).read(session_id='tele-1')
        text = json.dumps(result, ensure_ascii=False)
        self.assertEqual(3, result['total'])
        self.assertIn('Kết quả phân tích', text)
        for secret in ('system prompt private', 'tool result secret', 'private summary',
                       'synthetic-secret-value', 'private-image-payload'):
            self.assertNotIn(secret, text)
        self.assertIn('Structured content hidden', text)
        self.assertIn('api_key=[hidden]', text)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.home.iterdir()})
        for sid in ('hidden-1', 'nonexistent', "tele-1' OR 1=1 --"):
            with self.assertRaises(AdminError) as context:
                RuntimeHistory(self.home).read(session_id=sid)
            self.assertEqual(404, context.exception.status)

    def test_existing_user_text_and_historical_mask_labels_are_not_translated(self):
        original = 'Tôi đã viết nhãn [đã ẩn] trong ghi chú.'
        self.assertEqual(original, _text(original))

    def test_unsupported_schema_and_invalid_page_fail_closed(self):
        db = sqlite3.connect(self.home / 'state.db')
        db.execute('CREATE TABLE sessions(id TEXT)')
        db.close()
        self.assertFalse(RuntimeHistory(self.home).read()['available'])
        for page in (0, -1, True, '2'):
            with self.assertRaises(AdminError):
                RuntimeHistory(self.home).read(page=page)


class StandaloneConsoleCliTests(unittest.TestCase):
    def test_standalone_cli_serves_authenticated_runtime_without_bot_tokens(self):
        with TemporaryDirectory() as folder:
            home = Path(folder)
            auth = AdminAuth(home / 'console-admin.sqlite3')
            auth.create_account('owner', 'Synthetic-console-password-2026', ['conversations'], role='faculty')
            auth.close()
            db = sqlite3.connect(home / 'state.db')
            db.executescript('''
                CREATE TABLE sessions(id TEXT PRIMARY KEY,source TEXT,started_at REAL);
                CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,timestamp REAL);
                INSERT INTO sessions VALUES ('runtime-test','telegram',100);
                INSERT INTO messages VALUES (1,'runtime-test','assistant','Synthetic reply',101);
            ''')
            db.commit()
            db.close()
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            source = Path(__file__).resolve().parents[1] / 'src'
            process = subprocess.Popen([sys.executable, '-m', 'hermes_zalo.admin_cli', 'serve',
                '--home', folder, '--port', str(port)], cwd=folder,
                env={'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(source), 'PYTHONUNBUFFERED': '1'},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            cookie, csrf = '', ''
            def request(path, payload=None):
                nonlocal cookie, csrf
                headers = {'Cookie': cookie} if cookie else {}
                if payload is not None:
                    headers.update({'Origin': f'http://127.0.0.1:{port}', 'Content-Type': 'application/json',
                                    'X-CSRF-Token': csrf})
                conn = HTTPConnection('127.0.0.1', port, timeout=2)
                try:
                    conn.request('GET' if payload is None else 'POST', path,
                                 body=json.dumps(payload) if payload is not None else None, headers=headers)
                    result = conn.getresponse()
                    body = result.read()
                    if result.getheader('Set-Cookie'):
                        parsed = SimpleCookie(result.getheader('Set-Cookie'))
                        cookie = 'labcoach_admin=' + parsed['labcoach_admin'].value
                    data = json.loads(body) if result.getheader('Content-Type', '').startswith('application/json') else body.decode()
                    if isinstance(data, dict) and data.get('csrf'):
                        csrf = data['csrf']
                    return result.status, data
                finally:
                    conn.close()
            try:
                for attempt in range(60):
                    try:
                        status, session = request('/api/session')
                        break
                    except (ConnectionError, OSError):
                        if process.poll() is not None:
                            self.fail('Standalone console exited before accepting requests.')
                        time.sleep(.05)
                else:
                    self.fail('Standalone console did not become ready.')
                self.assertEqual(200, status)
                self.assertTrue(session['runtime_history'])
                self.assertEqual(401, request('/api/assistant/overview')[0])
                self.assertEqual(200, request('/api/login', {'username': 'owner',
                    'password': 'Synthetic-console-password-2026'})[0])
                status, overview = request('/api/assistant/overview')
                self.assertEqual(200, status)
                self.assertEqual(1, overview['counts']['sessions'])
                self.assertEqual('telegram', request('/api/assistant/sessions')[1]['items'][0]['source'])
                self.assertEqual('Synthetic reply', request('/api/assistant/history?session_id=runtime-test')[1]['items'][0]['content'])
                with sqlite3.connect(home / 'console-admin.sqlite3') as audit:
                    row = audit.execute("SELECT details FROM admin_auth_audit WHERE action='runtime_history_viewed'").fetchone()
                    self.assertIsNotNone(row)
                    self.assertIn('runtime-test', row[0])
                    self.assertNotIn('Synthetic reply', row[0])
                self.assertEqual(405, request('/api/assistant/runtime', {})[0])
                for asset in ('/', '/legacy.html', '/static/console.js', '/static/console.css'):
                    self.assertEqual(200, request(asset)[0], asset)
            finally:
                process.terminate()
                process.communicate(timeout=5)

    def test_paths_do_not_fall_back_to_personal_hermes(self):
        with patch.dict('os.environ', {}, clear=True):
            home, auth = console_paths()
        self.assertEqual('.runtime/hermes-home', home.as_posix().split('hermes-agent/')[-1])
        self.assertEqual(home / 'console-admin.sqlite3', auth)

    def test_console_account_needs_no_zalo_token_and_serve_needs_provisioning(self):
        with TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True), \
                patch('hermes_zalo.admin_cli.load_env'), \
                patch('hermes_zalo.admin_cli.getpass.getpass', return_value='Synthetic-console-password-2026'):
            self.assertEqual(2, main(['serve', '--home', folder]))
            self.assertEqual([], list(Path(folder).iterdir()))
            self.assertEqual(0, main(['create-console-account', '--home', folder,
                                     '--username', 'owner', '--permissions', 'conversations']))
            if os.name == 'posix':
                self.assertEqual(0o600, (Path(folder) / 'console-admin.sqlite3').stat().st_mode & 0o777)
            auth = AdminAuth(Path(folder) / 'console-admin.sqlite3')
            try:
                _, token = auth.session('', create=True)
                session, _ = auth.session(token)
                logged_in, _ = auth.login(session, 'owner', 'Synthetic-console-password-2026')
                self.assertEqual('faculty', auth.user(logged_in)['role'])
                self.assertEqual(['conversations'], auth.user(logged_in)['permissions'])
            finally:
                auth.close()
