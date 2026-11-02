"""Opt-in assistant-mode smoke tests with installed, unmodified Hermes classes.

A free-text Zalo message must pass the real BasePlatformAdapter session
machinery to the gateway handler, and its reply must reach the Zalo client.
The model and Zalo network are simulated; nothing is sent.
"""

import asyncio
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import io

def load_yaml(path):
    from ruamel.yaml import YAML  # Hermes' YAML library; PyYAML is bootstrap-only
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


@unittest.skipUnless(os.getenv("HERMES_INTEGRATION_TESTS") == "1", "Requires installed Hermes runtime")
class RealHermesAssistantTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="zalo-hermes-assistant-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.home = Path(cls.temp.name)
        cls.env = patch.dict(os.environ, {
            "HERMES_HOME": str(cls.home), "ZALO_BOT_TOKEN": "smoke-test-token",
            "ZALO_ALLOWED_USERS": "u1", "ZALO_ALLOWED_GROUPS": "",
            "ZALO_STATE_DIR": str(cls.home / "state"), "ZALO_BOT_MODE": "assistant",
        })
        cls.env.start()
        # tearDownClass is not called if runtime/plugin setup below fails.
        cls.addClassCleanup(cls.env.stop)
        root = Path(__file__).parents[1]
        config = load_yaml(root / "config" / "hermes.config.example.yaml")
        for key, value in load_yaml(root / "config" / "hermes.assistant.example.yaml").items():
            config.setdefault(key, {}).update(value)
        cls.config = config
        cls.write_config(config)
        plugin_dir = cls.home / "plugins" / "zalo-platform"
        shutil.copytree(root / "plugin", plugin_dir)
        shutil.copytree(root / "src" / "hermes_zalo", plugin_dir / "hermes_zalo",
                        ignore=shutil.ignore_patterns("__pycache__"))
        from hermes_cli.plugins import PluginManager
        from gateway.platform_registry import platform_registry

        cls.manager = PluginManager(scope_key=str(cls.home))
        cls.manager.discover_and_load()
        cls.entry = platform_registry.get("zalo")
        if cls.entry is None:
            raise AssertionError("Hermes failed to register the Zalo plugin")

    @classmethod
    def write_config(cls, config):
        from ruamel.yaml import YAML
        buffer = io.StringIO()
        YAML(typ="safe").dump(config, buffer)
        cls.home.joinpath("config.yaml").write_text(buffer.getvalue(), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        from hermes_state_registry import close_all_under

        cls.manager.unload()
        close_all_under(cls.home)

    async def asyncSetUp(self):
        from gateway.config import PlatformConfig
        import importlib

        self.adapter = self.entry.adapter_factory(PlatformConfig(enabled=True, extra={"group_sessions_per_user": True}))
        self.module = importlib.import_module(self.adapter.__class__.__module__)
        self.adapter.store = self.module.StateStore(self.home / (self._testMethodName + ".sqlite3"))
        self.adapter.client = SimpleNamespace(
            send_text_chunk=AsyncMock(return_value={"message_id": "r1"}),
            send_photo=AsyncMock(return_value={"message_id": "p1"}),
            send_chat_action=AsyncMock(), close=AsyncMock())
        self.adapter._groups_supported = True
        self.handler = AsyncMock(return_value="Chào bạn từ Hermes")
        self.adapter.set_message_handler(self.handler)
        self.work_handler = AsyncMock(return_value="Kết quả lệnh Lab")
        self.adapter.work = SimpleNamespace(
            handle=self.work_handler,
            owns_turn=AsyncMock(side_effect=lambda message: message.text.startswith("/")),
            member_admin_locked=lambda *args: False,
            background_registration_blocked=lambda *args: False,
            member_feature_allowed=lambda *args: False,
            pilot=SimpleNamespace(record=lambda *args: None), close=AsyncMock())

    async def asyncTearDown(self):
        await self.adapter.disconnect()

    @staticmethod
    def update(mid, text, user="u1", chat="dm1"):
        return {"event_name": "message.text.received", "message": {
            "message_id": mid, "from": {"id": user, "is_bot": False},
            "chat": {"id": chat, "chat_type": "PRIVATE"}, "text": text,
        }}

    async def wait_idle(self):
        for _ in range(200):
            if not self.adapter._active_sessions and not self.adapter._background_tasks:
                return
            await asyncio.sleep(0.02)

    async def test_config_guard_accepts_overlay_and_rejects_host_toolsets(self):
        self.module.assert_chat_only(True)
        with self.assertRaises(self.module.ConfigError):
            self.module.assert_chat_only(False)
        unsafe = dict(self.config, platform_toolsets={"zalo": ["web", "computer_use"]})
        self.write_config(unsafe)
        try:
            with self.assertRaises(self.module.ConfigError):
                self.module.assert_chat_only(True)
        finally:
            self.write_config(self.config)

    async def test_free_text_runs_through_real_gateway_base(self):
        await self.adapter._dispatch(self.update("m1", "Tóm tắt tin AI hôm nay"))
        await self.wait_idle()
        self.handler.assert_awaited_once()
        event = self.handler.await_args.args[0]
        self.assertEqual(event.source.chat_id, "dm1")
        self.assertFalse(event.allow_gateway_control)
        self.adapter.client.send_text_chunk.assert_awaited_with("dm1", "Chào bạn từ Hermes")
        self.work_handler.assert_not_awaited()
        self.assertIsNone(self.adapter.toolsets_for_source(event.source))

    async def test_commands_never_reach_the_model(self):
        await self.adapter._dispatch(self.update("m2", "/lich homnay"))
        await self.wait_idle()
        self.work_handler.assert_awaited_once()
        self.handler.assert_not_awaited()

    async def test_stranger_is_dropped(self):
        await self.adapter._dispatch(self.update("m3", "Xin chào", user="stranger", chat="dm9"))
        await self.wait_idle()
        self.handler.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
