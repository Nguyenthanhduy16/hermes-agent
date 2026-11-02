"""Management services and bounded controls for installed assistant resources."""
from datetime import datetime, timezone
import json
import re

import yaml

from .admin_auth import AdminError
from .admin_assistant import redact
from .admin_control_store import ControlStore, _revision
from .admin_management import ManagementAdmin

CONNECTION_SECTIONS = frozenset(('models', 'config', 'keys', 'channels', 'mcp'))
AUTOMATION_SECTIONS = frozenset(('skills', 'routines'))
EXTRA_SECTIONS = frozenset(('plugins', 'webhooks', 'profiles'))
CONTROL_SECTIONS = CONNECTION_SECTIONS | AUTOMATION_SECTIONS | EXTRA_SECTIONS
NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}')
PROTECTED_PLUGINS = frozenset(('assistant-core', 'zalo-platform', 'basic', 'nous', 'self-hosted'))


def _field(name, label, value='', *, type='text', required=True, maximum=200):
    return dict(name=name, label=label, value=value, type=type, required=required, max_length=maximum)


def _action(id, label, revision, fields, *, target=None, description=None):
    return dict(id=id, label=label, revision=revision, fields=fields, target=target, description=description)


def _text(values, key, maximum=200, *, optional=False):
    value = values.get(key, '')
    if not isinstance(value, str) or len(value) > maximum or (not optional and not value.strip()) or '\0' in value:
        raise AdminError('Invalid ' + key.replace('_', ' ') + '.')
    return value.strip()


def _name(values, key='name'):
    value = _text(values, key, 64)
    if not NAME.fullmatch(value) or value.lower() in ('default', '.', '..'):
        raise AdminError('Use a short name containing letters, numbers, hyphens or underscores.')
    return value


class ControlsAdmin:
    def __init__(self, home, *, mode='local', environ=None):
        self.store = ControlStore(home, mode=mode)
        self.mode, self.environ = mode, environ
        self.metadata = ManagementAdmin(home, mode=mode, environ=environ)
        self._connections = self._automation = None
        self._demo_profiles = set()

    def _service(self, section):
        if section in CONNECTION_SECTIONS:
            if self._connections is None:
                from .admin_connections import ConnectionsAdmin
                self._connections = ConnectionsAdmin(self.store, environ=self.environ)
            return self._connections
        if section in AUTOMATION_SECTIONS:
            if self._automation is None:
                from .admin_automation import AutomationAdmin
                self._automation = AutomationAdmin(self.store, environ=self.environ)
            return self._automation
        return self

    def snapshot(self, section):
        try:
            if section in CONNECTION_SECTIONS | AUTOMATION_SECTIONS:
                return self._service(section).snapshot(section)
            return getattr(self, '_' + section)()
        except AdminError as error:
            return dict(section=section, available=False, source='demo' if self.mode == 'demo' else 'hermes-home',
                        message=str(error), items=[], actions=[], updated_at=datetime.now(timezone.utc).isoformat())

    def action(self, section, data, *, authorize=None):
        if section not in CONTROL_SECTIONS:
            raise AdminError('This page has no management actions.', 404)
        if not isinstance(data, dict) or set(data) != {'action', 'expected_revision', 'confirm', 'values'} or data.get('confirm') is not True:
            raise AdminError('Confirm an exact management action before submitting.')
        if not isinstance(data['action'], str) or not isinstance(data['values'], dict):
            raise AdminError('Invalid management action.')
        if section in CONNECTION_SECTIONS | AUTOMATION_SECTIONS:
            return self._service(section).action(section, data, authorize=authorize)
        return self._extra_action(section, data, authorize=authorize)

    def _plugins(self):
        result = self.metadata.snapshot('plugins')
        config, revision = self.store.read_yaml()
        settings = config.get('plugins') or {}
        if not isinstance(settings, dict):
            raise AdminError('The plugin configuration is invalid.', 503)
        disabled = settings.get('disabled') or []
        if not isinstance(disabled, list):
            raise AdminError('The plugin selection is invalid.', 503)
        result['actions'] = []
        for item in result['items']:
            name = item.get('name')
            if not isinstance(name, str) or not NAME.fullmatch(name) or name in PROTECTED_PLUGINS:
                item['management'] = 'Required policy or authentication plugin; managed by the server operator.'
                continue
            enabled = name not in disabled and item.get('status') != 'installed'
            item['status'] = 'disabled' if name in disabled else 'enabled' if enabled else 'installed'
            # A manifest whose declared name differs from its folder remains inspectable.
            if self.mode != 'demo':
                text, _ = self.store.read_text(f'plugins/{name}/plugin.yaml')
                if not text:
                    continue
            result['actions'].append(_action('plugin_toggle', ('Disable ' if enabled else 'Enable ') + name,
                revision, [_field('name', 'Plugin', name, type='hidden'),
                           _field('enabled', 'Enabled on next gateway restart', not enabled, type='checkbox')], target=name))
        result['message'] = 'Inspect installed plugins and change their next-start selection. Required policy and authentication plugins stay enabled. No plugin code is installed or executed by this page.'
        return result

    def _webhooks(self):
        if self.mode == 'demo' and 'webhook_subscriptions.json' not in self.store._demo_files:
            _, rev = self.store.read_json('webhook_subscriptions.json', {})
            self.store.mutate_json('webhook_subscriptions.json', rev, lambda _: {
                'demo-research-update': {'enabled': False, 'events': ['update'], 'secret': 'demo-only',
                                         'description': 'Research events', 'prompt': 'Summarize the event.', 'deliver': 'log'}}, default={})
        data, revision = self.store.read_json('webhook_subscriptions.json', {})
        if not isinstance(data, dict):
            raise AdminError('The webhook subscription store is invalid.', 503)
        result = dict(section='webhooks', available=True, source='demo' if self.mode == 'demo' else 'hermes-home',
            items=[], actions=[], updated_at=datetime.now(timezone.utc).isoformat(),
            message='Signed incoming events produce local log output. Creating a route does not enable a public listener; configure the webhook platform separately. Existing delivery targets are preserved.')
        result['actions'].append(_action('webhook_create', 'Create webhook', revision, [
            _field('name', 'Route name', maximum=64), _field('description', 'Description', required=False, maximum=300),
            _field('events', 'Event names (comma separated)', required=False, maximum=500),
            _field('prompt', 'Instructions for the assistant', type='textarea', maximum=4000),
            _field('secret', 'HMAC signing secret (at least 32 characters)', type='password', maximum=256),
        ], description='Keep the signing secret in the sender configuration. It is never shown again. New subscriptions are created paused and deliver to the local log.'))
        for name, spec in list(data.items())[:100]:
            if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(spec, dict):
                continue
            result['items'].append(dict(name=name, enabled=spec.get('enabled') is not False,
                events=len(spec.get('events', [])) if isinstance(spec.get('events'), list) else 0,
                secret_configured=bool(spec.get('secret')), delivery='local log' if spec.get('deliver', 'log') == 'log' else 'configured target'))
            hidden = [_field('name', 'Route', name, type='hidden')]
            result['actions'].extend([
                _action('webhook_toggle', ('Pause ' if spec.get('enabled') is not False else 'Enable ') + name,
                    revision, [*hidden, _field('enabled', 'Enabled', spec.get('enabled') is False, type='checkbox')], target=name),
                _action('webhook_delete', 'Remove ' + name, revision, hidden, target=name)])
        return result

    def _profiles(self):
        result = self.metadata.snapshot('profiles')
        if self.mode == 'demo':
            names = {'demo-research'} | self._demo_profiles
            result['items'] = [dict(name=name, status='local profile') for name in sorted(names)]
        result.update(available=True, actions=[_action('profile_create', 'Create isolated profile', _revision(None), [
            _field('name', 'Profile name', maximum=64), _field('model', 'Main model'),
            _field('provider', 'Provider', 'auto'), _field('description', 'Description', required=False, maximum=1000)],
            description='Create a separate home with no copied credentials, channels or conversation history. This does not switch or start a gateway.')])
        for item in result['items']:
            name = item.get('name')
            if not isinstance(name, str) or not NAME.fullmatch(name):
                continue
            config, revision = self.store.read_yaml(f'profiles/{name}/config.yaml')
            model = config.get('model') or {}
            item['model'] = redact(model.get('default') if isinstance(model, dict) else model, 200)
            result['actions'].append(_action('profile_model', 'Change model: ' + name, revision, [
                _field('name', 'Profile', name, type='hidden'),
                _field('model', 'Main model', item['model'] or ''),
                _field('provider', 'Provider', redact(model.get('provider', 'auto'), 100) if isinstance(model, dict) else 'auto')], target=name))
            meta, revision = self.store.read_yaml(f'profiles/{name}/profile.yaml')
            description = redact(meta.get('description'), 1000) if isinstance(meta.get('description'), str) else ''
            description = description or ''
            item['description'] = description[:1000]
            result['actions'].append(_action('profile_describe', 'Edit description: ' + name, revision, [
                _field('name', 'Profile', name, type='hidden'),
                _field('description', 'Description', description[:1000], type='textarea', required=False, maximum=1000)], target=name))
        result['message'] = 'Profiles stay inside the selected bot home. Create an isolated profile or edit its model and description. This does not copy credentials, switch the active profile or restart a gateway.'
        return result

    def _extra_action(self, section, data, *, authorize=None):
        values, action, revision = data['values'], data['action'], data['expected_revision']
        snapshot = self.snapshot(section)
        descriptor = next((item for item in snapshot.get('actions', []) if item['id'] == action and
                           (not item.get('target') or item['target'] == values.get('name'))), None)
        if descriptor is None or set(values) - {field['name'] for field in descriptor['fields']}:
            raise AdminError('Unsupported management fields or action.')
        name = _name(values)
        if section == 'plugins' and action == 'plugin_toggle':
            if name in PROTECTED_PLUGINS or type(values.get('enabled')) is not bool:
                raise AdminError('This plugin selection cannot be changed.')
            def change(config):
                settings = config.setdefault('plugins', {})
                enabled, disabled = settings.get('enabled', []), settings.get('disabled', [])
                if not isinstance(enabled, list) or not isinstance(disabled, list):
                    raise AdminError('The plugin configuration is invalid.', 503)
                if values['enabled']:
                    settings['enabled'] = sorted(set(enabled) | {name})
                    settings['disabled'] = [item for item in disabled if item != name]
                else:
                    settings['disabled'] = sorted(set(disabled) | {name})
                    settings['enabled'] = [item for item in enabled if item != name]
                return config
            self.store.mutate_yaml('config.yaml', revision, change, authorize=authorize)
        elif section == 'webhooks':
            def change(subscriptions):
                if not isinstance(subscriptions, dict):
                    raise AdminError('The webhook subscription store is invalid.', 503)
                if action == 'webhook_create':
                    if name in subscriptions:
                        raise AdminError('This route already exists.', 409)
                    secret = _text(values, 'secret', 256)
                    if len(secret) < 32 or any(ord(c) < 33 or ord(c) > 126 for c in secret):
                        raise AdminError('Use a signing secret of 32 to 256 visible ASCII characters.')
                    events = [item.strip() for item in _text(values, 'events', 500, optional=True).split(',') if item.strip()]
                    if len(events) > 20 or any(not re.fullmatch(r'[\w.*:-]{1,80}', item) for item in events):
                        raise AdminError('Invalid webhook event names.')
                    subscriptions[name] = dict(description=_text(values, 'description', 300, optional=True), events=events,
                        prompt=_text(values, 'prompt', 4000), secret=secret, skills=[], deliver='log', profile='default',
                        enabled=False, created_at=datetime.now(timezone.utc).isoformat())
                elif name not in subscriptions or not isinstance(subscriptions[name], dict):
                    raise AdminError('This route no longer exists. Refresh before trying again.', 409)
                elif action == 'webhook_toggle' and type(values.get('enabled')) is bool:
                    subscriptions[name]['enabled'] = values['enabled']
                elif action == 'webhook_delete':
                    del subscriptions[name]
                else:
                    raise AdminError('Invalid webhook action.')
                return subscriptions
            self.store.mutate_json('webhook_subscriptions.json', revision, change, default={},
                                   authorize=authorize, lock='.webhook_subscriptions.lock')
        elif section == 'profiles' and action == 'profile_create':
            model, provider = _text(values, 'model'), _text(values, 'provider')
            self._validate_model(model, provider)
            profile = dict(model=dict(default=model, provider=provider),
                memory=dict(memory_enabled=False, user_profile_enabled=False),
                terminal=dict(backend='docker', docker_mount_cwd_to_workspace=False))
            self.store.create_tree('profiles/' + name, {'config.yaml': yaml.safe_dump(profile), '.env': '',
                'profile.yaml': yaml.safe_dump(dict(description=_text(values, 'description', 1000, optional=True), description_auto=False)),
                'SOUL.md': '# Assistant\nFollow the owner\'s instructions and respect private data.\n'}, revision, authorize=authorize)
            if self.mode == 'demo':
                self._demo_profiles.add(name)
        elif section == 'profiles' and action in ('profile_model', 'profile_describe'):
            relative = f'profiles/{name}/' + ('config.yaml' if action == 'profile_model' else 'profile.yaml')
            def change(config):
                if action == 'profile_model':
                    model = config.get('model')
                    model = dict(model) if isinstance(model, dict) else {}
                    chosen, provider = _text(values, 'model'), _text(values, 'provider')
                    self._validate_model(chosen, provider)
                    if model.get('provider', 'auto') != provider:
                        for key in ('api_key', 'base_url', 'api_mode'):
                            model.pop(key, None)
                    model.update(default=chosen, provider=provider)
                    config['model'] = model
                else:
                    config.update(description=_text(values, 'description', 1000, optional=True), description_auto=False)
                return config
            self.store.mutate_yaml(relative, revision, change, authorize=authorize)
        else:
            raise AdminError('Unsupported management action.')
        return dict(ok=True, message='Saved. Refresh to review the current state. Gateway configuration changes apply at its next start.')

    @staticmethod
    def _validate_model(model, provider):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,199}', model) or '://' in model:
            raise AdminError('Use a model identifier, without an endpoint or credentials.')
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,79}', provider):
            raise AdminError('Use a provider identifier such as auto, openrouter or anthropic.')
