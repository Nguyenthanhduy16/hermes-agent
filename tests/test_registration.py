"""Registration persistence/authorization tests with synthetic people only."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sqlite3
import unittest

from hermes_zalo.events import IncomingMessage
from hermes_zalo.registration import RegistrationError, RegistrationStore


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'registration.sqlite3'
        self.now, self.seq = 10000.0, 0
        self.open()
        self.person = SimpleNamespace(phone='0900000001', name='Người Thử A', source_key='synthetic-sheet:Workload',
                                      fingerprint='synthetic-person-fingerprint', name_unique=True)

    def open(self):
        self.store = RegistrationStore(self.path, admins=frozenset({'admin'}), clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def message(self, user='a', *, chat=None, kind='PRIVATE', mid=None):
        self.seq += 1
        return IncomingMessage(chat or 'dm-' + user, user, mid or str(self.seq), kind, '/dangky synthetic')

    def person_with(self, **changes):
        return SimpleNamespace(**(vars(self.person) | changes))

    def test_private_request_approval_and_exact_scope_persist_after_restart(self):
        message = self.message()
        self.store.check_attempt(message)
        request = self.store.request(message, self.person)
        self.assertRegex(request.id, r'^DK-[A-F0-9]{8}$')
        self.assertRegex(request.member_id, r'^member-[a-f0-9]{32}$')
        self.assertEqual('pending', request.status)
        self.assertIsNone(self.store.approved(message))
        self.assertEqual(request, self.store.get_for_admin('admin', request.id))
        approved = self.store.approve('admin', request.id, self.person)
        self.assertEqual('approved', approved.status)
        self.assertEqual(request.member_id, approved.member_id)
        self.store.close()
        self.open()
        self.assertEqual(approved, self.store.approved(message))
        self.assertEqual(approved, self.store.user_state('a'))
        self.assertIsNone(self.store.approved(self.message(chat='another-dm')))
        self.assertIsNone(self.store.status(self.message('b', chat='dm-a')))
        self.assertIsNone(self.store.approved(self.message('b')))
        self.assertNotIn(self.person.phone, repr(approved))

    def test_group_requests_and_reads_are_rejected_without_creating_records(self):
        message = self.message(kind='GROUP')
        for operation in (lambda: self.store.check_attempt(message), lambda: self.store.request(message, self.person),
                          lambda: self.store.status(message), lambda: self.store.approved(message)):
            with self.assertRaises(RegistrationError):
                operation()
        for table in ('registration_requests', 'registration_members', 'registration_attempts'):
            self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0])

    def test_auto_approval_is_atomic_persistent_idempotent_and_audited_as_system(self):
        message = self.message()
        first = self.store.request(message, self.person, auto_approve=True)
        self.assertEqual('approved', first.status)
        for again in (message, self.message()):
            self.assertEqual(first, self.store.request(again, self.person, auto_approve=True))
        self.assertEqual([('system:workload-auto', 'auto_approved')], [tuple(r) for r in self.store.db.execute(
            'SELECT actor,action FROM registration_events')])
        self.assertEqual([], self.store.pending('admin'))
        self.store.close()
        self.open()
        self.assertEqual(first, self.store.approved(message))
        with self.assertRaises(RegistrationError):
            self.store.pending(message.user_id)

    def test_auto_approval_upgrades_matching_pending_and_preserves_member_id(self):
        first = self.store.request(self.message(), self.person)
        approved = self.store.request(self.message(), self.person, auto_approve=True)
        self.assertEqual((first.id, first.member_id), (approved.id, approved.member_id))
        self.assertEqual('approved', approved.status)
        with self.assertRaises(RegistrationError):
            self.store.request(self.message(), self.person_with(fingerprint='changed'), auto_approve=True)

    def test_auto_approval_never_reopens_admin_rejection_or_revocation(self):
        for revoked in (False, True):
            user = 'revoked-user' if revoked else 'rejected-user'
            person = self.person_with(phone='0900000002' if revoked else '0900000001')
            original = self.message(user)
            first = self.store.request(original, person)
            if revoked:
                self.store.approve('admin', first.id, person)
                self.store.revoke('admin', first.member_id)
            else:
                self.store.reject('admin', first.id)
            self.assertIn(self.store.request(original, person, auto_approve=True).status, ('rejected', 'revoked'))
            pending = self.store.request(self.message(user), person, auto_approve=True)
            self.assertEqual('pending', pending.status)
            self.assertEqual(first.member_id, pending.member_id)
            self.assertEqual('pending', self.store.request(self.message(user), person, auto_approve=True).status)
            approved = self.store.approve('admin', pending.id, person)
            self.assertEqual(approved, self.store.request(self.message(user), person, auto_approve=True))

    def test_auto_approval_hold_follows_user_chat_and_member_independently(self):
        first = self.store.request(self.message(), self.person, auto_approve=True)
        self.store.revoke('admin', first.member_id)
        cases = ((self.message('a', chat='new-chat'), self.person_with(phone='0900000002')),
                 (self.message('new-user', chat='dm-a'), self.person_with(phone='0900000003')),
                 (self.message('new-owner'), self.person))
        for message, person in cases:
            with self.subTest(scope=message.user_id):
                self.assertEqual('pending', self.store.request(message, person, auto_approve=True).status)

    def test_auto_approval_race_cannot_assign_one_phone_to_two_users(self):
        messages = [self.message('auto-' + str(i)) for i in range(8)]
        def claim(message):
            other = RegistrationStore(self.path, admins={'admin'})
            try:
                return other.request(message, self.person, auto_approve=True)
            except RegistrationError:
                return None
            finally:
                other.close()
        with ThreadPoolExecutor(max_workers=8) as executor:
            accepted = [r for r in executor.map(claim, messages) if r]
        self.assertEqual(1, len(accepted))
        self.assertEqual('approved', accepted[0].status)

    def test_auto_approval_failure_rolls_back_identity_and_receipt(self):
        self.store.db.execute("CREATE TRIGGER refuse_auto BEFORE INSERT ON registration_events "
                              "WHEN NEW.action='auto_approved' BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        self.store.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.request(self.message(), self.person, auto_approve=True)
        for table in ('registration_requests', 'registration_members', 'registration_receipts', 'registration_events'):
            self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0])

    def test_auto_mode_flag_and_group_cannot_bypass_request_validation(self):
        for flag in ('true', 'false', 1, None):
            with self.assertRaises(RegistrationError):
                self.store.request(self.message(), self.person, auto_approve=flag)
        with self.assertRaises(RegistrationError):
            self.store.request(self.message(kind='GROUP'), self.person, auto_approve=True)

    def test_admin_acl_applies_to_reads_and_all_decisions(self):
        request = self.store.request(self.message(), self.person)
        for admin in ('other', '', None):
            for operation in (lambda: self.store.pending(admin), lambda: self.store.get_for_admin(admin, request.id),
                              lambda: self.store.members(admin),
                              lambda: self.store.approve(admin, request.id, self.person),
                              lambda: self.store.reject(admin, request.id), lambda: self.store.revoke(admin, request.member_id)):
                with self.assertRaises(RegistrationError):
                    operation()
        self.assertEqual('pending', self.store.user_state('a').status)

    def test_idempotent_request_never_overwrites_or_resurrects_old_decision(self):
        message = self.message(mid='original')
        request = self.store.request(message, self.person)
        self.assertEqual(request, self.store.request(message, self.person))
        repeated_message = self.message()
        self.assertEqual(request, self.store.request(repeated_message, self.person))
        for person in (self.person_with(phone='0900000002'), self.person_with(name='Người Thử B'),
                       self.person_with(source_key='other-sheet'), self.person_with(fingerprint='changed'),
                       self.person_with(name_unique=False)):
            with self.assertRaises(RegistrationError):
                self.store.request(self.message(), person)
        self.store.reject('admin', request.id)
        self.assertEqual('rejected', self.store.request(message, self.person).status)
        self.assertEqual('rejected', self.store.request(repeated_message, self.person).status)
        replacement = self.store.request(self.message(), self.person)
        self.assertNotEqual(request.id, replacement.id)
        self.assertEqual(request.member_id, replacement.member_id)
        self.assertEqual('pending', replacement.status)

    def test_existing_user_chat_or_person_cannot_be_linked_again(self):
        request = self.store.request(self.message(), self.person)
        self.store.approve('admin', request.id, self.person)
        attempts = [(self.message('b'), self.person),
                    (self.message('b', chat='dm-a'), self.person_with(phone='0900000002')),
                    (self.message('a', chat='dm-other'), self.person),
                    (self.message('a'), self.person_with(phone='0900000002'))]
        for message, person in attempts:
            with self.assertRaises(RegistrationError) as raised:
                self.store.request(message, person)
            self.assertNotIn(person.phone, str(raised.exception))
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])

    def test_concurrent_accounts_cannot_claim_the_same_person(self):
        messages = [self.message('u' + str(i)) for i in range(8)]
        def request(message):
            try:
                return self.store.request(message, self.person)
            except RegistrationError:
                return None
        with ThreadPoolExecutor(max_workers=8) as executor:
            accepted = [row for row in executor.map(request, messages) if row is not None]
        self.assertEqual(1, len(accepted))
        self.store.approve('admin', accepted[0].id, self.person)
        self.assertEqual(1, self.store.db.execute("SELECT COUNT(*) FROM registration_requests WHERE status='approved'").fetchone()[0])

    def test_approval_checks_fresh_source_snapshot_and_allows_duplicate_names_without_identity_inference(self):
        request = self.store.request(self.message(), self.person)
        for person in (self.person_with(phone='0900000002'), self.person_with(name='Changed'),
                       self.person_with(source_key='another-sheet'), self.person_with(fingerprint='changed'),
                       self.person_with(name_unique=False)):
            with self.assertRaises(RegistrationError):
                self.store.approve('admin', request.id, person)
        self.assertEqual('pending', self.store.user_state('a').status)
        self.store.reject('admin', request.id)
        duplicate_name = self.person_with(name_unique=False)
        new = self.store.request(self.message(), duplicate_name)
        approved = self.store.approve('admin', new.id, duplicate_name)
        self.assertFalse(approved.name_unique)
        self.assertEqual(request.member_id, approved.member_id)

    def test_revoke_immediately_disables_approval_and_reregister_keeps_member_id(self):
        message = self.message()
        request = self.store.request(message, self.person)
        self.store.approve('admin', request.id, self.person)
        revoked = self.store.revoke('admin', request.member_id)
        self.assertEqual('revoked', revoked.status)
        self.assertIsNone(self.store.approved(message))
        self.assertEqual(revoked, self.store.revoke('admin', request.member_id))
        with self.assertRaises(RegistrationError):
            self.store.approve('admin', request.id, self.person)
        replacement = self.store.request(self.message(), self.person_with(name='Tên Đã Sửa', fingerprint='updated-name'))
        self.assertEqual(request.member_id, replacement.member_id)
        self.assertNotEqual(request.id, replacement.id)
        self.assertIsNone(self.store.approved(message))

    def test_persistent_attempt_limit_and_boundary_with_replays(self):
        first = self.message()
        self.store.check_attempt(first)
        self.store.check_attempt(first)
        for _ in range(4):
            self.store.check_attempt(self.message())
        self.store.close()
        self.open()
        with self.assertRaises(RegistrationError):
            self.store.check_attempt(self.message(chat='other-dm'))
        self.now += 3599
        with self.assertRaises(RegistrationError):
            self.store.check_attempt(self.message())
        self.now += 1
        self.store.check_attempt(self.message())
        self.assertEqual(6, self.store.db.execute('SELECT COUNT(*) FROM registration_attempts').fetchone()[0])

    def test_global_attempt_limit_survives_different_users(self):
        for number in range(60):
            self.store.check_attempt(self.message('u' + str(number)))
        with self.assertRaises(RegistrationError):
            self.store.check_attempt(self.message('additional-user'))
        self.assertEqual(60, self.store.db.execute('SELECT COUNT(*) FROM registration_attempts').fetchone()[0])
        self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])

    def test_pending_pagination_is_bounded_and_unknown_people_are_not_stored(self):
        for number in range(12):
            self.store.request(self.message('u' + str(number)), self.person_with(phone=f'090000{number:04d}'))
        first, second = self.store.pending('admin'), self.store.pending('admin', page=2)
        self.assertEqual((10, 2), (len(first), len(second)))
        self.assertFalse({row.id for row in first} & {row.id for row in second})
        for args in ({'page': 0}, {'limit': 26}, {'page': True}, {'limit': -1}):
            with self.assertRaises(RegistrationError):
                self.store.pending('admin', **args)
        for person in (None, {}, self.person_with(phone='invalid-private-input')):
            with self.assertRaises(RegistrationError) as raised:
                self.store.request(self.message('unknown'), person)
            self.assertNotIn('invalid-private-input', str(raised.exception))
        self.assertEqual(12, self.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])

    def test_members_lists_only_approved_current_source_with_pagination_and_revoke(self):
        approved = []
        for index in range(13):
            person = self.person_with(phone=f'090000{index:04d}', name=f'Thành viên {index}')
            record = self.store.request(self.message('u' + str(index)), person)
            if index < 11:
                approved.append(self.store.approve('admin', record.id, person))
        other = self.person_with(phone='0900000099', source_key='other-source')
        record = self.store.request(self.message('other'), other)
        self.store.approve('admin', record.id, other)
        self.store.revoke('admin', approved[-1].member_id)
        self.store.close()
        self.open()
        first = self.store.members('admin', source_key=self.person.source_key)
        second = self.store.members('admin', source_key=self.person.source_key, page=2)
        self.assertEqual(approved[:5], first)
        self.assertEqual(approved[5:10], second)
        self.assertEqual([], self.store.members('admin', source_key=self.person.source_key, page=3))
        self.assertEqual(11, len(self.store.members('admin', limit=25)))
        for args in ({'page': 0}, {'page': True}, {'limit': 26}, {'source_key': ''}):
            with self.assertRaises(RegistrationError):
                self.store.members('admin', **args)


if __name__ == '__main__':
    unittest.main()
