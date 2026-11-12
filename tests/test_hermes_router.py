"""Opt-in: the router middleware inside installed, unmodified Hermes.

Loads the copied plugin with Hermes' own PluginManager and sends requests
through Hermes' real ``llm_request`` middleware chain and hook dispatchers.
No network, model or messaging platform is used.
"""

import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.getenv("HERMES_INTEGRATION_TESTS") == "1", "Requires installed Hermes runtime")
class RealHermesRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="assistant-router-smoke-")
        cls.home = Path(cls.temp.name)
        cls.env = patch.dict(os.environ, {
            "HERMES_HOME": str(cls.home), "ASSISTANT_ROUTER_POLICY": "rule",
            "ASSISTANT_ROUTER_STRONG_MODEL": "big-1", "ASSISTANT_ROUTER_CHEAP_MODEL": "small-1",
            "ASSISTANT_ROUTER_CHEAP_REQUEST": '{"reasoning": {"effort": "low"}}'})
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

    def call(self, number, session="s1"):
        """One model call of a turn through Hermes' middleware, then the hook Hermes fires after it."""
        from hermes_cli.lifecycle import invoke_hook
        from hermes_cli.middleware import apply_llm_request_middleware

        ids = dict(task_id="", turn_id="t1", api_request_id=f"{session}:t1:api:{number}", session_id=session,
                   platform="api_server", model="big-1", provider="smoke", base_url="", api_mode="chat_completions",
                   api_call_count=number)
        request = {"model": "big-1", "messages": [{"role": "user", "content": "hi"}], "reasoning": {"effort": "high"}}
        result = apply_llm_request_middleware(request, **ids)
        invoke_hook("post_api_request", **ids, api_duration=0.5, started_at=1.0,
                    usage={"input_tokens": 10, "output_tokens": 2})
        self.assertEqual("big-1", request["model"])  # Hermes hands the middleware a copy
        return result

    def test_middleware_is_registered(self):
        from hermes_cli.plugins import has_middleware

        self.assertTrue(has_middleware("llm_request"))

    def test_real_middleware_chain_reroutes_steps_and_telemetry_records_them(self):
        from hermes_cli.lifecycle import invoke_hook

        first = self.call(1)
        self.assertFalse(first.changed)                       # rule: the first step stays on the strong model
        self.assertEqual("big-1", first.payload["model"])
        second = self.call(2)
        self.assertTrue(second.changed)
        self.assertEqual(("small-1", {"effort": "low"}), (second.payload["model"], second.payload["reasoning"]))
        self.assertEqual("assistant-core-router", second.trace[0]["source"])
        self.assertEqual("cheap: routine step", second.trace[0]["reason"])
        invoke_hook("post_tool_call", tool_name="execute_code", args={}, result='{"error": "boom"}',
                    task_id="", session_id="s1", tool_call_id="c1", turn_id="t1", api_request_id="s1:t1:api:2",
                    duration_ms=5, status="error", error_type="tool_error", error_message="boom",
                    middleware_trace=[])
        third = self.call(3)
        self.assertEqual("big-1", third.payload["model"])     # escalated after the failed tool call
        db = sqlite3.connect(self.home / "assistant-core" / "telemetry.sqlite3")
        try:
            rows = db.execute("SELECT model, default_model, route_choice, route_reason FROM api_calls "
                              "WHERE session_id='s1' ORDER BY id").fetchall()
            policy = db.execute("SELECT policy FROM router_runs").fetchone()[0]
        finally:
            db.close()
        self.assertEqual([("big-1", "big-1", "strong", "first step of the turn"),
                          ("small-1", "big-1", "cheap", "routine step"),
                          ("big-1", "big-1", "strong", "tool error in the previous step")], rows)
        self.assertEqual("rule", policy)


if __name__ == "__main__":
    unittest.main()
