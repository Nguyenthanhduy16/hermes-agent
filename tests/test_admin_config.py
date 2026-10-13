from pathlib import Path
import tempfile
import unittest

from hermes_zalo.admin_config import AdminConfig
from hermes_zalo.admin_cli import provisioning_settings


class AdminConfigTests(unittest.TestCase):
    def test_disabled_default_and_explicit_external_origin(self):
        self.assertFalse(AdminConfig.from_mapping({}).enabled)
        with self.assertRaises(ValueError):
            AdminConfig.from_mapping({'ZALO_ADMIN_ENABLED': 'true'})
        enabled = {'ZALO_ADMIN_ENABLED': 'true', 'ZALO_CONVERSATIONS_ENABLED': 'true'}
        with self.assertRaises(ValueError):
            AdminConfig.from_mapping(enabled | {'ZALO_ADMIN_HOST': '0.0.0.0'})
        config = AdminConfig.from_mapping(enabled | {'ZALO_ADMIN_HOST': '0.0.0.0',
            'ZALO_ADMIN_PUBLIC_URL': 'https://admin.example.invalid', 'PORT': '8000'})
        self.assertEqual(8000, config.port)
        self.assertEqual(30, config.retention_days)
        for origin in ('http://example.invalid', 'https://user:pass@example.invalid',
                       'https://example.invalid/a', 'https://example.invalid/?key=x'):
            with self.assertRaises(ValueError):
                AdminConfig.from_mapping(enabled | {'ZALO_ADMIN_PUBLIC_URL': origin})

    def test_invalid_values_fail_closed(self):
        for values in ({'ZALO_ADMIN_ENABLED': 'yes'}, {'ZALO_CONVERSATION_RETENTION_DAYS': '0'},
                       {'ZALO_CONVERSATION_RETENTION_DAYS': '366'}, {'ZALO_ADMIN_PORT': '70000'},
                       {'ZALO_ADMIN_HOST': '::'}, {'ZALO_ADMIN_PORT': 'abc'}):
            with self.assertRaises(ValueError):
                AdminConfig.from_mapping(values)

    def test_proxy_secret_optional_bounded_and_not_in_repr_or_errors(self):
        self.assertEqual('', AdminConfig.from_mapping({}).proxy_secret)
        self.assertEqual('', AdminConfig.from_mapping({'ZALO_ADMIN_PROXY_SECRET': ''}).proxy_secret)
        for value in ('a' * 32, 'b' * 256):
            config = AdminConfig.from_mapping({'ZALO_ADMIN_PROXY_SECRET': value})
            self.assertEqual(value, config.proxy_secret)
            self.assertNotIn(value, repr(config))
        for value in ('short', 'a' * 31, 'a' * 257, 'a' * 32 + ' ', 'a' * 32 + '\n',
                      'a' * 32 + '\x7f', 'a' * 32 + 'đ'):
            with self.subTest(length=len(value)):
                with self.assertRaises(ValueError) as caught:
                    AdminConfig.from_mapping({'ZALO_ADMIN_PROXY_SECRET': value})
                self.assertNotIn(value, str(caught.exception))

    def test_provisioning_uses_same_state_path_as_windows_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'scripts').mkdir()
            (root / 'scripts/launch.py').touch()
            values = {'ZALO_BOT_TOKEN': 'synthetic-token', 'ZALO_ALLOWED_USERS': 'demo-admin',
                      'ZALO_STATE_DIR': str(root / 'different')}
            windows = provisioning_settings(values, root, windows=True)
            linux = provisioning_settings(values, root, windows=False)
            self.assertEqual(root / '.runtime/hermes-home/data/zalo', windows.state_dir)
            self.assertEqual(root / 'different', linux.state_dir)
