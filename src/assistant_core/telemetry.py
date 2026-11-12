"""Per-API-attempt and per-tool-call log for the SCER-AH router research.

One row per physical provider attempt (retries included) with model, token
buckets (cache read/write kept separate), latency and outcome, plus one row
per tool call. Rows carry the run metadata (Hermes revision, config hash) so
baselines and ablations can be told apart. No message text or tool arguments
are stored: only sizes, names and status.

When the router (assistant_core.router) decided the step, the row also holds
that decision: ``model`` is the model the call was sent to, ``default_model``
the session model Hermes would have used, and the ``route_*`` columns the
policy, its choice, reason, score and the features it saw.
"""

import json
from pathlib import Path
import re
import sqlite3
from threading import RLock
import time
from urllib.parse import urlsplit


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started_at REAL NOT NULL, hermes_rev TEXT, config_hash TEXT, label TEXT
);
CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL NOT NULL,
    platform TEXT, session_id TEXT, task_id TEXT, turn_id TEXT, api_request_id TEXT,
    api_call_count INTEGER, retry_count INTEGER, provider TEXT, model TEXT, response_model TEXT,
    duration_s REAL, ttfb_s REAL, finish_reason TEXT, error_type TEXT, status_code INTEGER,
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
    reasoning_tokens INTEGER, message_count INTEGER, context_length INTEGER, tool_call_count INTEGER,
    default_model TEXT, route_policy TEXT, route_choice TEXT, route_reason TEXT, route_score REAL,
    route_switched INTEGER, route_features TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL NOT NULL,
    platform TEXT, session_id TEXT, task_id TEXT, turn_id TEXT, api_request_id TEXT,
    tool_name TEXT, status TEXT, error_type TEXT, duration_ms REAL, blocked INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS api_calls_session ON api_calls (session_id, ts);
CREATE INDEX IF NOT EXISTS tool_calls_session ON tool_calls (session_id, ts);
CREATE TABLE IF NOT EXISTS config_snapshots (
    config_hash TEXT PRIMARY KEY, created_at REAL NOT NULL, snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS router_runs (
    run_id TEXT PRIMARY KEY, policy TEXT NOT NULL, settings_json TEXT NOT NULL
);
"""
# Columns added after the first release; an older store gets them on open.
ROUTE_COLUMNS = (("default_model", "TEXT"), ("route_policy", "TEXT"), ("route_choice", "TEXT"),
                 ("route_reason", "TEXT"), ("route_score", "REAL"), ("route_switched", "INTEGER"),
                 ("route_features", "TEXT"))


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _num(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _text(value, limit=200):
    return str(value)[:limit] if value not in (None, "") else None


class TelemetryStore:
    def __init__(self, path: Path, *, run_id: str, hermes_rev: str = "", config_hash: str = "", label: str = ""):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._lock = RLock()
        self._pending: dict[str, dict] = {}
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        present = {row[1] for row in self.db.execute("PRAGMA table_info(api_calls)")}
        for name, kind in ROUTE_COLUMNS:
            if name not in present:
                self.db.execute(f"ALTER TABLE api_calls ADD COLUMN {name} {kind}")
        self.db.execute("INSERT OR IGNORE INTO runs (run_id, started_at, hermes_rev, config_hash, label) "
                        "VALUES (?, ?, ?, ?, ?)", (run_id, time.time(), hermes_rev, config_hash, label))

    def pre_api_request(self, **kw):
        request_id = kw.get("api_request_id")
        if request_id:
            with self._lock:
                self._pending[str(request_id)] = {"retry_count": _int(kw.get("retry_count"))}
                if len(self._pending) > 1000:  # bound memory if post hooks never arrive
                    self._pending.pop(next(iter(self._pending)))

    def post_api_request(self, **kw):
        usage = kw.get("usage") if isinstance(kw.get("usage"), dict) else {}
        started, first = _num(kw.get("started_at")), _num(kw.get("first_chunk_at"))
        self._api_row(kw, usage=usage, duration=_num(kw.get("api_duration")),
                      ttfb=(first - started) if started is not None and first is not None else None,
                      finish=_text(kw.get("finish_reason"), 40), error=None, status=None,
                      tools=_int(kw.get("assistant_tool_call_count")))

    def api_request_error(self, **kw):
        error = kw.get("error")
        self._api_row(kw, usage={}, duration=_num(kw.get("api_duration")), ttfb=None, finish=None,
                      error=_text(kw.get("error_type") or (type(error).__name__ if error else "error"), 80),
                      status=_int(kw.get("status_code")), tools=None)

    def _api_row(self, kw, *, usage, duration, ttfb, finish, error, status, tools):
        route = kw.get("route") if isinstance(kw.get("route"), dict) else {}
        features = route.get("features")
        with self._lock:
            pending = self._pending.pop(str(kw.get("api_request_id")), {})
            self.db.execute(
                "INSERT INTO api_calls (run_id, ts, platform, session_id, task_id, turn_id, api_request_id, "
                "api_call_count, retry_count, provider, model, response_model, duration_s, ttfb_s, finish_reason, "
                "error_type, status_code, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
                "reasoning_tokens, message_count, context_length, tool_call_count, default_model, route_policy, "
                "route_choice, route_reason, route_score, route_switched, route_features) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.run_id, time.time(), _text(kw.get("platform"), 40), _text(kw.get("session_id")),
                 _text(kw.get("task_id")), _text(kw.get("turn_id")), _text(kw.get("api_request_id")),
                 _int(kw.get("api_call_count")), _int(kw.get("retry_count")) if kw.get("retry_count") is not None
                 else pending.get("retry_count"),
                 _text(kw.get("provider"), 80), _text(route.get("model") or kw.get("model"), 120),
                 _text(kw.get("response_model"), 120),
                 duration, ttfb, finish, error, status,
                 _int(usage.get("input_tokens")), _int(usage.get("output_tokens")),
                 _int(usage.get("cache_read_tokens")), _int(usage.get("cache_write_tokens")),
                 _int(usage.get("reasoning_tokens")), _int(kw.get("message_count")),
                 _int(kw.get("context_length")), tools,
                 _text(route.get("default_model"), 120) if route else None, _text(route.get("policy"), 40),
                 _text(route.get("choice"), 20), _text(route.get("reason"), 120), _num(route.get("score")),
                 int(bool(route.get("switched"))) if route else None,
                 json.dumps(features, sort_keys=True, ensure_ascii=False)[:2000]
                 if isinstance(features, dict) else None))

    def tool_call(self, *, blocked=False, **kw):
        with self._lock:
            self.db.execute(
                "INSERT INTO tool_calls (run_id, ts, platform, session_id, task_id, turn_id, api_request_id, "
                "tool_name, status, error_type, duration_ms, blocked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.run_id, time.time(), _text(kw.get("platform"), 40), _text(kw.get("session_id")),
                 _text(kw.get("task_id")), _text(kw.get("turn_id")), _text(kw.get("api_request_id")),
                 _text(kw.get("tool_name"), 80), _text(kw.get("status"), 40), _text(kw.get("error_type"), 80),
                 _num(kw.get("duration_ms")), int(bool(blocked))))

    def save_router(self, settings: dict) -> None:
        """Store the router settings of this run (policy, models, seed...); no secrets are in them."""
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO router_runs (run_id, policy, settings_json) VALUES (?, ?, ?)",
                            (self.run_id, str(settings.get("policy"))[:40],
                             json.dumps(settings, sort_keys=True, ensure_ascii=False)))

    def save_snapshot(self, config_hash: str, snapshot: dict) -> None:
        """Store the secret-free settings summary once per config hash (first writer wins)."""
        if not config_hash or not isinstance(snapshot, dict):
            return
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO config_snapshots (config_hash, created_at, snapshot_json) "
                            "VALUES (?, ?, ?)", (str(config_hash)[:64], time.time(),
                                                 json.dumps(snapshot, sort_keys=True, ensure_ascii=False)))

    def summary(self) -> dict:
        with self._lock:
            row = self.db.execute(
                "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cache_read_tokens), "
                "SUM(error_type IS NOT NULL) FROM api_calls WHERE run_id=?", (self.run_id,)).fetchone()
        return dict(zip(("api_calls", "input_tokens", "output_tokens", "cache_read_tokens", "errors"), row))

    def close(self):
        with self._lock:
            self.db.close()


def config_hash(config: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---- settings snapshots ------------------------------------------------------
#
# A run is only comparable when the settings it used are known. The snapshot is
# built from an explicit allowlist: anything not named here (API keys, tokens,
# prompts, personalities, channel ids, custom headers, URLs with paths or
# credentials) is never copied. Values are bounded scalars or short lists.

SNAPSHOT_VERSION = 1
_MODEL_KEYS = ("default", "provider", "context_length", "max_tokens", "temperature", "top_p",
               "reasoning_effort", "streaming", "api_mode")
_AGENT_KEYS = ("max_turns", "reasoning_effort", "api_max_retries", "run_budget_seconds", "gateway_timeout",
               "budget_warning_ratio", "temperature", "max_tokens")
_ROUTING_KEYS = ("enabled", "max_simple_chars", "max_simple_words", "strategy", "mode")
_ROUTE_MODEL_KEYS = ("provider", "model", "reasoning_effort", "temperature", "max_tokens", "timeout")
_COMPRESSION_KEYS = ("enabled", "threshold", "threshold_tokens", "target_ratio", "tail_mode", "protect_last_n",
                     "min_tail_user_messages", "max_attempts", "summary_model", "summary_provider")
_TERMINAL_KEYS = ("backend", "timeout", "lifetime_seconds", "docker_image", "modal_mode")
_CODE_KEYS = ("mode", "timeout", "max_tool_calls")
_MEMORY_KEYS = ("memory_enabled", "user_profile_enabled", "provider", "write_approval")
_DELEGATION_KEYS = ("provider", "model", "max_iterations", "reasoning_effort", "child_timeout_seconds")
_WEB_KEYS = ("backend", "search_backend", "extract_backend", "extract_char_limit", "cache_enabled")
# user:pass@host, provider key prefixes, "Bearer x", and long opaque tokens.
_CREDENTIAL = re.compile(r"(?i)^[^\s/@]+:[^\s@]+@|^(?:sk|pk|rk|ghp|gho|xox[abp]|AIza|hf)[-_]|\bbearer\s")
_TOKEN = re.compile(r"(?=[A-Za-z_-]*\d)(?=[0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}")
_SECRET_WORDS = ("key", "token", "secret", "password", "credential", "auth", "bearer", "cookie")


def _scalar(value, limit=120):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value if value == value and abs(value) < 1e15 else None  # no NaN/inf
    if isinstance(value, str):
        text = value.strip()
        # A string that looks like a credential or a URL is never copied verbatim.
        if len(text) > limit or "://" in text or _CREDENTIAL.search(text) or _TOKEN.fullmatch(text):
            return "[redacted]"
        return text if text else None
    return None


def _pick(source, keys):
    if not isinstance(source, dict):
        return {}
    out = {}
    for key in keys:
        if key in source:
            value = _scalar(source[key])
            if value is not None:
                out[key] = value
    return out


def url_host(value) -> str | None:
    """Host (and port) of an endpoint, never its path, query or credentials."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return "[invalid]"
    if not host:
        return "[invalid]"
    return f"{host}:{port}" if port else host


def _route(entry):
    if isinstance(entry, str):
        return {"model": _scalar(entry)}
    if not isinstance(entry, dict):
        return None
    out = _pick(entry, _ROUTE_MODEL_KEYS)
    host = url_host(entry.get("base_url"))
    if host:
        out["base_url_host"] = host
    return out or None


def _names(items, limit=40):
    if not isinstance(items, list):
        return None
    names = sorted({str(item)[:60] for item in items if isinstance(item, (str, int)) and str(item).strip()})
    return names[:limit]


def _skills_count(home: Path | None) -> int | None:
    if home is None:
        return None
    root = Path(home) / "skills"
    if not root.is_dir() or root.is_symlink():
        return None
    count = 0
    try:
        for depth in ("*/SKILL.md", "*/*/SKILL.md", "*/*/*/SKILL.md"):
            for _ in root.glob(depth):
                count += 1
                if count >= 2000:
                    return count
    except OSError:
        return None
    return count


def config_snapshot(config: dict, *, home: Path | None = None) -> dict:
    """Allowlisted, secret-free summary of a Hermes config.yaml for run comparison."""
    config = config if isinstance(config, dict) else {}
    snapshot: dict = {"version": SNAPSHOT_VERSION}
    model = config.get("model")
    if isinstance(model, str):
        model = {"default": model}
    model_part = _pick(model, _MODEL_KEYS)
    if isinstance(model, dict):
        host = url_host(model.get("base_url"))
        if host:
            model_part["base_url_host"] = host
    if model_part:
        snapshot["model"] = model_part
    for name, keys in (("agent", _AGENT_KEYS), ("compression", _COMPRESSION_KEYS), ("terminal", _TERMINAL_KEYS),
                       ("code_execution", _CODE_KEYS), ("memory", _MEMORY_KEYS), ("delegation", _DELEGATION_KEYS),
                       ("web", _WEB_KEYS)):
        part = _pick(config.get(name), keys)
        if part:
            snapshot[name] = part
    for name in ("smart_model_routing", "model_routing", "routing"):
        source = config.get(name)
        if isinstance(source, dict):
            part = _pick(source, _ROUTING_KEYS)
            for sub in ("cheap_model", "strong_model", "fast_model", "default_model"):
                route = _route(source.get(sub))
                if route:
                    part[sub] = route
            if part:
                snapshot[name] = part
    auxiliary = config.get("auxiliary")
    if isinstance(auxiliary, dict):
        part = {}
        for task, entry in list(auxiliary.items())[:40]:
            if isinstance(task, str) and isinstance(entry, dict) and not any(w in task.casefold() for w in _SECRET_WORDS):
                route = _route(entry)
                if route:
                    part[task[:40]] = route
        if part:
            snapshot["auxiliary"] = part
    fallback = config.get("fallback_providers")
    if isinstance(fallback, list) and fallback:
        routes = [route for route in (_route(item) for item in fallback[:8]) if route]
        if routes:
            snapshot["fallback_providers"] = routes
    toolsets = config.get("platform_toolsets")
    if isinstance(toolsets, dict):
        part = {str(platform)[:40]: _names(items) for platform, items in list(toolsets.items())[:20]
                if isinstance(items, list)}
        if part:
            snapshot["platform_toolsets"] = part
    if isinstance(config.get("toolsets"), list):
        snapshot["toolsets"] = _names(config["toolsets"])
    plugins = config.get("plugins")
    if isinstance(plugins, dict) and isinstance(plugins.get("enabled"), list):
        snapshot["plugins_enabled"] = _names(plugins["enabled"])
    if isinstance(config.get("mcp_servers"), dict):
        snapshot["mcp_servers"] = len(config["mcp_servers"])
    skills = _skills_count(home)
    if skills is not None:
        snapshot["skills_count"] = skills
    return snapshot


def flatten_snapshot(snapshot, prefix="") -> dict:
    """Dotted keys -> display values, for side-by-side settings diffs."""
    out = {}
    if isinstance(snapshot, dict):
        for key in sorted(snapshot):
            path = f"{prefix}.{key}" if prefix else str(key)
            value = snapshot[key]
            if isinstance(value, dict):
                out.update(flatten_snapshot(value, path))
            elif isinstance(value, list):
                out[path] = ", ".join(json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, dict)
                                      else str(v) for v in value) or "(none)"
            else:
                out[path] = value
    return out
