"""Administrator identity corrections use synthetic rosters and local SQLite only."""
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


class RegistrationCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'registration.sqlite3'
        self.now, self.seq = 10000.0, 0
        self.store = self.open()
        self.wrong = self.person('0900000001', 'Người thử sai')
        self.right = self.person('0900000002', 'Người thử đúng')
        self.source = self.store.request(self.message(), self.wrong, auto_approve=True)

    def open(self):
        return RegistrationStore(self.path, admins={'zalo-admin'}, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    @staticmethod
    def person(phone, name):
        return SimpleNamespace(phone=phone, name=name, source_key='synthetic:workload',
                               fingerprint='row-' + name, name_unique=True)

    def message(self, user='user-a', chat='dm-a'):
        self.seq += 1
        return IncomingMessage(chat, user, str(self.seq), 'PRIVATE', '/dangky synthetic')

    def snapshot(self, record=None, person=None):
        return self.store.correction_snapshot(record or self.source, person or self.right)

    def correct(self, *, record=None, person=None, snapshot=None, actor='web:admin', reason='Xác minh đúng chủ tài khoản'):
        record, person = record or self.source, person or self.right
        if snapshot is None:
            snapshot = self.snapshot(record, person)
        return self.store.correct_registration(actor, record, person, snapshot, reason)

    def lock(self, member_id):
        with self.store._transaction() as db:
            self.store._bump_revision(db, member_id)
            db.execute('UPDATE registration_member_controls SET locked=1 WHERE member_id=?', (member_id,))

    def test_correction_creates_new_identity_keeps_route_and_persists_without_admin_grants(self):
        before_members = [tuple(row) for row in self.store.db.execute('SELECT * FROM registration_members')]
        snapshot = self.snapshot()
        self.assertNotIn(self.wrong.phone, json.dumps(snapshot))
        self.assertNotIn(self.right.phone, json.dumps(snapshot))
        corrected = self.correct(snapshot=snapshot)
        self.assertNotEqual(self.source.id, corrected.id)
        self.assertNotEqual(self.source.member_id, corrected.member_id)
        self.assertEqual((self.source.user_id, self.source.chat_id), (corrected.user_id, corrected.chat_id))
        self.assertEqual((self.right.name, self.right.phone, 'approved'), (corrected.name, corrected.phone, corrected.status))
        old = self.store.latest_member(self.source.member_id)
        self.assertEqual(replace(self.source, status='revoked'), old)
        self.assertEqual(before_members[0], tuple(self.store.db.execute(
            'SELECT * FROM registration_members WHERE member_id=?', (self.source.member_id,)).fetchone()))
        self.assertEqual(frozenset({'zalo-admin'}), self.store.admins)
        with self.assertRaises(RegistrationError):
            self.store.pending('web:admin')
        self.store.close()
        self.store = self.open()
        self.assertEqual(corrected, self.store.approved(self.message()))
        self.assertEqual('revoked', self.store.latest_member(self.source.member_id).status)

    def test_preview_does_not_create_members_or_requests(self):
        for _ in range(3):
            self.snapshot()
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM registration_members').fetchone()[0])
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])

    def test_audit_contains_masked_phones_actor_reason_and_both_identity_ids(self):
        result = self.correct()
        rows = [dict(row) for row in self.store.db.execute(
            "SELECT * FROM registration_admin_audit WHERE action LIKE 'identity_corrected_%' ORDER BY id")]
        self.assertEqual(2, len(rows))
        self.assertEqual({self.source.member_id, result.member_id}, {row['member_id'] for row in rows})
        self.assertEqual({'web:admin'}, {row['actor'] for row in rows})
        serialized = json.dumps(rows, ensure_ascii=False)
        self.assertIn('******0001', serialized)
        self.assertIn('******0002', serialized)
        self.assertIn('Xác minh đúng chủ tài khoản', serialized)
        self.assertNotIn(self.wrong.phone, serialized)
        self.assertNotIn(self.right.phone, serialized)

    def test_reuses_target_fixed_member_id_after_revocation(self):
        target = self.store.request(self.message('old-user', 'old-dm'), self.right, auto_approve=True)
        self.store.revoke('zalo-admin', target.member_id)
        corrected = self.correct()
        self.assertEqual(target.member_id, corrected.member_id)
        self.assertEqual(2, self.store.db.execute('SELECT COUNT(*) FROM registration_members').fetchone()[0])

    def test_active_or_pending_target_is_never_taken_from_another_person(self):
        target = self.store.request(self.message('other-user', 'other-dm'), self.right)
        for state in ('pending', 'approved'):
            if state == 'approved':
                self.store.approve('zalo-admin', target.id, self.right)
            with self.assertRaisesRegex(RegistrationError, 'đăng ký khác'):
                self.snapshot()
        self.assertEqual(self.source, self.store.approved(self.message()))

    def test_same_identity_duplicate_names_and_other_source_are_refused(self):
        for person in (self.wrong, SimpleNamespace(**(vars(self.right) | {'name_unique': False})),
                       SimpleNamespace(**(vars(self.right) | {'source_key': 'other:roster'}))):
            with self.subTest(person=person.name):
                with self.assertRaises(RegistrationError):
                    self.snapshot(person=person)

    def test_locked_source_target_and_historical_route_are_refused(self):
        self.lock(self.source.member_id)
        with self.assertRaisesRegex(RegistrationError, 'khóa'):
            self.snapshot()
        with self.store._transaction() as db:
            db.execute('UPDATE registration_member_controls SET locked=0')
        target = self.store.request(self.message('other-user', 'other-dm'), self.right)
        self.store.revoke('zalo-admin', target.member_id)
        self.lock(target.member_id)
        with self.assertRaisesRegex(RegistrationError, 'khóa'):
            self.snapshot()
        # A historical locked identity on this route cannot be escaped by a
        # correction, even when the currently selected identity is unlocked.
        with self.store._transaction() as db:
            db.execute('UPDATE registration_requests SET user_id=?,chat_id=? WHERE id=?',
                       (self.source.user_id, self.source.chat_id, target.id))
        with self.assertRaises(RegistrationError):
            self.snapshot()

    def test_actor_and_reason_validation_rejects_phone_numbers_without_echo(self):
        for actor in ('admin', '', None, 'web:', 'web:admin\n'):
            with self.assertRaises(RegistrationError):
                self.correct(actor=actor)
        for reason in ('', 'abc', 'x' * 301, 'abc\nxyz', 'Số đúng 0900000002',
                       'Số đúng +84 90 000 0002', 'Số đúng 090.000.0002'):
            with self.assertRaises(RegistrationError) as raised:
                self.correct(reason=reason)
            self.assertNotIn(self.right.phone, str(raised.exception))
        self.assertEqual(self.source, self.store.approved(self.message()))

    def test_stale_source_control_or_target_snapshot_cannot_confirm(self):
        snapshot = self.snapshot()
        with self.store._transaction() as db:
            self.store._bump_revision(db, self.source.member_id)
        with self.assertRaises(RegistrationError):
            self.correct(snapshot=snapshot)
        snapshot = self.snapshot()
        target = self.store.request(self.message('old-user', 'old-dm'), self.right)
        self.store.revoke('zalo-admin', target.member_id)
        with self.assertRaises(RegistrationError):
            self.correct(snapshot=snapshot)
        snapshot = self.snapshot()
        with self.store._transaction() as db:
            self.store._bump_revision(db, target.member_id)
        with self.assertRaises(RegistrationError):
            self.correct(snapshot=snapshot)

    def test_changed_person_fields_and_record_snapshot_cannot_confirm(self):
        snapshot = self.snapshot()
        changed = SimpleNamespace(**(vars(self.right) | {'fingerprint': 'changed'}))
        with self.assertRaises(RegistrationError):
            self.correct(snapshot=snapshot, person=changed)
        with self.assertRaises(RegistrationError):
            self.correct(record=replace(self.source, name='Forged name'), snapshot=snapshot)
        self.store.revoke('zalo-admin', self.source.member_id)
        with self.assertRaises(RegistrationError):
            self.correct(snapshot=snapshot)

    def test_newer_historical_route_or_identity_blocks_old_registration(self):
        # Simulate imported legacy data; even an active source cannot transfer
        # a route whose newest known request is a different row.
        target = self.store.request(self.message('old-user', 'old-dm'), self.right)
        self.store.revoke('zalo-admin', target.member_id)
        with self.store._transaction() as db:
            db.execute('UPDATE registration_requests SET user_id=?,created=? WHERE id=?',
                       (self.source.user_id, self.now + 1, target.id))
        with self.assertRaisesRegex(RegistrationError, 'mới hơn'):
            self.snapshot()

    def test_repeated_confirm_never_creates_duplicate_new_registration(self):
        snapshot = self.snapshot()
        result = self.correct(snapshot=snapshot)
        with self.assertRaises(RegistrationError):
            self.correct(snapshot=snapshot)
        self.assertEqual(2, self.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])
        self.assertEqual(result, self.store.approved(self.message()))

    def test_atomic_rollback_restores_original_grant_on_audit_failure(self):
        self.store.db.execute("CREATE TRIGGER deny_correction BEFORE INSERT ON registration_events "
                              "WHEN NEW.action='identity_corrected_to' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        self.store.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.correct()
        self.assertEqual(self.source, self.store.approved(self.message()))
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM registration_members').fetchone()[0])
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])
        self.assertEqual(0, self.store.db.execute(
            "SELECT COUNT(*) FROM registration_admin_audit WHERE action LIKE 'identity_corrected_%'").fetchone()[0])

    def test_two_admins_cannot_claim_same_target_concurrently(self):
        second_person = self.person('0900000003', 'Người thử thứ ba')
        second = self.store.request(self.message('user-b', 'dm-b'), second_person, auto_approve=True)
        inputs = [(self.source, self.snapshot()), (second, self.snapshot(record=second))]
        def apply(item):
            record, snapshot = item
            store = self.open()
            try:
                return store.correct_registration('web:admin', record, self.right, snapshot, 'Đã kiểm tra chủ tài khoản')
            except RegistrationError:
                return None
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(apply, inputs))
        self.assertEqual(1, sum(result is not None for result in results))
        self.assertEqual(1, self.store.db.execute(
            "SELECT COUNT(*) FROM registration_requests WHERE phone=? AND status='approved'", (self.right.phone,)).fetchone()[0])

    def test_clock_moving_backwards_does_not_hide_corrected_registration(self):
        self.now -= 100
        result = self.correct()
        self.assertEqual(result, self.store.approved(self.message()))


if __name__ == '__main__':
    unittest.main()
