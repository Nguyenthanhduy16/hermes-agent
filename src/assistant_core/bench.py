"""Repeatable task suites against the Hermes API server, with durable results.

A suite is a YAML file of prompts with simple automatic checks. A run sends
every task (x repeats) to ``/v1/chat/completions`` of a Hermes API server, one
fresh conversation per task, and stores pass/fail, latency, token usage and the
settings snapshot of the Hermes home in ``<assistant_dir>/bench.sqlite3``. Runs
started under different settings (model, routing, compression...) can then be
compared side by side.

Rules: no automatic retries (a failed request is a recorded failure), bounded
concurrency (1-4), per-task timeouts, one active run per data directory, API
keys only from the environment and never stored. Response text is kept only
truncated (2000 characters) and only for these benchmark prompts.

Suite format (``config/bench/*.yaml`` bundled, ``<home>/benchmarks/*.yaml`` own)::

    name: assistant-basics            # a-z, 0-9 and '-', unique
    description: Everyday tasks       # optional, <= 500 characters
    tasks:                            # 1..300 tasks
      - id: weather-summary           # unique in the suite
        prompt: "Tóm tắt ..."         # <= 8000 characters
        level: L1                     # L1 simple, L2 multi-step, L3 long/tool-heavy
        category: summarize           # optional, default "general"
        timeout_s: 120                # optional, 5..1800, default 120
        checks:                       # optional, up to 10; all must pass
          - {type: contains, value: "Hà Nội"}           # case-insensitive
          - {type: contains, value: ["7", "bảy"]}       # any of the list
          - {type: not_contains, value: "I cannot"}
          - {type: regex, value: "\\d{4}"}               # Python re.search
          - {type: max_latency_s, value: 30}
          - {type: min_length, value: 200}               # characters
          - {type: json_valid}                            # whole reply (code fences allowed)
          - {type: answers, value: {mean_fare: "34.65"}}  # closed-form @name[value] answers

``answers`` scores replies the way InfiAgent-DABench's ``eval_closed_form.py``
does: every ``@name[value]`` in the reply is read (the last one wins per name)
and compared with the expected text, exactly or as numbers within 1e-6.

CLI::

    python -m assistant_core.bench suites
    python -m assistant_core.bench run --suite assistant-basics --label B1 [--repeats 2] [--concurrency 2]
    python -m assistant_core.bench list
    python -m assistant_core.bench show RUN
    python -m assistant_core.bench compare RUN RUN [RUN RUN]
    python -m assistant_core.bench cancel RUN
    python -m assistant_core.bench resume RUN [--retry-errors connection_error,provider_unavailable] [--force]

A stopped run (``cancel``, Ctrl-C, or a runner that died: marked failed on the next load) can be
finished with ``resume``: it keeps every recorded result, sends only the tasks without one, and
refuses if the suite or ``config.yaml`` changed. Results whose error is listed in
``--retry-errors`` are dropped and sent again (default: ``connection_error`` and
``provider_unavailable``, Hermes's notice that the model provider did not answer any retry; neither
says anything about the agent). A done run that has such results can be resumed the same way.
Three ``provider_unavailable`` tasks in a row stop the run as failed so it can be resumed later.
"""

import argparse
from concurrent import futures
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import statistics
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from . import reporting
from .telemetry import config_snapshot


BUNDLED_DIR = Path(__file__).resolve().parents[2] / "config" / "bench"
LEVELS = ("L1", "L2", "L3")
CHECK_TYPES = ("contains", "not_contains", "regex", "max_latency_s", "min_length", "json_valid", "answers", "external")
# ``external``: scored after the run by a benchmark's own evaluator (value names it); until then it fails.
EXTERNAL_SCORERS = ("dacode",)
STATUSES = ("queued", "running", "done", "cancelled", "failed")
ACTIVE = ("queued", "running")
SUITE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")
TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,47}")
CATEGORY = re.compile(r"[\w -]{1,40}")
LABEL = re.compile(r"[\w .:+-]{0,60}")
RUN_ID = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
SESSION_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
ANSWER_NAME = re.compile(r"\w{1,80}")
ANSWER_MARK = re.compile(r"@(\w+)\[(.*?)\]")  # same pattern as InfiAgent-DABench eval_closed_form.py
MAX_SUITE_BYTES = 1024 * 1024
MAX_SUITE_FILES = 50
MAX_TASKS = 300
MAX_TIMEOUT_S = 1800
MAX_ANSWERS = 12
MAX_ANSWER_CHARS = 500
MAX_PROMPT = 8000
MAX_CHECKS = 10
MAX_REQUESTS = 500
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
STORED_RESPONSE_CHARS = 2000
STALE_AFTER_S = 900
HEARTBEAT_S = 20
# Hermes answers HTTP 200 with a notice when every retry to the model provider failed (network down,
# provider outage). That is an infrastructure failure, not the agent's answer: record it as an error.
PROVIDER_DOWN = re.compile(r"didn't respond in time on any of \d+ attempts|Hermes can't reach the model provider")
# Result errors that say nothing about the agent and are run again on resume by default.
RETRYABLE_ERRORS = ("connection_error", "provider_unavailable")
# This many provider_unavailable results in a row stop the run (failed, resumable) instead of
# burning through the rest of the suite while the provider is down.
PROVIDER_DOWN_STREAK = 3
PROCESS_TOKEN = secrets.token_hex(4)
_SECRETISH = re.compile(r"(?i)\b(?:sk|pk|rk|ghp|gho|xox[abp]|hf)[-_][A-Za-z0-9_-]{12,}|\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"
                        r"|\bAIza[0-9A-Za-z_-]{20,}")


class BenchError(Exception):
    """A user-facing problem with a suite, the target or a run."""


# ---- suites -----------------------------------------------------------------

@dataclass(frozen=True)
class Check:
    type: str
    value: object = None
    ignore_case: bool = True


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    level: str
    category: str = "general"
    timeout_s: float = 120.0
    checks: tuple = ()


@dataclass(frozen=True)
class Suite:
    name: str
    description: str
    tasks: tuple
    source: str = "bundled"      # bundled | home | inline
    digest: str = ""

    def levels(self) -> dict:
        return {level: sum(task.level == level for task in self.tasks) for level in LEVELS}

    def categories(self) -> list:
        return sorted({task.category for task in self.tasks})


def _number(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BenchError(f"{name} must be a number.")
    if not low <= value <= high:
        raise BenchError(f"{name} must be between {low} and {high}.")
    return value


def _strings(value, name):
    if isinstance(value, str):
        value = [value]
    if (not isinstance(value, list) or not 1 <= len(value) <= 10
            or not all(isinstance(v, str) and 0 < len(v) <= 500 for v in value)):
        raise BenchError(f"{name} needs text (or a list of up to 10 texts) of at most 500 characters.")
    return tuple(value)


def _check(raw, where) -> Check:
    if not isinstance(raw, dict) or not set(raw) <= {"type", "value", "ignore_case"} or "type" not in raw:
        raise BenchError(f"{where}: a check needs 'type' and 'value' only.")
    kind = raw["type"]
    if kind not in CHECK_TYPES:
        raise BenchError(f"{where}: unknown check type. Use one of: {', '.join(CHECK_TYPES)}.")
    ignore_case = raw.get("ignore_case", kind not in ("regex", "answers"))
    if not isinstance(ignore_case, bool):
        raise BenchError(f"{where}: ignore_case must be true or false.")
    value = raw.get("value")
    if kind in ("contains", "not_contains"):
        value = _strings(value, f"{where} {kind}")
    elif kind == "regex":
        if not isinstance(value, str) or not 0 < len(value) <= 300:
            raise BenchError(f"{where}: regex needs a pattern of at most 300 characters.")
        try:
            re.compile(value)
        except re.error:
            raise BenchError(f"{where}: the regex pattern is invalid.") from None
    elif kind == "max_latency_s":
        value = float(_number(value, f"{where} max_latency_s", 0.1, 3600))
    elif kind == "min_length":
        value = int(_number(value, f"{where} min_length", 1, 100000))
    elif kind == "json_valid":
        if value not in (None, True):
            raise BenchError(f"{where}: json_valid takes no value (or true).")
        value = True
    elif kind == "answers":
        # Text only: an unquoted YAML ``no`` or ``0.50`` would silently become False or 0.5.
        if (not isinstance(value, dict) or not 1 <= len(value) <= MAX_ANSWERS
                or not all(isinstance(k, str) and ANSWER_NAME.fullmatch(k) and isinstance(v, str)
                           and len(v) <= MAX_ANSWER_CHARS and "\n" not in v and "]" not in v for k, v in value.items())):
            raise BenchError(f"{where}: answers needs a mapping of 1 to {MAX_ANSWERS} names (letters, digits, '_') "
                             f"to quoted text of at most {MAX_ANSWER_CHARS} characters without ']'.")
        value = tuple(sorted(value.items()))
    elif kind == "external":
        if value not in EXTERNAL_SCORERS:
            raise BenchError(f"{where}: external needs a scorer name: {', '.join(EXTERNAL_SCORERS)}.")
    return Check(kind, value, ignore_case)


def parse_suite(data, *, source="inline") -> Suite:
    """Validate a suite mapping strictly; raises BenchError with the first problem."""
    if not isinstance(data, dict) or not set(data) <= {"name", "description", "tasks", "version"}:
        raise BenchError("A suite is a mapping with name, description and tasks.")
    name = data.get("name")
    if not isinstance(name, str) or not SUITE_NAME.fullmatch(name):
        raise BenchError("Suite name must be 1-48 lowercase letters, digits or '-'.")
    description = data.get("description", "")
    if not isinstance(description, str) or len(description) > 500:
        raise BenchError(f"{name}: description must be text of at most 500 characters.")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS:
        raise BenchError(f"{name}: tasks must be a list of 1 to {MAX_TASKS} items.")
    parsed, seen = [], set()
    for index, raw in enumerate(tasks, 1):
        where = f"{name} task {index}"
        if not isinstance(raw, dict) or not set(raw) <= {"id", "prompt", "level", "category", "checks", "timeout_s"}:
            raise BenchError(f"{where}: allowed keys are id, prompt, level, category, checks, timeout_s.")
        task_id = raw.get("id")
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id) or task_id in seen:
            raise BenchError(f"{where}: id must be unique, 1-48 letters, digits, '.', '_' or '-'.")
        seen.add(task_id)
        where = f"{name}/{task_id}"
        prompt = raw.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT or "\0" in prompt:
            raise BenchError(f"{where}: prompt must be text of at most {MAX_PROMPT} characters.")
        level = raw.get("level")
        if level not in LEVELS:
            raise BenchError(f"{where}: level must be L1, L2 or L3.")
        category = raw.get("category", "general")
        if not isinstance(category, str) or not CATEGORY.fullmatch(category):
            raise BenchError(f"{where}: category must be 1-40 letters, digits, spaces, '_' or '-'.")
        timeout = float(_number(raw.get("timeout_s", 120), f"{where} timeout_s", 5, MAX_TIMEOUT_S))
        checks = raw.get("checks", [])
        if not isinstance(checks, list) or len(checks) > MAX_CHECKS:
            raise BenchError(f"{where}: checks must be a list of at most {MAX_CHECKS} items.")
        parsed.append(Task(task_id, prompt.strip(), level, category, timeout,
                           tuple(_check(check, where) for check in checks)))
    canonical = json.dumps({"name": name, "tasks": [[t.id, t.prompt, t.level, t.category, t.timeout_s,
                                                     [[c.type, c.value, c.ignore_case] for c in t.checks]]
                                                    for t in parsed]}, ensure_ascii=False, sort_keys=True)
    return Suite(name, description.strip(), tuple(parsed), source,
                 hashlib.sha256(canonical.encode()).hexdigest()[:12])


def _suite_files(directory: Path) -> list:
    if not directory.is_dir() or directory.is_symlink():
        return []
    files = sorted(p for p in directory.iterdir() if p.suffix in (".yaml", ".yml"))
    return files[:MAX_SUITE_FILES]


def load_suite_file(path: Path, *, source: str) -> Suite:
    import yaml
    if path.is_symlink() or not path.is_file():
        raise BenchError("Suite files must be regular files.")
    if path.stat().st_size > MAX_SUITE_BYTES:
        raise BenchError(f"Suite files are limited to {MAX_SUITE_BYTES // 1024} KB.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise BenchError("The file is not valid UTF-8 YAML.") from None
    return parse_suite(data, source=source)


def load_suites(home: Path | None = None, *, bundled_dir: Path = BUNDLED_DIR) -> tuple:
    """(suites by name, problems) from bundled examples and ``<home>/benchmarks``.

    A home suite reusing a bundled name is reported and skipped: rename the copy.
    """
    suites, problems = {}, []
    sources = [(bundled_dir, "bundled")]
    if home is not None:
        sources.append((Path(home) / "benchmarks", "home"))
    for directory, source in sources:
        for path in _suite_files(Path(directory)):
            try:
                suite = load_suite_file(path, source=source)
            except BenchError as error:
                problems.append(dict(file=path.name[:80], source=source, error=str(error)[:300]))
                continue
            if suite.name in suites:
                problems.append(dict(file=path.name[:80], source=source,
                                     error=f"Suite name '{suite.name}' is already used; rename this suite."))
                continue
            suites[suite.name] = suite
    return suites, problems


# ---- target -----------------------------------------------------------------

@dataclass(frozen=True)
class Target:
    endpoint: str
    key: str = ""
    model: str = "hermes"
    host: str = ""

    def __repr__(self):  # never print the key
        return f"Target(endpoint={self.endpoint!r}, model={self.model!r})"


def resolve_target(environ, api_url: str | None = None) -> Target:
    """Hermes API server from the environment (same rules as the console chat).

    URL: HERMES_BENCH_API_URL, else HERMES_CONSOLE_CHAT_API_URL. Key:
    HERMES_BENCH_API_KEY, else HERMES_CONSOLE_CHAT_API_KEY, else API_SERVER_KEY.
    HTTPS is required except on loopback; no credentials, query or fragment.
    """
    raw = (api_url or environ.get("HERMES_BENCH_API_URL") or environ.get("HERMES_CONSOLE_CHAT_API_URL") or "").strip()
    key = (environ.get("HERMES_BENCH_API_KEY") or environ.get("HERMES_CONSOLE_CHAT_API_KEY")
           or environ.get("API_SERVER_KEY") or "")
    if not raw or not key:
        raise BenchError("No Hermes API server is configured. Set HERMES_BENCH_API_URL (or HERMES_CONSOLE_CHAT_API_URL) "
                         "and API_SERVER_KEY on the server.")
    try:
        parsed = urlsplit(raw)
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if parsed.scheme not in ("https", "http") or (parsed.scheme == "http" and not local):
            raise ValueError()
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError()
        if parsed.path.rstrip("/") not in ("", "/v1", "/v1/chat/completions"):
            raise ValueError()
        port = parsed.port
    except ValueError:
        raise BenchError("The configured Hermes API URL is invalid: use https:// (http:// only for localhost), "
                         "no credentials, query or path other than /v1.") from None
    if "\n" in key or "\r" in key or len(key) > 4096:
        raise BenchError("The configured Hermes API key is invalid.")
    model = (environ.get("HERMES_BENCH_MODEL") or environ.get("HERMES_CONSOLE_CHAT_MODEL") or "hermes").strip()[:120]
    host = parsed.hostname + (f":{port}" if port else "")
    endpoint = urlunsplit((parsed.scheme, parsed.netloc, "/v1/chat/completions", "", ""))
    return Target(endpoint, key, model or "hermes", host)


# ---- settings identity ------------------------------------------------------

def config_file_hash(home: Path | None) -> str:
    """sha256(config.yaml bytes)[:16] like the gateway plugin, or '' when unreadable."""
    if home is None:
        return ""
    path = Path(home) / "config.yaml"
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def config_identity(home: Path | None) -> tuple:
    """(config_hash, snapshot) of ``<home>/config.yaml``, matching the gateway plugin's hash."""
    if home is None:
        return "", {}
    path = Path(home) / "config.yaml"
    try:
        if path.is_symlink() or not path.is_file():
            return "", {"note": "config.yaml not found"}
        data = path.read_bytes()
    except OSError:
        return "", {"note": "config.yaml unreadable"}
    digest = hashlib.sha256(data).hexdigest()[:16]
    try:
        import yaml
        parsed = yaml.safe_load(data.decode("utf-8"))
    except Exception:
        return digest, {"note": "config.yaml could not be parsed"}
    return digest, config_snapshot(parsed if isinstance(parsed, dict) else {}, home=Path(home))


def gateway_config_hash(telemetry_db: Path | None) -> str:
    """config_hash of the most recent gateway start, or '' when unknown."""
    if telemetry_db is None:
        return ""
    try:
        latest = reporting.runs(Path(telemetry_db), 1)
    except sqlite3.Error:
        return ""
    return (latest[0].get("config_hash") or "") if latest else ""


# ---- store ------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS bench_runs (
    id TEXT PRIMARY KEY, suite TEXT NOT NULL, suite_hash TEXT, label TEXT, status TEXT NOT NULL, reason TEXT,
    created_at REAL NOT NULL, started_at REAL, finished_at REAL, heartbeat_at REAL, owner TEXT,
    config_hash TEXT, gateway_config_hash TEXT, snapshot_json TEXT, target_host TEXT, model TEXT,
    repeats INTEGER NOT NULL, concurrency INTEGER NOT NULL, task_count INTEGER NOT NULL, total INTEGER NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0, passed INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bench_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, task_id TEXT NOT NULL, attempt INTEGER NOT NULL,
    level TEXT, category TEXT, passed INTEGER NOT NULL, checks_json TEXT, latency_s REAL,
    input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, response_model TEXT, session_id TEXT,
    error TEXT, status_code INTEGER, response_length INTEGER, response_text TEXT, finished_at REAL,
    UNIQUE (run_id, task_id, attempt)
);
CREATE INDEX IF NOT EXISTS bench_runs_created ON bench_runs (created_at);
"""
RUN_FIELDS = ("id", "suite", "suite_hash", "label", "status", "reason", "created_at", "started_at", "finished_at",
              "heartbeat_at", "owner", "config_hash", "gateway_config_hash", "snapshot_json", "target_host", "model",
              "repeats", "concurrency", "task_count", "total", "completed", "passed", "errors", "input_tokens",
              "output_tokens", "cancel_requested")
RESULT_FIELDS = ("task_id", "attempt", "level", "category", "passed", "checks_json", "latency_s", "input_tokens",
                 "output_tokens", "total_tokens", "response_model", "session_id", "error", "status_code",
                 "response_length", "response_text", "finished_at")


def _owner() -> str:
    return f"{socket.gethostname()[:60]}:{os.getpid()}:{PROCESS_TOKEN}"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _owner_gone(owner: str | None, heartbeat: float | None, now: float) -> bool:
    if heartbeat is None or now - heartbeat > STALE_AFTER_S:
        return True
    try:
        host, pid, token = str(owner).rsplit(":", 2)
        pid = int(pid)
    except ValueError:
        return True
    if host != socket.gethostname()[:60]:
        return False  # another machine: trust the heartbeat
    if pid == os.getpid():
        return token != PROCESS_TOKEN  # same pid, new process (container restart)
    return not _pid_alive(pid)


class BenchStore:
    def __init__(self, path: Path):
        path = Path(path)
        if path.is_symlink() or path.parent.is_symlink():
            raise BenchError("The benchmark store must be a regular local file.")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=5)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self.db.close()

    def _run_row(self, row):
        run = dict(zip(RUN_FIELDS, row))
        try:
            run["snapshot"] = json.loads(run.pop("snapshot_json") or "{}")
        except ValueError:
            run["snapshot"] = {}
        return run

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self.db.execute(f"SELECT {', '.join(RUN_FIELDS)} FROM bench_runs WHERE id=?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def list_runs(self, limit: int = 50) -> list:
        with self._lock:
            rows = self.db.execute(f"SELECT {', '.join(RUN_FIELDS)} FROM bench_runs ORDER BY created_at DESC LIMIT ?",
                                   (max(1, min(int(limit), 500)),)).fetchall()
        return [self._run_row(row) for row in rows]

    def results(self, run_id: str, limit: int = MAX_REQUESTS) -> list:
        with self._lock:
            rows = self.db.execute(f"SELECT {', '.join(RESULT_FIELDS)} FROM bench_results WHERE run_id=? "
                                   "ORDER BY id LIMIT ?", (run_id, limit)).fetchall()
        out = []
        for row in rows:
            item = dict(zip(RESULT_FIELDS, row))
            try:
                item["checks"] = json.loads(item.pop("checks_json") or "[]")
            except ValueError:
                item["checks"] = []
            item["passed"] = bool(item["passed"])
            out.append(item)
        return out

    def recover_stale(self, now: float | None = None) -> int:
        """Mark runs whose runner process is gone as failed (restart safety)."""
        now = time.time() if now is None else now
        with self._lock:
            rows = self.db.execute("SELECT id, owner, heartbeat_at FROM bench_runs WHERE status IN ('queued', 'running')"
                                   ).fetchall()
            stale = [run_id for run_id, owner, beat in rows if _owner_gone(owner, beat, now)]
            for run_id in stale:
                self.db.execute("UPDATE bench_runs SET status='failed', reason=?, finished_at=? "
                                "WHERE id=? AND status IN ('queued', 'running')",
                                ("The runner stopped (console or CLI restarted) before the run finished.", now, run_id))
        return len(stale)

    def active_run(self) -> dict | None:
        self.recover_stale()
        with self._lock:
            row = self.db.execute(f"SELECT {', '.join(RUN_FIELDS)} FROM bench_runs WHERE status IN ('queued', 'running') "
                                  "ORDER BY created_at DESC LIMIT 1").fetchone()
        return self._run_row(row) if row else None

    def create_run(self, suite: Suite, *, label: str, repeats: int, concurrency: int, target_host: str = "",
                   model: str = "", config_hash: str = "", gateway_hash: str = "", snapshot: dict | None = None) -> dict:
        label = validate_label(label)
        repeats = bounded_int(repeats, "Repeats", 1, 5)
        concurrency = bounded_int(concurrency, "Concurrency", 1, 4)
        total = len(suite.tasks) * repeats
        if total > MAX_REQUESTS:
            raise BenchError(f"A run is limited to {MAX_REQUESTS} requests; lower the repeats.")
        now = time.time()
        run_id = time.strftime("%Y%m%d-%H%M%S-", time.gmtime(now)) + secrets.token_hex(3)
        with self._lock:
            self.recover_stale(now)
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if self.db.execute("SELECT 1 FROM bench_runs WHERE status IN ('queued', 'running')").fetchone():
                    raise BenchError("Another benchmark run is in progress. Wait for it or cancel it first.")
                self.db.execute(
                    "INSERT INTO bench_runs (id, suite, suite_hash, label, status, created_at, heartbeat_at, owner, "
                    "config_hash, gateway_config_hash, snapshot_json, target_host, model, repeats, concurrency, "
                    "task_count, total) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, suite.name, suite.digest, label, now, now, _owner(), config_hash[:64], gateway_hash[:64],
                     json.dumps(snapshot or {}, ensure_ascii=False, sort_keys=True), target_host[:120], model[:120],
                     repeats, concurrency, len(suite.tasks), total))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return self.get_run(run_id)

    def mark_running(self, run_id: str):
        now = time.time()
        with self._lock:
            self.db.execute("UPDATE bench_runs SET status='running', started_at=COALESCE(started_at, ?), "
                            "heartbeat_at=?, owner=? WHERE id=? AND status='queued'", (now, now, _owner(), run_id))

    def heartbeat(self, run_id: str):
        with self._lock:
            self.db.execute("UPDATE bench_runs SET heartbeat_at=? WHERE id=? AND status IN ('queued', 'running')",
                            (time.time(), run_id))

    def add_result(self, run_id: str, result: dict):
        row = {key: result.get(key) for key in RESULT_FIELDS if key != "checks_json"}
        row["checks_json"] = json.dumps(result.get("checks") or [], ensure_ascii=False)
        row["passed"] = int(bool(result.get("passed")))
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(f"INSERT OR IGNORE INTO bench_results (run_id, {', '.join(RESULT_FIELDS)}) "
                                f"VALUES (?, {', '.join('?' * len(RESULT_FIELDS))})",
                                (run_id, *[row[key] for key in RESULT_FIELDS]))
                self.db.execute(
                    "UPDATE bench_runs SET completed=completed+1, passed=passed+?, errors=errors+?, "
                    "input_tokens=input_tokens+?, output_tokens=output_tokens+?, heartbeat_at=? WHERE id=?",
                    (row["passed"], int(bool(row.get("error"))), row.get("input_tokens") or 0,
                     row.get("output_tokens") or 0, time.time(), run_id))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def set_external_scores(self, run_id: str, scored: list):
        """Replace the checks of scored results: [(task_id, attempt, passed, checks)]; recount the run."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                for task_id, attempt, passed, checks in scored:
                    self.db.execute("UPDATE bench_results SET passed=?, checks_json=? WHERE run_id=? AND task_id=? "
                                    "AND attempt=?", (int(bool(passed)), json.dumps(checks, ensure_ascii=False),
                                                       run_id, task_id, attempt))
                self.db.execute("UPDATE bench_runs SET passed=(SELECT COUNT(*) FROM bench_results "
                                "WHERE run_id=? AND passed=1) WHERE id=?", (run_id, run_id))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def finish(self, run_id: str, status: str, reason: str | None = None):
        if status not in ("done", "cancelled", "failed"):
            raise ValueError(status)
        with self._lock:
            self.db.execute("UPDATE bench_runs SET status=?, reason=?, finished_at=? "
                            "WHERE id=? AND status IN ('queued', 'running')",
                            (status, (reason or None) and str(reason)[:300], time.time(), run_id))

    def reopen(self, run_id: str, *, retry_errors=()) -> dict:
        """Queue a cancelled or failed run again so a runner finishes it (``resume``).

        Results already recorded are kept and their tasks are not sent again, except results whose
        error is in ``retry_errors`` (for example ``connection_error`` when the gateway was down):
        those are deleted and their tasks run again. Counters are recounted from the kept results.
        A finished (done) run can be reopened too when it has results with such errors.
        """
        retry_errors = tuple(retry_errors)
        now = time.time()
        with self._lock:
            self.recover_stale(now)
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT status FROM bench_runs WHERE id=?", (run_id,)).fetchone()
                if row is None:
                    raise BenchError("No such run.")
                marks = ", ".join("?" * len(retry_errors))
                retryable = bool(retry_errors) and self.db.execute(
                    f"SELECT 1 FROM bench_results WHERE run_id=? AND error IN ({marks})",
                    (run_id, *retry_errors)).fetchone() is not None
                if row[0] not in ("cancelled", "failed") and not (row[0] == "done" and retryable):
                    raise BenchError(f"Only a cancelled or failed run, or a done run with retryable errors, "
                                     f"can be resumed (this one is {row[0]}).")
                if self.db.execute("SELECT 1 FROM bench_runs WHERE status IN ('queued', 'running')").fetchone():
                    raise BenchError("Another benchmark run is in progress. Wait for it or cancel it first.")
                if retry_errors:
                    self.db.execute(f"DELETE FROM bench_results WHERE run_id=? AND error IN ({marks})",
                                    (run_id, *retry_errors))
                self.db.execute(
                    "UPDATE bench_runs SET status='queued', reason=NULL, finished_at=NULL, cancel_requested=0, "
                    "heartbeat_at=?, owner=?, "
                    "completed=(SELECT COUNT(*) FROM bench_results WHERE run_id=?), "
                    "passed=(SELECT COUNT(*) FROM bench_results WHERE run_id=? AND passed=1), "
                    "errors=(SELECT COUNT(*) FROM bench_results WHERE run_id=? AND error IS NOT NULL AND error != ''), "
                    "input_tokens=(SELECT COALESCE(SUM(input_tokens), 0) FROM bench_results WHERE run_id=?), "
                    "output_tokens=(SELECT COALESCE(SUM(output_tokens), 0) FROM bench_results WHERE run_id=?) "
                    "WHERE id=?", (now, _owner(), *([run_id] * 6)))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
        return self.get_run(run_id)

    def request_cancel(self, run_id: str) -> bool:
        with self._lock:
            cursor = self.db.execute("UPDATE bench_runs SET cancel_requested=1 WHERE id=? AND status IN ('queued', 'running')",
                                     (run_id,))
        return cursor.rowcount > 0

    def cancel_requested(self, run_id: str) -> bool:
        with self._lock:
            row = self.db.execute("SELECT cancel_requested, status FROM bench_runs WHERE id=?", (run_id,)).fetchone()
        return not row or bool(row[0]) or row[1] not in ACTIVE

    def delete_run(self, run_id: str) -> bool:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT status FROM bench_runs WHERE id=?", (run_id,)).fetchone()
                if row is None:
                    self.db.execute("ROLLBACK")
                    return False
                if row[0] in ACTIVE:
                    raise BenchError("Cancel the run before deleting it.")
                self.db.execute("DELETE FROM bench_results WHERE run_id=?", (run_id,))
                self.db.execute("DELETE FROM bench_runs WHERE id=?", (run_id,))
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
        return True


def validate_label(label) -> str:
    label = "" if label is None else label
    if not isinstance(label, str) or not LABEL.fullmatch(label.strip()):
        raise BenchError("Label must be at most 60 letters, digits, spaces or . _ : + -")
    return label.strip()


def bounded_int(value, name, low, high) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or not low <= value <= high:
        raise BenchError(f"{name} must be a whole number from {low} to {high}.")
    return int(value)


# ---- checks -----------------------------------------------------------------

def _strip_fences(text: str) -> str:
    match = re.fullmatch(r"\s*```[A-Za-z0-9_-]*\s*\n(.*?)\n?```\s*", text, re.S)
    return match.group(1) if match else text


def _answer_matches(found, expected: str, ignore_case: bool) -> bool:
    if found is None:
        return False
    if found == expected or (ignore_case and found.casefold() == expected.casefold()):
        return True
    try:
        return abs(float(found) - float(expected)) < 1e-6
    except ValueError:
        return False


def evaluate_checks(checks, text: str, latency_s: float | None) -> list:
    """[{type, passed, detail}] for each check. Details describe the check, never the reply.

    An ``answers`` check also reports ``matched`` and ``total`` sub-answers.
    """
    text = text or ""
    scan = text[:100000]
    out = []
    for check in checks:
        kind, value = check.type, check.value
        if kind in ("contains", "not_contains"):
            haystack = scan.casefold() if check.ignore_case else scan
            found = [v for v in value if (v.casefold() if check.ignore_case else v) in haystack]
            passed = bool(found) if kind == "contains" else not found
            shown = " / ".join(value)[:120]
            detail = (f"contains {shown!r}" if passed else f"missing {shown!r}") if kind == "contains" else (
                f"does not contain {shown!r}" if passed else f"contains forbidden {found[0][:60]!r}")
        elif kind == "regex":
            passed = re.search(value, scan[:20000], re.I if check.ignore_case else 0) is not None
            detail = f"{'matches' if passed else 'no match for'} /{value[:80]}/"
        elif kind == "max_latency_s":
            passed = latency_s is not None and latency_s <= value
            detail = f"latency {latency_s:.1f}s vs limit {value:g}s" if latency_s is not None else "latency unknown"
        elif kind == "min_length":
            passed = len(text.strip()) >= value
            detail = f"{len(text.strip())} characters vs minimum {value}"
        elif kind == "answers":
            found = dict(ANSWER_MARK.findall(scan))  # a repeated name keeps its last value
            matched = sum(_answer_matches(found.get(name), expected, check.ignore_case) for name, expected in value)
            out.append(dict(type=kind, passed=matched == len(value), matched=matched, total=len(value),
                            detail=f"{matched}/{len(value)} answers match"))
            continue
        elif kind == "external":
            out.append(dict(type=kind, scorer=value, passed=False, pending=True,
                            detail=f"scored after the run by {value}"))
            continue
        else:  # json_valid
            try:
                json.loads(_strip_fences(text))
                passed, detail = True, "valid JSON"
            except ValueError:
                passed, detail = False, "not valid JSON"
        out.append(dict(type=kind, passed=passed, detail=detail))
    return out


def redact_response(text: str) -> str:
    return _SECRETISH.sub("[redacted]", text or "")[:STORED_RESPONSE_CHARS]


# ---- runner -----------------------------------------------------------------

def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def expected_session_id(system_prompt: str, first_user_message: str) -> str:
    """The session id the Hermes chat-completions endpoint derives for this conversation
    (``_derive_chat_session_id`` in gateway/platforms/api_server.py, no profile prefix).
    Recorded before the request so a task that times out, and so never gets the
    X-Hermes-Session-Id response header, can still be joined to its telemetry steps."""
    digest = hashlib.sha256(f"{system_prompt or ''}\n{first_user_message}".encode("utf-8")).hexdigest()[:16]
    return f"api-{digest}"


def execute_task(client, target: Target, run_id: str, task: Task, attempt: int, *, store_response=True) -> dict:
    """Send one task as a fresh conversation. Exactly one request; failures are recorded, not retried."""
    # The nonce gives each task its own Hermes session: the chat-completions
    # endpoint derives the session from the system prompt and first message.
    system = f"Benchmark run {run_id}, task {task.id}, attempt {attempt}."
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task.prompt}]
    headers = {"Authorization": "Bearer " + target.key, "User-Agent": f"hermes-console-bench/1 run={run_id}"}
    result = dict(task_id=task.id, attempt=attempt, level=task.level, category=task.category, passed=False,
                  checks=[], latency_s=None, input_tokens=None, output_tokens=None, total_tokens=None,
                  response_model=None, session_id=expected_session_id(system, task.prompt), error=None,
                  status_code=None, response_length=None, response_text=None)
    started = time.monotonic()
    import httpx
    try:
        with client.stream("POST", target.endpoint, headers=headers,
                           timeout=httpx.Timeout(task.timeout_s, connect=min(10.0, task.timeout_s)),
                           json={"model": target.model, "messages": messages, "stream": False}) as response:
            result["status_code"] = response.status_code
            session = response.headers.get("X-Hermes-Session-Id", "")
            if SESSION_ID.fullmatch(session or ""):
                result["session_id"] = session
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise BenchError("response_too_large")
                if time.monotonic() - started > task.timeout_s:
                    raise httpx.ReadTimeout("deadline")
                chunks.append(chunk)
        result["latency_s"] = round(time.monotonic() - started, 3)
        if response.status_code != 200:
            result["error"] = f"http_{response.status_code}"
            return result
        payload = json.loads(b"".join(chunks))
        content = payload["choices"][0]["message"].get("content")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ValueError("content")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        result.update(
            input_tokens=_int_or_none(usage.get("prompt_tokens", usage.get("input_tokens"))),
            output_tokens=_int_or_none(usage.get("completion_tokens", usage.get("output_tokens"))),
            total_tokens=_int_or_none(usage.get("total_tokens")),
            response_model=str(payload.get("model"))[:120] if payload.get("model") else None,
            response_length=len(content),
            response_text=redact_response(content) if store_response else None)
        if result["total_tokens"] is None and result["input_tokens"] is not None and result["output_tokens"] is not None:
            result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
        result["checks"] = evaluate_checks(task.checks, content, result["latency_s"])
        result["passed"] = all(check["passed"] for check in result["checks"]) and bool(content.strip())
        if not content.strip():
            result["error"] = "empty_reply"
        elif PROVIDER_DOWN.search(content[:2000]):
            result.update(error="provider_unavailable", passed=False, checks=[])
    except BenchError as error:
        result["error"] = str(error)
    except httpx.TimeoutException:
        result["error"] = "timeout"
    except httpx.HTTPError:
        result["error"] = "connection_error"
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        result["error"] = "invalid_response"
    if result["latency_s"] is None:
        result["latency_s"] = round(time.monotonic() - started, 3)
    return result


class BenchRunner:
    """Runs one stored run to completion, cancellation or failure."""

    def __init__(self, store: BenchStore, run_id: str, suite: Suite, target: Target, *, transport=None,
                 store_responses=True, cancel_event: threading.Event | None = None, progress=None):
        self.store, self.run_id, self.suite, self.target = store, run_id, suite, target
        self.transport, self.store_responses = transport, store_responses
        self.cancel_event = cancel_event or threading.Event()
        self.progress = progress

    def cancelled(self) -> bool:
        if self.cancel_event.is_set():
            return True
        if self.store.cancel_requested(self.run_id):
            self.cancel_event.set()
            return True
        return False

    def run(self) -> dict:
        run = self.store.get_run(self.run_id)
        if run is None or run["status"] != "queued":
            raise BenchError("This run cannot be started.")
        self.store.mark_running(self.run_id)
        try:
            status = self._execute(run)
        except Exception as error:
            reason = str(error) if isinstance(error, BenchError) else f"Runner error: {type(error).__name__}"
            self.store.finish(self.run_id, "failed", reason)
            raise
        self.store.finish(self.run_id, status, "Cancelled by the owner." if status == "cancelled" else None)
        return self.store.get_run(self.run_id)

    def _execute(self, run) -> str:
        import httpx
        done = {(r["task_id"], r["attempt"]) for r in self.store.results(self.run_id)}  # resumed runs
        jobs = [(task, attempt) for attempt in range(1, run["repeats"] + 1) for task in self.suite.tasks
                if (task.id, attempt) not in done]
        limits = httpx.Limits(max_connections=run["concurrency"], max_keepalive_connections=run["concurrency"])
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=False,
                          trust_env=False, limits=limits, transport=self.transport) as client, \
                futures.ThreadPoolExecutor(max_workers=run["concurrency"], thread_name_prefix="bench") as pool:
            pending, queue, last_beat, down = set(), list(reversed(jobs)), time.monotonic(), 0
            while queue or pending:
                if self.cancelled() or down >= PROVIDER_DOWN_STREAK:
                    queue.clear()  # no new requests; in-flight ones finish and are recorded
                while queue and len(pending) < run["concurrency"]:
                    task, attempt = queue.pop()
                    pending.add(pool.submit(execute_task, client, self.target, self.run_id, task, attempt,
                                            store_response=self.store_responses))
                if not pending:
                    break
                done, pending = futures.wait(pending, timeout=HEARTBEAT_S, return_when=futures.FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    self.store.add_result(self.run_id, result)
                    down = down + 1 if result.get("error") == "provider_unavailable" else 0
                    if self.progress:
                        self.progress(result)
                if time.monotonic() - last_beat >= HEARTBEAT_S:
                    self.store.heartbeat(self.run_id)
                    last_beat = time.monotonic()
        if down >= PROVIDER_DOWN_STREAK:
            raise BenchError(f"provider_unavailable: {down} tasks in a row could not reach the model provider; "
                             "resume once it is back")
        return "cancelled" if self.cancel_event.is_set() else "done"


# ---- background runs (console) ------------------------------------------------

_threads: dict = {}
_threads_lock = threading.Lock()


def prepare_run(store: BenchStore, suite: Suite, target: Target, *, label="", repeats=1, concurrency=1,
                home: Path | None = None, telemetry_db: Path | None = None) -> dict:
    config_hash, snapshot = config_identity(home)
    return store.create_run(suite, label=label, repeats=repeats, concurrency=concurrency, target_host=target.host,
                            model=target.model, config_hash=config_hash,
                            gateway_hash=gateway_config_hash(telemetry_db), snapshot=snapshot)


def resume_run(store: BenchStore, run_id: str, suite: Suite, *, retry_errors=(), home: Path | None = None,
               force: bool = False) -> dict:
    """Reopen a stopped run of ``suite``; refuses when the suite or config.yaml changed (unless ``force``)."""
    run = store.get_run(run_id)
    if run is None:
        raise BenchError("No such run.")
    if suite.name != run["suite"] or (run["suite_hash"] and suite.digest != run["suite_hash"]):
        raise BenchError("The suite changed since this run started; start a new run instead.")
    config_hash, _ = config_identity(home)
    if not force and run["config_hash"] and config_hash and config_hash != run["config_hash"]:
        raise BenchError(f"config.yaml changed since this run started ({run['config_hash']} -> {config_hash}); "
                         "start a new run, or pass --force to resume anyway.")
    return store.reopen(run_id, retry_errors=retry_errors)


def start_background(store: BenchStore, run: dict, suite: Suite, target: Target, *, transport=None) -> threading.Thread:
    """Run in a daemon thread of this process. Restart safety comes from the owner/heartbeat checks."""
    event = threading.Event()
    runner = BenchRunner(store, run["id"], suite, target, transport=transport, cancel_event=event)

    def work():
        try:
            runner.run()
        except Exception:
            pass  # already recorded as failed by the runner
        finally:
            with _threads_lock:
                _threads.pop(run["id"], None)

    thread = threading.Thread(target=work, name=f"bench-{run['id']}", daemon=True)
    with _threads_lock:
        _threads[run["id"]] = event
    thread.start()
    return thread


def cancel(store: BenchStore, run_id: str) -> bool:
    """Ask a run to stop: no new requests start; in-flight ones finish (bounded by their timeout)."""
    requested = store.request_cancel(run_id)
    with _threads_lock:
        event = _threads.get(run_id)
    if event is not None:
        event.set()
    elif requested:
        run = store.get_run(run_id)
        if run and run["status"] == "queued":
            store.finish(run_id, "cancelled", "Cancelled before it started.")
    return requested


# ---- summaries and comparison --------------------------------------------------

def _median(values):
    data = [v for v in values if isinstance(v, (int, float))]
    return statistics.median(data) if data else None


def answer_accuracy(results: list) -> dict | None:
    """InfiAgent-DABench accuracies over the results that carry an ``answers`` check.

    Like the original script, only replies that were scored count: a request
    error has no check results and is left out here (``pass_rate`` counts it as
    a failure). by_question = every sub-answer right, proportional = mean share
    of right sub-answers per question, by_subquestion = right / all sub-answers.
    """
    scored = []
    for result in results:
        parts = [c for c in result.get("checks") or [] if c.get("type") == "answers"
                 and isinstance(c.get("total"), int) and c["total"] > 0 and isinstance(c.get("matched"), int)]
        if parts:
            scored.append((sum(c["matched"] for c in parts), sum(c["total"] for c in parts)))
    if not scored:
        return None
    return dict(questions=len(scored),
                by_question=sum(matched == total for matched, total in scored) / len(scored),
                proportional=sum(matched / total for matched, total in scored) / len(scored),
                by_subquestion=sum(matched for matched, _ in scored) / sum(total for _, total in scored))


def external_score(results: list) -> float | None:
    """Mean score (0..1) an external evaluator gave; unscored results count as 0, none scored = None."""
    scores, any_scored = [], False
    for r in results:
        checks = r.get("checks")
        if checks is None:
            try:
                checks = json.loads(r.get("checks_json") or "[]")
            except ValueError:
                checks = []
        external = [c for c in checks if isinstance(c, dict) and c.get("type") == "external"]
        if not external:
            continue
        score = external[0].get("score")
        any_scored = any_scored or isinstance(score, (int, float))
        scores.append(float(score) if isinstance(score, (int, float)) else 0.0)
    return (sum(scores) / len(scores)) if any_scored and scores else None


def summarize(run: dict, results: list, *, sessions: dict | None = None, pricing: dict | None = None) -> dict:
    """Pass rates (overall and per level), latency, tokens, API calls and cost per task for one run."""
    sessions, pricing = sessions or {}, pricing or {}
    count = len(results)
    levels = {}
    for level in LEVELS:
        subset = [r for r in results if r.get("level") == level]
        levels[level] = dict(tasks=len(subset), passed=sum(bool(r["passed"]) for r in subset),
                             pass_rate=(sum(bool(r["passed"]) for r in subset) / len(subset)) if subset else None)
    ok = [r for r in results if not r.get("error")]
    tokens = [r["total_tokens"] for r in results if isinstance(r.get("total_tokens"), int)]
    calls, cost_values, priced, routed = [], [], 0, []
    for r in results:
        session = sessions.get(r.get("session_id") or "")
        if session:
            calls.append(session["api_calls"])
            if session.get("routing"):
                routed.append(session["routing"])
            costs = [reporting.estimate_cost(pricing, model, m["input_tokens"], m["output_tokens"],
                                             m["cache_read_tokens"]) for model, m in session["models"].items()]
            if costs and all(c is not None for c in costs):
                cost_values.append(sum(costs))
                priced += 1
                continue
        if pricing and r.get("response_model") and r.get("input_tokens") is not None:
            value = reporting.estimate_cost(pricing, r["response_model"], r["input_tokens"], r.get("output_tokens"))
            if value is not None:
                cost_values.append(value)
                priced += 1
    return dict(
        tasks=count, passed=sum(bool(r["passed"]) for r in results), errors=sum(bool(r.get("error")) for r in results),
        pass_rate=(sum(bool(r["passed"]) for r in results) / count) if count else None, levels=levels,
        median_latency_s=_median([r.get("latency_s") for r in ok]),
        p95_latency_s=reporting.percentile([r.get("latency_s") for r in ok], 0.95),
        tokens_per_task=(sum(tokens) / len(tokens)) if tokens else None,
        api_calls_per_task=(sum(calls) / len(calls)) if calls else None,
        cost_per_task=(sum(cost_values) / len(cost_values)) if cost_values else None,
        cost_total=sum(cost_values) if cost_values else None, cost_coverage=(priced / count) if count else None,
        # Router behaviour, from telemetry: share of model calls sent to the cheap model, and how often
        # the model changed from one step to the next within a task.
        cheap_step_share=(sum(r["cheap"] for r in routed) / sum(r["steps"] for r in routed)) if routed else None,
        switches_per_task=(sum(r["switches"] for r in routed) / len(routed)) if routed else None,
        answers=answer_accuracy(results), external_score=external_score(results))


COMPARE_ROWS = (("pass_rate", "Pass rate", "percent"), ("external_score", "Score (external evaluator)", "percent"), ("L1", "Pass rate L1", "percent"),
                ("L2", "Pass rate L2", "percent"), ("L3", "Pass rate L3", "percent"),
                ("median_latency_s", "Median latency", "seconds"), ("p95_latency_s", "p95 latency", "seconds"),
                ("tokens_per_task", "Tokens per task", "number"), ("api_calls_per_task", "API calls per task", "number"),
                ("cost_per_task", "Cost per task", "money"), ("cost_total", "Cost (priced tasks)", "money"),
                ("cheap_step_share", "Cheap-model steps", "percent"),
                ("switches_per_task", "Model switches per task", "number"),
                ("errors", "Request errors", "number"), ("tasks", "Results", "number"))


def compare(runs_with_summaries: list) -> dict:
    """Matrix rows for 2-4 runs: [{key, label, format, values}]."""
    rows = []
    for key, label, kind in COMPARE_ROWS:
        values = []
        for _, summary in runs_with_summaries:
            if key in LEVELS:
                values.append(summary["levels"][key]["pass_rate"])
            else:
                values.append(summary.get(key))
        rows.append(dict(key=key, label=label, format=kind, values=values))
    return dict(columns=[dict(id=run["id"], label=run.get("label") or "", suite=run["suite"],
                              suite_hash=run.get("suite_hash"), config_hash=run.get("config_hash"),
                              status=run["status"]) for run, _ in runs_with_summaries], rows=rows,
                same_suite=len({run["suite_hash"] for run, _ in runs_with_summaries}) == 1)


# ---- CLI ----------------------------------------------------------------------

def _paths(args, environ):
    home = Path(args.home or environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
    data = Path(args.data_dir or environ.get("ASSISTANT_DATA_DIR") or home / "assistant-core").expanduser()
    return home, data


def _fmt(value, kind="text"):
    if value is None:
        return "—"
    if kind == "percent":
        return f"{value * 100:.0f}%"
    if kind == "seconds":
        return f"{value:.1f}s"
    if kind == "money":
        return f"${value:.4f}"
    if kind == "number":
        return f"{value:,.1f}" if isinstance(value, float) and not value.is_integer() else f"{int(value):,}"
    return str(value)


def _table(headers, rows) -> str:
    cells = [[str(c) for c in headers]] + [[str(c) for c in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in cells]
    lines.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(lines)


def _clean(text) -> str:
    """Terminal-safe: stored strings are untrusted (no escape sequences)."""
    return "".join(ch if ch.isprintable() else "?" for ch in str(text if text is not None else "—"))


def _summary_for(store, run, data_dir, pricing):
    results = store.results(run["id"])
    sessions = reporting.session_totals(data_dir / "telemetry.sqlite3", [r.get("session_id") for r in results])
    return results, summarize(run, results, sessions=sessions, pricing=pricing)


def main(argv=None, environ=None, out=None) -> int:
    environ = os.environ if environ is None else environ
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m assistant_core.bench", description=__doc__.split("\n\n")[0])
    parser.add_argument("--home", help="Hermes home (default: HERMES_HOME or ~/.hermes)")
    parser.add_argument("--data-dir", help="assistant-core data dir (default: ASSISTANT_DATA_DIR or <home>/assistant-core)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("suites", help="list available suites")
    run_cmd = commands.add_parser("run", help="run a suite against the Hermes API server")
    run_cmd.add_argument("--suite", required=True)
    run_cmd.add_argument("--label", default="")
    run_cmd.add_argument("--api-url", help="override HERMES_BENCH_API_URL (key still from the environment)")
    run_cmd.add_argument("--repeats", type=int, default=1)
    run_cmd.add_argument("--concurrency", type=int, default=1)
    run_cmd.add_argument("--no-responses", action="store_true", help="do not store reply excerpts")
    resume_cmd = commands.add_parser("resume", help="finish a cancelled or failed run (tasks with a result are kept)")
    resume_cmd.add_argument("run")
    resume_cmd.add_argument("--api-url", help="override HERMES_BENCH_API_URL (key still from the environment)")
    resume_cmd.add_argument("--retry-errors", default=",".join(RETRYABLE_ERRORS),
                            help="comma-separated result errors to run again "
                                 f"(default {','.join(RETRYABLE_ERRORS)}; '' for none)")
    resume_cmd.add_argument("--force", action="store_true", help="resume even if config.yaml changed")
    resume_cmd.add_argument("--no-responses", action="store_true", help="do not store reply excerpts")
    list_cmd = commands.add_parser("list", help="recent runs")
    list_cmd.add_argument("--limit", type=int, default=20)
    show = commands.add_parser("show", help="one run with per-task results")
    show.add_argument("run")
    cmp = commands.add_parser("compare", help="compare 2-4 runs")
    cmp.add_argument("runs", nargs="+")
    stop = commands.add_parser("cancel", help="ask a running run to stop")
    stop.add_argument("run")
    export_cmd = commands.add_parser("export", help="write tasks.csv and steps.csv for runs (analysis, router data)")
    export_cmd.add_argument("runs", nargs="+")
    export_cmd.add_argument("--out", required=True, help="output directory")
    args = parser.parse_args(argv)
    home, data_dir = _paths(args, environ)
    write = lambda text="": print(text, file=out)
    try:
        if args.command == "suites":
            suites, problems = load_suites(home)
            write(_table(["Suite", "Source", "Tasks", "L1", "L2", "L3", "Description"],
                         [[_clean(s.name), s.source, len(s.tasks), *s.levels().values(), _clean(s.description[:60])]
                          for s in suites.values()]))
            for problem in problems:
                write(f"! {_clean(problem['file'])}: {_clean(problem['error'])}")
            return 0
        store = BenchStore(data_dir / "bench.sqlite3")
        try:
            return _command(args, store, home, data_dir, environ, write)
        finally:
            store.close()
    except BenchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


def _command(args, store, home, data_dir, environ, write) -> int:
    pricing = reporting.load_pricing(data_dir / "pricing.yaml")
    if args.command in ("run", "resume"):
        suites, _ = load_suites(home)
        if args.command == "resume":
            old = store.get_run(args.run)
            if old is None:
                raise BenchError("No such run.")
            suite = suites.get(old["suite"])
            if suite is None:
                raise BenchError(f"The suite {old['suite']} of this run is no longer available.")
            target = resolve_target(environ, args.api_url)
            retry = tuple(e.strip() for e in args.retry_errors.split(",") if e.strip())
            run = resume_run(store, old["id"], suite, retry_errors=retry, home=home, force=args.force)
            if run["gateway_config_hash"] and gateway_config_hash(data_dir / "telemetry.sqlite3") not in (
                    "", run["gateway_config_hash"]):
                write("! the gateway now runs another config.yaml than when this run started.")
            write(f"Run {run['id']}: {suite.name} x{run['repeats']} -> {target.host} (resumed: "
                  f"{run['completed']}/{run['total']} kept, config {run['config_hash'] or 'unknown'})")
        else:
            suite = suites.get(args.suite)
            if suite is None:
                raise BenchError(f"Unknown suite. Available: {', '.join(sorted(suites)) or 'none'}.")
            target = resolve_target(environ, args.api_url)
            run = prepare_run(store, suite, target, label=args.label, repeats=args.repeats,
                              concurrency=args.concurrency, home=home, telemetry_db=data_dir / "telemetry.sqlite3")
            if run["gateway_config_hash"] and run["config_hash"] and run["gateway_config_hash"] != run["config_hash"]:
                write("! config.yaml changed after the gateway started: restart the gateway so it uses these settings.")
            write(f"Run {run['id']}: {suite.name} x{run['repeats']} -> {target.host} ({run['total']} requests, "
                  f"config {run['config_hash'] or 'unknown'})")
        event = threading.Event()
        runner = BenchRunner(store, run["id"], suite, target, store_responses=not args.no_responses,
                             cancel_event=event, progress=lambda r: write(
                                 f"  {'PASS' if r['passed'] else 'FAIL'} {_clean(r['task_id'])}#{r['attempt']} "
                                 f"{_fmt(r['latency_s'], 'seconds')} {_clean(r['error'] or '')}"))
        try:
            run = runner.run()
        except KeyboardInterrupt:
            event.set()
            store.finish(run["id"], "cancelled", "Interrupted from the terminal.")
            write(f"Cancelled. Continue later with: python -m assistant_core.bench resume {run['id']}")
            return 130
        _, summary = _summary_for(store, run, data_dir, pricing)
        write(f"{run['status']}: {summary['passed']}/{summary['tasks']} passed "
              f"({_fmt(summary['pass_rate'], 'percent')}), median {_fmt(summary['median_latency_s'], 'seconds')}")
        return 0 if run["status"] == "done" else 1
    if args.command == "list":
        store.recover_stale()
        runs = store.list_runs(args.limit)
        write(_table(["Run", "Suite", "Label", "Status", "Passed", "Config", "Target"],
                     [[r["id"], _clean(r["suite"]), _clean(r["label"] or "—"), r["status"],
                       f"{r['passed']}/{r['completed']} of {r['total']}", r["config_hash"] or "—",
                       _clean(r["target_host"] or "—")] for r in runs]))
        return 0
    if args.command == "cancel":
        if not cancel(store, args.run):
            raise BenchError("That run is not active.")
        write("Cancellation requested; in-flight requests finish first.")
        return 0
    if args.command == "export":
        from . import bench_export
        try:
            suites, _ = load_suites(home)
            prompts = {name: {task.id: task.prompt for task in suite.tasks} for name, suite in suites.items()}
            done = bench_export.export(store, data_dir, args.runs, Path(args.out), pricing, prompts)
        except ValueError as error:
            raise BenchError(str(error)) from None
        write(f"{done['tasks']} task rows, {done['steps']} step rows -> {', '.join(done['files'])}")
        for item in done["unjoined"]:
            write(f"! no telemetry steps for {_clean(item)}")
        return 0
    ids = [args.run] if args.command == "show" else args.runs
    if args.command == "compare" and not 2 <= len(ids) <= 4:
        raise BenchError("Compare 2 to 4 runs.")
    runs = []
    for run_id in ids:
        run = store.get_run(run_id)
        if run is None:
            raise BenchError(f"Unknown run {_clean(run_id)[:40]}.")
        runs.append(run)
    if args.command == "show":
        run = runs[0]
        results, summary = _summary_for(store, run, data_dir, pricing)
        write(f"{run['id']}  {_clean(run['suite'])}  label={_clean(run['label'] or '—')}  {run['status']}"
              f"{'  (' + _clean(run['reason']) + ')' if run['reason'] else ''}")
        write(f"config {run['config_hash'] or '—'}  gateway {run['gateway_config_hash'] or '—'}  "
              f"target {_clean(run['target_host'])}  repeats {run['repeats']}  concurrency {run['concurrency']}")
        write(_table(["Task", "#", "Level", "Result", "Latency", "Tokens", "Failed checks / error"],
                     [[_clean(r["task_id"]), r["attempt"], r["level"], "pass" if r["passed"] else "FAIL",
                       _fmt(r["latency_s"], "seconds"), _fmt(r["total_tokens"], "number"),
                       _clean(r["error"] or "; ".join(c["detail"] for c in r["checks"] if not c["passed"]) or "")[:80]]
                      for r in results]))
        write(f"Pass rate {_fmt(summary['pass_rate'], 'percent')}  median {_fmt(summary['median_latency_s'], 'seconds')}"
              f"  tokens/task {_fmt(summary['tokens_per_task'], 'number')}  cost/task {_fmt(summary['cost_per_task'], 'money')}")
        answers = summary["answers"]
        if answers:
            write(f"Answers ({answers['questions']} scored replies): by question {answers['by_question']:.1%}"
                  f"  proportional by sub-question {answers['proportional']:.1%}"
                  f"  by sub-question {answers['by_subquestion']:.1%}")
        if summary.get("external_score") is not None:
            write(f"Score from the external evaluator (mean over results, 0-1): {summary['external_score']:.3f}")
        return 0
    matrix = compare([(run, _summary_for(store, run, data_dir, pricing)[1]) for run in runs])
    write(_table(["Metric", *[_clean(c["label"] or c["id"]) for c in matrix["columns"]]],
                 [[row["label"], *[_fmt(v, row["format"]) for v in row["values"]]] for row in matrix["rows"]]
                 + [["Config", *[c["config_hash"] or "—" for c in matrix["columns"]]],
                    ["Suite", *[_clean(c["suite"]) + "@" + (c["suite_hash"] or "?") for c in matrix["columns"]]]]))
    if not matrix["same_suite"]:
        write("! The runs used different suite versions; compare with care.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
