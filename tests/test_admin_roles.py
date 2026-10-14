"""Role migration and concurrency boundaries, using synthetic local databases."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from hermes_zalo.admin_auth import AdminAuth, AdminError, password_hash, password_matches


class AdminRoleTests(unittest.TestCase):
    PASSWORD = 'Synthetic-admin-role-password-2026'

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'roles.sqlite3'
        self.auth = AdminAuth(self.path)
        self.auth.create_account('teacher', self.PASSWORD, [], role='faculty', display_name='Giảng viên mẫu')
        self.teacher = self.login('teacher')

    def tearDown(self):
        self.auth.close()
        self.temp.cleanup()

    def login(self, username, auth=None):
        auth = auth or self.auth
        anonymous, _ = auth.session(None, create=True)
        return auth.login(anonymous, username, self.PASSWORD)[0]

    def create_admin(self, username='helper', permissions=('issues',)):
        return self.auth.manage_account(self.teacher, dict(operation='create', username=username,
            display_name='Người hỗ trợ', permissions=list(permissions), active=True, password=self.PASSWORD, confirm=True))

    def update(self, account, **changes):
        data = {key: account[key] for key in ('username', 'display_name', 'permissions', 'active', 'version', 'role')}
        return dict(data, operation='update', confirm=True) | changes

    def assert_error(self, status, action):
        with self.assertRaises(AdminError) as caught:
            action()
        self.assertEqual(status, caught.exception.status)

    def test_existing_database_migrates_without_promoting_or_expanding_permissions(self):
        path = Path(self.temp.name) / 'legacy.sqlite3'
        hashed = password_hash(self.PASSWORD)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE admin_accounts(username TEXT PRIMARY KEY,password TEXT NOT NULL,permissions TEXT NOT NULL,active INTEGER NOT NULL,updated REAL NOT NULL)')
            db.execute('INSERT INTO admin_accounts VALUES (?,?,?,?,?)', ('legacy', hashed, '["members","memories"]', 1, 10))
        db.close()
        auth = AdminAuth(path)
        try:
            session = self.login('legacy', auth)
            user = auth.user(session)
            self.assertEqual('admin', user['role'])
            self.assertEqual(['members', 'memories'], user['permissions'])
            self.assertEqual('legacy', user['display_name'])
            self.assert_error(403, lambda: auth.list_accounts(session))
            self.assert_error(403, lambda: auth.user(session, 'issues'))
            self.assertEqual(hashed, auth.db.execute('SELECT password FROM admin_accounts').fetchone()[0])
            auth.designate_faculty('legacy')
            self.assert_error(401, lambda: auth.user(session))
            user = auth.user(self.login('legacy', auth))
            self.assertEqual('faculty', user['role'])
            self.assertEqual(['members', 'memories'], user['permissions'])
        finally:
            auth.close()

    def test_faculty_can_manage_accounts_and_issues_without_private_conversation_access(self):
        self.assertEqual('faculty', self.auth.user(self.teacher, 'issues')['role'])
        for permission in ('members', 'conversations', 'memories'):
            self.assert_error(403, lambda: self.auth.user(self.teacher, permission))
        account = self.create_admin()
        helper = self.login('helper')
        self.assertEqual('admin', self.auth.user(helper, 'issues')['role'])
        for action in (lambda: self.auth.list_accounts(helper), lambda: self.auth.account_detail(helper, 'teacher'),
                       lambda: self.auth.manage_account(helper, self.update(account, role='faculty'))):
            self.assert_error(403, action)

    def test_forged_role_session_or_username_never_authorizes(self):
        self.create_admin()
        helper = self.login('helper')
        self.assert_error(403, lambda: self.auth.list_accounts(dict(helper, role='faculty')))
        self.assert_error(401, lambda: self.auth.list_accounts(dict(helper, username='teacher')))
        self.assert_error(401, lambda: self.auth.list_accounts(dict(self.teacher, hash='invented')))

    def test_web_cannot_create_or_promote_faculty(self):
        account = self.create_admin()
        self.assert_error(403, lambda: self.auth.manage_account(self.teacher, self.update(account, role='faculty')))
        self.assert_error(403, lambda: self.auth.manage_account(self.teacher, dict(operation='create', username='injected',
            permissions=['issues'], password=self.PASSWORD, role='faculty', confirm=True)))
        self.assertIsNone(self.auth.db.execute("SELECT 1 FROM admin_accounts WHERE username='injected'").fetchone())

    def test_account_change_revokes_captured_sessions_and_permission_removal_is_immediate(self):
        account = self.create_admin(permissions=('issues', 'conversations'))
        captured = self.login('helper')
        changed = self.auth.manage_account(self.teacher, self.update(account, permissions=['issues']))
        self.assert_error(401, lambda: self.auth.user(captured, 'conversations'))
        current = self.login('helper')
        self.assert_error(403, lambda: self.auth.user(current, 'conversations'))
        self.assertEqual('helper', self.auth.user(current, 'issues')['username'])
        locked = self.auth.manage_account(self.teacher, self.update(changed, active=False))
        self.assert_error(401, lambda: self.auth.user(current))
        self.assert_error(401, lambda: self.login('helper'))
        self.auth.manage_account(self.teacher, self.update(locked, active=True))
        self.assertEqual('helper', self.auth.user(self.login('helper'))['username'])

    def test_last_active_faculty_cannot_be_locked_or_locally_demoted(self):
        teacher = self.auth.account_detail(self.teacher, 'teacher')
        self.assert_error(409, lambda: self.auth.manage_account(self.teacher, self.update(teacher, active=False)))
        self.assert_error(409, lambda: self.auth.create_account('teacher', self.PASSWORD, ['issues'], replace=True))
        self.assertEqual('faculty', self.auth.user(self.teacher)['role'])
        self.assertEqual(1, self.auth.account_detail(self.teacher, 'teacher')['version'])

    def test_two_faculty_lock_race_preserves_one_active_operator(self):
        self.auth.create_account('second', self.PASSWORD, [], role='faculty')
        other = AdminAuth(self.path)
        try:
            second = self.login('second', other)
            teacher_account = other.account_detail(second, 'teacher')
            second_account = self.auth.account_detail(self.teacher, 'second')
            barrier = threading.Barrier(2)
            def lock(auth, session, account):
                barrier.wait()
                try:
                    auth.manage_account(session, self.update(account, active=False))
                    return 200
                except AdminError as error:
                    return error.status
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(lock, self.auth, self.teacher, second_account),
                           pool.submit(lock, other, second, teacher_account)]
                self.assertEqual([200, 401], sorted(future.result() for future in futures))
            self.assertEqual(1, self.auth.db.execute("SELECT COUNT(*) FROM admin_accounts WHERE role='faculty' AND active=1").fetchone()[0])
        finally:
            other.close()

    def test_stale_edits_across_connections_have_one_winner(self):
        account = self.create_admin()
        other = AdminAuth(self.path)
        try:
            barrier = threading.Barrier(2)
            def edit(auth, name):
                barrier.wait()
                try:
                    auth.manage_account(self.teacher, self.update(account, display_name=name))
                    return 200
                except AdminError as error:
                    return error.status
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(edit, self.auth, 'Tên thứ nhất'), pool.submit(edit, other, 'Tên thứ hai')]
                self.assertEqual([200, 409], sorted(future.result() for future in futures))
        finally:
            other.close()

    def test_faculty_permission_is_rechecked_after_password_hashing(self):
        self.auth.create_account('second', self.PASSWORD, [], role='faculty')
        second = self.login('second')
        teacher_account = self.auth.account_detail(second, 'teacher')
        def lock_during_hash(password, salt=None):
            result = password_hash(password, salt)
            self.auth.manage_account(second, self.update(teacher_account, active=False))
            return result
        with patch('hermes_zalo.admin_auth.password_hash', side_effect=lock_during_hash):
            self.assert_error(401, lambda: self.create_admin())
        self.assertIsNone(self.auth.db.execute("SELECT 1 FROM admin_accounts WHERE username='helper'").fetchone())

    def test_login_rechecks_revision_after_password_verification(self):
        account = self.create_admin()
        anonymous, _ = self.auth.session(None, create=True)
        def revoke_during_hash(password, stored):
            valid = password_matches(password, stored)
            self.auth.manage_account(self.teacher, self.update(account, permissions=[]))
            return valid
        with patch('hermes_zalo.admin_auth.password_matches', side_effect=revoke_during_hash):
            self.assert_error(401, lambda: self.auth.login(anonymous, 'helper', self.PASSWORD))
        self.assertEqual(0, self.auth.db.execute("SELECT COUNT(*) FROM admin_sessions WHERE username='helper'").fetchone()[0])

    def test_audit_search_and_operators_never_expose_credentials(self):
        account = self.create_admin(permissions=('issues', 'members'))
        self.auth.manage_account(self.teacher, self.update(account, display_name='NGÔ THỊ MẪU'))
        self.assertEqual(['helper'], [item['username'] for item in self.auth.list_accounts(self.teacher, 'ngô thị')['items']])
        self.assertEqual(0, self.auth.list_accounts(self.teacher, '%')['total'])
        helper = self.login('helper')
        self.assertEqual(['helper', 'teacher'], [item['username'] for item in self.auth.issue_operators(helper)['items']])
        self.auth.create_account('history', self.PASSWORD, ['conversations'])
        self.assert_error(403, lambda: self.auth.issue_operators(self.login('history')))
        detail = self.auth.account_detail(self.teacher, 'helper')
        self.assertEqual('web:teacher', detail['audit'][0]['actor'])
        self.assertEqual('account_updated', detail['audit'][0]['action'])
        self.assertEqual(['issues', 'members'], detail['audit'][0]['details']['before']['permissions'])
        for payload in (detail, self.auth.issue_operators(helper), self.auth.list_accounts(self.teacher)):
            serialized = json.dumps(payload)
            for secret in (self.PASSWORD, self.teacher['hash'], self.teacher['csrf'], 'password', 'csrf'):
                self.assertNotIn(secret, serialized)

    def test_duplicate_registration_invalid_inputs_and_confirmation_do_not_mutate(self):
        account = self.create_admin()
        self.assert_error(409, lambda: self.create_admin())
        for changes in (dict(confirm=False), dict(permissions=['faculty']), dict(permissions=['issues', 'issues']),
                        dict(active='true'), dict(version=True), dict(password='short'), dict(unexpected=True)):
            self.assert_error(400, lambda: self.auth.manage_account(self.teacher, self.update(account, **changes)))
        self.assertEqual(1, self.auth.account_detail(self.teacher, 'helper')['version'])

    def test_local_recovery_preserves_last_faculty_and_restart_persists(self):
        self.create_admin()
        self.auth.create_account('teacher', self.PASSWORD, ['issues'], replace=True, role='faculty')
        self.assert_error(401, lambda: self.auth.user(self.teacher))
        self.auth.close()
        self.auth = AdminAuth(self.path)
        teacher = self.login('teacher')
        self.assertEqual('faculty', self.auth.user(teacher)['role'])
        self.assertEqual(2, self.auth.account_detail(teacher, 'teacher')['version'])
        self.assertEqual('account_configured:teacher', self.auth.account_detail(teacher, 'teacher')['audit'][0]['action'])
        self.assertEqual(2, self.auth.list_accounts(teacher)['total'])

    def test_explicit_ticket_grant_preserves_identity_password_and_existing_permissions(self):
        account = self.create_admin(permissions=('members',))
        old_session = self.login('helper')
        password_before = self.auth.db.execute("SELECT password FROM admin_accounts WHERE username='helper'").fetchone()[0]
        result = self.auth.grant_permissions('helper', ['tickets'])
        self.assertEqual(['members', 'tickets'], result['permissions'])
        self.assertEqual(account['role'], result['role'])
        self.assertEqual(account['display_name'], result['display_name'])
        self.assertEqual(account['version'] + 1, result['version'])
        self.assert_error(401, lambda: self.auth.user(old_session))
        self.assertEqual('helper', self.auth.user(self.login('helper'), 'tickets')['username'])
        self.assertEqual(password_before, self.auth.db.execute("SELECT password FROM admin_accounts WHERE username='helper'").fetchone()[0])
        self.assertEqual(result, self.auth.grant_permissions('helper', ['tickets']))
        self.assertEqual([], self.auth.user(self.teacher)['permissions'])
        self.assert_error(403, lambda: self.auth.user(self.teacher, 'tickets'))
        self.assertEqual('permissions_granted', self.auth.account_detail(self.teacher, 'helper')['audit'][0]['action'])

    def test_faculty_assigns_ticket_permission_explicitly_without_conversation_access(self):
        self.create_admin(permissions=('tickets',))
        helper = self.login('helper')
        self.assertEqual(['tickets'], self.auth.user(helper)['permissions'])
        for permission in ('conversations', 'memories', 'issues', 'members'):
            self.assert_error(403, lambda: self.auth.user(helper, permission))


if __name__ == '__main__':
    unittest.main()
