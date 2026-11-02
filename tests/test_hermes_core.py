"""Opt-in: the assistant-core plugin inside installed, unmodified Hermes.

Loads the copied plugin with Hermes' own PluginManager and drives the real
hook dispatchers. No network, model or messaging platform is used.
"""

import asyncio
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


@unittest.skipUnless(os.getenv("HERMES_INTEGRATION_TESTS") == "1", "Requires installed Hermes runtime")
class RealHermesCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="assistant-core-smoke-")
        cls.home = Path(cls.temp.name)
        cls.env = patch.dict(os.environ, {"HERMES_HOME": str(cls.home), "ASSISTANT_USER_DAILY_LIMIT": "1"})
        cls.env.start()
        root = Path(__file__).parents[1]
        cls.home.joinpath("config.yaml").write_text(
            "plugins:\n  enabled: [assistant-core]\n"
            "platform_toolsets:\n  telegram: [web, vision]\n"
            "memory:\n  memory_enabled: false\n  user_profile_enabled: false\n", encoding="utf-8")
        plugin_dir = cls.home / "plugins" / "assistant-core"
        shutil.copytree(root / "plugins" / "assistant-core", plugin_dir)
        shutil.copytree(root / "src" / "assistant_core", plugin_dir / "assistant_core",
                        ignore=shutil.ignore_patterns("__pycache__"))
        from hermes_cli.plugins import PluginManager

        cls.manager = PluginManager(scope_key=str(cls.home))
        cls.manager.discover_and_load()

    @classmethod
    def tearDownClass(cls):
        from hermes_state_registry import close_all_under

        cls.manager.unload()
        close_all_under(cls.home)
        cls.temp.cleanup()
        cls.env.stop()

    def test_hooks_are_registered(self):
        from hermes_cli.lifecycle import has_hook

        for name in ("pre_gateway_dispatch", "pre_tool_call", "post_api_request", "post_tool_call"):
            self.assertTrue(has_hook(name), name)

    def test_real_pre_tool_call_dispatch_blocks_unscoped_tools(self):
        from hermes_cli.plugins import _dispatch_pre_tool_call_hooks

        block, _ = _dispatch_pre_tool_call_hooks("session_search", {"query": "x"}, session_id="s1")
        self.assertIn("session_search", block or "")
        allowed, _ = _dispatch_pre_tool_call_hooks("web_search", {"query": "x"}, session_id="s1")
        self.assertIsNone(allowed)

    def test_real_api_hooks_write_telemetry(self):
        from hermes_cli.lifecycle import invoke_hook

        invoke_hook("post_api_request", api_request_id="smoke-1", session_id="s1", platform="telegram",
                    model="smoke/model", api_duration=0.5, started_at=1.0,
                    usage={"input_tokens": 10, "output_tokens": 2})
        db = sqlite3.connect(self.home / "assistant-core" / "telemetry.sqlite3")
        try:
            row = db.execute("SELECT model, input_tokens FROM api_calls WHERE api_request_id='smoke-1'").fetchone()
        finally:
            db.close()
        self.assertEqual(row, ("smoke/model", 10))

    def test_real_gateway_dispatch_hook_enforces_quota(self):
        from hermes_cli.lifecycle import ainvoke_hook
        from gateway.config import Platform

        source = SimpleNamespace(platform=Platform("telegram"), user_id="7", chat_id="7")
        adapter = SimpleNamespace(send=AsyncMock())
        gateway = SimpleNamespace(_is_user_authorized_for_source=lambda s: True,
                                  _delivery_adapter_for=lambda s: adapter)

        def run(mid):
            event = SimpleNamespace(text="xin chào", source=source, message_id=mid, internal=False)
            return asyncio.run(ainvoke_hook("pre_gateway_dispatch", event=event, gateway=gateway, session_store=None))

        self.assertFalse([r for r in run("q1") if r])
        second = [r for r in run("q2") if r]
        self.assertEqual(second[0]["action"], "skip")
        adapter.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
