"""Offline quotas; no model, Zalo or real user data."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from hermes_zalo.ai_usage import AIUsageError, AIUsageLimit, AIUsageStore, options


class AIUsageTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "usage.sqlite3"
        self.now = datetime(2026, 10, 14, 10, tzinfo=timezone.utc).timestamp()

    def store(self, **kwargs):
        store = AIUsageStore(self.path, clock=lambda: self.now, **kwargs)
        self.addCleanup(store.close)
        return store

    def assert_limit(self, reason, store, user, event):
        with self.assertRaises(AIUsageLimit) as raised:
            store.reserve(user, event)
        self.assertEqual(reason, raised.exception.reason)

    def test_defaults_and_strict_configuration(self):
        self.assertEqual({"user_daily_limit": 10, "total_daily_limit": 300}, options({}))
        self.assertEqual({"user_daily_limit": 15, "total_daily_limit": 500}, options({
            "ZALO_AI_USER_DAILY_LIMIT": "15", "ZALO_AI_TOTAL_DAILY_LIMIT": "500"}))
        for flag, high in (("ZALO_AI_USER_DAILY_LIMIT", 1001), ("ZALO_AI_TOTAL_DAILY_LIMIT", 100001)):
            for value in ("0", "-1", "2.5", "off", True, 2.5, str(high)):
                with self.subTest(flag=flag, value=value), self.assertRaises(ValueError):
                    options({flag: value})
        for value in (True, 0, 1001, "10", 2.5):
            with self.subTest(constructor=value), self.assertRaises(ValueError):
                AIUsageStore(self.path, user_daily_limit=value)

    def test_per_user_quota_is_shared_across_dm_and_group(self):
        store = self.store(user_daily_limit=2)
        store.reserve("zalo-user-a", "private-chat:one")
        store.reserve("zalo-user-a", "group-chat:two")
        self.assert_limit("user_daily", store, "zalo-user-a", "other-group:three")
        store.reserve("zalo-user-b", "group-chat:three")
        snapshot = store.snapshot("zalo-user-a")
        self.assertEqual((2, 3), (snapshot.user_count, snapshot.total_count))

    def test_global_limit_blocks_new_users(self):
        store = self.store(total_daily_limit=2)
        store.reserve("a", "first")
        store.reserve("b", "second")
        self.assert_limit("total_daily", store, "c", "third")
        self.assertEqual(2, store.snapshot().total_count)
        self.assertEqual(0, store.snapshot().user_count)

    def test_duplicate_does_not_charge_or_dispatch_again(self):
        store = self.store(user_daily_limit=1)
        store.reserve("a", "private-chat:one")
        self.assert_limit("duplicate", store, "a", "private-chat:one")
        self.assertEqual(1, store.snapshot("a").user_count)
        store.reserve("b", "private-chat:one")
        self.assertEqual(2, store.snapshot().total_count)

    def test_attempt_survives_provider_failure_and_restart(self):
        first = AIUsageStore(self.path, user_daily_limit=1, clock=lambda: self.now)
        first.reserve("a", "upstream-request-fails-after-reservation")
        first.close()
        restarted = self.store(user_daily_limit=1)
        self.assert_limit("user_daily", restarted, "a", "new-attempt")
        self.assert_limit("duplicate", restarted, "a", "upstream-request-fails-after-reservation")

    def test_reset_is_midnight_vietnam_and_old_duplicate_remains_blocked(self):
        self.now = datetime(2026, 10, 14, 16, 59, 59, tzinfo=timezone.utc).timestamp()
        store = self.store(user_daily_limit=1, total_daily_limit=1)
        store.reserve("a", "before-midnight")
        self.assertEqual("2026-10-14", store.snapshot().day)
        self.now += 1
        self.assertEqual(("2026-10-15", 0), (store.snapshot().day, store.snapshot().total_count))
        self.assert_limit("duplicate", store, "a", "before-midnight")
        store.reserve("a", "after-midnight")
        self.assert_limit("user_daily", store, "a", "after-midnight-again")

    def test_concurrent_connections_cannot_exceed_global_limit(self):
        stores = [self.store(total_daily_limit=1) for _ in range(8)]
        barrier = Barrier(len(stores))

        def attempt(index):
            barrier.wait(timeout=5)
            try:
                stores[index].reserve(str(index), "event")
                return "accepted"
            except AIUsageLimit as exc:
                return exc.reason

        with ThreadPoolExecutor(max_workers=len(stores)) as executor:
            results = list(executor.map(attempt, range(len(stores))))
        self.assertEqual(1, results.count("accepted"))
        self.assertEqual(7, results.count("total_daily"))
        self.assertEqual(1, stores[0].snapshot().total_count)

    def test_concurrent_duplicates_reserve_only_once(self):
        stores = [self.store() for _ in range(4)]
        barrier = Barrier(len(stores))

        def attempt(index):
            barrier.wait(timeout=5)
            try:
                stores[index].reserve("same-user", "same-event")
                return "accepted"
            except AIUsageLimit as exc:
                return exc.reason

        with ThreadPoolExecutor(max_workers=len(stores)) as executor:
            results = list(executor.map(attempt, range(len(stores))))
        self.assertEqual(1, results.count("accepted"))
        self.assertEqual(3, results.count("duplicate"))

    def test_concurrent_connections_cannot_exceed_same_user_limit(self):
        stores = [self.store(user_daily_limit=1) for _ in range(4)]
        barrier = Barrier(len(stores))

        def attempt(index):
            barrier.wait(timeout=5)
            try:
                stores[index].reserve("same-user", f"chat-{index}:event")
                return "accepted"
            except AIUsageLimit as exc:
                return exc.reason

        with ThreadPoolExecutor(max_workers=len(stores)) as executor:
            results = list(executor.map(attempt, range(len(stores))))
        self.assertEqual(1, results.count("accepted"))
        self.assertEqual(3, results.count("user_daily"))

    def test_only_key_hashes_and_date_are_persisted(self):
        store = self.store()
        user, event = "sensitive-user-identifier", "sensitive-event-identifier"
        store.reserve(user, event)
        row = store.connection.execute("SELECT * FROM ai_usage").fetchone()
        self.assertEqual([64, 64, 10], [len(value) for value in row])
        self.assertNotIn(user, str(row))
        self.assertNotIn(event, str(row))

    def test_bad_keys_and_unavailable_store_fail_closed_with_fixed_error(self):
        store = self.store()
        for user, event in (("", "event"), ("user", ""), (None, "event"),
                            ("user", "x" * 4097), ("user", "\ud800")):
            with self.subTest(user=user), self.assertRaises(AIUsageError) as raised:
                store.reserve(user, event)
            self.assertEqual("invalid_key", str(raised.exception))
        self.assertEqual(0, store.snapshot().total_count)
        store.close()
        for action in (lambda: store.reserve("user", "event"), store.snapshot):
            with self.assertRaises(AIUsageError) as raised:
                action()
            self.assertEqual("unavailable", str(raised.exception))

    def test_corrupt_database_does_not_reset_allowance(self):
        self.path.write_text("not a sqlite database with sensitive details")
        with self.assertRaises(AIUsageError) as raised:
            self.store()
        self.assertEqual("unavailable", str(raised.exception))
        self.assertTrue(self.path.read_text().startswith("not a sqlite"))

    def test_database_failure_rolls_back_and_does_not_bypass_limit(self):
        store = self.store(user_daily_limit=1)
        store.connection.execute("""CREATE TRIGGER broken_insert BEFORE INSERT ON ai_usage
            BEGIN SELECT RAISE(ABORT, 'private error details'); END""")
        with self.assertRaises(AIUsageError) as raised:
            store.reserve("a", "first")
        self.assertEqual("unavailable", str(raised.exception))
        self.assertEqual(0, store.snapshot().total_count)
        store.connection.execute("DROP TRIGGER broken_insert")
        store.reserve("a", "first")
        self.assert_limit("user_daily", store, "a", "second")


if __name__ == "__main__":
    unittest.main()
