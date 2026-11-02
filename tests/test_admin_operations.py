"""System & operations section: status detection, opt-in gateway control (fake runner only),
health checks, private backups, update checks and demo mode. Never runs systemctl, docker or git."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import tarfile
from tempfile import TemporaryDirectory
import time
import unittest
from unittest import mock

import yaml

from hermes_zalo import admin_operations as ops
from hermes_zalo import console_sections
from hermes_zalo.admin_auth import AdminError
from hermes_zalo.console_sections import ConsoleContext

SECRET = 'synthetic-telegram-token-value-123456'


class FakeRunner:
    def __init__(self, replies=None):
        self.calls, self.replies = [], replies or {}

    def __call__(self, argv, timeout, environ):
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        self.calls.append((list(argv), timeout))
        for key, reply in self.replies.items():
            if key in ' '.join(argv):
                return reply(argv) if callable(reply) else reply
        return 0, '', ''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / 'home'
        self.home.mkdir()
        self.src = root / 'hermes-src'
        (self.src / '.git' / 'refs' / 'heads').mkdir(parents=True)
        (self.src / '.git' / 'HEAD').write_text('ref: refs/heads/main\n')
        (self.src / '.git' / 'refs' / 'heads' / 'main').write_text('a' * 40 + '\n')
        self.env = {'PATH': '/usr/bin:/bin', 'HERMES_SRC': str(self.src), 'XDG_RUNTIME_DIR': '/run/user/1000',
                    'SECRET_THAT_MUST_NOT_PASS': 'x'}
        self.runner = FakeRunner()
        self.patches = [mock.patch.object(ops, 'RUNNER', self.runner),
                        mock.patch.object(ops, '_which', lambda name, env: '/usr/bin/' + name),
                        mock.patch.object(ops, '_env_files', lambda home: [('Hermes home', Path(home) / '.env')]),
                        mock.patch.object(ops, 'HTTP_GET', self.fail_http)]
        for patch in self.patches:
            patch.start()
        ops.HISTORY.enabled = False
        for registry in (ops._CACHE, ops._JOBS, ops._LAST_CHECKS, ops._LAST_UPDATES, ops._DEMO):
            registry.clear()
        self.write('config.yaml', yaml.safe_dump({'terminal': {'backend': 'docker'},
                                                  'plugins': {'enabled': ['assistant-core']}}))

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def fail_http(self, url, timeout):
        raise AssertionError('unexpected HTTP request to ' + url)

    def write(self, rel, text, mode=None):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if mode is not None:
            path.chmod(mode)
        return path

    def ctx(self, **env):
        return ConsoleContext(home=self.home, mode='local', assistant_dir=self.home / 'assistant-core',
                              environ=dict(self.env, **env))

    def snap(self, **env):
        return ops.snapshot(self.ctx(**env), {})

    def act(self, action, values=None, revision=None, **env):
        current = self.snap(**env)
        payload = {'action': action, 'expected_revision': current['revision'] if revision is None else revision,
                   'confirm': True, 'values': values or {}}
        return ops.action(self.ctx(**env), payload)

    def wait_job(self):
        for _ in range(500):
            job = ops._job(self.home)
            if job and job['status'] != 'running':
                return job
            time.sleep(0.01)
        self.fail('job did not finish')


class RegistrationTests(unittest.TestCase):
    def test_section_is_registered_for_the_console(self):
        section = console_sections.get('operations')
        self.assertIs(ops.snapshot, section.snapshot)
        self.assertIs(ops.action, section.action)


class ControlParsingTests(unittest.TestCase):
    def test_explicit_forms_build_fixed_argv_lists(self):
        which = lambda name: '/usr/bin/' + name
        cases = {
            'systemd-user:hermes-gateway.service': ['/usr/bin/systemctl', '--user', '--no-block', 'restart',
                                                    'hermes-gateway.service'],
            'systemd:hermes-gateway': ['/usr/bin/systemctl', '--no-block', 'restart', 'hermes-gateway'],
            'systemd-sudo:hermes@bot.service': ['/usr/bin/sudo', '-n', '/usr/bin/systemctl', '--no-block', 'restart',
                                                'hermes@bot.service'],
            'docker:hermes-zalo-bot': ['/usr/bin/docker', 'restart', '-t', '60', 'hermes-zalo-bot'],
        }
        for raw, argv in cases.items():
            control, error = ops.parse_control(raw)
            self.assertIsNone(error, raw)
            self.assertEqual(argv, control.command('restart', which))
        docker, _ = ops.parse_control('docker:bot')
        self.assertEqual(['/usr/bin/docker', 'start', 'bot'], docker.command('start', which))
        self.assertEqual('systemctl --user stop x.service', ops.parse_control('systemd-user:x.service')[0].display('stop'))
        with self.assertRaises(AdminError):
            docker.command('rm', which)

    def test_off_and_malformed_values_never_enable_control(self):
        for raw in ('', 'off', 'false', None):
            self.assertEqual((None, None), ops.parse_control(raw))
        for raw in ('hermes-gateway.service', 'shell:reboot', 'systemd-user:', 'systemd-user:-H evil',
                    'systemd-user:a;reboot', 'systemd-user:x.socket', 'systemd-user:../x.service',
                    'docker:$(id)', 'docker:-v', 'docker:a b', 'systemd-user:' + 'a' * 120, 'systemd-user:x\n.service'):
            control, error = ops.parse_control(raw)
            self.assertIsNone(control, raw)
            self.assertTrue(error, raw)

    def test_child_environment_is_minimal(self):
        env = ops._child_env({'PATH': '/bin', 'API_SERVER_KEY': 'k', 'TELEGRAM_BOT_TOKEN': SECRET, 'HOME': '/h'})
        self.assertNotIn('API_SERVER_KEY', env)
        self.assertNotIn('TELEGRAM_BOT_TOKEN', env)
        self.assertEqual('/bin', env['PATH'])
        self.assertEqual('0', env['GIT_TERMINAL_PROMPT'])

    def test_real_runner_rejects_malformed_argv_without_a_shell(self):
        with self.assertRaises(AdminError):
            ops._run(['systemctl', 'x\0y'], 1, {})
        with self.assertRaises(AdminError):
            ops._run([], 1, {})


class GatewayStatusTests(Base):
    def pid_record(self, pid, start=None):
        self.write('gateway.pid', json.dumps({'pid': pid, 'kind': 'hermes-gateway', 'start_time': start}))

    def state(self, **fields):
        base = {'gateway_state': 'running', 'updated_at': datetime.now(timezone.utc).isoformat(),
                'platforms': {'telegram': {'state': 'connected', 'error_message': SECRET}},
                'code_version': '0.21.6', 'code_sha': 'b' * 40, 'active_agents': 2}
        self.write('gateway_state.json', json.dumps(dict(base, **fields)))

    def test_live_pid_with_matching_start_time_is_running(self):
        self.pid_record(os.getpid(), ops._proc_start(os.getpid()))
        self.state()
        gateway = self.snap()['gateway']
        self.assertEqual('running', gateway['state'])
        self.assertEqual('pid file', gateway['detected_by'])
        self.assertTrue(gateway['inside_console'])
        self.assertEqual([{'name': 'telegram', 'state': 'connected', 'needs_attention': False, 'error': None}],
                         gateway['platforms'])
        self.assertEqual(('0.21.6', 'b' * 40, 2), (gateway['version'], gateway['running_commit'], gateway['active_agents']))
        self.assertNotIn(SECRET, json.dumps(gateway))

    def test_reused_or_dead_pid_is_stopped(self):
        self.pid_record(os.getpid(), (ops._proc_start(os.getpid()) or 0) + 999)
        self.assertEqual('stopped', self.snap()['gateway']['state'])
        self.pid_record(2 ** 22 + 12345)
        ops._CACHE.clear()
        snapshot = self.snap()
        self.assertEqual('stopped', snapshot['gateway']['state'])
        self.assertEqual([], snapshot['gateway']['platforms'])

    def test_failed_start_and_missing_files(self):
        self.assertEqual('unknown', self.snap()['gateway']['state'])
        self.state(gateway_state='startup_failed', pid=None)
        self.assertEqual('failed', self.snap()['gateway']['state'])

    def test_stale_status_file_is_reported(self):
        self.pid_record(os.getpid(), ops._proc_start(os.getpid()))
        self.state(updated_at='2020-01-01T00:00:00+00:00')
        gateway = self.snap()['gateway']
        self.assertEqual('running', gateway['state'])
        self.assertTrue(gateway.get('stale'))
        self.assertEqual([], gateway['platforms'])

    def test_systemd_is_authoritative_and_flags_another_home(self):
        self.runner.replies['show'] = (0, 'LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=4321\n'
                                          'ActiveEnterTimestampMonotonic=1000000\n', '')
        gateway = self.snap(HERMES_CONSOLE_GATEWAY_CONTROL='systemd-user:hermes-gateway.service')['gateway']
        self.assertEqual(('running', 'systemd', 4321), (gateway['state'], gateway['detected_by'], gateway['pid']))
        self.assertTrue(gateway.get('mismatch'))
        self.assertIn('HERMES_HOME', gateway['detail'])
        argv = self.runner.calls[0][0]
        self.assertEqual(['/usr/bin/systemctl', '--user', 'show'], argv[:3])
        self.assertEqual('hermes-gateway.service', argv[-1])

    def test_docker_inspect_and_unknown_unit(self):
        self.runner.replies['inspect'] = (0, json.dumps({'Status': 'exited', 'Running': False, 'Pid': 0,
                                                         'StartedAt': '2026-10-31T10:00:00.123Z'}), '')
        self.assertEqual('stopped', self.snap(HERMES_CONSOLE_GATEWAY_CONTROL='docker:bot')['gateway']['state'])
        ops._CACHE.clear()
        self.runner.replies = {'show': (0, 'LoadState=not-found\nActiveState=inactive\n', '')}
        gateway = self.snap(HERMES_CONSOLE_GATEWAY_CONTROL='systemd-user:nope.service')['gateway']
        self.assertEqual('unknown', gateway['state'])

    def test_scheduler_heartbeat(self):
        self.assertEqual('missing', self.snap()['scheduler']['status'])
        self.write('cron/ticker_heartbeat', str(time.time() - 20))
        self.assertEqual('fresh', self.snap()['scheduler']['status'])
        self.write('cron/ticker_heartbeat', str(time.time() - 4000))
        self.assertEqual('stale', self.snap()['scheduler']['status'])


class ApiHealthTests(Base):
    def test_only_loopback_is_probed_with_a_short_timeout(self):
        seen = []
        with mock.patch.object(ops, 'HTTP_GET', lambda url, timeout: seen.append((url, timeout)) or (200, None)):
            self.assertEqual('ok', ops.api_health({'API_SERVER_ENABLED': 'true'})['status'])
            self.assertEqual(('http://127.0.0.1:8642/health', 2), seen[-1])
            ops._CACHE.clear()
            ops.api_health({'HERMES_CONSOLE_CHAT_API_URL': 'http://[::1]:9000/v1'})
            self.assertEqual('http://[::1]:9000/health', seen[-1][0])
            count = len(seen)
            for env in ({'HERMES_CONSOLE_CHAT_API_URL': 'https://api.example.com/v1'},
                        {'API_SERVER_ENABLED': 'true', 'API_SERVER_HOST': '0.0.0.0'}, {}):
                self.assertIn(ops.api_health(env)['status'], ('not_probed', 'not_configured'))
            self.assertEqual(count, len(seen))
        self.assertEqual('invalid', ops.api_health({'API_SERVER_ENABLED': '1', 'API_SERVER_PORT': 'x'})['status'])


class ResourceTests(unittest.TestCase):
    def test_pressure_banner_rules(self):
        self.assertEqual([], ops.pressure({'memory_percent': 50, 'disk_percent': 50, 'load_1m': 1, 'cpu_count': 2}))
        ids = [a['id'] for a in ops.pressure({'memory_percent': 95, 'disk_percent': 91, 'load_1m': 3, 'cpu_count': 2})]
        self.assertEqual(['memory', 'disk', 'load'], ids)

    def test_history_buckets_and_resources_shape(self):
        history = ops.History()
        now = 1_800_000_000
        for minute in range(120):
            history.record(now - minute * 60, {'load_1m': 1.0, 'memory_percent': 50.0, 'disk_percent': 10.0})
        series = history.series(now + 1)
        self.assertLessEqual(len(series['points']), ops.CHART_BUCKETS)
        self.assertEqual(120, sum(p['samples'] for p in series['points']))
        self.assertEqual(50.0, series['points'][-1]['memory_percent'])
        resources = ops.read_resources(Path('.'))
        self.assertIn('load_15m', resources)
        self.assertIn('swap_total_bytes', resources)


class HealthCheckTests(Base):
    def checks(self, **env):
        return {c['id']: c for c in self.snap(**env)['checks']['items']}

    def test_permissions_config_toolsets_sandbox_and_no_secret_values(self):
        self.write('.env', f'TELEGRAM_BOT_TOKEN={SECRET}\n', 0o644)
        (self.home / 'plugins' / 'assistant-core').mkdir(parents=True)
        checks = self.checks()
        self.assertEqual('warn', checks['env-hermes']['status'])
        self.assertIn('chmod 600', checks['env-hermes']['hint'])
        self.assertEqual('pass', checks['channels']['status'])
        self.assertEqual('pass', checks['plugin']['status'])
        self.assertEqual('pass', checks['sandbox']['status'])
        self.assertNotIn(SECRET, json.dumps(self.snap()))
        (self.home / '.env').chmod(0o600)
        self.write('config.yaml', yaml.safe_dump({'terminal': {'backend': 'local'},
                                                  'platform_toolsets': {'telegram': ['web', 'computer_use']},
                                                  'platforms': {'zalo': {'enabled': True}}}))
        checks = self.checks()
        self.assertEqual('pass', checks['env-hermes']['status'])
        self.assertEqual('warn', checks['sandbox']['status'])
        self.assertEqual('fail', checks['toolsets']['status'])
        self.assertEqual('fail', checks['channels']['status'])
        self.assertIn('ZALO_BOT_TOKEN', checks['channels']['hint'])
        self.assertEqual('warn', checks['plugin']['status'])
        self.write('config.yaml', 'a: [unclosed')
        self.assertEqual('fail', self.checks()['config']['status'])

    def test_console_exposure(self):
        self.assertEqual('pass', self.checks()['console']['status'])
        self.assertEqual('fail', self.checks(ZALO_ADMIN_HOST='0.0.0.0')['console']['status'])
        self.assertEqual('pass', self.checks(ZALO_ADMIN_HOST='0.0.0.0',
                                             ZALO_ADMIN_PUBLIC_URL='https://ops.example.com')['console']['status'])

    def test_run_checks_writes_and_removes_a_probe(self):
        result = self.act('run_checks')
        self.assertTrue(result['ok'])
        snapshot = self.snap()
        self.assertTrue(snapshot['checks']['deep'])
        self.assertEqual('pass', {c['id']: c for c in snapshot['checks']['items']}['home']['status'])
        self.assertEqual([], [p for p in self.home.iterdir() if p.name.startswith('.console-write-test')])


class GatewayControlTests(Base):
    CONTROL = {'HERMES_CONSOLE_GATEWAY_CONTROL': 'systemd-user:hermes-gateway.service'}

    def setUp(self):
        super().setUp()
        self.runner.replies['show'] = (0, 'LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=77\n', '')

    def control_calls(self):
        return [argv for argv, _ in self.runner.calls if 'show' not in argv]

    def test_disabled_control_offers_no_actions_and_explains_how_to_enable(self):
        snapshot = self.snap()
        self.assertFalse(snapshot['control']['enabled'])
        self.assertTrue(any('systemd-user:' in e['value'] for e in snapshot['control']['examples']))
        self.assertFalse([a for a in snapshot['actions'] if a['id'].startswith('gateway_')])
        with self.assertRaises(AdminError) as raised:
            self.act('gateway_restart')
        self.assertEqual(409, raised.exception.status)
        self.assertEqual([], self.runner.calls)
        snapshot = self.snap(HERMES_CONSOLE_GATEWAY_CONTROL='shell:rm -rf /')
        self.assertFalse(snapshot['control']['enabled'])
        self.assertIn('must start with', snapshot['control']['error'])

    def test_restart_runs_the_fixed_argv(self):
        result = self.act('gateway_restart', **self.CONTROL)
        self.assertTrue(result['ok'])
        self.assertEqual([['/usr/bin/systemctl', '--user', '--no-block', 'restart', 'hermes-gateway.service']],
                         self.control_calls())
        self.assertEqual(ops.CONTROL_TIMEOUT, [t for a, t in self.runner.calls if 'restart' in a][0])

    def test_stop_requires_the_typed_service_name(self):
        for values in ({}, {'confirm_name': 'hermes-gateway'}, {'confirm_name': 'hermes-gateway.service', 'x': 1}):
            with self.assertRaises(AdminError):
                self.act('gateway_stop', values, **self.CONTROL)
        self.assertEqual([], self.control_calls())
        self.act('gateway_stop', {'confirm_name': 'hermes-gateway.service'}, **self.CONTROL)
        self.assertEqual(['/usr/bin/systemctl', '--user', '--no-block', 'stop', 'hermes-gateway.service'],
                         self.control_calls()[0])

    def test_start_is_only_offered_when_stopped_and_failures_are_reported(self):
        with self.assertRaises(AdminError):
            self.act('gateway_start', **self.CONTROL)
        ops._CACHE.clear()
        self.runner.replies['show'] = (0, 'LoadState=loaded\nActiveState=inactive\nSubState=dead\nMainPID=0\n', '')
        self.runner.replies['--no-block start'] = (1, '', f'Failed to start: token {SECRET}=abc /etc/secret/path')
        with self.assertRaises(AdminError) as raised:
            self.act('gateway_start', **self.CONTROL)
        self.assertIn('Start failed', str(raised.exception))
        self.assertNotIn('/etc/secret/path', str(raised.exception))

    def test_stale_revision_and_unknown_actions_are_refused(self):
        with self.assertRaises(AdminError) as raised:
            self.act('gateway_restart', revision='old', **self.CONTROL)
        self.assertEqual(409, raised.exception.status)
        with self.assertRaises(AdminError):
            self.act('run_shell', {'command': 'id'}, **self.CONTROL)
        with self.assertRaises(AdminError):
            ops.action(self.ctx(**self.CONTROL), {'action': 'gateway_restart', 'expected_revision': 'x', 'values': {}})
        self.assertEqual([], self.control_calls())

    def test_sudo_and_docker_forms(self):
        self.act('gateway_restart', HERMES_CONSOLE_GATEWAY_CONTROL='systemd-sudo:hermes-gateway.service')
        self.assertEqual(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', '--no-block', 'restart', 'hermes-gateway.service'],
                         self.control_calls()[-1])
        self.runner.replies['inspect'] = (0, json.dumps({'Status': 'running', 'Pid': 9,
                                                         'StartedAt': '2026-10-31T10:00:00Z'}), '')
        self.act('gateway_restart', HERMES_CONSOLE_GATEWAY_CONTROL='docker:hermes-zalo-bot')
        self.wait_job()
        self.assertEqual(['/usr/bin/docker', 'restart', '-t', '60', 'hermes-zalo-bot'], self.control_calls()[-1])

    def test_missing_tool_is_reported_without_running_anything(self):
        with mock.patch.object(ops, '_which', lambda name, env: None):
            snapshot = self.snap(**self.CONTROL)
            self.assertIn('not installed', snapshot['gateway']['detail'])
            with self.assertRaises(AdminError) as raised:
                ops._run_control(self.home, self.env, ops.parse_control('docker:bot')[0], 'restart')
        self.assertEqual(503, raised.exception.status)
        self.assertEqual([], self.runner.calls)


class BackupTests(Base):
    def populate(self):
        self.write('.env', f'TELEGRAM_BOT_TOKEN={SECRET}\n', 0o600)
        self.write('auth.json', '{"token": "x"}', 0o600)
        self.write('memories/MEMORY.md', 'remember me')
        self.write('skills/a/hermes-agent/SKILL.md', 'nested name is kept')
        self.write('logs/gateway.log', 'small log')
        self.write('logs/huge.log', 'x' * 4096)
        self.write('cache/models.json', 'cache')
        self.write('installs/runtime.bin', 'runtime')
        self.write('skills/a/__pycache__/x.pyc', 'bytecode')
        self.write('gateway.pid', '{"pid": 1}')
        self.write('gateway.lock', '')
        outside = Path(self.tmp.name) / 'outside.txt'
        outside.write_text('outside the home')
        (self.home / 'link.txt').symlink_to(outside)
        (self.home / 'linkdir').symlink_to(Path(self.tmp.name))
        db = sqlite3.connect(self.home / 'state.db')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE t (v TEXT)')
        db.execute("INSERT INTO t VALUES ('committed')")
        db.commit()
        self.db = db  # keep open so -wal/-shm exist
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.bind(str(self.home / 'gateway.sock'))
        self.addCleanup(self.sock.close)
        self.addCleanup(db.close)

    def members(self, path):
        with tarfile.open(path) as tar:
            names = tar.getnames()
            manifest = json.load(tar.extractfile(ops.MANIFEST))
            return names, manifest, tar

    def test_backup_is_private_bounded_and_excludes_secrets_by_default(self):
        self.populate()
        with mock.patch.object(ops, 'BACKUP_LOG_CAP', 1024):
            result = ops.create_backup(self.home)
        folder = self.home / 'backups' / 'console'
        archive = folder / result['name']
        self.assertEqual(0o700, stat.S_IMODE(folder.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(archive.stat().st_mode))
        self.assertRegex(result['name'], r'^hermes-home-\d{8}-\d{6}\.tar\.gz$')
        names, manifest, _ = self.members(archive)
        root = ops.ARCHIVE_ROOT + '/'
        for kept in ('config.yaml', 'memories/MEMORY.md', 'logs/gateway.log', 'state.db', 'skills/a/hermes-agent/SKILL.md'):
            self.assertIn(root + kept, names)
        for skipped in ('.env', 'auth.json', 'logs/huge.log', 'cache/models.json', 'installs/runtime.bin', 'link.txt',
                        'linkdir', 'gateway.sock', 'gateway.pid', 'gateway.lock', 'state.db-wal', 'state.db-shm',
                        'skills/a/__pycache__/x.pyc', 'backups'):
            self.assertNotIn(root + skipped, names, skipped)
        self.assertFalse(any('outside' in n for n in names))
        self.assertFalse(manifest['includes_secrets'])
        self.assertEqual(1, manifest['sqlite_snapshots'])
        self.assertEqual(2, manifest['skipped']['secret'])
        self.assertGreaterEqual(manifest['skipped']['symlinks'], 2)
        self.assertEqual(1, manifest['skipped']['large_log'])
        self.assertIn('--strip-components=1', manifest['restore'])
        with tarfile.open(archive) as tar:
            raw = tar.extractfile(root + 'state.db').read()
            self.assertNotIn(SECRET.encode(), b''.join(tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()))
        copy = Path(self.tmp.name) / 'copy.db'
        copy.write_bytes(raw)
        check = sqlite3.connect(copy)
        try:
            self.assertEqual([('committed',)], check.execute('SELECT v FROM t').fetchall())
        finally:
            check.close()
        listed = ops.list_backups(self.home)
        self.assertEqual([result['name']], [b['name'] for b in listed])
        self.assertFalse(listed[0]['includes_secrets'])
        self.assertEqual([], [p for p in folder.iterdir() if p.name.endswith('.partial')])

    def test_secrets_only_with_the_explicit_choice(self):
        self.populate()
        result = ops.create_backup(self.home, include_secrets=True)
        self.assertTrue(result['name'].endswith('-secrets.tar.gz'))
        names, manifest, _ = self.members(self.home / 'backups' / 'console' / result['name'])
        self.assertIn(ops.ARCHIVE_ROOT + '/.env', names)
        self.assertEqual(['.env', 'auth.json'], sorted(manifest['secret_files']))
        self.assertTrue(ops.list_backups(self.home)[0]['includes_secrets'])

    def test_retention_and_refused_folder_symlink(self):
        stamps = iter([1_800_000_000 + i for i in range(4)])
        for _ in range(4):
            ops.create_backup(self.home, keep=2, clock=lambda: next(stamps))
        names = [b['name'] for b in ops.list_backups(self.home)]
        self.assertEqual(2, len(names))
        self.assertTrue(names[0] > names[1])
        other = TemporaryDirectory()
        self.addCleanup(other.cleanup)
        home2 = Path(other.name) / 'home'
        home2.mkdir()
        (home2 / 'backups').symlink_to(Path(other.name))
        with self.assertRaises(AdminError):
            ops.create_backup(home2)

    def test_limits_abort_and_leave_no_partial_file(self):
        self.write('memories/a.md', 'x' * 100)
        with mock.patch.object(ops, 'BACKUP_MAX_INPUT_BYTES', 10):
            with self.assertRaises(AdminError) as raised:
                ops.create_backup(self.home)
        self.assertEqual(413, raised.exception.status)
        self.assertEqual([], list((self.home / 'backups' / 'console').iterdir()))

    def test_actions_create_list_and_delete_through_descriptors(self):
        self.write('memories/a.md', 'hello')
        self.assertTrue(self.act('create_backup', {'include_secrets': False, 'keep': 3})['ok'])
        job = self.wait_job()
        self.assertEqual('done', job['status'], job['message'])
        snapshot = self.snap()
        [item] = snapshot['backups']['items']
        delete = [a for a in snapshot['actions'] if a['id'] == 'delete_backup']
        self.assertEqual([item['name']], [a['target'] for a in delete])
        self.assertEqual([{'name': 'backup', 'type': 'hidden', 'value': item['name']}], delete[0]['fields'])
        self.assertIn(item['name'], snapshot['backups']['restore_command'])
        for bad in ('../config.yaml', 'hermes-home-20260101-000000.tar.gz', '/etc/passwd'):
            with self.assertRaises(AdminError):
                self.act('delete_backup', {'backup': bad})
        for values in ({'include_secrets': 'yes'}, {'keep': 0}, {'keep': 2.5}, {'keep': 99}, {'path': '/tmp'}):
            with self.assertRaises(AdminError):
                self.act('create_backup', values)
        self.act('delete_backup', {'backup': item['name']})
        self.assertEqual([], ops.list_backups(self.home))
        self.assertTrue((self.home / 'memories' / 'a.md').exists())

    def test_one_job_at_a_time(self):
        ops._JOBS[str(self.home)] = {'id': 'x', 'kind': 'backup', 'label': 'Creating backup', 'status': 'running',
                                     'progress': {}}
        self.assertNotIn('create_backup', [a['id'] for a in self.snap()['actions']])
        with self.assertRaises(AdminError):
            ops._start_job(self.home, 'backup', 'x', lambda progress: 'done')


class UpdateTests(Base):
    def test_check_updates_uses_git_with_timeouts_only_for_checkouts(self):
        self.runner.replies = {
            '@{upstream}': (0, 'origin/main\n', ''),
            'ls-remote': (0, 'c' * 40 + '\trefs/heads/main\n', ''),
            'merge-base': (1, '', ''),
        }
        result = ops.check_updates(self.src, self.env)
        self.assertEqual(('available', 'a' * 40, 'c' * 40), (result['status'], result['local'], result['remote']))
        ls = [argv for argv, _ in self.runner.calls if 'ls-remote' in argv][0]
        self.assertEqual(['/usr/bin/git', '-C', str(self.src), 'ls-remote', '--quiet', 'origin', 'refs/heads/main'], ls)
        self.assertTrue(all(t == ops.GIT_TIMEOUT for _, t in self.runner.calls))
        self.runner.replies['ls-remote'] = (0, 'a' * 40 + '\trefs/heads/main\n', '')
        self.assertEqual('current', ops.check_updates(self.src, self.env)['status'])
        self.runner.replies['ls-remote'] = (128, '', 'fatal: could not read from https://user:pw@example.com/x')
        result = ops.check_updates(self.src, self.env)
        self.assertEqual('unknown', result['status'])
        self.assertNotIn('pw@', result['message'])
        self.runner.replies['@{upstream}'] = (0, 'origin/-evil\n', '')
        self.assertEqual('unknown', ops.check_updates(self.src, self.env)['status'])

    def test_action_records_results_and_never_installs(self):
        self.runner.replies = {'ls-remote': (0, 'a' * 40 + '\tHEAD\n', '')}
        self.assertTrue(self.act('check_updates')['ok'])
        updates = self.snap()['updates']
        self.assertTrue(updates['checked_at'])
        self.assertTrue(any(r['id'] == 'hermes' for r in updates['results']))
        commands = ' '.join(argv[3] for argv, _ in self.runner.calls if len(argv) > 3)
        for verb in ('pull', 'fetch', 'checkout', 'reset', 'merge '):
            self.assertNotIn(verb, commands)
        self.assertTrue(any('pull --ff-only' in c['command'] for c in updates['commands']))


class DemoTests(unittest.TestCase):
    def setUp(self):
        ops._DEMO.clear()
        self.ctx = ConsoleContext(home=Path('/nonexistent/demo-home'), mode='demo',
                                  assistant_dir=Path('/nonexistent/demo-home/assistant-core'), environ={})
        boom = mock.Mock(side_effect=AssertionError('demo must not run commands or touch the network'))
        self.patches = [mock.patch.object(ops, 'RUNNER', boom), mock.patch.object(ops, 'HTTP_GET', boom),
                        mock.patch.object(ops, 'read_resources', boom), mock.patch.object(ops, 'create_backup', boom),
                        mock.patch.object(ops.subprocess, 'run', boom)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        ops._DEMO.clear()

    def act(self, action, values=None):
        snapshot = ops.snapshot(self.ctx, {})
        return ops.action(self.ctx, {'action': action, 'expected_revision': snapshot['revision'], 'confirm': True,
                                     'values': values or {}})

    def test_synthetic_snapshot_and_simulated_actions(self):
        snapshot = ops.snapshot(self.ctx, {})
        self.assertEqual('demo', snapshot['source'])
        self.assertTrue(snapshot['control']['enabled'])
        self.assertEqual(48, len(snapshot['history']['points']))
        self.act('gateway_stop', {'confirm_name': 'hermes-gateway.service'})
        self.assertEqual('stopped', ops.snapshot(self.ctx, {})['gateway']['state'])
        self.act('gateway_start')
        self.assertEqual('running', ops.snapshot(self.ctx, {})['gateway']['state'])
        self.act('create_backup', {'include_secrets': True, 'keep': 2})
        items = ops.snapshot(self.ctx, {})['backups']['items']
        self.assertEqual(2, len(items))
        self.assertTrue(items[0]['includes_secrets'])
        self.act('delete_backup', {'backup': items[1]['name']})
        self.assertEqual(1, len(ops.snapshot(self.ctx, {})['backups']['items']))
        self.act('check_updates')
        self.act('run_checks')
        self.assertTrue(ops.snapshot(self.ctx, {})['checks']['ran_at'])
        with self.assertRaises(AdminError):
            self.act('gateway_stop', {'confirm_name': 'wrong'})
        self.assertFalse(Path('/nonexistent').exists())


if __name__ == '__main__':
    unittest.main()
