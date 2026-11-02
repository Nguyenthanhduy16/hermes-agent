"""CEO Briefing: the console landing page.

One read-only digest of what the assistant already knows, so the owner sees
"what needs me, how are we doing, what happened, what is next" at a glance:

- ``attention``: failed automations, connectors missing credentials, quota near
  its limit, gateway/scheduler down, new reports. Each item has a severity and
  a console hash link to the page that fixes it.
- ``kpis``: tiles with a value, change vs the previous period and a sparkline.
  Live tiles come from local telemetry, quota, schedules and, optionally,
  ``<home>/console/kpis.json`` (written by your own report jobs; see
  ``_custom_kpis``). Platform metrics are never fetched over the network.
- ``reports``: the newest saved job outputs (cron/output), as short excerpts.
- ``up_next``: the next scheduled automations.
- ``prompts``: quick questions that open Chat with a prefilled message.

Every block degrades on its own to ``{"available": False, "message": ...}``.
Sources are read through the existing services (AutomationAdmin, the
connectors section, admin_operations status readers, AssistantAdmin); nothing
runs a process or opens a network connection.

The only write is ``dismiss`` (and ``restore``): hides an attention item via
``ControlStore.mutate_json("console/briefing.json")`` with a revision check.
Item ids include the occurrence (e.g. the failed run's time), so a new
failure shows up again after an older one was dismissed.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import math
import re
import threading

from .admin_auth import AdminError
from .admin_control_store import ControlStore
from .console_sections import Section, get as get_section, register, validate_action

logger = logging.getLogger(__name__)

STORE = "console/briefing.json"
KPI_FILE = "console/kpis.json"
MAX_DISMISSED = 300
MAX_ATTENTION = 12
MAX_REPORTS = 4
MAX_UP_NEXT = 5
MAX_KPIS = 8
MAX_SERIES = 30
NEW_REPORT_HOURS = 24
QUOTA_WARN = 0.8
ITEM_ID = re.compile(r"a-[0-9a-f]{16}")
VIEW = re.compile(r"[a-z][a-z0-9-]{1,31}")
FAILED = frozenset({"error", "failed", "failure", "timeout", "crashed"})
SEVERITY = {"critical": 0, "warning": 1, "info": 2}
REPORT_PREFIX = "Report · "
JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
OUTPUT_FILE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.md")
OUTPUT_TIME = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

PROMPTS = (
    ("social", "Summarize today's social activity",
     "Summarize today's activity across my connected social channels: reach, notable posts, comments that need a "
     "reply and anything unusual."),
    ("replies", "What needs my reply?",
     "List the messages, comments and mentions that are waiting for my reply, most urgent first."),
    ("week", "Draft my weekly update",
     "Draft a short weekly update for the team from this week's reports and completed tasks."),
    ("failures", "Why did automations fail?",
     "Which of my scheduled automations failed recently, why, and how do I fix them?"),
    ("compare", "Compare this week with last week",
     "Compare this week's key numbers with last week's and explain the biggest changes."),
)


# ---- small helpers -------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _text(value, limit=160):
    if value is None or isinstance(value, (dict, list, tuple, set, bytes)):
        return None
    text = " ".join(_CONTROL.sub(" ", str(value)).split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text or None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _item_id(*parts):
    return "a-" + hashlib.sha256("\0".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _attention(key, severity, kind, title, detail, view, link_label, at=None, report=None):
    item = {"id": _item_id(kind, *key), "severity": severity, "kind": kind, "title": _text(title, 120),
            "detail": _text(detail, 240), "link": "#" + view, "link_label": link_label, "at": at}
    if report:
        item["report"] = report
    return item


def _report_ref(job_id, name):
    """{job, file} for the Reports reader, only when both look like what AutomationAdmin lists."""
    if isinstance(job_id, str) and JOB_ID.fullmatch(job_id) and isinstance(name, str) and OUTPUT_FILE.fullmatch(name):
        return {"job": job_id, "file": name}
    return None


def _parse_time(value):
    """An aware datetime from an ISO string (naive values are taken as UTC), else None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _output_time(name):
    match = OUTPUT_TIME.match(name or "")
    return f"{match.group(1)}T{match.group(2)}:{match.group(3)}:{match.group(4)}" if match else None


def _relative(delta):
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return "in a minute"
    if round(seconds / 60) < 60:
        return f"in {round(seconds / 60)} min"
    if seconds < 36 * 3600:
        hours = round(seconds / 3600)
        return f"in {hours} hour" + ("s" if hours != 1 else "")
    days = round(seconds / 86400)
    return f"in {days} day" + ("s" if days != 1 else "")


def _excerpt(markdown, limit=220):
    """Plain two-line excerpt from Markdown: headings dropped, bullets and emphasis stripped."""
    parts = []
    for line in str(markdown or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or set(line) <= set("-*_=|: "):
            continue
        line = re.sub(r"^(?:[-*+>]|\d+[.)])\s+", "", line)
        line = re.sub(r"[*_`]{1,3}", "", line)
        parts.append(line)
        if sum(len(p) for p in parts) > limit:
            break
    return _text(" · ".join(parts), limit)


def _change(current, previous):
    if _number(current) is None or not _number(previous):
        return None
    return round((current - previous) / abs(previous), 4)


def _unavailable(message):
    return {"available": False, "message": message, "items": []}


# ---- dismissals ------------------------------------------------------------------

_DEMO_LOCK = threading.Lock()
_DEMO_STORES = {}


def _store(ctx):
    if not ctx.demo:
        return ControlStore(ctx.home, mode="local")
    key = str(ctx.home)
    with _DEMO_LOCK:
        store = _DEMO_STORES.get(key)
        if store is None:
            if len(_DEMO_STORES) >= 8:
                _DEMO_STORES.pop(next(iter(_DEMO_STORES)))
            store = _DEMO_STORES[key] = ControlStore(ctx.home, mode="demo")
    return store


def _dismissed(value):
    raw = value.get("dismissed") if isinstance(value, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {key: at for key, at in raw.items() if ITEM_ID.fullmatch(key) and isinstance(at, str)}


def _read_dismissed(store):
    """({id: dismissed_at}, revision) or ({}, None) when the store cannot be read safely."""
    try:
        value, revision = store.read_json(STORE, {})
    except AdminError:
        return {}, None
    return _dismissed(value), revision


# ---- live sources -------------------------------------------------------------------

def _routines(ctx):
    from .admin_automation import AutomationAdmin
    return AutomationAdmin(ControlStore(ctx.home, mode="local"), environ=ctx.environ).snapshot("routines")


def _connectors(ctx):
    section = get_section("connectors")
    if section is None:
        return None
    return section.snapshot(ctx, {})


def _gateway(ctx):
    from .admin_operations import gateway_status, scheduler_status
    # control=None: status files only, never systemctl/docker.
    return gateway_status(ctx.home, ctx.environ, None), scheduler_status(ctx.home)


def _assistant(ctx):
    from .admin_assistant import AssistantAdmin
    return AssistantAdmin(None, None, mode="local", home=ctx.home, assistant_data_dir=ctx.assistant_dir)


def _safe(label, read):
    try:
        return read(), None
    except Exception:  # one unreadable source must not hide the rest of the briefing
        logger.debug("Briefing source %s failed", label, exc_info=True)
        return None, f"{label} could not be read."


def _job_title(name):
    name = name or "Scheduled job"
    return name[len(REPORT_PREFIX):] if name.startswith(REPORT_PREFIX) else name


def _report_view(name):
    return "reports" if (name or "").startswith(REPORT_PREFIX) and get_section("reports") is not None else "routines"


def _live_attention(routines, connectors, gateway, quota, now):
    items = []
    if gateway:
        status, scheduler = gateway
        state = status.get("state")
        if state in ("stopped", "failed"):
            items.append(_attention(("gateway", state), "critical", "gateway", "The gateway is not running",
                                    status.get("detail") or "Chat channels and scheduled automations are offline "
                                    "until it starts.", "operations", "Open System"))
        elif state == "unknown":
            items.append(_attention(("gateway", "unknown"), "warning", "gateway", "Gateway status is unknown",
                                    status.get("detail"), "operations", "Open System"))
        for platform in status.get("platforms") or []:
            if platform.get("needs_attention") or platform.get("error"):
                items.append(_attention(("platform", platform.get("name"), platform.get("error")), "warning",
                                        "channel", f"{str(platform.get('name') or 'A').title()} channel needs attention",
                                        platform.get("error") or platform.get("state"), "channels", "Open Channels"))
        enabled = [job for job in (routines or {}).get("items") or [] if job.get("enabled")]
        if state == "running" and enabled and scheduler.get("status") != "fresh":
            items.append(_attention(("scheduler", scheduler.get("status")), "warning", "scheduler",
                                    "Automations are not being scheduled",
                                    "The gateway is running but the scheduler heartbeat is "
                                    + ("stale." if scheduler.get("status") == "stale" else "missing."),
                                    "operations", "Open System"))
    for job in (routines or {}).get("items") or []:
        title = _job_title(job.get("name"))
        view = _report_view(job.get("name"))
        if str(job.get("last_status") or "").lower() in FAILED:
            streak = job.get("failure_streak") if isinstance(job.get("failure_streak"), int) else 1
            items.append(_attention(("job", job.get("id"), job.get("last_run_at")), "critical" if streak >= 2 or
                                    view == "reports" else "warning", "automation", f"{title} failed",
                                    job.get("last_error") or "The last run did not finish.", view,
                                    "Open " + ("Reports" if view == "reports" else "Automations"), job.get("last_run_at")))
        elif job.get("last_delivery_error"):
            items.append(_attention(("delivery", job.get("id"), job.get("last_run_at")), "warning", "automation",
                                    f"{title} ran but was not delivered", job.get("last_delivery_error"), view,
                                    "Open Automations", job.get("last_run_at")))
        latest = job.get("latest_output") or {}
        at = _output_time(latest.get("name"))
        when = _parse_time(at)
        if when and timedelta(0) <= now - when <= timedelta(hours=NEW_REPORT_HOURS):
            items.append(_attention(("report", job.get("id"), latest.get("name")), "info", "report",
                                    f"New report: {title}", _excerpt(latest.get("excerpt"), 160), view,
                                    "Read report", at, _report_ref(job.get("id"), latest.get("name"))))
    for connector in (connectors or {}).get("items") or []:
        if connector.get("status") == "needs_credentials" and connector.get("servers"):
            missing = [c.get("label") or c.get("variable") for c in connector.get("credentials") or []
                       if not c.get("configured")]
            items.append(_attention(("connector", connector.get("id")), "warning", "connector",
                                    f"{connector.get('name') or 'A connector'} needs credentials",
                                    ("Missing: " + ", ".join(str(m) for m in missing[:3])) if missing
                                    else "A saved server is missing a credential.", "connectors", "Set up"))
    if quota:
        used, limit = _number(quota.get("total_used_today")), _number(quota.get("total_daily_limit"))
        if used is not None and limit:
            ratio = used / limit
            if ratio >= QUOTA_WARN:
                items.append(_attention(("quota", quota.get("day"), "full" if ratio >= 1 else "near"),
                                        "critical" if ratio >= 1 else "warning", "quota",
                                        f"Daily AI quota at {round(ratio * 100)}%",
                                        f"{int(used):,} of {int(limit):,} turns used today.", "users", "Open Users"))
    return items


def _series(daily, key, days, today):
    by_day = {row.get("day"): _number(row.get(key)) or 0 for row in daily or [] if isinstance(row, dict)}
    return [by_day.get((today - timedelta(days=offset)).isoformat(), 0) for offset in range(days - 1, -1, -1)]


def _custom_kpis(store):
    tiles, period, message = _read_custom_kpis(store)
    return tiles, message, period


def _read_custom_kpis(store):
    """Optional tiles from <home>/console/kpis.json, e.g. written by a report job::

        {"period": "Last 7 days", "items": [{"id": "fb-reach", "platform": "facebook", "label": "Page reach",
          "value": 18400, "previous": 16370, "unit": "", "good_when": "up", "series": [1, 2, 3]}]}
    """
    try:
        value, _ = store.read_json(KPI_FILE, None)
    except AdminError:
        return [], None, "console/kpis.json could not be read."
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        return [], None, None
    tiles = []
    for raw in value["items"][:MAX_KPIS]:
        if not isinstance(raw, dict) or _number(raw.get("value")) is None:
            continue
        series = [v for v in (raw.get("series") or [])[-MAX_SERIES:] if _number(v) is not None] \
            if isinstance(raw.get("series"), list) else []
        change = _number(raw.get("change"))
        if change is None and _number(raw.get("previous")):
            change = _change(raw["value"], raw["previous"])
        tiles.append({"id": _text(raw.get("id"), 40) or _item_id("kpi", raw.get("label"))[2:],
                      "platform": _text(raw.get("platform"), 30) or "custom",
                      "label": _text(raw.get("label"), 60) or "Metric", "value": raw["value"],
                      "unit": _text(raw.get("unit"), 12), "change": change,
                      "change_label": _text(raw.get("change_label"), 40) or "vs previous period",
                      "good_when": raw.get("good_when") if raw.get("good_when") in ("up", "down", "neutral") else "up",
                      "series": series, "link": "#reports" if get_section("reports") else None,
                      "source": "console/kpis.json"})
    return tiles, _text(value.get("period"), 40), None


def _live_kpis(store, routines, usage, quota, gateway, now):
    tiles, message, period = _custom_kpis(store)
    today = now.date()
    if usage and usage.get("available"):
        series = _series(usage.get("daily"), "calls", 14, today)
        tiles.append({"id": "assistant-requests", "platform": "assistant", "label": "Assistant requests",
                      "value": sum(series[7:]), "change": _change(sum(series[7:]), sum(series[:7])),
                      "change_label": "vs previous 7 days", "good_when": "neutral", "series": series[7:],
                      "link": "#usage"})
    if quota and _number(quota.get("total_used_today")) is not None:
        limit = _number(quota.get("total_daily_limit"))
        tiles.append({"id": "turns-today", "platform": "assistant", "label": "AI turns today",
                      "value": quota["total_used_today"], "unit": f"/ {int(limit):,}" if limit else None,
                      "change": None, "change_label": "daily limit" if limit else "no limit configured",
                      "good_when": "neutral", "series": [], "link": "#users"})
    jobs = (routines or {}).get("items") or []
    if routines and routines.get("available") is not False:
        active = [job for job in jobs if job.get("enabled")]
        healthy = [job for job in active if str(job.get("last_status") or "").lower() not in FAILED]
        tiles.append({"id": "automations", "platform": "automations", "label": "Healthy automations",
                      "value": len(healthy), "unit": f"/ {len(active)}", "change": None,
                      "change_label": "none scheduled yet" if not jobs else f"{len(jobs) - len(active)} paused"
                      if len(jobs) > len(active) else "all active",
                      "good_when": "up", "series": [], "link": "#routines"})
        week = [0] * 7
        for job in jobs:
            for output in job.get("outputs") or []:
                when = _parse_time(output.get("at") if isinstance(output, dict) else None)
                if when and 0 <= (today - when.date()).days < 7:
                    week[6 - (today - when.date()).days] += 1
        tiles.append({"id": "reports-saved", "platform": "reports", "label": "Reports saved",
                      "value": sum(week), "change": None, "change_label": "last 7 days", "good_when": "up",
                      "series": week, "link": "#reports" if get_section("reports") else "#routines"})
    if gateway:
        platforms = gateway[0].get("platforms") or []
        if platforms:
            up = sum(1 for p in platforms if p.get("state") in ("connected", "running", "ok", "ready"))
            tiles.append({"id": "channels", "platform": "channels", "label": "Channels online", "value": up,
                          "unit": f"/ {len(platforms)}", "change": None,
                          "change_label": ", ".join(str(p.get("name")) for p in platforms[:4]), "good_when": "up",
                          "series": [], "link": "#channels"})
    if not tiles:
        return _unavailable(message or "No metrics are recorded yet. Connect a platform or add a report job.")
    return {"available": True, "period": period or "Last 7 days", "items": tiles[:MAX_KPIS], "message": message}


def _live_reports(routines):
    rows = []
    for job in (routines or {}).get("items") or []:
        latest = job.get("latest_output") or {}
        if not latest.get("name"):
            continue
        name = job.get("name") or ""
        rows.append({"id": _text(job.get("id"), 80), "title": _job_title(name),
                     "source": "Report" if name.startswith(REPORT_PREFIX) else "Automation",
                     "at": _output_time(latest["name"]), "excerpt": _excerpt(latest.get("excerpt")),
                     "status": "failed" if str(job.get("last_status") or "").lower() in FAILED else "ok",
                     "link": "#" + _report_view(name), "report": _report_ref(job.get("id"), latest["name"])})
    rows.sort(key=lambda row: row["at"] or "", reverse=True)
    return {"available": True, "items": rows[:MAX_REPORTS], "total": len(rows)}


def _live_up_next(routines):
    rows = []
    for job in (routines or {}).get("items") or []:
        if not job.get("enabled") or not job.get("next_run_at"):
            continue
        name = job.get("name") or ""
        rows.append({"id": _text(job.get("id"), 80), "name": _job_title(name), "at": job["next_run_at"],
                     "schedule": _text(job.get("schedule"), 80),
                     "kind": "report" if name.startswith(REPORT_PREFIX) else "automation",
                     "link": "#" + _report_view(name)})
    rows.sort(key=lambda row: _parse_time(row["at"]) or datetime.max.replace(tzinfo=timezone.utc))
    return {"available": True, "items": rows[:MAX_UP_NEXT],
            "total": len(rows)}


def _live(ctx, now):
    routines, routines_error = _safe("Automations", lambda: _routines(ctx))
    if routines and routines.get("available") is False:
        routines_error, routines = routines.get("message") or "Automations could not be read.", None
    connectors, connectors_error = _safe("Connectors", lambda: _connectors(ctx))
    if connectors and connectors.get("available") is False:
        connectors = None
    gateway, _ = _safe("Gateway status", lambda: _gateway(ctx))
    assistant, _ = _safe("Assistant data", lambda: _assistant(ctx))
    quota = usage = None
    if assistant is not None:
        quota, _ = _safe("Quota", lambda: assistant.snapshot("users"))
        usage, _ = _safe("Usage", lambda: assistant.snapshot("usage"))
    attention = _live_attention(routines, connectors, gateway, quota, now)
    store = _store(ctx)
    return {
        "attention": attention,
        "attention_note": routines_error,
        "kpis": _live_kpis(store, routines, usage, quota, gateway, now),
        "reports": _live_reports(routines) if routines else _unavailable(routines_error),
        "up_next": _live_up_next(routines) if routines else _unavailable(routines_error),
        "connected": sum(1 for c in (connectors or {}).get("items") or [] if c.get("status") == "enabled"),
        "gateway": (gateway[0].get("state") if gateway else None),
    }


# ---- demo -----------------------------------------------------------------------------

def _demo_refs(ctx):
    """{template id: {job, file}} for the newest synthetic saved report of each template on the Reports page."""
    def read():
        from . import admin_integrations
        return admin_integrations.saved_reports(ctx, 20)
    rows, _ = _safe("Saved reports", read)
    refs = {}
    for row in rows or []:
        ref = _report_ref(row.get("job"), row.get("file")) if isinstance(row, dict) else None
        if ref and isinstance(row.get("template"), str):
            refs.setdefault(row["template"], ref)
    return refs


def _demo(ctx, now):
    refs = _demo_refs(ctx)
    # Up next starts on the next 5-minute mark at least 20 minutes away, so the demo never says "just now".
    soon = (now + timedelta(minutes=24)).replace(second=0, microsecond=0)
    soon -= timedelta(minutes=soon.minute % 5)
    ago = lambda **kw: _iso(now - timedelta(**kw))  # noqa: E731
    ahead = lambda **kw: _iso(soon + timedelta(**kw))  # noqa: E731
    attention = [
        _attention(("demo", "zalo-digest"), "critical", "automation", "Weekly Zalo OA digest failed",
                   "The zalo-oa connector is disabled, so this morning's run could not read OA messages.",
                   "reports", "Open Reports", ago(hours=3, minutes=12)),
        _attention(("demo", "fb-token"), "warning", "connector", "Facebook Pages needs a new access token",
                   "The page token expires in 3 days. Reach and comment reports stop when it does.",
                   "connectors", "Set up"),
        _attention(("demo", "quota"), "warning", "quota", "Daily AI quota at 86%",
                   "430 of 500 turns used today, mostly by the Telegram community group.", "users", "Open Users"),
        _attention(("demo", "x-spike"), "info", "report", "Mentions on X jumped 64%",
                   "A post about the new oat-milk latte is being shared widely; sentiment is mostly positive.",
                   "reports", "Read report", ago(hours=1, minutes=40), refs.get("mentions-to-reply")),
        _attention(("demo", "new-report"), "info", "report", "New report: Daily social summary · Hermes Café",
                   "Reach up 12% on Facebook · two comments need a reply · the Instagram reel is the top post.",
                   "reports", "Read report", ago(hours=2, minutes=5), refs.get("daily-social-summary")),
    ]

    def wave(base, drift, swing, n=14):
        return [round(base * (1 + drift * i / n + swing * ((i * 7919) % 13 - 6) / 60)) for i in range(n)]

    kpis = [
        {"id": "fb-reach", "platform": "facebook", "label": "Facebook page reach", "value": 18420, "change": .124,
         "change_label": "vs previous 7 days", "good_when": "up", "series": wave(2300, .25, .6), "link": "#connectors"},
        {"id": "zalo-messages", "platform": "zalo", "label": "Zalo OA messages", "value": 642, "change": .081,
         "change_label": "vs previous 7 days", "good_when": "up", "series": wave(80, .15, .8), "link": "#channels"},
        {"id": "telegram-members", "platform": "telegram", "label": "Telegram members", "value": 1284, "change": .023,
         "change_label": "vs previous 7 days", "good_when": "up", "series": wave(1250, .03, .05), "link": "#channels"},
        {"id": "x-mentions", "platform": "x", "label": "X mentions", "value": 213, "change": .64,
         "change_label": "vs previous 7 days", "good_when": "up", "series": wave(14, 1.2, 1.1), "link": "#reports"},
        {"id": "tasks-done", "platform": "work", "label": "Tasks completed", "value": 37, "change": -.05,
         "change_label": "Notion + GitHub · vs previous 7 days", "good_when": "up", "series": wave(6, -.1, 1.4),
         "link": "#connectors"},
    ]
    reports = [
        {"id": "demo-social", "title": "Daily social summary · Hermes Café", "source": "Report · Facebook, Instagram",
         "at": ago(hours=2, minutes=5), "status": "ok", "link": "#reports",
         "excerpt": "Reach up 12% on Facebook · two comments need a reply · the Instagram reel about the oat-milk "
                    "latte is the top post with 4.1K views."},
        {"id": "demo-mentions", "title": "Mentions watch · Hermes Café", "source": "Report · X, web search",
         "at": ago(hours=1, minutes=40), "status": "ok", "link": "#reports",
         "excerpt": "213 mentions this week (+64%) · a food blogger's thread drove most of the spike · 3 complaints "
                    "about weekend wait times."},
        {"id": "demo-work", "title": "Weekly work digest", "source": "Report · Notion, GitHub",
         "at": ago(days=1, hours=6), "status": "ok", "link": "#reports",
         "excerpt": "37 tasks closed, 5 fewer than last week · the menu redesign is blocked on photos · two pull "
                    "requests wait for review."},
        {"id": "demo-zalo", "title": "Weekly Zalo OA digest", "source": "Report · Zalo OA",
         "at": ago(days=7, hours=3), "status": "failed", "link": "#reports",
         "excerpt": "642 OA messages (+8%) · most questions were about opening hours and delivery areas."},
    ]
    for row, template in zip(reports, ("daily-social-summary", "mentions-to-reply", "weekly-work-digest", None)):
        row["report"] = refs.get(template)
        if not row["report"]:
            # A failed automation without a saved file cannot open a report reader.
            row["link"] = "#routines"
    up_next = [
        {"id": "demo-replies", "name": "Reply digest", "at": ahead(), "schedule": "every 2 hours, 8:00–20:00",
         "kind": "automation", "link": "#routines"},
        {"id": "demo-mentions", "name": "Mentions watch · Hermes Café", "at": ahead(hours=3),
         "schedule": "every day at 9:00 and 15:00", "kind": "report", "link": "#reports"},
        {"id": "demo-social", "name": "Daily social summary · Hermes Café", "at": ahead(hours=22),
         "schedule": "every day at 8:00", "kind": "report", "link": "#reports"},
        {"id": "demo-work", "name": "Weekly work digest", "at": ahead(days=1, hours=1), "schedule": "Monday 8:00",
         "kind": "report", "link": "#reports"},
    ]
    return {
        "attention": attention, "attention_note": None,
        "kpis": {"available": True, "period": "Last 7 days", "items": kpis},
        "reports": {"available": True, "items": reports, "total": 12},
        "up_next": {"available": True, "items": up_next, "total": 6},
        "connected": 5, "gateway": "running",
    }


# ---- section ---------------------------------------------------------------------------

def _summary(attention, parts):
    critical = sum(1 for item in attention if item["severity"] == "critical")
    warning = sum(1 for item in attention if item["severity"] == "warning")
    if critical:
        tone = "bad"
        head = f"{critical} urgent item" + ("s" if critical != 1 else "") + " need" + ("" if critical != 1 else "s") \
            + " your attention"
    elif warning:
        tone = "warning"
        head = f"{warning} item" + ("s" if warning != 1 else "") + " to review"
    else:
        tone, head = "good", "All clear: nothing needs you right now"
    return tone, head + "".join("; " + part for part in parts if part) + "."


def _build(ctx, now):
    data = _demo(ctx, now) if ctx.demo else _live(ctx, now)
    store = _store(ctx)
    dismissed, revision = _read_dismissed(store)
    attention = sorted(data["attention"], key=lambda item: (SEVERITY[item["severity"]], -(
        (_parse_time(item.get("at")) or now).timestamp())))
    unique, seen = [], set()
    for item in attention:
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)
    visible = [item for item in unique if item["id"] not in dismissed]
    hidden = len(unique) - len(visible)
    visible = visible[:MAX_ATTENTION]
    parts = []
    if data["connected"]:
        parts.append(f"{data['connected']} connector" + ("s" if data["connected"] != 1 else "") + " configured")
    reports = data["reports"]
    fresh = [r for r in reports.get("items") or []
             if (when := _parse_time(r.get("at"))) and now - when <= timedelta(hours=NEW_REPORT_HOURS)]
    if fresh:
        parts.append(f"{len(fresh)} new report" + ("s" if len(fresh) != 1 else "") + " today")
    upcoming = data["up_next"].get("items") or []
    nxt = _parse_time(upcoming[0]["at"]) if upcoming else None
    if nxt and nxt > now:
        parts.append(f"next automation {_relative(nxt - now)}")
    tone, summary = _summary(visible, parts)
    actions = []
    if revision is not None:
        for item in visible:
            actions.append({"id": "dismiss", "label": "Dismiss", "revision": revision, "target": item["id"],
                            "fields": [{"name": "id", "label": "Item", "type": "hidden", "value": item["id"]}],
                            "description": "Hide this item from the briefing. A new occurrence shows up again."})
        if visible:
            actions.append({"id": "dismiss_all", "label": "Dismiss all", "revision": revision, "fields": [],
                            "description": f"Hide all {len(visible)} item{'s' if len(visible) != 1 else ''} on the "
                                           "briefing, including urgent ones. "
                                           "New occurrences show up again; \"Show dismissed items\" brings them back."})
        if dismissed:
            actions.append({"id": "restore", "label": "Show dismissed items", "revision": revision, "fields": [],
                            "description": "Bring back every attention item you dismissed."})
    return {
        "section": "briefing", "available": True, "source": "demo" if ctx.demo else "hermes-home",
        "updated_at": _iso(now), "generated_at": _iso(now),
        "summary": summary, "tone": tone, "gateway": data["gateway"], "connected": data["connected"],
        "attention": {"available": True, "items": visible, "dismissed": hidden, "can_dismiss": revision is not None,
                      "message": data.get("attention_note")},
        "kpis": data["kpis"], "reports": reports, "up_next": data["up_next"],
        "prompts": [{"id": pid, "label": label, "prompt": prompt} for pid, label, prompt in PROMPTS],
        "items": visible, "actions": actions, "revision": revision,
        "message": "Demo data: synthetic numbers; nothing is read from disk or sent anywhere." if ctx.demo else None,
    }


def snapshot(ctx, query):
    return _build(ctx, _now())


def action(ctx, data):
    action_id, revision, values = validate_action(data)
    if action_id not in ("dismiss", "dismiss_all", "restore"):
        raise AdminError("Unknown action for this page.")
    store = _store(ctx)
    if action_id == "dismiss":
        if set(values) != {"id"} or not isinstance(values["id"], str) or not ITEM_ID.fullmatch(values["id"]):
            raise AdminError("Choose an attention item to dismiss.")
        current = _build(ctx, _now())
        if values["id"] not in {item["id"] for item in current["attention"]["items"]}:
            raise AdminError("This item is no longer on the briefing. Refresh the page.", 409)
        targets = [values["id"]]
    elif values:
        raise AdminError("Unexpected fields for this action.")
    elif action_id == "dismiss_all":
        targets = [item["id"] for item in _build(ctx, _now())["attention"]["items"]]
        if not targets:
            raise AdminError("Nothing to dismiss. Refresh the page.", 409)
    if not isinstance(revision, str):
        raise AdminError("A valid data revision is required.")
    stamp = _iso(_now())

    def change(value):
        dismissed = _dismissed(value)
        if action_id in ("dismiss", "dismiss_all"):
            for target in targets:
                dismissed[target] = stamp
            dismissed = dict(sorted(dismissed.items(), key=lambda pair: pair[1])[-MAX_DISMISSED:])
        else:
            dismissed = {}
        return {"version": 1, "dismissed": dismissed}

    store.mutate_json(STORE, revision, change, default={})
    if action_id == "dismiss":
        return {"ok": True, "message": "Dismissed. It will not show again unless it happens again."}
    if action_id == "dismiss_all":
        return {"ok": True, "message": f"Dismissed {len(targets)} items. New occurrences will show up again."}
    return {"ok": True, "message": "Dismissed items are visible again."}


register(Section("briefing", snapshot, action))
