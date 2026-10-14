from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest

from hermes_zalo.lab_calendar import LabEvent, LabSchedule
from hermes_zalo.reminders import VIETNAM_TZ
from hermes_zalo.schedule_reminders import (
    AutoReminderSettings, MAX_NOTICE_LENGTH, ScheduleReminderStore, plan_notices,
)


PERIODS = {"Sáng": {"start": "09:00", "end": "13:00"},
           "Chiều": {"start": "14:00", "end": "18:00"},
           "Tối": {"start": "17:30", "end": "20:30"}}
MEMBER = {"source_key": "spreadsheet-stable", "member_id": "MB-001", "member_name": "An Nguyễn",
          "user_id": "zalo-user", "chat_id": "private-chat"}


def instant(value):
    return datetime.fromisoformat(value).replace(tzinfo=VIETNAM_TZ)


def event(day="2026-10-16", **changes):
    return LabEvent(day=date.fromisoformat(day), course=changes.pop("course", "Python"),
                    session=changes.pop("session", "Session 1"), key_coach=changes.pop("key_coach", "Khánh"),
                    room=changes.pop("room", "Lab 2"), period=changes.pop("period", "Sáng"),
                    first_row=changes.pop("first_row", 10), last_row=changes.pop("last_row", 10),
                    coach_names=changes.pop("coach_names", ["An Nguyễn"]), **changes)


def schedule(*events, sheet_id=100, tab="Lịch lab"):
    return LabSchedule(tab, sheet_id, {e.day for e in events}, list(events))


class AutoReminderSettingsTests(unittest.TestCase):
    def test_safe_defaults_and_explicit_enable(self):
        settings = AutoReminderSettings.from_mapping({})
        self.assertFalse(settings.enabled)
        self.assertEqual((settings.evening_time, settings.lead_minutes, settings.poll_seconds), ("20:00", 30, 60))
        enabled = AutoReminderSettings.from_mapping({"ZALO_AUTO_REMINDERS_ENABLED": "true"})
        self.assertTrue(enabled.enabled)

    def test_strict_values_and_errors_do_not_echo_secrets(self):
        for key, value in (("ZALO_AUTO_REMINDERS_ENABLED", "a-private-secret"),
                           ("ZALO_AUTO_REMINDERS_EVENING_TIME", "23:00"),
                           ("ZALO_AUTO_REMINDERS_EVENING_TIME", "8:00"),
                           ("ZALO_AUTO_REMINDERS_LEAD_MINUTES", "0"),
                           ("ZALO_AUTO_REMINDERS_LEAD_MINUTES", "181"),
                           ("ZALO_AUTO_REMINDERS_POLL_SECONDS", "14"),
                           ("TICKETS_LAB_PERIODS_JSON", '{"Sáng":{"start":"13:00","end":"09:00"}}'),
                           ("TICKETS_LAB_PERIODS_JSON", '{"Sáng":{"start":"09:00"}}'),
                           ("TICKETS_LAB_PERIODS_JSON", '[]'),
                           ("TICKETS_LAB_PERIODS_JSON", 'a-private-secret')):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError) as caught:
                AutoReminderSettings.from_mapping({key: value})
            self.assertNotIn("a-private-secret", str(caught.exception))


class SchedulePlannerTests(unittest.TestCase):
    def setUp(self):
        self.settings = AutoReminderSettings(enabled=True, periods=PERIODS)

    def plan(self, when, events=None, **identity):
        return plan_notices(self.settings, events or [schedule(event())], now=instant(when),
                            **(MEMBER | identity))

    def test_evening_exact_boundaries_and_one_tomorrow_digest(self):
        events = [schedule(event(), event(period="Chiều", session="Session 2"))]
        self.assertEqual(self.plan("2026-10-15T19:59:59", events), [])
        at = self.plan("2026-10-15T20:00:00", events)
        self.assertEqual(len(at), 1)
        self.assertEqual(at[0].kind, "evening")
        self.assertIn("16/10/2026", at[0].text)
        self.assertIn("09:00–13:00", at[0].text)
        self.assertIn("14:00–18:00", at[0].text)
        self.assertEqual(at[0].id, self.plan("2026-10-15T22:59:59", events)[0].id)
        self.assertEqual(self.plan("2026-10-15T23:00:00", events), [])
        self.assertEqual(self.plan("2026-10-16T00:01:00", events), [])

    def test_preclass_window_and_past_are_not_sent(self):
        self.assertEqual(self.plan("2026-10-16T08:29:59"), [])
        at = self.plan("2026-10-16T08:30:00")
        self.assertEqual(len(at), 1)
        self.assertEqual(at[0].kind, "before_class")
        self.assertIn("09:00–13:00", at[0].text)
        self.assertIn("check in", at[0].text)
        self.assertEqual(at[0].id, self.plan("2026-10-16T08:59:59")[0].id)
        self.assertEqual(at[0].text, self.plan("2026-10-16T08:59:59")[0].text)
        self.assertEqual(at[0], self.plan("2026-10-16T08:31:00")[0])
        self.assertEqual(self.plan("2026-10-16T09:00:00"), [])
        self.assertEqual(self.plan("2026-10-17T08:30:00"), [])

    def test_vietnam_timezone_not_host_timezone(self):
        vn = self.plan("2026-10-15T20:00:00")[0]
        utc = plan_notices(self.settings, [schedule(event())],
                           now=datetime(2026, 10, 15, 13, tzinfo=timezone.utc), **MEMBER)[0]
        self.assertEqual(vn, utc)
        for bad in (datetime(2026, 10, 15, 20), float("nan"), float("inf"), True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                plan_notices(self.settings, [schedule(event())], now=bad, **MEMBER)

    def test_no_classes_or_disabled_no_notice(self):
        self.assertEqual(self.plan("2026-10-15T20:00:00", [schedule(event(coach_names=["Bình"]))]), [])
        self.assertEqual(plan_notices(AutoReminderSettings(), [schedule(event())], now=0, **MEMBER), [])

    def test_key_coach_partial_casefold_and_combined_names_not_guessed(self):
        for names in (["An"], ["An Nguyen"], ["AN NGUYỄN"], ["An Nguyễn và Bình"], []):
            with self.subTest(names=names):
                events = [schedule(event(coach_names=names, key_coach="An Nguyễn"))]
                self.assertEqual(self.plan("2026-10-16T08:30:00", events), [])

    def test_unknown_hours_visible_evening_but_never_guessed_for_preclass(self):
        events = [schedule(event(period="Chưa rõ giờ"))]
        evening = self.plan("2026-10-15T20:00:00", events)
        self.assertIn("chưa có giờ", evening[0].text)
        self.assertEqual(self.plan("2026-10-16T08:30:00", events), [])

    def test_explicit_hours_take_priority_and_overnight_is_not_guessed(self):
        events = [schedule(event(period="Sáng 10:00–12:00"))]
        self.assertEqual(self.plan("2026-10-16T08:30:00", events), [])
        self.assertIn("10:00–12:00", self.plan("2026-10-16T09:30:00", events)[0].text)
        self.assertEqual(self.plan("2026-10-16T08:30:00", [schedule(event(period="Sáng 09:00–01:00"))]), [])

    def test_next_midnight_preclass_due_the_previous_date(self):
        events = [schedule(event(period="Sáng 00:15–01:00"))]
        self.assertEqual(self.plan("2026-10-15T23:44:59", events), [])
        notices = self.plan("2026-10-15T23:45:00", events)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].kind, "before_class")

    def test_same_session_moved_rows_or_changed_roster_retains_key(self):
        first = self.plan("2026-10-16T08:30:00")[0]
        moved = self.plan("2026-10-16T08:30:00", [schedule(event(first_row=900, last_row=901,
                          coach_names=["Bình", "An Nguyễn"], key_coach="Other"), tab="Renamed")])[0]
        self.assertEqual(first.id, moved.id)
        self.assertEqual(first.event_key, moved.event_key)
        rerouted = self.plan("2026-10-16T08:30:00", chat_id="another-private-chat", user_id="another-user")[0]
        self.assertEqual(first.id, rerouted.id)

    def test_new_owner_gets_distinct_recipient_key_old_owner_disappears(self):
        original = self.plan("2026-10-16T08:30:00")[0]
        updated = [schedule(event(coach_names=["Bình"]))]
        self.assertEqual(self.plan("2026-10-16T08:30:00", updated), [])
        new = self.plan("2026-10-16T08:30:00", updated, member_name="Bình", member_id="MB-002")[0]
        self.assertEqual(original.event_key, new.event_key)
        self.assertNotEqual(original.id, new.id)

    def test_distinct_events_or_times_get_distinct_ids(self):
        original = self.plan("2026-10-16T08:30:00")[0]
        for changed in (event(room="Lab 3"), event(course="Java"), event(session="Session 2")):
            with self.subTest(changed=changed):
                other = self.plan("2026-10-16T08:30:00", [schedule(changed)])[0]
                self.assertNotEqual(original.id, other.id)
        self.settings = replace(self.settings, periods=PERIODS | {"Sáng": {"start": "10:00", "end": "13:00"}})
        shifted = self.plan("2026-10-16T09:30:00")[0]
        self.assertNotEqual(original.id, shifted.id)
        tomorrow = self.plan("2026-10-17T09:30:00", [schedule(event(day="2026-10-17"))])[0]
        self.assertNotEqual(tomorrow.id, shifted.id)

    def test_duplicate_rows_and_tabs_deduplicated_distinct_rooms_retained(self):
        notices = self.plan("2026-10-16T08:30:00", [schedule(event(), event(first_row=20), event(room="Lab 3"))])
        self.assertEqual(len(notices), 2)
        notices = self.plan("2026-10-16T08:30:00", [schedule(event()), schedule(event(), sheet_id=101)])
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices, self.plan("2026-10-16T08:30:00", [schedule(event(), sheet_id=901)]))
        digest = self.plan("2026-10-15T20:00:00", [schedule(event()), schedule(event(), sheet_id=101)])[0]
        self.assertEqual(digest.text.count("Session 1"), 1)

    def test_conflicting_copied_sessions_skip_all_owners_without_guessing(self):
        for others in (["Bình"], ["An Nguyễn", "Bình"], []):
            copies = [schedule(event()), schedule(event(coach_names=others), sheet_id=101)]
            with self.subTest(others=others):
                self.assertEqual(self.plan("2026-10-16T08:30:00", copies), [])
                self.assertEqual(self.plan("2026-10-15T20:00:00", copies), [])
                self.assertEqual(self.plan("2026-10-16T08:30:00", copies,
                                          member_name="Bình", member_id="MB-002"), [])

    def test_duplicate_sessions_with_same_owner_set_order_are_one_session(self):
        copies = [schedule(event(coach_names=["An Nguyễn", "Bình"])),
                  schedule(event(coach_names=["Bình", "An Nguyễn", "Bình"]), sheet_id=101)]
        self.assertEqual(len(self.plan("2026-10-16T08:30:00", copies)), 1)

    def test_digest_key_daily_member_not_roster_content_or_phone(self):
        first = self.plan("2026-10-15T20:00:00")[0]
        modified = self.plan("2026-10-15T20:00:00", [schedule(event(), event(period="Chiều"))])[0]
        self.assertEqual(first.id, modified.id)
        other = self.plan("2026-10-15T20:00:00", member_id="MB-002")[0]
        self.assertNotEqual(first.id, other.id)
        self.assertNotIn("An Nguyễn", first.id)

    def test_long_digest_explicit_omission_and_bounded_fields(self):
        events = [schedule(*(event(session=f"Session {i}", course="K" * 4000, room="R" * 4000)
                             for i in range(40)))]
        notice = self.plan("2026-10-15T20:00:00", events)[0]
        self.assertLessEqual(len(notice.text), MAX_NOTICE_LENGTH)
        self.assertIn("buổi chưa hiển thị", notice.text)
        self.assertIn("/lichcuatoi ngaymai", notice.text)


class ScheduleReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "schedule.sqlite3"
        self.store = ScheduleReminderStore(self.path)
        self.now = instant("2026-10-16T08:30:00")
        self.notice = plan_notices(AutoReminderSettings(enabled=True, periods=PERIODS),
                                   [schedule(event())], now=self.now, **MEMBER)[0]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_sent_deduplicates_after_restart_and_payload_never_replayed(self):
        self.assertTrue(self.store.claim(self.notice, self.now))
        self.assertTrue(self.store.finish(self.notice.id, "sent", now=self.now))
        self.store.close()
        self.store = ScheduleReminderStore(self.path)
        changed = replace(self.notice, text="Nội dung mới", chat_id="different-chat")
        self.assertFalse(self.store.claim(changed, self.now))
        self.assertEqual(self.store.get(self.notice.id)["text"], self.notice.text)
        self.assertEqual(self.store.get(self.notice.id)["status"], "sent")

    def test_claimed_recovers_uncertain_and_cannot_be_replayed(self):
        self.assertTrue(self.store.claim(self.notice, self.now))
        self.store.close()
        self.store = ScheduleReminderStore(self.path)
        self.assertEqual(self.store.recover_claimed(), 1)
        self.assertEqual(self.store.recover_claimed(), 0)
        self.assertEqual(self.store.get(self.notice.id)["status"], "uncertain")
        self.assertFalse(self.store.claim(self.notice, self.now))
        self.assertFalse(self.store.finish(self.notice.id, "sent", now=self.now))

    def test_too_early_or_expired_never_claimed(self):
        self.assertFalse(self.store.claim(self.notice, self.now - timedelta(seconds=1)))
        self.assertFalse(self.store.claim(self.notice, self.notice.expires_at))
        self.assertIsNone(self.store.get(self.notice.id))

    def test_two_connections_cannot_claim_same_delivery(self):
        second = ScheduleReminderStore(self.path)
        outcomes = []
        barrier = threading.Barrier(2)

        def claim(store):
            barrier.wait()
            outcomes.append(store.claim(self.notice, self.now))

        a, b = threading.Thread(target=claim, args=(self.store,)), threading.Thread(target=claim, args=(second,))
        a.start()
        b.start()
        a.join(3)
        b.join(3)
        second.close()
        self.assertEqual(sorted(outcomes), [False, True])

    def test_terminal_failure_states_do_not_retry_implicitly(self):
        for index, state in enumerate(("failed", "blocked", "expired", "uncertain")):
            notice = replace(self.notice, id=f"ID-{index}", event_key=f"EVENT-{index}")
            self.assertTrue(self.store.claim(notice, self.now))
            self.assertTrue(self.store.finish(notice.id, state, now=self.now))
            self.assertFalse(self.store.claim(notice, self.now))
            self.assertEqual(self.store.get(notice.id)["status"], state)

    def test_unique_recipient_key_even_if_caller_changes_notice_id(self):
        self.assertTrue(self.store.claim(self.notice, self.now))
        self.assertFalse(self.store.claim(replace(self.notice, id="another-id"), self.now))


if __name__ == "__main__":
    unittest.main()
