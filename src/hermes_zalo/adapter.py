"""Official Zalo transport for the Hermes platform-plugin interface.

Hermes owns general conversation. Explicit work commands are handled locally;
the model has no general-purpose tools or gateway administration access.
"""

import asyncio
from contextlib import suppress
from contextvars import ContextVar
from hashlib import sha256
import logging
import os
from pathlib import Path
from datetime import datetime
import time
from dataclasses import replace

from gateway.config import Platform, PlatformConfig
from gateway.platforms._shared import get_scoped_secret
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType, ProcessingOutcome
from hermes_constants import get_hermes_home

from .api import AuthenticationError, DeliveryUncertain, WebhookInspectionUnavailable, ZaloAPIError, ZaloClient
from .config import ConfigError, Settings
from .events import is_allowed, parse_update, split_text
from .mentions import strip_bot_mention
from .logging_utils import protect_logs
from .state import StateStore
from .work import WorkService, state_prefix
from .admin_config import AdminConfig
from .conversations import ConversationStore
from .managed_chat import ManagedChat, ManagedChatError
from .reminders import VIETNAM_TZ


logger = logging.getLogger(__name__)
PLUGIN_NAME = "zalo-platform"
TURN_TIMEOUT_SECONDS = 300


def scoped_settings() -> Settings:
    values = {
        name: get_scoped_secret(name, "") or ""
        for name in ("ZALO_BOT_TOKEN", "ZALO_ALLOWED_USERS", "ZALO_ALLOWED_GROUPS", "ZALO_STATE_DIR", "ZALO_KNOWLEDGE_DIR", "ZALO_GROUP_ACCESS")
    }
    if not values["ZALO_STATE_DIR"]:
        values["ZALO_STATE_DIR"] = str(Path(get_hermes_home()) / "zalo-state")
    return Settings.from_mapping(values)


def assert_chat_only() -> None:
    """Reject unsafe config, including implicit tools added by Hermes plugins.

An empty adapter override is ignored by some Hermes releases; checking the
effective resolver is required instead of assuming that returning [] suffices.
"""
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.tools_config import _get_platform_tools

    config = load_user_config_effective(Path(get_hermes_home()) / "config.yaml", fail_closed=True)
    if (config.get("platform_toolsets") or {}).get("zalo") != []:
            raise ConfigError("Zalo requires platform_toolsets.zalo: []; work commands run in the adapter.")
    if _get_platform_tools(config, "zalo"):
        raise ConfigError("Hermes still exposes tools to Zalo; disable tools, MCP and other tool plugins.")
    memory = config.get("memory") or {}
    if memory.get("memory_enabled") is not False or memory.get("user_profile_enabled") is not False:
        raise ConfigError("Phase 1 requires memory.memory_enabled and memory.user_profile_enabled: false.")
    gateway = config.get("gateway") or {}
    if gateway.get("delivery_ledger") is not False:
        raise ConfigError("Phase 1 requires gateway.delivery_ledger: false to avoid ambiguous Zalo resends.")
    if config.get("group_sessions_per_user", gateway.get("group_sessions_per_user", True)) is not True:
        raise ConfigError("Zalo group_sessions_per_user must remain true.")
    if config.get("multiplex_profiles", gateway.get("multiplex_profiles")) is not False:
        raise ConfigError("Phase 1 requires gateway.multiplex_profiles: false in a dedicated Hermes home.")
    if gateway.get("profile_routes") or config.get("profile_routes"):
        raise ConfigError("Profile routing is disabled for the phase 1 Zalo adapter.")


class ZaloAdapter(BasePlatformAdapter):
    # Chunk exactly once, inside send(); no Markdown transformations or editing.
    splits_long_messages = True
    supports_async_delivery = False
    MAX_MESSAGE_LENGTH = 12000

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform("zalo"))
        self.settings = scoped_settings()
        self.client: ZaloClient | None = None
        self.store: StateStore | None = None
        self._poll_task: asyncio.Task | None = None
        self._reminder_task: asyncio.Task | None = None
        self._tickets_task: asyncio.Task | None = None
        self._issues_task: asyncio.Task | None = None
        self.work: WorkService | None = None
        self._groups_supported = False
        self._bot_names: tuple[str, ...] = ()
        self._current_event: MessageEvent | None = None
        self._current_outcome: str | None = None
        self._reply_chat: str | None = None
        self._reply_budget = self.settings.max_reply_chars
        self._delivery_failed = False
        self.conversations = None
        self.managed_chat = None
        self.admin_runtime = None
        self._current_message = None
        self._archive_sequence = 0
        self._archive_task = None
        self._send_permit = ContextVar("zalo_send_permit", default=None)

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        protect_logs()
        try:
            assert_chat_only()
            if not self._message_handler:
                raise ConfigError("Hermes gateway message handler is not installed.")
            token_id = sha256(self.settings.token.encode()).hexdigest()
            if not self._acquire_platform_lock("zalo_bot", token_id, "Zalo long polling"):
                return False
            self.client = ZaloClient(self.settings.token)
            me = await self.client.get_me()
            # account_name may be an internal bot.* handle. Group mentions use
            # display_name, which is present in the live getMe response.
            self._bot_names = tuple(dict.fromkeys(
                value.strip() for key in ("display_name", "account_name")
                if isinstance(value := me.get(key), str) and value.strip()
            ))
            try:
                webhook = await self.client.get_webhook_info()
            except WebhookInspectionUnavailable:
                webhook = None
                logger.warning("Zalo getWebhookInfo returned 404; webhook state unverified. Trying polling without changing webhook configuration.")
            if webhook is not None and webhook.get("url"):
                raise ConfigError("Bot has an active webhook. Remove it explicitly before long polling.")
            self._groups_supported = me.get("can_join_groups") is True
            if self.settings.allowed_groups and not self._groups_supported:
                raise ConfigError("getMe does not confirm group access; remove group IDs or enable group support.")
            self.store = StateStore(self.settings.state_dir / ("zalo-" + token_id[:16] + ".sqlite3"))
            self.store.prune()
            self.work = WorkService(self.settings)
            self.work.recover()
            admin_config = AdminConfig.from_mapping(os.environ)
            if admin_config.conversations:
                self.conversations = ConversationStore(state_prefix(self.settings).with_suffix('.conversations.sqlite3'),
                    retention_days=admin_config.retention_days)
                self.managed_chat = ManagedChat()
                self._archive_task = asyncio.create_task(self._archive_maintenance(), name='zalo-history-retention')
            if admin_config.enabled:
                from .admin_web import AdminRuntime
                self.admin_runtime = AdminRuntime(self.work, self.conversations,
                    state_prefix(self.settings).with_suffix('.admin.sqlite3'), admin_config)
            self._mark_connected()
            self._poll_task = asyncio.create_task(self._poll_loop(), name="zalo-long-poll")
            self._reminder_task = asyncio.create_task(self._reminder_loop(), name="zalo-reminders")
            self._tickets_task = asyncio.create_task(self._tickets_loop(), name="zalo-ticket-notifications")
            if getattr(self.work, 'managed_issues', None) is not None:
                self._issues_task = asyncio.create_task(self._issues_loop(), name="zalo-issue-notifications")
            logger.info("Zalo adapter connected; group capability=%s", self._groups_supported)
            return True
        except AuthenticationError:
            self._set_fatal_error("zalo_auth", "Zalo rejected the bot token.", retryable=False)
        except ConfigError as error:
            self._set_fatal_error("zalo_config", str(error), retryable=False)
        except ZaloAPIError:
            self._set_fatal_error("zalo_connect", "Cannot establish a Zalo API connection.", retryable=True)
        except Exception:
            # Never echo exception URLs, response bodies or local secrets.
            self._set_fatal_error("zalo_startup", "Zalo startup failed; check runtime compatibility and local config.", retryable=False)
        await self.disconnect()
        return False

    async def disconnect(self) -> None:
        self._mark_disconnected()
        if self.admin_runtime:
            await self.admin_runtime.close()
            self.admin_runtime = None
        task, self._poll_task = self._poll_task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        # Cancel Hermes before closing HTTP/database resources it may still use.
        reminder_task, self._reminder_task = self._reminder_task, None
        if reminder_task and reminder_task is not asyncio.current_task():
            reminder_task.cancel()
            with suppress(asyncio.CancelledError):
                await reminder_task
        tickets_task, self._tickets_task = self._tickets_task, None
        if tickets_task and tickets_task is not asyncio.current_task():
            tickets_task.cancel()
            with suppress(asyncio.CancelledError):
                await tickets_task
        issues_task, self._issues_task = self._issues_task, None
        if issues_task and issues_task is not asyncio.current_task():
            issues_task.cancel()
            with suppress(asyncio.CancelledError):
                await issues_task
        await self.cancel_background_tasks()
        if self._archive_task:
            self._archive_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._archive_task
            self._archive_task = None
        if self.conversations:
            self.conversations.close()
            self.conversations = None
        if self.work:
            await self.work.close()
            self.work = None
        if self.client:
            await self.client.close()
            self.client = None
        if self.store:
            self.store.close()
            self.store = None
        self._reply_chat = None
        self._release_platform_lock()

    async def _archive_maintenance(self):
        while True:
            self.conversations.maintenance()
            await asyncio.sleep(3600)

    def _archive_member(self, message):
        registration = getattr(self.work, 'registration', None)
        record = registration.identity(message.user_id) if registration else None
        if record and (message.chat_type == 'GROUP' or record.chat_id == message.chat_id):
            return record.member_id
        return None

    def _archive_outgoing(self, text, status, key):
        if self.conversations and self._current_message:
            self.conversations.record_outgoing(self._current_message, text, status=status, delivery_key=key)

    async def _reminder_loop(self) -> None:
        try:
            while self._running:
                await self._deliver_due_reminders()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A stopped scheduler must be visible rather than silently losing tasks.
            logger.error("Reminder scheduler stopped; restart the bot after checking local storage")
            self._set_fatal_error("zalo_reminders", "Reminder scheduler failed; restart after checking storage.", retryable=False)
            await self._notify_fatal_error()

    async def _deliver_due_reminders(self, now=None) -> None:
        if self.work is None or self.client is None:
            return
        now = time.time() if now is None else now
        for _ in range(20):
            ready = self.work.reminders.claim_due(now=now, limit=1)
            if not ready:
                break
            reminder = ready[0]
            # Destinations come ONLY from a previously authorized user command.
            # This path is never exposed to the model or arbitrary background tasks.
            member_allowed = getattr(self.work, 'member_feature_allowed', None)
            if member_allowed is not None:
                authorized = member_allowed(reminder.user_id, reminder.chat_id, reminder.chat_type)
            else:
                authorized = reminder.user_id in self.settings.allowed_users and (
                    reminder.chat_type == "PRIVATE" or
                    (reminder.chat_type == "GROUP" and reminder.chat_id in self.settings.allowed_groups))
                if authorized and getattr(self.work, 'background_registration_blocked', lambda *args: False)(
                        reminder.user_id, reminder.chat_id, reminder.chat_type):
                    authorized = False
            # Approval authorizes only the recorded private destination. Group
            # delivery still needs the existing group scope and bot capability.
            if reminder.chat_type == "GROUP" and (
                    not self._groups_supported or reminder.chat_id not in self.settings.allowed_groups):
                authorized = False
            if not authorized:
                self.work.reminders.finish(reminder.id, "blocked")
                self.work.pilot.record("reminders", "blocked")
                continue
            due = datetime.fromtimestamp(reminder.due_at, VIETNAM_TZ)
            late = " (gửi trễ do bot/mạng chưa sẵn sàng)" if now - reminder.due_at > 60 else ""
            text = f"⏰ Nhắc việc {reminder.id}{late}\nHẹn: {due:%d/%m/%Y %H:%M} (giờ Việt Nam)\n{reminder.text}"
            started = time.monotonic()
            try:
                # Preserve full Unicode content; never replay a partially sent reminder.
                for chunk in split_text(text):
                    await self.client.send_text_chunk(reminder.chat_id, chunk)
                status = "sent"
            except AuthenticationError:
                status = "failed"
                self._set_fatal_error("zalo_auth", "Token rejected when delivering reminder.", retryable=False)
            except DeliveryUncertain:
                status = "uncertain"
            except ZaloAPIError:
                status = "failed"
            except BaseException:
                self.work.reminders.finish(reminder.id, "uncertain")
                raise
            self.work.reminders.finish(reminder.id, status)
            self.work.pilot.record("reminders", "error" if status == "failed" else status,
                                   (time.monotonic() - started) * 1000)
            if self.has_fatal_error:
                await self._notify_fatal_error()
                break

    async def _tickets_loop(self) -> None:
        try:
            while self._running:
                await self._deliver_ticket_notifications()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Tickets can still be reviewed manually through /choxacnhan.
            # Keep unrelated work available if this optional sender stops.
            logger.error("Tickets notification sender stopped; check local storage and restart. No request details logged")

    async def _deliver_ticket_notifications(self) -> None:
        if self.work is None or self.client is None or self.has_fatal_error:
            return
        for _ in range(20):
            notice = await self.work.claim_ticket_notification()
            if notice is None:
                break
            # User/chat IDs originate from administrator-verified DM bindings.
            # This separate transport path is not available to Hermes/model sends.
            delivery_allowed = getattr(self.work, 'ticket_delivery_allowed', None)
            if delivery_allowed is not None:
                authorized = delivery_allowed(notice['user_id'], notice['chat_id'])
            else:
                authorized = (notice['user_id'] in self.settings.allowed_users and not
                    getattr(self.work, 'background_registration_blocked', lambda *args: False)(
                        notice['user_id'], notice['chat_id']))
            if not authorized:
                await self.work.finish_ticket_notification(notice['id'], 'blocked')
                continue
            started = time.monotonic()
            try:
                for chunk in split_text(notice['text']):
                    await self.client.send_text_chunk(notice['chat_id'], chunk)
                status = 'sent'
            except AuthenticationError:
                status = 'failed'
                self._set_fatal_error('zalo_auth', 'Token rejected when delivering ticket notification.', retryable=False)
            except DeliveryUncertain:
                status = 'uncertain'
            except ZaloAPIError:
                status = 'failed'
            except BaseException:
                await self.work.finish_ticket_notification(notice['id'], 'uncertain')
                raise
            await self.work.finish_ticket_notification(notice['id'], status)
            self.work.pilot.record('tickets_notification', 'error' if status == 'failed' else status,
                                   (time.monotonic() - started) * 1000)
            if self.has_fatal_error:
                await self._notify_fatal_error()
                break

    async def _issues_loop(self) -> None:
        try:
            while self._running:
                await self._deliver_issue_notifications()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The durable outbox remains reviewable in Admin; a stopped optional
            # sender must not disable calendar commands or expose issue details.
            logger.error('Issue notification sender stopped; check local storage and restart. No request details logged')

    async def _deliver_issue_notifications(self) -> None:
        if (self.work is None or self.client is None or self.has_fatal_error
                or getattr(self.work, 'managed_issues', None) is None):
            return
        store = self.work.managed_issues
        for _ in range(20):
            notice = await self.work.claim_issue_notification()
            if notice is None:
                break
            started, sent_chunks = time.monotonic(), 0
            status, error_code = 'sent', ''
            try:
                for chunk in split_text(notice['text']):
                    # No allowlist fallback: the current stable member, Zalo
                    # account and authorized private route must all still match.
                    # Recheck after every await, so locking/rebinding a member
                    # stops later chunks without replaying the earlier ones.
                    if notice.get('chat_type') != 'PRIVATE' or not self.work.issue_delivery_allowed(notice):
                        status = 'uncertain' if sent_chunks else 'blocked'
                        error_code = 'access_revoked'
                        break
                    if self.has_fatal_error:
                        status = 'uncertain' if sent_chunks else 'blocked'
                        error_code = 'interrupted'
                        break
                    await self.client.send_text_chunk(notice['chat_id'], chunk)
                    sent_chunks += 1
            except AuthenticationError:
                status, error_code = ('uncertain' if sent_chunks else 'failed'), 'rejected'
                self._set_fatal_error('zalo_auth', 'Token rejected when delivering issue notification.', retryable=False)
            except DeliveryUncertain:
                status, error_code = 'uncertain', 'network'
            except ZaloAPIError as error:
                # Only an explicit, known rejection before the FIRST delivered
                # chunk is retryable. Zalo provides no send idempotency key.
                rejected = error.code in (400, 401, 404, 405, 409, 413, 415, 422, 429)
                status = 'failed' if rejected and not sent_chunks else 'uncertain'
                error_code = 'rate_limit' if error.code == 429 else 'rejected'
            except BaseException:
                store.finish_notification(notice['id'], 'uncertain', 'interrupted')
                raise
            store.finish_notification(notice['id'], status, error_code)
            self.work.pilot.record('issue_notification', 'error' if status == 'failed' else status,
                                   (time.monotonic() - started) * 1000)
            if self.has_fatal_error:
                await self._notify_fatal_error()
                break

    async def _poll_loop(self) -> None:
        failures = 0
        try:
            while self._running:
                # The gateway starts adapters before restoring its sessions.
                # Polling during that gate would enqueue a turn outside our
                # admission lifetime and silently lose its reply permission.
                while self._running and getattr(self.gateway_runner, "_startup_restore_in_progress", False):
                    await asyncio.sleep(0.1)
                if not self._running:
                    break
                try:
                    update = await self.client.poll(timeout=self.settings.poll_timeout)
                    failures = 0
                except AuthenticationError:
                    self._set_fatal_error("zalo_auth", "Zalo rejected the bot token; polling stopped.", retryable=False)
                    break
                except ZaloAPIError as error:
                    # These are documented configuration or resource errors,
                    # unlike 403 (an internal API error in Zalo's error table).
                    if error.code in (400, 404, 409):
                        self._set_fatal_error("zalo_poll", "Zalo rejected polling configuration; inspect bot/webhook settings.", retryable=False)
                        break
                    failures += 1
                    logger.warning("Zalo polling unavailable; retry number %s", failures)
                    await asyncio.sleep(min(30, 2 ** min(failures, 5)))
                    continue
                if update is not None:
                    await self._dispatch(update)
                else:
                    # Some API errors/no-event responses return immediately;
                    # do not burn CPU or hammer the service in a tight loop.
                    await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            raise
        except ConfigError as error:
            self._set_fatal_error("zalo_config", str(error), retryable=False)
        except asyncio.TimeoutError:
            self._set_fatal_error("zalo_turn_timeout", "Hermes turn exceeded the phase 1 limit; restart after checking the failed turn.", retryable=False)
        except Exception:
            self._set_fatal_error("zalo_runtime", "Zalo adapter stopped after an internal failure.", retryable=False)
        if self.has_fatal_error:
            await self._notify_fatal_error()

    async def _dispatch(self, update: dict) -> None:
        message = parse_update(update)
        if message is None:
            return
        if len(message.text) > self.settings.max_inbound_chars:
            logger.info("Zalo message ignored: input exceeds configured limit")
            return
        registration_admission = bool(self.work and getattr(self.work, 'registration_admission', lambda message: False)(message))
        registration_blocked = bool(self.work and getattr(self.work, 'registration_blocked', lambda message: False)(message))
        if (not is_allowed(message, self.settings.allowed_users, self.settings.allowed_groups,
                           group_members=self.settings.group_access == "members") and not registration_admission) or registration_blocked:
            logger.info("Zalo message ignored: sender/chat not authorized")
            if self.work:
                self.work.pilot.record("access", "denied")
            return
        if message.chat_type == "GROUP" and not self._groups_supported:
            return
        # Official events do not document Telegram-style mention offsets.
        # Accept the bot's display name or account handle only at the start.
        if message.chat_type == "GROUP" and self._bot_names:
            message = replace(message, text=strip_bot_mention(message.text, self._bot_names))
        assert_chat_only()
        if not self.store.reserve(message.key):
            logger.info("Zalo duplicate update ignored")
            if self.work:
                self.work.pilot.record("command", "duplicate")
            return
        source = self.build_source(
            chat_id=message.chat_id,
            chat_type="dm" if message.chat_type == "PRIVATE" else "group",
            user_id=message.user_id,
            message_id=message.message_id,
        )
        event = MessageEvent(
            text=message.text,
            message_type=MessageType.TEXT,
            source=source,
            user_id=message.user_id,
            message_id=message.message_id,
            allow_gateway_control=False,
            reply_expected=True,
        )
        self._current_event, self._current_outcome = event, None
        self._current_message = message
        self._archive_sequence = 0
        self._reply_chat = message.chat_id
        self._reply_budget = self.settings.max_reply_chars
        self._delivery_failed = False
        permit = {"active": True, "chat_id": message.chat_id, "message_id": message.message_id}
        permit_token = self._send_permit.set(permit)
        started = time.monotonic()
        try:
            self.store.finish(message.key, "admitted")
            if self.conversations:
                self.conversations.record_incoming(message, self._archive_member(message))
                if self.conversations.notice_needed(message):
                    notice = (f'ℹ️ Bot lưu tin bạn gửi cho bot và câu trả lời trong {self.conversations.retention_days} ngày '
                              'để hỗ trợ và kiểm tra lỗi. Quản trị được cấp quyền có thể xem hội thoại '
                              'và chọn thông tin ghi nhớ; thông tin riêng không tự chia sẻ sang nhóm.')
                    delivered_notice = await self.send(message.chat_id, notice, reply_to=message.message_id)
                    if not delivered_notice.success:
                        self.store.finish(message.key, 'failed_uncertain')
                        return
                    self.conversations.mark_notice(message)
            if self.work:
                response = await self.work.handle(message)
                if response is not None:
                    delivered = await self.send(message.chat_id, response, reply_to=message.message_id)
                    self.store.finish(message.key, "completed" if delivered.success else "failed_uncertain")
                    self.work.pilot.record("command", "ok" if delivered.success else "uncertain",
                                           (time.monotonic() - started) * 1000)
                    return
            if (message.chat_type == "GROUP" and message.user_id not in self.settings.allowed_users
                    and self.work is None):
                # WorkService must distinguish ordinary conversation from
                # scoped work reads before a group member reaches Hermes.
                # If that boundary is unavailable, remain closed.
                self.store.finish(message.key, "not_admitted")
                return
            if self.conversations:
                context = self.conversations.prepare_context(message, self._archive_member(message))
                try:
                    response = await self.managed_chat.reply(message, context)
                    is_chat = True
                except ManagedChatError:
                    response = 'Bot chưa kết nối được AI. Bạn vẫn có thể dùng /menu và các lệnh công việc.'
                    is_chat = False
                delivered = await self.send(message.chat_id, response, reply_to=message.message_id)
                self.conversations.complete_turn(message, is_chat=is_chat and delivered.success)
                self.store.finish(message.key, 'completed' if delivered.success else 'failed_uncertain')
                if self.work:
                    self.work.pilot.record('chat', 'ok' if delivered.success and is_chat else 'uncertain',
                                           (time.monotonic() - started) * 1000)
                return
            await self.handle_message(event)
            # handle_message returns before generation. Await the task owned by
            # the base adapter, including its guard cleanup, so a rapid follow-up
            # is never merged/interrupted and only one turn is in memory.
            task = self._session_tasks.get(self._event_session_key(event))
            if task is None or not event._gateway_accepted:
                self.store.finish(message.key, "not_admitted")
                return
            await asyncio.wait_for(asyncio.shield(task), timeout=TURN_TIMEOUT_SECONDS)
            self.store.finish(message.key, self._current_outcome or "unconfirmed")
            if self.work:
                self.work.pilot.record("chat", "ok" if self._current_outcome == "completed" else "uncertain",
                                       (time.monotonic() - started) * 1000)
        except asyncio.CancelledError:
            self.store.finish(message.key, "cancelled_uncertain")
            raise
        except asyncio.TimeoutError:
            self._delivery_failed = True
            self.store.finish(message.key, "timeout_uncertain")
            await self.cancel_background_tasks()
            raise
        except Exception:
            self.store.finish(message.key, "failed_uncertain")
            raise
        finally:
            permit["active"] = False
            self._send_permit.reset(permit_token)
            self._current_event = None
            self._current_message = None
            self._reply_chat = None

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        if event is self._current_event:
            self._current_outcome = "completed" if outcome == ProcessingOutcome.SUCCESS and not self._delivery_failed else "failed_uncertain"

    async def handle_message(self, event: MessageEvent) -> None:
        # Admission is exclusively through _dispatch. Background notifications
        # and crash-resume events must not bypass its auth/dedup boundaries.
        if event is not self._current_event or event.internal:
            event._gateway_accepted = False
            return
        await super().handle_message(event)

    async def _record_delivery_obligation(self, *args, **kwargs):
        # Hermes can otherwise replay a failed/ambiguous final after restart or
        # on a timer. Zalo documents no idempotency token for sendMessage.
        return None

    async def _notify_turn_error(self, event: MessageEvent, error: BaseException):
        # The generic base error includes exception detail; provider failures
        # must not disclose configuration or upstream response bodies in Zalo.
        await self.send(event.source.chat_id,
                        "Xin lỗi, bot chưa xử lý được yêu cầu này. Bạn hãy thử lại sau.",
                        reply_to=event.message_id)
        return None

    async def send(self, chat_id: str, content: str, reply_to=None, metadata=None) -> SendResult:
        # Only the active, authorized inbound turn may produce a reply. Cron,
        # model-initiated sends and unrelated destinations are outside phase 1.
        permit = self._send_permit.get()
        if (not permit or not permit["active"] or permit["chat_id"] != str(chat_id)
                or permit["message_id"] != reply_to
                or self._reply_chat != str(chat_id) or self.client is None or self._delivery_failed
                or self._current_event is None or reply_to != self._current_event.message_id):
            return SendResult(success=False, error="ZALO_SEND_DENIED", retryable=False)
        if not content or self._reply_budget <= 0:
            return SendResult(success=False, error="ZALO_REPLY_LIMIT", retryable=False)
        if self.work and self._current_message and getattr(self.work, 'member_admin_locked', lambda *args: False)(
                self._current_message.user_id, self._current_message.chat_id):
            return SendResult(success=False, error='ZALO_ACCESS_CHANGED', retryable=False)
        text = content[:self._reply_budget]
        if len(text) < len(content):
            suffix = "\n[Da rut gon cau tra loi.]"
            text = text[:max(0, len(text) - len(suffix))] + suffix
        self._reply_budget -= len(text)
        ids = []
        key = None
        chunk = ''
        try:
            for chunk in split_text(text):
                if self.work and self._current_message and getattr(self.work, 'member_admin_locked', lambda *args: False)(
                        self._current_message.user_id, self._current_message.chat_id):
                    return SendResult(success=False, error='ZALO_ACCESS_CHANGED', retryable=False)
                self._archive_sequence += 1
                key = 'reply-' + str(self._archive_sequence)
                result = await self.client.send_text_chunk(str(chat_id), chunk)
                self._archive_outgoing(chunk, 'sent', key)
                message_id = result.get("message_id")
                if message_id is not None:
                    ids.append(str(message_id))
            return SendResult(success=True, message_id=ids[-1] if ids else None, continuation_message_ids=tuple(ids[:-1]))
        except AuthenticationError:
            self._archive_outgoing(chunk, 'failed', key)
            self._delivery_failed = True
            self._set_fatal_error("zalo_auth", "Zalo rejected the token while sending.", retryable=False)
            return SendResult(success=False, error="ZALO_AUTH_REJECTED", retryable=False)
        except (DeliveryUncertain, ZaloAPIError):
            self._archive_outgoing(chunk, 'uncertain', key)
            self._delivery_failed = True
            # Opaque error code avoids Hermes heuristic transport retries, which
            # could duplicate already-delivered earlier chunks.
            logger.warning("Zalo reply incomplete or unconfirmed; automatic resend disabled")
            return SendResult(success=False, error="ZALO_DELIVERY_UNCONFIRMED", retryable=False)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        return None

    # Hermes normally recognizes image URLs and local paths in model prose and
    # may turn them into attachments. Keep all model output plain text here;
    # an untrusted request must never trigger local file discovery or upload.
    @staticmethod
    def extract_media(content: str):
        return [], content

    @staticmethod
    def extract_images(content: str):
        return [], content

    @staticmethod
    def extract_local_files(content: str):
        return [], content

    async def get_chat_info(self, chat_id: str) -> dict:
        return {"name": str(chat_id), "type": "group" if str(chat_id) in self.settings.allowed_groups else "dm"}

    def toolsets_for_source(self, source):
        # Defense in depth only: assert_chat_only checks the effective resolver.
        return []


def check_requirements() -> bool:
    try:
        scoped_settings()
        return True
    except ConfigError:
        return False


def register(ctx) -> None:
    ctx.register_platform(
        name="zalo",
        label="Zalo Bot Platform (official)",
        adapter_factory=ZaloAdapter,
        check_fn=check_requirements,
        validate_config=lambda config: check_requirements(),
        required_env=["ZALO_BOT_TOKEN", "ZALO_ALLOWED_USERS"],
        allowed_users_env="ZALO_ALLOWED_USERS",
        allow_update_command=False,
        trusted_inbound=False,
        display_tier="minimal",
        max_message_length=2000,
        platform_hint=("Reply in Vietnamese unless asked otherwise. Plain text only. Ordinary conversation, writing help and "
                       "general knowledge questions are welcome in private chats and authorized groups. You have no tools. "
                       "Do not infer company-specific facts, a user's roster identity, internal rules or document contents. "
                       "Never claim to read internal documents/calendar, schedule reminders, exchange shifts, send notifications "
                       "or create issues yourself, and never claim a command completed. The application handles work commands "
                       "and supported Vietnamese work requests with their existing permissions. Guide users to /menu, /help lich, "
                       "/help nhac, /help tailieu, /help tickets or /help issue when they need those capabilities. "
                       "Commands include /hoi QUESTION, /tailieu, /lich homnay, /lichcuatoi mai, /nhac, /thongbao, /doica "
                       "and /issue. Never assume a feature is enabled; issue creation and shift exchanges require the application's "
                       "own confirmation flow. For private work details, direct users to their authorized application commands."),
        emoji="💬",
    )
