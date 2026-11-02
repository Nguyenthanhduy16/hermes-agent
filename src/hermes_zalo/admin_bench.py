"""Experiments and Benchmarks console sections.

- ``experiments`` (read-only): gateway runs from assistant-core telemetry, each
  tied to the secret-free settings snapshot of the config it started with;
  2-4 runs or labels side by side with a settings diff.
- ``benchmarks``: repeatable task suites (``assistant_core.bench``) sent to the
  configured Hermes API server from a background thread of this process; one
  active run at a time, cancellable, results in ``<assistant_dir>/bench.sqlite3``.

Demo mode uses synthetic data held in memory and never touches disk or network.
Every string from files, databases and model replies is untrusted: the browser
renders it as text only.
"""
from datetime import datetime, timezone
import hashlib
import json
import random
import threading
import time
import zlib
from pathlib import Path

from .admin_auth import AdminError
from .console_sections import Section, register, validate_action


LEVELS = ('L1', 'L2', 'L3')
MAX_COMPARE = 4
SETUP_STEPS = (
    'Run the Hermes API server (API_SERVER_ENABLED=true) on the bot host.',
    'Set HERMES_BENCH_API_URL (or HERMES_CONSOLE_CHAT_API_URL), for example http://127.0.0.1:8642/v1, '
    'in the console environment. Use https:// for any non-local host.',
    'Set API_SERVER_KEY (or HERMES_BENCH_API_KEY) to the API server key, then restart the console.',
)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _revision(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _modules():
    """assistant_core may be absent in a sealed plugin layout; the pages then say so."""
    try:
        from assistant_core import bench, reporting
        from assistant_core.telemetry import flatten_snapshot
    except ImportError:
        return None
    return bench, reporting, flatten_snapshot


def _unavailable(section, message):
    return dict(section=section, available=False, items=[], message=message, updated_at=now_iso())


def _safe_dir(ctx):
    directory = Path(ctx.assistant_dir)
    if directory.is_symlink():
        raise AdminError('The assistant data directory must not be a symbolic link.', 503)
    return directory


def _compare_tokens(raw, *, allow_labels):
    tokens = [token.strip() for token in (raw or '').split(',') if token.strip()]
    if not tokens:
        return []
    if not 2 <= len(tokens) <= MAX_COMPARE or len(set(tokens)) != len(tokens):
        raise AdminError('Choose 2 to 4 different items to compare.')
    for token in tokens:
        if len(token) > 70 or (token.startswith('label:') and not allow_labels):
            raise AdminError('Unknown comparison item.')
    return tokens


def _diff(columns_flat):
    """Rows of settings that differ between columns, plus how many keys are identical."""
    keys = sorted(set().union(*[set(flat) for flat in columns_flat if flat is not None])) if columns_flat else []
    rows, same = [], 0
    for key in keys[:400]:
        values = [None if flat is None else flat.get(key) for flat in columns_flat]
        if len({json.dumps(v, sort_keys=True, default=str) for v in values}) == 1:
            same += 1
            continue
        rows.append(dict(key=key, values=['—' if v is None else str(v)[:160] for v in values]))
    return dict(rows=rows, same=same, missing=[i for i, flat in enumerate(columns_flat) if flat is None])


def _merge_flat(flats):
    """One column from several snapshots (a label spanning configs): differing values read 'mixed'."""
    flats = [flat for flat in flats if flat is not None]
    if not flats:
        return None
    merged = {}
    for key in set().union(*flats):
        values = {json.dumps(flat.get(key), sort_keys=True, default=str) for flat in flats}
        merged[key] = flats[0].get(key) if len(values) == 1 else 'mixed'
    return merged


# ---- Experiments ----------------------------------------------------------------

EXPERIMENT_METRICS = (
    ('runs', 'Gateway runs', 'number'), ('turns', 'Turns', 'number'), ('api_calls', 'API calls', 'number'),
    ('api_calls_per_turn', 'API calls per turn', 'decimal'), ('tokens_per_turn', 'Tokens per turn', 'number'),
    ('input_tokens', 'Input tokens', 'number'), ('output_tokens', 'Output tokens', 'number'),
    ('p50_latency_s', 'p50 call latency', 'seconds'), ('p95_latency_s', 'p95 call latency', 'seconds'),
    ('error_rate', 'API error rate', 'percent'), ('tool_calls', 'Tool calls', 'number'),
    ('tool_failure_rate', 'Tool failure rate', 'percent'), ('cost_usd', 'Estimated cost', 'money'),
)


def _experiment_item(row):
    return dict(id=row['run_id'], label=row.get('label') or '', started_at=row.get('started_at'),
                hermes_rev=row.get('hermes_rev') or '', config_hash=row.get('config_hash') or '',
                active_s=row.get('active_s'), turns=row.get('turns'), api_calls=row.get('api_calls'),
                input_tokens=row.get('input_tokens'), output_tokens=row.get('output_tokens'),
                errors=row.get('errors'), tool_calls=row.get('tool_calls'), tool_failures=row.get('tool_failures'),
                has_snapshot=bool(row.get('has_snapshot')))


def _labels(items):
    labels = {}
    for item in items:
        if item['label']:
            entry = labels.setdefault(item['label'], dict(label=item['label'], runs=0, configs=set()))
            entry['runs'] += 1
            entry['configs'].add(item['config_hash'])
    return [dict(label=e['label'], runs=e['runs'], configs=len(e['configs'])) for e in labels.values()]


def experiments_snapshot(ctx, query):
    if ctx.demo:
        return _demo_experiments(query)
    modules = _modules()
    if modules is None:
        return _unavailable('experiments', 'assistant_core is not installed beside the console.')
    _, reporting, flatten = modules
    db = _safe_dir(ctx) / 'telemetry.sqlite3'
    tokens = _compare_tokens(query.get('compare'), allow_labels=True)
    if db.is_symlink() or not db.is_file():
        return dict(_unavailable('experiments', 'No gateway telemetry yet. Start the gateway with the assistant-core '
                                 'plugin (ASSISTANT_TELEMETRY=true) and label runs with ASSISTANT_RUN_LABEL.'),
                    labels=[], compare=None, pricing=False)
    pricing = reporting.load_pricing(Path(ctx.assistant_dir) / 'pricing.yaml')
    try:
        rows = reporting.runs_overview(db, 100)
        items = [_experiment_item(row) for row in rows]
        compare = None
        if tokens:
            compare = _experiment_compare(db, reporting, flatten, items, tokens, pricing)
    except AdminError:
        raise
    except Exception:
        raise AdminError('Gateway telemetry could not be read.', 503) from None
    return dict(section='experiments', available=True, source='telemetry', items=items, labels=_labels(items),
                compare=compare, pricing=bool(pricing), metrics=[dict(key=k, label=l, format=f)
                                                                for k, l, f in EXPERIMENT_METRICS],
                updated_at=now_iso())


def _experiment_compare(db, reporting, flatten, items, tokens, pricing):
    by_id = {item['id']: item for item in items}
    columns = []
    for token in tokens:
        if token.startswith('label:'):
            runs = [item for item in items if item['label'] == token[6:]]
            title, kind = token[6:], 'label'
        else:
            runs = [by_id[token]] if token in by_id else []
            title, kind = (runs[0]['label'] or token) if runs else token, 'run'
        if not runs:
            raise AdminError('A compared run or label is no longer in the recent gateway runs.', 404)
        columns.append(dict(key=token, title=title, kind=kind, runs=[run['id'] for run in runs],
                            config_hashes=sorted({run['config_hash'] for run in runs if run['config_hash']})))
    snapshots = reporting.config_snapshots(db, {h for column in columns for h in column['config_hashes']})
    flats, values = [], []
    for column in columns:
        metrics = reporting.run_metrics(db, column['runs'], pricing)
        values.append(metrics)
        column['models'] = metrics.get('models', [])[:5]
        column['cost_complete'] = metrics.get('cost_complete')
        flats.append(_merge_flat([flatten(snapshots[h]) if h in snapshots else None for h in column['config_hashes']]))
    rows = [dict(key=key, label=label, format=kind, values=[v.get(key) for v in values])
            for key, label, kind in EXPERIMENT_METRICS]
    return dict(columns=columns, rows=rows, settings=_diff(flats))


# ---- Benchmarks -------------------------------------------------------------------

_stores = {}
_stores_lock = threading.Lock()


def close_stores():
    """Close cached benchmark stores (tests; a running thread keeps its own reference)."""
    with _stores_lock:
        for store in _stores.values():
            store.close()
        _stores.clear()


def _store(ctx, bench):
    path = _safe_dir(ctx) / 'bench.sqlite3'
    key = str(path.resolve())
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            try:
                store = _stores[key] = bench.BenchStore(path)
            except (bench.BenchError, OSError, Exception):
                raise AdminError('The benchmark store could not be opened.', 503) from None
        return store


def _bundled_dir(ctx, bench):
    override = ctx.environ.get('HERMES_BENCH_BUNDLED_DIR', '').strip()
    return Path(override) if override else bench.BUNDLED_DIR


def _suite_view(suite):
    return dict(name=suite.name, description=suite.description, source=suite.source, tasks=len(suite.tasks),
                levels=suite.levels(), categories=suite.categories()[:12], digest=suite.digest)


def _run_view(run):
    completed, total = run.get('completed') or 0, run.get('total') or 0
    return dict(id=run['id'], suite=run['suite'], suite_hash=run.get('suite_hash'), label=run.get('label') or '',
                status=run['status'], reason=run.get('reason'), created_at=run.get('created_at'),
                started_at=run.get('started_at'), finished_at=run.get('finished_at'), total=total,
                completed=completed, passed=run.get('passed') or 0, errors=run.get('errors') or 0,
                pass_rate=(run['passed'] / completed) if completed else None,
                progress=(completed / total) if total else None, config_hash=run.get('config_hash') or '',
                gateway_config_hash=run.get('gateway_config_hash') or '', target_host=run.get('target_host') or '',
                model=run.get('model') or '', repeats=run.get('repeats'), concurrency=run.get('concurrency'),
                input_tokens=run.get('input_tokens'), output_tokens=run.get('output_tokens'))


def _result_view(result):
    return dict(task_id=result['task_id'], attempt=result['attempt'], level=result.get('level'),
                category=result.get('category'), passed=bool(result['passed']),
                checks=[dict(type=str(c.get('type'))[:20], passed=bool(c.get('passed')), detail=str(c.get('detail'))[:200])
                        for c in (result.get('checks') or [])[:12] if isinstance(c, dict)],
                latency_s=result.get('latency_s'), total_tokens=result.get('total_tokens'),
                input_tokens=result.get('input_tokens'), output_tokens=result.get('output_tokens'),
                response_model=result.get('response_model'), error=result.get('error'),
                status_code=result.get('status_code'), response_length=result.get('response_length'),
                response_excerpt=result.get('response_text'), session_id=result.get('session_id'))


def _start_action(suites, revision):
    """suites: {name: task count}."""
    names = sorted(suites)
    return dict(id='start_run', label='Start benchmark run', revision=revision,
                description=('Sends every task of the suite to your Hermes API server as real model requests. '
                             'This uses model quota and costs money. Failed requests are recorded, never retried.'),
                fields=[
                    dict(name='suite', label='Suite', type='select', required=True, value=names[0],
                         options=[dict(value=name, label=f"{name} · {suites[name]} tasks") for name in names]),
                    dict(name='label', label='Label', type='text', max_length=60, value='',
                         help='Name the settings under test, e.g. B1-cheap or B2-routing.'),
                    dict(name='repeats', label='Repeats', type='number', value=1, required=True,
                         help='Runs each task 1 to 5 times to measure variance.'),
                    dict(name='concurrency', label='Parallel requests', type='select', value=1,
                         options=[dict(value=n, label=str(n)) for n in (1, 2, 3, 4)],
                         help='Higher is faster but can hit provider rate limits.'),
                ])


def _row_actions(runs):
    actions = []
    for run in runs:
        if run['status'] in ('queued', 'running'):
            actions.append(dict(id='cancel_run', label='Cancel', target=run['id'], revision=f"{run['id']}:{run['status']}",
                                description='Stops starting new requests. Requests already sent finish and are recorded.',
                                fields=[dict(name='run_id', type='hidden', value=run['id'])]))
        else:
            actions.append(dict(id='delete_run', label='Delete', target=run['id'], revision=f"{run['id']}:{run['status']}",
                                description='Deletes this run and its stored results. Gateway telemetry is kept.',
                                fields=[dict(name='run_id', type='hidden', value=run['id'])]))
    return actions


def _summary(bench, reporting, store, run, ctx, pricing):
    results = store.results(run['id'])
    db = Path(ctx.assistant_dir) / 'telemetry.sqlite3'
    try:
        sessions = reporting.session_totals(db, [r.get('session_id') for r in results]) if db.is_file() else {}
    except Exception:
        sessions = {}
    return results, bench.summarize(run, results, sessions=sessions, pricing=pricing)


def benchmarks_snapshot(ctx, query):
    if ctx.demo:
        return _DEMO.snapshot(query)
    modules = _modules()
    if modules is None:
        return _unavailable('benchmarks', 'assistant_core is not installed beside the console.')
    bench, reporting, flatten = modules
    compare_ids = _compare_tokens(query.get('compare'), allow_labels=False)
    run_id = (query.get('run') or '').strip()
    if run_id and not bench.RUN_ID.fullmatch(run_id):
        raise AdminError('Unknown benchmark run.', 404)
    store = _store(ctx, bench)
    suites, problems = bench.load_suites(ctx.home, bundled_dir=_bundled_dir(ctx, bench))
    try:
        target = bench.resolve_target(ctx.environ)
        configured, target_message = True, None
    except bench.BenchError as error:
        target, configured, target_message = None, False, str(error)
    pricing = reporting.load_pricing(Path(ctx.assistant_dir) / 'pricing.yaml')
    active = store.active_run()
    runs = store.list_runs(50)
    items = [_run_view(run) for run in runs]
    current_hash = bench.config_file_hash(ctx.home) if not active else (active.get('config_hash') or '')
    gateway_hash = bench.gateway_config_hash(Path(ctx.assistant_dir) / 'telemetry.sqlite3')
    detail = None
    if run_id:
        run = store.get_run(run_id)
        if run is None:
            raise AdminError('Unknown benchmark run.', 404)
        results, summary = _summary(bench, reporting, store, run, ctx, pricing)
        suite = suites.get(run['suite'])
        detail = dict(_run_view(run), summary=summary, results=[_result_view(r) for r in results],
                      settings=[dict(key=k, value=str(v)[:160]) for k, v in flatten(run.get('snapshot') or {}).items()][:200],
                      suite_changed=bool(suite and suite.digest != run.get('suite_hash')))
    compare = None
    if compare_ids:
        pairs = []
        for cid in compare_ids:
            run = store.get_run(cid) if bench.RUN_ID.fullmatch(cid) else None
            if run is None:
                raise AdminError('A compared run no longer exists.', 404)
            pairs.append((run, _summary(bench, reporting, store, run, ctx, pricing)[1]))
        compare = bench.compare(pairs)
        compare['settings'] = _diff([flatten(run.get('snapshot') or {}) or None for run, _ in pairs])
    revision = _revision(active and active['id'], sorted((s.name, s.digest) for s in suites.values()),
                         target and target.host)
    actions = []
    if configured and not active and suites:
        actions.append(_start_action({name: len(suite.tasks) for name, suite in suites.items()}, revision))
    actions.extend(_row_actions(runs))
    message = None
    if gateway_hash and current_hash and gateway_hash != current_hash:
        message = ('config.yaml changed after the gateway started. Restart the gateway so a new run measures '
                   'the current settings.')
    return dict(section='benchmarks', available=True, source='bench-store', configured=configured,
                target=dict(host=target.host, model=target.model) if target else None,
                setup=[] if configured else list(SETUP_STEPS), target_message=target_message,
                config=dict(current_hash=current_hash, gateway_hash=gateway_hash,
                            mismatch=bool(gateway_hash and current_hash and gateway_hash != current_hash)),
                suites=[_suite_view(s) for s in suites.values()], suite_problems=problems, items=items,
                active=_run_view(active) if active else None, run=detail, compare=compare, pricing=bool(pricing),
                actions=actions, message=message, revision=revision, updated_at=now_iso())


def _values(values, keys):
    if not isinstance(values, dict) or set(values) != set(keys):
        raise AdminError('Unsupported benchmark action fields.')
    return values


def benchmarks_action(ctx, data):
    action, revision, values = validate_action(data)
    if ctx.demo:
        return _DEMO.action(action, revision, values)
    modules = _modules()
    if modules is None:
        raise AdminError('assistant_core is not installed beside the console.', 503)
    bench, _, _ = modules
    store = _store(ctx, bench)
    if action == 'start_run':
        values = _values(values, ('suite', 'label', 'repeats', 'concurrency'))
        suites, _ = bench.load_suites(ctx.home, bundled_dir=_bundled_dir(ctx, bench))
        try:
            target = bench.resolve_target(ctx.environ)
        except bench.BenchError as error:
            raise AdminError(str(error), 503) from None
        active = store.active_run()
        current = _revision(active and active['id'], sorted((s.name, s.digest) for s in suites.values()), target.host)
        if revision != current:
            raise AdminError('Benchmarks changed since this page loaded. Refresh and try again.', 409)
        suite = suites.get(values['suite']) if isinstance(values['suite'], str) else None
        if suite is None:
            raise AdminError('Choose an available suite.')
        label, repeats, concurrency = _run_options(bench, values)
        try:
            run = bench.prepare_run(store, suite, target, label=label, repeats=repeats, concurrency=concurrency,
                                    home=ctx.home, telemetry_db=Path(ctx.assistant_dir) / 'telemetry.sqlite3')
            bench.start_background(store, run, suite, target)
        except bench.BenchError as error:
            raise AdminError(str(error), 409) from None
        return {'ok': True, 'message': f"Benchmark started: {run['total']} requests to {target.host}.", 'run_id': run['id']}
    if action in ('cancel_run', 'delete_run'):
        values = _values(values, ('run_id',))
        run_id = values['run_id']
        run = store.get_run(run_id) if isinstance(run_id, str) and bench.RUN_ID.fullmatch(run_id) else None
        if run is None:
            raise AdminError('Unknown benchmark run.', 404)
        if revision != f"{run['id']}:{run['status']}":
            raise AdminError('This run changed since the page loaded. Refresh and try again.', 409)
        if action == 'cancel_run':
            if run['status'] not in ('queued', 'running') or not bench.cancel(store, run['id']):
                raise AdminError('This run is no longer active.', 409)
            return {'ok': True, 'message': 'Cancellation requested. Requests already sent will finish first.'}
        try:
            store.delete_run(run['id'])
        except bench.BenchError as error:
            raise AdminError(str(error), 409) from None
        return {'ok': True, 'message': 'Benchmark run deleted.'}
    raise AdminError('Unknown benchmark action.')


def _run_options(bench, values):
    concurrency = values['concurrency']
    if isinstance(concurrency, str) and concurrency.isdigit():
        concurrency = int(concurrency)
    try:
        return (bench.validate_label(values['label']), bench.bounded_int(values['repeats'], 'Repeats', 1, 5),
                bench.bounded_int(concurrency, 'Parallel requests', 1, 4))
    except bench.BenchError as error:
        raise AdminError(str(error)) from None


# ---- Demo --------------------------------------------------------------------------

DEMO_SNAPSHOTS = {
    'a1f0c3d2e4b5a6c7': {'version': 1, 'model': {'default': 'demo/fast-small', 'provider': 'demo', 'reasoning_effort': 'low'},
                         'agent': {'max_turns': 30}, 'compression': {'enabled': True, 'threshold': 0.5},
                         'platform_toolsets': {'telegram': ['code_execution', 'vision', 'web']},
                         'terminal': {'backend': 'docker'}, 'memory': {'memory_enabled': False}},
    'b2e1d4c3f5a6b7c8': {'version': 1, 'model': {'default': 'demo/fast-small', 'provider': 'demo', 'reasoning_effort': 'medium'},
                         'agent': {'max_turns': 30}, 'compression': {'enabled': True, 'threshold': 0.5},
                         'auxiliary': {'compression': {'model': 'demo/fast-small'}},
                         'platform_toolsets': {'telegram': ['code_execution', 'vision', 'web']},
                         'terminal': {'backend': 'docker'}, 'memory': {'memory_enabled': False}},
    'c3f2e5d4a6b7c8d9': {'version': 1, 'model': {'default': 'demo/strong-large', 'provider': 'demo', 'reasoning_effort': 'high'},
                         'agent': {'max_turns': 60}, 'compression': {'enabled': False},
                         'platform_toolsets': {'telegram': ['code_execution', 'vision', 'web']},
                         'terminal': {'backend': 'docker'}, 'memory': {'memory_enabled': False}},
}
DEMO_GATEWAY = (
    # run_id, label, hours ago, config, turns, calls/turn, tokens/turn, p50, p95, error rate, tools, tool failure rate, cost
    ('20261028-090000-a1b2c3', 'B1-cheap', 96, 'a1f0c3d2e4b5a6c7', 84, 2.1, 5200, 1.4, 4.2, 0.012, 61, 0.05, 0.42),
    ('20261029-090000-b2c3d4', 'B2-routing', 72, 'b2e1d4c3f5a6b7c8', 91, 2.4, 6100, 1.9, 6.8, 0.008, 70, 0.03, 0.88),
    ('20261030-090000-c3d4e5', 'B3-strong', 48, 'c3f2e5d4a6b7c8d9', 77, 2.9, 8900, 3.6, 11.5, 0.004, 66, 0.02, 2.95),
    ('20261031-090000-d4e5f6', 'B3-strong', 24, 'c3f2e5d4a6b7c8d9', 52, 3.0, 9300, 3.8, 12.1, 0.0, 41, 0.02, 2.10),
)


def _demo_experiment_items():
    now = time.time()
    items = []
    for run_id, label, hours, config, turns, per_turn, tokens, *_rest in DEMO_GATEWAY:
        calls = round(turns * per_turn)
        items.append(dict(id=run_id, label=label, started_at=now - hours * 3600, hermes_rev='demo000000',
                          config_hash=config, active_s=hours * 3600 - 600, turns=turns, api_calls=calls,
                          input_tokens=round(turns * tokens * 0.85), output_tokens=round(turns * tokens * 0.15),
                          errors=round(calls * _rest[3]), tool_calls=_rest[4], tool_failures=round(_rest[4] * _rest[5]),
                          has_snapshot=True))
    return items


def _demo_experiments(query):
    items = _demo_experiment_items()
    tokens = _compare_tokens(query.get('compare'), allow_labels=True)
    compare = None
    if tokens:
        columns, values, flats = [], [], []
        for token in tokens:
            rows = [r for r in DEMO_GATEWAY if (token.startswith('label:') and r[1] == token[6:]) or r[0] == token]
            if not rows:
                raise AdminError('A compared run or label is no longer in the recent gateway runs.', 404)
            turns = sum(r[4] for r in rows)
            calls = sum(round(r[4] * r[5]) for r in rows)
            tokens_total = sum(r[4] * r[6] for r in rows)
            tool_calls = sum(r[10] for r in rows)
            values.append(dict(runs=len(rows), turns=turns, api_calls=calls, api_calls_per_turn=calls / turns,
                               tokens_per_turn=tokens_total / turns, input_tokens=round(tokens_total * .85),
                               output_tokens=round(tokens_total * .15), p50_latency_s=rows[-1][7],
                               p95_latency_s=rows[-1][8], error_rate=rows[-1][9], tool_calls=tool_calls,
                               tool_failure_rate=rows[-1][11], cost_usd=sum(r[12] for r in rows)))
            columns.append(dict(key=token, title=token[6:] if token.startswith('label:') else rows[0][1],
                                kind='label' if token.startswith('label:') else 'run', runs=[r[0] for r in rows],
                                config_hashes=sorted({r[3] for r in rows}), cost_complete=True,
                                models=[dict(model=DEMO_SNAPSHOTS[rows[0][3]]['model']['default'], api_calls=calls)]))
            from assistant_core.telemetry import flatten_snapshot
            flats.append(_merge_flat([flatten_snapshot(DEMO_SNAPSHOTS[h]) for h in {r[3] for r in rows}]))
        compare = dict(columns=columns, rows=[dict(key=k, label=l, format=f, values=[v.get(k) for v in values])
                                              for k, l, f in EXPERIMENT_METRICS], settings=_diff(flats))
    return dict(section='experiments', available=True, source='demo', items=items, labels=_labels(items),
                compare=compare, pricing=True, metrics=[dict(key=k, label=l, format=f) for k, l, f in EXPERIMENT_METRICS],
                updated_at=now_iso())


DEMO_SUITES = (
    dict(name='assistant-basics', description='Everyday Vietnamese and English assistant tasks, L1-L3, no tools required.',
         source='bundled', tasks=14, levels=dict(L1=5, L2=5, L3=4),
         categories=['chat', 'math', 'planning', 'reasoning', 'writing'], digest='demo00000001'),
    dict(name='tool-use', description='Web search, code execution and multi-step tool tasks.', source='bundled', tasks=7,
         levels=dict(L1=2, L2=2, L3=3), categories=['code', 'multi-step', 'web'], digest='demo00000002'),
)
DEMO_PASS = {'a1f0c3d2e4b5a6c7': (0.9, 0.62, 0.38), 'b2e1d4c3f5a6b7c8': (0.92, 0.78, 0.6),
             'c3f2e5d4a6b7c8d9': (0.95, 0.88, 0.8)}
DEMO_LATENCY = {'a1f0c3d2e4b5a6c7': 3.2, 'b2e1d4c3f5a6b7c8': 4.6, 'c3f2e5d4a6b7c8d9': 9.8}
DEMO_STEP_S = 1.5


class _DemoBench:
    """In-memory benchmark state for demo mode. Progress is derived from the clock: no thread, no network."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        now = time.time()
        self.runs = {}
        for index, (label, config, suite) in enumerate((('B1-cheap', 'a1f0c3d2e4b5a6c7', 'assistant-basics'),
                                                        ('B2-routing', 'b2e1d4c3f5a6b7c8', 'assistant-basics'),
                                                        ('B3-strong', 'c3f2e5d4a6b7c8d9', 'assistant-basics'))):
            created = now - (3 - index) * 86400
            run_id = time.strftime('%Y%m%d-%H%M%S-', time.gmtime(created)) + f'{index + 1:06x}'
            self.runs[run_id] = dict(id=run_id, suite=suite, suite_hash='demo00000001', label=label, status='done',
                                     reason=None, created_at=created, started_at=created, finished_at=created + 600,
                                     config_hash=config, gateway_config_hash=config, snapshot=DEMO_SNAPSHOTS[config],
                                     target_host='demo-hermes.local', model='hermes', repeats=2, concurrency=2,
                                     task_count=14, total=28)

    def _suite(self, name):
        return next((s for s in DEMO_SUITES if s['name'] == name), None)

    def _tasks(self, suite_name):
        suite = self._suite(suite_name)
        tasks = []
        for level in LEVELS:
            for n in range(suite['levels'][level]):
                tasks.append((f"{suite_name.split('-')[0]}-{level.lower()}-{n + 1}", level,
                              suite['categories'][(n + len(tasks)) % len(suite['categories'])]))
        return tasks

    def _results(self, run, now):
        tasks = self._tasks(run['suite'])
        jobs = [(task, attempt) for attempt in range(1, run['repeats'] + 1) for task in tasks]
        if run['status'] in ('queued', 'running'):
            jobs = jobs[:min(len(jobs), int((now - run['started_at']) / DEMO_STEP_S))]
        elif run['status'] == 'cancelled':
            jobs = jobs[:run.get('completed_at_cancel', 0)]
        rates, base = DEMO_PASS.get(run['config_hash'], (0.8, 0.6, 0.4)), DEMO_LATENCY.get(run['config_hash'], 4.0)
        results = []
        for (task_id, level, category), attempt in jobs:
            rng = random.Random(zlib.crc32(f"{run['id']}/{task_id}/{attempt}".encode()))
            index = LEVELS.index(level)
            error = 'timeout' if rng.random() < 0.02 else None
            passed = not error and rng.random() < rates[index]
            latency = round(base * (1 + index * 0.8) * rng.uniform(0.6, 1.5), 2)
            tokens = int((900 + index * 1600) * rng.uniform(0.7, 1.4))
            checks = [] if error else [dict(type='contains', passed=passed, detail="contains 'demo answer'" if passed
                                            else "missing 'demo answer'"),
                                       dict(type='max_latency_s', passed=True, detail=f'latency {latency:.1f}s vs limit 300s')]
            results.append(dict(task_id=task_id, attempt=attempt, level=level, category=category, passed=passed,
                                checks=checks, latency_s=latency, total_tokens=None if error else tokens,
                                input_tokens=None if error else int(tokens * .8),
                                output_tokens=None if error else tokens - int(tokens * .8),
                                response_model='hermes', error=error, status_code=None if error else 200,
                                response_length=None if error else 400 + index * 300,
                                response_excerpt=None if error else 'Demo response. No model was called.',
                                session_id=None))
        return results

    def _refresh(self, now):
        for run in self.runs.values():
            if run['status'] == 'running' and int((now - run['started_at']) / DEMO_STEP_S) >= run['total']:
                run.update(status='done', finished_at=run['started_at'] + run['total'] * DEMO_STEP_S)

    def _view(self, run, now):
        results = self._results(run, now)
        view = dict(run, completed=len(results), passed=sum(r['passed'] for r in results),
                    errors=sum(bool(r['error']) for r in results),
                    input_tokens=sum(r['input_tokens'] or 0 for r in results),
                    output_tokens=sum(r['output_tokens'] or 0 for r in results))
        return _run_view(view), results

    def _summary(self, run, results):
        from assistant_core import bench
        pricing = {'hermes': {'input': 0.5, 'output': 2.0, 'cache_read': 0.05}}
        if run['config_hash'] == 'c3f2e5d4a6b7c8d9':
            pricing = {'hermes': {'input': 3.0, 'output': 15.0, 'cache_read': 0.3}}
        return bench.summarize(run, results, pricing=pricing)

    def _revision(self):
        active = next((r['id'] for r in self.runs.values() if r['status'] in ('queued', 'running')), None)
        return _revision('demo', active, [s['digest'] for s in DEMO_SUITES])

    def snapshot(self, query):
        compare_ids = _compare_tokens(query.get('compare'), allow_labels=False)
        run_id = (query.get('run') or '').strip()
        with self.lock:
            now = time.time()
            self._refresh(now)
            runs = sorted(self.runs.values(), key=lambda r: r['created_at'], reverse=True)
            items = [self._view(run, now)[0] for run in runs]
            active = next((item for item in items if item['status'] in ('queued', 'running')), None)
            detail = None
            if run_id:
                run = self.runs.get(run_id)
                if run is None:
                    raise AdminError('Unknown benchmark run.', 404)
                view, results = self._view(run, now)
                from assistant_core.telemetry import flatten_snapshot
                detail = dict(view, summary=self._summary(run, results), results=results, suite_changed=False,
                              settings=[dict(key=k, value=str(v)) for k, v in flatten_snapshot(run['snapshot']).items()])
            compare = None
            if compare_ids:
                from assistant_core import bench
                from assistant_core.telemetry import flatten_snapshot
                pairs = []
                for cid in compare_ids:
                    run = self.runs.get(cid)
                    if run is None:
                        raise AdminError('A compared run no longer exists.', 404)
                    pairs.append((run, self._summary(run, self._results(run, now))))
                compare = bench.compare(pairs)
                compare['settings'] = _diff([flatten_snapshot(run['snapshot']) for run, _ in pairs])
            revision = self._revision()
            actions = [] if active else [_start_action({s['name']: s['tasks'] for s in DEMO_SUITES}, revision)]
            actions.extend(_row_actions(items))
        return dict(section='benchmarks', available=True, source='demo', configured=True,
                    target=dict(host='demo-hermes.local', model='hermes'), setup=[], target_message=None,
                    config=dict(current_hash='c3f2e5d4a6b7c8d9', gateway_hash='c3f2e5d4a6b7c8d9', mismatch=False),
                    suites=[dict(s) for s in DEMO_SUITES], suite_problems=[], items=items, active=active, run=detail,
                    compare=compare, pricing=True, actions=actions, revision=revision,
                    message='Demo mode: runs are simulated in memory. No model is called and nothing is saved.',
                    updated_at=now_iso())

    def action(self, action, revision, values):
        with self.lock:
            now = time.time()
            self._refresh(now)
            if action == 'start_run':
                values = _values(values, ('suite', 'label', 'repeats', 'concurrency'))
                if revision != self._revision():
                    raise AdminError('Benchmarks changed since this page loaded. Refresh and try again.', 409)
                if any(r['status'] in ('queued', 'running') for r in self.runs.values()):
                    raise AdminError('Another benchmark run is in progress. Wait for it or cancel it first.', 409)
                suite = self._suite(values['suite']) if isinstance(values['suite'], str) else None
                if suite is None:
                    raise AdminError('Choose an available suite.')
                from assistant_core import bench
                label, repeats, concurrency = _run_options(bench, values)
                if len(self.runs) >= 20:
                    oldest = min(self.runs.values(), key=lambda r: r['created_at'])
                    self.runs.pop(oldest['id'])
                run_id = time.strftime('%Y%m%d-%H%M%S-', time.gmtime(now)) + f'{zlib.crc32(str(now).encode()) & 0xffffff:06x}'
                self.runs[run_id] = dict(id=run_id, suite=suite['name'], suite_hash=suite['digest'], label=label,
                                         status='running', reason=None, created_at=now, started_at=now, finished_at=None,
                                         config_hash='c3f2e5d4a6b7c8d9', gateway_config_hash='c3f2e5d4a6b7c8d9',
                                         snapshot=DEMO_SNAPSHOTS['c3f2e5d4a6b7c8d9'], target_host='demo-hermes.local',
                                         model='hermes', repeats=repeats, concurrency=concurrency,
                                         task_count=suite['tasks'], total=suite['tasks'] * repeats)
                return {'ok': True, 'message': f"Demo run started: {suite['tasks'] * repeats} simulated requests.",
                        'run_id': run_id}
            if action in ('cancel_run', 'delete_run'):
                values = _values(values, ('run_id',))
                run = self.runs.get(values['run_id']) if isinstance(values['run_id'], str) else None
                if run is None:
                    raise AdminError('Unknown benchmark run.', 404)
                if revision != f"{run['id']}:{run['status']}":
                    raise AdminError('This run changed since the page loaded. Refresh and try again.', 409)
                if action == 'cancel_run':
                    if run['status'] not in ('queued', 'running'):
                        raise AdminError('This run is no longer active.', 409)
                    done = min(run['total'], int((now - run['started_at']) / DEMO_STEP_S))
                    run.update(status='cancelled', reason='Cancelled by the owner.', finished_at=now,
                               completed_at_cancel=done)
                    return {'ok': True, 'message': 'Demo run cancelled.'}
                if run['status'] in ('queued', 'running'):
                    raise AdminError('Cancel the run before deleting it.', 409)
                del self.runs[run['id']]
                return {'ok': True, 'message': 'Demo run deleted.'}
        raise AdminError('Unknown benchmark action.')


_DEMO = _DemoBench()


register(Section('experiments', experiments_snapshot, None, frozenset({'compare'})))
register(Section('benchmarks', benchmarks_snapshot, benchmarks_action, frozenset({'compare', 'run'})))
