"""Opt-in smoke tests using installed, unmodified Hermes classes and loader.

Set HERMES_INTEGRATION_TESTS=1 and put the Hermes source/runtime and this
project's src on PYTHONPATH. Only the model handler and Zalo network are mocked.
"""

import asyncio
import os
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


@unittest.skipUnless(os.getenv("HERMES_INTEGRATION_TESTS") == "1", "Requires installed Hermes runtime")
class RealHermesTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="zalo-hermes-smoke-")
        cls.home = Path(cls.temp.name)
        cls.env = patch.dict(os.environ, {
            "HERMES_HOME": str(cls.home), "ZALO_BOT_TOKEN": "smoke-test-token",
            "ZALO_ALLOWED_USERS": "u1,u2", "ZALO_ALLOWED_GROUPS": "g1",
            "ZALO_STATE_DIR": str(cls.home / "state"),
        })
        cls.env.start()
        root = Path(__file__).parents[1]
        cls.home.joinpath("config.yaml").write_text(
            "plugins:\n  enabled: [zalo-platform]\nplatform_toolsets:\n  zalo: []\n"
            "gateway:\n  multiplex_profiles: false\n  group_sessions_per_user: true\n  delivery_ledger: false\n"
            "  platforms:\n    zalo:\n      enabled: true\n"
            "memory:\n  memory_enabled: false\n  user_profile_enabled: false\n", encoding="utf-8")
        plugin_dir = cls.home / "plugins" / "zalo-platform"
        shutil.copytree(root / "plugin", plugin_dir)
        shutil.copytree(root / "src" / "hermes_zalo", plugin_dir / "hermes_zalo", ignore=shutil.ignore_patterns("__pycache__"))
        from hermes_cli.plugins import PluginManager
        from gateway.platform_registry import platform_registry

        cls.manager = PluginManager(scope_key=str(cls.home))
        cls.manager.discover_and_load()
        cls.entry = platform_registry.get("zalo")
        if cls.entry is None:
            raise AssertionError("Hermes failed to discover and register the official Zalo plugin")

    @classmethod
    def tearDownClass(cls):
        from hermes_state_registry import close_all_under

        cls.manager.unload()
        close_all_under(cls.home)
        # Windows can still be retiring a Hermes file writer after its task
        # completed. Retry that cleanup briefly; do not mask a persistent error.
        try:
            for attempt in range(3):
                try:
                    cls.temp.cleanup()
                    break
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(0.2)
        finally:
            cls.env.stop()

    async def asyncSetUp(self):
        from gateway.config import PlatformConfig

        self.adapter = self.entry.adapter_factory(PlatformConfig(enabled=True, extra={"group_sessions_per_user": True}))
        # Use the plugin's own StateStore class; no replacement of Hermes base.
        import importlib
        self.module = importlib.import_module(self.adapter.__class__.__module__)
        self.adapter.store = self.module.StateStore(self.home / (self._testMethodName + ".sqlite3"))
        self.adapter.client = SimpleNamespace(send_text_chunk=AsyncMock(return_value={"message_id": "r1"}), close=AsyncMock())
        self.adapter._groups_supported = True
        self.handler = AsyncMock(return_value="Xin chào từ Hermes")
        self.adapter.set_message_handler(self.handler)

    async def asyncTearDown(self):
        await self.adapter.disconnect()

    @staticmethod
    def update(mid, user="u1", chat="g1", kind="GROUP", text="xin chào"):
        return {"event_name": "message.text.received", "message": {
            "message_id": mid, "from": {"id": user, "is_bot": False},
            "chat": {"id": chat, "chat_type": kind}, "text": text,
        }}

    async def test_real_loader_and_model_tools_disabled(self):
        from model_tools import _select_tool_names

        self.assertFalse(self.adapter.__abstractmethods__)
        self.assertEqual(self.entry.allowed_users_env, "ZALO_ALLOWED_USERS")
        self.assertFalse(self.entry.trusted_inbound)
        self.assertFalse(self.entry.allow_update_command)
        self.module.assert_chat_only()
        self.assertEqual(_select_tool_names([], None, True), set())
        self.assertIsNone(await self.adapter._record_delivery_obligation(None, None, None, None, False))

    async def test_actual_base_routes_completes_and_deduplicates(self):
        await self.adapter._dispatch(self.update("m1", text="/update"))
        await self.adapter._dispatch(self.update("m1", text="/update"))
        self.handler.assert_awaited_once()
        event = self.handler.await_args.args[0]
        self.assertFalse(event.is_command())
        self.assertFalse(event.allow_gateway_control)
        self.assertEqual(event.source.user_id, "u1")
        self.adapter.client.send_text_chunk.assert_awaited_once_with("g1", "Xin chào từ Hermes")
        self.assertFalse(self.adapter._session_tasks)
        status = self.adapter.store.connection.execute("SELECT status FROM messages").fetchone()[0]
        self.assertEqual(status, "completed")

    async def test_actual_hermes_session_keys_separate_users_and_dms(self):
        from gateway.session import build_session_key

        await self.adapter._dispatch(self.update("m1", user="u1"))
        await self.adapter._dispatch(self.update("m2", user="u2"))
        await self.adapter._dispatch(self.update("m3", user="u1", chat="dm1", kind="PRIVATE"))
        events = [call.args[0] for call in self.handler.await_args_list]
        keys = [build_session_key(event.source, group_sessions_per_user=True) for event in events]
        self.assertEqual(len(set(keys)), 3)
        self.assertFalse(self.adapter._session_tasks)

    async def test_old_task_has_no_send_permission_during_new_same_chat_turn(self):
        release = asyncio.Event()
        stale_tasks = []

        async def stale_send():
            await release.wait()
            return await self.adapter.send("g1", "old background reply", reply_to="m2")

        async def handler(event):
            if event.message_id == "m1":
                stale_tasks.append(asyncio.create_task(stale_send()))
            else:
                release.set()
                result = await stale_tasks[0]
                self.assertFalse(result.success)
            return "Current reply"

        self.adapter.set_message_handler(handler)
        await self.adapter._dispatch(self.update("m1"))
        await self.adapter._dispatch(self.update("m2"))
        self.assertEqual(self.adapter.client.send_text_chunk.await_count, 2)
        self.assertTrue(all(call.args == ("g1", "Current reply")
                            for call in self.adapter.client.send_text_chunk.await_args_list))

    async def test_native_plugin_work_command_does_not_invoke_model(self):
        import importlib
        work_module = importlib.import_module(self.adapter.__class__.__module__.rsplit(".", 1)[0] + ".work")
        self.adapter.work = work_module.WorkService(self.adapter.settings, environ={})
        await self.adapter._dispatch(self.update("work-1", text="/nhac sau 1 phut | Nhắc thử"))
        self.handler.assert_not_awaited()
        reminders = self.adapter.work.reminders.list_for("u1", "g1", "GROUP")
        self.assertEqual(len(reminders), 1)
        self.assertIn("Đã đặt nhắc", self.adapter.client.send_text_chunk.await_args.args[1])
        self.adapter.client.send_text_chunk.reset_mock()
        await self.adapter._deliver_due_reminders(now=reminders[0].due_at + 1)
        self.adapter.client.send_text_chunk.assert_awaited_once()
        self.assertEqual("g1", self.adapter.client.send_text_chunk.await_args.args[0])
        self.assertIn("Nhắc thử", self.adapter.client.send_text_chunk.await_args.args[1])

    async def test_native_group_member_work_read_never_falls_through_to_model(self):
        import importlib
        from dataclasses import replace
        work_module = importlib.import_module(self.adapter.__class__.__module__.rsplit(".", 1)[0] + ".work")
        self.adapter.settings = replace(self.adapter.settings, group_access="members")
        self.adapter.work = work_module.WorkService(self.adapter.settings, environ={})
        self.adapter._bot_names = ("Bot LabCoach", "bot.synthetic-handle")
        await self.adapter._dispatch(self.update("group-1", user="new-member", text="@Bot LabCoach /menu"))
        self.assertIn("MENU BOT", self.adapter.client.send_text_chunk.await_args.args[1])
        await self.adapter._dispatch(self.update("group-2", user="new-member", text="Ngày mai mình có lịch gì?"))
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)
        self.assertTrue(all(c.args[0] == "g1" for c in self.adapter.client.send_text_chunk.await_args_list))
        self.handler.assert_not_awaited()
        await self.adapter._dispatch(self.update("group-3", user="new-member", chat="not-approved", text="/menu"))
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)


if __name__ == "__main__":
    unittest.main()
