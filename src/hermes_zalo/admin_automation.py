"""Console management for Hermes skills and automations (scheduled cron jobs).

``AutomationAdmin(store, environ=None)`` exposes ``snapshot(section)`` and
``action(section, data, authorize=None)`` for the sections ``skills`` and
``routines`` following the Console control contract (snapshot + action
descriptors, revision tokens, exact request shape).

Compatibility with the installed Hermes runtime (``cron/jobs.py``,
``hermes_cli/skills_config.py``, ``tools/skill_manager_tool.py``):

- Jobs are stored in ``<home>/cron/jobs.json`` as ``{"jobs": [...],
  "updated_at": ...}`` with the upstream record fields, written through
  ``ControlStore.mutate_json`` under the upstream cross-process lock
  ``cron/.jobs.lock``.
- Schedules use the upstream grammar subset that needs no runtime state:
  5-field cron, intervals (``30m``, ``every 2h``), weekday/time phrases
  (``every monday 9am``) and future ISO one-shots. ``next_run_at`` is never
  invented: it is left ``null`` and the scheduler's own missing-next-run
  recovery computes it on its next tick (resume keeps an elapsed recurring
  slot due, as upstream ``resume_job`` does).
- Delivery is an allowlist, never free text: ``local`` (saved output only),
  ``origin`` (only for a job that already has an origin chat) or a bare
  platform name (``telegram``, ``zalo``) whose home chat is configured for this
  home (``<PLATFORM>_HOME_CHANNEL`` or ``platforms.<name>.home_channel`` plus the
  bot token). Upstream resolves a bare platform to that home chat at run time,
  so no chat id is ever taken from the client. No script, workdir or model
  fields are accepted.
- Skills are toggled through ``skills.disabled`` in config.yaml; the
  essential ``hermes-agent`` skill can never be disabled. New skills are a
  generated ``SKILL.md`` under ``<home>/skills/<category>/<name>/``.

Nothing here mutates ``os.environ``, follows symlinks, accepts file paths
from the client or returns secrets. Cron output excerpts are redacted.
"""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
import uuid

import yaml

from .admin_auth import AdminError
from .admin_assistant import _Unavailable, _connect_ro, _columns, _iso, _read_bounded, redact

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


SECTIONS = ("skills", "routines")
JOBS = "cron/jobs.json"
JOBS_LOCK = "cron/.jobs.lock"
CONFIG = "config.yaml"
MISSING_REVISION = hashlib.sha256(b"missing").hexdigest()  # ControlStore token for an absent file
ESSENTIAL_SKILLS = frozenset({"hermes-agent"})
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")  # upstream skill_manager_tool.VALID_NAME_RE
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_SKILL_NAME = 64
SKILL_DESC_LIMIT = 60  # upstream SKILL_PROMPT_DESC_LIMIT for new skills
MAX_INSTRUCTIONS = 7000  # 32 KiB request bound: 7000 chars <= 28 KiB UTF-8 plus envelope
MAX_PROMPT = 5000
MAX_SCHEDULE = 100
MAX_JOB_NAME = 80
MAX_JOB_SKILLS = 8
MAX_REPEAT = 100_000
ROW_LIMIT = 200
ROUTINE_LIMIT = 50  # edit prefills make job rows large
SNAPSHOT_BUDGET = 1536 * 1024  # under the 2 MiB proxy response bound, ASCII-escaped
SKILL_LIMIT = 1000
SKILL_SCAN_DIRS = 3000
SKILL_SCAN_DEPTH = 4
FRONTMATTER_BYTES = 16 * 1024
OUTPUT_FILES = 10
OUTPUT_EXCERPT = 2000
OUTPUT_MAX_BYTES = 256 * 1024
RUN_HISTORY = 5
HEARTBEAT_FRESH_SECONDS = 180
ONESHOT_GRACE_SECONDS = 120
OUTPUT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.md$")
DEMO_UPDATED_AT = "2026-10-31T08:00:00+00:00"

ROUTINE_NOTE = ("Automations save their result in this bot home and can also send it to your own Telegram or Zalo "
                "home chat. They never message anyone else. The scheduler sets the next run time once the gateway "
                "is running.")
# Chat platforms an automation may deliver to: (name, label, home env var, bot token env var).
# A target is offered only when its home chat and its bot token are configured for this home.
DELIVERY_PLATFORMS = (("telegram", "Telegram", "TELEGRAM_HOME_CHANNEL", "TELEGRAM_BOT_TOKEN"),
                      ("zalo", "Zalo", "ZALO_HOME_CHANNEL", "ZALO_BOT_TOKEN"))
KEEP_DELIVERY = "keep"
DEMO_DELIVERY = ({"value": "telegram", "label": "Telegram · Owner chat (demo)", "platform": "telegram"},
                 {"value": "zalo", "label": "Zalo · Owner chat (demo)", "platform": "zalo"})
SKILL_NOTE = ("Enabling or disabling a skill takes effect in new sessions. The essential hermes-agent skill "
              "cannot be disabled.")
SCHEDULE_HELP = ("Cron (0 9 * * *), interval (30m, every 2h, 1d), weekly/daily phrase (every monday 9am, "
                 "weekdays at 8:30) or a future one-shot time (2026-12-01T09:00).")

_SECRET_KV = re.compile(r"(?i)\b([\w-]*(?:api[_-]?key|token|secret|password|passwd|credential|authorization)[\w-]*)"
                        r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)")
_SECRET = re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|github_pat|xox[abprs]|AKIA|AIza|ya29)[-_A-Za-z0-9.]{8,}"
                     r"|\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*"
                     r"|\b\d{8,10}:[A-Za-z0-9_-]{30,}"  # Telegram bot token
                     r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"  # JWT
                     r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"
                     r"|\b[A-Za-z0-9+/_-]{40,}={0,2}")
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
_TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def redact_output(text):
    """Multi-line output with credential-like values replaced by [secret]."""
    text = _TEXT_CONTROL.sub("", text)
    text = _URL_CREDENTIALS.sub(r"\1[secret]@", text)
    text = _SECRET_KV.sub(lambda m: m.group(1) + m.group(2) + "[secret]", text)
    return _SECRET.sub("[secret]", text)


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _field(name, label, type_="text", **extra):
    return {"name": name, "label": label, "type": type_, **{k: v for k, v in extra.items() if v is not None}}


def _descriptor(id_, label, revision, fields, *, target=None, description=None):
    out = {"id": id_, "label": label, "revision": revision, "fields": fields}
    if target is not None:
        out["target"] = target
    if description:
        out["description"] = description
    return out


def _clean_text(value, label, maximum, *, required=False, multiline=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise AdminError(label + " must be text.")
    value = value.replace("\r\n", "\n").strip()
    if required and not value:
        raise AdminError(label + " is required.")
    if len(value) > maximum:
        raise AdminError(f"{label} must be at most {maximum} characters.")
    bad = _TEXT_CONTROL if multiline else re.compile(r"[\x00-\x1f\x7f]")
    if bad.search(value):
        raise AdminError(label + " contains unsupported control characters.")
    return value


def _name_list(values):
    """Upstream _normalize_skill_names / parse_config_string_list: null, scalar, JSON-array string, list."""
    if values is None:
        return []
    if isinstance(values, str):
        text = values.strip()
        if text.startswith("["):
            try:
                loaded = yaml.safe_load(text)
                if isinstance(loaded, list):
                    values = loaded
            except yaml.YAMLError:
                pass
        if isinstance(values, str):
            values = [values]
    if not isinstance(values, list):
        return []
    out = []
    for item in values:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


# ---- schedule grammar (ported from upstream cron/jobs.py, pure parts only) ----

_DURATION = re.compile(r"^(\d*)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$")
_WEEKDAY = {"sunday": "0", "sun": "0", "monday": "1", "mon": "1", "tuesday": "2", "tue": "2", "tues": "2",
            "wednesday": "3", "wed": "3", "weds": "3", "thursday": "4", "thu": "4", "thur": "4", "thurs": "4",
            "friday": "5", "fri": "5", "saturday": "6", "sat": "6"}
_DAYSPEC = {"day": "*", "daily": "*", "everyday": "*", "weekday": "1-5", "weekdays": "1-5",
            "weekend": "0,6", "weekends": "0,6"}
_CRON_FIELDS = ((0, 59, {}), (0, 23, {}), (1, 31, {}),
                (1, 12, {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug",
                                                         "sep", "oct", "nov", "dec"))}),
                (0, 7, {d: i for i, d in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}))


def _duration_minutes(text):
    match = _DURATION.match(text.strip().lower())
    if not match:
        return None
    value = int(match.group(1)) if match.group(1) else 1
    minutes = value * {"m": 1, "h": 60, "d": 1440}[match.group(2)[0]]
    return minutes if minutes >= 1 else None


def _clock(text):
    t = text.strip().lower().replace(" ", "")
    if t in ("noon", "midday"):
        return 12, 0
    if t == "midnight":
        return 0, 0
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$", t)
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _natural_to_cron(rest):
    tokens = rest.lower().replace(",", " ").split()
    if not tokens:
        return None
    dow, idx = _DAYSPEC.get(tokens[0]), 1
    if dow is None:
        days, idx = [], len(tokens)
        for i, tok in enumerate(tokens):
            if tok == "and":
                continue
            mapped = _WEEKDAY.get(tok)
            if mapped is None:
                idx = i
                break
            if mapped not in days:
                days.append(mapped)
        if not days:
            return None
        dow = ",".join(days)
    time_tokens = tokens[idx:]
    if time_tokens and time_tokens[0] == "at":
        time_tokens = time_tokens[1:]
    parsed = _clock(" ".join(time_tokens)) if time_tokens else None
    if parsed is None:
        return None
    return f"{parsed[1]} {parsed[0]} * * {dow}"


def _cron_value(text, low, high, names):
    text = text.lower()
    if text in names:
        return names[text]
    if not text.isdigit():
        raise ValueError
    value = int(text)
    if not low <= value <= high:
        raise ValueError
    return value


def _valid_cron(expr):
    parts = expr.split()
    if len(parts) != 5:
        return False
    try:
        for part, (low, high, names) in zip(parts, _CRON_FIELDS):
            for item in part.split(","):
                base, _, step = item.partition("/")
                if step and (not step.isdigit() or int(step) < 1):
                    return False
                if base == "*":
                    continue
                start, dash, end = base.partition("-")
                first = _cron_value(start, low, high, names)
                if dash and _cron_value(end, low, high, names) < first:
                    return False
                if step and not dash and not base:
                    return False
    except ValueError:
        return False
    return True


def parse_schedule(text, tz):
    """Schedule string -> upstream schedule dict; ValueError with an English message otherwise."""
    schedule = text.strip()
    lower = schedule.lower()
    is_every = lower.startswith("every ")
    rest = schedule[6:].strip() if is_every else lower
    expr = _natural_to_cron(rest)
    if expr is not None:
        return {"kind": "cron", "expr": expr, "display": schedule}
    if is_every:
        minutes = _duration_minutes(rest)
        if minutes is None:
            raise ValueError("Invalid interval. Use for example every 30m, every 2h or every 1d.")
        return {"kind": "interval", "minutes": minutes, "display": f"every {minutes}m"}
    parts = schedule.split()
    if len(parts) >= 5 and all(re.match(r"^[A-Za-z\d*\-,/]+$", p) for p in parts[:5]):
        if len(parts) != 5 or not _valid_cron(schedule):
            raise ValueError("Invalid cron expression. Use five fields: minute hour day month weekday.")
        return {"kind": "cron", "expr": schedule, "display": schedule}
    if "T" in schedule or re.match(r"^\d{4}-\d{2}-\d{2}", schedule):
        try:
            moment = datetime.fromisoformat(schedule)
        except ValueError:
            raise ValueError("Invalid date and time. Use for example 2026-12-01T09:00.") from None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=tz)
        return {"kind": "once", "run_at": moment.isoformat(), "display": f"once at {moment.strftime('%Y-%m-%d %H:%M')}"}
    if lower.startswith("in "):
        raise ValueError("Relative one-shot times are not supported here; enter the exact date and time.")
    minutes = _duration_minutes(schedule)
    if minutes is not None:
        return {"kind": "interval", "minutes": minutes, "display": f"every {minutes}m"}
    raise ValueError("Invalid schedule. " + SCHEDULE_HELP)


# ---- service ---------------------------------------------------------------

class AutomationAdmin:
    def __init__(self, store, *, environ=None, clock=None):
        self.store = store
        self.home = Path(store.home)
        self.mode = getattr(store, "mode", "local")
        self.environ = {} if self.mode == "demo" else dict(os.environ if environ is None else environ)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ---- public ----------------------------------------------------------

    def snapshot(self, section):
        if section not in SECTIONS:
            raise AdminError("Unknown section.", 404)
        self._seed_demo()
        return getattr(self, "_snapshot_" + section)()

    def action(self, section, data, *, authorize=None):
        if section not in SECTIONS:
            raise AdminError("Unknown section.", 404)
        if not isinstance(data, dict) or set(data) != {"action", "expected_revision", "confirm", "values"}:
            raise AdminError("The request must contain exactly action, expected_revision, confirm and values.")
        if data["confirm"] is not True:
            raise AdminError("The action must be confirmed.")
        name, values, revision = data["action"], data["values"], data["expected_revision"]
        if not isinstance(name, str) or not isinstance(values, dict) or not isinstance(revision, str):
            raise AdminError("Invalid action request.")
        spec = ACTIONS.get(section, {}).get(name)
        if spec is None:
            raise AdminError("Unknown action.")
        unknown = set(values) - set(spec)
        if unknown:
            raise AdminError("Unsupported field: " + sorted(unknown)[0][:40])
        self._seed_demo()
        return getattr(self, "_do_" + name)(values, revision, authorize)

    # ---- helpers ---------------------------------------------------------

    def _seed_demo(self):
        if self.mode != "demo":
            return
        _, revision = self.store.read_text(JOBS)
        if revision == MISSING_REVISION:
            self.store.mutate_json(JOBS, MISSING_REVISION, lambda _: {"jobs": _demo_jobs(), "updated_at": DEMO_UPDATED_AT},
                                   lock=JOBS_LOCK)

    def _config(self):
        try:
            return self.store.read_yaml(CONFIG)
        except AdminError:
            return None, None

    def _timezone(self, config=None):
        names = [self.environ.get("HERMES_TIMEZONE")]
        try:
            env, _ = self.store.read_env()
            names.append(env.get("HERMES_TIMEZONE"))
        except AdminError:
            pass
        if config is None:
            config, _ = self._config()
        if isinstance(config, dict):
            names.append(config.get("timezone"))
        for name in names:
            if isinstance(name, str) and name.strip() and ZoneInfo is not None:
                try:
                    return ZoneInfo(name.strip())
                except Exception:
                    continue
        return datetime.now().astimezone().tzinfo

    def _now(self, tz=None):
        return self._clock().astimezone(tz or self._timezone())

    def _safe_dir(self, *parts):
        """A real directory under home with no symlink component, else None (reads only)."""
        path = self.home
        for part in ("",) + parts:
            path = path / part if part else path
            try:
                info = os.lstat(path)
            except OSError:
                return None
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return None
        return path

    # ---- skills ----------------------------------------------------------

    def _dir_state(self, *parts):
        """'ok' (real directory), 'missing' (the first absent component and everything below can be created
        safely; every existing component is a real directory) or 'unsafe' (symlink, file or unreadable)."""
        path = self.home
        for part in ("",) + parts:
            path = path / part if part else path
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                return "missing"
            except OSError:
                return "unsafe"
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                return "unsafe"
        return "ok" if os.access(path, os.R_OK | os.X_OK) else "unsafe"

    def _scan_skills(self):
        if self.mode == "demo":
            items = [dict(item) for item in _DEMO_SKILLS]
            for key in sorted(getattr(self.store, "_demo_files", {})):
                match = re.fullmatch(r"skills/(?:([a-z0-9][a-z0-9._-]*)/)?([a-z0-9][a-z0-9._-]*)/SKILL\.md", key)
                if match:
                    text, _ = self.store.read_text(key)
                    items.append(_skill_meta(text, match.group(1), match.group(2)))
            return items, False, True
        state = self._dir_state("skills")
        if state == "missing":
            return [], False, True  # empty library; create_skill makes the folder on first write
        root = self._safe_dir("skills") if state == "ok" else None
        if root is None:
            return [], False, False
        items, visited, truncated = [], 0, False
        for current, dirs, files in os.walk(root, followlinks=False):
            visited += 1
            rel = Path(current).relative_to(root)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(current) / d).is_symlink())
            if len(rel.parts) >= SKILL_SCAN_DEPTH:
                dirs[:] = []
            if visited > SKILL_SCAN_DIRS or len(items) >= SKILL_LIMIT:
                truncated = True
                break
            if "SKILL.md" not in files or not rel.parts:
                continue
            dirs[:] = []
            path = Path(current) / "SKILL.md"
            if path.is_symlink():
                continue
            try:
                text = _read_bounded(path, 1024 * 1024)[:FRONTMATTER_BYTES].decode("utf-8", "replace")
            except (OSError, _Unavailable):
                continue
            category = rel.parts[0] if len(rel.parts) > 1 else None
            items.append(_skill_meta(text, category, rel.name))
        return items, truncated, True

    def _snapshot_skills(self):
        config, config_rev = self._config()
        items, truncated, available = self._scan_skills()
        if config is None:
            return {"section": "skills", "available": False, "source": self._source(), "updated_at": _now_iso(),
                    "message": "config.yaml cannot be read safely; repair it before managing skills.",
                    "items": [], "actions": [], "categories": []}
        skills_cfg = config.get("skills") if isinstance(config.get("skills"), dict) else {}
        disabled = set(_name_list(skills_cfg.get("disabled"))) - ESSENTIAL_SKILLS
        platform = skills_cfg.get("platform_disabled") if isinstance(skills_cfg.get("platform_disabled"), dict) else {}
        platform_sets = {str(k): set(_name_list(v)) for k, v in platform.items()}
        categories, actions = {}, []
        for item in items:
            name = item["name"]
            item["essential"] = name in ESSENTIAL_SKILLS
            item["enabled"] = item["essential"] or name not in disabled
            item["platform_disabled"] = sorted(p for p, names in platform_sets.items()
                                               if name in names and not item["essential"])
            cat = item["category"] or "uncategorized"
            entry = categories.setdefault(cat, {"id": "category:" + cat, "name": cat, "total": 0, "enabled": 0,
                                                "_on": 0, "_off": 0})
            entry["total"] += 1
            entry["enabled"] += 1 if item["enabled"] else 0
            if not item["essential"]:
                entry["_on" if item["enabled"] else "_off"] += 1
        items.sort(key=lambda s: (s["category"] or "", s["name"]))
        shown = items[:ROW_LIMIT]
        for item in shown:
            if item["essential"]:
                continue
            hidden = [_field("skill", "Skill", "hidden", value=item["name"])]
            if item["enabled"]:
                actions.append(_descriptor("disable_skill", "Disable", config_rev, hidden, target=item["id"],
                                           description="Disable " + item["name"] + " in new sessions."))
                if item["platform_disabled"]:
                    actions.append(_descriptor("enable_skill", "Enable on all platforms", config_rev, hidden,
                                               target=item["id"], description="Clear the per-platform disables of "
                                               + item["name"] + " (" + ", ".join(item["platform_disabled"]) + ")."))
            else:
                actions.append(_descriptor("enable_skill", "Enable", config_rev, hidden, target=item["id"],
                                           description="Enable " + item["name"] + " (also clears per-platform disables)."))
        category_rows = []
        for entry in sorted(categories.values(), key=lambda c: c["name"]):
            on, off = entry.pop("_on"), entry.pop("_off")
            category_rows.append(entry)
            hidden = [_field("category", "Category", "hidden", value=entry["name"])]
            if on:
                actions.append(_descriptor("disable_category", "Disable category", config_rev, hidden,
                                           target=entry["id"], description=f"Disable all {on + off} changeable skills in {entry['name']}."))
            if off:
                actions.append(_descriptor("enable_category", "Enable category", config_rev, hidden,
                                           target=entry["id"], description=f"Enable all skills in {entry['name']}."))
        if available or self.mode == "demo":
            category_options = [{"value": "", "label": "(no category)"}] + [
                {"value": c["name"], "label": c["name"]} for c in category_rows if c["name"] != "uncategorized"]
            actions.append(_descriptor("create_skill", "Create skill", MISSING_REVISION, [
                _field("name", "Name", required=True, max_length=MAX_SKILL_NAME,
                       help="Lowercase letters, digits, dots, hyphens and underscores; must start with a letter or digit."),
                _field("category", "Category", "select", value="", options=category_options),
                _field("new_category", "New category", max_length=MAX_SKILL_NAME,
                       help="Optional; overrides the selected category."),
                _field("description", "Description", required=True, max_length=SKILL_DESC_LIMIT,
                       help="One short sentence saying when to use the skill."),
                _field("instructions", "Instructions", "textarea", required=True, max_length=MAX_INSTRUCTIONS,
                       help="Markdown instructions the assistant follows when it loads the skill."),
            ], description="Creates SKILL.md in this bot's skills folder. It is available in new sessions."))
        return {"section": "skills", "available": available, "source": self._source(), "updated_at": _now_iso(),
                "message": (SKILL_NOTE + (" No skills are installed yet; create one to start the library." if not items else "")
                            if available else "The skills folder is not a safe local directory (symbolic link, file or "
                            "unreadable); repair it before managing skills."),
                "items": shown, "total": len(items), "truncated": truncated or len(items) > ROW_LIMIT,
                "categories": category_rows, "actions": actions, "revision": config_rev}

    def _installed_names(self):
        items, _, _ = self._scan_skills()
        return items

    def _mutate_disabled(self, revision, authorize, change):
        def callback(config):
            config = dict(config or {})
            skills_cfg = config.get("skills")
            if skills_cfg is None:
                skills_cfg = {}
            if not isinstance(skills_cfg, dict):
                raise AdminError("The skills setting in config.yaml is not a mapping; repair it first.", 409)
            skills_cfg = dict(skills_cfg)
            current = _name_list(skills_cfg.get("disabled"))
            platform = skills_cfg.get("platform_disabled")
            new_disabled, new_platform = change(current, platform if isinstance(platform, dict) else None)
            skills_cfg["disabled"] = sorted(set(new_disabled) - ESSENTIAL_SKILLS)
            if new_platform is not None:
                skills_cfg["platform_disabled"] = new_platform
            config["skills"] = skills_cfg
            return config
        return self.store.mutate_yaml(CONFIG, revision, callback, authorize=authorize)

    @staticmethod
    def _strip_platform(platform, names):
        if platform is None:
            return None
        return {key: (sorted(set(_name_list(value)) - names) if isinstance(value, (list, str)) or value is None else value)
                for key, value in platform.items()}

    def _do_disable_skill(self, values, revision, authorize):
        name = _clean_text(values.get("skill"), "Skill", MAX_SKILL_NAME, required=True)
        if name in ESSENTIAL_SKILLS:
            raise AdminError("The essential hermes-agent skill cannot be disabled.")
        if name not in {s["name"] for s in self._installed_names()}:
            raise AdminError("That skill is not installed.", 404)
        new_rev = self._mutate_disabled(revision, authorize, lambda cur, plat: (cur + [name], None))
        return {"ok": True, "message": f"Skill {name} disabled for new sessions.", "revision": new_rev}

    def _do_enable_skill(self, values, revision, authorize):
        name = _clean_text(values.get("skill"), "Skill", MAX_SKILL_NAME, required=True)
        new_rev = self._mutate_disabled(revision, authorize, lambda cur, plat: (
            [n for n in cur if n != name], self._strip_platform(plat, {name})))
        return {"ok": True, "message": f"Skill {name} enabled for new sessions.", "revision": new_rev}

    def _category_names(self, values):
        category = _clean_text(values.get("category"), "Category", MAX_SKILL_NAME, required=True)
        names = {s["name"] for s in self._installed_names() if (s["category"] or "uncategorized") == category}
        names -= ESSENTIAL_SKILLS
        if not names:
            raise AdminError("That category has no skills that can be changed.", 404)
        return category, names

    def _do_disable_category(self, values, revision, authorize):
        category, names = self._category_names(values)
        new_rev = self._mutate_disabled(revision, authorize, lambda cur, plat: (cur + sorted(names), None))
        return {"ok": True, "message": f"Disabled {len(names)} skills in {category} for new sessions.", "revision": new_rev}

    def _do_enable_category(self, values, revision, authorize):
        category, names = self._category_names(values)
        new_rev = self._mutate_disabled(revision, authorize, lambda cur, plat: (
            [n for n in cur if n not in names], self._strip_platform(plat, names)))
        return {"ok": True, "message": f"Enabled the skills in {category} for new sessions.", "revision": new_rev}

    def _do_create_skill(self, values, revision, authorize):
        name = _clean_text(values.get("name"), "Name", MAX_SKILL_NAME, required=True)
        category = _clean_text(values.get("new_category"), "New category", MAX_SKILL_NAME) or \
            _clean_text(values.get("category"), "Category", MAX_SKILL_NAME)
        description = _clean_text(values.get("description"), "Description", SKILL_DESC_LIMIT, required=True)
        instructions = _clean_text(values.get("instructions"), "Instructions", MAX_INSTRUCTIONS, required=True,
                                   multiline=True)
        if not NAME_RE.match(name):
            raise AdminError("Invalid skill name. Use lowercase letters, digits, dots, hyphens and underscores.")
        if category and (not NAME_RE.match(category) or category == "uncategorized"):
            raise AdminError("Invalid category. Use one lowercase folder name.")
        if revision != MISSING_REVISION:
            raise AdminError("This form is out of date. Refresh before trying again.", 409)
        if name in {s["name"] for s in self._installed_names()}:
            raise AdminError("A skill with that name already exists.", 409)
        relative = "skills/" + (category + "/" if category else "") + name
        if self.mode != "demo" and os.path.lexists(self.home / relative):
            raise AdminError("A file or folder with that skill name already exists.", 409)
        front = yaml.safe_dump({"name": name, "description": description, "version": "1.0.0"},
                               allow_unicode=True, sort_keys=False)
        title = name.replace("-", " ").replace("_", " ").title()
        content = f"---\n{front}---\n\n# {title}\n\n{instructions}\n"
        self.store.write_text(relative + "/SKILL.md", content, MISSING_REVISION, authorize=authorize)
        return {"ok": True, "message": f"Skill {name} created. It is available in new sessions.",
                "item": {"id": (category + ":" if category else "") + name, "name": name, "category": category or None}}

    # ---- routines ----------------------------------------------------------

    def _jobs(self):
        value, revision = self.store.read_json(JOBS)
        if value is None:
            return [], revision
        jobs = value.get("jobs") if isinstance(value, dict) else value
        if not isinstance(jobs, list):
            raise AdminError("The job file has an unsupported shape; Hermes repairs it on its next tick.", 503)
        return [job for job in jobs if isinstance(job, dict)], revision

    def _heartbeat_fresh(self):
        if self.mode == "demo":
            return False
        cron = self._safe_dir("cron")
        try:
            if cron is None or (cron / "ticker_heartbeat").is_symlink():
                return False
            stamp = float(_read_bounded(cron / "ticker_heartbeat", 200).decode("utf-8-sig").split()[0])
        except (OSError, _Unavailable, ValueError, IndexError, UnicodeDecodeError):
            return False
        return 0 <= time.time() - stamp <= HEARTBEAT_FRESH_SECONDS

    def _runs(self, job_ids):
        cron = self._safe_dir("cron")
        if self.mode == "demo" or cron is None or not job_ids:
            return {}
        try:
            db = _connect_ro(cron / "executions.db")
        except (OSError, sqlite3.Error, _Unavailable):
            return {}
        out = {}
        with closing(db):
            try:
                if not {"job_id", "status", "claimed_at", "started_at", "finished_at", "error"} <= _columns(db, "executions"):
                    return {}
                for job_id in job_ids[:ROUTINE_LIMIT]:
                    rows = db.execute("SELECT status, claimed_at, started_at, finished_at, error FROM executions "
                                      "WHERE job_id=? ORDER BY julianday(claimed_at) DESC LIMIT ?", (job_id, RUN_HISTORY))
                    out[job_id] = [{"status": redact(r[0], 20), "claimed_at": _iso(r[1]), "started_at": _iso(r[2]),
                                    "finished_at": _iso(r[3]), "error": redact(r[4], 200)} for r in rows]
            except sqlite3.Error:
                return {}
        return out

    def _outputs(self, job_id):
        if self.mode == "demo":
            return _DEMO_OUTPUTS.get(job_id, ([], None))
        if not JOB_ID_RE.match(job_id or ""):
            return [], None
        folder = self._safe_dir("cron", "output", job_id)
        if folder is None:
            return [], None
        try:
            names = sorted((e.name for e in os.scandir(folder)
                            if OUTPUT_NAME_RE.match(e.name) and e.is_file(follow_symlinks=False)), reverse=True)
        except OSError:
            return [], None
        files = [{"name": n, "at": n[:10] + "T" + n[11:19].replace("-", ":")} for n in names[:OUTPUT_FILES]]
        latest = None
        for name in names[:3]:
            try:
                raw = _read_bounded(folder / name, OUTPUT_MAX_BYTES)
            except (OSError, _Unavailable):
                continue
            text = redact_output(raw.decode("utf-8", "replace")).strip()
            latest = {"name": name, "excerpt": text[:OUTPUT_EXCERPT], "truncated": len(text) > OUTPUT_EXCERPT}
            break
        return files, latest

    def read_output(self, job_id, name=None):
        """Full redacted output (bounded) of one run; ``name`` must be a listed file name, never a path."""
        if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
            raise AdminError("Invalid job.")
        jobs, _ = self._jobs()
        if not any(job.get("id") == job_id for job in jobs):
            raise AdminError("Job not found.", 404)
        files, latest = self._outputs(job_id)
        if self.mode == "demo":
            return {"ok": True, "job_id": job_id, "files": files, "output": latest}
        listed = {f["name"] for f in files}
        name = name or (files[0]["name"] if files else None)
        if name is None:
            return {"ok": True, "job_id": job_id, "files": files, "output": None}
        if not isinstance(name, str) or name not in listed:
            raise AdminError("Output not found.", 404)
        folder = self._safe_dir("cron", "output", job_id)
        try:
            raw = _read_bounded(folder / name, OUTPUT_MAX_BYTES)
        except (OSError, _Unavailable, TypeError):
            raise AdminError("The output cannot be read safely.", 503) from None
        return {"ok": True, "job_id": job_id, "files": files,
                "output": {"name": name, "text": redact_output(raw.decode("utf-8", "replace"))}}

    def _job_fields(self, job=None, skill_names=()):
        job = job or {}
        skills = job.get("skills") if isinstance(job.get("skills"), list) else ([job["skill"]] if job.get("skill") else [])
        repeat = (job.get("repeat") or {}).get("times") if isinstance(job.get("repeat"), dict) else None
        fields = []
        if job:
            fields.append(_field("id", "Job", "hidden", value=job.get("id")))
        fields += [
            _field("name", "Name", value=str(job.get("name") or "")[:MAX_JOB_NAME] if job else None,
                   required=not job, max_length=MAX_JOB_NAME),
            _field("schedule", "Schedule", value=str(job.get("schedule_display") or "")[:MAX_SCHEDULE] if job else None,
                   required=True, max_length=MAX_SCHEDULE, help=SCHEDULE_HELP),
            _field("prompt", "Instructions", "textarea", value=str(job.get("prompt") or "")[:MAX_PROMPT] if job else None,
                   max_length=MAX_PROMPT, help="What the assistant should do on each run. Required unless skills are set."),
            _field("skills", "Skills", value=", ".join(str(s) for s in skills)[:600] if job else None, max_length=600,
                   help="Optional comma-separated installed skill names, loaded before the instructions."),
            _field("repeat", "Repeat count", "number", value=repeat if job else None,
                   help="Leave blank to repeat forever. One-shot times always run once."),
        ]
        options = self.delivery_targets(job or None)
        current = self._delivery_choice(job.get("deliver"), job) if job else "local"
        fields.append(_field("deliver", "Send the result to", "select", required=True,
                             value=current if any(o["value"] == current for o in options) else KEEP_DELIVERY,
                             options=[{"value": o["value"], "label": o["label"]} for o in options],
                             help="The result is always saved here too. Only your configured home chats are listed."))
        if not job:
            fields.append(_field("paused", "Create paused", "checkbox", value=False))
        return fields

    # ---- delivery ------------------------------------------------------------

    @staticmethod
    def _platform_config(config, platform):
        gateway = config.get("gateway") if isinstance(config.get("gateway"), dict) else {}
        merged = {}
        for parent in (gateway.get("platforms"), config.get("platforms"), gateway):
            block = parent.get(platform) if isinstance(parent, dict) else None
            if isinstance(block, dict):
                merged.update(block)
        return merged

    def _home_chat(self, config, env, platform, env_var):
        """The configured home chat for *platform*: (name, chat_id) or None. Values never leave this module."""
        chat = None
        if env_var:
            chat = env.get(env_var) or self.environ.get(env_var)
        block = self._platform_config(config, platform)
        home = block.get("home_channel")
        name = None
        if isinstance(home, dict):
            chat = chat or (str(home.get("chat_id")) if home.get("chat_id") not in (None, "") else None)
            name = home.get("name") if isinstance(home.get("name"), str) else None
        chat = str(chat).strip() if chat is not None else ""
        pattern = r"(?:-?[0-9]{1,24}|@[A-Za-z0-9_]{5,64})" if platform == "telegram" else r"[A-Za-z0-9_-]{1,120}"
        if not re.fullmatch(pattern, chat):
            return None
        return (redact(name, 40) or "home chat"), chat

    def _has_token(self, config, env, platform, token_var):
        block = self._platform_config(config, platform)
        return bool(env.get(token_var) or self.environ.get(token_var) or (isinstance(block, dict) and block.get("token")))

    def _delivery_map(self):
        """Opaque browser selections resolve to exact configured targets only on the server."""
        if self.mode == "demo":
            return {"telegram": ("telegram:55500001", "Owner chat (demo)"),
                    "zalo": ("zalo:demo-owner", "Owner chat (demo)")}
        config, _ = self._config()
        config = config if isinstance(config, dict) else {}
        env, _ = self.store.read_env()
        targets = {}
        for platform, _, env_var, token_var in DELIVERY_PLATFORMS:
            home = self._home_chat(config, env, platform, env_var)
            if home and self._has_token(config, env, platform, token_var):
                targets[platform] = (platform + ":" + home[1], home[0])
        return targets

    def _origin_allowed(self, job):
        origin = job.get("origin") if isinstance(job, dict) else None
        if not isinstance(origin, dict):
            return False
        platform, chat = origin.get("platform"), origin.get("chat_id")
        target = self._delivery_map().get(platform) if isinstance(platform, str) else None
        return bool(target and chat is not None and platform + ":" + str(chat) == target[0])

    def _delivery_choice(self, deliver, job=None):
        if not deliver or deliver == "local":
            return "local"
        if deliver == "origin":
            return "origin" if self._origin_allowed(job) else KEEP_DELIVERY
        for platform, (target, _) in self._delivery_map().items():
            if deliver in (platform, target):
                return platform
        return KEEP_DELIVERY

    def delivery_targets(self, job=None):
        """Allowlisted delivery options as [{"value", "label", "platform"?}]; never contains a chat id."""
        options = [{"value": "local", "label": "This console only (saved in the bot home)"}]
        for platform, (_, label) in self._delivery_map().items():
            options.append({"value": platform, "label": f"{platform.title()} · {label}", "platform": platform})
        origin = job.get("origin") if isinstance(job, dict) else None
        if self._origin_allowed(job):
            platform = redact(str(origin.get("platform")), 20) or "chat"
            options.append({"value": "origin", "label": "Back to the " + platform + " chat it was created from",
                            "platform": platform})
        current = self._delivery_choice(job.get("deliver"), job) if isinstance(job, dict) else "local"
        if current == KEEP_DELIVERY:
            options.insert(0, {"value": KEEP_DELIVERY, "label": "Keep current delivery (managed outside the console)"})
        return options

    def _delivery(self, values, job=None):
        """Validated ``deliver`` value, or None to keep the current one (edit) / use local (create)."""
        value = values.get("deliver")
        if value is None:
            return None
        if not isinstance(value, str):
            raise AdminError("Choose where to send the result.")
        if value not in {o["value"] for o in self.delivery_targets(job)}:
            raise AdminError("Choose one of the listed delivery targets. Chat targets need a configured home chat.")
        if value == KEEP_DELIVERY:
            return None
        return self._delivery_map()[value][0] if value in ("telegram", "zalo") else value

    def delivery_label(self, deliver, options, job=None):
        deliver = self._delivery_choice(deliver, job)
        for option in options:
            if option["value"] == deliver:
                return option["label"]
        return {"local": "This console only (saved in the bot home)", "origin": "Back to the chat it was created from"}.get(
            deliver, "A chat target set outside the console")

    def _snapshot_routines(self):
        try:
            jobs, revision = self._jobs()
        except AdminError as exc:
            return {"section": "routines", "available": False, "source": self._source(), "updated_at": _now_iso(),
                    "message": str(exc), "items": [], "actions": []}
        heartbeat = self._heartbeat_fresh()
        runs = self._runs([str(job.get("id")) for job in jobs])
        targets = self.delivery_targets()
        deliverable = {o["value"] for o in targets}
        items, actions = [], []
        for job in jobs[:ROUTINE_LIMIT]:
            job_id = str(job.get("id") or "")
            schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
            repeat = job.get("repeat") if isinstance(job.get("repeat"), dict) else {}
            enabled = bool(job.get("enabled", True))
            state = _effective_state(job)
            files, latest = self._outputs(job_id)
            item = {
                "id": job_id, "name": redact(job.get("name"), MAX_JOB_NAME) or job_id,
                "prompt_preview": redact(job.get("prompt"), 160),
                "schedule": redact(job.get("schedule_display") or schedule.get("display") or schedule.get("expr"), MAX_SCHEDULE),
                "schedule_kind": redact(schedule.get("kind"), 20), "enabled": enabled, "state": state,
                "next_run_at": _iso(job.get("next_run_at")),
                "next_run_pending": enabled and state == "scheduled" and not job.get("next_run_at"),
                "last_run_at": _iso(job.get("last_run_at")), "last_status": redact(job.get("last_status"), 20),
                "last_error": redact(job.get("last_error"), 200),
                "last_delivery_error": redact(job.get("last_delivery_error"), 200),
                "failure_streak": job.get("failure_streak") if isinstance(job.get("failure_streak"), int) else None,
                "repeat": {"times": repeat.get("times") if isinstance(repeat.get("times"), int) else None,
                           "completed": repeat.get("completed") if isinstance(repeat.get("completed"), int) else None},
                "skills": [redact(s, 60) for s in (job.get("skills") or [])[:MAX_JOB_SKILLS] if isinstance(s, str)],
                "deliver": self._delivery_choice(job.get("deliver"), job),
                "deliver_label": self.delivery_label(job.get("deliver"), self.delivery_targets(job), job),
                "has_script": bool(job.get("script") or job.get("monitor_script") or job.get("monitor_url")),
                "created_at": _iso(job.get("created_at")),
                "runs": runs.get(job_id, []), "outputs": files, "latest_output": latest,
            }
            items.append(item)
            if not JOB_ID_RE.match(job_id):
                continue  # unsafe legacy id: show only, manage with the Hermes CLI
            hidden = [_field("id", "Job", "hidden", value=job_id)]
            if state != "completed":
                actions.append(_descriptor("edit_routine", "Edit", revision, self._job_fields(job), target=job_id,
                                           description="Changes apply from the next scheduled run."))
            if enabled and state != "completed":
                actions.append(_descriptor("pause_routine", "Pause", revision, hidden, target=job_id))
            elif state == "paused" or (not enabled and state not in ("completed",)):
                actions.append(_descriptor("resume_routine", "Resume", revision, hidden, target=job_id))
            if heartbeat and enabled and state == "scheduled" and self._can_run_now(job, deliverable):
                actions.append(_descriptor("run_routine", "Run now", revision, hidden, target=job_id,
                                           description="The running scheduler starts it within about a minute" +
                                           ("." if (job.get("deliver") or "local") == "local" else
                                            " and sends the result to " + self.delivery_label(job.get("deliver"), targets) + ".")))
            actions.append(_descriptor("delete_routine", "Delete", revision, hidden, target=job_id,
                                       description="Removes the job and its saved output. This cannot be undone."))
        actions.append(_descriptor("create_routine", "New automation", revision, self._job_fields(),
                                   description=ROUTINE_NOTE))
        message = ROUTINE_NOTE
        if not heartbeat:
            message += (" Run now is unavailable because no running Hermes scheduler was detected for this home."
                        if self.mode != "demo" else " Run now is not available in the demo.")
        return _fit_budget({"section": "routines", "available": True, "source": self._source(),
                            "updated_at": _now_iso(), "message": message, "items": items, "total": len(jobs),
                            "truncated": len(jobs) > ROUTINE_LIMIT, "scheduler_active": heartbeat,
                            "actions": actions, "revision": revision})

    def _job_inputs(self, values, *, existing=None):
        tz = self._timezone()
        name = _clean_text(values.get("name"), "Name", MAX_JOB_NAME, required=existing is None)
        schedule_text = _clean_text(values.get("schedule"), "Schedule", MAX_SCHEDULE, required=True)
        prompt = _clean_text(values.get("prompt"), "Instructions", MAX_PROMPT, multiline=True)
        skills = [s.strip() for s in _clean_text(values.get("skills"), "Skills", 600).split(",") if s.strip()]
        skills = list(dict.fromkeys(skills))
        if len(skills) > MAX_JOB_SKILLS:
            raise AdminError(f"At most {MAX_JOB_SKILLS} skills can be attached.")
        if skills:
            installed = {s["name"] for s in self._installed_names()}
            missing = [s for s in skills if s not in installed]
            if missing:
                raise AdminError("Unknown skill: " + missing[0][:60])
        if not prompt and not skills:
            raise AdminError("Enter instructions or at least one skill.")
        try:
            schedule = parse_schedule(schedule_text, tz)
        except ValueError as exc:
            raise AdminError(str(exc)) from None
        if schedule["kind"] == "once":
            run_at = datetime.fromisoformat(schedule["run_at"])
            if (self._now(tz) - run_at).total_seconds() > ONESHOT_GRACE_SECONDS:
                raise AdminError("The one-shot time is in the past.")
        repeat = values.get("repeat")
        if repeat in (None, ""):
            repeat = None
        else:
            if isinstance(repeat, str) and repeat.strip().isdigit():
                repeat = int(repeat.strip())
            if isinstance(repeat, bool) or not isinstance(repeat, int) or not 0 <= repeat <= MAX_REPEAT:
                raise AdminError(f"Repeat count must be a whole number from 0 to {MAX_REPEAT}.")
            repeat = repeat or None  # upstream: 0 means forever
        if schedule["kind"] == "once":
            repeat = 1
        return {"name": name, "schedule": schedule, "prompt": prompt, "skills": skills, "repeat": repeat, "tz": tz}

    def _mutate_jobs(self, revision, authorize, change):
        result = {}

        def callback(value):
            if value is None:
                payload = {}
            elif isinstance(value, dict):
                payload = dict(value)
            elif isinstance(value, list):
                payload = {"jobs": value}
            else:
                raise AdminError("The job file has an unsupported shape.", 503)
            jobs = payload.get("jobs", [])
            if not isinstance(jobs, list):
                raise AdminError("The job file has an unsupported shape.", 503)
            jobs = [dict(job) if isinstance(job, dict) else job for job in jobs]
            result["value"] = change(jobs)
            payload["jobs"] = jobs
            payload["updated_at"] = self._now().isoformat()
            return payload

        new_rev = self.store.mutate_json(JOBS, revision, callback, authorize=authorize, lock=JOBS_LOCK)
        return new_rev, result.get("value")

    @staticmethod
    def _find(jobs, job_id):
        for index, job in enumerate(jobs):
            if isinstance(job, dict) and job.get("id") == job_id:
                return index, job
        raise AdminError("Job not found. Refresh and try again.", 404)

    @staticmethod
    def _job_id(values):
        job_id = values.get("id")
        if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
            raise AdminError("Invalid job.")
        return job_id

    def _can_run_now(self, job, deliverable):
        """Run now only for local results or a delivery target that is still configured."""
        deliver = self._delivery_choice(job.get("deliver"), job)
        if deliver == "origin":
            return self._origin_allowed(job)
        return deliver == "local" or (deliver in deliverable and deliver != KEEP_DELIVERY)

    def _do_create_routine(self, values, revision, authorize):
        data = self._job_inputs(values)
        deliver = self._delivery(values) or "local"
        paused = values.get("paused", False)
        if not isinstance(paused, bool):
            raise AdminError("Create paused must be true or false.")
        now = self._now(data["tz"]).isoformat()
        schedule = data["schedule"]
        label = data["prompt"] or data["skills"][0]
        job = {
            "id": uuid.uuid4().hex[:12], "name": data["name"] or label[:50].strip(), "prompt": data["prompt"],
            "skills": data["skills"], "skill": data["skills"][0] if data["skills"] else None,
            "model": None, "provider": None, "base_url": None, "script": None, "no_agent": False,
            "monitor_script": None, "monitor_url": None, "monitor_state": None, "context_from": None,
            "schedule": schedule, "schedule_display": schedule.get("display"),
            "repeat": {"times": data["repeat"], "completed": 0},
            "enabled": not paused, "state": "paused" if paused else "scheduled",
            "paused_at": now if paused else None,
            "paused_reason": "Created paused from the admin console." if paused else None,
            "created_at": now,
            # Computed by the Hermes scheduler (missing next_run_at recovery) on its next tick.
            "next_run_at": None,
            "last_run_at": None, "last_status": None, "last_error": None, "last_delivery_error": None,
            "last_delivery_unverified": None, "last_failure": None, "failure_streak": 0,
            "deliver": deliver, "origin": None, "enabled_toolsets": None, "workdir": None,
        }
        def append(jobs):
            job["deliver"] = self._delivery(values) or "local"
            jobs.append(job)
        new_rev, _ = self._mutate_jobs(revision, authorize, append)
        return {"ok": True, "message": f"Automation {job['name']} created" + (" (paused)." if paused else
                ". The scheduler sets its next run time on its next tick."),
                "revision": new_rev, "item": {"id": job["id"], "name": job["name"]}}

    def _do_edit_routine(self, values, revision, authorize):
        job_id = self._job_id(values)
        jobs_now, _ = self._jobs()
        _, current = self._find(jobs_now, job_id)
        data = self._job_inputs(values, existing=current)
        self._delivery(values, current)  # validate before entering the store; validate again under its lock

        def change(jobs):
            index, job = self._find(jobs, job_id)
            current_delivery = self._delivery(values, job)
            if job.get("state") == "completed":
                raise AdminError("A completed job cannot be edited. Create a new one instead.", 409)
            updated = dict(job)
            if data["name"]:
                updated["name"] = data["name"]
            updated["prompt"] = data["prompt"]
            updated["skills"] = data["skills"]
            updated["skill"] = data["skills"][0] if data["skills"] else None
            if current_delivery is not None:
                updated["deliver"] = current_delivery
            repeat = dict(job.get("repeat") or {}) if isinstance(job.get("repeat"), dict) else {}
            repeat["times"] = data["repeat"]
            repeat.setdefault("completed", 0)
            updated["repeat"] = repeat
            old = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
            if data["schedule"] != old or job.get("schedule_display") != data["schedule"].get("display"):
                updated["schedule"] = data["schedule"]
                updated["schedule_display"] = data["schedule"].get("display")
                updated["next_run_at"] = None  # the scheduler recomputes it from the new schedule
                for key in ("pending_slot", "quota_hold_until", "quota_hold_cron_expr"):
                    updated.pop(key, None)
                if updated.get("state") == "error" and updated.get("enabled", True):
                    updated["state"] = "scheduled"
            jobs[index] = updated
            return updated

        new_rev, updated = self._mutate_jobs(revision, authorize, change)
        return {"ok": True, "message": f"Automation {updated.get('name') or job_id} updated.", "revision": new_rev,
                "item": {"id": job_id}}

    def _do_pause_routine(self, values, revision, authorize):
        job_id = self._job_id(values)

        def change(jobs):
            index, job = self._find(jobs, job_id)
            if job.get("state") == "completed":
                raise AdminError("A completed job cannot be paused.", 409)
            job = dict(job, enabled=False, state="paused", paused_at=self._now().isoformat(),
                       paused_reason="Paused from the admin console.")
            job.pop("pending_slot", None)
            jobs[index] = job
            return job

        new_rev, job = self._mutate_jobs(revision, authorize, change)
        return {"ok": True, "message": f"Automation {job.get('name') or job_id} paused.", "revision": new_rev,
                "item": {"id": job_id}}

    def _do_resume_routine(self, values, revision, authorize):
        job_id = self._job_id(values)

        def change(jobs):
            index, job = self._find(jobs, job_id)
            if job.get("state") == "completed":
                raise AdminError("A completed job cannot be resumed. Create a new one instead.", 409)
            schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
            now = self._now()
            stored = job.get("next_run_at")
            next_run = None
            if schedule.get("kind") in ("cron", "interval") and isinstance(stored, str):
                try:
                    moment = datetime.fromisoformat(stored)
                    if moment.tzinfo is not None and moment <= now:
                        next_run = stored  # upstream keeps an elapsed slot due (catch-up policy decides)
                except ValueError:
                    pass
            elif schedule.get("kind") == "once":
                try:
                    run_at = datetime.fromisoformat(str(schedule.get("run_at")))
                except ValueError:
                    run_at = None
                if run_at is None or run_at.tzinfo is None or (now - run_at).total_seconds() > ONESHOT_GRACE_SECONDS:
                    raise AdminError("Cannot resume: the one-shot time is in the past.", 409)
            elif schedule.get("kind") != "cron" and schedule.get("kind") != "interval":
                raise AdminError("This job's schedule is not supported here; use the Hermes CLI.", 409)
            job = dict(job, enabled=True, state="scheduled", paused_at=None, paused_reason=None, next_run_at=next_run)
            job.pop("pending_slot", None)
            jobs[index] = job
            return job

        new_rev, job = self._mutate_jobs(revision, authorize, change)
        return {"ok": True, "message": f"Automation {job.get('name') or job_id} resumed.", "revision": new_rev,
                "item": {"id": job_id}}

    def _do_run_routine(self, values, revision, authorize):
        job_id = self._job_id(values)
        if not self._heartbeat_fresh():
            raise AdminError("No running Hermes scheduler was detected, so the job cannot run now.", 409)

        def change(jobs):
            index, job = self._find(jobs, job_id)
            if not job.get("enabled", True) or _effective_state(job) != "scheduled":
                raise AdminError("Only an active automation can run now.", 409)
            if not self._can_run_now(job, {o["value"] for o in self.delivery_targets()}):
                raise AdminError("Run now needs a local result or a delivery chat that is still configured.", 409)
            moment = self._now().isoformat()
            job = dict(job, next_run_at=moment, manual_run_at=moment, manual_run_prompt=None)
            jobs[index] = job
            return job

        new_rev, job = self._mutate_jobs(revision, authorize, change)
        return {"ok": True, "message": f"Automation {job.get('name') or job_id} will run on the next scheduler tick.",
                "revision": new_rev, "item": {"id": job_id}}

    def _do_delete_routine(self, values, revision, authorize):
        job_id = self._job_id(values)

        def change(jobs):
            index, job = self._find(jobs, job_id)
            del jobs[index]
            return job

        new_rev, job = self._mutate_jobs(revision, authorize, change)
        self._remove_output(job_id)
        return {"ok": True, "message": f"Automation {job.get('name') or job_id} deleted.", "revision": new_rev,
                "item": {"id": job_id}}

    def _remove_output(self, job_id):
        if self.mode == "demo" or not JOB_ID_RE.match(job_id):
            return
        folder = self._safe_dir("cron", "output", job_id)
        if folder is not None:
            shutil.rmtree(folder, ignore_errors=True)  # rmtree never follows symlinks inside

    def _source(self):
        return "demo" if self.mode == "demo" else "hermes-home"


ACTIONS = {
    "skills": {
        "disable_skill": {"skill"}, "enable_skill": {"skill"},
        "disable_category": {"category"}, "enable_category": {"category"},
        "create_skill": {"name", "category", "new_category", "description", "instructions"},
    },
    "routines": {
        "create_routine": {"name", "schedule", "prompt", "skills", "repeat", "paused", "deliver"},
        "edit_routine": {"id", "name", "schedule", "prompt", "skills", "repeat", "deliver"},
        "pause_routine": {"id"}, "resume_routine": {"id"}, "run_routine": {"id"}, "delete_routine": {"id"},
    },
}


def _size(value):
    return len(json.dumps(value, ensure_ascii=True))


def _fit_budget(snapshot):
    """Keep a routines snapshot under SNAPSHOT_BUDGET: drop output excerpts, then trailing rows."""
    if _size(snapshot) <= SNAPSHOT_BUDGET:
        return snapshot
    for item in snapshot["items"]:
        if item.get("latest_output"):
            item["latest_output"] = dict(item["latest_output"], excerpt=None, excerpt_omitted=True)
    while _size(snapshot) > SNAPSHOT_BUDGET and snapshot["items"]:
        dropped = snapshot["items"].pop()["id"]
        snapshot["actions"] = [a for a in snapshot["actions"] if a.get("target") != dropped]
        snapshot["truncated"] = True
    return snapshot


def _effective_state(job):
    """Upstream effective_job_state: enabled is authoritative, terminal states preserved."""
    stored = str(job.get("state") or "").strip()
    if stored in ("completed", "error"):
        return stored
    if not job.get("enabled", True):
        return "paused"
    return "scheduled" if stored in ("", "paused") else stored


def _skill_meta(text, category, folder):
    meta = {}
    match = re.match(r"\A﻿?---\s*\n(.*?)\n---\s*(?:\n|\Z)", text or "", re.S)
    if match:
        try:
            loaded = yaml.safe_load(match.group(1))
            meta = loaded if isinstance(loaded, dict) else {}
        except yaml.YAMLError:
            meta = {}
    raw_name = meta.get("name") if isinstance(meta.get("name"), str) and meta.get("name").strip() else folder
    name = str(raw_name).strip()[:MAX_SKILL_NAME]
    tags = meta.get("tags")
    hermes = (meta.get("metadata") or {}).get("hermes") if isinstance(meta.get("metadata"), dict) else None
    if not isinstance(tags, list) and isinstance(hermes, dict):
        tags = hermes.get("tags")
    return {
        "id": (category + ":" if category else "") + name, "name": name, "category": category,
        "description": redact(meta.get("description"), 240) if isinstance(meta.get("description"), str) else None,
        "version": redact(meta.get("version"), 20) if isinstance(meta.get("version"), (str, int, float)) else None,
        "tags": [redact(t, 30) for t in tags[:8] if isinstance(t, str) and redact(t, 30)] if isinstance(tags, list) else [],
    }


_DEMO_SKILLS = [
    {"id": "autonomous-ai-agents:hermes-agent", "name": "hermes-agent", "category": "autonomous-ai-agents",
     "description": "Configure and extend Hermes Agent.", "version": "1.0.0", "tags": ["hermes"]},
    {"id": "news:morning-briefing", "name": "morning-briefing", "category": "news",
     "description": "Summarize news for a selected topic.", "version": "1.0.0", "tags": ["news"]},
    {"id": "data:data-analysis", "name": "data-analysis", "category": "data",
     "description": "Analyze tabular data and summarize trends.", "version": "1.0.0", "tags": ["data"]},
    {"id": "research:source-research", "name": "source-research", "category": "research",
     "description": "Find sources and summarize key findings.", "version": "1.0.0", "tags": ["research"]},
    {"id": "productivity:weekly-notes", "name": "weekly-notes", "category": "productivity",
     "description": "Summarize weekly notes and follow-ups.", "version": "1.0.0", "tags": ["notes"]},
]

_DEMO_OUTPUTS = {
    "demo00000001": ([{"name": "2026-10-31_07-00-04.md", "at": "2026-10-31T07:00:04"}],
                     {"name": "2026-10-31_07-00-04.md", "excerpt": "Synthetic demo briefing: three headlines summarized.",
                      "truncated": False}),
}


def _demo_jobs():
    base = {"model": None, "provider": None, "base_url": None, "script": None, "no_agent": False,
            "monitor_script": None, "monitor_url": None, "monitor_state": None, "context_from": None,
            "last_error": None, "last_delivery_error": None, "last_delivery_unverified": None, "last_failure": None,
            "failure_streak": 0, "deliver": "local", "origin": None, "enabled_toolsets": None, "workdir": None,
            "created_at": "2026-10-01T08:00:00+07:00", "paused_at": None, "paused_reason": None}
    return [
        {**base, "id": "demo00000001", "name": "Morning briefing", "prompt": "Summarize today's top news.",
         "deliver": "telegram",
         "skills": ["morning-briefing"], "skill": "morning-briefing",
         "schedule": {"kind": "cron", "expr": "0 7 * * *", "display": "0 7 * * *"}, "schedule_display": "0 7 * * *",
         "repeat": {"times": None, "completed": 30}, "enabled": True, "state": "scheduled",
         "next_run_at": "2026-11-01T07:00:00+07:00", "last_run_at": "2026-10-31T07:00:04+07:00", "last_status": "ok"},
        {**base, "id": "demo00000002", "name": "Weekly notes digest", "prompt": "Summarize this week's notes.",
         "skills": ["weekly-notes"], "skill": "weekly-notes",
         "schedule": {"kind": "interval", "minutes": 10080, "display": "every 10080m"},
         "schedule_display": "every 10080m", "repeat": {"times": None, "completed": 4}, "enabled": False,
         "state": "paused", "paused_at": "2026-10-28T09:00:00+07:00", "paused_reason": "Demo pause.",
         "next_run_at": None, "last_run_at": "2026-10-24T09:00:00+07:00", "last_status": "error",
         "last_error": "Synthetic demo error.", "failure_streak": 1},
    ]
