from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest

from hermes_zalo.admin_auth import AdminError
from hermes_zalo.admin_control_store import ControlStore


class ControlStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.store = ControlStore(self.home)

    def test_reads_do_not_create_files_and_atomic_config_preserves_other_fields(self):
        config, revision = self.store.read_yaml()
        self.assertEqual({}, config)
        self.assertFalse(self.home.exists())
        changed = self.store.mutate_yaml('config.yaml', revision,
            lambda old: {**old, 'model': {'default': 'test-model'}, 'other': {'keep': True}})
        self.assertNotEqual(revision, changed)
        config, revision = self.store.read_yaml()
        self.store.mutate_yaml('config.yaml', revision,
            lambda old: {**old, 'model': {'default': 'new-model'}})
        self.assertEqual({'keep': True}, self.store.read_yaml()[0]['other'])
        self.assertEqual(0o600, stat.S_IMODE((self.home / 'config.yaml').stat().st_mode))
        self.assertEqual(['.console-control.lock'], [p.name for p in self.home.glob('.console-*')])

    def test_stale_or_invalid_revision_cannot_write_or_create_home(self):
        for revision, expected_status in (('0' * 64, 409), ('bad', 400), (None, 400)):
            with self.assertRaises(AdminError) as error:
                self.store.write_text('config.yaml', 'model: test\n', revision)
            self.assertEqual(expected_status, error.exception.status)
            self.assertFalse(self.home.exists())

    def test_empty_existing_file_has_different_revision_to_missing(self):
        _, missing = self.store.read_text('notes.txt')
        empty = self.store.write_text('notes.txt', '', missing)
        self.assertNotEqual(empty, missing)

    def test_two_store_instances_only_apply_one_concurrent_revision(self):
        _, revision = self.store.read_yaml()
        other = ControlStore(self.home)
        def save(args):
            service, name = args
            try:
                service.mutate_yaml('config.yaml', revision, lambda _: {'model': name})
                return 200
            except AdminError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual([200, 409], sorted(pool.map(save, ((self.store, 'a'), (other, 'b')))))

    def test_permission_rechecked_before_mutation_and_immediately_before_write(self):
        _, revision = self.store.read_yaml()
        def denied():
            raise AdminError('Permission revoked.', 403)
        with self.assertRaises(AdminError):
            self.store.mutate_yaml('config.yaml', revision, lambda _: {}, authorize=denied)
        self.assertFalse(self.home.exists())
        calls = []
        def revoke_before_write():
            calls.append(True)
            if len(calls) == 3:
                denied()
        with self.assertRaises(AdminError):
            self.store.mutate_yaml('config.yaml', revision, lambda _: {'private': True}, authorize=revoke_before_write)
        self.assertFalse((self.home / 'config.yaml').exists())

    def test_invalid_yaml_recursive_alias_and_special_files_are_not_overwritten(self):
        self.home.mkdir()
        config = self.home / 'config.yaml'
        for content in ('model: [broken\n', 'recursive: &cycle [*cycle]\n', '- not-a-map\n'):
            config.write_text(content)
            with self.assertRaises(AdminError):
                self.store.read_yaml()
            self.assertEqual(content, config.read_text())
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.home / 'fifo')
            with self.assertRaises(AdminError):
                self.store.read_text('fifo')

    def test_rejects_path_escape_symlinks_and_symlinked_home(self):
        for path in ('../secret', '/etc/passwd', 'a/../config.yaml', 'a\\b', 'a\0b'):
            with self.assertRaises(AdminError):
                self.store.read_text(path)
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        self.home.mkdir()
        (self.home / 'config.yaml').symlink_to(outside / 'config.yaml')
        with self.assertRaises(AdminError):
            self.store.read_yaml()
        (self.home / 'nested').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(AdminError):
            self.store.read_text('nested/missing.txt')
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(AdminError):
            ControlStore(alias).read_yaml()

    def test_env_updates_preserve_comments_unrelated_secrets_and_remove_duplicates(self):
        self.home.mkdir()
        env_file = self.home / '.env'
        env_file.write_text('# Keep this comment\nOTHER_KEY=keep-private\nOPENAI_API_KEY=old\nOPENAI_API_KEY=duplicate\n')
        _, revision = self.store.read_env()
        value = "key with 'quotes' and ${NO_INTERPOLATION} and \\slashes"
        self.store.mutate_env(revision, {'OPENAI_API_KEY': value})
        values, revision = self.store.read_env()
        self.assertEqual(value, values['OPENAI_API_KEY'])
        self.assertEqual('keep-private', values['OTHER_KEY'])
        self.assertIn('# Keep this comment', env_file.read_text())
        self.assertEqual(1, env_file.read_text().count('OPENAI_API_KEY='))
        self.store.mutate_env(revision, {'OPENAI_API_KEY': None})
        self.assertNotIn('OPENAI_API_KEY', self.store.read_env()[0])
        self.assertEqual(0o600, stat.S_IMODE(env_file.stat().st_mode))

    def test_env_values_cannot_inject_new_entries_and_do_not_change_process_environment(self):
        before = dict(os.environ)
        _, revision = self.store.read_env()
        for changes in ({'BAD\nNAME': 'x'}, {'OPENAI_API_KEY': 'x\nINJECTED=value'}, {'OPENAI_API_KEY': 'x\0y'}):
            with self.assertRaises(AdminError):
                self.store.mutate_env(revision, changes)
        self.assertEqual(before, dict(os.environ))
        self.assertFalse(self.home.exists())

    def test_mutation_failure_leaves_original_bytes_and_demo_never_touches_real_home(self):
        self.home.mkdir()
        file = self.home / 'config.yaml'
        file.write_text('model: real-private-model\n')
        _, revision = self.store.read_yaml()
        def fail(_):
            raise ValueError('synthetic failure')
        with self.assertRaises(ValueError):
            self.store.mutate_yaml('config.yaml', revision, fail)
        self.assertEqual('model: real-private-model\n', file.read_text())
        demo = ControlStore(self.home, mode='demo')
        data, revision = demo.read_yaml()
        self.assertEqual({}, data)
        demo.mutate_yaml('config.yaml', revision, lambda _: {'model': 'demo'})
        self.assertEqual('demo', demo.read_yaml()[0]['model'])
        self.assertEqual('model: real-private-model\n', file.read_text())

    def test_json_store_preserves_schema_and_uses_cron_lock(self):
        _, revision = self.store.read_json('cron/jobs.json', {'jobs': []})
        self.store.mutate_json('cron/jobs.json', revision,
            lambda value: {**value, 'jobs': [{'id': 'synthetic'}]}, default={'jobs': []}, lock='cron/.jobs.lock')
        self.assertEqual('synthetic', self.store.read_json('cron/jobs.json')[0]['jobs'][0]['id'])
        self.assertTrue((self.home / 'cron' / '.jobs.lock').is_file())


if __name__ == '__main__':
    unittest.main()
