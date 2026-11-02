"""Integrations console pages: an MCP connector catalog and scheduled reports.

``connectors`` shows curated connector templates (``integration_catalog``) with a
status computed from the bot home's ``config.yaml`` and ``.env``, grouped as
connected / needs setup / available. ``reports`` lists the saved report files
(newest first) with a reader for one of them, creates scheduled report jobs from
templates (template -> connectors -> schedule -> delivery -> confirm) and manages
those jobs (run now, pause, resume, delete).

Single writer: every change is delegated to the existing services with the
revisions shown in the snapshot, so the same validation and isolation rules
apply as on the Tools (MCP), API keys and Schedules pages:

- install / enable / disable / remove a server -> ``ConnectionsAdmin`` (mcp);
- known provider keys -> ``ConnectionsAdmin`` (keys); other connector
  credentials declared by the catalog -> ``ControlStore.mutate_env`` behind a
  strict allowlist (the store used by ``ConnectionsAdmin`` itself);
- report jobs -> ``AutomationAdmin`` (routines); delivery stays local.

The console never launches local (stdio) servers and never runs OAuth. The only
network call is the owner-confirmed "Test connection": an MCP ``initialize`` +
``tools/list`` to the configured URL (https, or http on loopback), no proxies,
no redirects, short timeouts and bounded responses. Configured headers are sent
only to that server. Demo mode keeps everything in memory and never touches the
disk or network.
"""
from datetime import datetime, timezone
import http.client
import json
import hashlib
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from .admin_auth import AdminError
from .admin_automation import MAX_JOB_NAME, MAX_PROMPT, AutomationAdmin
from .admin_connections import (KEY_NAMES, NAME as SERVER_NAME, ConnectionsAdmin, _check_url, _host,
                                _mcp_exposure, _mcp_token_var, _secret)
from .admin_control_store import ENV_NAME, ControlStore
from .console_sections import Section, register, validate_action
from . import integration_catalog as catalog

RESTART = "Hermes reads it at its next start, so restart the gateway to apply it."
DEMO_TIME = "2026-10-31T08:00:00+00:00"
REPORT_PREFIX = "Report · "
REPORT_MARKER = re.compile(r"^Hermes report: ([a-z0-9-]{1,48})\b")
REPORT_JOB_ACTIONS = ("run_routine", "pause_routine", "resume_routine", "delete_routine")
LANGUAGES = (("vi", "Tiếng Việt"), ("en", "English"))
# Schedule presets offered by the "New report" flow; "template" keeps the template's own schedule.
SCHEDULE_PRESETS = (("template", None), ("daily-8", "0 8 * * *"), ("weekdays-8", "0 8 * * 1-5"),
                    ("monday-8", "0 8 * * 1"), ("custom", None))
PRESET_LABELS = {"daily-8": "Every day at 8:00", "weekdays-8": "Weekdays at 8:00", "monday-8": "Every Monday at 8:00",
                 "custom": "Custom (cron, interval or phrase below)"}
USE_PREFIX = "use."
SAVED_LIMIT = 60          # saved report rows in one snapshot
READER_MAX_CHARS = 64_000  # reader text bound (the file itself is bounded by AutomationAdmin)
REPORT_FILE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.md")
JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
RISK_ORDER = ("read", "unknown", "write", "public", "messages")
STATUS_LABELS = {"available": "Available", "needs_credentials": "Needs credentials",
                 "installed": "Installed (disabled)", "enabled": "Enabled", "bridge_needed": "Bridge needed",
                 "unknown": "Unknown"}
KIND_LABELS = {"remote": "Remote MCP server", "local": "Local server · install on the server",
               "bridge": "Bridge needed", "custom": "Any HTTP MCP server"}

# Credentials a connector may write to the bot home .env (besides ConnectionsAdmin's own keys).
_CREDENTIAL_SHAPE = re.compile(r"[A-Z][A-Z0-9_]{1,63}_(?:TOKEN|ACCESS_TOKEN|API_KEY|KEY|SECRET|CLIENT_SECRET|CLIENT_ID)")
_DENIED_PREFIXES = ("HERMES_", "ASSISTANT_", "PYTHON", "LD_", "DYLD_", "ZALO_BOT", "OPENAI_BASE", "NODE_", "NPM_")
_DENIED = frozenset({"PATH", "HOME", "SHELL", "ZALO_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"})

PROBE_TIMEOUT = 5.0
PROBE_DEADLINE = 15.0
PROBE_MAX_BYTES = 256 * 1024
PROBE_PROTOCOL = "2025-06-18"
PROBE_PAGES = 5
PROBE_TOOL_LIMIT = 500
TOOL_NAME = re.compile(r"[A-Za-z0-9_.:/-]{1,64}")
HEADER_NAME = re.compile(r"[A-Za-z0-9-]{1,64}")
ENV_REF = re.compile(r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}")
SKIPPED_HEADERS = frozenset({"host", "content-length", "content-type", "accept", "transfer-encoding", "connection",
                             "mcp-session-id", "mcp-protocol-version"})


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_variable(name):
    return (isinstance(name, str) and ENV_NAME.fullmatch(name) is not None and _CREDENTIAL_SHAPE.fullmatch(name) is not None
            and name not in _DENIED and not name.startswith(_DENIED_PREFIXES))


TEMPLATES = {item["id"]: item for item in catalog.CONNECTORS}
REPORTS = {item["id"]: item for item in catalog.REPORTS}
CATEGORY_LABELS = dict(catalog.CATEGORIES)
WRITABLE = {}
for _template in catalog.CONNECTORS:
    for _cred in _template["credentials"]:
        if _cred["writable"]:
            if not _safe_variable(_cred["variable"]):
                raise ValueError("Unsafe catalog credential: " + _cred["variable"])
            WRITABLE.setdefault(_template["id"], set()).add(_cred["variable"])
    if _template["category"] not in CATEGORY_LABELS:
        raise ValueError("Unknown catalog category: " + _template["category"])
for _report in catalog.REPORTS:
    if any(name not in TEMPLATES for name in _report["connectors"]):
        raise ValueError("Unknown connector in report template " + _report["id"])


def _field(name, label, kind="text", **extra):
    return {"name": name, "label": label, "type": kind, **{k: v for k, v in extra.items() if v is not None}}


def _hidden(name, value):
    return _field(name, name.replace("_", " ").capitalize(), "hidden", value=value)


def _action(id_, label, revision, fields, *, target=None, description=None):
    out = {"id": id_, "label": label, "revision": revision, "fields": fields}
    if target is not None:
        out["target"] = target
    if description:
        out["description"] = description
    return out


def _expect(values, required=(), optional=()):
    unknown = set(values) - set(required) - set(optional)
    if unknown:
        raise AdminError("Unsupported field: " + sorted(unknown)[0][:40])
    missing = [name for name in required if name not in values]
    if missing:
        raise AdminError("Missing field: " + missing[0])
    for value in values.values():
        if value is not None and not isinstance(value, (str, bool, int, float)):
            raise AdminError("Action values must be text, numbers or booleans.")


def _envelope(action, revision, values):
    return {"action": action, "expected_revision": revision, "confirm": True,
            "values": {key: value for key, value in values.items() if value is not None}}


def _single_line(value, label, maximum, *, required=False):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise AdminError(label + " must be text.")
    value = " ".join(value.split())
    if required and not value:
        raise AdminError(label + " is required.")
    if len(value) > maximum:
        raise AdminError(f"{label} must be at most {maximum} characters.")
    if re.search(r"[\x00-\x1f\x7f]", value):
        raise AdminError(label + " contains unsupported characters.")
    return value


def _enabled_flag(spec):
    enabled = spec.get("enabled")
    return enabled is not False and str(enabled).lower() not in ("false", "off", "0", "no")


# ---- services -----------------------------------------------------------------

class _Services:
    """The existing single-writer services bound to one bot home."""

    def __init__(self, ctx):
        self.demo = ctx.demo
        self.store = ControlStore(ctx.home, mode="demo" if ctx.demo else "local")
        self.environ = {} if ctx.demo else ctx.environ
        self.connections = ConnectionsAdmin(self.store, environ=self.environ)
        # Same construction as the Schedules page (ControlsAdmin), so times match.
        self.automation = AutomationAdmin(self.store, environ=self.environ)
        self.lock = threading.RLock()
        self.seeded = False
        self.demo_runs = {}
        self.demo_reports = {}  # job id -> [{"name", "text"}], newest first (demo only)
        # Last connection tests, in memory only (per demo home, or process-wide for real homes).
        self.tests = {} if ctx.demo else _TEST_RESULTS

    def seed(self):
        """Synthetic demo connectors and report jobs, created through the services in memory."""
        if not self.demo:
            return
        with self.lock:
            if self.seeded:
                return
            self.seeded = True
            snap = self.connections.snapshot("mcp")
            add = next(a for a in snap["actions"] if a["id"] == "add_http_server")
            result = self.connections.action("mcp", _envelope("add_http_server", add["revision"], {
                "name": "github", "url": "https://api.githubcopilot.com/mcp/", "token": "demo-token-not-real",
                "timeout": 120, "enabled": True}))
            result = self.connections.action("mcp", _envelope("add_http_server", result["revision"], {
                "name": "facebook-pages", "url": "http://127.0.0.1:8791/mcp", "timeout": 60, "enabled": True}))
            self.connections.action("mcp", _envelope("add_http_server", result["revision"], {
                "name": "zalo-oa", "url": "http://127.0.0.1:8790/mcp", "timeout": 60, "enabled": False}))
            _, env_rev = self.store.read_env()
            self.store.mutate_env(env_rev, {"NOTION_TOKEN": "demo-token-not-real"})
            routines = self.automation.snapshot("routines")
            revision = routines["revision"]
            for template_id, account, schedule, paused, status, reports in DEMO_REPORT_JOBS:
                template = REPORTS[template_id]
                values = _report_values(template, {"account": account, "focus": "", "schedule": schedule,
                                                   "language": "en", "notes": "", "paused": paused,
                                                   "delivery": DEMO_REPORT_DELIVERY.get(template_id, "local")})
                created = self.automation.action("routines", _envelope("create_routine", revision, values))
                revision = created["revision"]
                job_id = created["item"]["id"]
                saved = sorted(({"name": name, "text": text} for name, text in reports),
                               key=lambda row: row["name"], reverse=True)
                self.demo_reports[job_id] = saved
                latest = saved[0] if saved else None
                self.demo_runs[job_id] = {
                    "last_run_at": _file_time(latest["name"]) if latest else "2026-10-31T08:00:12+07:00",
                    "last_status": status,
                    "last_error": "Synthetic demo error: the zalo-oa connector is disabled." if status == "error" else None,
                    "latest_output": {"name": latest["name"], "excerpt": latest["text"][:2000], "truncated":
                                      len(latest["text"]) > 2000} if latest else None}


# Synthetic demo reports: realistic shape, invented numbers, no real accounts or people.
DEMO_DAILY_TODAY = """# Daily social summary · Hermes Café · 31 Oct 2026

## Summary
- **Reach up 12%** on Facebook after the Halloween latte post (synthetic data).
- Two comments are waiting for a reply for more than 6 hours.
- The Instagram reel *"Pumpkin spice in 15 seconds"* is the top post of the day.

## Key metrics
| Metric | Value | Change |
|---|---:|---:|
| Facebook reach | 18,420 | +12% |
| Instagram reach | 9,310 | +4% |
| Engagement rate | 5.8% | +0.6 pt |
| New followers | 146 | +21 |

## Top content
1. **Instagram reel · Pumpkin spice in 15 seconds**: 2,904 plays, 7.1% engagement. Short, seasonal, strong hook.
2. **Facebook post · Halloween latte**: 412 reactions, 38 shares. Customer photos drove sharing.
3. *Weakest:* Facebook link post to the blog, 0.9% engagement. Links without an image underperform.

## Needs attention
- A customer asked twice about vegan milk options and has no answer yet.
- One negative comment about waiting time at the Đống Đa branch.

## Suggested actions
1. Reply to the two open comments before 10:00.
2. Turn the reel format into a weekly series.
3. Add an image to blog link posts.

## Data gaps
- TikTok is not connected, so TikTok numbers are missing.
- See the [Meta Graph API docs](https://developers.facebook.com/docs/graph-api) for the reach definition.
"""

DEMO_DAILY_YESTERDAY = """# Daily social summary · Hermes Café · 30 Oct 2026

## Summary
- Reach was flat on Facebook and **down 6%** on Instagram (synthetic data).
- No comments are waiting for a reply.
- The menu carousel was the top post.

## Key metrics
| Metric | Value | Change |
|---|---:|---:|
| Facebook reach | 16,450 | +1% |
| Instagram reach | 8,950 | -6% |
| Engagement rate | 5.2% | -0.2 pt |
| New followers | 125 | -9 |

## Top content
1. **Instagram carousel · Autumn menu**: 1,120 likes, 6.4% engagement.
2. *Weakest:* Facebook text-only opening hours post, 1.1% engagement.

## Suggested actions
1. Schedule the Halloween reel for the morning slot.
2. Pin the autumn menu post on Facebook.

## Data gaps
- TikTok is not connected.
"""

DEMO_WORK_DIGEST = """# Weekly work digest · Marketing team · 20–26 Oct 2026

## Summary
- 14 tasks closed, 3 overdue, 2 decisions waiting for you (synthetic data).
- The autumn campaign landing page shipped on Thursday.
- Next week is busy: 9 meetings, including the quarterly review.

## Needs a decision
| Item | Waiting | Since |
|---|---|---|
| Approve the November ad budget | Finance | Tue |
| Choose the new packaging supplier | Operations | Thu |

## Done last week
- **Website:** autumn landing page, menu photos, Search Console fix for 4 broken links.
- **Social:** 11 posts published, Halloween reel produced.
- **Ops:** new opening hours published on all channels.

## Coming up
- Mon 09:00 · Weekly planning
- Wed 14:00 · Quarterly review (slides due Tue)
- Fri · Deadline: November content calendar

## Risks
- The *loyalty app* issue `#142` is blocked on a vendor reply for 9 days.
- Two blog drafts have no reviewer assigned.

## Data gaps
- Gmail is not connected; email threads were not checked.
"""

DEMO_MENTIONS = """# Mentions watch · Hermes Café · 31 Oct 2026, 17:00

## Reply first
1. **Facebook comment** on the Halloween post: a customer asks whether the café is open on 1 Nov. *Why:* question, high visibility.
2. **Zalo OA message**: a company asks for a quote for 40 drinks on Friday. *Why:* sales lead.

## Reply today
- Instagram: two people asked for the pumpkin spice recipe.
- Facebook: a regular customer praised the new barista.

## Suggested replies
> Yes, we are open on 1 Nov from 7:00 to 22:00. See you there!

> Thank you! We will send a quote within the hour. Could you share the delivery address?

## Skipped
- 6 spam comments, 3 emoji-only reactions.

## Data gaps
- TikTok and X are not connected.
"""

DEMO_REPORT_JOBS = (
    ("daily-social-summary", "Hermes Café", "0 8 * * *", False, "ok",
     (("2026-10-31_08-00-12.md", DEMO_DAILY_TODAY), ("2026-10-30_08-00-09.md", DEMO_DAILY_YESTERDAY))),
    ("weekly-work-digest", "Marketing team", "0 8 * * 1", False, "ok",
     (("2026-10-27_08-00-21.md", DEMO_WORK_DIGEST),)),
    ("mentions-to-reply", "Hermes Café", "0 9,17 * * *", False, "ok",
     (("2026-10-31_17-00-05.md", DEMO_MENTIONS),)),
    ("zalo-oa-weekly", "Hermes Café OA", "every monday 8am", True, "error", ()),
)


DEMO_REPORT_DELIVERY = {"daily-social-summary": "telegram", "weekly-work-digest": "zalo"}


def _file_time(name):
    """ISO time of a cron output file name (2026-10-31_08-00-12.md), in the scheduler's local time."""
    return name[:10] + "T" + name[11:19].replace("-", ":")


_CACHE_LOCK = threading.Lock()
_DEMO_SERVICES = {}
_TEST_RESULTS = {}  # (home, server name) -> last connection test, in memory only


def _services(ctx):
    if not ctx.demo:
        return _Services(ctx)
    key = str(ctx.home)
    with _CACHE_LOCK:
        services = _DEMO_SERVICES.get(key)
        if services is None:
            if len(_DEMO_SERVICES) >= 8:
                _DEMO_SERVICES.pop(next(iter(_DEMO_SERVICES)))
            services = _DEMO_SERVICES[key] = _Services(ctx)
    services.seed()
    return services


def _test_key(ctx, name):
    return (ctx.mode, str(ctx.home), name)


def _test_fingerprint(spec, env, environ):
    # Server configuration and effective credential values, never sent to clients.
    references = {key: env.get(key) or environ.get(key) for key in _references(spec)}
    connection = {key: value for key, value in spec.items() if key != "enabled"}
    raw = json.dumps([connection, references], sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


# ---- connectors -----------------------------------------------------------------

def _present(env, environ, name):
    if env.get(name):
        return "home .env"
    if environ.get(name):
        return "process environment"
    return None


def _references(spec):
    """Variables referenced as ${VAR} by a server's headers, env and URL."""
    found = []
    values = [spec.get("url")]
    for key in ("headers", "env"):
        block = spec.get(key)
        if isinstance(block, dict):
            values.extend(block.values())
    for value in values:
        if isinstance(value, str):
            for match in ENV_REF.finditer(value):
                if match.group(1) not in found:
                    found.append(match.group(1))
    return found


def _console_token(name, spec):
    """The token variable this console created for a server, when its header uses exactly that reference."""
    headers = spec.get("headers")
    variable = _mcp_token_var(name)
    if isinstance(headers, dict) and headers.get("Authorization") == "Bearer ${" + variable + "}":
        return variable
    return None


def _template_for(name, spec):
    host = _host(spec.get("url")) if spec.get("url") else None
    for template in catalog.CONNECTORS:
        if template["kind"] == "custom":
            continue
        if name == template["server_name"]:
            return template["id"]
        if host and template.get("url") and _host(template["url"]) == host:
            return template["id"]
    return "custom"


def _server_rows(config, env, environ, tests, ctx):
    rows = {}
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return rows
    for name, spec in list(servers.items())[:100]:
        if not isinstance(name, str) or not isinstance(spec, dict):
            continue
        transport = "http" if spec.get("url") else "stdio" if spec.get("command") else "unknown"
        missing = [var for var in _references(spec) if not _present(env, environ, var)]
        token = _console_token(name, spec)
        cached = tests.get(_test_key(ctx, name)) or {}
        tested = cached.get("result") if cached.get("fingerprint") == _test_fingerprint(spec, env, environ) else None
        row = {"name": name[:48], "transport": transport, "enabled": _enabled_flag(spec),
               "host": _host(spec.get("url")) if transport == "http" else None,
               "credentials_configured": bool(spec.get("headers") or spec.get("env") or spec.get("auth")),
               "missing_credentials": missing[:10], "console_token": token,
               "exposed_to": _mcp_exposure(config, name, environ, env),
               "manageable": SERVER_NAME.fullmatch(name) is not None,
               "auth_method": "oauth_operator" if spec.get("auth") == "oauth" else "bearer" if spec.get("headers") else "server_managed",
               "runtime_status": "unknown",
               "last_test": tested}
        rows.setdefault(_template_for(name, spec), []).append(row)
    return rows


def _risk(template):
    levels = [cap["access"] for cap in template["capabilities"]]
    return max(levels, key=RISK_ORDER.index) if levels else "read"


def _status(template, servers, credentials):
    if servers:
        if any(server["missing_credentials"] for server in servers):
            return "needs_credentials"
        return "enabled" if any(server["enabled"] for server in servers) else "installed"
    if template["kind"] == "bridge":
        return "bridge_needed"
    if template["kind"] == "local" and any(not cred["configured"] for cred in credentials):
        return "needs_credentials"
    return "available"


def _install_description(template, exposure):
    risky = [cap["label"] for cap in template["capabilities"] if cap["access"] in ("write", "public", "messages")]
    parts = []
    if template["kind"] == "bridge":
        parts.append("Enter the URL of the bridge you run for " + template["name"] + ". " + template["api"] + ".")
    elif template["kind"] == "custom":
        parts.append("Adds any Streamable HTTP MCP server.")
    if risky:
        parts.append("Warning: the assistant could use tools that " + "; ".join(r.lower() for r in risky) +
                     ". Prefer read-only tokens.")
    if exposure:
        parts.append("Chat users on " + ", ".join(exposure) + " would receive its tools, so it can only be saved "
                     "disabled until those channels list no_mcp in their toolsets.")
    parts.append("Same rules as the Tools (MCP) page. The connection is not tested automatically. " + RESTART)
    return " ".join(parts)


def _template_actions(template, servers, credentials, config_rev, env_rev, exposure):
    tid, actions = template["id"], []
    if template["kind"] in ("remote", "bridge", "custom") and (template["kind"] == "custom" or not servers):
        label = {"remote": "Install", "bridge": "Install bridge URL", "custom": "Add server"}[template["kind"]]
        fields = [_hidden("template", tid),
                  _field("name", "Server name", value=template["server_name"] or None, required=True, max_length=48,
                         help="Lowercase letters, digits, - and _. Used as the tool prefix."),
                  _field("url", "Server URL", value=template.get("url") or None, required=True, max_length=500,
                         help="https URL of the MCP endpoint (http only for localhost)" +
                              (", for example http://127.0.0.1:8790/mcp for a local bridge." if template["kind"] == "bridge" else ".")),
                  _field("authentication", "Authentication", "select", value="bearer" if template["kind"] == "remote" else "none",
                         options=[{"value": "bearer", "label": "Bearer token"}] if template["kind"] == "remote" else
                                 [{"value": "none", "label": "No authentication"}, {"value": "bearer", "label": "Bearer token"}]),
                  _field("token", "Bearer token", "password", max_length=4096, required=True,
                         when={"field": "authentication", "value": "bearer"},
                         help="Saved write-only in the bot home .env and sent only to this endpoint."),
                  _field("timeout", "Tool timeout (seconds)", "number", value=120, help="From 5 to 600."),
                  _field("enabled", "Enable after restart", "checkbox", value=False,
                         help="Leave off to review the tools with Test connection first.")]
        actions.append(_action("install", label + (" " + template["name"] if template["kind"] != "custom" else ""),
                               f"{config_rev}.{env_rev}", fields, target=tid,
                               description=_install_description(template, exposure)))
    for cred in credentials:
        # Provider secrets alone do not create a bridge. HTTP servers keep those
        # secrets in their own environment, not in the Console's process.
        if template["kind"] == "bridge" and not any(s["transport"] == "stdio" for s in servers):
            continue
        if not cred["writable"]:
            continue
        verb = "Replace " if cred["source"] == "home .env" else "Set "
        actions.append(_action("set_credential", verb + cred["variable"], env_rev, [
            _hidden("template", tid), _hidden("variable", cred["variable"]),
            _field("value", cred["label"], "password", required=True, max_length=4096, help=cred["help"])],
            target=tid, description="Stored only in the bot home .env and never shown again. " + RESTART))
        if cred["source"] == "home .env":
            actions.append(_action("remove_credential", "Remove " + cred["variable"], env_rev, [
                _hidden("template", tid), _hidden("variable", cred["variable"])], target=tid,
                description="Deletes the value from the bot home .env. A value inherited from the process environment "
                            "would still apply. " + RESTART))
    for server in servers:
        if not server["manageable"]:
            continue
        name = server["name"]
        hidden = [_hidden("template", tid), _hidden("name", name)]
        enable = not server["enabled"]
        actions.append(_action("toggle_server", ("Enable " if enable else "Disable ") + name, config_rev,
                               hidden + [_field("enabled", "Enabled", "checkbox", value=enable)], target=tid,
                               description=("Refused while chat users on " + ", ".join(server["exposed_to"]) +
                                            " would receive its tools. " if enable and server["exposed_to"] else "")
                               + RESTART))
        if server["transport"] == "http" and server["auth_method"] != "oauth_operator":
            actions.append(_action("test_connection", "Test " + name, config_rev, hidden, target=tid,
                                   description="Connects once to the configured URL (MCP initialize and tools/list, "
                                               "15 s limit, no redirects) and lists its tools. Configured headers are "
                                               "sent only to that server. Nothing is saved to disk."))
        if server["console_token"]:
            actions.append(_action("replace_token", "Replace token for " + name, env_rev,
                                   hidden + [_field("value", "Bearer token", "password", required=True, max_length=4096)],
                                   target=tid, description="Replaces " + server["console_token"] +
                                   " in the bot home .env. " + RESTART))
        actions.append(_action("remove_server", "Remove " + name, f"{config_rev}.{env_rev}", hidden, target=tid,
                               description="Deletes this server from config.yaml and the token this console created "
                                           "for it, if any. " + RESTART))
    return actions


def _connector_state(services, ctx, *, with_actions=True):
    """Items, actions and revisions for the catalog; raises AdminError when the config is unreadable."""
    config, config_rev = services.store.read_yaml("config.yaml")
    env, env_rev = services.store.read_env()
    environ = services.environ
    rows = _server_rows(config, env, environ, services.tests, ctx)
    exposure = _mcp_exposure(config, "", environ, env)
    items, actions = [], []
    for template in catalog.CONNECTORS:
        servers = rows.get(template["id"], [])
        credentials = []
        for cred in template["credentials"]:
            source = _present(env, environ, cred["variable"])
            credentials.append({"variable": cred["variable"], "label": cred["label"], "help": cred["help"],
                                "writable": cred["writable"], "configured": source is not None, "source": source})
        status = _status(template, servers, credentials)
        guide = catalog.connection_guide(template)
        items.append({
            "id": template["id"], "name": template["name"], "category": template["category"],
            "category_label": CATEGORY_LABELS[template["category"]], "initials": template["initials"],
            "description": template["description"], "kind": template["kind"], "kind_label": KIND_LABELS[template["kind"]],
            "api": template["api"], "note": template.get("note"), "status": status, "status_label": STATUS_LABELS[status],
            "risk": _risk(template), "risk_label": catalog.ACCESS_LABELS[_risk(template)],
            "url": template.get("url"), "server_name": template["server_name"] or None,
            "capabilities": [dict(cap, access_label=catalog.ACCESS_LABELS[cap["access"]]) for cap in template["capabilities"]],
            "credentials": credentials, "docs": [dict(doc) for doc in template["docs"]],
            "snippet": template.get("snippet"), "servers": servers,
            "publisher": guide["publisher"], "connection": guide,
            "configured": bool(servers) or any(c["configured"] for c in credentials),
            "test_status": "failed" if any(s["last_test"] and s["last_test"].get("ok") is False for s in servers)
                else "passed" if servers and all(s["last_test"] and s["last_test"].get("ok") is True for s in servers) else "not_tested",
            "runtime_status": "unknown",
            "runtime_note": "Restart the gateway after changes. Runtime use is not verified by this Console.",
        })
        if with_actions:
            actions += _template_actions(template, servers, credentials, config_rev, env_rev, exposure)
    return items, actions, config_rev, env_rev


def _result(ctx, section, available, **extra):
    return {"section": section, "available": available, "source": "demo" if ctx.demo else "hermes-home",
            "updated_at": DEMO_TIME if ctx.demo else _now(), "items": [], "actions": [], **extra}


CONNECTORS_MESSAGE = "Credentials are write-only · local servers are installed on the server · changes apply after a restart."
# Status -> page section: what is working, what needs the owner, what could be added.
GROUPS = {"enabled": "configured", "installed": "needs_setup", "needs_credentials": "needs_setup",
          "available": "available", "bridge_needed": "available", "unknown": "available"}


def connectors_snapshot(ctx, query):
    services = _services(ctx)
    try:
        items, actions, config_rev, env_rev = _connector_state(services, ctx)
    except AdminError as error:
        return _result(ctx, "connectors", False, message=str(error))
    counts = {key: sum(1 for item in items if item["status"] == key) for key in STATUS_LABELS}
    categories = [{"id": key, "label": label, "count": sum(1 for item in items if item["category"] == key)}
                  for key, label in catalog.CATEGORIES]
    message = CONNECTORS_MESSAGE
    if ctx.demo:
        message = "Demo data · nothing is saved or sent · " + message[0].lower() + message[1:]
    for item in items:
        item["group"] = GROUPS[item["status"]]
    groups = {key: sum(1 for item in items if item["group"] == key) for key in ("configured", "needs_setup", "available")}
    return _result(ctx, "connectors", True, items=items, actions=actions, categories=categories,
                   summary={"enabled": counts["enabled"], "installed": counts["installed"] + counts["enabled"],
                            "needs_credentials": counts["needs_credentials"], "bridge_needed": counts["bridge_needed"],
                            "total": len(items), "yours": sum(i["configured"] for i in items),
                            "tested": sum(i["test_status"] == "passed" for i in items), **groups},
                   revision=f"{config_rev}.{env_rev}", message=message)


CONNECTOR_ACTIONS = {
    "install": (("template", "name", "url"), ("token", "timeout", "enabled", "authentication")),
    "set_credential": (("template", "variable", "value"), ()),
    "remove_credential": (("template", "variable"), ()),
    "toggle_server": (("template", "name", "enabled"), ()),
    "remove_server": (("template", "name"), ()),
    "replace_token": (("template", "name", "value"), ()),
    "test_connection": (("template", "name"), ()),
}


def _template(values):
    template = TEMPLATES.get(values.get("template"))
    if template is None:
        raise AdminError("Unknown connector. Refresh the page.", 404)
    return template


def _server_name(values):
    name = values.get("name")
    if not isinstance(name, str) or not SERVER_NAME.fullmatch(name.strip()):
        raise AdminError("Server names use lowercase letters, digits, - and _ (at most 48 characters).")
    return name.strip()


def _server_spec(services, name):
    config, revision = services.store.read_yaml("config.yaml")
    servers = config.get("mcp_servers")
    spec = servers.get(name) if isinstance(servers, dict) else None
    if not isinstance(spec, dict):
        raise AdminError("This MCP server no longer exists. Refresh the page.", 404)
    return spec, revision


def _credential_variable(template, values):
    variable = values.get("variable")
    if variable not in WRITABLE.get(template["id"], ()) or not _safe_variable(variable):
        raise AdminError("This credential cannot be managed from the console.")
    return variable


def _demo_suffix(services, message):
    return message + (" (demo only)" if services.demo and not message.endswith("(demo only)") else "")


def connectors_action(ctx, data):
    action, revision, values = validate_action(data)
    spec = CONNECTOR_ACTIONS.get(action)
    if spec is None:
        raise AdminError("Unknown action for this page.")
    _expect(values, *spec)
    template = _template(values)
    services = _services(ctx)
    if action == "install":
        if template["kind"] not in ("remote", "bridge", "custom"):
            raise AdminError("Local servers are installed by the operator on the server. Use the config snippet.")
        forwarded = {key: values.get(key) for key in ("name", "url", "token", "timeout", "enabled")}
        authentication = values.get("authentication", "bearer" if values.get("token") else "none")
        if authentication not in ("none", "bearer"):
            raise AdminError("Choose no authentication or a bearer token. Browser OAuth is not available here.")
        if authentication == "bearer" and not values.get("token"):
            raise AdminError("Enter a bearer token for this authentication method.")
        if authentication == "none" and values.get("token"):
            raise AdminError("Choose bearer token authentication before entering a token.")
        if forwarded["token"] == "":
            forwarded["token"] = None
        result = services.connections.action("mcp", _envelope("add_http_server", revision, forwarded))
        with _CACHE_LOCK:
            services.tests.pop(_test_key(ctx, values["name"]), None)
        return dict(result, message=result["message"].replace("MCP server", template["name"] + " connector", 1)
                    if template["kind"] != "custom" else result["message"])
    if action in ("toggle_server", "remove_server"):
        forwarded = {"name": _server_name(values)}
        if action == "toggle_server":
            forwarded["enabled"] = values["enabled"]
        result = services.connections.action("mcp", _envelope(action, revision, forwarded))
        if action == "remove_server":
            with _CACHE_LOCK:
                services.tests.pop(_test_key(ctx, forwarded["name"]), None)
        return result
    if action in ("set_credential", "remove_credential"):
        variable = _credential_variable(template, values)
        if variable in KEY_NAMES:
            forwarded = {"variable": variable}
            if action == "set_credential":
                forwarded["value"] = values["value"]
            return services.connections.action(
                "keys", _envelope("set_key" if action == "set_credential" else "remove_key", revision, forwarded))
        value = _secret(values["value"]) if action == "set_credential" else None
        new = services.store.mutate_env(revision, {variable: value})
        verb = "saved" if value is not None else "removed from the bot home .env"
        return {"ok": True, "message": _demo_suffix(services, f"{variable} {verb}. " + RESTART), "revision": new,
                "item": {"id": template["id"], "variable": variable, "configured": value is not None}}
    name = _server_name(values)
    spec, config_rev = _server_spec(services, name)
    if action == "replace_token":
        variable = _console_token(name, spec)
        if variable is None:
            raise AdminError("This server's token was not created by the console. Remove the server and install it "
                             "again with a token, or edit config.yaml on the server.", 409)
        new = services.store.mutate_env(revision, {variable: _secret(values["value"])})
        return {"ok": True, "message": _demo_suffix(services, f"Token for {name} replaced. " + RESTART), "revision": new,
                "item": {"id": template["id"], "name": name}}
    # test_connection
    if not isinstance(revision, str) or revision != config_rev:
        raise AdminError("This configuration changed. Refresh before trying again.", 409)
    if not spec.get("url"):
        raise AdminError("Only HTTP servers can be tested from the console.")
    test_env, _ = services.store.read_env()
    fingerprint = _test_fingerprint(spec, test_env, services.environ)
    result = _demo_probe(template) if services.demo else _probe_server(services, spec)
    result = dict(result, at=DEMO_TIME if services.demo else _now())
    with _CACHE_LOCK:
        if len(services.tests) >= 200:
            services.tests.pop(next(iter(services.tests)))
        services.tests[_test_key(ctx, name)] = {"fingerprint": fingerprint, "result": result}
    if result["ok"]:
        shown = ", ".join(result["tools"][:8]) + (", …" if result["count"] > 8 else "")
        message = f"{name} answered: {result['count']} tools" + (f" ({shown})." if shown else ".")
    else:
        message = f"Connection test for {name} failed: {result['error']}"
    return {"ok": True, "message": _demo_suffix(services, message), "item": {"id": template["id"], "name": name},
            "test": result}


def _demo_probe(template):
    tools = {"github": ["get_me", "search_repositories", "list_issues", "get_issue", "list_pull_requests",
                        "get_pull_request", "create_issue", "add_issue_comment"],
             "zalo-oa": ["get_oa_profile", "get_follower_stats", "list_recent_messages", "get_article_stats"]}
    names = tools.get(template["id"], ["list_resources", "search", "fetch"])
    return {"ok": True, "tools": names, "count": len(names), "server": "demo-server", "error": None}


# ---- MCP connection test --------------------------------------------------------

class ProbeError(Exception):
    """A user-facing reason the test failed; never contains secrets."""


def _resolve_headers(spec, env, environ):
    headers = spec.get("headers") or {}
    if not isinstance(headers, dict) or len(headers) > 10:
        raise ProbeError("The configured headers have an unsupported shape.")
    resolved, missing = {}, []
    for key, value in headers.items():
        if not isinstance(key, str) or not HEADER_NAME.fullmatch(key) or not isinstance(value, str):
            raise ProbeError("The configured headers have an unsupported shape.")
        if key.lower() in SKIPPED_HEADERS:
            continue

        def substitute(match):
            found = env.get(match.group(1)) or environ.get(match.group(1))
            if not found:
                missing.append(match.group(1))
                return ""
            return found
        text = ENV_REF.sub(substitute, value)
        if "${" in text or any(c in text for c in "\r\n\0"):
            raise ProbeError("A configured header contains an unsupported value.")
        resolved[key] = text
    if missing:
        raise ProbeError("Set " + ", ".join(sorted(set(missing))) + " first; the header needs it.")
    return resolved


def _read_bounded(response, budget):
    data = response.read(budget[0] + 1)
    budget[0] -= len(data)
    if budget[0] < 0:
        raise ProbeError("The server's response is larger than the test allows.")
    return data


def _read_event_stream(response, expected_id, budget, deadline):
    """Read server-sent events until the JSON-RPC response with *expected_id* arrives."""
    lines = []
    while True:
        if time.monotonic() > deadline:
            raise ProbeError("The server did not answer in time.")
        line = response.readline(min(65536, max(1, budget[0] + 1)))
        budget[0] -= len(line)
        if budget[0] < 0:
            raise ProbeError("The server's response is larger than the test allows.")
        if not line:
            raise ProbeError("The server closed the event stream without an answer.")
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        if text.startswith("data:"):
            lines.append(text[5:].lstrip(" "))
            continue
        if text or not lines:
            continue
        payload, lines = "\n".join(lines), []
        try:
            message = json.loads(payload)
        except ValueError:
            continue
        found = _match_response(message, expected_id)
        if found is not None:
            return found


def _match_response(message, expected_id):
    for item in message if isinstance(message, list) else [message]:
        if isinstance(item, dict) and item.get("id") == expected_id and ("result" in item or "error" in item):
            return item
    return None


def _clean_text(value, limit=120):
    if not isinstance(value, str):
        return None
    text = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    return " ".join(text.split())[:limit] or None


def probe_mcp(url, headers, *, timeout=PROBE_TIMEOUT, deadline_seconds=PROBE_DEADLINE, max_bytes=PROBE_MAX_BYTES):
    """MCP ``initialize`` + ``tools/list`` over Streamable HTTP. Returns ``(server_name, tool_names)``.

    The URL must already be validated (https, or http on loopback). No proxy is used and redirects
    are refused. ``headers`` go only to this URL.
    """
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port
    path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    if parts.scheme == "https":
        def connect():
            return http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context())
    else:
        def connect():
            return http.client.HTTPConnection(host, port, timeout=timeout)
    deadline = time.monotonic() + deadline_seconds
    budget = [max_bytes]
    state = {"session": None, "version": None}

    def request(method, payload=None, expected_id=None):
        if time.monotonic() > deadline:
            raise ProbeError("The server did not answer in time.")
        sent = {"Accept": "application/json, text/event-stream", "User-Agent": "hermes-console-connection-test",
                **headers}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            sent["Content-Type"] = "application/json"
        if state["session"]:
            sent["Mcp-Session-Id"] = state["session"]
        if state["version"]:
            sent["MCP-Protocol-Version"] = state["version"]
        connection = connect()
        try:
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            status = response.status
            if 300 <= status < 400:
                raise ProbeError(f"The server redirected the request (HTTP {status}); redirects are not followed. "
                                 "Install the final URL instead.")
            if status in (401, 403):
                raise ProbeError(f"The server rejected the credentials (HTTP {status}).")
            if expected_id is None:
                if status >= 400 and method != "DELETE":
                    raise ProbeError(f"The server refused the initialized notification (HTTP {status}).")
                return None
            if status == 404 and state["session"]:
                raise ProbeError("The server ended the session early (HTTP 404).")
            if status != 200:
                raise ProbeError(f"The server answered HTTP {status}. Check that the URL is an MCP endpoint "
                                 "that supports Streamable HTTP.")
            session = response.getheader("Mcp-Session-Id")
            if session and not state["session"]:
                if not re.fullmatch(r"[\x21-\x7e]{1,200}", session):
                    raise ProbeError("The server returned an invalid session id.")
                state["session"] = session
            kind = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if kind == "text/event-stream":
                message = _read_event_stream(response, expected_id, budget, deadline)
            elif kind == "application/json":
                try:
                    message = _match_response(json.loads(_read_bounded(response, budget)), expected_id)
                except ValueError:
                    raise ProbeError("The server returned invalid JSON.") from None
            else:
                raise ProbeError("The server did not return MCP JSON or an event stream.")
            if message is None:
                raise ProbeError("The server did not answer the request.")
            if "error" in message:
                error = message["error"] if isinstance(message["error"], dict) else {}
                raise ProbeError("The server returned an error: " + (_clean_text(error.get("message"), 160) or "unknown"))
            return message.get("result") if isinstance(message.get("result"), dict) else {}
        except ProbeError:
            raise
        except ssl.SSLError:
            raise ProbeError("The TLS certificate could not be verified.") from None
        except (socket.timeout, TimeoutError):
            raise ProbeError("The server did not answer in time.") from None
        except (OSError, http.client.HTTPException):
            raise ProbeError("Could not connect to the server.") from None
        finally:
            connection.close()

    initialized = request("POST", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": PROBE_PROTOCOL, "capabilities": {},
        "clientInfo": {"name": "hermes-console", "version": "1.0"}}}, expected_id=1)
    version = initialized.get("protocolVersion")
    state["version"] = version if isinstance(version, str) and re.fullmatch(r"[\w.-]{1,40}", version) else PROBE_PROTOCOL
    info = initialized.get("serverInfo") if isinstance(initialized.get("serverInfo"), dict) else {}
    server = _clean_text(info.get("name"), 80)
    if not isinstance(initialized.get("capabilities"), dict) or "tools" not in initialized["capabilities"]:
        tools = []
    else:
        request("POST", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools, cursor = [], None
        for page in range(PROBE_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = request("POST", {"jsonrpc": "2.0", "id": 2 + page, "method": "tools/list", "params": params},
                             expected_id=2 + page)
            for tool in result.get("tools") or []:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str) and len(tools) < PROBE_TOOL_LIMIT:
                    name = tool["name"] if TOOL_NAME.fullmatch(tool["name"]) else _clean_text(tool["name"], 64)
                    if name:
                        tools.append(name)
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or len(cursor) > 500:
                break
    if state["session"]:
        try:
            request("DELETE")
        except ProbeError:
            pass
    return server, tools


def _probe_server(services, spec):
    if spec.get("auth") == "oauth":
        return {"ok": False, "tools": [], "count": 0, "server": None,
                "error": "OAuth must be authorized and tested with Hermes MCP CLI or its Dashboard. This Console tests bearer/no-auth HTTP servers."}
    try:
        url = _check_url(spec.get("url"), required=True)
    except AdminError as error:
        return {"ok": False, "tools": [], "count": 0, "server": None, "error": str(error)}
    try:
        env, _ = services.store.read_env()
        headers = _resolve_headers(spec, env, services.environ)
        server, tools = probe_mcp(url, headers)
    except ProbeError as error:
        return {"ok": False, "tools": [], "count": 0, "server": None, "error": str(error)}
    return {"ok": True, "tools": tools[:50], "count": len(tools), "server": server, "error": None}


# ---- reports --------------------------------------------------------------------

def _schedule(template, values):
    """The schedule text for a new report: a preset, the template default or the owner's custom schedule."""
    preset = values.get("schedule_preset")
    custom = values.get("schedule")
    if custom is not None and not isinstance(custom, str):
        raise AdminError("Schedule must be text.")
    if preset is None:  # direct API use: the schedule field alone
        preset = "custom"
    presets = dict(SCHEDULE_PRESETS)
    if preset not in presets:
        raise AdminError("Choose one of the schedule options.")
    if preset == "template":
        return template["schedule"]
    if preset == "custom":
        if not custom or not custom.strip():
            raise AdminError("Schedule is required: enter a cron expression, an interval or a phrase.")
        return custom.strip()
    return presets[preset]


def _selection(template, values, enabled):
    """Connector ids picked with ``use.<id>`` checkboxes, or None when the request picked none explicitly."""
    keys = [key for key in values if key.startswith(USE_PREFIX)]
    if not keys:
        return None
    if len(keys) > len(TEMPLATES):
        raise AdminError("Too many connectors.")
    chosen = []
    for key in keys:
        cid, flag = key[len(USE_PREFIX):], values[key]
        if not isinstance(flag, bool):
            raise AdminError("Connector choices must be true or false.")
        if cid not in template["connectors"]:
            raise AdminError("This connector is not used by the report template. Refresh the page.")
        if flag:
            if cid not in enabled:
                raise AdminError(TEMPLATES[cid]["name"] + " is not enabled. Enable it on Connectors first.", 409)
            chosen.append(cid)
    return chosen


def _report_values(template, values, enabled=frozenset()):
    """Validated create_routine values for a report template."""
    account = _single_line(values.get("account"), template.get("account_label", "Account"), 120, required=True)
    focus = _single_line(values.get("focus"), "Focus", 200) or template["focus"]
    language = values.get("language", "vi")
    if language not in dict(LANGUAGES):
        raise AdminError("Choose Vietnamese or English.")
    notes = values.get("notes") or ""
    if not isinstance(notes, str) or len(notes) > 1000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", notes):
        raise AdminError("Notes must be text of at most 1000 characters.")
    if template.get("notes_required") and not notes.strip():
        raise AdminError(template.get("notes_label", "Instructions") + " is required.")
    schedule = _schedule(template, values)
    delivery = values.get("delivery")
    if delivery is not None and not isinstance(delivery, str):
        raise AdminError("Choose where to send the report.")
    paused = values.get("paused", False)
    if not isinstance(paused, bool):
        raise AdminError("Create paused must be true or false.")
    chosen = _selection(template, values, enabled)
    if chosen == [] and template["requires"] == "any" and not paused:
        raise AdminError("Choose at least one enabled connector, or create the report paused.")
    name = (REPORT_PREFIX + template["short"] + " · " + account)[:MAX_JOB_NAME]
    prompt = report_prompt(template, account, focus, language, notes.strip(), connectors=chosen)
    if len(prompt) > MAX_PROMPT:
        raise AdminError("The report instructions are too long; shorten the notes.")
    out = {"name": name, "schedule": schedule, "prompt": prompt, "paused": paused}
    if delivery is not None:
        out["deliver"] = delivery  # validated against the allowlist by AutomationAdmin
    return out


def report_prompt(template, account, focus, language, notes="", connectors=None):
    ids = connectors if connectors else template["connectors"]
    names = [TEMPLATES[cid]["name"] for cid in ids]
    if not connectors and template["id"] == "custom":
        sources = "web search and any enabled connected tools"
    elif template["requires"] == "optional":
        sources = "web search and any connected tools for " + ", ".join(names)
    else:
        sources = "the connected tools for " + ", ".join(names)
    lines = [
        "Hermes report: " + template["id"],
        f"Prepare the \"{template['name']}\" report for: {account}.",
        f"Period: {template['period']}, in this bot's time zone.",
        f"Focus on: {focus}.",
        "Write the whole report in " + ("Vietnamese" if language == "vi" else "English") +
        ", in Markdown, starting with one title line naming the report, the account and the period, "
        "then exactly these sections:",
    ]
    for index, section in enumerate(template["sections"], 1):
        lines.append(f"{index}. {section['vi'] if language == 'vi' else section['en']}: {section['guide']}.")
    lines += [
        "",
        "Rules:",
        f"- Use data from {sources}. If a tool is missing, disabled or fails, say so in the last section and never "
        "invent numbers.",
        "- This is a read-only report. Do not publish, reply, send messages, email anyone or change any data, even "
        "if a tool allows it.",
        "- Keep personal data out: no phone numbers, email addresses or private message text; refer to people by "
        "their public display name only.",
        "- Be concise: short bullets and small tables, under about 500 words.",
    ]
    for rule in template.get("extra_rules", ()):
        lines.append("- " + rule)
    if notes:
        lines += ["", "Owner notes: " + notes]
    return "\n".join(lines)


def _report_template_id(item):
    match = REPORT_MARKER.match(item.get("prompt_preview") or "")
    return match.group(1) if match and match.group(1) in REPORTS else None


def _is_report(item):
    return isinstance(item.get("name"), str) and item["name"].startswith(REPORT_PREFIX)


def _report_items(services, routines):
    items = []
    for item in routines.get("items") or []:
        if not isinstance(item, dict) or not _is_report(item):
            continue
        template_id = _report_template_id(item)
        parts = item["name"][len(REPORT_PREFIX):].split(" · ", 1)
        row = {key: item.get(key) for key in (
            "id", "name", "schedule", "schedule_kind", "enabled", "state", "next_run_at", "next_run_pending",
            "last_run_at", "last_status", "last_error", "failure_streak", "deliver", "deliver_label", "latest_output",
            "runs")}
        files = [f for f in item.get("outputs") or [] if isinstance(f, dict) and isinstance(f.get("name"), str)]
        row.update(template=template_id, template_name=REPORTS[template_id]["name"] if template_id else None,
                   account=parts[1] if len(parts) == 2 else None, outputs=len(files), files=files)
        if services.demo and item.get("id") in services.demo_runs:
            row.update({key: value for key, value in services.demo_runs[item["id"]].items() if value is not None})
            demo_files = services.demo_reports.get(item["id"], [])
            row.update(files=[{"name": r["name"], "at": _file_time(r["name"])} for r in demo_files],
                       outputs=len(demo_files))
        items.append(row)
    return items


def _saved(jobs):
    """Saved report files of every report job, newest first."""
    rows = []
    for job in jobs:
        for entry in job["files"]:
            name = entry["name"]
            if not REPORT_FILE.fullmatch(name) or not isinstance(job.get("id"), str):
                continue
            rows.append({"id": job["id"] + "/" + name, "job": job["id"], "file": name, "at": _file_time(name),
                         "title": job.get("template_name") or job.get("name"), "account": job.get("account"),
                         "template": job.get("template"), "job_name": job.get("name")})
    rows.sort(key=lambda row: (row["file"], row["job"]), reverse=True)
    return rows[:SAVED_LIMIT]


def _title(text):
    for line in text.splitlines()[:20]:
        match = re.match(r"\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            return _clean_text(match.group(1), 160)
    return None


def _reader(services, jobs, job_id, file_name):
    """The full saved report *file_name* of report job *job_id* (bounded, redacted by AutomationAdmin)."""
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id) or not isinstance(file_name, str) \
            or not REPORT_FILE.fullmatch(file_name):
        raise AdminError("Unknown saved report.", 404)
    job = next((j for j in jobs if j["id"] == job_id), None)
    if job is None or not any(f["name"] == file_name for f in job["files"]):
        raise AdminError("This saved report no longer exists. Refresh the page.", 404)
    if services.demo:
        text = next(r["text"] for r in services.demo_reports.get(job_id, []) if r["name"] == file_name)
    else:
        output = services.automation.read_output(job_id, file_name).get("output") or {}
        text = output.get("text") if isinstance(output.get("text"), str) else ""
    return {"job": job_id, "file": file_name, "at": _file_time(file_name), "job_name": job.get("name"),
            "template": job.get("template"), "template_name": job.get("template_name"), "account": job.get("account"),
            "title": _title(text) or job.get("template_name") or job.get("name"),
            "text": text[:READER_MAX_CHARS], "truncated": len(text) > READER_MAX_CHARS, "length": len(text)}


def _readiness(template, statuses):
    connectors = [{"id": cid, "name": TEMPLATES[cid]["name"], "status": statuses.get(cid, "unknown"),
                   "status_label": STATUS_LABELS[statuses.get(cid, "unknown")]} for cid in template["connectors"]]
    enabled = [c for c in connectors if c["status"] == "enabled"]
    if template["requires"] == "optional":
        state = "ready"
        label = "Ready · uses web search" + (" and " + ", ".join(c["name"] for c in enabled) if enabled else "")
    elif enabled:
        state, label = "ready", "Ready · " + ", ".join(c["name"] for c in enabled)
    else:
        state, label = "needs_connector", "Needs an enabled connector"
    return connectors, state, label


def _create_fields(template, connectors, state, targets):
    enabled = [c for c in connectors if c["status"] == "enabled"]
    fields = [
        _hidden("template", template["id"]),
        _field("account", template.get("account_label", "Account or page name"), required=True, max_length=120,
               help=template.get("account_help", "Shown in the report title and job name.")),
    ]
    for c in enabled:
        fields.append(_field(USE_PREFIX + c["id"], "Use " + c["name"], "checkbox", value=True))
    fields += [
        _field("schedule_preset", "Schedule", "select", value="template", required=True,
               options=[{"value": key, "label": PRESET_LABELS.get(key, "Template recommendation")}
                        for key, _ in SCHEDULE_PRESETS]),
        _field("schedule", "Custom schedule", value=template["schedule"], max_length=100,
               help="Used only with Custom: cron (0 8 * * *), interval (every 1d) or phrase (every monday 8am)."),
        _field("delivery", "Send the report to", "select", required=True,
               value=next((t["value"] for t in targets if t["value"] != "local"), "local"),
               options=[{"value": t["value"], "label": t["label"]} for t in targets],
               help="Every report is also saved here. Only your configured Telegram or Zalo home chats are listed."
               if len(targets) > 1 else "Set a Telegram or Zalo home chat on Channels to also get reports in chat."),
        _field("language", "Report language", "select", value="vi", required=True,
               options=[{"value": value, "label": label} for value, label in LANGUAGES]),
        _field("focus", "Focus", value=template["focus"], max_length=200, help="What the report should concentrate on."),
        _field("notes", template.get("notes_label", "Extra instructions"), "textarea", max_length=1000,
               required=bool(template.get("notes_required")) or None,
               help=None if template.get("notes_required") else
               "Optional, for example accounts to compare or topics to ignore."),
        _field("paused", "Create paused", "checkbox", value=state != "ready",
               help="Paused reports do not run until you resume them."),
    ]
    return fields


def _create_description(template, connectors, state):
    missing = [c["name"] for c in connectors if c["status"] != "enabled"]
    parts = [template["description"]]
    if template["id"] != "custom" and missing:
        shown = ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
        parts.append("Not connected yet: " + shown + " (set them up on Connectors).")
    if state != "ready":
        parts.append("No connector it needs is enabled, so it is created paused by default.")
    parts.append("Each run saves a read-only report here and can send it to your own home chat; it never posts or "
                 "messages anyone else.")
    return " ".join(parts)


REPORTS_MESSAGE = "Saved here and optionally sent to your own chat · never posted · edit instructions on Automations."


def reports_snapshot(ctx, query):
    services = _services(ctx)
    routines = services.automation.snapshot("routines")
    if routines.get("available") is False:
        return _result(ctx, "reports", False, message=routines.get("message"), templates=[], saved=[])
    try:
        connectors, _, _, _ = _connector_state(services, ctx, with_actions=False)
        statuses = {item["id"]: item["status"] for item in connectors}
    except AdminError:
        statuses = {}
    revision = routines.get("revision")
    targets = [t for t in services.automation.delivery_targets() if t["value"] != "origin"]
    jobs = _report_items(services, routines)
    ids = {job["id"] for job in jobs}
    actions = [dict(a) for a in routines.get("actions") or []
               if a.get("id") in REPORT_JOB_ACTIONS and a.get("target") in ids]
    templates = []
    for template in catalog.REPORTS:
        needed, state, label = _readiness(template, statuses)
        templates.append({"id": template["id"], "name": template["name"], "description": template["description"],
                          "featured": bool(template.get("featured")), "connectors": needed,
                          "readiness": state, "readiness_label": label,
                          "schedule": template["schedule"], "focus": template["focus"],
                          "sections": [section["en"] for section in template["sections"]],
                          "reports": sum(1 for job in jobs if job["template"] == template["id"])})
        if revision is None:
            continue
        actions.append(_action("create_report", "Create report · " + template["name"], revision,
                               _create_fields(template, needed, state, targets), target=template["id"],
                               description=_create_description(template, needed, state)))
    saved = _saved(jobs)
    reader, reader_error = None, None
    job_id, file_name = query.get("job"), query.get("file")
    if job_id or file_name:
        try:
            reader = _reader(services, jobs, job_id, file_name)
        except AdminError as error:
            reader_error = str(error)
    elif saved:
        try:
            reader = _reader(services, jobs, saved[0]["job"], saved[0]["file"])
        except AdminError as error:
            reader_error = str(error)
    for job in jobs:
        job.pop("files", None)
    message = REPORTS_MESSAGE
    if ctx.demo:
        message = "Demo data · nothing is saved or sent."
    summary = {"scheduled": sum(1 for j in jobs if j.get("enabled") is not False and j.get("state") == "scheduled"),
               "paused": sum(1 for j in jobs if j.get("enabled") is False or j.get("state") == "paused"),
               "failing": sum(1 for j in jobs if j.get("last_error") or j.get("last_status") in ("error", "failed")),
               "saved": sum(j.get("outputs") or 0 for j in jobs)}
    return _result(ctx, "reports", True, items=jobs, actions=actions, templates=templates, revision=revision,
                   scheduler_active=routines.get("scheduler_active"), message=message, total=len(jobs),
                   saved=saved, reader=reader, reader_error=reader_error, summary=summary)


def saved_reports(ctx, limit=5):
    """Newest saved reports (no text) for other console pages, such as the CEO briefing."""
    snap = reports_snapshot(ctx, {})
    return [dict(row) for row in (snap.get("saved") or [])[:max(0, min(int(limit), SAVED_LIMIT))]]


def _report_message(message):
    for prefix in ("Automation " + REPORT_PREFIX, "Automation "):
        if message.startswith(prefix):
            return "Report " + message[len(prefix):]
    return message


CREATE_FIELDS = ("focus", "language", "notes", "paused", "schedule", "schedule_preset", "delivery")


def reports_action(ctx, data):
    action, revision, values = validate_action(data)
    services = _services(ctx)
    if action == "create_report":
        plain = {key: value for key, value in values.items() if not key.startswith(USE_PREFIX)}
        _expect(plain, ("template", "account"), CREATE_FIELDS)
        for key, value in values.items():
            if key.startswith(USE_PREFIX) and not isinstance(value, bool):
                raise AdminError("Connector choices must be true or false.")
        template = REPORTS.get(values.get("template"))
        if template is None:
            raise AdminError("Unknown report template. Refresh the page.", 404)
        try:
            connectors, _, _, _ = _connector_state(services, ctx, with_actions=False)
            enabled = frozenset(item["id"] for item in connectors if item["status"] == "enabled")
        except AdminError:
            enabled = frozenset()
        result = services.automation.action("routines", _envelope("create_routine", revision,
                                                                  _report_values(template, values, enabled)))
        return dict(result, message=_report_message(result["message"]))
    if action in REPORT_JOB_ACTIONS:
        _expect(values, ("id",))
        routines = services.automation.snapshot("routines")
        if not any(_is_report(item) and item.get("id") == values["id"] for item in routines.get("items") or []):
            raise AdminError("This report no longer exists. Refresh the page.", 404)
        result = services.automation.action("routines", _envelope(action, revision, {"id": values["id"]}))
        if services.demo and action == "delete_routine":
            services.demo_reports.pop(values["id"], None)
            services.demo_runs.pop(values["id"], None)
        return dict(result, message=_report_message(result["message"]))
    raise AdminError("Unknown action for this page.")


register(Section("connectors", connectors_snapshot, connectors_action))
register(Section("reports", reports_snapshot, reports_action, query_keys=frozenset({"job", "file"})))
