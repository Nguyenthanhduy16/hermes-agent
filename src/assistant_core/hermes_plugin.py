"""Wire the assistant core into Hermes through lifecycle hooks only.

- pre_gateway_dispatch: per-person daily quota for authorized, non-command
  turns on every platform (Telegram, Zalo assistant mode, ...).
- pre_tool_call: veto tools that are not yet isolated per person.
- pre/post_api_request, api_request_error, post_tool_call: telemetry.

Hooks observe or veto; none of them changes which model runs a step.
"""

import hashlib
import logging
import os
from pathlib import Path
import secrets
import subprocess
import time

from .quota import QuotaExceeded, QuotaStore, person_key
from .settings import CoreSettings, SettingsError
from .telemetry import TelemetryStore, config_snapshot


logger = logging.getLogger(__name__)

# Host-level toolsets never exposed to a messaging chat.
FORBIDDEN_TOOLSETS = frozenset({"computer_use", "desktop_ui", "connections", "setup", "project"})
QUOTA_USER_TEXT = ("Bạn đã dùng hết {limit} lượt hỏi trợ lý hôm nay. "
                   "Hạn mức đặt lại lúc 0:00 (giờ Việt Nam).")
QUOTA_TOTAL_TEXT = "Trợ lý đã dùng hết hạn mức chung hôm nay. Bạn thử lại vào ngày mai nhé."
UNSAFE_TEXT = "Trợ lý đang tạm dừng vì cấu hình công cụ chưa an toàn. Quản trị viên cần kiểm tra lại."
BLOCKED_TOOL_TEXT = ("Công cụ {tool} đang bị tắt trong trợ lý nhiều người dùng vì chưa tách dữ liệu "
                     "theo từng người. Hãy trả lời mà không dùng công cụ này.")


def unsafe_toolsets(config: dict) -> list[str]:
    """Platforms whose toolset list includes a host-level toolset."""
    toolsets = (config or {}).get("platform_toolsets") or {}
    return sorted(name for name, items in toolsets.items()
                  if isinstance(items, list) and FORBIDDEN_TOOLSETS.intersection(items))


def config_file_hash(config_text: bytes) -> str:
    """The run's config identity: the first 16 hex digits of sha256(config.yaml bytes)."""
    return hashlib.sha256(config_text or b"").hexdigest()[:16]


def _hermes_rev() -> str:
    try:
        import gateway
        root = Path(gateway.__file__).resolve().parents[1]
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=10", "HEAD"],
                             capture_output=True, text=True, timeout=3)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _platform(source) -> str:
    platform = getattr(source, "platform", None)
    return str(getattr(platform, "value", platform) or "unknown")


class AssistantCore:
    def __init__(self, settings: CoreSettings, *, config: dict, config_text: bytes = b"", home: Path | None = None):
        self.settings = settings
        self.unsafe = unsafe_toolsets(config)
        if self.unsafe:
            logger.error("assistant-core: host-level toolsets configured for %s; all turns are refused",
                         ", ".join(self.unsafe))
        self.quota = QuotaStore(settings.data_dir / "quota.sqlite3",
                                user_daily_limit=settings.user_daily_limit,
                                total_daily_limit=settings.total_daily_limit)
        self.telemetry = None
        if settings.telemetry:
            run_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
            config_hash = config_file_hash(config_text)
            self.telemetry = TelemetryStore(
                settings.data_dir / "telemetry.sqlite3", run_id=run_id, hermes_rev=_hermes_rev(),
                config_hash=config_hash, label=settings.run_label)
            try:
                # Secret-free settings summary so runs can be compared by what they used.
                self.telemetry.save_snapshot(config_hash, config_snapshot(config, home=home))
            except Exception:
                logger.warning("assistant-core could not store the settings snapshot", exc_info=True)

    # ---- admission ------------------------------------------------------

    async def pre_gateway_dispatch(self, event=None, gateway=None, **_):
        if event is None or getattr(event, "internal", False):
            return None
        source = getattr(event, "source", None)
        user_id = getattr(source, "user_id", None)
        text = (getattr(event, "text", "") or "").lstrip()
        if not user_id or text.startswith("/"):
            return None  # gateway commands and anonymous events never consume quota
        # This hook runs before Hermes authorization: never let strangers
        # consume (or probe) the quota; the gateway answers them itself.
        check = getattr(gateway, "_is_user_authorized_for_source", None)
        try:
            if check is not None and not check(source):
                return None
        except Exception:
            return None
        if self.unsafe:
            await self._reply(gateway, source, UNSAFE_TEXT)
            return {"action": "skip", "reason": "assistant-core unsafe toolsets"}
        platform = _platform(source)
        message = f"{platform}:{getattr(source, 'chat_id', '')}:{getattr(event, 'message_id', None) or time.time_ns()}"
        try:
            self.quota.reserve(person_key(platform, str(user_id)), message)
        except QuotaExceeded as exceeded:
            text = (QUOTA_USER_TEXT.format(limit=self.settings.user_daily_limit)
                    if exceeded.scope == "user" else QUOTA_TOTAL_TEXT)
            await self._reply(gateway, source, text)
            return {"action": "skip", "reason": "assistant-core quota " + exceeded.scope}
        except Exception:
            logger.exception("assistant-core quota store failed; refusing the turn")
            return {"action": "skip", "reason": "assistant-core quota unavailable"}
        return None

    @staticmethod
    async def _reply(gateway, source, text):
        try:
            adapter = gateway._delivery_adapter_for(source)
            if adapter is not None:
                await adapter.send(source.chat_id, text)
        except Exception:
            logger.warning("assistant-core could not deliver a notice", exc_info=True)

    # ---- tools ----------------------------------------------------------

    def pre_tool_call(self, tool_name=None, **kw):
        if tool_name in self.settings.blocked_tools:
            if self.telemetry:
                self.telemetry.tool_call(tool_name=tool_name, status="blocked", blocked=True, **kw)
            return {"action": "block", "message": BLOCKED_TOOL_TEXT.format(tool=tool_name)}
        return None

    def post_tool_call(self, **kw):
        if self.telemetry:
            self.telemetry.tool_call(**{k: v for k, v in kw.items() if k not in ("args", "result", "blocked")})

    # ---- telemetry ------------------------------------------------------

    def pre_api_request(self, **kw):
        if self.telemetry:
            self.telemetry.pre_api_request(**kw)

    def post_api_request(self, **kw):
        if self.telemetry:
            self.telemetry.post_api_request(**kw)

    def api_request_error(self, **kw):
        if self.telemetry:
            self.telemetry.api_request_error(**kw)


def _safe(callback):
    """Observers must never break a turn; vetoes are returned unchanged."""
    def wrapper(**kw):
        try:
            return callback(**kw)
        except Exception:
            logger.warning("assistant-core %s hook failed", getattr(callback, "__name__", "?"), exc_info=True)
            return None
    wrapper.__name__ = getattr(callback, "__name__", "hook")
    return wrapper


def register(ctx) -> None:
    from hermes_constants import get_hermes_home
    from hermes_cli.config import load_config_readonly

    home = Path(get_hermes_home())
    try:
        settings = CoreSettings.from_mapping(os.environ, default_dir=home / "assistant-core")
    except SettingsError as error:
        logger.error("assistant-core disabled: %s", error)
        raise
    config_path = home / "config.yaml"
    core = AssistantCore(settings, config=load_config_readonly(),
                         config_text=config_path.read_bytes() if config_path.exists() else b"", home=home)
    ctx.register_hook("pre_gateway_dispatch", core.pre_gateway_dispatch)
    # Not wrapped: a failing veto must surface, not silently allow the tool.
    ctx.register_hook("pre_tool_call", core.pre_tool_call)
    for name in ("post_tool_call", "pre_api_request", "post_api_request", "api_request_error"):
        ctx.register_hook(name, _safe(getattr(core, name)))
    logger.info("assistant-core loaded: quota %s/%s per day, blocked tools %s, telemetry %s",
                settings.user_daily_limit, settings.total_daily_limit,
                ",".join(sorted(settings.blocked_tools)), "on" if core.telemetry else "off")
