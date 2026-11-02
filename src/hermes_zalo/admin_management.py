"""Bounded, read-only management metadata for one explicit Hermes home.

The caller supplies the home and authorizes every request. This module never
imports plugins, executes commands, contacts services, modifies configuration,
or falls back to the operator's personal home. Configuration secrets, MCP
arguments and pairing codes are deliberately absent from its result schemas.
"""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import stat

import yaml

from .admin_assistant import redact


SECTIONS = frozenset(("models", "logs", "plugins", "mcp", "channels", "webhooks",
                      "pairing", "profiles", "config", "keys", "documentation", "chat"))
ROW_LIMIT = 100
READ_LIMIT = 1024 * 1024
LOG_LIMIT = 64 * 1024
SCAN_LIMIT = 300
DEMO_TIME = "2026-10-31T08:00:00+00:00"
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOG_FILES = ("gateway.log", "agent.log", "errors.log", "hermes.log")
TIMESTAMP = re.compile(r"\A\[?(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:[.,](\d{1,6}))?")
UNAVAILABLE = "The configured data source is unavailable or its schema is unsupported."
KEYS = (
    ("OpenAI", "OPENAI_API_KEY"), ("Anthropic", "ANTHROPIC_API_KEY"),
    ("OpenRouter", "OPENROUTER_API_KEY"), ("Google", "GEMINI_API_KEY"),
    ("Google", "GOOGLE_API_KEY"), ("xAI", "XAI_API_KEY"),
    ("DeepSeek", "DEEPSEEK_API_KEY"), ("Nous", "NOUS_API_KEY"),
    ("Gateway API", "API_SERVER_KEY"), ("Console chat", "HERMES_CONSOLE_CHAT_API_KEY"),
    ("Telegram", "TELEGRAM_BOT_TOKEN"), ("Zalo", "ZALO_BOT_TOKEN"),
)
CHANNEL_KEYS = {"telegram": "TELEGRAM_BOT_TOKEN", "zalo": "ZALO_BOT_TOKEN",
                "discord": "DISCORD_BOT_TOKEN", "slack": "SLACK_BOT_TOKEN",
                "api_server": "API_SERVER_KEY"}
SAFE_CONFIG = {
    "model": ("default", "model", "provider", "max_tokens", "temperature"),
    "terminal": ("backend", "timeout", "lifetime_seconds", "container_persistent",
                 "docker_mount_cwd_to_workspace"),
    "code_execution": ("timeout", "max_tool_calls"),
    "memory": ("memory_enabled", "user_profile_enabled", "write_approval"),
    "skills": ("write_approval",),
    "gateway": ("trust_env", "signal_interrupt_grace_timeout"),
}
DOCS = (
    ("Agent Console", "Local guide", "docs/admin.md", "Create a console account, start the server, and use an SSH tunnel."),
    ("Start the bot", "Local command", "scripts/run.sh Start", "Start the configured messaging gateway in the foreground."),
    ("Choose a model", "Local command", "scripts/run.sh Model", "Select a provider and model using the existing setup flow."),
    ("Runtime status", "Local command", "scripts/run.sh Status", "Inspect the selected Hermes installation and process status."),
    ("Hermes dashboard", "Official documentation", "https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard", "Upstream management features and authentication."),
    ("Hermes source", "Official repository", "https://github.com/NousResearch/hermes-agent", "Version-specific schemas and runtime documentation."),
)


class _Missing(ValueError):
    pass


def _identifier(value):
    return value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else None


def _scalar(value, maximum=160):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(value) else None
    return redact(value, maximum) if isinstance(value, str) else None


def _safe_log(line):
    # Logs can carry arbitrary provider exceptions; remove common forms beyond
    # the metadata redactor, including tokens shorter than its entropy threshold.
    line = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [secret]", line)
    line = re.sub(r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization|cookie)[\"']?\s*[:=]\s*)(?:[\"'][^\"']*[\"']|[^\s,;]+)",
                  r"\1[secret]", line)
    line = re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{10,}\b", "[secret]", line)
    return redact(line, 600)


class ManagementAdmin:
    def __init__(self, home, *, mode="local", environ=None):
        if home is None:
            raise ValueError("An explicit Hermes home is required.")
        self.home = Path(home)
        self.mode = mode
        self.environ = os.environ if environ is None else environ

    def snapshot(self, section, query=None):
        if section not in SECTIONS:
            raise ValueError("Unknown management section.")
        query = self._query(section, query)
        if self.mode == "demo":
            return self._demo(section, query)
        try:
            return getattr(self, "_" + section)(query)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError, yaml.YAMLError):
            # ValueError covers _Missing, JSON and Unicode errors. Unexpected
            # shapes in operator files become an honest "unavailable", never a 500.
            return self._result(section, False, message=UNAVAILABLE)

    @staticmethod
    def _query(section, query):
        if query is None:
            return {}
        if not isinstance(query, dict) or set(query) - ({"q", "level", "file", "component", "lines"} if section == "logs" else set()):
            raise ValueError("Unsupported management filters.")
        if any(not isinstance(value, str) or len(value) > 100 for value in query.values()):
            raise ValueError("Filter values must be strings of at most 100 characters.")
        level = query.get("level", "").upper()
        if level and level not in LEVELS:
            raise ValueError("Unsupported log level.")
        if query.get('file', '') not in ('', 'all', 'agent', 'gateway', 'errors', 'hermes'):
            raise ValueError('Unsupported log file.')
        if query.get('component', '') not in ('', 'all', 'gateway', 'agent', 'tools', 'cli', 'cron'):
            raise ValueError('Unsupported log component.')
        if query.get('lines', '') not in ('', '50', '100', '200', '500'):
            raise ValueError('Unsupported log line limit.')
        return {**query, "level": level} if section == "logs" else query

    def _result(self, section, available, *, items=None, message=None, source="hermes-home",
                truncated=False, item_limit=ROW_LIMIT, **extra):
        items = items or []
        return {"section": section, "available": available, "source": source,
                "message": message, "items": items[:item_limit],
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "truncated": truncated or len(items) > item_limit, **extra}

    def _path(self, relative):
        parts = Path(relative).parts
        if Path(relative).is_absolute() or any(part in ("..", ".") for part in parts):
            raise _Missing()
        path = self.home
        if path.is_symlink():
            raise _Missing()
        for part in parts:
            path /= part
            if path.is_symlink():
                raise _Missing()
        return path

    def _read(self, relative, *, tail=False):
        path = self._path(relative)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise _Missing()
            if tail:
                stream.seek(max(0, info.st_size - LOG_LIMIT))
                raw = stream.read(LOG_LIMIT)
                if info.st_size > LOG_LIMIT:
                    raw = raw.split(b"\n", 1)[-1]
            else:
                raw = stream.read(READ_LIMIT + 1)
                if len(raw) > READ_LIMIT:
                    raise _Missing()
        return raw.decode("utf-8-sig", "replace")

    def _yaml(self, relative):
        data = yaml.safe_load(self._read(relative))
        if not isinstance(data, dict):
            raise _Missing()
        return data

    def _config_data(self):
        try:
            return self._yaml("config.yaml")
        except (OSError, _Missing, yaml.YAMLError):
            return None

    def _children(self, relative):
        path = self._path(relative)
        # Collect at most SCAN_LIMIT entries before sorting, even for huge homes.
        entries = []
        with os.scandir(path) as iterator:
            for scanned, entry in enumerate(iterator):
                if scanned >= SCAN_LIMIT:
                    break
                if not entry.name.startswith(".") and not entry.is_symlink():
                    entries.append(entry)
        return sorted(entries, key=lambda item: item.name)

    def _models(self, _):
        config = self._config_data()
        if config is None:
            return self._result("models", False, message="No readable model configuration is available.")
        items = []
        main = config.get("model")
        if isinstance(main, str):
            items.append({"role": "Main", "model": _scalar(main), "provider": "auto"})
        elif isinstance(main, dict):
            name = main.get("default") or main.get("model")
            if isinstance(name, str):
                items.append({"role": "Main", "model": _scalar(name), "provider": _scalar(main.get("provider")) or "auto"})
        auxiliary = config.get("auxiliary")
        if isinstance(auxiliary, dict):
            for role, value in list(auxiliary.items())[:ROW_LIMIT]:
                if not _identifier(role):
                    continue
                model = value.get("model") if isinstance(value, dict) else value
                if isinstance(model, str):
                    items.append({"role": _scalar(role), "model": _scalar(model),
                                  "provider": _scalar(value.get("provider")) if isinstance(value, dict) else None})
        return self._result("models", bool(items), items=items,
                            message="Configured models only. This view does not probe providers or change the running gateway.")

    def _logs(self, query):
        found, rows, truncated = False, [], False
        limit = int(query.get('lines') or ROW_LIMIT)
        # Fixed filenames only. Do not accept a caller-supplied path or read a
        # parent directory outside this home, even if the repo stores logs there.
        for order, filename in enumerate(LOG_FILES):
            if query.get('file') not in (None, '', 'all', filename.removesuffix('.log')):
                continue
            try:
                text = self._read("logs/" + filename, tail=True)
            except (OSError, _Missing):
                continue
            found = True
            stamp, matched, inherited_level = "", [], 'UNKNOWN'
            for index, raw in enumerate(text.splitlines()):
                parsed = TIMESTAMP.match(raw)
                if parsed:
                    # Continuation lines (tracebacks) inherit the entry's time.
                    stamp = f"{parsed[1]} {parsed[2]}.{(parsed[3] or '').ljust(6, '0')}"
                detected = next((name for name in reversed(LEVELS) if re.search(r"\b" + name + r"\b", raw)), None)
                if detected:
                    inherited_level = detected
                level = detected or inherited_level
                if query.get("level") and level != query["level"]:
                    continue
                safe = _safe_log(raw)
                # Search the redacted text only, so a filter cannot probe secrets.
                if not safe or query.get("q", "").casefold() not in safe.casefold():
                    continue
                component = query.get('component')
                if component not in (None, '', 'all') and component not in (safe + ' ' + filename).casefold():
                    continue
                matched.append(((stamp, order, index), {"file": filename, "level": level, "line": safe}))
            truncated = truncated or len(matched) > limit
            rows.extend(matched[-limit:])
        rows.sort(key=lambda row: row[0])
        truncated = truncated or len(rows) > limit
        items = [item for _, item in rows[-limit:]][::-1]
        return self._result("logs", found, items=items, truncated=truncated,
                            message="Latest bounded log lines, newest first by their timestamps. Content and secrets are redacted; this is not a live gateway heartbeat.",
                            item_limit=limit, summary={"limit": limit, "read_only": True})

    def _plugins(self, _):
        try:
            children = self._children("plugins")
        except (OSError, _Missing):
            return self._result("plugins", False, message="No local plugin directory is available.")
        config = self._config_data() or {}
        settings = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
        enabled = settings.get("enabled")
        disabled = settings.get("disabled") if isinstance(settings.get("disabled"), list) else []
        items = []
        for entry in children:
            if not entry.is_dir(follow_symlinks=False) or not _identifier(entry.name):
                continue
            try:
                manifest = self._yaml(f"plugins/{entry.name}/plugin.yaml")
            except (OSError, _Missing, yaml.YAMLError):
                continue
            declared = manifest.get("name")
            name = _scalar(declared, 100) if isinstance(declared, str) and declared.strip() else entry.name
            state = "disabled" if name in disabled else "enabled" if isinstance(enabled, list) and name in enabled else "installed"
            hooks = manifest.get("provides_hooks")
            items.append({"name": name, "version": _scalar(manifest.get("version")),
                          "description": _scalar(manifest.get("description"), 240), "status": state,
                          "hooks": len(hooks) if isinstance(hooks, list) else 0})
        return self._result("plugins", True, items=items,
                            message="Local plugin manifests only. Installed does not imply loaded; no plugin code is imported or executed.")

    def _mcp(self, _):
        config = self._config_data()
        servers = config.get("mcp_servers") if config else None
        if not isinstance(servers, dict):
            return self._result("mcp", False, message="No MCP server configuration is available.")
        items = []
        for name, spec in list(servers.items())[:ROW_LIMIT]:
            if not _identifier(name) or not isinstance(spec, dict):
                continue
            transport = spec.get("transport")
            kind = transport if transport in ("stdio", "sse", "http", "streamable-http") else "remote" if spec.get("url") else "stdio" if spec.get("command") else "unknown"
            items.append({"name": _scalar(name), "transport": kind, "enabled": spec.get("enabled") is not False,
                          "credentials_configured": bool(spec.get("env") or spec.get("headers") or spec.get("auth")),
                          "status": "configured"})
        return self._result("mcp", True, items=items,
                            message="Configuration metadata only. Commands, arguments, endpoints and environment values are hidden; connectivity has not been tested.")

    def _channels(self, _):
        config = self._config_data() or {}
        platforms = config.get("platforms") if isinstance(config.get("platforms"), dict) else {}
        names = set(CHANNEL_KEYS) | {name for name in list(platforms)[:ROW_LIMIT] if _identifier(name)}
        items = []
        for name in sorted(names):
            spec = platforms.get(name)
            spec = spec if isinstance(spec, dict) else {}
            credential = CHANNEL_KEYS.get(name)
            present = bool(credential and self.environ.get(credential))
            if name not in platforms and not present:
                continue
            enabled = spec.get("enabled") if isinstance(spec.get("enabled"), bool) else None
            items.append({"channel": _scalar(name), "enabled": enabled, "credential_configured": present,
                          "status": "configured", "connection": "not verified"})
        return self._result("channels", bool(items), items=items,
                            message="Configured channels only. A configured credential is not proof of a connected or healthy gateway.")

    def _webhooks(self, _):
        try:
            data = json.loads(self._read("webhook_subscriptions.json"))
        except (OSError, _Missing):
            return self._result("webhooks", False, message="No webhook subscription store is available.")
        if not isinstance(data, dict):
            raise _Missing()
        items = []
        for name, spec in list(data.items())[:ROW_LIMIT]:
            if not _identifier(name) or not isinstance(spec, dict):
                continue
            events = spec.get("events")
            items.append({"name": _scalar(name), "enabled": spec.get("enabled") is not False,
                          "events": len(events) if isinstance(events, list) else 0,
                          "secret_configured": bool(spec.get("secret")),
                          "created_at": _scalar(spec.get("created_at"))})
        return self._result("webhooks", True, items=items,
                            message="Subscription metadata only. Secrets, URLs, delivery targets, prompts and scripts are hidden.")

    def _pairing(self, _):
        items, found = [], False
        seen = set()
        for directory in ("platforms/pairing", "pairing"):
            try:
                entries = self._children(directory)
            except (OSError, _Missing):
                continue
            for entry in entries:
                match = re.fullmatch(r"([a-z][a-z0-9_]{0,40})-(pending|approved)\.json", entry.name)
                if not match or not entry.is_file(follow_symlinks=False) or match.groups() in seen:
                    continue
                try:
                    data = json.loads(self._read(directory + "/" + entry.name))
                except (OSError, ValueError):
                    continue  # one unreadable store must not hide the others
                if not isinstance(data, dict) or any(not isinstance(value, dict) for value in data.values()):
                    continue
                found = True
                seen.add(match.groups())
                platform, status = match.groups()
                items.append({"channel": platform, "status": status, "count": len(data)})
        return self._result("pairing", found, items=items,
                            message="Per-channel request and approval counts only. Pairing codes, request identifiers and user details are not exposed.")

    def _profiles(self, _):
        try:
            entries = self._children("profiles")
        except (OSError, _Missing):
            return self._result("profiles", False, message="No profile directory exists inside this Hermes home. Other homes are not scanned.")
        items = []
        for entry in entries:
            if not _identifier(entry.name) or not entry.is_dir(follow_symlinks=False):
                continue
            try:
                spec = self._yaml(f"profiles/{entry.name}/config.yaml")
                model = spec.get("model")
                main = model.get("default") or model.get("model") if isinstance(model, dict) else model
            except (OSError, _Missing, yaml.YAMLError):
                main = None
            items.append({"name": _scalar(entry.name), "model": _scalar(main), "status": "local profile", "switching": "not enabled"})
        return self._result("profiles", True, items=items,
                            message="Profiles inside the selected home only. This console does not switch or modify profiles.")

    def _config(self, _):
        config = self._config_data()
        if config is None:
            return self._result("config", False, message="No readable configuration is available.")
        items = []
        for section, allowed in SAFE_CONFIG.items():
            value = config.get(section)
            if not isinstance(value, dict):
                continue
            for key in allowed:
                if key in value and isinstance(value[key], (str, int, float, bool)):
                    items.append({"section": section, "setting": key, "value": _scalar(value[key])})
        toolsets = config.get("platform_toolsets")
        if isinstance(toolsets, dict):
            for name, tools in list(toolsets.items())[:ROW_LIMIT]:
                if _identifier(name) and isinstance(tools, list):
                    tool_names = [_identifier(tool) for tool in tools[:40]]
                    items.append({"section": "platform_toolsets", "setting": name,
                                  "value": ", ".join(tool for tool in tool_names if tool)})
        return self._result("config", True, items=items,
                            message="Allowlisted settings only. Secrets, system prompts, commands, endpoint URLs and unknown settings are omitted. Changes remain in the existing configuration workflow.")

    def _keys(self, _):
        items = [{"provider": provider, "variable": name, "configured": bool(self.environ.get(name))}
                 for provider, name in KEYS]
        return self._result("keys", True, source="credential-presence", items=items,
                            message="Presence checks only. Values are never returned; configure credentials through the existing server-side setup.")

    def _documentation(self, _):
        return self._result("documentation", True, source="documentation", items=[
            {"name": name, "kind": kind, "reference": reference, "description": description}
            for name, kind, reference, description in DOCS],
            message="Local commands use this repository. Upstream documentation may describe a newer Hermes version.")

    def _chat(self, _):
        configured = bool(self.environ.get("HERMES_CONSOLE_CHAT_API_URL"))
        return self._result("chat", configured, source="chat-configuration", items=[
            {"connection": "Hermes Gateway API", "configured": configured, "status": "not probed"}],
            configured=configured,
            message="Chat requests use the explicitly configured Gateway API connection." if configured else
            "Configure HERMES_CONSOLE_CHAT_API_URL and its API credential on the server to enable console chat.")

    def _demo(self, section, query):
        sample = {
            "models": [{"role": "Main", "model": "demo-reasoning", "provider": "demo"},
                       {"role": "Vision", "model": "demo-vision", "provider": "demo"}],
            "logs": [{"file": "gateway.log", "level": "INFO", "line": "Demo gateway connected to a synthetic channel."},
                     {"file": "gateway.log", "level": "WARNING", "line": "Demo job is waiting for review."}],
            "plugins": [{"name": "assistant-core", "version": "0.1.0", "description": "Shared assistant policy and telemetry.", "status": "installed", "hooks": 6}],
            "mcp": [{"name": "demo-documents", "transport": "http", "enabled": True, "credentials_configured": True, "status": "configured"}],
            "channels": [{"channel": "telegram", "enabled": True, "credential_configured": True, "status": "configured", "connection": "synthetic demo"}],
            "webhooks": [{"name": "demo-research-update", "enabled": False, "events": 1, "secret_configured": True, "created_at": DEMO_TIME}],
            "pairing": [{"channel": "telegram", "status": "approved", "count": 2}],
            "profiles": [{"name": "demo-research", "model": "demo-reasoning", "status": "local profile", "switching": "not enabled"}],
            "config": [{"section": "terminal", "setting": "backend", "value": "docker"},
                       {"section": "memory", "setting": "memory_enabled", "value": False}],
            "keys": [{"provider": "Demo provider", "variable": "DEMO_API_KEY", "configured": True}],
            "documentation": [{"name": name, "kind": kind, "reference": ref, "description": desc} for name, kind, ref, desc in DOCS],
            "chat": [{"connection": "Hermes Gateway API", "configured": False, "status": "demo; no outbound calls"}],
        }
        items = sample[section]
        if section == "logs":
            items = [item for item in items if (not query.get("level") or item["level"] == query["level"])
                     and query.get("q", "").casefold() in item["line"].casefold()
                     and query.get('file', '') in ('', 'all', item['file'].removesuffix('.log'))
                     and (query.get('component', '') in ('', 'all') or query['component'] in (item['line'] + ' ' + item['file']).casefold())]
        extra = {"chat": {"configured": False}, "logs": {"summary": {"limit": ROW_LIMIT, "read_only": True}}}
        return {"section": section, "available": section != "chat", "source": "demo", "items": items,
                "message": "Synthetic demo metadata. No real files, credentials, services or model calls are used.",
                "updated_at": DEMO_TIME, "truncated": False, **extra.get(section, {})}
