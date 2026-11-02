"""Observe sections: read-only telemetry traces, turn grouping, analytics, pricing and demo data."""
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import yaml

from assistant_core.telemetry import SCHEMA
from hermes_zalo import admin_observe as observe
from hermes_zalo import console_sections
from hermes_zalo.admin_auth import AdminError
from hermes_zalo.console_sections import ConsoleContext

NOW = 1_793_430_000.0  # 2026-10-31, fixed so windows are deterministic


def api_row(**kw):
    row = dict(run_id='run-a', ts=NOW - 600, platform='telegram', session_id='sess-alpha', task_id=None, turn_id='turn-1',
               api_request_id='req-1', api_call_count=1, retry_count=0, provider='openrouter', model='model-a',
               response_model='model-a', duration_s=2.0, ttfb_s=0.5, finish_reason='stop', error_type=None,
               status_code=None, input_tokens=1000, output_tokens=200, cache_read_tokens=0, cache_write_tokens=0,
               reasoning_tokens=0, message_count=4, context_length=128000, tool_call_count=0)
    row.update(kw)
    return row


def tool_row(**kw):
    row = dict(run_id='run-a', ts=NOW - 590, platform='telegram', session_id='sess-alpha', task_id=None, turn_id='turn-1',
               api_request_id='req-1', tool_name='web_search', status='ok', error_type=None, duration_ms=300.0, blocked=0)
    row.update(kw)
    return row


class ObserveCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'assistant-core'
        self.data.mkdir()
        self.db_path = self.data / 'telemetry.sqlite3'
        self.ctx = ConsoleContext(home=self.root / 'home', mode='local', assistant_dir=self.data, environ={})
        clock = mock.patch.object(observe, '_now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, api=(), tools=(), runs=(('run-a', NOW - 86400, 'abc123', 'cfg-1', 'baseline'),), wal=False):
        with closing(sqlite3.connect(self.db_path)) as db:
            if wal:
                db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            for run in runs:
                db.execute('INSERT INTO runs VALUES (?, ?, ?, ?, ?)', run)
            for row in api:
                db.execute(f"INSERT INTO api_calls ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            for row in tools:
                db.execute(f"INSERT INTO tool_calls ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            db.commit()

    def activity(self, **query):
        return observe.activity_snapshot(self.ctx, {k: str(v) for k, v in query.items()})

    def analytics(self, **query):
        return observe.analytics_snapshot(self.ctx, {k: str(v) for k, v in query.items()})

    def pricing(self, text):
        (self.data / 'pricing.yaml').write_text(text, encoding='utf-8')


class AvailabilityTests(ObserveCase):
    def test_sections_are_registered_with_their_filters(self):
        self.assertEqual({'days', 'platform', 'model', 'status', 'session', 'page', 'turn'},
                         set(console_sections.get('activity').query_keys))
        self.assertEqual({'days'}, set(console_sections.get('analytics').query_keys))

    def test_missing_database_is_unavailable_with_a_hint_and_nothing_is_created(self):
        for snapshot in (self.activity(), self.analytics()):
            self.assertFalse(snapshot['available'])
            self.assertIn('ASSISTANT_TELEMETRY=true', snapshot['message'])
            self.assertNotIn(str(self.root), snapshot['message'])
        self.assertEqual([], list(self.data.iterdir()))

    def test_symlinked_locations_are_refused(self):
        self.write([api_row()])
        link = self.root / 'linked'
        link.symlink_to(self.data, target_is_directory=True)
        linked = ConsoleContext(home=self.root, mode='local', assistant_dir=link)
        snapshot = observe.activity_snapshot(linked, {})
        self.assertFalse(snapshot['available'])
        self.assertIn('symbolic link', snapshot['message'])
        other = self.root / 'other'
        other.mkdir()
        (other / 'telemetry.sqlite3').symlink_to(self.db_path)
        snapshot = observe.analytics_snapshot(ConsoleContext(home=self.root, mode='local', assistant_dir=other), {})
        self.assertFalse(snapshot['available'])

    def test_unsupported_files_are_unavailable(self):
        self.db_path.write_bytes(b'not a database')
        self.assertFalse(self.activity()['available'])
        self.db_path.unlink()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute('CREATE TABLE other (x)')
        self.assertFalse(self.analytics()['available'])

    def test_reads_never_write_or_create_side_files(self):
        for wal in (False, True):
            with self.subTest(wal=wal):
                for path in self.data.iterdir():
                    path.unlink()
                self.write([api_row(), api_row(ts=NOW - 100, turn_id='turn-2')], [tool_row()], wal=wal)
                before = (self.db_path.read_bytes(), sorted(p.name for p in self.data.iterdir()))
                self.activity()
                self.activity(turn='a1')
                self.analytics(days=90)
                self.assertEqual(before, (self.db_path.read_bytes(), sorted(p.name for p in self.data.iterdir())))

    def test_schema_additions_and_missing_optional_columns_are_tolerated(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript('CREATE TABLE api_calls (id INTEGER PRIMARY KEY, ts REAL NOT NULL, session_id TEXT, '
                             'turn_id TEXT, model TEXT, duration_s REAL, error_type TEXT, input_tokens INTEGER, '
                             'output_tokens INTEGER, future_column TEXT);'
                             'CREATE TABLE config_snapshots (id INTEGER PRIMARY KEY, body TEXT);')
            db.execute("INSERT INTO api_calls (ts, session_id, turn_id, model, duration_s, input_tokens, output_tokens, "
                       "future_column) VALUES (?, 's', 't', 'model-a', 1.5, 10, 5, 'x')", (NOW - 60,))
            db.commit()
        snapshot = self.activity()
        self.assertTrue(snapshot['available'])
        self.assertEqual(1, snapshot['total'])
        self.assertIsNone(snapshot['items'][0]['cache_read_tokens'])
        self.assertNotIn('future_column', repr(snapshot))
        analytics = self.analytics()
        self.assertEqual(0, analytics['kpis']['tool_calls'])
        self.assertIsNone(analytics['kpis']['ttfb_p50_s'])
        self.assertEqual([], analytics['runs'])


class ActivityTests(ObserveCase):
    def scenario(self):
        self.write(
            api=[
                # turn-1: tool step then answer
                api_row(ts=NOW - 600, finish_reason='tool_calls', tool_call_count=1),
                api_row(ts=NOW - 595, api_request_id='req-2', api_call_count=2, model='model-b',
                        input_tokens=500, output_tokens=100, cache_read_tokens=1500),
                # turn-2 on zalo: rate limited, retried, recovered; blocked tool
                api_row(ts=NOW - 400, platform='zalo', session_id='sess-beta', turn_id='turn-2', api_request_id='r2',
                        duration_s=0.2, ttfb_s=None, error_type='RateLimitError', status_code=429,
                        input_tokens=None, output_tokens=None, finish_reason=None),
                api_row(ts=NOW - 398, platform='zalo', session_id='sess-beta', turn_id='turn-2', api_request_id='r2',
                        retry_count=1),
                # turn-3: failed (final attempt errored)
                api_row(ts=NOW - 300, session_id='sess-alpha', turn_id='turn-3', api_request_id='r3',
                        error_type='APITimeoutError', input_tokens=None, output_tokens=None),
                # legacy rows without turn ids: two bursts 10 minutes apart -> two turns
                api_row(ts=NOW - 2000, platform='web', session_id='legacy', turn_id=None, api_request_id=None),
                api_row(ts=NOW - 1990, platform='web', session_id='legacy', turn_id=None, api_request_id=None),
                api_row(ts=NOW - 1390, platform='web', session_id='legacy', turn_id=None, api_request_id=None),
                # outside the 1-day window
                api_row(ts=NOW - 3 * 86400, session_id='old', turn_id='old-turn'),
            ],
            tools=[
                tool_row(ts=NOW - 597.5, turn_id=None, duration_ms=500.0),  # joins turn-1 through req-1
                tool_row(ts=NOW - 397, platform='zalo', session_id='sess-beta', turn_id='turn-2', api_request_id='r2',
                         tool_name='terminal', status='blocked', blocked=1, duration_ms=1.0),
            ])

    def test_turns_group_by_turn_id_request_and_time_gap(self):
        self.scenario()
        snapshot = self.activity(days=1)
        self.assertTrue(snapshot['available'])
        self.assertEqual('list', snapshot['view'])
        self.assertEqual(5, snapshot['total'])
        turns = {(t['session_id'], t['started_at']): t for t in snapshot['items']}
        first = next(t for t in snapshot['items'] if t['session_id'] == 'sess-alpha' and t['api_calls'] == 2)
        self.assertEqual(['model-a', 'model-b'], first['models'])
        self.assertEqual((1, 'ok', 'turn_id'), (first['tool_calls'], first['status'], first['grouping']))
        self.assertEqual(1500, first['input_tokens'])
        self.assertEqual(7.0, first['duration_s'])  # 602 s before NOW to 595 s before NOW
        legacy = [t for t in snapshot['items'] if t['session_id'] == 'legacy']
        self.assertEqual([1, 2], sorted(t['api_calls'] for t in legacy))
        self.assertEqual({'time_gap'}, {t['grouping'] for t in legacy})
        self.assertEqual(len(turns), 5)
        # newest first
        starts = [t['start_ts'] for t in snapshot['items']]
        self.assertEqual(sorted(starts, reverse=True), starts)

    def test_status_retries_and_blocked_tools(self):
        self.scenario()
        items = self.activity(days=1)['items']
        beta = next(t for t in items if t['session_id'] == 'sess-beta')
        self.assertEqual(('issues', 1, 1, 1), (beta['status'], beta['retries'], beta['api_errors'], beta['blocked']))
        self.assertIn('RateLimitError', beta['error_types'])
        failed = next(t for t in items if t['status'] == 'failed')
        self.assertEqual(['APITimeoutError'], failed['error_types'])

    def test_filters_and_paging(self):
        self.scenario()
        self.assertEqual(6, self.activity(days=7)['total'])
        self.assertEqual(['zalo'], [t['platform'] for t in self.activity(days=1, platform='zalo')['items']])
        self.assertEqual(1, self.activity(days=1, model='model-b')['total'])
        self.assertEqual({'ok'}, {t['status'] for t in self.activity(days=1, status='ok')['items']})
        self.assertEqual({'issues', 'failed'}, {t['status'] for t in self.activity(days=1, status='error')['items']})
        self.assertEqual({'legacy'}, {t['session_id'] for t in self.activity(days=1, session='leg')['items']})
        facets = self.activity(days=1, platform='zalo')['facets']
        self.assertEqual(['telegram', 'web', 'zalo'], facets['platforms'])
        with mock.patch.object(observe, 'PAGE_SIZE', 2):
            page = self.activity(days=1, page=3)
            self.assertEqual((3, 1, 5, 2), (page['page'], len(page['items']), page['total'], page['limit']))
            self.assertEqual(3, self.activity(days=1, page=99)['page'])
        for bad in ({'days': '2'}, {'status': 'weird'}, {'session': 'a b'}, {'page': '0'}, {'turn': 'x1'},
                    {'platform': '<x>'}, {'session': 'x' * 65}):
            with self.subTest(bad=bad), self.assertRaises(AdminError):
                observe.activity_snapshot(self.ctx, bad)

    def test_turn_detail_interleaves_steps(self):
        self.scenario()
        first = next(t for t in self.activity(days=1)['items'] if t['session_id'] == 'sess-alpha' and t['api_calls'] == 2)
        detail = self.activity(turn=first['id'])
        self.assertEqual('detail', detail['view'])
        steps = detail['items']
        self.assertEqual(['api', 'tool', 'api'], [s['kind'] for s in steps])
        self.assertEqual([0.0, 4000.0, 5000.0], [s['offset_ms'] for s in steps])
        self.assertEqual((2000.0, 500.0), (steps[0]['duration_ms'], steps[0]['ttfb_ms']))
        self.assertEqual(('web_search', 0), (steps[1]['tool_name'], steps[1]['requested_by']))
        self.assertEqual(7000.0, detail['turn']['span_ms'])
        self.assertEqual([{'run_id': 'run-a', 'started_at': observe._iso(NOW - 86400), 'hermes_rev': 'abc123',
                           'config_hash': 'cfg-1', 'label': 'baseline'}], detail['turn']['runs'])
        self.assertEqual(3000, detail['turn']['prompt_tokens'])
        legacy = min((t for t in self.activity(days=1)['items'] if t['session_id'] == 'legacy'),
                     key=lambda t: t['start_ts'])
        self.assertEqual(2, len(self.activity(turn=legacy['id'])['items']))
        missing = self.activity(turn='a999')
        self.assertIsNone(missing['turn'])
        self.assertIn('no longer', missing['message'])

    def test_untrusted_values_are_reduced_to_safe_text(self):
        self.write([api_row(model='<script>alert(1)</script>', session_id='s"><img src=x>', error_type='/home/owner/.env',
                            platform='tele\ngram', finish_reason='stop; rm -rf')], [])
        item = self.activity(days=1)['items'][0]
        self.assertEqual(['scriptalert1/script'], item['models'])
        self.assertEqual('simgsrcx', item['session_id'])
        self.assertEqual(['[hidden]'], item['error_types'])
        self.assertEqual('telegram', item['platform'])
        self.assertNotIn('/home/owner', repr(self.activity(turn=item['id'])))

    def test_row_cap_drops_incomplete_history_and_says_so(self):
        self.write([api_row(ts=NOW - 100 * i, turn_id=f't{i}') for i in range(1, 11)])
        with mock.patch.object(observe, 'ROW_LIMIT', 4):
            snapshot = self.activity(days=1)
            analytics = self.analytics(days=1)
        self.assertTrue(snapshot['truncated'])
        self.assertEqual(4, snapshot['total'])
        self.assertEqual(observe._iso(NOW - 400), snapshot['covered_since'])
        self.assertTrue(analytics['truncated'])
        self.assertEqual(4, analytics['kpis']['api_calls'])


class AnalyticsTests(ObserveCase):
    def scenario(self):
        api = [api_row(ts=NOW - 1000 - i, turn_id=f't{i}', api_request_id=f'r{i}', duration_s=float(i + 1),
                       ttfb_s=0.1 * (i + 1), model='model-a' if i < 3 else 'model-b',
                       platform='telegram' if i % 2 else 'zalo', input_tokens=1000, output_tokens=100,
                       cache_read_tokens=1000 if i == 0 else 0)
               for i in range(5)]
        api.append(api_row(ts=NOW - 900, turn_id='t9', api_request_id='r9', error_type='RateLimitError', duration_s=0.1,
                           input_tokens=None, output_tokens=None, model='model-b'))
        api.append(api_row(ts=NOW - 2 * 86400, turn_id='older', run_id='run-b'))
        tools = [tool_row(ts=NOW - 990, turn_id='t0', tool_name='web_search', duration_ms=100.0),
                 tool_row(ts=NOW - 989, turn_id='t0', tool_name='web_search', status='error',
                          error_type='TimeoutError', duration_ms=300.0),
                 tool_row(ts=NOW - 988, turn_id='t1', tool_name='terminal', status='blocked', blocked=1,
                          duration_ms=1.0)]
        self.write(api, tools, runs=[('run-a', NOW - 86400, 'abc', 'cfg-a', 'baseline'),
                                     ('run-b', NOW - 5 * 86400, 'abc', 'cfg-b', 'router')])

    def test_kpis_percentiles_and_tables(self):
        self.scenario()
        snapshot = self.analytics(days=1)
        k = snapshot['kpis']
        self.assertEqual((6, 6, 1), (k['turns'], k['api_calls'], k['errors']))
        self.assertAlmostEqual(1 / 6, k['error_rate'], places=3)
        self.assertEqual((3.0, 4.8), (k['latency_p50_s'], k['latency_p95_s']))  # successful calls 1..5 s
        self.assertEqual(0.3, k['ttfb_p50_s'])
        self.assertEqual((5000, 500, 1000), (k['input_tokens'], k['output_tokens'], k['cache_read_tokens']))
        self.assertEqual(6500, k['total_tokens'])
        self.assertAlmostEqual(1000 / 6000, k['cache_hit_rate'], places=3)
        self.assertEqual((3, 1, 1), (k['tool_calls'], k['tool_failures'], k['blocked_tools']))
        self.assertIsNone(k['cost_usd'])
        self.assertEqual('missing', snapshot['pricing']['status'])
        models = {m['model']: m for m in snapshot['models']}
        self.assertEqual((3, 0, 2.0), (models['model-a']['api_calls'], models['model-a']['errors'],
                                       models['model-a']['latency_p50_s']))
        self.assertEqual(0.3333, models['model-b']['error_rate'])
        tools = {t['tool']: t for t in snapshot['tools']}
        self.assertEqual((2, 1, 0.5, 0, 200.0), (tools['web_search']['calls'], tools['web_search']['failures'],
                                                 tools['web_search']['failure_rate'], tools['web_search']['blocked'],
                                                 tools['web_search']['duration_avg_ms']))
        self.assertEqual(1, tools['terminal']['blocked'])
        self.assertEqual({'telegram', 'zalo'}, {p['platform'] for p in snapshot['platforms']})
        self.assertEqual(['baseline'], [r['label'] for r in snapshot['runs']])
        self.assertEqual(2, len(snapshot['daily']))
        self.assertEqual(6, sum(d['api_calls'] for d in snapshot['daily']))
        self.assertEqual({'router', 'baseline'}, {r['label'] for r in self.analytics(days=7)['runs']})
        with self.assertRaises(AdminError):
            self.analytics(days=3)

    def test_pricing_estimates_cost_and_reports_partial_or_invalid_files(self):
        self.scenario()
        self.pricing(yaml.safe_dump({'models': {'model-a': {'input': 1.0, 'output': 10.0, 'cache_read': 0.1}}}))
        snapshot = self.analytics(days=1)
        # model-a: 3 calls x (1000 in x $1 + 100 out x $10) / 1M, plus 1000 cache-read tokens x $0.1 / 1M
        expected = (3 * (1000 * 1.0 + 100 * 10.0) + 1000 * 0.1) / 1_000_000
        self.assertAlmostEqual(expected, snapshot['kpis']['cost_usd'], places=9)
        self.assertTrue(snapshot['kpis']['cost_partial'])
        self.assertEqual('partial', snapshot['pricing']['status'])
        self.assertEqual(['model-b'], snapshot['pricing']['unpriced_models'])
        models = {m['model']: m for m in snapshot['models']}
        self.assertIsNone(models['model-b']['cost_usd'])
        self.pricing('models:\n  openrouter/model-a: {input: 1, output: 10}\n  model-b: {input: 2, output: 4}\n'
                     '  broken: {input: -1, output: 2}\n')
        snapshot = self.analytics(days=1)
        self.assertEqual('ok', snapshot['pricing']['status'])
        self.assertEqual(1, snapshot['pricing']['skipped_entries'])
        self.assertFalse(snapshot['kpis']['cost_partial'])
        self.pricing('models: [1, 2]')
        snapshot = self.analytics(days=1)
        self.assertEqual('invalid', snapshot['pricing']['status'])
        self.assertIsNone(snapshot['kpis']['cost_usd'])
        (self.data / 'pricing.yaml').unlink()
        target = self.root / 'prices.yaml'
        target.write_text('models: {model-a: {input: 1, output: 1}}')
        (self.data / 'pricing.yaml').symlink_to(target)
        self.assertEqual('invalid', self.analytics(days=1)['pricing']['status'])

    def test_example_pricing_file_is_valid(self):
        example = Path(__file__).resolve().parents[1] / 'config' / 'pricing.example.yaml'
        self.pricing(example.read_text(encoding='utf-8'))
        pricing = observe.load_pricing(self.ctx)
        self.assertEqual('ok', pricing.status)
        self.assertEqual(0, pricing.skipped)
        self.assertGreaterEqual(len(pricing.rates), 3)

    def test_percentile(self):
        self.assertIsNone(observe.percentile([], 50))
        self.assertEqual(5, observe.percentile([5], 95))
        self.assertEqual(2.5, observe.percentile([4, 1, None, 3, 2], 50))
        self.assertAlmostEqual(3.85, observe.percentile([1, 2, 3, 4], 95))


class DemoTests(unittest.TestCase):
    def test_demo_is_synthetic_deterministic_and_never_touches_disk(self):
        ctx = ConsoleContext(home=Path('/nonexistent-home'), mode='demo', assistant_dir=Path('/nonexistent-data'))
        with mock.patch.object(observe, '_connect_ro', side_effect=AssertionError('disk read')), \
                mock.patch.object(observe, '_read_bounded', side_effect=AssertionError('disk read')):
            first = observe.activity_snapshot(ctx, {'days': '1'})
            again = observe.activity_snapshot(ctx, {'days': '1'})
            analytics = observe.analytics_snapshot(ctx, {'days': '30'})
            detail = observe.activity_snapshot(ctx, {'turn': first['items'][0]['id']})
        strip = lambda s: {k: v for k, v in s.items() if k != 'updated_at'}
        self.assertEqual(strip(first), strip(again))
        self.assertEqual('demo', first['source'])
        statuses = {t['status'] for t in first['items']}
        self.assertEqual({'ok', 'issues', 'failed'}, statuses)
        self.assertTrue(any(t['retries'] for t in first['items']))
        self.assertTrue(any(t['blocked'] for t in first['items']))
        self.assertTrue(any(t['grouping'] == 'time_gap' for t in first['items']))
        self.assertTrue(any(t['api_calls'] >= 3 and t['tool_calls'] >= 2 for t in first['items']))
        self.assertGreaterEqual(len(analytics['models']), 3)
        self.assertTrue({'telegram', 'zalo'} <= {p['platform'] for p in analytics['platforms']})
        self.assertIsNotNone(analytics['kpis']['cost_usd'])
        self.assertEqual({'baseline', 'router-v1'}, {r['label'] for r in analytics['runs']})
        self.assertTrue(detail['items'])
        self.assertFalse(os.path.exists('/nonexistent-data'))


if __name__ == '__main__':
    unittest.main()
