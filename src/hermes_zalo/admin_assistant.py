"""Read-only operations snapshots of the Hermes runtime for the admin console.

``AssistantAdmin.snapshot(section)`` returns plain dicts for the sections
overview, runtime, skills, routines and usage. Guarantees:

- Only the Hermes home passed explicitly, ``$HERMES_HOME`` or the repo's
  ``.runtime/hermes-home`` is read; never the personal ``~/.hermes`` default.
- Nothing is written: SQLite opens read-only (``immutable`` when no writer has
  a WAL open, because a plain read-only open would create -wal/-shm files),
  JSON/YAML/Markdown are only read, no shell, process control or Docker socket.
- Skills and routines expose allowlisted metadata only; free text is redacted
  (URLs, e-mails, filesystem paths, environment references, secret-like
  values). Skill files are never followed through symlinks and scans are bounded.
- Missing files or unknown schemas give ``available=False`` and ``None``
  values, never invented zeros. ``mode="demo"`` returns deterministic synthetic
  data marked ``source="demo"`` without touching the filesystem.
"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import time
from urllib.parse import quote

import yaml


SECTIONS = ("overview", "runtime", "skills", "routines", "usage", "users")
ROW_LIMIT = 100
ACTIVITY_LIMIT = 20
DAILY_LIMIT = 30
SKILL_LIMIT = 500
SKILL_SCAN_DIRS = 2000
SKILL_SCAN_DEPTH = 4
FRONTMATTER_BYTES = 16 * 1024
JSON_MAX_BYTES = 2 * 1024 * 1024
CONFIG_MAX_BYTES = 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[2]
TELEMETRY_COLUMNS = frozenset({"ts", "provider", "model", "input_tokens", "output_tokens", "duration_s", "error_type"})
VIETNAM_TZ = timezone(timedelta(hours=7))  # same day boundary as assistant_core.quota
ALLOWLIST_ENV = (("telegram", "TELEGRAM_ALLOWED_USERS"), ("zalo", "ZALO_ALLOWED_USERS"))
# Mirrors assistant_core.settings: name -> (default, maximum); minimum is 1.
LIMIT_ENV = {"ASSISTANT_USER_DAILY_LIMIT": (30, 10000), "ASSISTANT_TOTAL_DAILY_LIMIT": (500, 1000000)}
USER_ID = re.compile(r"[A-Za-z0-9_.@:-]{1,120}")
USERS_MESSAGE = ("Only users from the explicit allowlists (TELEGRAM_ALLOWED_USERS, ZALO_ALLOWED_USERS, "
                 "Zalo configuration) are shown. This does not represent all access: allowed group members and registered users "
                 "are not included. Limits are configured values, not proof of runtime enforcement.")
DEMO_UPDATED_AT = "2026-10-31T08:00:00+00:00"
COST_SCOPE = "all_hermes_sessions"  # spend of every Hermes session in this home, not only telemetry calls

_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_SECRET_KV = re.compile(r"(?i)\b[\w-]*(?:key|token|secret|password|passwd|credential)[\w-]*\s*[:=]\s*\S+")
_SECRET = re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|xox[abprs]|AKIA|AIza)[-_A-Za-z0-9]{8,}|\b[A-Za-z0-9_\-+/]{32,}={0,2}")
_ENV = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|%[A-Za-z_][A-Za-z0-9_]*%")
_PATH = re.compile(r"(?<![\w/])(?:~|\.{1,2})?/[\w.\-]+(?:/[\w.\-]*)*|\b[A-Za-z]:\\\S*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def redact(value, limit=200):
    """Bounded single-line text with URLs, paths, env references and secrets removed."""
    if value is None or isinstance(value, (dict, list, tuple, set, bytes)):
        return None
    text = _CONTROL.sub(" ", str(value))
    for pattern, token in ((_URL, "[url]"), (_EMAIL, "[email]"), (_SECRET_KV, "[secret]"),
                           (_SECRET, "[secret]"), (_ENV, "[env]"), (_PATH, "[path]")):
        text = pattern.sub(token, text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text or None


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(value):
    """Epoch seconds or ISO text -> ISO 8601 UTC; anything else -> None."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat(timespec="seconds")
        if isinstance(value, str) and value.strip():
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _query(query):
    if query is None:
        return None
    if not isinstance(query, str) or len(query) > 100:
        raise ValueError("The filter must be a string of at most 100 characters.")
    return query.strip().casefold() or None


def _matches(query, *values):
    return query is None or any(query in str(value).casefold() for value in values if value)


def _section(section, *, available, source, message=None, items=None, **extra):
    return {"section": section, "available": available, "source": source, "message": message,
            "items": items if items is not None else [], "updated_at": _now_iso(), **extra}


class _Unavailable(Exception):
    """A data source is missing or has an unknown shape (message is user-facing, path-free)."""


def _open_regular(path: Path):
    """Open a regular file without following a final symlink or blocking on a FIFO."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    handle = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
        handle.close()
        raise _Unavailable("the data source is not a regular file")
    return handle


def _connect_ro(path: Path):
    """Read-only SQLite connection that never creates files next to the database."""
    if path.is_symlink() or not path.is_file():
        raise _Unavailable("no data is available")
    with _open_regular(path) as handle:
        header = handle.read(100)
    if not header.startswith(b"SQLite format 3\x00"):
        raise _Unavailable("the data file has an unsupported format")
    wal = len(header) > 18 and header[18] == 2
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    if wal and not Path(str(path) + "-wal").exists():
        # No writer holds the WAL: a plain read-only open would still create -wal/-shm.
        uri += "&immutable=1"
    db = sqlite3.connect(uri, uri=True)
    db.execute("PRAGMA query_only=1")
    return db


def _columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _read_bounded(path: Path, maximum: int) -> bytes:
    with _open_regular(path) as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise _Unavailable("file exceeds the size limit")
    return data


class AssistantAdmin:
    def __init__(self, work, conversations, *, mode="local", home=None, assistant_data_dir=None, clock=None):
        self.work, self.conversations, self.mode = work, conversations, mode
        self._clock = clock or (lambda: datetime.now(VIETNAM_TZ))
        if home is not None:
            self.home = Path(home)
        elif os.environ.get("HERMES_HOME"):
            self.home = Path(os.environ["HERMES_HOME"])
        else:
            self.home = REPO_ROOT / ".runtime" / "hermes-home"
        data_dir = assistant_data_dir or os.environ.get("ASSISTANT_DATA_DIR")
        self.assistant_dir = Path(data_dir) if data_dir else self.home / "assistant-core"

    # ---- public ---------------------------------------------------------

    def snapshot(self, section, query=None):
        if section not in SECTIONS:
            raise ValueError("Unknown section: " + str(section)[:40])
        query = _query(query)
        if self.mode == "demo":
            return _demo(section, query)
        return getattr(self, "_" + section)(query)

    # ---- sources --------------------------------------------------------

    def _home_path(self, *parts):
        """Path inside the home; symlinks in the home or any component are refused."""
        path = self.home
        if path.is_symlink():
            raise _Unavailable("the data location is a symbolic link")
        for part in parts:
            path = path / part
            if path.is_symlink():
                raise _Unavailable("the data location is a symbolic link")
        return path

    def _config(self):
        try:
            path = self._home_path("config.yaml")
            data = yaml.safe_load(_read_bounded(path, CONFIG_MAX_BYTES).decode("utf-8-sig"))
        except (OSError, _Unavailable, UnicodeDecodeError, yaml.YAMLError):
            return {}
        return data if isinstance(data, dict) else {}

    def _telemetry_path(self):
        if self.assistant_dir.is_symlink():
            raise _Unavailable("the data location is a symbolic link")
        return self.assistant_dir / "telemetry.sqlite3"

    def _telemetry(self):
        db = _connect_ro(self._telemetry_path())
        try:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "api_calls" not in tables or not TELEMETRY_COLUMNS <= _columns(db, "api_calls"):
                raise _Unavailable("unsupported telemetry schema")
        except BaseException:
            db.close()
            raise
        return db, tables

    def _sessions_db(self):
        db = _connect_ro(self._home_path("state.db"))
        try:
            if not {"started_at", "source"} <= _columns(db, "sessions"):
                raise _Unavailable("unsupported state database schema")
        except BaseException:
            db.close()
            raise
        return db

    def _jobs(self):
        path = self._home_path("cron", "jobs.json")
        if not path.is_file():
            raise _Unavailable("no scheduled jobs have been created")
        try:
            data = json.loads(_read_bounded(path, JSON_MAX_BYTES).decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            raise _Unavailable("the job file cannot be read") from None
        jobs = data.get("jobs") if isinstance(data, dict) else data
        if not isinstance(jobs, list):
            raise _Unavailable("unsupported job file schema")
        return [job for job in jobs if isinstance(job, dict)]

    def _memory_entries(self, name):
        path = self._home_path("memories", name)
        if not path.is_file():
            return None
        text = _read_bounded(path, JSON_MAX_BYTES).decode("utf-8", "replace")
        return len([part for part in re.split(r"(?m)^\s*§\s*$", text) if part.strip()])

    # ---- sections -------------------------------------------------------

    def _skills(self, query):
        try:
            root = self._home_path("skills")
        except _Unavailable:
            root = None
        if root is None or not root.is_dir():
            return _section("skills", available=False, source="hermes-home", message="No skills directory is available.",
                            total=None, truncated=False)
        skills_cfg = self._config().get("skills")
        disabled = set()
        if isinstance(skills_cfg, dict) and isinstance(skills_cfg.get("disabled"), list):
            disabled = {str(name) for name in skills_cfg["disabled"]}
        items, skipped, visited, truncated = [], 0, 0, False
        for current, dirs, files in os.walk(root, followlinks=False):
            visited += 1
            rel = Path(current).relative_to(root)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(current) / d).is_symlink())
            if len(rel.parts) >= SKILL_SCAN_DEPTH:
                dirs[:] = []
            if visited > SKILL_SCAN_DIRS or len(items) >= SKILL_LIMIT:
                truncated = True
                break
            if "SKILL.md" not in files:
                continue
            dirs[:] = []  # a skill directory's subfolders hold references, not skills
            skill_file = Path(current) / "SKILL.md"
            if skill_file.is_symlink():
                skipped += 1
                continue
            item = self._skill_item(skill_file, rel, disabled)
            if item is None:
                skipped += 1
            elif _matches(query, item["name"], item["category"], item["description"], *item["tags"]):
                items.append(item)
        items.sort(key=lambda item: (item["category"] or "", item["name"]))
        return _section("skills", available=True, source="hermes-home", items=items[:ROW_LIMIT],
                        total=len(items), truncated=truncated or len(items) > ROW_LIMIT, skipped=skipped)

    @staticmethod
    def _skill_item(path: Path, rel: Path, disabled):
        try:
            raw = _read_bounded(path, 1024 * 1024)[:FRONTMATTER_BYTES].decode("utf-8", "replace")
        except (OSError, _Unavailable):
            return None
        meta = {}
        match = re.match(r"\A﻿?---\s*\n(.*?)\n---\s*(?:\n|\Z)", raw, re.S)
        if match:
            try:
                loaded = yaml.safe_load(match.group(1))
                meta = loaded if isinstance(loaded, dict) else {}
            except yaml.YAMLError:
                meta = {}
        name = redact(meta.get("name") if isinstance(meta.get("name"), str) else rel.name, 80) or redact(rel.name, 80)
        tags = meta.get("tags")
        hermes_meta = (meta.get("metadata") or {}).get("hermes") if isinstance(meta.get("metadata"), dict) else None
        if not isinstance(tags, list) and isinstance(hermes_meta, dict):
            tags = hermes_meta.get("tags")
        category = redact(rel.parts[0], 60) if len(rel.parts) > 1 else None
        return {
            "id": (category + ":" if category else "") + (name or ""),
            "name": name,
            "category": category,
            "description": redact(meta.get("description"), 240) if isinstance(meta.get("description"), str) else None,
            "version": redact(meta.get("version"), 20) if isinstance(meta.get("version"), (str, int, float)) else None,
            "tags": [redact(tag, 30) for tag in tags[:8] if isinstance(tag, str) and redact(tag, 30)]
            if isinstance(tags, list) else [],
            "enabled": name not in disabled and rel.name not in disabled,
        }

    def _routines(self, query):
        try:
            jobs = self._jobs()
        except (OSError, _Unavailable) as exc:
            return _section("routines", available=False, source="hermes-home", message=_message(exc), total=None)
        items = []
        for job in jobs:
            schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
            repeat = job.get("repeat") if isinstance(job.get("repeat"), dict) else {}
            skills = job.get("skills") if isinstance(job.get("skills"), list) else []
            item = {
                "id": redact(job.get("id"), 64),
                "name": redact(job.get("name"), 80),
                "schedule": redact(job.get("schedule_display") or schedule.get("display") or schedule.get("expr"), 80),
                "schedule_kind": redact(schedule.get("kind"), 20),
                "enabled": job.get("enabled") if isinstance(job.get("enabled"), bool) else None,
                "state": redact(job.get("state"), 20),
                "next_run_at": _iso(job.get("next_run_at")),
                "last_run_at": _iso(job.get("last_run_at")),
                "last_status": redact(job.get("last_status"), 20),
                "has_error": bool(job.get("last_error") or job.get("last_delivery_error")),
                "failure_streak": _int(job.get("failure_streak")),
                "repeat": {"times": _int(repeat.get("times")), "completed": _int(repeat.get("completed"))},
                "skills": [redact(name, 60) for name in skills[:8] if isinstance(name, str)],
            }
            if _matches(query, item["name"], item["schedule"], *item["skills"]):
                items.append(item)
        items.sort(key=lambda item: (item["next_run_at"] is None, item["next_run_at"] or "", item["name"] or ""))
        return _section("routines", available=True, source="hermes-home", items=items[:ROW_LIMIT],
                        total=len(items), truncated=len(items) > ROW_LIMIT)

    def _usage(self, query):
        try:
            db, _ = self._telemetry()
        except (OSError, sqlite3.Error, _Unavailable) as exc:
            return _section("usage", available=False, source="assistant-telemetry", message=_message(exc),
                            **_usage_totals(None), daily=[], timezone="UTC")
        try:
            with closing(db):
                calls, input_tokens, output_tokens, errors = db.execute(
                    "SELECT COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(error_type IS NOT NULL) "
                    "FROM api_calls").fetchone()
                where, args = "", []
                if query:
                    where, args = " WHERE instr(lower(coalesce(model,'') || ' ' || coalesce(provider,'')), ?) > 0", [query]
                rows = db.execute("SELECT ts, provider, model, input_tokens, output_tokens, duration_s, error_type "
                                  "FROM api_calls" + where + " ORDER BY ts DESC, rowid DESC LIMIT ?",
                                  [*args, ROW_LIMIT]).fetchall()
                daily = db.execute("SELECT date(ts, 'unixepoch') AS day, COUNT(*), SUM(input_tokens), "
                                   "SUM(output_tokens) FROM api_calls" + where +
                                   " GROUP BY day ORDER BY day DESC LIMIT ?", [*args, DAILY_LIMIT]).fetchall()
        except sqlite3.Error:
            return _section("usage", available=False, source="assistant-telemetry",
                            message="Telemetry could not be read.", **_usage_totals(None), daily=[], timezone="UTC")
        totals = {"calls": calls, "errors": errors if calls else 0,
                  "input_tokens": _sum(input_tokens, calls), "output_tokens": _sum(output_tokens, calls)}
        totals.update(self._cost())
        items = [{"ts": _iso(ts), "provider": redact(provider, 80), "model": redact(model, 120),
                  "input_tokens": _int(inp), "output_tokens": _int(out), "duration_s": _num(duration),
                  "error_type": redact(error, 80)} for ts, provider, model, inp, out, duration, error in rows]
        daily = [{"day": day, "calls": count, "input_tokens": _sum(inp, count), "output_tokens": _sum(out, count)}
                 for day, count, inp, out in daily if day]
        return _section("usage", available=True, source="assistant-telemetry", items=items,
                        **totals, daily=daily, timezone="UTC")

    def _cost(self):
        """Spend Hermes recorded per session (telemetry carries no prices).

        Covers every session in this home (``cost_scope``), so it is not limited
        to the calls listed in usage. Missing data -> unavailable; NaN, infinite,
        negative, non-numeric values or an unreadable table -> unknown. Never raises.
        """
        result = {"cost_usd": None, "cost_status": "unavailable", "cost_scope": COST_SCOPE}
        try:
            db = self._sessions_db()
        except (OSError, _Unavailable):
            return result
        except sqlite3.Error:
            return {**result, "cost_status": "unknown"}
        try:
            with closing(db):
                if not {"actual_cost_usd", "estimated_cost_usd"} <= _columns(db, "sessions"):
                    return result
                cost = "coalesce(actual_cost_usd, estimated_cost_usd)"
                checks = " OR ".join(
                    f"({c} IS NOT NULL AND (typeof({c}) NOT IN ('integer', 'real') OR {c} < 0 OR {c} > 1e12))"
                    for c in ("actual_cost_usd", "estimated_cost_usd"))
                invalid, actual_rows, priced, total = db.execute(
                    f"SELECT SUM({checks}), COUNT(actual_cost_usd), COUNT({cost}), SUM({cost}) FROM sessions").fetchone()
        except (sqlite3.Error, TypeError, ValueError):
            return {**result, "cost_status": "unknown"}
        if not priced:
            return result
        if invalid or not isinstance(total, (int, float)) or not math.isfinite(total) or total < 0:
            return {**result, "cost_status": "unknown"}
        return {**result, "cost_usd": round(float(total), 6),
                "cost_status": "actual" if actual_rows == priced else "estimated"}

    def _allowlist(self):
        """(platform, user_id) pairs from the explicit allowlists only, plus how many entries were rejected."""
        sources = [(platform, os.environ.get(name) or "") for platform, name in ALLOWLIST_ENV]
        settings = getattr(self.work, "settings", None)
        configured = getattr(settings, "allowed_users", None)
        if isinstance(configured, (set, frozenset, list, tuple)):
            sources.append(("zalo", list(configured)))
        users, rejected = {}, 0
        for platform, raw in sources:
            for entry in (raw.split(",") if isinstance(raw, str) else raw):
                user_id = entry.strip() if isinstance(entry, str) else ""
                if not user_id and isinstance(entry, str):
                    continue  # empty item between commas
                if not USER_ID.fullmatch(user_id):  # also rejects "*" and other wildcards
                    rejected += 1
                    continue
                users.setdefault((platform, user_id), None)
        return sorted(users), rejected

    def _limits(self):
        """Configured daily limits (not proof of runtime enforcement).

        Uses assistant_core's own parser when importable. In the sealed Hermes
        plugin layout assistant_core lives in a separate plugin, so fall back to
        the same rules: defaults 30/500, integers within 1..10000 / 1..1000000.
        """
        values = {name: os.environ[name] for name in LIMIT_ENV if name in os.environ}
        try:
            from assistant_core.settings import CoreSettings, SettingsError
        except ImportError:
            limits = [_parse_limit(values, name, default, maximum)
                      for name, (default, maximum) in LIMIT_ENV.items()]
            if None in limits:
                return None, None, "Configured limits are invalid."
            return limits[0], limits[1], None
        try:
            settings = CoreSettings.from_mapping(values, default_dir=self.assistant_dir)
        except SettingsError:
            return None, None, "Configured limits are invalid."
        return settings.user_daily_limit, settings.total_daily_limit, None

    def _quota_usage(self, day, people):
        """{person hash: turns today} and the day's total, or (None, None) when the store is missing."""
        try:
            if self.assistant_dir.is_symlink():
                raise _Unavailable("the data location is a symbolic link")
            db = _connect_ro(self.assistant_dir / "quota.sqlite3")
        except (OSError, sqlite3.Error, _Unavailable):
            return None, None
        try:
            with closing(db):
                if not {"day", "person", "message"} <= _columns(db, "turns"):
                    return None, None
                total = db.execute("SELECT COUNT(*) FROM turns WHERE day=?", (day,)).fetchone()[0]
                hashes = list(people)
                counts = {}
                for start in range(0, len(hashes), 50):
                    chunk = hashes[start:start + 50]
                    counts.update(db.execute(
                        f"SELECT person, COUNT(*) FROM turns WHERE day=? AND person IN ({','.join('?' * len(chunk))}) "
                        "GROUP BY person", (day, *chunk)).fetchall())
        except sqlite3.Error:
            return None, None
        return {person: counts.get(person, 0) for person in hashes}, total

    def _users(self, query):
        pairs, rejected = self._allowlist()
        user_limit, total_limit, limit_error = self._limits()
        day = self._clock().astimezone(VIETNAM_TZ).strftime("%Y-%m-%d")
        selected = [(p, u) for p, u in pairs if _matches(query, p, u)]
        truncated = len(selected) > ROW_LIMIT
        selected = selected[:ROW_LIMIT]
        keyed = {hashlib.sha256(f"{p}:{u}".encode()).hexdigest(): (p, u) for p, u in selected}  # QuotaStore._hash
        used, total_used = self._quota_usage(day, keyed)
        items = [{"user_id": u, "platform": p, "status": "configured",
                  "used_today": used[h] if used is not None else None, "user_daily_limit": user_limit}
                 for h, (p, u) in keyed.items()]
        message = USERS_MESSAGE + (" " + limit_error if limit_error else "") + (
            "" if used is not None else " Quota data is unavailable, so usage today is unknown.")
        return _section("users", available=bool(pairs), source="allowlist", message=message, items=items,
                        day=day, timezone="Asia/Ho_Chi_Minh (UTC+7)", total=len(pairs), truncated=truncated,
                        rejected=rejected, total_used_today=total_used, total_daily_limit=total_limit,
                        user_daily_limit=user_limit, quota_available=used is not None,
                        coverage="explicit_allowlist_only", limits_source="config")

    def _runtime(self, query=None):
        info = self._runtime_info()
        resources = _resources(self.home)
        units = {"cpu_count": None, "load_1m": None}
        items = [{"id": key, "label": _RESOURCE_LABELS[key], "value": value,
                  "unit": units.get(key, "bytes")} for key, value in resources.items()]
        return _section("runtime", available=True, source="system", items=items, **info,
                        resources=resources, controls={"restart": False, "terminal": False})

    def _runtime_info(self):
        settings = getattr(self.work, "settings", None)
        terminal = self._config().get("terminal")
        backend = terminal.get("backend") if isinstance(terminal, dict) else None
        return {"mode": self.mode, "bot_mode": redact(getattr(settings, "bot_mode", None), 20),
                "sandbox_backend": redact(backend, 30) if isinstance(backend, str) else None,
                "hermes_commit": self._hermes_commit(), "uptime_seconds": _uptime()}

    def _hermes_commit(self):
        gateway = sys.modules.get("gateway")
        if getattr(gateway, "__file__", None):
            commit = _git_head(Path(gateway.__file__).resolve().parents[1])
            if commit:
                return commit
        try:
            db, tables = self._telemetry()
        except (OSError, sqlite3.Error, _Unavailable):
            return None
        try:
            with closing(db):
                if "runs" not in tables or not {"hermes_rev", "started_at"} <= _columns(db, "runs"):
                    return None
                row = db.execute("SELECT hermes_rev FROM runs WHERE hermes_rev <> '' "
                                 "ORDER BY started_at DESC LIMIT 1").fetchone()
        except sqlite3.Error:
            return None
        return _commit(row[0]) if row else None

    def _overview(self, query=None):
        counts = {"sessions": None, "memories": None, "skills": None, "routines": None}
        capabilities, activity = [], []
        try:
            with closing(self._sessions_db()) as db:
                counts["sessions"] = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                ended = "ended_at" in _columns(db, "sessions")
                for source, started, done in db.execute(
                        "SELECT source, started_at, " + ("ended_at" if ended else "NULL") +
                        " FROM sessions ORDER BY started_at DESC LIMIT ?", (ACTIVITY_LIMIT,)):
                    activity.append({"kind": "session", "label": "Session " + (redact(source, 30) or "?"),
                                     "status": "ended" if done else "active", "at": _iso(started)})
            capabilities.append(_capability("sessions", "Hermes session storage", "ok", "Session metadata is read in read-only mode."))
        except (OSError, sqlite3.Error, _Unavailable) as exc:
            capabilities.append(_capability("sessions", "Hermes session storage", "unavailable", _message(exc)))
        memory_counts = []
        for name in ("MEMORY.md", "USER.md"):
            try:
                memory_counts.append(self._memory_entries(name))
            except (OSError, _Unavailable):
                memory_counts.append(None)
        if any(count is not None for count in memory_counts):
            counts["memories"] = sum(count or 0 for count in memory_counts)
        skills = self._skills(None)
        if skills["available"]:
            counts["skills"] = skills["total"]
            enabled = sum(1 for item in skills["items"] if item["enabled"])
            capabilities.append(_capability("skills", "Skills", "ok", f"{enabled}/{skills['total']} skills enabled."))
        else:
            capabilities.append(_capability("skills", "Skills", "unavailable", skills["message"]))
        routines = self._routines(None)
        if routines["available"]:
            counts["routines"] = routines["total"]
            capabilities.append(_capability("routines", "Scheduled jobs", "ok", f"{routines['total']} jobs."))
            for item in routines["items"]:
                if item["last_run_at"]:
                    activity.append({"kind": "routine", "label": item["name"] or "Job",
                                     "status": item["last_status"] or "unknown", "at": item["last_run_at"]})
        else:
            capabilities.append(_capability("routines", "Scheduled jobs", "unavailable", routines["message"]))
        usage = self._usage(None)
        if usage["available"]:
            capabilities.append(_capability("telemetry", "Model-call telemetry", "ok", f"{usage['calls']} calls."))
            for item in usage["items"][:ACTIVITY_LIMIT]:
                activity.append({"kind": "api_call", "label": item["model"] or "model",
                                 "status": "error" if item["error_type"] else "ok", "at": item["ts"]})
        else:
            capabilities.append(_capability("telemetry", "Model-call telemetry", "unavailable", usage["message"]))
        quota = self.assistant_dir / "quota.sqlite3"
        present = not self.assistant_dir.is_symlink() and not quota.is_symlink() and quota.is_file()
        capabilities.append(_capability("quota", "Turn quota", "ok" if present else "unavailable",
                                        "Quota data is present." if present else "Quota data is unavailable."))
        runtime = self._runtime_info()
        backend = runtime["sandbox_backend"]
        capabilities.append(_capability(
            "sandbox", "Execution sandbox", "unavailable" if backend is None else "warning" if backend == "local" else "ok",
            "No execution backend is configured." if backend is None else
            f"Configured backend {backend}" + (" (commands run directly on the host)" if backend == "local" else "")
            + "; execution has not been verified."))
        capabilities.append(_capability("controls", "Restart / terminal", "disabled",
                                        "This console is read-only and does not control processes."))
        activity = sorted((a for a in activity if a["at"]), key=lambda a: a["at"], reverse=True)[:ACTIVITY_LIMIT]
        return _section("overview", available=True, source="hermes-home", items=capabilities,
                        runtime=runtime, counts=counts, capabilities=capabilities, activity=activity,
                        usage={key: usage[key] for key in _USAGE_KEYS}, daily=usage["daily"])


_USAGE_KEYS = ("calls", "input_tokens", "output_tokens", "cost_usd", "cost_status", "cost_scope")
_RESOURCE_LABELS = {
    "cpu_count": "CPU count", "load_1m": "1-minute load", "memory_used_bytes": "Used memory",
    "memory_total_bytes": "Total memory", "disk_used_bytes": "Used disk", "disk_total_bytes": "Total disk",
}


def _usage_totals(_):
    return {"calls": None, "errors": None, "input_tokens": None, "output_tokens": None,
            "cost_usd": None, "cost_status": "unavailable", "cost_scope": COST_SCOPE}


def _parse_limit(values, name, default, maximum):
    """Same rules as assistant_core.settings._int; None instead of raising."""
    raw = values.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value if 1 <= value <= maximum else None


def _sum(total, count):
    if total is not None:
        return total
    return 0 if count == 0 else None  # an empty table really used 0 tokens; NULL columns are unknown


def _message(exc):
    if isinstance(exc, _Unavailable):
        return "Data unavailable: " + str(exc) + "."
    return "The data could not be read."


def _capability(id_, label, status, description):
    return {"id": id_, "label": label, "status": status, "description": description}


def _commit(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{7,64}", value) else None


def _git_head(root: Path):
    git = root / ".git"
    try:
        if git.is_file():
            text = git.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir:"):
                return None
            git = (root / text[7:].strip()).resolve()
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return _commit(head)
        ref = head[4:].strip()
        ref_file = git / ref
        if ref_file.is_file():
            return _commit(ref_file.read_text(encoding="utf-8").strip())
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return _commit(parts[0])
    except (OSError, UnicodeDecodeError):
        return None
    return None


def _psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _uptime():
    psutil = _psutil()
    try:
        if psutil is not None:
            return round(time.time() - psutil.Process().create_time(), 1)
        ticks = os.sysconf("SC_CLK_TCK")
        start = int(Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]) / ticks
        system = float(Path("/proc/uptime").read_text().split()[0])
        return round(system - start, 1)
    except (OSError, ValueError, IndexError, AttributeError, Exception):
        return None


def _resources(home: Path):
    psutil = _psutil()
    result = dict.fromkeys(_RESOURCE_LABELS)
    result["cpu_count"] = os.cpu_count()
    try:
        result["load_1m"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    try:
        if psutil is not None:
            memory = psutil.virtual_memory()
            result["memory_total_bytes"], result["memory_used_bytes"] = memory.total, memory.total - memory.available
        else:
            info = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, value = line.partition(":")
                info[key] = int(value.split()[0]) * 1024
            if "MemTotal" in info and "MemAvailable" in info:
                result["memory_total_bytes"] = info["MemTotal"]
                result["memory_used_bytes"] = info["MemTotal"] - info["MemAvailable"]
    except (OSError, ValueError, IndexError, Exception):
        pass
    try:
        disk = shutil.disk_usage(home if home.exists() else REPO_ROOT)
        result["disk_total_bytes"], result["disk_used_bytes"] = disk.total, disk.used
    except OSError:
        pass
    return result


# ---- demo ---------------------------------------------------------------

def _demo(section, query):
    base = {"section": section, "available": True, "source": "demo",
            "message": "Synthetic demo data, not live metrics.", "updated_at": DEMO_UPDATED_AT}
    day0 = datetime(2026, 10, 31, 8, tzinfo=timezone.utc).timestamp()
    skills = [
        {"id": f"{cat}:{name}", "name": name, "category": cat, "description": desc, "version": "1.0.0",
         "tags": [cat], "enabled": enabled}
        for cat, name, desc, enabled in (
            ("news", "morning-briefing", "Summarize news for a selected topic.", True),
            ("data", "data-analysis", "Analyze tabular data and summarize trends.", True),
            ("research", "source-research", "Find source material and summarize key findings with citations.", True),
            ("productivity", "weekly-notes", "Summarize weekly notes and follow-up tasks.", False))]
    routines = [
        {"id": "demo-1", "name": "Morning briefing", "schedule": "0 7 * * *", "schedule_kind": "cron",
         "enabled": True, "state": "scheduled", "next_run_at": "2026-11-01T00:00:00+00:00",
         "last_run_at": "2026-10-31T00:00:00+00:00", "last_status": "ok", "has_error": False, "failure_streak": 0,
         "repeat": {"times": None, "completed": 30}, "skills": ["morning-briefing"]},
        {"id": "demo-2", "name": "Weekly research digest", "schedule": "0 9 * * 5", "schedule_kind": "cron",
         "enabled": True, "state": "scheduled", "next_run_at": "2026-11-06T02:00:00+00:00",
         "last_run_at": "2026-10-30T02:00:00+00:00", "last_status": "ok", "has_error": False, "failure_streak": 0,
         "repeat": {"times": None, "completed": 6}, "skills": ["source-research", "weekly-notes"]},
        {"id": "demo-3", "name": "Data analysis report", "schedule": "every 7d", "schedule_kind": "interval",
         "enabled": False, "state": "paused", "next_run_at": None, "last_run_at": "2026-10-24T09:00:00+00:00",
         "last_status": "error", "has_error": True, "failure_streak": 1,
         "repeat": {"times": None, "completed": 3}, "skills": ["data-analysis"]}]
    calls = [{"ts": _iso(day0 - i * 5400), "provider": "demo", "model": ("demo-small", "demo-large")[i % 2],
              "input_tokens": 800 + 37 * i, "output_tokens": 120 + 11 * i, "duration_s": round(1.2 + 0.1 * i, 2),
              "error_type": "RateLimitError" if i == 4 else None} for i in range(12)]
    daily = {}
    for call in calls:
        row = daily.setdefault(call["ts"][:10], {"day": call["ts"][:10], "calls": 0, "input_tokens": 0, "output_tokens": 0})
        row["calls"] += 1
        row["input_tokens"] += call["input_tokens"]
        row["output_tokens"] += call["output_tokens"]
    usage = {"calls": len(calls), "errors": 1, "input_tokens": sum(c["input_tokens"] for c in calls),
             "output_tokens": sum(c["output_tokens"] for c in calls), "cost_usd": 0.0421, "cost_status": "estimated",
             "cost_scope": COST_SCOPE}
    resources = {"cpu_count": 4, "load_1m": 0.42, "memory_used_bytes": 3 * 2**30, "memory_total_bytes": 8 * 2**30,
                 "disk_used_bytes": 21 * 2**30, "disk_total_bytes": 64 * 2**30}
    runtime = {"mode": "demo", "bot_mode": "assistant", "sandbox_backend": "docker",
               "hermes_commit": "0000000demo", "uptime_seconds": 86400}
    daily_rows = sorted(daily.values(), key=lambda d: d["day"], reverse=True)
    if section == "users":
        people = [("telegram", "100000001", 12), ("telegram", "100000002", 3), ("zalo", "demo-zalo-user-1", 0),
                  ("zalo", "demo-zalo-user-2", 30)]
        items = [{"user_id": u, "platform": p, "status": "configured", "used_today": n, "user_daily_limit": 30}
                 for p, u, n in people if _matches(query, p, u)]
        return {**base, "message": "Synthetic demo data. " + USERS_MESSAGE, "items": items, "day": "2026-10-31",
                "timezone": "Asia/Ho_Chi_Minh (UTC+7)", "total": len(items), "truncated": False, "rejected": 0,
                "total_used_today": 45, "total_daily_limit": 500, "user_daily_limit": 30, "quota_available": True,
                "coverage": "explicit_allowlist_only", "limits_source": "config"}
    if section == "skills":
        items = [s for s in skills if _matches(query, s["name"], s["category"], s["description"])]
        return {**base, "items": items, "total": len(items), "truncated": False, "skipped": 0}
    if section == "routines":
        items = [r for r in routines if _matches(query, r["name"], r["schedule"])]
        return {**base, "items": items, "total": len(items), "truncated": False}
    if section == "usage":
        items = [c for c in calls if _matches(query, c["model"], c["provider"])]
        return {**base, "items": items, **usage, "daily": daily_rows, "timezone": "UTC"}
    if section == "runtime":
        items = [{"id": k, "label": _RESOURCE_LABELS[k], "value": v,
                  "unit": None if k in ("cpu_count", "load_1m") else "bytes"} for k, v in resources.items()]
        return {**base, "items": items, **runtime, "resources": resources,
                "controls": {"restart": False, "terminal": False}}
    capabilities = [_capability(i, label, status, desc) for i, label, status, desc in (
        ("sessions", "Hermes session storage", "ok", "Demo."), ("skills", "Skills", "ok", "3 of 4 skills are enabled."),
        ("routines", "Scheduled jobs", "ok", "3 jobs."), ("telemetry", "Model-call telemetry", "ok", "12 calls."),
        ("quota", "Turn quota", "ok", "Demo."), ("sandbox", "Execution sandbox", "ok", "Configured backend docker; execution has not been verified."),
        ("controls", "Restart / terminal", "disabled", "This console is read-only and does not control processes."))]
    activity = sorted(
        [{"kind": "api_call", "label": c["model"], "status": "error" if c["error_type"] else "ok", "at": c["ts"]}
         for c in calls[:6]] +
        [{"kind": "routine", "label": r["name"], "status": r["last_status"], "at": r["last_run_at"]} for r in routines],
        key=lambda a: a["at"], reverse=True)[:ACTIVITY_LIMIT]
    return {**base, "items": capabilities, "runtime": runtime,
            "counts": {"sessions": 37, "memories": 9, "skills": len(skills), "routines": len(routines)},
            "capabilities": capabilities, "activity": activity, "usage": {k: usage[k] for k in _USAGE_KEYS},
            "daily": daily_rows}
