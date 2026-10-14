"""Encrypted backup download over synthetic loopback HTTP, without live credentials."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.client import HTTPConnection
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

from hermes_zalo.admin_config import AdminConfig
from hermes_zalo.admin_web import AdminServer
from hermes_zalo.backup import MAGIC, verify_backup
from hermes_zalo.backup_service import BackupHTTPError, BackupService


@unittest.skipUnless(Fernet, 'optional backup dependency is not installed')
class BackupServiceTests(unittest.TestCase):
    TOKEN = 'synthetic-backup-access-token-1234567890'

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = Fernet.generate_key()
        self.now = 1_000.0
        self.values = {'LABCOACH_BACKUP_ENABLED': 'true', 'LABCOACH_BACKUP_ACCESS_TOKEN': self.TOKEN,
                       'LABCOACH_BACKUP_KEY': self.key.decode(), 'BOT_DATA_DIR': str(self.root)}
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def factory(self, root, output, key):
        self.calls.append((root, output, key))
        output.write_bytes(MAGIC + b'synthetic-encrypted-payload')

    def service(self, **changes):
        return BackupService.from_mapping(self.values | changes, factory=self.factory, clock=lambda: self.now)

    def assert_error(self, status, action):
        with self.assertRaises(BackupHTTPError) as caught:
            action()
        self.assertEqual(status, caught.exception.status)
        return caught.exception

    def test_disabled_default_and_invalid_config_fail_closed_without_copy(self):
        self.assert_error(404, lambda: BackupService.from_mapping({}).download([self.TOKEN]))
        for changes in ({'LABCOACH_BACKUP_ENABLED': 'yes'}, {'LABCOACH_BACKUP_ACCESS_TOKEN': 'bad'},
                        {'LABCOACH_BACKUP_KEY': 'invalid-key'}, {'BOT_DATA_DIR': 'relative'},
                        {'LABCOACH_BACKUP_KEY_FILE': 'relative.key'}, {'BOT_DATA_DIR': str(self.root / 'absent')}):
            service = self.service(**changes)
            self.assertFalse(service.config_valid)
            error = self.assert_error(503, lambda: service.download([self.TOKEN]))
            self.assertNotIn(self.TOKEN, str(error))
            self.assertNotIn(str(self.root), str(error))
        self.assertEqual([], self.calls)

    def test_missing_wrong_duplicate_and_malformed_keys_never_read_source(self):
        service = self.service()
        for supplied in ([], [self.TOKEN, self.TOKEN], ['x' * 40], ['short'], [None], ['é' * 40], [self.TOKEN + '\n']):
            self.assert_error(403, lambda: service.download(supplied))
        self.assertEqual([], self.calls)

    def test_key_file_overrides_env_and_ciphertext_is_bounded_hashed_and_cleaned(self):
        path = self.root / 'private-backup.key'
        path.write_bytes(self.key)
        service = self.service(LABCOACH_BACKUP_KEY='ignored-invalid-key', LABCOACH_BACKUP_KEY_FILE=str(path))
        result = service.download([self.TOKEN])
        self.assertEqual(self.key, self.calls[0][2])
        self.assertEqual(hashlib.sha256(result.body).hexdigest(), result.sha256)
        self.assertNotIn(result.body.decode(), repr(result))
        self.assertFalse(self.calls[0][1].parent.exists(), 'temporary encrypted output must be removed')

    def test_one_success_per_ten_minutes_and_failed_requests_are_bounded(self):
        service = self.service()
        service.download([self.TOKEN])
        error = self.assert_error(429, lambda: service.download([self.TOKEN]))
        self.assertGreaterEqual(error.retry_after, 600)
        self.assertEqual(1, len(self.calls))
        self.now += 600
        service.download([self.TOKEN])
        self.assertEqual(2, len(self.calls))

        def broken(root, output, key):
            raise OSError('private-path and secret should never be shown')

        failed = BackupService.from_mapping(self.values, factory=broken, clock=lambda: self.now)
        error = self.assert_error(503, lambda: failed.download([self.TOKEN]))
        self.assertNotIn('private-path', str(error))
        self.assert_error(429, lambda: failed.download([self.TOKEN]))
        self.now += 60
        self.assert_error(503, lambda: failed.download([self.TOKEN]))

    def test_concurrent_authenticated_download_does_not_start_second_snapshot(self):
        entered, release = threading.Event(), threading.Event()

        def blocked(root, output, key):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            self.factory(root, output, key)

        service = BackupService.from_mapping(self.values, factory=blocked)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(service.download, [self.TOKEN])
            try:
                self.assertTrue(entered.wait(3))
                self.assert_error(429, lambda: service.download([self.TOKEN]))
            finally:
                release.set()
            self.assertTrue(future.result(5).body.startswith(MAGIC))
        self.assertEqual(1, len(self.calls))

    def test_real_core_archive_can_be_verified_and_contains_no_plaintext_response(self):
        folder = self.root / 'hermes-home/data/zalo'
        folder.mkdir(parents=True)
        database = folder / 'work-0123456789abcdef.admin.sqlite3'
        with sqlite3.connect(database) as db:
            db.execute('CREATE TABLE sample(value TEXT)')
            db.execute('INSERT INTO sample VALUES (?)', ('Synthetic private member content',))
        db.close()
        service = BackupService.from_mapping(self.values)
        result = service.download([self.TOKEN])
        self.assertNotIn(b'Synthetic private member content', result.body)
        self.assertNotIn(b'SQLite format', result.body)
        self.assertNotIn(self.key, result.body)
        encrypted = self.root / 'download.lcbak'
        encrypted.write_bytes(result.body)
        self.assertEqual(1, verify_backup(encrypted, self.key)['files'])


@unittest.skipUnless(Fernet, 'optional backup dependency is not installed')
class BackupHttpTests(unittest.TestCase):
    TOKEN = BackupServiceTests.TOKEN
    PROXY = 'synthetic-admin-proxy-secret-1234567890'

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.calls = 0

        def factory(root, output, key):
            self.calls += 1
            output.write_bytes(MAGIC + b'synthetic-encrypted-payload')

        self.service = BackupService.from_mapping({
            'LABCOACH_BACKUP_ENABLED': 'true', 'BOT_DATA_DIR': self.temp.name,
            'LABCOACH_BACKUP_ACCESS_TOKEN': self.TOKEN, 'LABCOACH_BACKUP_KEY': Fernet.generate_key().decode(),
        }, factory=factory)
        self.server = AdminServer(SimpleNamespace(), None, AdminConfig(port=0,
            public_url='https://backend.example.test', proxy_secret=self.PROXY), backup_service=self.service)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01))
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())
        self.temp.cleanup()

    def request(self, path='/api/backup', method='GET', headers=None):
        client = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            client.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            pairs = [('Host', 'backend.example.test'), *(headers or [])]
            for name, value in pairs:
                client.putheader(name, value)
            if method == 'POST':
                client.putheader('Content-Length', '0')
            client.endheaders()
            result = client.getresponse()
            return result.status, dict(result.getheaders()), result.read()
        finally:
            client.close()

    def test_dedicated_key_downloads_without_proxy_secret_admin_cookie_or_csrf(self):
        status, headers, body = self.request(headers=[('X-LabCoach-Backup-Key', self.TOKEN)])
        self.assertEqual(200, status)
        self.assertEqual('application/octet-stream', headers['Content-Type'])
        self.assertEqual(hashlib.sha256(body).hexdigest(), headers['X-LabCoach-Backup-SHA256'])
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertNotIn('Set-Cookie', headers)
        self.assertEqual(1, self.calls)
        status, headers, _ = self.request(headers=[('X-LabCoach-Backup-Key', self.TOKEN)])
        self.assertEqual(429, status)
        self.assertIn('Retry-After', headers)

    def test_backup_key_cannot_open_admin_routes_and_proxy_key_cannot_download(self):
        for path in ('/', '/api/session', '/api/members', '/api/admin/accounts'):
            self.assertEqual(403, self.request(path, headers=[('X-LabCoach-Backup-Key', self.TOKEN)])[0])
        self.assertEqual(403, self.request(headers=[('X-LabCoach-Admin-Proxy', self.PROXY)])[0])
        self.assertEqual(0, self.calls)

    def test_machine_endpoint_rejects_browser_requests_query_duplicates_and_wrong_method(self):
        valid = [('X-LabCoach-Backup-Key', self.TOKEN)]
        for path, method, headers, expected in (
            ('/api/backup?x=1', 'GET', valid, 400),
            ('/api/backup?x=1&x=2', 'GET', valid, 400),
            ('/api/backup?', 'GET', valid, 400),
            ('/api/backup', 'POST', valid, 405),
            ('/api/backup', 'GET', valid * 2, 403),
            ('/api/backup', 'GET', valid + [('Host', 'backend.example.test')], 403),
            ('/api/backup', 'GET', valid + [('Origin', 'https://backend.example.test')], 403),
            ('/api/backup', 'GET', valid + [('Origin', 'null')], 403),
            ('/api/backup', 'GET', valid + [('Sec-Fetch-Mode', 'navigate')], 403),
            ('/api/backup', 'GET', valid + [('Cookie', 'labcoach_admin=synthetic')], 403),
        ):
            with self.subTest(path=path, method=method, headers=[name for name, _ in headers]):
                self.assertEqual(expected, self.request(path, method, headers)[0])
        self.assertEqual(0, self.calls)

    def test_invalid_configuration_affects_only_backup_and_never_exposes_secrets(self):
        self.server.backup_service = BackupService.from_mapping({'LABCOACH_BACKUP_ENABLED': 'true'})
        status, _, raw = self.request(headers=[('X-LabCoach-Backup-Key', self.TOKEN)])
        self.assertEqual(503, status)
        self.assertNotIn(self.TOKEN, raw.decode())
        self.assertEqual(0, self.calls)
        self.assertEqual(200, self.request('/', headers=[('X-LabCoach-Admin-Proxy', self.PROXY)])[0])


if __name__ == '__main__':
    unittest.main()
