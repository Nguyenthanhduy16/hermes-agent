import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from hermes_zalo.events import IncomingMessage
from hermes_zalo.issues import IssueStore, issue_target, preview
from hermes_zalo.sheets import SheetsError, SheetsSettings, SheetsUncertain


def message(user="u1", chat="p1", kind="PRIVATE", mid="m1"):
    return IncomingMessage(chat_id=chat, user_id=user, message_id=mid, chat_type=kind,
                           text="/issue tao Tên issue | Mô tả issue")


class FakeSheets:
    def __init__(self, failure=None):
        self.settings = SheetsSettings("test_spreadsheet_id", Path("fake.json"),
                                       users=frozenset({"u1", "u2"}), groups=frozenset({"g1", "g2"}))
        self.rows = []
        self.failure = failure

    async def append_issue(self, row):
        self.rows.append(list(row))
        if self.failure:
            raise self.failure


class IssueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "issues.sqlite3"
        self.store = IssueStore(self.path)
        self.addCleanup(self.store.close)
        self.client = FakeSheets()

    def draft(self, event=None, title="Tên issue", body="Mô tả issue", now=100):
        return self.store.create(event or message(), title, body, issue_target(self.client), now=now)

    async def test_create_and_preview_do_not_write_and_show_exact_immutable_content(self):
        draft = self.draft(title="  Tiêu đề đã duyệt  ", body="  Mô tả chi tiết  ")
        shown = preview(draft, self.client.settings.issues_tab)
        self.assertIn("chưa ghi", shown)
        self.assertIn("Tiêu đề đã duyệt", shown)
        self.assertIn("Mô tả chi tiết", shown)
        self.assertIn("https://docs.google.com/spreadsheets/d/test_spreadsheet_id/edit", shown)
        self.assertIn("/issue xacnhan " + draft["id"], shown)
        self.assertEqual([], self.client.rows)
        result = await self.store.confirm(draft["id"], message(mid="confirm"), self.client, now=101)
        self.assertIn("Đã tạo issue", result)
        self.assertEqual("Tiêu đề đã duyệt", self.client.rows[0][2])
        self.assertEqual("Mô tả chi tiết", self.client.rows[0][3])
        self.assertEqual(["u1", "p1", "PRIVATE"], self.client.rows[0][5:])

    async def test_duplicate_event_reuses_draft_and_preserves_original_content(self):
        first = self.draft(title="Original title")
        replay = self.draft(title="Changed title", now=110)
        self.assertEqual(first["id"], replay["id"])
        self.assertEqual("Original title", replay["title"])
        self.assertEqual(first["expires"], replay["expires"])
        self.assertEqual([], self.client.rows)

    async def test_confirmation_and_cancel_are_bound_to_author_chat_and_type(self):
        event = message(chat="g1", kind="GROUP")
        draft = self.draft(event)
        for wrong in (message(user="u2", chat="g1", kind="GROUP"),
                      message(chat="g2", kind="GROUP"), message(chat="g1", kind="PRIVATE")):
            with self.subTest(user=wrong.user_id, chat=wrong.chat_id, kind=wrong.chat_type):
                result = await self.store.confirm(draft["id"], wrong, self.client, now=101)
                self.assertIn("Không tìm thấy", result)
                self.assertNotIn(draft["title"], result)
                self.assertFalse(self.store.cancel(draft["id"], wrong))
        self.assertEqual([], self.client.rows)
        self.assertEqual("draft", self.store.scoped(draft["id"], event)["status"])

    async def test_expired_confirmation_is_rejected_at_ten_minute_boundary(self):
        draft = self.draft(now=100)
        result = await self.store.confirm(draft["id"], message(), self.client, now=700)
        self.assertIn("hết hạn", result)
        self.assertEqual("expired", self.store.scoped(draft["id"], message())["status"])
        self.assertEqual([], self.client.rows)

    async def test_changed_sheet_or_tab_requires_new_preview(self):
        for field, changed in (("spreadsheet_id", "different_spreadsheet_id"), ("issues_tab", "OtherIssues")):
            with self.subTest(field=field):
                event = message(mid=field)
                draft = self.draft(event)
                original = self.client.settings
                self.client.settings = replace(original, **{field: changed})
                result = await self.store.confirm(draft["id"], event, self.client, now=101)
                self.assertIn("Nơi nhận issue đã đổi", result)
                self.assertEqual([], self.client.rows)
                self.client.settings = original

    async def test_permission_revocation_between_preview_and_confirm_prevents_write(self):
        draft = self.draft()
        self.client.settings = replace(self.client.settings, users=frozenset())
        result = await self.store.confirm(draft["id"], message(), self.client, now=101)
        self.assertIn("chưa được cấp quyền", result)
        self.assertEqual([], self.client.rows)
        self.assertEqual("draft", self.store.scoped(draft["id"], message())["status"])

    async def test_dynamic_guard_denial_overrides_static_access_without_claiming_draft(self):
        draft = self.draft()
        self.client.append_issue = AsyncMock()
        self.assertTrue(self.client.settings.allows('u1', 'p1', 'PRIVATE'))
        result = await self.store.confirm(draft['id'], message(), self.client, now=101, access_guard=lambda: False)
        self.assertIn('chưa được cấp quyền', result)
        self.client.append_issue.assert_not_awaited()
        self.assertEqual('draft', self.store.scoped(draft['id'], message())['status'])

    async def test_dynamic_member_guard_preserves_draft_owner_scope_and_writes_once(self):
        event = message(user='approved-member', chat='member-dm')
        draft = self.draft(event)
        self.client.settings = replace(self.client.settings, users=frozenset())
        self.client.append_issue = AsyncMock()
        guard = lambda: True
        wrong = message(user='different-member', chat='member-dm')
        denied = await self.store.confirm(draft['id'], wrong, self.client, now=101, access_guard=guard)
        self.assertIn('Không tìm thấy', denied)
        self.client.append_issue.assert_not_awaited()
        result = await self.store.confirm(draft['id'], event, self.client, now=102, access_guard=guard)
        self.assertIn('Đã tạo issue', result)
        self.client.append_issue.assert_awaited_once()
        self.assertIs(guard, self.client.append_issue.await_args.kwargs['access_guard'])
        self.assertEqual(['approved-member', 'member-dm', 'PRIVATE'], self.client.append_issue.await_args.args[0][5:])
        repeat = await self.store.confirm(draft['id'], event, self.client, now=103, access_guard=guard)
        self.assertIn('không tự tạo lại', repeat)
        self.client.append_issue.assert_awaited_once()
        self.assertEqual(frozenset(), self.client.settings.users)

    async def test_cancel_prevents_later_confirm(self):
        draft = self.draft()
        self.assertTrue(self.store.cancel(draft["id"], message()))
        result = await self.store.confirm(draft["id"], message(), self.client, now=101)
        self.assertIn("không tự tạo lại", result)
        self.assertEqual([], self.client.rows)

    async def test_repeated_confirmation_writes_only_once(self):
        draft = self.draft()
        await self.store.confirm(draft["id"], message(), self.client, now=101)
        second = await self.store.confirm(draft["id"], message(), self.client, now=102)
        self.assertEqual(1, len(self.client.rows))
        self.assertIn("không tự tạo lại", second)

    async def test_concurrent_confirm_claim_is_committed_before_http_and_cannot_cancel_inflight(self):
        draft = self.draft()
        started, release = asyncio.Event(), asyncio.Event()
        async def slow_append(row):
            self.client.rows.append(list(row))
            started.set()
            await release.wait()
        self.client.append_issue = slow_append
        first = asyncio.create_task(self.store.confirm(draft["id"], message(), self.client, now=101))
        await started.wait()
        observer = IssueStore(self.path)
        try:
            self.assertEqual("sending", observer.scoped(draft["id"], message())["status"])
        finally:
            observer.close()
        second = await self.store.confirm(draft["id"], message(), self.client, now=101)
        self.assertIn("không tự tạo lại", second)
        self.assertFalse(self.store.cancel(draft["id"], message()))
        release.set()
        await first
        self.assertEqual(1, len(self.client.rows))

    async def test_ambiguous_write_and_restart_never_replay_or_expose_error(self):
        draft = self.draft()
        self.client.failure = SheetsUncertain("PRIVATE_TOKEN upstream body")
        result = await self.store.confirm(draft["id"], message(), self.client, now=101)
        self.assertIn("Chưa xác nhận", result)
        self.assertNotIn("PRIVATE_TOKEN", result)
        self.assertEqual("uncertain", self.store.scoped(draft["id"], message())["status"])
        self.store.close()
        reopened = IssueStore(self.path)
        try:
            reopened.recover()
            second = await reopened.confirm(draft["id"], message(), self.client, now=102)
            self.assertIn("không tự tạo lại", second)
        finally:
            reopened.close()
        self.assertEqual(1, len(self.client.rows))

    async def test_crash_claim_recovery_is_uncertain_and_blocks_future_write(self):
        draft = self.draft()
        with self.store.db:
            self.store.db.execute("UPDATE drafts SET status='sending' WHERE id=?", (draft["id"],))
        self.store.recover()
        self.assertEqual("uncertain", self.store.scoped(draft["id"], message())["status"])
        result = await self.store.confirm(draft["id"], message(), self.client, now=101)
        self.assertIn("không tự tạo lại", result)
        self.assertEqual([], self.client.rows)

    async def test_definite_api_error_is_sanitized_and_draft_cannot_be_reconfirmed(self):
        draft = self.draft()
        self.client.failure = SheetsError("PRIVATE_TOKEN secret body")
        result = await self.store.confirm(draft["id"], message(), self.client, now=101)
        self.assertIn("Chưa tạo được issue", result)
        self.assertNotIn("PRIVATE_TOKEN", result)
        self.assertEqual("failed", self.store.scoped(draft["id"], message())["status"])
        await self.store.confirm(draft["id"], message(), self.client, now=102)
        self.assertEqual(1, len(self.client.rows))

    async def test_task_cancellation_marks_uncertain_before_propagating(self):
        draft = self.draft()
        started = asyncio.Event()
        async def interrupted_append(row):
            self.client.rows.append(list(row))
            started.set()
            await asyncio.Event().wait()
        self.client.append_issue = interrupted_append
        task = asyncio.create_task(self.store.confirm(draft["id"], message(), self.client, now=101))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual("uncertain", self.store.scoped(draft["id"], message())["status"])
        await self.store.confirm(draft["id"], message(), self.client, now=102)
        self.assertEqual(1, len(self.client.rows))

    async def test_draft_validation_and_pending_limit_precede_any_write(self):
        for title, body in (("", "body"), ("title", ""), ("x" * 161, "body"), ("title", "x" * 1201)):
            with self.subTest(lengths=(len(title), len(body))), self.assertRaises(ValueError):
                self.draft(title=title, body=body)
        for number in range(20):
            self.draft(message(mid=f"draft{number}"))
        with self.assertRaises(ValueError):
            self.draft(message(mid="overflow"))
        self.assertEqual([], self.client.rows)


if __name__ == "__main__":
    unittest.main()
