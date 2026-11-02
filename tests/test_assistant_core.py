"""assistant-core: quota, tool policy and telemetry, independent of any channel."""

import asyncio
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from assistant_core.hermes_plugin import AssistantCore, unsafe_toolsets
from assistant_core.quota import VIETNAM_TZ, QuotaExceeded, QuotaStore, person_key
from assistant_core.settings import CoreSettings, SettingsError
from assistant_core.telemetry import TelemetryStore


SAFE_CONFIG = {"platform_toolsets": {"telegram": ["web", "vision"], "zalo": ["web"]}}


def event(text="Xin chào", user="42", chat="42", message_id="m1", platform="telegram", internal=False):
    source = SimpleNamespace(platform=SimpleNamespace(value=platform), user_id=user, chat_id=chat)
    return SimpleNamespace(text=text, source=source, message_id=message_id, internal=internal)


class FakeGateway:
    def __init__(self, authorized=True):
        self.adapter = SimpleNamespace(send=AsyncMock())
        self.authorized = authorized

    def _is_user_authorized_for_source(self, source):
        return self.authorized

    def _delivery_adapter_for(self, source):
        return self.adapter


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 10, 31, 23, 59, tzinfo=VIETNAM_TZ)
        self.store = QuotaStore(Path(self.temp.name) / "q.sqlite3", user_daily_limit=2,
                                total_daily_limit=3, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_user_limit_duplicates_and_daily_reset(self):
        alice = person_key("telegram", "1")
        self.assertTrue(self.store.reserve(alice, "a"))
        self.assertFalse(self.store.reserve(alice, "a"))  # redelivered message counts once
        self.assertTrue(self.store.reserve(alice, "b"))
        with self.assertRaises(QuotaExceeded) as caught:
            self.store.reserve(alice, "c")
        self.assertEqual(caught.exception.scope, "user")
        self.now = datetime(2026, 11, 1, 0, 0, tzinfo=VIETNAM_TZ)
        self.assertTrue(self.store.reserve(alice, "c"))

    def test_total_limit_spans_people_and_platforms(self):
        self.store.reserve(person_key("telegram", "1"), "a")
        self.store.reserve(person_key("zalo", "1"), "b")
        self.store.reserve(person_key("telegram", "2"), "c")
        with self.assertRaises(QuotaExceeded) as caught:
            self.store.reserve(person_key("telegram", "3"), "d")
        self.assertEqual(caught.exception.scope, "total")

    def test_no_identifiers_are_stored_in_clear(self):
        self.store.reserve(person_key("telegram", "123456789"), "telegram:chat:secret-message")
        dump = "\n".join(self.store.db.iterdump())
        self.assertNotIn("123456789", dump)
        self.assertNotIn("secret-message", dump)


class SettingsTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        settings = CoreSettings.from_mapping({}, default_dir=Path("/tmp/x"))
        self.assertEqual(settings.user_daily_limit, 30)
        self.assertIn("session_search", settings.blocked_tools)
        self.assertIn("skill_manage", settings.blocked_tools)
        with self.assertRaises(SettingsError):
            CoreSettings.from_mapping({"ASSISTANT_USER_DAILY_LIMIT": "0"}, default_dir=Path("/tmp/x"))
        custom = CoreSettings.from_mapping({"ASSISTANT_BLOCKED_TOOLS": "terminal, browser_navigate"},
                                           default_dir=Path("/tmp/x"))
        self.assertEqual(custom.blocked_tools, frozenset({"terminal", "browser_navigate"}))

    def test_unsafe_toolsets_detected_per_platform(self):
        self.assertEqual(unsafe_toolsets(SAFE_CONFIG), [])
        self.assertEqual(unsafe_toolsets({"platform_toolsets": {"telegram": ["web", "computer_use"]}}), ["telegram"])


class CoreHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = CoreSettings(Path(self.temp.name), user_daily_limit=2, total_daily_limit=100)
        self.core = AssistantCore(self.settings, config=SAFE_CONFIG, config_text=b"cfg")
        self.gateway = FakeGateway()

    async def asyncTearDown(self):
        self.core.quota.close()
        self.core.telemetry.close()
        self.temp.cleanup()

    async def test_quota_counts_turns_and_refuses_with_a_notice(self):
        for mid in ("m1", "m2"):
            self.assertIsNone(await self.core.pre_gateway_dispatch(event=event(message_id=mid), gateway=self.gateway))
        result = await self.core.pre_gateway_dispatch(event=event(message_id="m3"), gateway=self.gateway)
        self.assertEqual(result["action"], "skip")
        self.gateway.adapter.send.assert_awaited_once()
        self.assertIn("hết 2 lượt", self.gateway.adapter.send.await_args.args[1])

    async def test_commands_internal_and_unauthorized_never_consume_quota(self):
        for item, gateway in ((event(text="/new"), self.gateway), (event(internal=True), self.gateway),
                              (event(message_id="x"), FakeGateway(authorized=False))):
            self.assertIsNone(await self.core.pre_gateway_dispatch(event=item, gateway=gateway))
        self.assertEqual(self.core.quota.usage(person_key("telegram", "42")), (0, 0))

    async def test_unsafe_config_refuses_every_turn(self):
        core = AssistantCore(self.settings, config={"platform_toolsets": {"telegram": ["computer_use"]}})
        try:
            result = await core.pre_gateway_dispatch(event=event(), gateway=self.gateway)
            self.assertEqual(result["action"], "skip")
        finally:
            core.quota.close()
            core.telemetry.close()

    def test_blocked_tools_are_vetoed_and_logged(self):
        result = self.core.pre_tool_call(tool_name="session_search", args={}, session_id="s1")
        self.assertEqual(result["action"], "block")
        self.assertIsNone(self.core.pre_tool_call(tool_name="web_search", args={}, session_id="s1"))
        rows = self.core.telemetry.db.execute("SELECT tool_name, blocked FROM tool_calls").fetchall()
        self.assertEqual(rows, [("session_search", 1)])

    def test_api_attempts_are_logged_without_content(self):
        self.core.pre_api_request(api_request_id="r1", retry_count=1, session_id="s1")
        self.core.post_api_request(
            api_request_id="r1", session_id="s1", platform="telegram", model="m-fast", provider="p",
            api_call_count=1, api_duration=1.5, started_at=100.0, first_chunk_at=100.4, finish_reason="stop",
            usage={"input_tokens": 900, "output_tokens": 50, "cache_read_tokens": 800, "cache_write_tokens": 0},
            message_count=3, assistant_tool_call_count=1, assistant_message="SECRET ANSWER")
        self.core.api_request_error(api_request_id="r2", session_id="s1", model="m-fast",
                                    error=TimeoutError("boom"), status_code=429)
        rows = self.core.telemetry.db.execute(
            "SELECT model, retry_count, input_tokens, cache_read_tokens, round(ttfb_s, 2), error_type, status_code "
            "FROM api_calls ORDER BY id").fetchall()
        self.assertEqual(rows[0], ("m-fast", 1, 900, 800, 0.4, None, None))
        self.assertEqual(rows[1][5:], ("TimeoutError", 429))
        dump = "\n".join(self.core.telemetry.db.iterdump())
        self.assertNotIn("SECRET ANSWER", dump)
        run = self.core.telemetry.db.execute("SELECT config_hash FROM runs").fetchone()[0]
        self.assertEqual(len(run), 16)



class ReportingTests(unittest.TestCase):
    def test_read_only_reports(self):
        from assistant_core import reporting

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.sqlite3"
            self.assertEqual(reporting.runs(path), [])  # missing store is empty, not created
            self.assertFalse(path.exists())
            store = TelemetryStore(path, run_id="r1", label="B1")
            store.post_api_request(api_request_id="a", session_id="s", platform="telegram", model="m",
                                   api_duration=2.0, usage={"input_tokens": 5, "output_tokens": 1})
            store.tool_call(tool_name="web_search", status="ok", session_id="s")
            store.close()
            self.assertEqual(reporting.runs(path)[0]["api_calls"], 1)
            self.assertEqual(reporting.models(path)[0]["input_tokens"], 5)
            self.assertEqual(reporting.tools(path)[0]["tool_name"], "web_search")
            self.assertEqual(len(reporting.session_steps(path, "s")), 1)
            with self.assertRaises(sqlite3.OperationalError):
                with reporting._connect(path) as db:
                    db.execute("DELETE FROM runs")


if __name__ == "__main__":
    unittest.main()
