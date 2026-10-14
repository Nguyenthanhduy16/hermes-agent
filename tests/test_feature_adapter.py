"""Application commands and timer delivery through the actual adapter, offline."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import test_adapter as contract
from hermes_zalo.api import DeliveryUncertain
from hermes_zalo.api import AuthenticationError
from hermes_zalo.work import WorkService
from hermes_zalo.nlu import OUT_OF_SCOPE


class FeatureAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await contract.AdapterTests.asyncSetUp(self)
        root = Path(self.temp.name) / "knowledge"
        root.mkdir()
        (root / "handbook.md").write_text("# Giờ làm việc\nGiờ làm việc: từ 08:30 đến 17:30, thứ Hai đến thứ Sáu.\n", encoding="utf-8")
        (root / "manifest.json").write_text(json.dumps({"documents": [{"id": "d1", "title": "Sổ tay", "path": "handbook.md", "users": ["u1"], "groups": []}]}), encoding="utf-8")
        self.adapter.settings = replace(self.adapter.settings, knowledge_dir=root)
        self.adapter.work = WorkService(self.adapter.settings, environ={})

    async def asyncTearDown(self):
        await contract.AdapterTests.asyncTearDown(self)

    async def test_document_answer_does_not_enter_model_history_and_keeps_citation(self):
        await self.adapter._dispatch(contract.update(text="/hoi Giờ làm việc là khi nào?"))
        self.handler.assert_not_awaited()
        args = self.adapter.client.send_text_chunk.await_args.args
        self.assertEqual(args[0], "dm1")
        self.assertIn("08:30", args[1])
        self.assertIn("handbook.md", args[1])
        self.assertFalse(self.adapter._session_tasks)

    async def test_menu_typos_and_natural_calendar_are_app_commands_not_model_prompts(self):
        for index, text in enumerate(("/menu", "/help nhac", "/lichh", "lịch hôm nay")):
            await self.adapter._dispatch(contract.update(str(index), text=text))
        self.handler.assert_not_awaited()
        self.assertEqual(4, self.adapter.client.send_text_chunk.await_count)
        await self.adapter._dispatch(contract.update("denied", user="stranger", text="/menu"))
        self.assertEqual(4, self.adapter.client.send_text_chunk.await_count)

    async def test_group_cannot_retrieve_private_document(self):
        await self.adapter._dispatch(contract.update(chat="g1", kind="GROUP", text="/hoi Giờ làm việc là khi nào?"))
        output = self.adapter.client.send_text_chunk.await_args.args[1]
        self.assertNotIn("08:30", output)
        self.assertNotIn("handbook.md", output)
        self.handler.assert_not_awaited()

    async def test_reminder_creation_dedup_no_ai_and_cancellation(self):
        update = contract.update(text="/nhac sau 1 phut | Họp thử")
        await self.adapter._dispatch(update)
        await self.adapter._dispatch(update)
        self.handler.assert_not_awaited()
        rows = self.adapter.work.reminders.list_for("u1", "dm1", "PRIVATE")
        self.assertEqual(len(rows), 1)
        await self.adapter._dispatch(contract.update("m2", text=f"/nhac huy {rows[0].id}"))
        await self.adapter._deliver_due_reminders(now=rows[0].due_at + 1)
        self.assertEqual(self.adapter.client.send_text_chunk.await_count, 2)  # two acknowledgements only

    async def test_timer_sends_to_stored_destination_only_once(self):
        item = self.adapter.work.reminders.create("u1", "g1", "GROUP", "Họp thử", 1010, now=1000, request_key="timer1")
        await self.adapter._deliver_due_reminders(now=1009)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        await self.adapter._deliver_due_reminders(now=1010)
        await self.adapter._deliver_due_reminders(now=1011)
        self.adapter.client.send_text_chunk.assert_awaited_once()
        self.assertEqual(self.adapter.client.send_text_chunk.await_args.args[0], "g1")
        self.assertIn("Họp thử", self.adapter.client.send_text_chunk.await_args.args[1])
        self.assertEqual(self.adapter.work.reminders.list_for("u1", "g1", "GROUP")[0].status, "sent")
        self.assertFalse((await self.adapter.send("g1", "arbitrary send")).success)

    async def test_revoked_user_or_group_blocks_scheduled_send(self):
        store = self.adapter.work.reminders
        store.create("stranger", "dm2", "PRIVATE", "private", 1010, now=1000, request_key="revoked-user")
        store.create("u1", "g2", "GROUP", "group", 1010, now=1000, request_key="revoked-group")
        await self.adapter._deliver_due_reminders(now=1010)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual(store.list_for("u1", "g2", "GROUP")[0].status, "blocked")

    async def test_ambiguous_timer_delivery_not_retried_and_does_not_break_bot_replies(self):
        self.adapter.work.reminders.create("u1", "dm1", "PRIVATE", "test", 1010, now=1000, request_key="timeout")
        self.adapter.client.send_text_chunk.side_effect = DeliveryUncertain()
        await self.adapter._deliver_due_reminders(now=1010)
        await self.adapter._deliver_due_reminders(now=1011)
        self.adapter.client.send_text_chunk.assert_awaited_once()
        self.assertEqual(self.adapter.work.reminders.list_for("u1", "dm1", "PRIVATE")[0].status, "uncertain")
        self.adapter.client.send_text_chunk.side_effect = None
        await self.adapter._dispatch(contract.update(text="Xin chào"))
        self.handler.assert_not_awaited()
        self.assertEqual(OUT_OF_SCOPE, self.adapter.client.send_text_chunk.await_args.args[1])

    async def test_empty_sheets_config_is_safe_and_does_not_call_model(self):
        await self.adapter._dispatch(contract.update(text="/lich homnay"))
        self.handler.assert_not_awaited()
        self.assertIn("Chưa kết nối", self.adapter.client.send_text_chunk.await_args.args[1])
        events = self.adapter.work.pilot.report()["events"]
        self.assertTrue(any(r["kind"] == "calendar" and r["outcome"] == "unsupported" for r in events))

    async def test_fatal_first_send_does_not_claim_other_reminders(self):
        store = self.adapter.work.reminders
        for n in range(2):
            store.create("u1", "dm1", "PRIVATE", f"test{n}", 1010 + n, now=1000, request_key=str(n))
        self.adapter.client.send_text_chunk.side_effect = AuthenticationError()
        await self.adapter._deliver_due_reminders(now=1020)
        self.assertEqual({r.status for r in store.list_for("u1", "dm1", "PRIVATE")}, {"failed", "pending"})

    async def test_long_unicode_reminder_is_not_truncated(self):
        content = "🦉" * 1000
        self.adapter.work.reminders.create("u1", "dm1", "PRIVATE", content, 1010, now=1000, request_key="long")
        await self.adapter._deliver_due_reminders(now=1010)
        self.assertGreater(self.adapter.client.send_text_chunk.await_count, 1)
        sent = "".join(c.args[1] for c in self.adapter.client.send_text_chunk.await_args_list)
        self.assertIn(content, sent)

    async def test_draft_requires_own_confirmation_at_original_chat(self):
        from hermes_zalo.sheets import SheetsSettings
        settings = SheetsSettings("fixture-sheet-123", Path("not-used"), users=frozenset({"u1", "u2"}), groups=frozenset({"g1"}))
        sheets = SimpleNamespace(settings=settings, append_issue=AsyncMock(), close=AsyncMock())
        self.adapter.work.sheets = sheets
        await self.adapter._dispatch(contract.update(text="/issue tao Lỗi nút bấm | Không lưu được"))
        sheets.append_issue.assert_not_awaited()
        draft = self.adapter.work.issues.db.execute("SELECT id FROM drafts").fetchone()[0]
        for mid, user, chat, kind in (("2", "u2", "dm1", "PRIVATE"), ("3", "u1", "g1", "GROUP")):
            await self.adapter._dispatch(contract.update(mid, user=user, chat=chat, kind=kind, text=f"/issue xacnhan {draft}"))
        sheets.append_issue.assert_not_awaited()
        await self.adapter._dispatch(contract.update("4", text=f"/issue xacnhan {draft}"))
        await self.adapter._dispatch(contract.update("5", text=f"/issue xacnhan {draft}"))
        sheets.append_issue.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
