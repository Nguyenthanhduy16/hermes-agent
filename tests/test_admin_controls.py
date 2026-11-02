import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from hermes_zalo.admin_auth import AdminError
from hermes_zalo.admin_controls import ControlsAdmin


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'bot'
        self.service = ControlsAdmin(self.home, environ={})
        self.store = self.service.store

    def write(self, relative, content):
        _, revision = self.store.read_text(relative)
        self.store.write_text(relative, content, revision)

    def action(self, section, id, values, *, revision=None, authorize=None):
        descriptor = next(item for item in self.service.snapshot(section)['actions'] if item['id'] == id and
            (not item.get('target') or item['target'] == values.get('name')))
        return self.service.action(section, dict(action=id, expected_revision=revision or descriptor['revision'],
            confirm=True, values=values), authorize=authorize)

    def test_profile_creation_is_isolated_and_duplicate_cannot_replace_data(self):
        values = dict(name='research', model='vendor/reasoner', provider='auto', description='Private research')
        self.assertTrue(self.action('profiles', 'profile_create', values)['ok'])
        profile = self.home / 'profiles' / 'research'
        self.assertEqual('', (profile / '.env').read_text())
        config, _ = self.store.read_yaml('profiles/research/config.yaml')
        self.assertEqual('vendor/reasoner', config['model']['default'])
        self.assertFalse(config['memory']['memory_enabled'])
        self.assertFalse(config['terminal']['docker_mount_cwd_to_workspace'])
        self.assertFalse((self.home / '.env').exists())
        with self.assertRaises(AdminError) as error:
            self.action('profiles', 'profile_create', values)
        self.assertEqual(409, error.exception.status)
        self.assertEqual('', (profile / '.env').read_text())
        self.assertFalse(list((self.home / 'profiles').glob('.profile-*')))

    def test_profile_changes_preserve_settings_and_compare_revisions(self):
        self.write('profiles/research/config.yaml', 'model:\n  default: old\n  provider: auto\nother: keep\n')
        descriptor = next(item for item in self.service.snapshot('profiles')['actions'] if item['id'] == 'profile_model')
        self.action('profiles', 'profile_model', dict(name='research', model='new', provider='auto'))
        config, _ = self.store.read_yaml('profiles/research/config.yaml')
        self.assertEqual('keep', config['other'])
        with self.assertRaises(AdminError) as error:
            self.action('profiles', 'profile_model', dict(name='research', model='stale', provider='auto'), revision=descriptor['revision'])
        self.assertEqual(409, error.exception.status)
        self.action('profiles', 'profile_describe', dict(name='research', description='Research assistant'))
        self.assertEqual('Research assistant', self.store.read_yaml('profiles/research/profile.yaml')[0]['description'])

    def test_profile_creation_denied_before_home_exists_and_invalid_paths_are_blocked(self):
        def denied():
            raise AdminError('Revoked.', 403)
        values = dict(name='research', model='demo', provider='auto')
        with self.assertRaises(AdminError):
            self.action('profiles', 'profile_create', values, authorize=denied)
        self.assertFalse(self.home.exists())
        for name in ('../outside', 'default', 'a/b', '.hidden'):
            with self.assertRaises(AdminError):
                self.action('profiles', 'profile_create', {**values, 'name': name})
        self.assertFalse(self.home.exists())

    def test_webhook_creation_pause_enable_remove_never_returns_secret_or_sends(self):
        secret = 'synthetic-signing-secret-never-returned-123456'
        values = dict(name='research', description='Research events', events='report.ready,task.done',
                      prompt='Summarize the event.', secret=secret)
        self.action('webhooks', 'webhook_create', values)
        data, _ = self.store.read_json('webhook_subscriptions.json')
        self.assertEqual(secret, data['research']['secret'])
        self.assertFalse(data['research']['enabled'])
        self.assertEqual('log', data['research']['deliver'])
        self.assertNotIn(secret, json.dumps(self.service.snapshot('webhooks')))
        self.action('webhooks', 'webhook_toggle', dict(name='research', enabled=True))
        self.assertTrue(self.store.read_json('webhook_subscriptions.json')[0]['research']['enabled'])
        self.action('webhooks', 'webhook_delete', dict(name='research'))
        self.assertEqual({}, self.store.read_json('webhook_subscriptions.json')[0])

    def test_webhook_toggle_preserves_existing_target_and_unknown_fields(self):
        self.write('webhook_subscriptions.json', json.dumps({'alerts': {
            'enabled': True, 'secret': 'private-secret', 'deliver': 'telegram:private-target', 'other': {'keep': True}}}))
        self.action('webhooks', 'webhook_toggle', dict(name='alerts', enabled=False))
        data, _ = self.store.read_json('webhook_subscriptions.json')
        self.assertEqual('telegram:private-target', data['alerts']['deliver'])
        self.assertEqual({'keep': True}, data['alerts']['other'])
        self.assertNotIn('private-target', json.dumps(self.service.snapshot('webhooks')))

    def test_optional_plugins_toggle_but_required_plugins_cannot_be_disabled(self):
        self.write('plugins/research/plugin.yaml', 'name: research\nversion: 1\n')
        self.write('plugins/assistant-core/plugin.yaml', 'name: assistant-core\n')
        self.write('config.yaml', 'plugins:\n  enabled: [research, assistant-core]\nother: keep\n')
        self.action('plugins', 'plugin_toggle', dict(name='research', enabled=False))
        config, _ = self.store.read_yaml()
        self.assertIn('research', config['plugins']['disabled'])
        self.assertIn('assistant-core', config['plugins']['enabled'])
        self.assertEqual('keep', config['other'])
        with self.assertRaises(AdminError):
            self.service.action('plugins', dict(action='plugin_toggle', expected_revision=self.store.read_yaml()[1],
                confirm=True, values=dict(name='assistant-core', enabled=False)))

    def test_demo_mutations_do_not_inspect_or_change_real_files(self):
        self.write('webhook_subscriptions.json', '{"real-private-name":{"secret":"real-private-secret"}}')
        self.service = ControlsAdmin(self.home, mode='demo', environ={})
        self.assertNotIn('real-private', json.dumps(self.service.snapshot('webhooks')))
        self.action('profiles', 'profile_create', dict(name='demo-new', model='demo', provider='auto'))
        self.assertFalse((self.home / 'profiles').exists())
        self.assertIn('demo-new', [item['name'] for item in self.service.snapshot('profiles')['items']])
        self.assertIn('real-private-secret', (self.home / 'webhook_subscriptions.json').read_text())

    def test_unknown_action_fields_and_missing_confirmation_cannot_mutate(self):
        before = self.service.snapshot('profiles')
        descriptor = before['actions'][0]
        data = dict(action=descriptor['id'], expected_revision=descriptor['revision'], confirm=False,
                    values=dict(name='research', model='demo', provider='auto'))
        for invalid in (data, {**data, 'confirm': True, 'actor': 'forged'},
                        {**data, 'confirm': True, 'values': {**data['values'], 'command': 'run'}}):
            with self.assertRaises(AdminError):
                self.service.action('profiles', invalid)
        self.assertFalse(self.home.exists())


if __name__ == '__main__':
    unittest.main()
