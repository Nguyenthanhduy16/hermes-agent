"""Read-only queries over assistant-core data for admin pages and analysis.

Opens SQLite in ``mode=ro`` so a dashboard can never write to or lock the
stores the gateway uses. Callers get plain dicts; the schema stays here.
"""

from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3


def _connect(path: Path):
    return sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)


def _rows(path: Path, sql: str, params=()) -> list[dict]:
    if not Path(path).exists():
        return []
    with closing(_connect(path)) as db:
        cursor = db.execute(sql, params)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def runs(telemetry_db: Path, limit: int = 50) -> list[dict]:
    """Gateway runs (one per process start) with call, token and error totals."""
    return _rows(telemetry_db, """
        SELECT r.run_id, r.started_at, r.hermes_rev, r.config_hash, r.label,
               COUNT(a.id) AS api_calls, COALESCE(SUM(a.input_tokens), 0) AS input_tokens,
               COALESCE(SUM(a.output_tokens), 0) AS output_tokens,
               COALESCE(SUM(a.cache_read_tokens), 0) AS cache_read_tokens,
               COALESCE(SUM(a.error_type IS NOT NULL), 0) AS errors
        FROM runs r LEFT JOIN api_calls a ON a.run_id = r.run_id
        GROUP BY r.run_id ORDER BY r.started_at DESC LIMIT ?""", (limit,))


def models(telemetry_db: Path, since: float = 0.0) -> list[dict]:
    """Per platform and model: calls, tokens, latency and error rate since a timestamp."""
    return _rows(telemetry_db, """
        SELECT platform, model, COUNT(*) AS api_calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
               COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
               AVG(duration_s) AS avg_duration_s, AVG(ttfb_s) AS avg_ttfb_s,
               COALESCE(SUM(error_type IS NOT NULL), 0) AS errors
        FROM api_calls WHERE ts >= ? GROUP BY platform, model ORDER BY api_calls DESC""", (since,))


def tools(telemetry_db: Path, since: float = 0.0) -> list[dict]:
    """Per tool: calls, failures, vetoes and mean duration since a timestamp."""
    return _rows(telemetry_db, """
        SELECT tool_name, COUNT(*) AS calls, COALESCE(SUM(blocked), 0) AS blocked,
               COALESCE(SUM(status IS NOT NULL AND status NOT IN ('ok', 'success', 'blocked')), 0) AS failed,
               AVG(duration_ms) AS avg_duration_ms
        FROM tool_calls WHERE ts >= ? GROUP BY tool_name ORDER BY calls DESC""", (since,))


STEP_COLUMNS = ("ts, turn_id, api_request_id, api_call_count, retry_count, model, duration_s, ttfb_s, "
                "finish_reason, error_type, input_tokens, output_tokens, cache_read_tokens, tool_call_count")
ROUTE_COLUMNS = "default_model, route_policy, route_choice, route_reason, route_score, route_switched, route_features"


def session_steps(telemetry_db: Path, session_id: str) -> list[dict]:
    """Chronological API attempts of one session: the trajectory view for the router.

    Routed steps carry the router's decision; ``route_features`` is parsed into a dict.
    """
    query = "SELECT {} FROM api_calls WHERE session_id = ? ORDER BY ts"
    try:
        rows = _rows(telemetry_db, query.format(f"{STEP_COLUMNS}, {ROUTE_COLUMNS}"), (session_id,))
    except sqlite3.OperationalError as error:  # a store written before the router existed
        if "no such column" not in str(error):
            raise
        return _rows(telemetry_db, query.format(STEP_COLUMNS), (session_id,))
    for row in rows:
        try:
            row["route_features"] = json.loads(row["route_features"]) if row["route_features"] else None
        except ValueError:
            row["route_features"] = None
    return rows


def quota_today(quota_db: Path, day: str) -> dict:
    """Turns used today: total and number of distinct people (hashed, never named)."""
    rows = _rows(quota_db, "SELECT COUNT(*) AS turns, COUNT(DISTINCT person) AS people FROM turns WHERE day = ?",
                 (day,))
    return rows[0] if rows else {"turns": 0, "people": 0}


# ---- experiments: runs, settings snapshots and comparable metrics -------------

TOOL_FAILED = "status IS NOT NULL AND status NOT IN ('ok', 'success', 'blocked')"
MAX_LATENCY_SAMPLES = 50000


def _safe_rows(path: Path, sql: str, params=()) -> list[dict]:
    """Like _rows, but an older store without a newer table reads as empty."""
    try:
        return _rows(path, sql, params)
    except sqlite3.OperationalError as error:
        if "no such table" in str(error) or "no such column" in str(error):
            return []
        raise


def _marks(values) -> str:
    return ",".join("?" * len(values))


def runs_overview(telemetry_db: Path, limit: int = 100) -> list[dict]:
    """Gateway runs with turns, tool outcomes, last activity and whether settings were captured."""
    rows = runs(telemetry_db, limit)
    if not rows:
        return rows
    ids = [row["run_id"] for row in rows]
    extra = {r["run_id"]: r for r in _rows(telemetry_db, f"""
        SELECT run_id, COUNT(DISTINCT turn_id) AS turns, COUNT(DISTINCT session_id) AS sessions,
               MAX(ts) AS last_api_ts, AVG(duration_s) AS avg_duration_s
        FROM api_calls WHERE run_id IN ({_marks(ids)}) GROUP BY run_id""", ids)}
    tools_by_run = {r["run_id"]: r for r in _rows(telemetry_db, f"""
        SELECT run_id, COUNT(*) AS tool_calls, COALESCE(SUM({TOOL_FAILED}), 0) AS tool_failures,
               MAX(ts) AS last_tool_ts
        FROM tool_calls WHERE run_id IN ({_marks(ids)}) GROUP BY run_id""", ids)}
    hashes = {row["config_hash"] for row in rows if row.get("config_hash")}
    snapshots = set(config_snapshots(telemetry_db, hashes))
    for row in rows:
        api, tool = extra.get(row["run_id"], {}), tools_by_run.get(row["run_id"], {})
        last = max([value for value in (api.get("last_api_ts"), tool.get("last_tool_ts")) if value] or [0])
        row.update(turns=api.get("turns") or 0, sessions=api.get("sessions") or 0,
                   avg_duration_s=api.get("avg_duration_s"), tool_calls=tool.get("tool_calls") or 0,
                   tool_failures=tool.get("tool_failures") or 0, last_activity=last or None,
                   active_s=(last - row["started_at"]) if last and row.get("started_at") else None,
                   has_snapshot=row.get("config_hash") in snapshots)
    return rows


def config_snapshots(telemetry_db: Path, hashes) -> dict:
    """config_hash -> settings snapshot (dict) for the hashes that have one."""
    hashes = sorted({str(h) for h in hashes if h})[:200]
    if not hashes:
        return {}
    out = {}
    for row in _safe_rows(telemetry_db, f"SELECT config_hash, snapshot_json FROM config_snapshots "
                                        f"WHERE config_hash IN ({_marks(hashes)})", hashes):
        try:
            value = json.loads(row["snapshot_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            out[row["config_hash"]] = value
    return out


def percentile(values, fraction):
    """Nearest-rank percentile of a list of numbers (None when empty)."""
    data = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not data:
        return None
    rank = max(1, math.ceil(fraction * len(data)))
    return data[min(rank, len(data)) - 1]


def run_metrics(telemetry_db: Path, run_ids, pricing: dict | None = None) -> dict:
    """Comparable metrics over one or more gateway runs (a label groups several runs)."""
    ids = sorted({str(r) for r in run_ids if r})[:200]
    empty = dict(runs=len(ids), turns=0, api_calls=0, input_tokens=0, output_tokens=0, cache_read_tokens=0,
                 errors=0, tool_calls=0, tool_failures=0, api_calls_per_turn=None, tokens_per_turn=None,
                 p50_latency_s=None, p95_latency_s=None, error_rate=None, tool_failure_rate=None, cost_usd=None,
                 cost_complete=False, models=[])
    if not ids or not Path(telemetry_db).exists():
        return empty
    marks = _marks(ids)
    totals = _rows(telemetry_db, f"""
        SELECT COUNT(*) AS api_calls, COUNT(DISTINCT turn_id) AS turns,
               COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
               COALESCE(SUM(error_type IS NOT NULL), 0) AS errors
        FROM api_calls WHERE run_id IN ({marks})""", ids)[0]
    tools_row = _rows(telemetry_db, f"""
        SELECT COUNT(*) AS tool_calls, COALESCE(SUM({TOOL_FAILED}), 0) AS tool_failures
        FROM tool_calls WHERE run_id IN ({marks})""", ids)[0]
    latencies = [r["duration_s"] for r in _rows(telemetry_db, f"""
        SELECT duration_s FROM api_calls WHERE run_id IN ({marks}) AND duration_s IS NOT NULL
        ORDER BY id DESC LIMIT {MAX_LATENCY_SAMPLES}""", ids)]
    by_model = _rows(telemetry_db, f"""
        SELECT COALESCE(response_model, model) AS model, COUNT(*) AS api_calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens
        FROM api_calls WHERE run_id IN ({marks}) GROUP BY COALESCE(response_model, model)
        ORDER BY api_calls DESC LIMIT 50""", ids)
    result = dict(empty, **totals, **tools_row)
    turns, calls = result["turns"], result["api_calls"]
    tokens = result["input_tokens"] + result["output_tokens"]
    result.update(
        api_calls_per_turn=calls / turns if turns else None,
        tokens_per_turn=tokens / turns if turns else None,
        p50_latency_s=percentile(latencies, 0.5), p95_latency_s=percentile(latencies, 0.95),
        error_rate=result["errors"] / calls if calls else None,
        tool_failure_rate=result["tool_failures"] / result["tool_calls"] if result["tool_calls"] else None,
        models=[dict(model=row["model"], api_calls=row["api_calls"]) for row in by_model[:10]])
    if pricing:
        cost, complete = 0.0, True
        for row in by_model:
            value = estimate_cost(pricing, row["model"], row["input_tokens"], row["output_tokens"],
                                  row["cache_read_tokens"])
            if value is None:
                complete = False
            else:
                cost += value
        # A partial estimate is still shown, flagged by cost_complete=False.
        if not calls:
            cost = 0.0
        elif not complete and not cost:
            cost = None
        result.update(cost_usd=cost, cost_complete=complete)
    return result


def session_totals(telemetry_db: Path, session_ids) -> dict:
    """Per Hermes session: API calls, tokens, models and tool outcomes (bench tasks are one session each)."""
    ids = sorted({str(s) for s in session_ids if s})[:2000]
    out: dict = {}
    if not ids or not Path(telemetry_db).exists():
        return out
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for row in _rows(telemetry_db, f"""
            SELECT session_id, COALESCE(response_model, model) AS model, COUNT(*) AS api_calls,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens, COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                   COALESCE(SUM(error_type IS NOT NULL), 0) AS errors
            FROM api_calls WHERE session_id IN ({_marks(chunk)})
            GROUP BY session_id, COALESCE(response_model, model)""", chunk):
            entry = out.setdefault(row["session_id"], dict(api_calls=0, input_tokens=0, output_tokens=0,
                                                           cache_read_tokens=0, errors=0, models={},
                                                           tool_calls=0, tool_failures=0))
            for key in ("api_calls", "input_tokens", "output_tokens", "cache_read_tokens", "errors"):
                entry[key] += row[key] or 0
            entry["models"][row["model"]] = dict(api_calls=row["api_calls"], input_tokens=row["input_tokens"],
                                                 output_tokens=row["output_tokens"],
                                                 cache_read_tokens=row["cache_read_tokens"])
        for row in _rows(telemetry_db, f"""
            SELECT session_id, COUNT(*) AS tool_calls, COALESCE(SUM({TOOL_FAILED}), 0) AS tool_failures
            FROM tool_calls WHERE session_id IN ({_marks(chunk)}) GROUP BY session_id""", chunk):
            entry = out.setdefault(row["session_id"], dict(api_calls=0, input_tokens=0, output_tokens=0,
                                                           cache_read_tokens=0, errors=0, models={},
                                                           tool_calls=0, tool_failures=0))
            entry.update(tool_calls=row["tool_calls"], tool_failures=row["tool_failures"])
        # Routed steps in order; a retried step has several rows and counts once.
        previous = {}
        for row in _safe_rows(telemetry_db, f"""
            SELECT session_id, api_request_id, MIN(route_choice) AS choice, MIN(ts) AS first_ts
            FROM api_calls WHERE session_id IN ({_marks(chunk)}) AND route_choice IS NOT NULL
            GROUP BY session_id, api_request_id ORDER BY session_id, first_ts""", chunk):
            entry = out.get(row["session_id"])
            if entry is None:
                continue
            routing = entry.setdefault("routing", dict(steps=0, cheap=0, switches=0))
            routing["steps"] += 1
            routing["cheap"] += row["choice"] == "cheap"
            if previous.get(row["session_id"]) not in (None, row["choice"]):
                routing["switches"] += 1
            previous[row["session_id"]] = row["choice"]
    return out


# ---- pricing: <assistant_dir>/pricing.yaml -------------------------------------
#
#   anthropic/claude-sonnet-4.5: {input: 3.0, output: 15.0, cache_read: 0.3}
#   gpt-4.1-mini:                {input: 0.4, output: 1.6}
#
# USD per 1M tokens. ``cache_read`` defaults to the input price. A top-level
# ``models:`` mapping is accepted too. Unknown models stay unpriced (None).

MAX_PRICING_BYTES = 65536


def load_pricing(path: Path) -> dict:
    """model -> {input, output, cache_read} USD per 1M tokens; {} when missing or invalid."""
    path = Path(path)
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_PRICING_BYTES:
            return {}
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("models"), dict):
        data = data["models"]
    if not isinstance(data, dict):
        return {}
    prices = {}
    for model, entry in list(data.items())[:500]:
        if not isinstance(model, str) or not model.strip() or len(model) > 160 or not isinstance(entry, dict):
            continue
        values = {}
        for key in ("input", "output", "cache_read"):
            value = entry.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 10000:
                values[key] = float(value)
        if "input" in values and "output" in values:
            values.setdefault("cache_read", values["input"])
            prices[model.strip()] = values
    return prices


def _price_for(pricing: dict, model) -> dict | None:
    if not pricing or not isinstance(model, str) or not model:
        return None
    if model in pricing:
        return pricing[model]
    lowered = {key.casefold(): value for key, value in pricing.items()}
    short = model.casefold().rsplit("/", 1)[-1]
    return lowered.get(model.casefold()) or lowered.get(short) or next(
        (value for key, value in lowered.items() if key.rsplit("/", 1)[-1] == short), None)


def estimate_cost(pricing: dict, model, input_tokens, output_tokens, cache_read_tokens=0):
    """USD estimate for one model's tokens, or None when the model has no price."""
    price = _price_for(pricing, model)
    if price is None:
        return None
    return ((input_tokens or 0) * price["input"] + (output_tokens or 0) * price["output"]
            + (cache_read_tokens or 0) * price["cache_read"]) / 1_000_000
