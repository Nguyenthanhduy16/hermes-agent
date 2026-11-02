"""Console management for models, settings, credentials, channels and MCP servers.

``ConnectionsAdmin(store)`` works only through the root ``ControlStore`` bound to
one explicit bot home: ``config.yaml`` and that home's ``.env``. It never runs
commands, contacts providers or MCP servers, mutates ``os.environ`` or returns a
credential value. Every write is a revision-checked edit that preserves all
settings it does not own.

Schemas follow the installed Hermes release:
- ``model.default`` / ``model.provider`` / ``model.base_url`` for the main model;
  ``auxiliary.<task>.provider`` / ``.model`` for side tasks ("auto" = main model).
- Platform blocks may live in ``gateway.platforms.<p>``, ``platforms.<p>`` or the
  ``gateway.<p>`` shorthand (later wins). An explicit ``enabled`` beats an env token.
- ``mcp_servers.<name>`` with ``url``/``headers``/``timeout``/``enabled``; headers
  may reference ``${VAR}``, which Hermes resolves from the home ``.env``.
- Hermes loads the home ``.env`` over inherited process variables at start, so
  saved values apply after the gateway restarts.
"""
from datetime import datetime, timezone
import ipaddress
import math
import os
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .admin_auth import AdminError


SECTIONS = ("models", "config", "keys", "channels", "mcp")
DEMO_TIME = "2026-10-31T08:00:00+00:00"
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,159}")
REVISION = re.compile(r"[a-f0-9]{64}")
RESTART = "Saved for the selected bot home. The running gateway keeps its current settings until it is restarted."
PROVIDERS = ("auto", "openrouter", "nous", "nous-api", "anthropic", "openai-codex", "copilot", "gemini", "zai",
             "kimi-coding", "minimax", "minimax-cn", "huggingface", "nvidia", "xiaomi", "arcee", "ollama-cloud",
             "deepinfra", "kilocode", "ai-gateway", "azure-foundry", "lmstudio", "custom")
AUX_ROLES = (
    ("vision", "Vision"), ("compression", "Context compression"), ("title_generation", "Title generation"),
    ("approval", "Command approval"), ("mcp", "MCP helper"), ("skills_hub", "Skills hub"),
    ("memory_query_rewrite", "Memory search"), ("curator", "Curator"), ("background_review", "Background review"),
)
KEYS = (
    ("OPENROUTER_API_KEY", "OpenRouter", "provider"), ("OPENAI_API_KEY", "OpenAI", "provider"),
    ("ANTHROPIC_API_KEY", "Anthropic", "provider"), ("GEMINI_API_KEY", "Google Gemini", "provider"),
    ("GOOGLE_API_KEY", "Google AI Studio", "provider"), ("XAI_API_KEY", "xAI", "provider"),
    ("DEEPSEEK_API_KEY", "DeepSeek", "provider"), ("NOUS_API_KEY", "Nous Portal", "provider"),
    ("GLM_API_KEY", "Z.ai GLM", "provider"), ("KIMI_API_KEY", "Kimi / Moonshot", "provider"),
    ("HF_TOKEN", "Hugging Face", "provider"), ("NVIDIA_API_KEY", "NVIDIA", "provider"),
    ("TELEGRAM_BOT_TOKEN", "Telegram bot", "channel"), ("DISCORD_BOT_TOKEN", "Discord bot", "channel"),
    ("SLACK_BOT_TOKEN", "Slack bot", "channel"), ("SLACK_APP_TOKEN", "Slack app", "channel"),
    ("FIRECRAWL_API_KEY", "Firecrawl", "tool"), ("EXA_API_KEY", "Exa", "tool"),
    ("TAVILY_API_KEY", "Tavily", "tool"), ("GITHUB_TOKEN", "GitHub", "tool"),
)
KEY_NAMES = {name for name, _, _ in KEYS}
# Zalo is this repository's plugin platform; its token lives in the repository .env.
READ_ONLY_KEYS = (("ZALO_BOT_TOKEN", "Zalo bot", "channel"),)
CHANNELS = (("telegram", "Telegram", "TELEGRAM_BOT_TOKEN"), ("discord", "Discord", "DISCORD_BOT_TOKEN"),
            ("slack", "Slack", "SLACK_BOT_TOKEN"))
REPLY_MODES = ("off", "first", "all")


def _setting(id_, section, key, label, kind, help_, **rule):
    return {"id": id_, "path": (section, key), "label": label, "type": kind, "help": help_, **rule}


SETTINGS = (
    _setting("reasoning_effort", "agent", "reasoning_effort", "Reasoning effort", "select",
             "How much the model thinks before answering. Empty uses the provider default.",
             options=("", "minimal", "low", "medium", "high", "xhigh", "max"), default=""),
    _setting("max_turns", "agent", "max_turns", "Tool-call budget per message", "number",
             "Maximum agent iterations for one message (Hermes default 500).", minimum=5, maximum=500, default=None),
    _setting("gateway_timeout", "agent", "gateway_timeout", "Message time limit (seconds)", "number",
             "Maximum run time of one chat message. HERMES_AGENT_TIMEOUT in the environment overrides it.",
             minimum=60, maximum=7200, default=1800),
    _setting("terminal_timeout", "terminal", "timeout", "Command timeout (seconds)", "number",
             "Maximum run time of one sandboxed command.", minimum=10, maximum=3600, default=180),
    # "off" (never ask) is not offered: it removes a guard for multi-user bots.
    _setting("approvals_mode", "approvals", "mode", "Command approval mode", "select",
             "manual asks every time; smart lets a model approve low-risk commands.",
             options=("manual", "smart"), default="smart"),
    _setting("compression_enabled", "compression", "enabled", "Context compression", "checkbox",
             "Summarize long conversations instead of failing at the context limit.", default=True),
    _setting("timezone", None, "timezone", "Time zone", "text",
             "IANA name such as Asia/Ho_Chi_Minh. Empty uses the server time zone.", max_length=64, default=""),
)
# Shown but never editable here: they protect isolation between users of a
# shared bot (assistant-core and the Zalo adapter refuse to start otherwise).
LOCKED = (
    ("memory", "memory_enabled", "Long-term memory", False,
     "Locked: one memory store is shared by every user of this home, so it must stay off."),
    ("memory", "user_profile_enabled", "User profile memory", False,
     "Locked: profiles would be shared across users of this home, so it must stay off."),
    ("terminal", "backend", "Execution sandbox", "local",
     "Locked: changing the sandbox affects host isolation. Use the setup scripts."),
    ("platform_toolsets", None, "Channel toolsets", None,
     "Locked: toolsets decide what chat users may run. Use the setup scripts."),
)
SETTING_IDS = {item["id"]: item for item in SETTINGS}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value, limit=160):
    return value[:limit] if isinstance(value, str) else None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _slot(parent, key):
    """Child mapping of *parent*, created when absent; refuses to replace a non-mapping."""
    value = parent.get(key)
    if value is None:
        value = parent[key] = {}
    if not isinstance(value, dict):
        raise AdminError(f"The configuration key {key} has an unsupported shape. Repair config.yaml first.", 409)
    return value


def _field(name, label, kind, **extra):
    return {"name": name, "label": label, "type": kind, **{k: v for k, v in extra.items() if v is not None}}


def _hidden(name, value):
    return {"name": name, "label": name.replace("_", " ").capitalize(), "type": "hidden", "value": value}


def _options(values, labels=None):
    return [{"value": value, "label": (labels or {}).get(value, value)} for value in values]


def _safe_url(value):
    """Display form of a URL, or None when it embeds credentials or a query."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.username or parts.password or parts.query or parts.fragment or "${" in value:
        return None
    return value[:300]


def _host(value):
    try:
        return urlsplit(value).hostname if isinstance(value, str) else None
    except ValueError:
        return None


def _check_url(value, *, required):
    if value in (None, ""):
        if required:
            raise AdminError("A URL is required.")
        return None
    if not isinstance(value, str) or len(value) > 500 or any(c.isspace() or ord(c) < 32 for c in value):
        raise AdminError("Enter a single URL of at most 500 characters.")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise AdminError("The URL is invalid.") from None
    host = parts.hostname or ""
    if parts.scheme not in ("https", "http") or not host or port == 0:
        raise AdminError("Use an http(s) URL with a host name.")
    if parts.username or parts.password:
        raise AdminError("Do not put credentials in the URL. Use the separate secret field.")
    if parts.fragment or "${" in value:
        raise AdminError("The URL must not contain fragments or variable references.")
    if parts.scheme == "http":
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise AdminError("Plain http is only allowed for localhost. Use https for remote servers.")
    return value


def _secret(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(c.isspace() or ord(c) < 32 for c in value):
        raise AdminError("Enter the secret as one line without spaces (at most 4096 characters).")
    return value


def _bool(value):
    if isinstance(value, bool):
        return value
    if value in ("true", "on", "1"):
        return True
    if value in ("false", "off", "0", "", None):
        return False
    raise AdminError("A checkbox value must be true or false.")


def _choice(value, options, label):
    if value not in options:
        raise AdminError(f"Choose a supported {label}.")
    return value


def _model_name(value, *, required):
    value = value.strip() if isinstance(value, str) else value
    if value in (None, ""):
        if required:
            raise AdminError("A model name is required.")
        return ""
    if not isinstance(value, str) or not MODEL.fullmatch(value):
        raise AdminError("Model names use letters, digits and . _ : / @ + - (at most 160 characters).")
    return value


def _main_model(config):
    model = config.get("model")
    if isinstance(model, str):
        return {"default": model}
    return _dict(model)


def _platform_blocks(config, name):
    """Platform blocks lowest-precedence first, as the gateway merges them."""
    gateway = _dict(config.get("gateway"))
    return [(path, block) for path, block in (
        (("gateway", "platforms", name), _dict(gateway.get("platforms")).get(name)),
        (("platforms", name), _dict(config.get("platforms")).get(name)),
        (("gateway", name), gateway.get(name)),
    ) if isinstance(block, dict)]


def _platform(config, name):
    merged = {}
    for _, block in _platform_blocks(config, name):
        merged.update(block)
    return merged


def _mcp_exposure(config, name, environ, env):
    """Messaging platforms that would receive tools from MCP server *name* if it were enabled.

    Mirrors hermes_cli.tools_config._merge_mcp_servers: a platform gets every enabled
    server unless its explicit toolset list contains ``no_mcp`` or names MCP servers
    (an allowlist). ``cli`` is the operator's own terminal and is not a chat channel.
    """
    toolsets = _dict(config.get("platform_toolsets"))
    servers = _dict(config.get("mcp_servers"))
    platforms = {key for key in toolsets if isinstance(key, str) and key != "cli"}
    for channel, _, token in CHANNELS:
        explicit = _platform(config, channel).get("enabled")
        if explicit is True or explicit is not False and (env.get(token) or environ.get(token)):
            platforms.add(channel)
    plugins = _dict(config.get("plugins")).get("enabled")
    if isinstance(plugins, list) and "zalo-platform" in plugins:
        platforms.add("zalo")
    exposed = []
    for platform in sorted(platforms):
        listed = toolsets.get(platform)
        if isinstance(listed, list):
            if "no_mcp" in listed:
                continue
            allowlist = {item for item in listed if isinstance(item, str) and item in servers and item != name}
            if allowlist and name not in listed:
                continue
        exposed.append(platform)
    return exposed


def _mcp_token_var(name):
    return "MCP_" + re.sub(r"[^A-Z0-9]", "_", name.upper()) + "_TOKEN"


class ConnectionsAdmin:
    def __init__(self, store, *, environ=None):
        self.store = store
        self.demo = getattr(store, "mode", "local") == "demo"
        # The demo never reflects the server's real environment.
        self.environ = {} if self.demo else (os.environ if environ is None else environ)
        self._seeded = False

    # ---- public -----------------------------------------------------------

    def snapshot(self, section):
        if section not in SECTIONS:
            raise AdminError("Unknown connections section.", 404)
        self._seed_demo()
        try:
            config, config_rev = self.store.read_yaml("config.yaml")
            env, env_rev = self.store.read_env()
        except AdminError as exc:
            return self._result(section, False, message=str(exc))
        return getattr(self, "_snap_" + section)(config, config_rev, env, env_rev)

    def action(self, section, data, *, authorize=None):
        if section not in SECTIONS:
            raise AdminError("Unknown connections section.", 404)
        if not isinstance(data, dict) or set(data) != {"action", "expected_revision", "confirm", "values"}:
            raise AdminError("The request must contain action, expected_revision, confirm and values.")
        if data["confirm"] is not True:
            raise AdminError("Confirm the change before saving.")
        values = data["values"]
        if not isinstance(values, dict) or any(not isinstance(key, str) for key in values):
            raise AdminError("Action values must be an object.")
        if any(not isinstance(value, (str, bool, int, float)) or isinstance(value, float) and not math.isfinite(value)
               for value in values.values()):
            raise AdminError("Action values must be text, numbers or booleans.")
        handler = getattr(self, f"_do_{section}_{data['action']}", None) if isinstance(data["action"], str) else None
        if handler is None or not re.fullmatch(r"[a-z_]{1,40}", data["action"]):
            raise AdminError("Unknown action for this page.")
        revision = data["expected_revision"]
        if not isinstance(revision, str) or not all(REVISION.fullmatch(part) for part in revision.split(".")):
            raise AdminError("A valid data revision is required.")
        self._seed_demo()
        return handler(revision, values, authorize)

    # ---- shared -------------------------------------------------------------

    def _seed_demo(self):
        """Give a demo store deterministic sample data, in memory only."""
        if not self.demo or self._seeded:
            return
        self._seeded = True
        text, revision = self.store.read_text("config.yaml")
        if not text:
            self.store.write_text("config.yaml", DEMO_CONFIG, revision)

    def _result(self, section, available, *, message=None, items=None, actions=None, **extra):
        return {"section": section, "available": available, "source": "demo" if self.demo else "hermes-home",
                "message": ("Demo data in memory only. Nothing is saved to disk or sent anywhere. " + (message or "")).strip()
                if self.demo else message,
                "updated_at": DEMO_TIME if self.demo else _now(), "items": items or [], "actions": actions or [], **extra}

    def _ok(self, message, revision, item=None):
        result = {"ok": True, "message": message + (" (demo only)" if self.demo else ""), "revision": revision}
        if item is not None:
            result["item"] = item
        return result

    @staticmethod
    def _expect(values, required=(), optional=()):
        unknown = set(values) - set(required) - set(optional)
        if unknown:
            raise AdminError("Unsupported field: " + sorted(unknown)[0][:40])
        missing = [name for name in required if name not in values]
        if missing:
            raise AdminError("Missing field: " + missing[0])

    @staticmethod
    def _split(revision, count):
        parts = revision.split(".")
        if len(parts) != count:
            raise AdminError("A valid data revision is required.")
        return parts

    def _mutate_config(self, revision, change, authorize):
        return self.store.mutate_yaml("config.yaml", revision, change, authorize=authorize)

    def _env_present(self, env, name):
        if env.get(name):
            return "home .env"
        if self.environ.get(name):
            return "process environment"
        return None

    # ---- models -------------------------------------------------------------

    def _snap_models(self, config, rev, env, env_rev):
        main = _main_model(config)
        name = main.get("default") or main.get("model")
        provider = main.get("provider") if isinstance(main.get("provider"), str) else "auto"
        base_url = main.get("base_url")
        items = [{"id": "main", "role": "Main", "model": _text(name) or None, "provider": provider[:40] or "auto",
                  "base_url": _safe_url(base_url), "base_url_configured": bool(base_url),
                  "credential_in_config": bool(main.get("api_key"))}]
        auxiliary = _dict(config.get("auxiliary"))
        for role, label in AUX_ROLES:
            spec = _dict(auxiliary.get(role))
            items.append({"id": role, "role": label, "model": _text(spec.get("model")) or None,
                          "provider": _text(spec.get("provider"), 40) or "auto",
                          "uses_main": not spec.get("model") and (spec.get("provider") or "auto") == "auto"})
        provider_options = _options(PROVIDERS if provider in PROVIDERS else (*PROVIDERS, provider[:40]))
        actions = [{
            "id": "save_main", "label": "Change main model", "revision": rev, "target": "main",
            "description": "Model used by every new conversation. " + RESTART,
            "fields": [
                _field("model", "Model", "text", value=_text(name) or "", required=True, max_length=160,
                       help="Provider model id, for example anthropic/claude-sonnet-4.5."),
                _field("provider", "Provider", "select", value=provider if provider in PROVIDERS else provider[:40],
                       options=provider_options, required=True,
                       help="auto detects the provider from configured keys."),
                _field("base_url", "Base URL", "text", value=_safe_url(base_url) or "", max_length=500,
                       help="Only for custom or self-hosted endpoints. Leave empty to keep the current value."),
                _field("clear_base_url", "Remove base URL", "checkbox", value=False,
                       help="Use the provider's default endpoint."),
            ]}]
        for role, label in AUX_ROLES:
            spec = _dict(auxiliary.get(role))
            current = spec.get("provider") if isinstance(spec.get("provider"), str) else "auto"
            actions.append({
                "id": "save_auxiliary", "label": f"Change {label.lower()} model", "revision": rev, "target": role,
                "description": "Leave the model empty and choose auto to reuse the main model. " + RESTART,
                "fields": [_hidden("role", role),
                           _field("provider", "Provider", "select", value=current if current in PROVIDERS else "auto",
                                  options=_options(PROVIDERS), required=True),
                           _field("model", "Model", "text", value=_text(spec.get("model")) or "", max_length=160)]})
        return self._result("models", True, items=items, actions=actions,
                            message="Configured models from config.yaml. Credentials are managed on the Keys page. " + RESTART)

    def _do_models_save_main(self, revision, values, authorize):
        self._expect(values, ("model", "provider"), ("base_url", "clear_base_url"))
        model = _model_name(values["model"], required=True)
        provider = values["provider"]
        if provider not in PROVIDERS:
            raise AdminError("Choose a supported provider.")
        base_url = _check_url(values.get("base_url") or None, required=False)
        clear = _bool(values.get("clear_base_url", False))
        if clear and base_url:
            raise AdminError("Either enter a base URL or remove it, not both.")

        def change(config):
            current = config.get("model")
            main = {"default": current} if isinstance(current, str) else _slot(config, "model")
            config["model"] = main
            main["default"] = model
            if "model" in main:
                main["model"] = model  # keep both spellings consistent
            main["provider"] = provider
            if base_url:
                main["base_url"] = base_url
            elif clear:
                main.pop("base_url", None)
            if provider == "custom" and not main.get("base_url"):
                raise AdminError("A custom provider needs a base URL.")
            return config
        new = self._mutate_config(revision, change, authorize)
        return self._ok(f"Main model set to {model}. " + RESTART, new, {"id": "main", "model": model, "provider": provider})

    def _do_models_save_auxiliary(self, revision, values, authorize):
        self._expect(values, ("role", "provider"), ("model",))
        roles = dict(AUX_ROLES)
        role = values["role"]
        if role not in roles:
            raise AdminError("Unknown auxiliary task.")
        provider = values["provider"]
        if provider not in PROVIDERS:
            raise AdminError("Choose a supported provider.")
        model = _model_name(values.get("model", ""), required=False)

        def change(config):
            spec = _slot(_slot(config, "auxiliary"), role)
            spec["provider"] = provider
            spec["model"] = model
            return config
        new = self._mutate_config(revision, change, authorize)
        what = model or ("the main model" if provider == "auto" else f"the {provider} default")
        return self._ok(f"{roles[role]} now uses {what}. " + RESTART, new,
                        {"id": role, "model": model or None, "provider": provider})

    # ---- config ---------------------------------------------------------------

    @staticmethod
    def _setting_value(config, setting):
        section, key = setting["path"]
        holder = config if section is None else _dict(config.get(section))
        return holder[key] if key in holder else setting["default"], key in holder

    def _snap_config(self, config, rev, env, env_rev):
        items, actions = [], []
        for setting in SETTINGS:
            value, explicit = self._setting_value(config, setting)
            if isinstance(value, bool) or value is None or isinstance(value, int):
                shown = value
            elif isinstance(value, float) and math.isfinite(value):
                shown = value
            elif isinstance(value, str):
                shown = value[:100]
            else:
                shown = None
            items.append({"id": setting["id"], "label": setting["label"], "value": shown, "editable": True,
                          "source": "config.yaml" if explicit else "Hermes default", "help": setting["help"]})
            options = setting.get("options")
            field = _field("value", setting["label"], setting["type"],
                           value=shown if not options or shown in options else options[0], help=setting["help"],
                           options=_options(options, {"": "Provider default"}) if options else None,
                           max_length=setting.get("max_length"), required=setting["type"] == "number" or None)
            actions.append({"id": "save_setting", "label": "Change " + setting["label"].lower(), "revision": rev,
                            "target": setting["id"], "description": RESTART,
                            "fields": [_hidden("setting", setting["id"]), field]})
        for section, key, label, default, note in LOCKED:
            holder = _dict(config.get(section))
            if key is None:
                value = ", ".join(sorted(str(name)[:40] for name in holder)[:20]) or None
                source = "config.yaml" if holder else "Hermes default"
            else:
                raw = holder.get(key, default)
                value = raw if isinstance(raw, (bool, int)) or raw is None else str(raw)[:40]
                source = "config.yaml" if key in holder else "Hermes default"
            items.append({"id": f"{section}.{key}" if key else section, "label": label, "value": value,
                          "editable": False, "source": source, "help": note})
        return self._result("config", True, items=items, actions=actions,
                            message="Allowlisted settings only; every other setting in config.yaml is preserved. "
                                    "Memory, sandbox and toolsets are locked to keep users of a shared bot isolated. "
                                    + RESTART)

    def _do_config_save_setting(self, revision, values, authorize):
        self._expect(values, ("setting", "value"))
        setting = SETTING_IDS.get(values["setting"])
        if setting is None:
            raise AdminError("This setting cannot be changed from the console.")
        value = values["value"]
        if setting["type"] == "checkbox":
            value = _bool(value)
        elif setting["type"] == "select":
            value = _choice(value, setting["options"], "value")
        elif setting["type"] == "number":
            try:
                value = int(str(value).strip())
            except ValueError:
                raise AdminError("Enter a whole number.") from None
            if not setting["minimum"] <= value <= setting["maximum"]:
                raise AdminError(f"Enter a number from {setting['minimum']} to {setting['maximum']}.")
        else:
            value = value.strip() if isinstance(value, str) else None
            if value is None or len(value) > setting["max_length"]:
                raise AdminError("The value is too long.")
            if setting["id"] == "timezone" and value:
                try:
                    if not re.fullmatch(r"[A-Za-z0-9_+./-]{1,64}", value) or ".." in value:
                        raise ValueError
                    ZoneInfo(value)
                except (ValueError, ZoneInfoNotFoundError):
                    raise AdminError("Enter a valid IANA time zone such as Asia/Ho_Chi_Minh.") from None
        section, key = setting["path"]

        def change(config):
            holder = config if section is None else _slot(config, section)
            holder[key] = value
            return config
        new = self._mutate_config(revision, change, authorize)
        return self._ok(f"{setting['label']} saved. " + RESTART, new, {"id": setting["id"], "value": value})

    # ---- keys -----------------------------------------------------------------

    def _snap_keys(self, config, rev, env, env_rev):
        items, actions = [], []
        for name, label, kind in KEYS:
            where = self._env_present(env, name)
            items.append({"id": name, "variable": name, "provider": label, "kind": kind,
                          "configured": where is not None, "source": where, "editable": True,
                          "notice": self._override_notice(env, name)})
            actions.append({"id": "set_key", "label": ("Replace " if env.get(name) else "Add ") + label + " key",
                            "revision": env_rev, "target": name,
                            "description": "Stored only in the bot home .env and never shown again. " + RESTART,
                            "fields": [_hidden("variable", name),
                                       _field("value", label + " secret", "password", required=True, max_length=4096)]})
            if env.get(name):
                actions.append({"id": "remove_key", "label": "Remove " + label + " key", "revision": env_rev,
                                "target": name, "description": "Deletes the value from the bot home .env. "
                                "A value inherited from the process environment would still apply.",
                                "fields": [_hidden("variable", name)]})
        for name, label, kind in READ_ONLY_KEYS:
            where = self._env_present(env, name)
            items.append({"id": name, "variable": name, "provider": label, "kind": kind,
                          "configured": where is not None, "source": where, "editable": False,
                          "note": "Managed in the repository .env by the Zalo setup."})
        return self._result("keys", True, items=items, actions=actions,
                            message="Presence only; values are never returned. Hermes loads the bot home .env over "
                                    "inherited process variables when it starts. " + RESTART)

    def _override_notice(self, env, name):
        inherited = self.environ.get(name)
        if not inherited:
            return None
        if not env.get(name):
            return ("The active value comes from the process environment or project .env. Saving a value here "
                    "makes the bot home .env win from the next gateway start.")
        if env.get(name) != inherited:
            return ("The process environment holds a different value; Hermes uses the bot home .env value "
                    "after the next gateway start.")
        return None

    def _key_name(self, values):
        name = values["variable"]
        if name not in KEY_NAMES:
            raise AdminError("This credential cannot be managed from the console.")
        return name

    def _do_keys_set_key(self, revision, values, authorize):
        self._expect(values, ("variable", "value"))
        name = self._key_name(values)
        new = self.store.mutate_env(revision, {name: _secret(values["value"])}, authorize=authorize)
        return self._ok(f"{name} saved. " + RESTART, new, {"id": name, "configured": True})

    def _do_keys_remove_key(self, revision, values, authorize):
        self._expect(values, ("variable",))
        name = self._key_name(values)
        new = self.store.mutate_env(revision, {name: None}, authorize=authorize)
        still = bool(self.environ.get(name))
        return self._ok(f"{name} removed from the bot home .env." + (
            " The process environment still provides a value." if still else "") + " " + RESTART, new,
            {"id": name, "configured": still})

    # ---- channels -------------------------------------------------------------

    def _snap_channels(self, config, rev, env, env_rev):
        items, actions = [], []
        known = {name for name, _, _ in CHANNELS}
        extra = sorted({name for name in (*_dict(config.get("platforms")),
                                          *_dict(_dict(config.get("gateway")).get("platforms")))
                        if isinstance(name, str) and NAME.fullmatch(name) and name not in known and name != "zalo"})
        for name, label, token in (*CHANNELS, *((name, name.replace("_", " ").title(), None) for name in extra)):
            spec = _platform(config, name)
            explicit = spec.get("enabled") if isinstance(spec.get("enabled"), bool) else None
            credential = self._env_present(env, token) if token else None
            effective = explicit if explicit is not None else credential is not None
            reply = spec.get("reply_to_mode") if spec.get("reply_to_mode") in REPLY_MODES else "first"
            typing = spec.get("typing_indicator") if isinstance(spec.get("typing_indicator"), bool) else True
            items.append({"id": name, "channel": label, "enabled": effective,
                          "mode": "on" if explicit is True else "off" if explicit is False else "auto",
                          "credential": token, "credential_configured": credential is not None,
                          "credential_source": credential, "reply_to_mode": reply, "typing_indicator": typing,
                          "connection": "not verified", "editable": token is not None,
                          **({} if token else {"note": "Read-only here: other platforms may open network listeners. "
                                                       "Use the setup scripts."})})
            if token is None:
                continue
            actions.append({"id": "save_channel", "label": f"{label} settings", "revision": rev, "target": name,
                            "description": "auto starts the channel whenever its token is configured; off keeps it "
                                           "stopped even with a token. " + RESTART,
                            "fields": [_hidden("channel", name),
                                       _field("mode", "Start channel", "select",
                                              value="on" if explicit is True else "off" if explicit is False else "auto",
                                              options=_options(("auto", "on", "off"), {"auto": "Automatic (when a token exists)",
                                                                                       "on": "Always", "off": "Never"}),
                                              required=True),
                                       _field("reply_to_mode", "Reply threading", "select", value=reply,
                                              options=_options(REPLY_MODES, {"off": "Never reply-quote",
                                                                             "first": "First message only",
                                                                             "all": "Every message"})),
                                       _field("typing_indicator", "Show typing indicator", "checkbox", value=typing)]})
        plugins = _dict(config.get("plugins")).get("enabled")
        if isinstance(plugins, list) and "zalo-platform" in plugins or self._env_present(env, "ZALO_BOT_TOKEN"):
            items.append({"id": "zalo", "channel": "Zalo", "enabled": isinstance(plugins, list) and "zalo-platform" in plugins,
                          "mode": "plugin", "credential": "ZALO_BOT_TOKEN",
                          "credential_configured": self._env_present(env, "ZALO_BOT_TOKEN") is not None,
                          "credential_source": self._env_present(env, "ZALO_BOT_TOKEN"), "reply_to_mode": None,
                          "typing_indicator": None, "connection": "not verified",
                          "note": "Managed by the repository Zalo setup scripts."})
        return self._result("channels", True, items=items, actions=actions,
                            message="Channel settings from config.yaml; tokens are managed on the Keys page. "
                                    "A configured channel is not proof of a healthy connection. " + RESTART)

    def _do_channels_save_channel(self, revision, values, authorize):
        self._expect(values, ("channel", "mode"), ("reply_to_mode", "typing_indicator"))
        name = values["channel"]
        if not isinstance(name, str) or not NAME.fullmatch(name) or name == "zalo":
            raise AdminError("This channel cannot be managed from the console.")
        mode = _choice(values["mode"], ("auto", "on", "off"), "start mode")
        reply = _choice(values["reply_to_mode"], REPLY_MODES, "reply mode") if "reply_to_mode" in values else None
        typing = _bool(values["typing_indicator"]) if "typing_indicator" in values else None
        if name not in {item for item, _, _ in CHANNELS}:
            raise AdminError("This channel cannot be managed from the console.")

        def change(config):
            blocks = _platform_blocks(config, name)
            if blocks:
                target = blocks[-1][1]  # the highest-precedence block the gateway reads
            else:
                target = _slot(_slot(config, "platforms"), name)
            if mode == "auto":
                for _, block in _platform_blocks(config, name):
                    block.pop("enabled", None)
            else:
                target["enabled"] = mode == "on"
            if reply is not None:
                target["reply_to_mode"] = reply
            if typing is not None:
                target["typing_indicator"] = typing
            return config
        new = self._mutate_config(revision, change, authorize)
        return self._ok(f"{name.capitalize()} settings saved. " + RESTART, new, {"id": name, "mode": mode})

    # ---- MCP --------------------------------------------------------------------

    def _snap_mcp(self, config, rev, env, env_rev):
        servers = _dict(config.get("mcp_servers"))
        items, actions = [], []
        for name, spec in list(servers.items())[:100]:
            if not isinstance(name, str) or not isinstance(spec, dict):
                continue
            safe = NAME.fullmatch(name) is not None
            kind = "http" if spec.get("url") else "stdio" if spec.get("command") else "unknown"
            enabled = spec.get("enabled") is not False and str(spec.get("enabled")).lower() not in ("false", "off", "0", "no")
            items.append({"id": name[:48], "name": name[:48], "transport": kind,
                          "host": _host(spec.get("url")) if kind == "http" else None, "enabled": enabled,
                          "credentials_configured": bool(spec.get("headers") or spec.get("env") or spec.get("auth")),
                          "exposed_to": _mcp_exposure(config, name, self.environ, env),
                          "status": "configured", "connection": "not tested"})
            if not safe:
                continue
            actions.append({"id": "toggle_server", "label": ("Disable " if enabled else "Enable ") + name,
                            "revision": rev, "target": name, "description": RESTART,
                            "fields": [_hidden("name", name), _field("enabled", "Enabled", "checkbox", value=not enabled)]})
            actions.append({"id": "remove_server", "label": "Remove " + name, "revision": f"{rev}.{env_rev}",
                            "target": name, "description": "Deletes this server from config.yaml and the console-created "
                            "token for it, if any. " + RESTART, "fields": [_hidden("name", name)]})
        actions.insert(0, {
            "id": "add_http_server", "label": "Add MCP server", "revision": f"{rev}.{env_rev}",
            "description": "Remote (HTTP) servers only. Local command servers must be added by an operator on the host. "
                           "A server can only be enabled when no chat channel would receive its tools (add no_mcp "
                           "to that channel's toolsets in setup first); otherwise it is saved disabled. "
                           "The connection is not tested here. " + RESTART,
            "fields": [_field("name", "Name", "text", required=True, max_length=48,
                              help="Lowercase letters, digits, - and _."),
                       _field("url", "Server URL", "text", required=True, max_length=500,
                              help="https URL of the MCP endpoint (http only for localhost)."),
                       _field("token", "Bearer token", "password", max_length=4096,
                              help="Optional. Saved in the bot home .env and sent as an Authorization header."),
                       _field("timeout", "Tool timeout (seconds)", "number", value=120,
                              help="From 5 to 600."),
                       _field("enabled", "Enabled", "checkbox", value=not _mcp_exposure(config, "", self.environ, env))]})
        return self._result("mcp", True, items=items, actions=actions,
                            message="Commands, arguments, URLs and secrets are hidden. Hermes offers an enabled MCP "
                                    "server's tools to every chat channel that does not list no_mcp, so the console "
                                    "refuses to enable a server that chat users would reach. Hermes connects to MCP servers "
                                    "when it starts; connectivity is not tested here. " + RESTART)

    def _refuse_exposure(self, config, name, env, enabled):
        exposed = _mcp_exposure(config, name, self.environ, env) if enabled else []
        if exposed:
            raise AdminError("Enabling this server would give its tools to chat users on " + ", ".join(exposed) +
                             ". Add no_mcp to those channels' toolsets in setup first, or save it disabled.", 409)

    def _server_name(self, values):
        name = values["name"].strip() if isinstance(values["name"], str) else None
        if not name or not NAME.fullmatch(name):
            raise AdminError("Server names use lowercase letters, digits, - and _ (at most 48 characters).")
        return name

    def _do_mcp_add_http_server(self, revision, values, authorize):
        self._expect(values, ("name", "url"), ("token", "timeout", "enabled"))
        config_rev, env_rev = self._split(revision, 2)
        name = self._server_name(values)
        url = _check_url(values["url"].strip() if isinstance(values["url"], str) else None, required=True)
        token = values.get("token") or None
        token = _secret(token) if token is not None else None
        try:
            timeout = int(str(values.get("timeout", 120)).strip())
        except ValueError:
            raise AdminError("Enter the timeout as a whole number of seconds.") from None
        if not 5 <= timeout <= 600:
            raise AdminError("The timeout must be from 5 to 600 seconds.")
        enabled = _bool(values.get("enabled", True))
        variable = _mcp_token_var(name)
        config, current = self.store.read_yaml("config.yaml")
        if current != config_rev:
            raise AdminError("This configuration changed. Refresh before trying again.", 409)
        if name in _dict(config.get("mcp_servers")):
            raise AdminError("An MCP server with this name already exists.", 409)
        env, _ = self.store.read_env()
        self._refuse_exposure(config, name, env, enabled)
        if token and env.get(variable):
            raise AdminError(f"{variable} already exists in the bot home .env. Remove it or choose another name.", 409)
        new_env = None
        if token:
            new_env = self.store.mutate_env(env_rev, {variable: token}, authorize=authorize)

        def change(config):
            servers = _slot(config, "mcp_servers")
            if name in servers:
                raise AdminError("An MCP server with this name already exists.", 409)
            self._refuse_exposure(config, name, env, enabled)
            entry = {"url": url, "timeout": timeout, "enabled": enabled}
            if token:
                entry["headers"] = {"Authorization": "Bearer ${" + variable + "}"}
            servers[name] = entry
            return config
        try:
            new = self._mutate_config(config_rev, change, authorize)
        except Exception:
            if new_env is not None:
                # Do not leave an orphan secret behind when the config edit fails.
                try:
                    self.store.mutate_env(new_env, {variable: None}, authorize=authorize)
                except Exception:
                    pass
            raise
        _, env_now = self.store.read_env()
        return self._ok(f"MCP server {name} added. " + RESTART, f"{new}.{env_now}",
                        {"id": name, "transport": "http", "enabled": enabled, "credentials_configured": bool(token)})

    def _do_mcp_toggle_server(self, revision, values, authorize):
        self._expect(values, ("name", "enabled"))
        name = self._server_name(values)
        enabled = _bool(values["enabled"])

        def change(config):
            server = _dict(config.get("mcp_servers")).get(name)
            if not isinstance(server, dict):
                raise AdminError("This MCP server no longer exists. Refresh the page.", 404)
            self._refuse_exposure(config, name, env, enabled)
            server["enabled"] = enabled
            return config
        env, _ = self.store.read_env()
        new = self._mutate_config(revision, change, authorize)
        return self._ok(f"MCP server {name} {'enabled' if enabled else 'disabled'}. " + RESTART, new,
                        {"id": name, "enabled": enabled})

    def _do_mcp_remove_server(self, revision, values, authorize):
        self._expect(values, ("name",))
        config_rev, env_rev = self._split(revision, 2)
        name = self._server_name(values)
        variable = _mcp_token_var(name)
        owned = []

        def change(config):
            servers = _dict(config.get("mcp_servers"))
            server = servers.get(name)
            if not isinstance(server, dict):
                raise AdminError("This MCP server no longer exists. Refresh the page.", 404)
            headers = _dict(server.get("headers"))
            # Only the token this console created, referenced exactly as it wrote it.
            owned.append(headers.get("Authorization") == "Bearer ${" + variable + "}")
            del servers[name]
            return config
        new = self._mutate_config(config_rev, change, authorize)
        env, current_env = self.store.read_env()
        if owned and owned[0] and env.get(variable):
            used = any(variable in str(spec) for spec in _dict(self.store.read_yaml("config.yaml")[0].get("mcp_servers")).values())
            if not used and current_env == env_rev:
                current_env = self.store.mutate_env(env_rev, {variable: None}, authorize=authorize)
        return self._ok(f"MCP server {name} removed. " + RESTART, f"{new}.{current_env}", {"id": name})


DEMO_CONFIG = """\
model:
  default: demo/reasoning-large
  provider: openrouter
auxiliary:
  vision:
    provider: openrouter
    model: demo/vision-small
  compression:
    provider: auto
    model: ''
memory:
  memory_enabled: false
  user_profile_enabled: false
platform_toolsets:
  telegram: [web, vision, no_mcp]
approvals:
  mode: smart
terminal:
  backend: docker
  timeout: 180
platforms:
  telegram:
    enabled: true
    reply_to_mode: first
mcp_servers:
  demo-docs:
    url: https://mcp.example.invalid/mcp
    timeout: 120
    enabled: true
"""
