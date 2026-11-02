"""Assistant mode: admitted non-command messages run the Hermes agent loop.

Hermes and Zalo are simulated with the same contract stubs as test_adapter.
"""

import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from hermes_zalo.config import ConfigError, Settings
from hermes_zalo.events import parse_update
from hermes_zalo.state import StateStore

try:  # unittest discovery and explicit package-qualified test runs use different roots.
    from test_adapter import Outcome, adapter_module, update
except ModuleNotFoundError:
    from tests.test_adapter import Outcome, adapter_module, update


def image_update(mid="img1", *, chat="dm1", user="u1", kind="PRIVATE", caption="Đây là bảng gì?"):
    return {"event_name": "message.image.received", "message": {
        "message_id": mid, "chat": {"id": chat, "chat_type": kind},
        "from": {"id": user, "is_bot": False},
        "photo_url": "https://zalo.example/photo.jpg", "caption": caption,
    }}


class AssistantAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        settings = Settings("test-token", frozenset({"u1", "u2"}), frozenset({"g1"}), Path(self.temp.name),
                            bot_mode="assistant")
        with patch.object(adapter_module, "scoped_settings", return_value=settings):
            self.adapter = adapter_module.ZaloAdapter(SimpleNamespace())
        self.guard = patch.object(adapter_module, "assert_chat_only")
        self.guard.start()
        self.adapter.store = StateStore(Path(self.temp.name) / "state.sqlite3")
        self.adapter.client = SimpleNamespace(
            send_text_chunk=AsyncMock(return_value={"message_id": "reply1"}),
            send_photo=AsyncMock(return_value={"message_id": "photo1"}),
            send_chat_action=AsyncMock(), download=AsyncMock(return_value=b"\x89PNG"), close=AsyncMock())
        self.adapter._groups_supported = True
        self.handler = AsyncMock(return_value="Chào bạn, mình giúp gì được?")
        self.adapter._message_handler = self.handler
        self.work_handler = AsyncMock(return_value="Kết quả công việc")
        self.owns = AsyncMock(return_value=False)
        self.adapter.work = SimpleNamespace(
            handle=self.work_handler, owns_turn=self.owns,
            member_admin_locked=lambda *args: False,
            background_registration_blocked=lambda *args: False,
            member_feature_allowed=lambda *args: False,
            pilot=SimpleNamespace(record=lambda *args: None), close=AsyncMock())
        completions = []
        original = self.adapter.on_processing_complete

        async def track(event, outcome):
            completions.append(outcome)
            await original(event, outcome)
        self.adapter.on_processing_complete = track
        self.completions = completions

    async def asyncTearDown(self):
        self.guard.stop()
        await self.adapter.disconnect()
        self.temp.cleanup()

    async def drain(self):
        while self.adapter._session_tasks:
            await asyncio.gather(*list(self.adapter._session_tasks.values()))

    async def test_free_text_runs_agent_and_reply_is_delivered(self):
        await self.adapter._dispatch(update(text="Tóm tắt giúp mình bài này"))
        await self.drain()
        self.handler.assert_awaited_once()
        self.work_handler.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_awaited_once_with("dm1", "Chào bạn, mình giúp gì được?")
        status = self.adapter.store.connection.execute("SELECT status FROM messages").fetchone()[0]
        self.assertEqual(status, "completed")
        self.assertEqual(self.completions, [Outcome.SUCCESS])

    async def test_commands_and_work_dialogs_stay_local(self):
        self.owns.return_value = True
        await self.adapter._dispatch(update(text="/lich homnay"))
        await self.drain()
        self.work_handler.assert_awaited_once()
        self.handler.assert_not_awaited()

    async def test_unapproved_enrolment_sender_never_reaches_agent(self):
        self.adapter.work.registration_admission = lambda message: True
        await self.adapter._dispatch(update(user="newcomer", text="Viết thơ đi"))
        await self.drain()
        self.handler.assert_not_awaited()
        self.work_handler.assert_awaited_once()

    async def test_approved_member_dm_reaches_agent(self):
        self.adapter.work.registration_admission = lambda message: True
        self.adapter.work.member_feature_allowed = lambda user, chat, kind: user == "member"
        await self.adapter._dispatch(update(chat="dm9", user="member", text="Giải thích hồi quy logistic"))
        await self.drain()
        self.handler.assert_awaited_once()

    async def test_image_is_downloaded_into_a_photo_turn(self):
        with patch.object(adapter_module, "cache_image", return_value="/cache/img.jpg"):
            await self.adapter._dispatch(image_update())
            await self.drain()
        event = self.handler.await_args.args[0]
        self.assertEqual(event.media_urls, ["/cache/img.jpg"])
        self.assertEqual(event.text, "Đây là bảng gì?")
        self.adapter.client.download.assert_awaited_once_with("https://zalo.example/photo.jpg")

    async def test_send_outside_admitted_chats_is_refused(self):
        result = await self.adapter.send("elsewhere", "spam")
        self.assertFalse(result.success)
        photo = await self.adapter.send_image_file("elsewhere", "/tmp/x.png")
        self.assertFalse(photo.success)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.adapter.client.send_photo.assert_not_awaited()

    async def test_chart_file_is_uploaded_to_the_admitted_chat(self):
        await self.adapter._dispatch(update(text="Vẽ biểu đồ"))
        await self.drain()
        chart = Path(self.temp.name) / "chart.png"
        chart.write_bytes(b"\x89PNG")
        result = await self.adapter.send_image_file("dm1", str(chart), caption="Doanh thu")
        self.assertTrue(result.success)
        self.adapter.client.send_photo.assert_awaited_once_with("dm1", b"\x89PNG", "Doanh thu", filename="chart.png")

    async def test_revoked_access_blocks_late_agent_sends(self):
        await self.adapter._dispatch(update(text="Xin chào"))
        await self.drain()
        self.adapter.work.member_admin_locked = lambda *args: True
        result = await self.adapter.send("dm1", "late")
        self.assertFalse(result.success)

    async def test_lab_mode_still_never_reaches_agent(self):
        self.adapter.settings = Settings("test-token", frozenset({"u1"}), frozenset(), Path(self.temp.name))
        self.work_handler.return_value = None
        await self.adapter._dispatch(update(text="Viết truyện"))
        await self.drain()
        self.handler.assert_not_awaited()
        self.assertEqual(self.adapter.client.send_text_chunk.await_args.args[1], adapter_module.OUT_OF_SCOPE)


class AssistantParsingTests(unittest.TestCase):
    def test_images_parse_only_when_enabled(self):
        self.assertIsNone(parse_update(image_update()))
        message = parse_update(image_update(), images=True)
        self.assertEqual(message.photo_url, "https://zalo.example/photo.jpg")

    def test_non_https_image_is_ignored(self):
        raw = image_update()
        raw["message"]["photo_url"] = "http://insecure/photo.jpg"
        self.assertIsNone(parse_update(raw, images=True))

    def test_bot_mode_is_validated(self):
        base = {"ZALO_BOT_TOKEN": "t", "ZALO_ALLOWED_USERS": "u1"}
        self.assertEqual(Settings.from_mapping(base).bot_mode, "lab")
        self.assertTrue(Settings.from_mapping({**base, "ZALO_BOT_MODE": "assistant"}).assistant)
        with self.assertRaises(ConfigError):
            Settings.from_mapping({**base, "ZALO_BOT_MODE": "everything"})


if __name__ == "__main__":
    unittest.main()
