"""Self-service password changes with synthetic accounts and disposable stores."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from hermes_zalo.admin_auth import AdminAuth, AdminError, password_hash, password_matches


class AdminPasswordTests(unittest.TestCase):
    OLD = 'Synthetic-original-password-2026'
    NEW = 'Synthetic-new-password-2026'

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'auth.sqlite3'
        self.now = 1_800_000_000
        self.auth = AdminAuth(self.path, clock=lambda: self.now)
        self.auth.create_account('operator', self.OLD, [])
        self.session = self.login()

    def tearDown(self):
        self.auth.close()
        self.temp.cleanup()

    def login(self, username='operator', password=None):
        anonymous, _ = self.auth.session(None, create=True)
        return self.auth.login(anonymous, username, password or self.OLD)[0]

    def payload(self, **changes):
        return dict(current_password=self.OLD, new_password=self.NEW, confirm_password=self.NEW) | changes

    def assert_error(self, status, action):
        with self.assertRaises(AdminError) as caught:
            action()
        self.assertEqual(status, caught.exception.status)

    def test_changes_only_own_password_revokes_every_own_session_and_persists(self):
        other_session = self.login()
        self.auth.create_account('teacher', self.OLD, ['issues'], role='faculty')
        teacher = self.login('teacher')
        before = dict(self.auth.db.execute("SELECT * FROM admin_accounts WHERE username='operator'").fetchone())
        self.assertEqual({'ok': True}, self.auth.change_password(self.session, self.payload()))
        for revoked in (self.session, other_session):
            self.assert_error(401, lambda: self.auth.user(revoked))
        self.assertEqual('teacher', self.auth.user(teacher)['username'])
        after = dict(self.auth.db.execute("SELECT * FROM admin_accounts WHERE username='operator'").fetchone())
        for key in ('username', 'display_name', 'permissions', 'role', 'active'):
            self.assertEqual(before[key], after[key])
        self.assertNotEqual(before['password'], after['password'])
        self.assertTrue(password_matches(self.NEW, after['password']))
        audit = dict(self.auth.db.execute("SELECT * FROM admin_auth_audit WHERE action='password_changed'").fetchone())
        self.assertEqual('web:operator', audit['actor'])
        self.assertEqual('operator', audit['target'])
        for secret in (self.OLD, self.NEW, before['password'], after['password'], self.session['csrf']):
            self.assertNotIn(secret, json.dumps(audit))
        self.auth.close()
        self.auth = AdminAuth(self.path, clock=lambda: self.now)
        self.assert_error(401, lambda: self.login())
        self.assertEqual([], self.auth.user(self.login(password=self.NEW))['permissions'])

    def test_rejects_wrong_current_password_and_validates_repeat_without_mutation(self):
        before = self.auth.db.execute("SELECT password FROM admin_accounts WHERE username='operator'").fetchone()[0]
        for data in (
            self.payload(current_password='Wrong-synthetic-password'),
            self.payload(new_password='short', confirm_password='short'),
            self.payload(confirm_password='Does-not-match-new-password'),
            self.payload(new_password=self.OLD, confirm_password=self.OLD),
            self.payload(username='teacher'), self.payload(role='faculty'),
        ):
            self.assert_error(400, lambda: self.auth.change_password(self.session, data))
        self.assertEqual(before, self.auth.db.execute("SELECT password FROM admin_accounts WHERE username='operator'").fetchone()[0])
        self.assertEqual('operator', self.auth.user(self.session)['username'])
        self.assertEqual(0, self.auth.db.execute("SELECT COUNT(*) FROM admin_auth_audit WHERE action='password_changed'").fetchone()[0])

    def test_anonymous_forged_and_revoked_sessions_cannot_change_password(self):
        anonymous, _ = self.auth.session(None, create=True)
        self.assert_error(401, lambda: self.auth.change_password(anonymous, self.payload()))
        self.auth.create_account('teacher', self.OLD, [], role='faculty')
        self.assert_error(401, lambda: self.auth.change_password(dict(self.session, username='teacher'), self.payload()))
        self.auth.logout(self.session)
        self.assert_error(401, lambda: self.auth.change_password(self.session, self.payload()))

    def test_failed_attempt_limit_survives_login_and_restart_then_expires(self):
        for _ in range(5):
            self.assert_error(400, lambda: self.auth.change_password(self.session, self.payload(current_password='Wrong-password-value')))
        self.auth.close()
        self.auth = AdminAuth(self.path, clock=lambda: self.now)
        self.session = self.login()
        self.assert_error(429, lambda: self.auth.change_password(self.session, self.payload()))
        self.now += 600
        self.assertEqual({'ok': True}, self.auth.change_password(self.session, self.payload()))

    def test_permission_revocation_during_password_verification_wins(self):
        original = self.auth.db.execute("SELECT password FROM admin_accounts WHERE username='operator'").fetchone()[0]

        def change_account_during_verify(password, stored):
            result = password_matches(password, stored)
            self.auth.grant_permissions('operator', ['issues'])
            return result

        with patch('hermes_zalo.admin_auth.password_matches', side_effect=change_account_during_verify):
            self.assert_error(401, lambda: self.auth.change_password(self.session, self.payload()))
        self.assertEqual(original, self.auth.db.execute("SELECT password FROM admin_accounts WHERE username='operator'").fetchone()[0])

    def test_two_concurrent_password_changes_have_one_winner(self):
        second_session = self.login()
        barrier = threading.Barrier(2)
        choices = (self.NEW, 'Synthetic-other-new-password-2026')

        def encode_together(password, salt=None):
            encoded = password_hash(password, salt)
            if password in choices and salt is None:
                barrier.wait(5)
            return encoded

        def change(pair):
            session, password = pair
            try:
                self.auth.change_password(session, self.payload(new_password=password, confirm_password=password))
                return password, 200
            except AdminError as error:
                return password, error.status

        with patch('hermes_zalo.admin_auth.password_hash', side_effect=encode_together), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, zip((self.session, second_session), choices)))
        self.assertEqual([200, 401], sorted(status for _, status in results))
        winner = next(password for password, status in results if status == 200)
        self.assertEqual('operator', self.auth.user(self.login(password=winner))['username'])
        self.assertEqual(1, self.auth.db.execute("SELECT COUNT(*) FROM admin_auth_audit WHERE action='password_changed'").fetchone()[0])


if __name__ == '__main__':
    unittest.main()
