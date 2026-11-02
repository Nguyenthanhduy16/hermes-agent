"""Experiments and Benchmarks console sections: demo isolation, telemetry comparison, background runs."""
import json
from pathlib import Path
import tempfile
import time
import unittest

import yaml

from assistant_core import bench
from assistant_core.telemetry import TelemetryStore
from hermes_zalo import admin_bench, console_sections
from hermes_zalo.admin_auth import AdminError
from hermes_zalo.console_sections import ConsoleContext

from tests.test_assistant_bench import SUITE, FakeHermes


def envelope(action, revision, values):
    return {'action': action, 'expected_revision': revision, 'confirm': True, 'values': values}


def action_by(snapshot, action_id, target=None):
    return next(a for a in snapshot['actions'] if a['id'] == action_id and a.get('target') == target)


class RegistrationTests(unittest.TestCase):
    def test_sections_are_registered(self):
        experiments, benchmarks = console_sections.get('experiments'), console_sections.get('benchmarks')
        self.assertIsNone(experiments.action)
        self.assertEqual({'compare'}, set(experiments.query_keys))
        self.assertEqual({'compare', 'run'}, set(benchmarks.query_keys))


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'never-created'
        self.ctx = ConsoleContext(home=self.home, mode='demo', assistant_dir=self.home / 'assistant-core',
                                  environ={})
        admin_bench._DEMO.reset()

    def tearDown(self):
        self.assertFalse(self.home.exists(), 'demo mode must not touch disk')
        self.temp.cleanup()

    def test_demo_experiments_compare_runs_and_labels(self):
        data = admin_bench.experiments_snapshot(self.ctx, {})
        self.assertEqual('demo', data['source'])
        self.assertTrue(data['items'])
        data = admin_bench.experiments_snapshot(self.ctx, {'compare': 'label:B1-cheap,label:B3-strong'})
        rows = {row['key']: row['values'] for row in data['compare']['rows']}
        self.assertEqual([1, 2], rows['runs'])
        keys = {row['key'] for row in data['compare']['settings']['rows']}
        self.assertIn('model.default', keys)
        with self.assertRaises(AdminError):
            admin_bench.experiments_snapshot(self.ctx, {'compare': 'label:nope,label:B1-cheap'})
        with self.assertRaises(AdminError):
            admin_bench.experiments_snapshot(self.ctx, {'compare': 'only-one'})

    def test_demo_run_lifecycle_is_simulated_in_memory(self):
        data = admin_bench.benchmarks_snapshot(self.ctx, {})
        self.assertIsNone(data['active'])
        start = action_by(data, 'start_run')
        self.assertIn('costs money', start['description'])
        with self.assertRaises(AdminError):
            admin_bench.benchmarks_action(self.ctx, envelope('start_run', 'stale', dict(
                suite='tool-use', label='x', repeats=1, concurrency=1)))
        result = admin_bench.benchmarks_action(self.ctx, envelope('start_run', start['revision'], dict(
            suite='tool-use', label='B4 demo', repeats=1, concurrency='2')))
        self.assertTrue(result['ok'])
        data = admin_bench.benchmarks_snapshot(self.ctx, {})
        self.assertEqual('running', data['active']['status'])
        self.assertFalse([a for a in data['actions'] if a['id'] == 'start_run'])
        cancel = action_by(data, 'cancel_run', data['active']['id'])
        self.assertTrue(admin_bench.benchmarks_action(self.ctx, envelope('cancel_run', cancel['revision'],
                                                                        {'run_id': cancel['target']}))['ok'])
        data = admin_bench.benchmarks_snapshot(self.ctx, {'run': cancel['target']})
        self.assertEqual('cancelled', data['run']['status'])
        ids = [item['id'] for item in data['items'] if item['status'] == 'done'][:2]
        data = admin_bench.benchmarks_snapshot(self.ctx, {'compare': ','.join(ids)})
        self.assertEqual(len(ids), len(data['compare']['columns']))
        delete = action_by(data, 'delete_run', cancel['target'])
        admin_bench.benchmarks_action(self.ctx, envelope('delete_run', delete['revision'], {'run_id': cancel['target']}))
        self.assertNotIn(cancel['target'], [i['id'] for i in admin_bench.benchmarks_snapshot(self.ctx, {})['items']])


class LocalExperimentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.ctx = ConsoleContext(home=self.dir / 'home', mode='local', assistant_dir=self.dir / 'ac', environ={})

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_telemetry_is_unavailable_not_created(self):
        data = admin_bench.experiments_snapshot(self.ctx, {})
        self.assertFalse(data['available'])
        self.assertFalse((self.dir / 'ac' / 'telemetry.sqlite3').exists())

    def test_runs_compare_with_settings_diff_and_pricing(self):
        db = self.dir / 'ac' / 'telemetry.sqlite3'
        for run_id, label, config, model in (('r1', 'B1', 'h1', 'cheap'), ('r2', 'B2', 'h2', 'strong'),
                                             ('r3', 'B2', 'h2', 'strong'), ('r4', '', 'h0', 'old')):
            store = TelemetryStore(db, run_id=run_id, config_hash=config, label=label)
            if config != 'h0':
                store.save_snapshot(config, {'model': {'default': model}, 'agent': {'max_turns': 30}})
            store.post_api_request(api_request_id=run_id, session_id='s', turn_id='t-' + run_id, model=model,
                                   api_duration=1.0, usage={'input_tokens': 100, 'output_tokens': 10})
            store.close()
        (self.dir / 'ac' / 'pricing.yaml').write_text('cheap: {input: 1, output: 1}\n')
        data = admin_bench.experiments_snapshot(self.ctx, {})
        self.assertEqual({'r1', 'r2', 'r3', 'r4'}, {item['id'] for item in data['items']})
        self.assertFalse(next(i for i in data['items'] if i['id'] == 'r4')['has_snapshot'])
        self.assertEqual({'B1': 1, 'B2': 2}, {l['label']: l['runs'] for l in data['labels']})
        self.assertTrue(data['pricing'])
        data = admin_bench.experiments_snapshot(self.ctx, {'compare': 'r1,label:B2,r4'})
        rows = {row['key']: row['values'] for row in data['compare']['rows']}
        self.assertEqual([1, 2, 1], rows['turns'])
        self.assertAlmostEqual(110 / 1e6, rows['cost_usd'][0])
        self.assertIsNone(rows['cost_usd'][1])
        settings = data['compare']['settings']
        self.assertEqual([{'key': 'model.default', 'values': ['cheap', 'strong', '—']}],
                         [r for r in settings['rows'] if r['key'] == 'model.default'])
        self.assertEqual([2], settings['missing'])
        with self.assertRaises(AdminError):
            admin_bench.experiments_snapshot(self.ctx, {'compare': 'r1,unknown'})


class LocalBenchmarksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        bundled = self.dir / 'bundled'
        bundled.mkdir()
        (bundled / 'unit.yaml').write_text(yaml.safe_dump(SUITE))
        self.home = self.dir / 'home'
        self.home.mkdir()
        (self.home / 'config.yaml').write_text('model:\n  default: m-one\n  api_key: sk-CONSOLESECRET0123456789\n')
        self.fake = FakeHermes()
        self.environ = dict(self.fake.environ, HERMES_BENCH_BUNDLED_DIR=str(bundled))
        self.ctx = ConsoleContext(home=self.home, mode='local', assistant_dir=self.dir / 'ac', environ=self.environ)

    def tearDown(self):
        self.fake.close()
        admin_bench.close_stores()
        self.temp.cleanup()

    def wait_idle(self):
        deadline = time.time() + 15
        while time.time() < deadline:
            data = admin_bench.benchmarks_snapshot(self.ctx, {})
            if data['active'] is None:
                return data
            time.sleep(0.05)
        self.fail('benchmark run did not finish')

    def test_unconfigured_target_hides_start_and_explains_setup(self):
        ctx = ConsoleContext(home=self.home, mode='local', assistant_dir=self.dir / 'ac',
                             environ={'HERMES_BENCH_BUNDLED_DIR': self.environ['HERMES_BENCH_BUNDLED_DIR']})
        data = admin_bench.benchmarks_snapshot(ctx, {})
        self.assertFalse(data['configured'])
        self.assertEqual([], [a for a in data['actions'] if a['id'] == 'start_run'])
        self.assertTrue(data['setup'])
        self.assertEqual(['unit'], [s['name'] for s in data['suites']])
        with self.assertRaises(AdminError):
            admin_bench.benchmarks_action(ctx, envelope('start_run', data['revision'], dict(
                suite='unit', label='', repeats=1, concurrency=1)))

    def test_start_run_in_background_then_compare_and_delete(self):
        data = admin_bench.benchmarks_snapshot(self.ctx, {})
        self.assertEqual('127.0.0.1', data['target']['host'].split(':')[0])
        start = action_by(data, 'start_run')
        for values in (dict(suite='unit', label='B1', repeats=9, concurrency=1),
                       dict(suite='unit', label='B1', repeats=1, concurrency=7),
                       dict(suite='missing', label='B1', repeats=1, concurrency=1),
                       dict(suite='unit', label='B1', repeats=1)):
            with self.assertRaises(AdminError):
                admin_bench.benchmarks_action(self.ctx, envelope('start_run', start['revision'], values))
        result = admin_bench.benchmarks_action(self.ctx, envelope('start_run', start['revision'], dict(
            suite='unit', label='B1', repeats=1, concurrency=2)))
        self.assertTrue(result['ok'])
        first = result['run_id']
        data = self.wait_idle()
        run = next(item for item in data['items'] if item['id'] == first)
        self.assertEqual(('done', 3, 2), (run['status'], run['completed'], run['passed']))
        with self.assertRaises(AdminError):  # revision changed: the old descriptor is stale
            admin_bench.benchmarks_action(self.ctx, envelope('start_run', 'old', dict(
                suite='unit', label='B2', repeats=1, concurrency=1)))
        start = action_by(data, 'start_run')
        second = admin_bench.benchmarks_action(self.ctx, envelope('start_run', start['revision'], dict(
            suite='unit', label='B2', repeats=1, concurrency=1)))['run_id']
        data = self.wait_idle()
        detail = admin_bench.benchmarks_snapshot(self.ctx, {'run': first})['run']
        self.assertEqual(3, len(detail['results']))
        self.assertEqual('m-one', dict((s['key'], s['value']) for s in detail['settings'])['model.default'])
        failed = next(r for r in detail['results'] if r['task_id'] == 'fail-task')
        self.assertEqual('http_500', failed['error'])
        compare = admin_bench.benchmarks_snapshot(self.ctx, {'compare': f'{first},{second}'})['compare']
        self.assertTrue(compare['same_suite'])
        self.assertEqual(['B1', 'B2'], [c['label'] for c in compare['columns']])
        payload = json.dumps(admin_bench.benchmarks_snapshot(self.ctx, {'run': first, 'compare': f'{first},{second}'}))
        self.assertNotIn('test-key', payload)
        self.assertNotIn('CONSOLESECRET', payload)
        delete = action_by(data, 'delete_run', first)
        with self.assertRaises(AdminError):
            admin_bench.benchmarks_action(self.ctx, envelope('delete_run', first + ':running', {'run_id': first}))
        admin_bench.benchmarks_action(self.ctx, envelope('delete_run', delete['revision'], {'run_id': first}))
        with self.assertRaises(AdminError):
            admin_bench.benchmarks_snapshot(self.ctx, {'run': first})
        with self.assertRaises(AdminError):
            admin_bench.benchmarks_snapshot(self.ctx, {'run': '../etc'})

    def test_cancel_running_run_and_restart_recovery(self):
        self.fake.hold = True
        data = admin_bench.benchmarks_snapshot(self.ctx, {})
        run_id = admin_bench.benchmarks_action(self.ctx, envelope('start_run', action_by(data, 'start_run')['revision'],
                                                                  dict(suite='unit', label='', repeats=1, concurrency=1)))['run_id']
        data = admin_bench.benchmarks_snapshot(self.ctx, {})
        self.assertEqual(run_id, data['active']['id'])
        cancel = action_by(data, 'cancel_run', run_id)
        admin_bench.benchmarks_action(self.ctx, envelope('cancel_run', cancel['revision'], {'run_id': run_id}))
        self.fake.gate.set()
        data = self.wait_idle()
        self.assertEqual('cancelled', next(i for i in data['items'] if i['id'] == run_id)['status'])
        # A run left "running" by a previous console process is failed on the next load.
        store = admin_bench._store(self.ctx, bench)
        store.db.execute("UPDATE bench_runs SET status='running', owner='elsewhere:1:x', heartbeat_at=? WHERE id=?",
                         (time.time() - 7200, run_id))
        data = admin_bench.benchmarks_snapshot(self.ctx, {})
        self.assertIsNone(data['active'])
        self.assertEqual('failed', next(i for i in data['items'] if i['id'] == run_id)['status'])


if __name__ == '__main__':
    unittest.main()
