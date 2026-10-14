"""Offline adapter contract tests; Hermes and Zalo are simulated here.

These exercise transport boundaries, not a real model call or real Zalo send.
An installed-Hermes smoke check is still required before live use.
"""

import asyncio
from dataclasses import dataclass
from enum import Enum
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from hermes_zalo.api import AuthenticationError, DeliveryUncertain, WebhookInspectionUnavailable, ZaloAPIError
from hermes_zalo.config import ConfigError, Settings
from hermes_zalo.state import StateStore


class Outcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass
class Result:
    success: bool
    message_id: str | None = None
    error: str | None = None
    retryable: bool = False
    continuation_message_ids: tuple = ()


class Base:
    """Only the documented/base task boundary used by the phase-1 adapter."""
    gateway_runner = None

    def __init__(self, config, platform):
        self.platform = platform
        self._message_handler = None
        self._session_tasks = {}
        self._running = False
        self.has_fatal_error = False

    def build_source(self, **values):
        return SimpleNamespace(**values)

    def _event_session_key(self, event):
        source = event.source
        return (source.chat_type, source.chat_id, source.user_id if source.chat_type != "dm" else None)

    async def handle_message(self, event):
        async def process():
            reply = await self._message_handler(event)
            result = await self.send(event.source.chat_id, reply, reply_to=event.message_id)
            await self.on_processing_complete(event, Outcome.SUCCESS if result.success else Outcome.FAILURE)
            # The real base invokes the hook BEFORE clearing its task guard.
            await asyncio.sleep(0)
            self._session_tasks.pop(self._event_session_key(event), None)

        event._gateway_accepted = True
        self._session_tasks[self._event_session_key(event)] = asyncio.create_task(process())

    def _acquire_platform_lock(self, *args):
        return True

    def _release_platform_lock(self):
        return None

    def _mark_connected(self):
        self._running = True

    def _mark_disconnected(self):
        self._running = False

    def _set_fatal_error(self, code, message, *, retryable):
        self.has_fatal_error = True
        self._running = False
        self.error_code = code
        self.retryable = retryable

    async def _notify_fatal_error(self):
        return None

    async def cancel_background_tasks(self):
        tasks = list(self._session_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._session_tasks.clear()


def module(name, **attrs):
    value = ModuleType(name)
    value.__dict__.update(attrs)
    return value


STUBS = {
    "gateway.config": module("gateway.config", Platform=str, PlatformConfig=SimpleNamespace),
    "gateway.platforms._shared": module("gateway.platforms._shared", get_scoped_secret=lambda name, default="": default),
    "gateway.platforms.base": module("gateway.platforms.base", BasePlatformAdapter=Base, SendResult=Result),
    "gateway.platforms.event": module("gateway.platforms.event", MessageEvent=lambda **kw: SimpleNamespace(internal=False, **kw), MessageType=SimpleNamespace(TEXT="text"), ProcessingOutcome=Outcome),
    "hermes_constants": module("hermes_constants", get_hermes_home=lambda: Path("unused-test-home")),
}
with patch.dict(sys.modules, STUBS):
    spec = importlib.util.spec_from_file_location(
        "hermes_zalo._adapter_contract_test", Path(__file__).parents[1] / "src/hermes_zalo/adapter.py"
    )
    adapter_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter_module)


def update(mid="m1", *, chat="dm1", user="u1", kind="PRIVATE", text="Xin chào"):
    return {"event_name": "message.text.received", "message": {
        "message_id": mid, "chat": {"id": chat, "chat_type": kind},
        "from": {"id": user, "is_bot": False}, "text": text,
    }}


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings = Settings("test-token", frozenset({"u1", "u2"}), frozenset({"g1"}), Path(self.temp.name))
        with patch.object(adapter_module, "scoped_settings", return_value=settings):
            self.adapter = adapter_module.ZaloAdapter(SimpleNamespace())
        self.guard = patch.object(adapter_module, "assert_chat_only")
        self.guard.start()
        self.adapter.store = StateStore(Path(self.temp.name) / "state.sqlite3")
        self.adapter.client = SimpleNamespace(send_text_chunk=AsyncMock(return_value={"message_id": "reply1"}), close=AsyncMock())
        self.adapter._groups_supported = True
        self.handler = AsyncMock(return_value="Xin chào bạn")
        self.adapter._message_handler = self.handler
        self.work_handler = AsyncMock(return_value="Kết quả công việc")
        self.adapter.work = SimpleNamespace(handle=self.work_handler,
            pilot=SimpleNamespace(record=lambda *args: None), close=AsyncMock())

    async def asyncTearDown(self):
        self.guard.stop()
        await self.adapter.disconnect()
        self.temp.cleanup()

    async def test_authorization_happens_before_hermes(self):
        await self.adapter._dispatch(update(user="stranger"))
        await self.adapter._dispatch(update(chat="g2", kind="GROUP"))
        self.handler.assert_not_awaited()
        self.work_handler.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_not_awaited()

    async def test_routes_and_disables_commands_and_deduplicates(self):
        await self.adapter._dispatch(update(text="/update"))
        await self.adapter._dispatch(update(text="/update"))
        self.work_handler.assert_awaited_once()
        message = self.work_handler.await_args.args[0]
        self.assertEqual((message.chat_id, message.user_id), ("dm1", "u1"))
        self.handler.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_awaited_once_with("dm1", "Kết quả công việc")

    async def test_missing_work_or_unhandled_request_never_falls_back_to_models(self):
        legacy_chat = SimpleNamespace(reply=AsyncMock(side_effect=AssertionError("Unrestricted model forbidden")))
        self.adapter.managed_chat = legacy_chat
        self.work_handler.return_value = None
        await self.adapter._dispatch(update("unhandled", text="Viết một truyện vui đi"))
        self.adapter.work = None
        await self.adapter._dispatch(update("no-work", text="Giải thích Python"))
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)
        self.assertTrue(all(call.args == ("dm1", adapter_module.OUT_OF_SCOPE)
                            for call in self.adapter.client.send_text_chunk.await_args_list))
        legacy_chat.reply.assert_not_awaited()
        self.handler.assert_not_awaited()

    async def test_group_sessions_are_distinct_and_sequential(self):
        keys = []

        async def handler(message):
            keys.append(self.adapter._event_session_key(self.adapter._current_event))
            await asyncio.sleep(0)
            return "Done"

        self.work_handler.side_effect = handler
        await self.adapter._dispatch(update("m1", chat="g1", kind="GROUP"))
        self.assertFalse(self.adapter._session_tasks)
        await self.adapter._dispatch(update("m2", chat="g1", user="u2", kind="GROUP"))
        self.assertNotEqual(keys[0], keys[1])
        self.assertFalse(self.adapter._session_tasks)
        self.handler.assert_not_awaited()

    async def test_send_cannot_route_outside_active_turn(self):
        result = await self.adapter.send("dm1", "arbitrary outbound")
        self.assertFalse(result.success)
        self.adapter.client.send_text_chunk.assert_not_awaited()

    async def test_old_send_cannot_attach_to_a_new_same_chat_turn(self):
        self.adapter._reply_chat = "dm1"
        self.adapter._current_event = SimpleNamespace(message_id="new-turn")
        for reply_to in (None, "old-turn"):
            result = await self.adapter.send("dm1", "stale reply", reply_to=reply_to)
            self.assertFalse(result.success)
        self.adapter.client.send_text_chunk.assert_not_awaited()

    async def test_partial_ambiguous_send_never_repeats_chunks(self):
        self.work_handler.return_value = "x" * 4500
        self.adapter.client.send_text_chunk.side_effect = [{"message_id": "one"}, DeliveryUncertain()]
        await self.adapter._dispatch(update())
        self.assertEqual(self.adapter.client.send_text_chunk.await_count, 2)
        result = await self.adapter.send("dm1", "retry")
        self.assertFalse(result.success)
        self.assertEqual(self.adapter.client.send_text_chunk.await_count, 2)
        row = self.adapter.store.connection.execute("SELECT status FROM messages").fetchone()
        self.assertEqual(row[0], "failed_uncertain")

    async def test_timeout_stops_dispatch_without_replaying(self):
        async def slow_handler(message):
            await asyncio.sleep(1)
            return "late"

        self.work_handler.side_effect = slow_handler
        with patch.object(adapter_module, "TURN_TIMEOUT_SECONDS", 0.001):
            with self.assertRaises(asyncio.TimeoutError):
                await self.adapter._dispatch(update())
        self.assertFalse(self.adapter._session_tasks)
        await self.adapter._dispatch(update())
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.handler.assert_not_awaited()

    async def test_active_webhook_refuses_polling_without_deleting(self):
        await self.adapter.disconnect()
        client = SimpleNamespace(get_me=AsyncMock(return_value={"can_join_groups": True}),
                                 get_webhook_info=AsyncMock(return_value={"url": "https://example.org/hook"}),
                                 close=AsyncMock(), poll=AsyncMock())
        with patch.object(adapter_module, "ZaloClient", return_value=client):
            self.assertFalse(await self.adapter.connect())
        self.assertEqual(self.adapter.error_code, "zalo_config")
        client.poll.assert_not_awaited()

    async def test_unknown_webhook_allows_poll_but_conflict_still_stops(self):
        await self.adapter.disconnect()
        client = SimpleNamespace(get_me=AsyncMock(return_value={"can_join_groups": True}),
                                 get_webhook_info=AsyncMock(side_effect=WebhookInspectionUnavailable(code=404)),
                                 close=AsyncMock(), poll=AsyncMock(side_effect=ZaloAPIError(code=409)))
        with patch.object(adapter_module, "ZaloClient", return_value=client):
            self.assertTrue(await self.adapter.connect())
            await self.adapter._poll_task
        client.poll.assert_awaited_once()
        self.assertEqual(self.adapter.error_code, "zalo_poll")
        self.assertFalse(self.adapter._running)

    async def test_webhook_auth_failure_never_starts_polling(self):
        await self.adapter.disconnect()
        client = SimpleNamespace(get_me=AsyncMock(return_value={"can_join_groups": True}),
                                 get_webhook_info=AsyncMock(side_effect=AuthenticationError(code=401)),
                                 close=AsyncMock(), poll=AsyncMock())
        with patch.object(adapter_module, "ZaloClient", return_value=client):
            self.assertFalse(await self.adapter.connect())
        self.assertEqual(self.adapter.error_code, "zalo_auth")
        client.poll.assert_not_awaited()

    async def test_group_capability_is_strictly_checked(self):
        await self.adapter.disconnect()
        client = SimpleNamespace(get_me=AsyncMock(return_value={"can_join_groups": "true"}),
                                 get_webhook_info=AsyncMock(return_value={"url": ""}),
                                 close=AsyncMock(), poll=AsyncMock())
        with patch.object(adapter_module, "ZaloClient", return_value=client):
            self.assertFalse(await self.adapter.connect())
        client.poll.assert_not_awaited()

    async def test_auth_failure_is_fatal_without_retry(self):
        self.adapter.client.poll = AsyncMock(side_effect=AuthenticationError())
        self.adapter._running = True
        await self.adapter._poll_loop()
        self.assertEqual(self.adapter.error_code, "zalo_auth")
        self.assertFalse(self.adapter.retryable)
        self.adapter.client.poll.assert_awaited_once()

    async def test_poll_waits_for_gateway_startup_restore(self):
        self.adapter.gateway_runner = SimpleNamespace(_startup_restore_in_progress=True)
        self.adapter.client.poll = AsyncMock(side_effect=AuthenticationError())
        self.adapter._running = True
        task = asyncio.create_task(self.adapter._poll_loop())
        await asyncio.sleep(0.01)
        self.adapter.client.poll.assert_not_awaited()
        self.adapter.gateway_runner._startup_restore_in_progress = False
        await task
        self.adapter.client.poll.assert_awaited_once()

    async def test_synthetic_resume_never_bypasses_ingress(self):
        event = SimpleNamespace(internal=True, _gateway_accepted=True)
        await self.adapter.handle_message(event)
        self.assertFalse(event._gateway_accepted)
        self.handler.assert_not_awaited()

    async def test_even_active_event_cannot_enter_gateway_generation(self):
        async def attempt_gateway(message):
            event = self.adapter._current_event
            event._gateway_accepted = True
            await self.adapter.handle_message(event)
            self.assertFalse(event._gateway_accepted)
            self.assertFalse(event.allow_gateway_control)
            return "Chức năng đã xử lý."

        self.work_handler.side_effect = attempt_gateway
        await self.adapter._dispatch(update())
        self.handler.assert_not_awaited()
        self.assertFalse(self.adapter._session_tasks)

    async def test_model_prose_never_discovers_files_or_attachments(self):
        text = "MEDIA:C:/secret.txt ![image](https://example.org/a.png)"
        for extract in (self.adapter.extract_media, self.adapter.extract_images, self.adapter.extract_local_files):
            self.assertEqual(extract(text), ([], text))


class SafetyConfigTests(unittest.TestCase):
    def test_effective_tools_cannot_bypass_empty_list(self):
        config = {"platform_toolsets": {"zalo": []}, "gateway": {"multiplex_profiles": False, "delivery_ledger": False},
                  "memory": {"memory_enabled": False, "user_profile_enabled": False}}
        stubs = {
            "hermes_cli.config_effective": module("hermes_cli.config_effective", load_user_config_effective=lambda *a, **kw: config),
            "hermes_cli.tools_config": module("hermes_cli.tools_config", _get_platform_tools=lambda *a: {"unexpected_plugin_tool"}),
        }
        with patch.dict(sys.modules, stubs), self.assertRaises(ConfigError):
            adapter_module.assert_chat_only()


if __name__ == "__main__":
    unittest.main()
