"""Registration resets use synthetic messages and isolated SQLite databases."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from hermes_zalo.events import IncomingMessage
from hermes_zalo.registration import RegistrationError, RegistrationStore


class RegistrationResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'registration.sqlite3'
        self.now, self.seq = 10000.0, 0
        self.store = self.open()
        self.person = self.person_with()
        self.original = self.message()
        self.record = self.store.request(self.original, self.person, auto_approve=True)

    def open(self):
        return RegistrationStore(self.path, admins={'zalo-admin'}, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    @staticmethod
    def person_with(**changes):
        return SimpleNamespace(**(dict(phone='0900000001', name='Người thử A', source_key='synthetic:Workload',
                                       fingerprint='synthetic-a', name_unique=True) | changes))

    def message(self, user='user-a', chat='dm-a'):
        self.seq += 1
        return IncomingMessage(chat, user, str(self.seq), 'PRIVATE', '/dangky synthetic')

    def reset(self, record=None, *, snapshot=None, actor='web:admin', reason='Cho thành viên đăng ký lại'):
        record = record or self.record
        snapshot = self.store.reset_snapshot(record) if snapshot is None else snapshot
        return self.store.reset_registration(actor, record, snapshot, reason)

    def lock(self, member_id, *, locked=True):
        with self.store._transaction() as db:
            self.store._bump_revision(db, member_id)
            db.execute('UPDATE registration_member_controls SET locked=? WHERE member_id=?', (locked, member_id))

    def test_reset_withdraws_access_and_preserves_history_ids_receipts_and_audit_after_restart(self):
        extra_receipt = self.message()
        self.store.request(extra_receipt, self.person, auto_approve=True)
        snapshot = self.store.reset_snapshot(self.record)
        self.assertNotIn(self.person.phone, json.dumps(snapshot))
        result = self.reset(snapshot=snapshot)
        self.assertEqual(replace(self.record, status='reset'), result)
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM registration_members').fetchone()[0])
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])
        self.assertEqual(2, self.store.db.execute('SELECT COUNT(*) FROM registration_receipts').fetchone()[0])
        self.assertIsNone(self.store.approved(self.message()))
        self.assertEqual([], self.store.approved_records(self.person.source_key))
        self.assertEqual([], self.store.members('zalo-admin'))
        self.assertEqual(frozenset({'zalo-admin'}), self.store.admins)
        audit = [dict(row) for row in self.store.db.execute(
            "SELECT * FROM registration_admin_audit WHERE action='registration_reset'")]
        self.assertEqual(1, len(audit))
        self.assertEqual('web:admin', audit[0]['actor'])
        self.assertIn('******0001', audit[0]['before_json'])
        self.assertNotIn(self.person.phone, json.dumps(audit))
        self.store.close()
        self.store = self.open()
        self.assertEqual(result, self.store.user_state('user-a'))
        self.assertIsNone(self.store.approved(self.message()))
        for receipt in (self.original, extra_receipt):
            self.assertEqual(result, self.store.request(receipt, self.person, auto_approve=True))

    def test_new_message_can_autoapprove_same_phone_with_fixed_member_id_and_new_request(self):
        self.reset()
        fresh = self.message()
        result = self.store.request(fresh, self.person, auto_approve=True)
        self.assertEqual('approved', result.status)
        self.assertEqual(self.record.member_id, result.member_id)
        self.assertNotEqual(self.record.id, result.id)
        self.assertEqual(result, self.store.request(fresh, self.person, auto_approve=True))
        self.assertEqual(result, self.store.approved(self.message()))
        self.assertEqual('reset', self.store.request(self.original, self.person, auto_approve=True).status)
        self.assertEqual(result, self.store.approved(self.message()))
        with self.assertRaises(RegistrationError):
            self.store.approve('zalo-admin', self.record.id, self.person)
        self.assertEqual(1, self.store.db.execute(
            "SELECT COUNT(*) FROM registration_events WHERE action='identity_reset_to'").fetchone()[0])

    def test_new_message_can_select_correct_phone_without_changing_old_identity(self):
        self.reset()
        correct = self.person_with(phone='0900000002', name='Người thử đúng', fingerprint='synthetic-b')
        result = self.store.request(self.message(), correct, auto_approve=True)
        self.assertEqual('approved', result.status)
        self.assertNotEqual(self.record.member_id, result.member_id)
        self.assertEqual('reset', self.store.latest_member(self.record.member_id).status)
        self.assertEqual(self.person.phone, self.store.latest_member(self.record.member_id).phone)
        self.assertEqual((self.record.user_id, self.record.chat_id), (result.user_id, result.chat_id))

    def test_reset_can_explicitly_clear_pending_rejected_or_revoked_decision(self):
        for status in ('pending', 'rejected', 'revoked'):
            with self.subTest(status=status):
                person = self.person_with(phone={'pending': '0900000002', 'rejected': '0900000003', 'revoked': '0900000004'}[status])
                record = self.store.request(self.message(status, 'dm-' + status), person)
                if status == 'rejected':
                    record = self.store.reject('zalo-admin', record.id)
                elif status == 'revoked':
                    record = self.store.revoke('zalo-admin', record.member_id)
                self.assertEqual('reset', self.reset(record).status)
                fresh = self.store.request(self.message(status, 'dm-' + status), person, auto_approve=True)
                self.assertEqual('approved', fresh.status)

    def test_ordinary_revoke_after_reset_registration_still_prevents_autoapproval(self):
        self.reset()
        fresh = self.store.request(self.message(), self.person, auto_approve=True)
        self.store.revoke('zalo-admin', fresh.member_id)
        pending = self.store.request(self.message(), self.person, auto_approve=True)
        self.assertEqual('pending', pending.status)
        self.assertEqual('pending', self.store.request(self.message(), self.person, auto_approve=True).status)

    def test_old_receipt_cannot_be_repurposed_for_a_different_phone_after_reset(self):
        self.reset()
        with self.assertRaises(RegistrationError):
            self.store.request(self.original, self.person_with(phone='0900000002'), auto_approve=True)
        self.assertIsNone(self.store.approved(self.message()))
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])

    def test_reset_does_not_clear_phone_or_global_attempt_limits(self):
        for _ in range(5):
            self.store.check_attempt(self.message())
        self.reset()
        with self.assertRaisesRegex(RegistrationError, 'giới hạn'):
            self.store.check_attempt(self.message())

    def test_snapshot_is_readonly_and_repeat_confirmation_is_rejected(self):
        for _ in range(3):
            snapshot = self.store.reset_snapshot(self.record)
        before_events = self.store.db.execute('SELECT COUNT(*) FROM registration_events').fetchone()[0]
        result = self.reset(snapshot=snapshot)
        with self.assertRaises(RegistrationError):
            self.reset(snapshot=snapshot)
        with self.assertRaises(RegistrationError):
            self.reset(result)
        self.assertEqual(before_events + 1, self.store.db.execute('SELECT COUNT(*) FROM registration_events').fetchone()[0])

    def test_stale_status_record_or_control_revision_cannot_reset(self):
        snapshot = self.store.reset_snapshot(self.record)
        with self.store._transaction() as db:
            self.store._bump_revision(db, self.record.member_id)
        with self.assertRaises(RegistrationError):
            self.reset(snapshot=snapshot)
        with self.assertRaises(RegistrationError):
            self.reset(replace(self.record, name='Forged'))
        snapshot = self.store.reset_snapshot(self.record)
        self.store.revoke('zalo-admin', self.record.member_id)
        with self.assertRaises(RegistrationError):
            self.reset(snapshot=snapshot)

    def test_old_identity_cannot_reset_route_after_a_correction(self):
        person = self.person_with(phone='0900000002', name='Người thử B', fingerprint='synthetic-b')
        corrected = self.store.correct_registration('web:admin', self.record, person,
                                                   self.store.correction_snapshot(self.record, person), 'Sửa đúng tài khoản')
        old = self.store.latest_member(self.record.member_id)
        with self.assertRaisesRegex(RegistrationError, 'mới hơn'):
            self.reset(old)
        self.assertEqual(corrected, self.store.approved(self.message()))

    def test_locked_current_and_historical_route_are_refused_until_explicit_unlock(self):
        self.lock(self.record.member_id)
        with self.assertRaisesRegex(RegistrationError, 'khóa'):
            self.reset()
        self.lock(self.record.member_id, locked=False)
        self.reset()
        result = self.store.request(self.message(), self.person, auto_approve=True)
        self.assertEqual('approved', result.status)
        # An ordinary revoked historical identity stays in force.
        self.store.revoke('zalo-admin', result.member_id)
        second = self.store.request(self.message(), self.person_with(phone='0900000002'), auto_approve=True)
        self.lock(self.record.member_id)
        with self.assertRaisesRegex(RegistrationError, 'khóa'):
            self.reset(second)

    def test_lock_applied_between_reset_and_fresh_registration_still_blocks(self):
        self.reset()
        self.lock(self.record.member_id)
        with self.assertRaisesRegex(RegistrationError, 'khóa'):
            self.store.request(self.message(), self.person_with(phone='0900000002'), auto_approve=True)

    def test_lock_on_old_wrong_member_does_not_block_completed_reset_to_new_identity(self):
        self.reset()
        person = self.person_with(phone='0900000002', name='Người thử B', fingerprint='synthetic-b')
        result = self.store.request(self.message(), person, auto_approve=True)
        self.lock(self.record.member_id)
        self.assertFalse(self.store.route_locked(result.user_id))
        self.assertFalse(self.store.route_locked(result.user_id, result.chat_id))
        self.assertEqual(result, self.store.approved(self.message()))
        self.store.close()
        self.store = self.open()
        self.assertEqual(result, self.store.approved(self.message()))
        self.lock(result.member_id)
        self.assertTrue(self.store.route_locked(result.user_id))
        self.assertIsNone(self.store.approved(self.message()))

    def test_reset_of_same_member_does_not_let_new_registration_escape_its_lock(self):
        self.reset()
        result = self.store.request(self.message(), self.person, auto_approve=True)
        self.lock(self.record.member_id)
        self.assertEqual(self.record.member_id, result.member_id)
        self.assertTrue(self.store.route_locked(result.user_id))
        self.assertIsNone(self.store.approved(self.message()))

    def test_changed_user_or_chat_after_reset_requires_manual_review(self):
        for suffix, user, chat in (('a', 'user-a', 'new-dm'), ('b', 'new-user', 'dm-b')):
            person = self.person_with(phone='0900000003' if suffix == 'a' else '0900000004')
            old = self.store.request(self.message('user-' + suffix, 'dm-' + suffix), person) if suffix != 'a' else self.record
            if suffix == 'a':
                person = self.person
            self.reset(old)
            fresh = self.store.request(self.message(user, chat), person, auto_approve=True)
            self.assertEqual('pending', fresh.status)

    def test_other_member_owner_and_revoked_target_are_never_automatically_overwritten(self):
        person = self.person_with(phone='0900000002', name='Người thử B', fingerprint='synthetic-b')
        owner = self.store.request(self.message('user-b', 'dm-b'), person, auto_approve=True)
        self.reset()
        with self.assertRaisesRegex(RegistrationError, 'đăng ký khác'):
            self.store.request(self.message(), person, auto_approve=True)
        self.assertEqual(owner, self.store.approved(self.message('user-b', 'dm-b')))
        self.store.revoke('zalo-admin', owner.member_id)
        fresh = self.store.request(self.message(), person, auto_approve=True)
        self.assertEqual('pending', fresh.status)
        self.assertIsNone(self.store.approved(self.message()))

    def test_duplicate_parallel_reset_only_applies_once(self):
        snapshot = self.store.reset_snapshot(self.record)
        def apply(_):
            store = self.open()
            try:
                return store.reset_registration('web:admin', self.record, snapshot, 'Cho đăng ký lại')
            except RegistrationError:
                return None
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(apply, range(2)))
        self.assertEqual(1, sum(result is not None for result in results))

    def test_audit_failure_rolls_back_reset(self):
        self.store.db.execute("CREATE TRIGGER deny_reset BEFORE INSERT ON registration_events "
                              "WHEN NEW.action='registration_reset' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        self.store.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.reset()
        self.assertEqual(self.record, self.store.approved(self.message()))

    def test_actor_and_reason_require_explicit_web_attribution_without_phone_leak(self):
        for actor in ('', 'member', 'web:', None):
            with self.assertRaises(RegistrationError):
                self.reset(actor=actor)
        for reason in ('', 'abc', 'x' * 301, 'Đổi số 0900000001', 'lý do\nkhác'):
            with self.assertRaises(RegistrationError) as raised:
                self.reset(reason=reason)
            self.assertNotIn(self.person.phone, str(raised.exception))
        self.assertEqual(self.record, self.store.approved(self.message()))

    def test_clock_moving_backwards_does_not_hide_fresh_approved_request(self):
        self.now -= 100
        self.reset()
        result = self.store.request(self.message(), self.person, auto_approve=True)
        self.assertEqual('approved', result.status)
        self.assertEqual(result, self.store.approved(self.message()))
        self.store.revoke('zalo-admin', result.member_id)
        pending = self.store.request(self.message(), self.person, auto_approve=True)
        self.assertEqual('pending', pending.status)


if __name__ == '__main__':
    unittest.main()
