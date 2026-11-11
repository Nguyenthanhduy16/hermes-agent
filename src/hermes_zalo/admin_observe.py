"""Observe: agent behaviour traces and analytics from assistant-core telemetry.

Two console sections, both read-only:

- ``activity``  recent agent turns (``days``, ``platform``, ``model``, ``status``,
  ``session`` session-id prefix, ``page``) or one turn step by step (``turn``).
- ``analytics`` KPIs, a daily series and per model / tool / platform / run
  tables for one period (``days``), for benchmarking settings over time.

Source: ``<assistant_dir>/telemetry.sqlite3`` written by
``assistant_core.telemetry``. It is opened read-only (``mode=ro``, never
created, never followed through a symlink); only the columns of the current
schema are read and a missing column reads as unknown. Every query is bounded
(``ROW_LIMIT`` rows per table and window) and a snapshot says when it was cut.

Turns. Rows sharing ``(session_id, turn_id)`` form one turn. A tool row without
``turn_id`` joins the turn of the API request (``api_request_id``) that asked
for it. Rows still without a turn are grouped per session (per platform when
the session is unknown) by time: a gap of more than ``TURN_GAP_S`` seconds
starts a new turn. A turn is addressed by its earliest row (``a<id>`` for an
API attempt, ``t<id>`` for a tool call), so the detail view regroups the same
rows and never needs raw ids from the browser.

Costs come only from an optional ``<assistant_dir>/pricing.yaml`` (see
``config/pricing.example.yaml``); without it costs are ``None``, never 0.
Telemetry holds no message text or tool arguments; names and ids are reduced
to a safe character set before they leave this module.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import functools
import math
import random
import re
import sqlite3
import time

import yaml

from .admin_assistant import _Unavailable, _connect_ro, _read_bounded
from .admin_auth import AdminError
from .console_sections import Section, register


PERIODS = (1, 7, 30, 90)
DEFAULT_DAYS = 7
ROW_LIMIT = 20000          # rows per table per window
PAGE_SIZE = 25
TURN_GAP_S = 120.0
DETAIL_BEFORE_S = 3600.0   # the detail view rereads one session around the turn
DETAIL_AFTER_S = 12 * 3600.0
DETAIL_ROW_LIMIT = 2000
STEP_LIMIT = 400
FACET_LIMIT = 40
TABLE_LIMIT = 50
RUN_LIMIT = 20
RUN_META_LIMIT = 200
PRICING_FILE = "pricing.yaml"
PRICING_MAX_BYTES = 256 * 1024
PRICE_MAX = 100000.0
TZ = timezone(timedelta(hours=7))  # day boundaries in Vietnam time, like assistant_core.quota
TZ_LABEL = "UTC+07:00"
OK_TOOL_STATUSES = frozenset({"ok", "success", "succeeded", "completed", "done"})
SOURCE = "assistant-telemetry"
ENABLE_HINT = ("No agent telemetry has been recorded yet. Enable the assistant-core plugin (plugins/assistant-core) "
               "with ASSISTANT_TELEMETRY=true, the default, restart the gateway and send the assistant a message.")

API_COLUMNS = ("id", "run_id", "ts", "platform", "session_id", "turn_id", "api_request_id", "api_call_count",
               "retry_count", "provider", "model", "response_model", "duration_s", "ttfb_s", "finish_reason",
               "error_type", "status_code", "input_tokens", "output_tokens", "cache_read_tokens",
               "cache_write_tokens", "reasoning_tokens", "message_count", "context_length", "tool_call_count",
               "route_policy", "route_choice", "route_reason")
TOOL_COLUMNS = ("id", "run_id", "ts", "platform", "session_id", "turn_id", "api_request_id", "tool_name", "status",
                "error_type", "duration_ms", "blocked")
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")

_NAME_DROP = re.compile(r"[^A-Za-z0-9 _.:/@+\-]")
_IDENT_DROP = re.compile(r"[^A-Za-z0-9_.:\-]")
_TURN = re.compile(r"([at])([0-9]{1,18})")
_QUERY_IDENT = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")


def _now():
    return time.time()


# ---- small helpers ---------------------------------------------------------

def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _name(value, limit=120):
    """Model, tool, platform or error names: a safe character set, bounded, never a path."""
    if value is None or isinstance(value, (bytes, dict, list, tuple, set)):
        return None
    text = " ".join(_NAME_DROP.sub("", str(value)).split())[:limit]
    if text.startswith(("/", "~", ".")):
        return "[hidden]"
    return text or None


def _ident(value, limit=120):
    if value is None or isinstance(value, (bytes, dict, list, tuple, set)):
        return None
    return _IDENT_DROP.sub("", str(value))[:limit] or None


def _short(session):
    if not session:
        return None
    return session if len(session) <= 20 else session[:10] + "…" + session[-6:]


def _iso(ts):
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _day(ts):
    return datetime.fromtimestamp(ts, TZ).date().isoformat()


def _sum(values):
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _rate(part, whole):
    return round(part / whole, 4) if whole else None


def _round(value, digits=3):
    return round(value, digits) if value is not None else None


def percentile(values, p):
    """Linear-interpolated percentile (like numpy's default) of the known values; None when empty."""
    data = sorted(v for v in values if v is not None)
    if not data:
        return None
    k = (len(data) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


def _tool_failed(tool):
    if tool["blocked"]:
        return False
    if tool["error_type"]:
        return True
    status = (tool["status"] or "").lower()
    return bool(status) and status not in OK_TOOL_STATUSES and status != "blocked"


def _section(name, *, available=True, source=SOURCE, message=None, items=None, **extra):
    return {"section": name, "available": available, "source": source, "message": message,
            "items": items if items is not None else [],
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}


# ---- query validation ------------------------------------------------------

def _days(query):
    raw = query.get("days") or str(DEFAULT_DAYS)
    try:
        days = int(raw)
    except ValueError:
        days = 0
    if days not in PERIODS:
        raise AdminError("Choose a period of 1, 7, 30 or 90 days.")
    return days


def _activity_filters(query):
    filters = {"days": _days(query), "platform": None, "model": None, "status": None, "session": None, "page": 1,
               "turn": None}
    platform = (query.get("platform") or "").strip()
    if platform:
        if len(platform) > 40 or _name(platform, 40) != platform:
            raise AdminError("Unknown platform filter.")
        filters["platform"] = platform
    model = (query.get("model") or "").strip()
    if model:
        if _name(model) != model:
            raise AdminError("Unknown model filter.")
        filters["model"] = model
    status = (query.get("status") or "").strip()
    if status:
        if status not in ("ok", "error"):
            raise AdminError("Status must be ok or error.")
        filters["status"] = status
    prefix = (query.get("session") or "").strip()
    if prefix:
        if not _QUERY_IDENT.fullmatch(prefix):
            raise AdminError("Search by the start of a session id (letters, digits, _ . : -).")
        filters["session"] = prefix
    page = (query.get("page") or "1").strip()
    if not page.isdigit() or not 1 <= int(page) <= 100000:
        raise AdminError("Invalid page.")
    filters["page"] = int(page)
    turn = (query.get("turn") or "").strip()
    if turn:
        if not _TURN.fullmatch(turn):
            raise AdminError("Invalid turn reference.")
        filters["turn"] = turn
    return filters


# ---- rows ------------------------------------------------------------------

def _api_event(row):
    ts = _num(row.get("ts"))
    if ts is None:
        return None
    duration = _num(row.get("duration_s"))
    duration = duration if duration is not None and duration >= 0 else None
    ttfb = _num(row.get("ttfb_s"))
    event = {"kind": "api", "id": _int(row.get("id")) or 0, "ts": ts, "end": ts,
             "start": ts - (duration or 0.0), "duration_s": duration,
             "ttfb_s": ttfb if ttfb is not None and ttfb >= 0 else None,
             "_session": row.get("session_id"), "_turn": row.get("turn_id"), "_req": row.get("api_request_id"),
             "_platform": row.get("platform"), "_run": row.get("run_id"),
             "run_id": _ident(row.get("run_id"), 64), "platform": _name(row.get("platform"), 40),
             "session_id": _ident(row.get("session_id")), "provider": _name(row.get("provider"), 80),
             "model": _name(row.get("model")) or _name(row.get("response_model")),
             "response_model": _name(row.get("response_model")),
             "api_call_count": _int(row.get("api_call_count")), "retry_count": _int(row.get("retry_count")),
             "finish_reason": _name(row.get("finish_reason"), 40), "error_type": _name(row.get("error_type"), 80),
             "status_code": _int(row.get("status_code")), "message_count": _int(row.get("message_count")),
             "context_length": _int(row.get("context_length")), "tool_call_count": _int(row.get("tool_call_count")),
             # The router's decision for this step (assistant_core.router), when it made one.
             "route_policy": _name(row.get("route_policy"), 40), "route_choice": _name(row.get("route_choice"), 20),
             "route_reason": _name(row.get("route_reason"), 120)}
    for key in TOKEN_KEYS:
        value = _int(row.get(key))
        event[key] = value if value is None or value >= 0 else None
    return event


def _tool_event(row):
    ts = _num(row.get("ts"))
    if ts is None:
        return None
    duration = _num(row.get("duration_ms"))
    duration = duration if duration is not None and duration >= 0 else None
    return {"kind": "tool", "id": _int(row.get("id")) or 0, "ts": ts, "end": ts,
            "start": ts - (duration or 0.0) / 1000.0, "duration_ms": duration,
            "_session": row.get("session_id"), "_turn": row.get("turn_id"), "_req": row.get("api_request_id"),
            "_platform": row.get("platform"), "_run": row.get("run_id"),
            "run_id": _ident(row.get("run_id"), 64), "platform": _name(row.get("platform"), 40),
            "session_id": _ident(row.get("session_id")), "tool_name": _name(row.get("tool_name"), 80),
            "status": _name(row.get("status"), 40), "error_type": _name(row.get("error_type"), 80),
            "blocked": bool(_int(row.get("blocked")))}


def _cut(api, tools, api_capped, tool_capped):
    """Drop rows older than the oldest row of a capped table so every turn shown is complete."""
    cutoff = None
    if api_capped and api:
        cutoff = min(e["ts"] for e in api)
    if tool_capped and tools:
        oldest = min(e["ts"] for e in tools)
        cutoff = oldest if cutoff is None else max(cutoff, oldest)
    if cutoff is None:
        return api, tools, None
    return [e for e in api if e["ts"] >= cutoff], [e for e in tools if e["ts"] >= cutoff], cutoff


class _DbSource:
    """Bounded read-only queries over telemetry.sqlite3."""
    name = SOURCE

    def __init__(self, db):
        self.db = db
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 500")}
        if "api_calls" not in tables:
            raise _Unavailable("the telemetry file has an unsupported format")
        self.has_tools = "tool_calls" in tables
        self.has_runs = "runs" in tables
        self.select = {"api_calls": self._select("api_calls", API_COLUMNS)}
        if self.has_tools:
            self.select["tool_calls"] = self._select("tool_calls", TOOL_COLUMNS)

    def close(self):
        self.db.close()

    def _select(self, table, columns):
        present = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
        if table == "api_calls":
            self.api_present = present
        if "ts" not in present:
            raise _Unavailable("the telemetry file has an unsupported format")
        # Column names come from the constants above, never from input.
        exprs = [("rowid AS id" if c == "id" and c not in present else c if c in present else f"NULL AS {c}")
                 for c in columns]
        return "SELECT " + ", ".join(exprs) + f" FROM {table}"

    def _rows(self, table, where, args, limit):
        if table not in self.select:
            return []
        cursor = self.db.execute(self.select[table] + " WHERE " + " AND ".join(where) +
                                 " ORDER BY ts DESC, id DESC LIMIT ?", [*args, limit])
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def window(self, since, platform=None, prefix=None):
        where, args = ["ts >= ?"], [since]
        if platform:
            where.append("platform = ?")
            args.append(platform)
        if prefix:
            where.append("substr(session_id, 1, ?) = ?")
            args += [len(prefix), prefix]
        api_rows = self._rows("api_calls", where, args, ROW_LIMIT)
        tool_rows = self._rows("tool_calls", where, args, ROW_LIMIT)
        api = [e for e in map(_api_event, api_rows) if e]
        tools = [e for e in map(_tool_event, tool_rows) if e]
        return _cut(api, tools, len(api_rows) >= ROW_LIMIT, len(tool_rows) >= ROW_LIMIT)

    def facets(self, since):
        platforms, models = set(), set()
        for column, target in (("platform", platforms), ("model", models)):
            if column not in self.api_present:
                continue
            for (value,) in self.db.execute(f"SELECT DISTINCT {column} FROM api_calls WHERE ts >= ? "
                                            f"AND {column} IS NOT NULL LIMIT ?", (since, FACET_LIMIT)):
                cleaned = _name(value, 40 if column == "platform" else 120)
                if cleaned:
                    target.add(cleaned)
        return sorted(platforms), sorted(models)

    def anchor(self, kind, rowid):
        table = "api_calls" if kind == "a" else "tool_calls"
        key = "rowid" if "rowid AS id" in self.select.get(table, "") else "id"
        rows = self._rows(table, [key + " = ?"], [rowid], 1)
        if not rows:
            return None
        return (_api_event if kind == "a" else _tool_event)(rows[0])

    def session_rows(self, anchor, lo, hi):
        where, args = ["ts >= ?", "ts <= ?", "session_id IS ?"], [lo, hi, anchor["_session"]]
        if anchor["_session"] is None:
            where.append("platform IS ?")
            args.append(anchor["_platform"])
        api = [e for e in map(_api_event, self._rows("api_calls", where, args, DETAIL_ROW_LIMIT)) if e]
        tools = [e for e in map(_tool_event, self._rows("tool_calls", where, args, DETAIL_ROW_LIMIT)) if e]
        return api, tools

    def runs(self, run_ids):
        if not self.has_runs or not run_ids:
            return {}
        present = {row[1] for row in self.db.execute("PRAGMA table_info(runs)")}
        wanted = ("run_id", "started_at", "hermes_rev", "config_hash", "label")
        exprs = [c if c in present else f"NULL AS {c}" for c in wanted]
        ids = list(run_ids)[:RUN_META_LIMIT]
        rows = self.db.execute("SELECT " + ", ".join(exprs) + " FROM runs WHERE run_id IN (" +
                               ",".join("?" * len(ids)) + ") LIMIT ?", [*ids, RUN_META_LIMIT]).fetchall()
        return {row[0]: _run_meta(*row) for row in rows if row[0] is not None}


def _run_meta(run_id, started_at, hermes_rev, config_hash, label):
    return {"run_id": _ident(run_id, 64), "started_at": _iso(_num(started_at)), "hermes_rev": _ident(hermes_rev, 40),
            "config_hash": _ident(config_hash, 64), "label": _name(label, 80)}


class _Unreadable(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _open_source(ctx):
    """A _DbSource or _DemoSource; raises _Unreadable with a user-facing, path-free message."""
    if ctx.demo:
        return _DemoSource()
    directory = ctx.assistant_dir
    path = directory / "telemetry.sqlite3"
    if directory.is_symlink() or path.is_symlink():
        raise _Unreadable("The telemetry location is a symbolic link, so it is not read.")
    if not path.exists():
        raise _Unreadable(ENABLE_HINT)
    try:
        db = _connect_ro(path)
    except _Unavailable as exc:
        raise _Unreadable("Telemetry is unavailable: " + str(exc) + ".") from None
    except (OSError, sqlite3.Error):
        raise _Unreadable("Telemetry could not be opened right now. Try again shortly.") from None
    try:
        return _DbSource(db)
    except _Unavailable as exc:
        db.close()
        raise _Unreadable("Telemetry is unavailable: " + str(exc) + ".") from None
    except sqlite3.Error:
        db.close()
        raise _Unreadable("Telemetry could not be read right now. Try again shortly.") from None


# ---- pricing ---------------------------------------------------------------

def _last(name):
    return name.lower().rsplit("/", 1)[-1]


class Pricing:
    """USD per 1M tokens per model; ``cost`` returns None for unpriced models."""

    def __init__(self, rates=None, *, status="missing", message=None, skipped=0):
        self.rates = {k.lower(): v for k, v in (rates or {}).items()}
        tails = {}
        for key, value in self.rates.items():
            tails.setdefault(_last(key), []).append(value)
        self.tails = {k: v[0] for k, v in tails.items() if len(v) == 1}
        self.status, self.message, self.skipped = status, message, skipped

    @property
    def available(self):
        return bool(self.rates)

    def rate(self, model):
        if not model or not self.rates:
            return None
        return self.rates.get(model.lower()) or self.tails.get(_last(model))

    def cost(self, event):
        rate = self.rate(event["model"])
        if rate is None:
            return None
        tokens = {k: event[k] or 0 for k in TOKEN_KEYS}
        total = (tokens["input_tokens"] * rate["input"] + tokens["output_tokens"] * rate["output"]
                 + tokens["cache_read_tokens"] * rate.get("cache_read", rate["input"])
                 + tokens["cache_write_tokens"] * rate.get("cache_write", rate["input"]))
        return total / 1_000_000

    def describe(self, models_seen):
        unpriced = sorted({m for m in models_seen if m and self.rate(m) is None})[:FACET_LIMIT]
        if not self.available:
            status = self.status
        elif not models_seen:
            status = "ok"
        elif len(unpriced) == len({m for m in models_seen if m}):
            status = "unmatched"
        else:
            status = "partial" if unpriced else "ok"
        messages = {
            "missing": "Pricing unavailable: add pricing.yaml next to telemetry (see config/pricing.example.yaml).",
            "invalid": self.message or "pricing.yaml could not be read; costs are not estimated.",
            "unmatched": "pricing.yaml has no prices for the models in this period.",
            "partial": "Some models have no price; estimated cost covers priced models only.",
        }
        return {"status": status, "available": self.available, "models_priced": len(self.rates),
                "unpriced_models": unpriced, "skipped_entries": self.skipped, "message": messages.get(status),
                "unit": "USD per 1M tokens"}


def _rate_entry(value):
    if not isinstance(value, dict):
        return None
    rate = {}
    for key in ("input", "output", "cache_read", "cache_write"):
        raw = value.get(key)
        if raw is None and key in ("cache_read", "cache_write"):
            continue
        number = _num(raw)
        if number is None or not 0 <= number <= PRICE_MAX:
            return None
        rate[key] = number
    return rate


def load_pricing(ctx):
    if ctx.demo:
        return Pricing(DEMO_PRICING, status="ok")
    directory = ctx.assistant_dir
    path = directory / PRICING_FILE
    if directory.is_symlink() or path.is_symlink():
        return Pricing(status="invalid", message="pricing.yaml is a symbolic link, so it is not read.")
    if not path.exists():
        return Pricing(status="missing")
    try:
        data = yaml.safe_load(_read_bounded(path, PRICING_MAX_BYTES).decode("utf-8-sig"))
    except (OSError, _Unavailable, UnicodeDecodeError, yaml.YAMLError):
        return Pricing(status="invalid", message="pricing.yaml could not be read; costs are not estimated.")
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict):
        return Pricing(status="invalid", message="pricing.yaml needs a 'models' mapping; costs are not estimated.")
    rates, skipped = {}, 0
    for key, value in list(models.items())[:1000]:
        rate = _rate_entry(value)
        if isinstance(key, str) and 0 < len(key) <= 120 and rate is not None:
            rates[key.strip()] = rate
        else:
            skipped += 1
    if not rates:
        return Pricing(status="invalid", skipped=skipped,
                       message="pricing.yaml has no valid model prices; costs are not estimated.")
    return Pricing(rates, status="ok", skipped=skipped)


# ---- turns -----------------------------------------------------------------

def group_turns(api, tools):
    """Group events into turns (see the module docstring). Returns lists of events sorted by start."""
    events = sorted([*api, *tools], key=lambda e: (e["start"], e["end"], e["kind"], e["id"]))
    turn_of_request = {(e["_session"], e["_req"]): e["_turn"] for e in api if e["_turn"] is not None and e["_req"]}
    groups, open_gap = {}, {}
    for event in events:
        turn = event["_turn"]
        if turn is None and event["kind"] == "tool" and event["_req"]:
            turn = turn_of_request.get((event["_session"], event["_req"]))
        if turn is not None:
            key, method = ("turn", event["_session"], turn), "turn_id"
        else:
            scope = event["_session"] if event["_session"] is not None else ("platform", event["_platform"])
            previous = open_gap.get(scope)
            if previous and event["start"] - previous[1] <= TURN_GAP_S:
                key = previous[0]
                open_gap[scope] = (key, max(previous[1], event["end"]))
            else:
                key = ("gap", scope, event["kind"], event["id"])
                open_gap[scope] = (key, event["end"])
            method = "time_gap"
        groups.setdefault(key, {"method": method, "events": []})["events"].append(event)
    return list(groups.values())


def _ref(event):
    return ("a" if event["kind"] == "api" else "t") + str(event["id"])


def summarize(group, pricing=None):
    events = group["events"]
    api = [e for e in events if e["kind"] == "api"]
    tools = [e for e in events if e["kind"] == "tool"]
    start, end = min(e["start"] for e in events), max(e["end"] for e in events)
    models = list(dict.fromkeys(e["model"] for e in api if e["model"]))
    api_errors = [e for e in api if e["error_type"]]
    failed_tools = [t for t in tools if _tool_failed(t)]
    blocked = sum(1 for t in tools if t["blocked"])
    last_api = max(api, key=lambda e: (e["end"], e["id"])) if api else None
    if last_api is not None and last_api["error_type"]:
        status = "failed"
    elif api_errors or failed_tools or blocked:
        status = "issues"
    else:
        status = "ok"
    session = next((e["session_id"] for e in events if e["session_id"]), None)
    summary = {
        "id": _ref(events[0]), "started_at": _iso(start), "ended_at": _iso(end), "start_ts": round(start, 3),
        "platform": next((e["platform"] for e in events if e["platform"]), None),
        "session": _short(session), "session_id": session, "models": models,
        "api_calls": len(api), "tool_calls": len(tools),
        "duration_s": _round(end - start), "model_time_s": _round(_sum(e["duration_s"] for e in api)),
        "tool_time_s": _round(_sum((t["duration_ms"] / 1000.0 if t["duration_ms"] is not None else None)
                                   for t in tools)),
        "retries": sum(1 for e in api if (e["retry_count"] or 0) > 0),
        "api_errors": len(api_errors), "tool_failures": len(failed_tools), "blocked": blocked,
        "error_types": list(dict.fromkeys([*(e["error_type"] for e in api_errors),
                                           *(t["error_type"] or t["status"] for t in failed_tools)]))[:5],
        "status": status, "grouping": group["method"],
        "final_model": last_api["model"] if last_api else None,
        "finish_reason": last_api["finish_reason"] if last_api else None,
    }
    for key in TOKEN_KEYS:
        summary[key] = _sum(e[key] for e in api)
    if pricing is not None and pricing.available:
        costs = [pricing.cost(e) for e in api]
        summary["cost_usd"] = _round(_sum(costs), 6)
        summary["cost_partial"] = any(c is None and e["model"] for c, e in zip(costs, api))
    else:
        summary["cost_usd"], summary["cost_partial"] = None, False
    return summary


def _step(event, origin, pricing, request_steps):
    duration_s = (event["duration_s"] if event["kind"] == "api"
                  else event["duration_ms"] / 1000.0 if event["duration_ms"] is not None else None)
    step = {"id": _ref(event), "kind": event["kind"], "at": _iso(event["end"]),
            "offset_ms": round((event["start"] - origin) * 1000, 1),
            "duration_ms": round(duration_s * 1000, 1) if duration_s is not None else None,
            "platform": event["platform"], "run_id": event["run_id"], "error_type": event["error_type"]}
    if event["kind"] == "api":
        step.update({k: event[k] for k in ("provider", "model", "response_model", "api_call_count", "retry_count",
                                            "finish_reason", "status_code", "message_count", "context_length",
                                            "tool_call_count", "route_policy", "route_choice", "route_reason",
                                            *TOKEN_KEYS)})
        step["ttfb_ms"] = round(event["ttfb_s"] * 1000, 1) if event["ttfb_s"] is not None else None
        step["cost_usd"] = _round(pricing.cost(event), 6) if pricing is not None and pricing.available else None
        step["status"] = "error" if event["error_type"] else "ok"
    else:
        step.update({"tool_name": event["tool_name"], "tool_status": event["status"], "blocked": event["blocked"],
                     "requested_by": request_steps.get(event["_req"]) if event["_req"] else None,
                     "status": "blocked" if event["blocked"] else "error" if _tool_failed(event) else "ok"})
    return step


# ---- sections --------------------------------------------------------------

def _unavailable(name, message, **extra):
    return _section(name, available=False, message=message, **extra)


def activity_snapshot(ctx, query):
    filters = _activity_filters(query)
    now = DEMO_NOW if ctx.demo else _now()
    base = {"days": filters["days"], "filters": {k: v for k, v in filters.items() if k != "turn"},
            "timezone": TZ_LABEL, "gap_seconds": TURN_GAP_S, "row_limit": ROW_LIMIT}
    try:
        source = _open_source(ctx)
    except _Unreadable as exc:
        return _unavailable("activity", exc.message, view="detail" if filters["turn"] else "list", total=0,
                            page=1, limit=PAGE_SIZE, facets={"platforms": [], "models": []}, summary={}, **base)
    try:
        with closing(source):
            pricing = load_pricing(ctx)
            if filters["turn"]:
                return _detail(source, filters["turn"], pricing, base)
            return _turn_list(source, filters, now, pricing, base)
    except sqlite3.Error:
        return _unavailable("activity", "Telemetry could not be read right now. Try again shortly.",
                            view="list", total=0, page=1, limit=PAGE_SIZE, facets={"platforms": [], "models": []},
                            summary={}, **base)


def _turn_list(source, filters, now, pricing, base):
    since = now - filters["days"] * 86400
    api, tools, cutoff = source.window(since, filters["platform"], filters["session"])
    platforms, models = source.facets(since)
    turns = [summarize(g, pricing) for g in group_turns(api, tools)]
    if filters["model"]:
        turns = [t for t in turns if filters["model"] in t["models"]]
    if filters["status"] == "ok":
        turns = [t for t in turns if t["status"] == "ok"]
    elif filters["status"] == "error":
        turns = [t for t in turns if t["status"] != "ok"]
    turns.sort(key=lambda t: (t["start_ts"], t["id"]), reverse=True)
    total = len(turns)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(filters["page"], pages)
    durations = [t["duration_s"] for t in turns]
    summary = {"turns": total, "failed": sum(t["status"] == "failed" for t in turns),
               "issues": sum(t["status"] == "issues" for t in turns),
               "duration_p50_s": _round(percentile(durations, 50)), "duration_p95_s": _round(percentile(durations, 95)),
               "api_calls": sum(t["api_calls"] for t in turns), "tool_calls": sum(t["tool_calls"] for t in turns),
               "input_tokens": _sum(t["input_tokens"] for t in turns),
               "output_tokens": _sum(t["output_tokens"] for t in turns)}
    return _section("activity", source=source.name, items=turns[(page - 1) * PAGE_SIZE: page * PAGE_SIZE],
                    view="list", total=total, page=page, limit=PAGE_SIZE, summary=summary,
                    facets={"platforms": platforms, "models": models},
                    truncated=cutoff is not None, covered_since=_iso(cutoff), **base)


def _detail(source, ref, pricing, base):
    match = _TURN.fullmatch(ref)
    anchor = source.anchor(match.group(1), int(match.group(2)))
    missing = _section("activity", source=source.name, view="detail", turn=None, total=0, page=1, limit=PAGE_SIZE,
                       message="This turn is no longer in telemetry. Go back to the list and pick another one.",
                       **base)
    if anchor is None:
        return missing
    api, tools = source.session_rows(anchor, anchor["start"] - DETAIL_BEFORE_S, anchor["ts"] + DETAIL_AFTER_S)
    group = next((g for g in group_turns(api, tools)
                  if any(e["kind"] == anchor["kind"] and e["id"] == anchor["id"] for e in g["events"])), None)
    if group is None:
        return missing
    summary = summarize(group, pricing)
    events = group["events"]
    origin = min(e["start"] for e in events)
    request_steps, steps = {}, []
    for index, event in enumerate(events[:STEP_LIMIT]):
        if event["kind"] == "api" and event["_req"] and not event["error_type"]:
            request_steps[event["_req"]] = index
        steps.append(_step(event, origin, pricing, request_steps))
    runs = source.runs({e["_run"] for e in events if e["_run"] is not None})
    summary["runs"] = sorted(runs.values(), key=lambda r: r["started_at"] or "")
    summary["prompt_tokens"] = _sum([summary["input_tokens"], summary["cache_read_tokens"],
                                     summary["cache_write_tokens"]])
    summary["span_ms"] = round((max(e["end"] for e in events) - origin) * 1000, 1)
    return _section("activity", source=source.name, items=steps, view="detail", turn=summary,
                    steps_truncated=len(events) > STEP_LIMIT, total=len(steps), page=1, limit=STEP_LIMIT,
                    pricing=pricing.describe(summary["models"]), **base)


def _stats(api, pricing):
    """Shared per-group API figures."""
    ok = [e for e in api if not e["error_type"]]
    errors = len(api) - len(ok)
    tokens = {k: _sum(e[k] for e in api) for k in TOKEN_KEYS}
    prompt = _sum([tokens["input_tokens"], tokens["cache_read_tokens"], tokens["cache_write_tokens"]])
    costs = [pricing.cost(e) for e in api] if pricing.available else []
    priced = [c for c in costs if c is not None]
    return {
        "api_calls": len(api), "errors": errors, "error_rate": _rate(errors, len(api)),
        "retries": sum(1 for e in api if (e["retry_count"] or 0) > 0),
        **tokens, "prompt_tokens": prompt, "total_tokens": _sum([prompt, tokens["output_tokens"]]),
        "cache_hit_rate": _rate(tokens["cache_read_tokens"] or 0, prompt) if prompt else None,
        "latency_p50_s": _round(percentile([e["duration_s"] for e in ok], 50)),
        "latency_p95_s": _round(percentile([e["duration_s"] for e in ok], 95)),
        "ttfb_p50_s": _round(percentile([e["ttfb_s"] for e in ok], 50)),
        "ttfb_avg_s": _round(_mean([e["ttfb_s"] for e in ok])),
        "cost_usd": _round(sum(priced), 6) if priced else None,
        "cost_partial": bool(priced) and len(priced) < sum(1 for e in api if e["model"]),
    }


def _mean(values):
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def _by(events, key):
    groups = {}
    for event in events:
        groups.setdefault(event[key], []).append(event)
    return groups


def analytics_snapshot(ctx, query):
    unknown = set(query) - {"days"}
    if unknown:
        raise AdminError("Unsupported filter for this page.")
    days = _days(query)
    now = DEMO_NOW if ctx.demo else _now()
    base = {"days": days, "timezone": TZ_LABEL, "row_limit": ROW_LIMIT, "gap_seconds": TURN_GAP_S}
    try:
        source = _open_source(ctx)
    except _Unreadable as exc:
        return _unavailable("analytics", exc.message, kpis={}, daily=[], models=[], tools=[], platforms=[], runs=[],
                            pricing=None, truncated=False, **base)
    try:
        with closing(source):
            pricing = load_pricing(ctx)
            since = now - days * 86400
            api, tools, cutoff = source.window(since)
            result = _analytics(api, tools, cutoff, since, now, pricing, source)
    except sqlite3.Error:
        return _unavailable("analytics", "Telemetry could not be read right now. Try again shortly.", kpis={},
                            daily=[], models=[], tools=[], platforms=[], runs=[], pricing=None, truncated=False,
                            **base)
    return _section("analytics", source=source.name, items=result["models"], **result, **base)


def _analytics(api, tools, cutoff, since, now, pricing, source):
    turns = [summarize(g) for g in group_turns(api, tools)]
    kpis = _stats(api, pricing)
    turn_durations = [t["duration_s"] for t in turns]
    kpis.update({
        "turns": len(turns), "failed_turns": sum(t["status"] == "failed" for t in turns),
        "turn_latency_p50_s": _round(percentile(turn_durations, 50)),
        "turn_latency_p95_s": _round(percentile(turn_durations, 95)),
        "steps_per_turn": _round(len(api) / len(turns), 2) if turns else None,
        "tool_calls": len(tools), "tool_failures": sum(1 for t in tools if _tool_failed(t)),
        "blocked_tools": sum(1 for t in tools if t["blocked"]),
    })

    first_day = datetime.fromtimestamp(since, TZ).date()
    last_day = datetime.fromtimestamp(now, TZ).date()
    cutoff_day = _day(cutoff) if cutoff is not None else None
    api_days, tool_days = _by([dict(e, day=_day(e["ts"])) for e in api], "day"), {}
    for tool in tools:
        tool_days[_day(tool["ts"])] = tool_days.get(_day(tool["ts"]), 0) + 1
    turn_days = {}
    for turn in turns:
        day = _day(turn["start_ts"])
        turn_days[day] = turn_days.get(day, 0) + 1
    daily, day = [], first_day
    while day <= last_day:
        key = day.isoformat()
        rows = api_days.get(key, [])
        partial = cutoff_day is not None and key <= cutoff_day
        tokens = {k: _sum(e[k] for e in rows) for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                                                       "cache_write_tokens")}
        costs = [c for c in (pricing.cost(e) for e in rows) if c is not None] if pricing.available else []
        daily.append({"day": key, "api_calls": len(rows), "errors": sum(1 for e in rows if e["error_type"]),
                      "turns": turn_days.get(key, 0), "tool_calls": tool_days.get(key, 0), **tokens,
                      "total_tokens": _sum(tokens.values()), "cost_usd": _round(sum(costs), 6) if costs else None,
                      "partial": partial})
        day += timedelta(days=1)

    models = []
    for model, rows in _by(api, "model").items():
        models.append({"model": model, "providers": sorted({e["provider"] for e in rows if e["provider"]})[:5],
                       "priced": pricing.rate(model) is not None, **_stats(rows, pricing)})
    models.sort(key=lambda m: (-m["api_calls"], m["model"] or ""))

    tool_rows = []
    for name, rows in _by(tools, "tool_name").items():
        durations = [t["duration_ms"] for t in rows]
        failed = sum(1 for t in rows if _tool_failed(t))
        tool_rows.append({"tool": name, "calls": len(rows), "failures": failed, "failure_rate": _rate(failed, len(rows)),
                          "blocked": sum(1 for t in rows if t["blocked"]),
                          "duration_avg_ms": _round(_mean(durations), 1),
                          "duration_p95_ms": _round(percentile(durations, 95), 1)})
    tool_rows.sort(key=lambda t: (-t["calls"], t["tool"] or ""))

    platform_turns = _by(turns, "platform")
    platform_tools = _by(tools, "platform")
    platforms = []
    for platform in sorted({*_by(api, "platform"), *platform_turns, *platform_tools}, key=lambda p: p or ""):
        rows = [e for e in api if e["platform"] == platform]
        p_turns = platform_turns.get(platform, [])
        platforms.append({"platform": platform, "turns": len(p_turns),
                          "tool_calls": len(platform_tools.get(platform, [])),
                          "turn_latency_p50_s": _round(percentile([t["duration_s"] for t in p_turns], 50)),
                          **_stats(rows, pricing)})
    platforms.sort(key=lambda p: (-p["api_calls"], p["platform"] or ""))

    by_run = _by([e for e in api if e["_run"] is not None], "_run")
    recent = sorted(by_run, key=lambda r: max(e["ts"] for e in by_run[r]), reverse=True)[:RUN_LIMIT]
    meta = source.runs(recent)
    runs = []
    for run in recent:
        rows = by_run[run]
        info = meta.get(run) or {"run_id": _ident(run, 64), "started_at": None, "hermes_rev": None,
                                 "config_hash": None, "label": None}
        runs.append({**info, "first_seen": _iso(min(e["ts"] for e in rows)), "last_seen": _iso(max(e["ts"] for e in rows)),
                     "turns": len({_ref(g["events"][0]) for g in group_turns(rows, [])}), **_stats(rows, pricing)})

    return {"kpis": kpis, "daily": daily, "models": models[:TABLE_LIMIT], "tools": tool_rows[:TABLE_LIMIT],
            "platforms": platforms[:TABLE_LIMIT], "runs": runs, "pricing": pricing.describe([e["model"] for e in api]),
            "truncated": cutoff is not None, "covered_since": _iso(cutoff),
            "models_truncated": len(models) > TABLE_LIMIT, "tools_truncated": len(tool_rows) > TABLE_LIMIT}


# ---- demo ------------------------------------------------------------------

DEMO_NOW = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc).timestamp()
DEMO_SOURCE = "demo"
DEMO_PRICING = {
    "google/gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cache_read": 0.075},
    "openai/gpt-4.1-mini": {"input": 0.40, "output": 1.60, "cache_read": 0.10},
    "anthropic/claude-sonnet-4.5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "qwen/qwen3-32b": {"input": 0.10, "output": 0.30},
}
DEMO_RUNS = (
    ("20260831-090000-b45e1a", DEMO_NOW - 61 * 86400, "a1b2c3d4e5", "3f9c0d12aa7e4b01", "baseline"),
    ("20261019-083000-7c21fd", DEMO_NOW - 12 * 86400, "a1b2c3d4e5", "9e4410bc35d2f7a8", "router-v1"),
)
FLASH, MINI, SONNET, QWEN = DEMO_PRICING


class _DemoSource:
    """Deterministic synthetic telemetry; never touches disk."""
    name = DEMO_SOURCE

    def __init__(self):
        self.api, self.tools = _demo_rows()

    def close(self):
        pass

    @staticmethod
    def _keep(event, since, platform, prefix):
        return (event["ts"] >= since and (not platform or event["_platform"] == platform)
                and (not prefix or (event["_session"] or "").startswith(prefix)))

    def window(self, since, platform=None, prefix=None):
        return ([e for e in self.api if self._keep(e, since, platform, prefix)],
                [e for e in self.tools if self._keep(e, since, platform, prefix)], None)

    def facets(self, since):
        rows = [e for e in self.api if e["ts"] >= since]
        return sorted({e["platform"] for e in rows if e["platform"]}), sorted({e["model"] for e in rows if e["model"]})

    def anchor(self, kind, rowid):
        rows = self.api if kind == "a" else self.tools
        return next((e for e in rows if e["id"] == rowid), None)

    def session_rows(self, anchor, lo, hi):
        def keep(e):
            return (lo <= e["ts"] <= hi and e["_session"] == anchor["_session"]
                    and (anchor["_session"] is not None or e["_platform"] == anchor["_platform"]))
        return [e for e in self.api if keep(e)], [e for e in self.tools if keep(e)]

    def runs(self, run_ids):
        return {r[0]: _run_meta(*r) for r in DEMO_RUNS if r[0] in run_ids}


class _DemoBuilder:
    def __init__(self):
        self.api, self.tools, self.turn = [], [], 0

    def call(self, t, *, platform, session, turn, model, duration, ttfb=None, tokens=(0, 0, 0, 0), finish="stop",
             error=None, status=None, retry=0, request=None, tool_calls=0, run=None, step=1, messages=4):
        inp, out, cache_read, cache_write = tokens
        self.api.append({
            "id": len(self.api) + 1, "run_id": run, "ts": t + duration, "platform": platform, "session_id": session,
            "turn_id": turn, "api_request_id": request, "api_call_count": step, "retry_count": retry,
            "provider": model.split("/")[0], "model": model, "response_model": None if error else model,
            "duration_s": duration, "ttfb_s": ttfb, "finish_reason": None if error else finish, "error_type": error,
            "status_code": status, "input_tokens": None if error else inp, "output_tokens": None if error else out,
            "cache_read_tokens": None if error else cache_read, "cache_write_tokens": None if error else cache_write,
            "reasoning_tokens": None if error else (out // 4 if "sonnet" in model else 0),
            "message_count": messages, "context_length": 200000 if "sonnet" in model else 128000,
            "tool_call_count": None if error else tool_calls})
        return t + duration

    def tool(self, t, *, platform, session, turn, name, duration_ms, request=None, status="ok", error=None,
             blocked=False, run=None):
        self.tools.append({
            "id": len(self.tools) + 1, "run_id": run, "ts": t + duration_ms / 1000.0, "platform": platform,
            "session_id": session, "turn_id": turn, "api_request_id": request, "tool_name": name, "status": status,
            "error_type": error, "duration_ms": duration_ms, "blocked": int(blocked)})
        return t + duration_ms / 1000.0


SESSIONS = {"telegram": ("20261031_065512_tg8f31c2", "20261030_201004_tg51aa90", "20261027_090233_tg0c77e1"),
            "zalo": ("20261031_071845_zl4b2d6e", "20261029_111530_zl93fe02", "20261025_163011_zl2a6c48"),
            "web": ("20261031_073002_web1e5d0",)}


@functools.lru_cache(maxsize=1)
def _demo_rows():
    b, rng = _DemoBuilder(), random.Random(20261031)
    baseline, router = DEMO_RUNS[0][0], DEMO_RUNS[1][0]

    # Hand-made recent turns so every case is visible in the last day.
    t = DEMO_NOW - 40 * 60  # telegram: multi-step with search, a failed extract and a stronger final model
    kw = dict(platform="telegram", session=SESSIONS["telegram"][0], turn="turn-demo-1", run=router)
    t = b.call(t, model=FLASH, duration=1.42, ttfb=0.38, tokens=(2150, 96, 0, 0), finish="tool_calls",
               request="req-d1-1", tool_calls=2, step=1, **kw)
    t = b.tool(t + 0.02, name="web_search", duration_ms=840, request="req-d1-1", **kw)
    t = b.tool(t + 0.01, name="web_extract", duration_ms=5000, request="req-d1-1", status="error",
               error="TimeoutError", **kw)
    t = b.call(t + 0.05, model=FLASH, duration=1.9, ttfb=0.41, tokens=(1480, 120, 2048, 0), finish="tool_calls",
               request="req-d1-2", tool_calls=1, step=2, messages=7, **kw)
    t = b.tool(t + 0.02, name="web_search", duration_ms=910, request="req-d1-2", **kw)
    b.call(t + 0.05, model=SONNET, duration=6.8, ttfb=1.25, tokens=(3920, 640, 0, 3584), request="req-d1-3",
           step=3, messages=9, **kw)

    t = DEMO_NOW - 28 * 60  # zalo: rate limited, retried, then a memory lookup
    kw = dict(platform="zalo", session=SESSIONS["zalo"][0], turn="turn-demo-2", run=router)
    t = b.call(t, model=MINI, duration=0.31, error="RateLimitError", status=429, request="req-d2-1", step=1, **kw)
    t = b.call(t + 2.0, model=MINI, duration=1.6, ttfb=0.52, tokens=(1830, 64, 1024, 0), finish="tool_calls",
               retry=1, request="req-d2-1", tool_calls=1, step=1, **kw)
    t = b.tool(t + 0.02, name="memory", duration_ms=45, request="req-d2-1", **kw)
    b.call(t + 0.03, model=MINI, duration=2.4, ttfb=0.47, tokens=(950, 310, 1920, 0), request="req-d2-2", step=2,
           messages=6, **kw)

    t = DEMO_NOW - 17 * 60  # telegram: a vetoed host tool
    kw = dict(platform="telegram", session=SESSIONS["telegram"][0], turn="turn-demo-3", run=router)
    t = b.call(t, model=FLASH, duration=1.1, ttfb=0.33, tokens=(2600, 80, 1536, 0), finish="tool_calls",
               request="req-d3-1", tool_calls=1, step=1, **kw)
    t = b.tool(t + 0.01, name="terminal", duration_ms=2, request="req-d3-1", status="blocked", blocked=True, **kw)
    b.call(t + 0.03, model=FLASH, duration=2.2, ttfb=0.36, tokens=(820, 260, 2560, 0), request="req-d3-2", step=2,
           messages=6, **kw)

    t = DEMO_NOW - 9 * 60  # zalo: the provider timed out twice; the turn failed
    kw = dict(platform="zalo", session=SESSIONS["zalo"][1], turn="turn-demo-4", run=router)
    t = b.call(t, model=SONNET, duration=60.0, error="APITimeoutError", request="req-d4-1", step=1, **kw)
    b.call(t + 4.0, model=SONNET, duration=60.0, error="APITimeoutError", retry=1, request="req-d4-1", step=1, **kw)

    t = DEMO_NOW - 4 * 60  # web: an older plugin without turn ids (grouped by time gap)
    kw = dict(platform="web", session=SESSIONS["web"][0], turn=None, run=router)
    t = b.call(t, model=QWEN, duration=0.9, ttfb=0.21, tokens=(640, 48, 0, 0), finish="tool_calls",
               tool_calls=1, step=1, **kw)
    t = b.tool(t + 0.01, name="session_search", duration_ms=120, **kw)
    b.call(t + 0.02, model=QWEN, duration=1.3, ttfb=0.22, tokens=(820, 190, 0, 0), step=2, **kw)

    # Background history: 60 days, denser in the last month; router-v1 for the last 12 days.
    tools = ("web_search", "memory", "session_search", "web_extract", "send_message", "todo")
    for day in range(60):
        for index in range(rng.randint(5, 12) if day < 30 else rng.randint(0, 3)):
            platform = rng.choice(("telegram", "telegram", "zalo", "zalo", "web"))
            session = rng.choice(SESSIONS[platform])
            run = router if day < 12 else baseline
            turn = f"turn-{day:02d}-{index:02d}"
            kw = dict(platform=platform, session=session, turn=turn, run=run)
            t = DEMO_NOW - 3600 - day * 86400 - rng.uniform(0, 80000)
            steps = rng.choice((1, 1, 2, 2, 3, 4))
            context = rng.randint(900, 3000)
            for step in range(1, steps + 1):
                last = step == steps
                model = (SONNET if last and rng.random() < 0.4 else MINI if last else FLASH) if run == router else (
                    SONNET if rng.random() < 0.8 else MINI)
                request = f"req-{day}-{index}-{step}"
                retry = 0
                if rng.random() < 0.05:
                    t = b.call(t, model=model, duration=round(rng.uniform(0.2, 0.6), 2), error="RateLimitError",
                               status=429, request=request, step=step, **kw) + 1.5
                    retry = 1
                cached = rng.choice((0, 0, 1024, 2048)) if step > 1 or rng.random() < 0.5 else 0
                calls = 0 if last else rng.randint(1, 2)
                duration = round(rng.uniform(0.8, 3.0) * (2.4 if model == SONNET else 1.0), 2)
                t = b.call(t, model=model, duration=duration, ttfb=round(duration * rng.uniform(0.15, 0.35), 2),
                           tokens=(context, rng.randint(40, 120) if calls else rng.randint(150, 700), cached,
                                   1024 if model == SONNET and step == 1 else 0),
                           finish="tool_calls" if calls else "stop", retry=retry, request=request,
                           tool_calls=calls, step=step, messages=2 + step * 2, **kw)
                for _ in range(calls):
                    name = rng.choice(tools)
                    failed = name == "web_extract" and rng.random() < 0.25
                    t = b.tool(t + 0.02, name=name, duration_ms=round(rng.uniform(20, 2500 if "web" in name else 300)),
                               request=request, status="error" if failed else "ok",
                               error="TimeoutError" if failed else None, **kw)
                context += rng.randint(200, 900)
    api = tuple(e for e in map(_api_event, b.api) if e)
    tools = tuple(e for e in map(_tool_event, b.tools) if e)
    return api, tools


register(Section("activity", activity_snapshot,
                 query_keys=frozenset({"days", "platform", "model", "status", "session", "page", "turn"})))
register(Section("analytics", analytics_snapshot, query_keys=frozenset({"days"})))
