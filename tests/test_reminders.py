"""Offline reminder contract tests; no Zalo, network, or paid model calls."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest

from hermes_zalo.reminders import (
    MAX_ACTIVE, MAX_HORIZON, MAX_LATENESS, ReminderStore, parse_reminder_command,
)


NOW = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc).timestamp()


class ReminderParsingTests(unittest.TestCase):
    def test_absolute_time_is_vietnam_time_not_machine_timezone(self):
        parsed = parse_reminder_command("/nhac 2026-10-05 08:30 | Gọi chị Mai Anh", now=NOW)
        self.assertEqual("create", parsed.action)
        self.assertEqual(NOW + 90 * 60, parsed.due_at)
        self.assertEqual("Gọi chị Mai Anh", parsed.text)

    def test_relative_units_and_vietnamese_accents(self):
        for command, seconds in [
            ("/nhac sau 10 phut | Uống nước", 600),
            ("/NHẮC sau 2 giờ | Kiểm tra", 7200),
            ("nhắc mình sau 1 ngày: Gửi báo cáo", 86400),
        ]:
            with self.subTest(command=command):
                parsed = parse_reminder_command(command, now=NOW)
                self.assertEqual(NOW + seconds, parsed.due_at)
                self.assertTrue(parsed.text)

    def test_list_cancel_and_unrelated_chat(self):
        self.assertEqual("list", parse_reminder_command("/nhắc xem", now=NOW).action)
        parsed = parse_reminder_command("/nhac hủy a12b34", now=NOW)
        self.assertEqual(("cancel", "A12B34"), (parsed.action, parsed.reminder_id))
        for text in ("Xin chào", "Hãy giải thích nhắc việc", "/nhacxyz", "/nhack"):
            self.assertIsNone(parse_reminder_command(text, now=NOW))

    def test_malformed_or_ambiguous_schedule_never_creates_a_reminder(self):
        for command in (
            "/nhac", "/nhac hủy", "/nhac xem thêm", "/nhac mai | Gọi điện",
            "/nhac 08:30 | Gọi điện", "/nhac 2026-02-30 08:30 | Gọi điện",
            "/nhac 2026-10-05 25:00 | Gọi điện", "/nhac sau -1 phut | Gọi điện",
            "/nhac sau 0 phut | Gọi điện", "/nhac sau 999999999999999 phut | Gọi điện",
            "/nhac sau 91 ngay | Gọi điện", "/nhac sau 1 giờ 30 phút | Gọi điện",
            "nhắc mình sau 1 giờ 30 phút gọi điện", "/nhac sau 1 phut | ",
            "/nhac 2026-10-04 08:30 | Gọi điện", "/nhac 2026-10-05 07:00 | Gọi điện",
            "/nhac sau 1 phut | " + "a" * 1001,
        ):
            with self.subTest(command=command[:70]):
                with self.assertRaises(ValueError):
                    parse_reminder_command(command, now=NOW)


class ReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private" / "reminders.sqlite"
        self.store = ReminderStore(self.path)
        self.addCleanup(lambda: self.store.close())

    def create(self, key="event-1", *, user="alice", chat="dm-a", kind="PRIVATE", due=None):
        return self.store.create(
            user, chat, kind, "Nội dung riêng", NOW + 60 if due is None else due,
            now=NOW, request_key=key,
        )

    def test_create_destination_and_state_survive_restart(self):
        created = self.create(user="alice", chat="group-a", kind="GROUP")
        self.store.close()
        self.store = ReminderStore(self.path)
        loaded = self.store.list_for("alice", "group-a", "GROUP")
        self.assertEqual([created], loaded)
        self.assertEqual(("alice", "group-a", "GROUP", "pending"), (
            loaded[0].user_id, loaded[0].chat_id, loaded[0].chat_type, loaded[0].status,
        ))

    def test_other_users_chats_and_chat_kinds_cannot_list_or_cancel(self):
        created = self.create(user="alice", chat="group-a", kind="GROUP")
        for user, chat, kind in (
            ("bob", "group-a", "GROUP"), ("alice", "group-b", "GROUP"),
            ("alice", "group-a", "PRIVATE"), ("bob", "dm-b", "PRIVATE"),
        ):
            with self.subTest(scope=(user, chat, kind)):
                self.assertEqual([], self.store.list_for(user, chat, kind))
                self.assertFalse(self.store.cancel(created.id, user, chat, kind))
        self.assertTrue(self.store.cancel(created.id.lower(), "alice", "group-a", "GROUP"))
        self.assertEqual([], self.store.claim_due(NOW + 60))

    def test_duplicate_request_preserves_original_destination_text_time_and_status(self):
        created = self.create()
        self.store.cancel(created.id, "alice", "dm-a", "PRIVATE")
        duplicate = self.store.create(
            "alice", "dm-a", "PRIVATE", "Changed by a duplicate", NOW + 999,
            now=NOW + 1000, request_key="event-1",
        )
        self.assertEqual(created.id, duplicate.id)
        self.assertEqual(created.text, duplicate.text)
        self.assertEqual(created.due_at, duplicate.due_at)
        self.assertEqual("cancelled", duplicate.status)
        self.assertEqual(1, len(self.store.list_for("alice", "dm-a", "PRIVATE")))
        with self.assertRaises(ValueError):
            self.create(user="bob")

    def test_quota_is_atomic_across_connections_but_scoped_per_user_chat(self):
        for i in range(MAX_ACTIVE):
            self.create(str(i))
        other = ReminderStore(self.path)
        try:
            with self.assertRaises(ValueError):
                other.create("alice", "dm-a", "PRIVATE", "Limit", NOW + 90, now=NOW, request_key="over")
            duplicated = other.create("alice", "dm-a", "PRIVATE", "Duplicate", NOW + 90, now=NOW, request_key="0")
            self.assertEqual("Nội dung riêng", duplicated.text)
            self.assertTrue(other.cancel(duplicated.id, "alice", "dm-a", "PRIVATE"))
            self.create("new-after-cancel")
            self.create("other-user", user="bob")
            self.create("other-chat", chat="dm-b")
        finally:
            other.close()

    def test_claim_persisted_before_return_and_never_replayed(self):
        created = self.create()
        self.assertEqual([], self.store.claim_due(NOW + 59))
        due = self.store.claim_due(NOW + 60)
        self.assertEqual([created.id], [r.id for r in due])
        self.assertEqual("claimed", due[0].status)
        second = ReminderStore(self.path)
        try:
            self.assertEqual([], second.claim_due(NOW + 61))
            self.assertEqual("claimed", second.list_for("alice", "dm-a", "PRIVATE")[0].status)
        finally:
            second.close()
        self.assertFalse(self.store.cancel(created.id, "alice", "dm-a", "PRIVATE"))
        self.assertTrue(self.store.finish(created.id, "sent"))
        self.assertFalse(self.store.finish(created.id, "failed"))
        self.assertEqual([], self.store.claim_due(NOW + 120))

    def test_concurrent_quota_and_claim_transactions_have_one_winner(self):
        for index in range(MAX_ACTIVE - 1):
            self.create(str(index))
        barrier = threading.Barrier(2)

        def worker(index):
            store = ReminderStore(self.path)
            try:
                barrier.wait(timeout=5)
                try:
                    store.create("alice", "dm-a", "PRIVATE", "Concurrent", NOW + 60,
                                 now=NOW, request_key=f"concurrent-{index}")
                    created = True
                except ValueError:
                    created = False
                barrier.wait(timeout=5)
                claimed = store.claim_due(NOW + 60, limit=20)
                return created, {row.id for row in claimed}
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(worker, (1, 2)))
        self.assertEqual(1, sum(created for created, _ in results))
        self.assertFalse(results[0][1] & results[1][1])
        self.assertEqual(MAX_ACTIVE, len(results[0][1] | results[1][1]))

    def test_restart_marks_crash_during_network_call_uncertain_without_retry(self):
        created = self.create()
        self.store.claim_due(NOW + 60)
        self.store.close()
        self.store = ReminderStore(self.path)
        self.assertEqual(1, self.store.recover_claimed())
        self.assertEqual(0, self.store.recover_claimed())
        self.assertEqual("uncertain", self.store.list_for("alice", "dm-a", "PRIVATE")[0].status)
        self.assertEqual([], self.store.claim_due(NOW + 120))
        self.assertFalse(self.store.finish(created.id, "sent"))

    def test_ambiguous_failed_and_revoked_permission_outcomes_are_terminal(self):
        for index, status in enumerate(("uncertain", "failed", "blocked")):
            created = self.create(str(index))
            claimed = self.store.claim_due(NOW + 60)
            self.assertEqual([created.id], [row.id for row in claimed])
            self.assertTrue(self.store.finish(created.id, status))
            self.assertEqual([], self.store.claim_due(NOW + 120))

    def test_offline_grace_expires_old_rows_but_claims_boundary_and_recent(self):
        first = self.create("old", due=NOW + 1)
        second = self.create("boundary", due=NOW + 2)
        third = self.create("recent", due=NOW + 3)
        due = self.store.claim_due(NOW + 2 + MAX_LATENESS)
        self.assertEqual([second.id, third.id], [r.id for r in due])
        statuses = {row.id: row.status for row in self.store.list_for("alice", "dm-a", "PRIVATE")}
        self.assertEqual("expired", statuses[first.id])

    def test_batch_limit_keeps_other_due_rows_pending(self):
        first = self.create("first", due=NOW + 1)
        second = self.create("second", due=NOW + 2)
        self.assertEqual([first.id], [r.id for r in self.store.claim_due(NOW + 3, limit=1)])
        self.assertEqual([second.id], [r.id for r in self.store.claim_due(NOW + 3, limit=1)])

    def test_invalid_timestamps_scope_and_limits_fail_closed(self):
        for due in (float("nan"), float("inf"), True, None, "tomorrow", NOW, NOW - 1, NOW + MAX_HORIZON + 1):
            with self.subTest(due=due), self.assertRaises(ValueError):
                self.store.create("alice", "dm-a", "PRIVATE", "Text", due, now=NOW, request_key="bad")
        for user, chat, kind in (("", "dm-a", "PRIVATE"), ("alice", "x:y", "PRIVATE"), ("alice", "g", "OTHER")):
            with self.assertRaises(ValueError):
                self.create(user=user, chat=chat, kind=kind)
        for limit in (0, -1, True, 101):
            with self.assertRaises(ValueError):
                self.store.claim_due(NOW, limit=limit)
        for lateness in (-1, MAX_LATENESS + 1, float("nan")):
            with self.assertRaises(ValueError):
                self.store.claim_due(NOW, max_lateness=lateness)
        with self.assertRaises(ValueError):
            self.store.finish("unknown", "pending")


if __name__ == "__main__":
    unittest.main()
