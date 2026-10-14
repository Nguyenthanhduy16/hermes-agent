"""Real work/auth/Issue API integration; synthetic roster and no external I/O."""
from dataclasses import replace
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from hermes_zalo.admin_auth import AdminAuth, AdminError
from hermes_zalo.admin_web import AdminApplication
from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.issue_management import IssueError
from hermes_zalo.sheets import SheetsSettings
from hermes_zalo.work import WorkService
from hermes_zalo.workload import READ_ERROR, WorkloadError, WorkloadPerson, WorkloadSettings


class IssueIntegrationTests(unittest.IsolatedAsyncioTestCase):
    PASSWORD = 'Synthetic-integration-password-2026'

    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.sequence = 0
        self.settings = Settings('synthetic-issue-integration', frozenset({'registration-admin'}),
            frozenset({'allowed-group'}), state_dir=root, knowledge_dir=root / 'knowledge', group_access='members')
        self.values = dict(REGISTRATION_ENABLED='true', REGISTRATION_AUTO_APPROVE='false',
            REGISTRATION_ADMIN_USERS='registration-admin', REGISTRATION_WORKLOAD_TAB='Synthetic Workload',
            REGISTRATION_WORKLOAD_NAME_COLUMN='A', REGISTRATION_WORKLOAD_PHONE_COLUMN='B',
            REGISTRATION_WORKLOAD_HEADER_ROW='1', REGISTRATION_WORKLOAD_NAME_HEADER='Họ tên',
            REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại', ZALO_ISSUES_MODE='managed', TICKETS_CHAT_MODE='off')
        sheet_settings = SheetsSettings('synthetic-spreadsheet', root / 'never-read-key.json', issues_enabled=False)
        self.sheets = SimpleNamespace(settings=sheet_settings, close=AsyncMock(),
            _request=AsyncMock(side_effect=AssertionError('No Google API call expected')),
            append_issue=AsyncMock(side_effect=AssertionError('Managed mode never writes Sheets')))
        directory_settings = WorkloadSettings.from_mapping(self.values, sheet_settings)
        self.people = {f'090000000{index}': WorkloadPerson('Tên trùng mô phỏng', f'090000000{index}',
            directory_settings.source_key, 'synthetic-person-' + str(index), False) for index in (1, 2, 3)}
        async def lookup(phone):
            if phone not in self.people:
                raise WorkloadError('Không tìm thấy số mô phỏng.')
            return self.people[phone]
        self.directory = SimpleNamespace(source_key=directory_settings.source_key,
            lookup=AsyncMock(side_effect=lookup), resolve_name=AsyncMock(side_effect=AssertionError('No name guess permitted')))
        self.classifier = SimpleNamespace(classify=AsyncMock(side_effect=AssertionError('No AI needed for these commands')),
                                          close=AsyncMock())
        self.work = self.open_work()
        self.work.managed_issues.mark_source_reconciled('synthetic-empty-legacy-source', [])
        self.member_a = self.work.registration.store.request(self.message('/dangky', user='member-a'), self.people['0900000001'], auto_approve=True)
        self.member_b = self.work.registration.store.request(self.message('/dangky', user='member-b'), self.people['0900000002'], auto_approve=True)
        self.work.registration.store.request(self.message('/dangky', user='pending'), self.people['0900000003'], auto_approve=False)
        self.auth = AdminAuth(root / 'web-auth.sqlite3')
        self.auth.create_account('teacher', self.PASSWORD, [], role='faculty')
        self.auth.create_account('helper', self.PASSWORD, ['issues'])
        self.auth.create_account('memberadmin', self.PASSWORD, ['members'])
        self.sessions = {username: self.login(username) for username in ('teacher', 'helper', 'memberadmin')}
        self.app = AdminApplication(self.work, None, self.auth)

    async def asyncTearDown(self):
        await self.work.close()
        self.auth.close()
        self.sheets._request.assert_not_awaited()
        self.sheets.append_issue.assert_not_awaited()
        self.temp.cleanup()

    def open_work(self):
        return WorkService(self.settings, sheets_client=self.sheets, environ=self.values,
            registration_directory=self.directory, tickets_chat=None, intent_router=self.classifier)

    def login(self, username):
        anonymous, _ = self.auth.session(None, create=True)
        return self.auth.login(anonymous, username, self.PASSWORD)[0]

    def message(self, text, *, user='member-a', chat=None, kind='PRIVATE'):
        self.sequence += 1
        return IncomingMessage(chat or 'dm-' + user, user, 'integration-' + str(self.sequence), kind, text)

    async def send(self, text, **scope):
        return await self.work.handle(self.message(text, **scope))

    async def request(self, method, path, *, user='helper', data=None, query=None):
        return await self.app.route(method, path, query or {}, data or {}, self.sessions[user])

    async def assert_api_error(self, status, method, path, **kwargs):
        with self.assertRaises((AdminError, IssueError)) as caught:
            await self.request(method, path, **kwargs)
        self.assertEqual(status, caught.exception.status)

    async def create_issue(self, kind='support', user='member-a'):
        action = 'dexuat' if kind == 'feature' else 'tao'
        preview = await self.send(f'/issue {action} Tiêu đề riêng {user} | Nội dung riêng {user}', user=user)
        draft_id = re.search(r'/issue xacnhan (\S+)', preview).group(1)
        reply = await self.send('/issue xacnhan ' + draft_id, user=user)
        return re.search(r'ISS-\d+', reply).group(0)

    async def action(self, identity, action, *, user='helper', **payload):
        detail = await self.request('GET', '/api/issue', user=user, query={'id': identity})
        return await self.request('POST', '/api/issue/action', user=user,
            data=dict(id=identity, action=action, revision=detail['issue']['revision'], confirm=True, **payload))

    async def test_feature_preview_confirmation_repeated_command_and_restart_preserve_one_issue(self):
        message = self.message('/issue dexuat Nhắc trước ca | Báo trước 30 phút')
        preview = await self.work.handle(message)
        self.assertIn('chưa tạo', preview)
        self.assertIn('Góp ý / đề xuất', preview)
        self.assertEqual(0, (await self.request('GET', '/api/issues'))['total'])
        self.assertEqual(preview, await self.work.handle(message))
        draft_id = re.search(r'/issue xacnhan (\S+)', preview).group(1)
        response = await self.send('/issue xacnhan ' + draft_id)
        self.assertIn('ISS-000001', response)
        await self.work.close()
        self.work = self.open_work()
        self.app.work = self.work
        self.assertTrue(self.work.managed_issues_ready)
        self.assertIn('ISS-000001', await self.send('/issue xacnhan ' + draft_id))
        listed = await self.request('GET', '/api/issues')
        self.assertEqual(1, listed['total'])
        self.assertEqual('feature', listed['items'][0]['kind'])
        self.assertEqual(self.member_a.member_id, listed['items'][0]['member_id'])

    async def test_same_name_members_only_see_their_own_issue_and_public_responses(self):
        own = await self.create_issue(user='member-a')
        other = await self.create_issue(user='member-b')
        await self.action(own, 'comment', visibility='internal', text='INTERNAL-SECRET-NOTE')
        unassigned = await self.request('GET', '/api/issue', query={'id': own})
        await self.assert_api_error(403, 'POST', '/api/issue/action', data=dict(id=own, action='comment',
            revision=unassigned['issue']['revision'], visibility='public', text='Chưa nhận xử lý', confirm=True))
        await self.action(own, 'claim')
        await self.action(own, 'comment', visibility='public', text='Phản hồi công khai cho thành viên')
        reply = await self.send('/issue xem ' + own)
        self.assertIn('Phản hồi công khai cho thành viên', reply)
        self.assertNotIn('INTERNAL-SECRET-NOTE', reply)
        self.assertNotIn('Nội dung riêng member-b', await self.send('/issue xem ' + other))
        listed = await self.send('/issue danhsach')
        self.assertIn(own, listed)
        self.assertNotIn(other, listed)
        self.assertNotEqual(self.member_a.member_id, self.member_b.member_id)
        self.directory.resolve_name.assert_not_awaited()

    async def test_unknown_pending_wrong_dm_and_group_cannot_create_or_reach_ai(self):
        for user in ('unknown', 'pending'):
            for text in ('/issue tao Bí mật | Nội dung', 'Mình muốn báo lỗi bot', 'Tôi cần giúp với lỗi này'):
                self.assertIsInstance(await self.send(text, user=user), str)
        for scope in (dict(chat='different-dm'), dict(chat='allowed-group', kind='GROUP')):
            reply = await self.send('/issue tao Bí mật | Nội dung', **scope)
            self.assertNotIn('/issue xacnhan', reply)
        self.assertEqual(0, (await self.request('GET', '/api/issues'))['total'])
        self.assertEqual(0, self.work.managed_issues.db.execute('SELECT COUNT(*) FROM managed_issue_drafts').fetchone()[0])
        self.classifier.classify.assert_not_awaited()

    async def test_plain_language_support_and_feature_requests_only_show_guidance(self):
        for phrase in ('Mình muốn báo lỗi bot', 'Mình muốn đề xuất tính năng', 'minh muon de xuat tinh nang'):
            reply = await self.send(phrase)
            self.assertIn('/issue', reply)
            self.assertIn('xác nhận', reply)
        self.assertEqual(0, (await self.request('GET', '/api/issues'))['total'])
        self.classifier.classify.assert_not_awaited()

    async def test_admin_permission_and_faculty_private_data_boundaries(self):
        identity = await self.create_issue()
        for path, query in (('/api/issues', {}), ('/api/issue', {'id': identity}), ('/api/issue/operators', {})):
            await self.assert_api_error(403, 'GET', path, user='memberadmin', query=query)
        await self.assert_api_error(403, 'GET', '/api/admin/accounts')
        self.assertEqual(1, (await self.request('GET', '/api/issues', user='teacher'))['total'])
        for path in ('/api/history', '/api/memories'):
            await self.assert_api_error(403, 'GET', path, user='teacher')
        await self.assert_api_error(400, 'POST', '/api/issue/action', data={'id': identity, 'action': 'claim', 'revision': 1})
        self.assertIsNone((await self.request('GET', '/api/issue', query={'id': identity}))['issue']['assignee'])

    async def test_faculty_account_creation_counts_and_permission_revocation_use_live_sessions(self):
        created = await self.request('POST', '/api/admin/account/save', user='teacher', data=dict(operation='create',
            username='newhelper', display_name='Admin mới', permissions=['issues'], active=True, password=self.PASSWORD, confirm=True))
        self.assertEqual('admin', created['role'])
        self.sessions['newhelper'] = self.login('newhelper')
        identity = await self.create_issue()
        await self.action(identity, 'claim', user='newhelper')
        detail = await self.request('GET', '/api/admin/account', user='teacher', query={'username': 'newhelper'})
        self.assertEqual(1, detail['open_issues'])
        updated = {key: detail[key] for key in ('username', 'display_name', 'active', 'version')}
        await self.request('POST', '/api/admin/account/save', user='teacher',
            data=dict(updated, operation='update', permissions=[], confirm=True))
        await self.assert_api_error(401, 'GET', '/api/issues', user='newhelper')
        self.sessions['newhelper'] = self.login('newhelper')
        await self.assert_api_error(403, 'GET', '/api/issues', user='newhelper')
        operators = await self.request('GET', '/api/issue/operators', user='teacher')
        self.assertNotIn('newhelper', [item['username'] for item in operators['items']])

    async def test_only_faculty_can_assign_to_current_active_issue_operator(self):
        identity = await self.create_issue()
        await self.assert_api_error(403, 'POST', '/api/issue/action', data=dict(id=identity, action='assign',
            revision=1, assignee='helper', reason='Tự bàn giao trái quyền', confirm=True))
        await self.assert_api_error(403, 'POST', '/api/issue/action', user='teacher', data=dict(id=identity, action='assign',
            revision=1, assignee='helper', reason='Giả quyền', target_actor={'username': 'helper', 'role': 'faculty'}, confirm=True))
        account = await self.request('GET', '/api/admin/account', user='teacher', query={'username': 'helper'})
        edit = {key: account[key] for key in ('username', 'display_name', 'permissions', 'version')}
        await self.request('POST', '/api/admin/account/save', user='teacher', data=dict(edit, operation='update', active=False, confirm=True))
        await self.assert_api_error(409, 'POST', '/api/issue/action', user='teacher', data=dict(id=identity, action='assign',
            revision=1, assignee='helper', reason='Admin đã khóa', confirm=True))
        assigned = await self.action(identity, 'assign', user='teacher', assignee='teacher', reason='Giảng viên tiếp nhận')
        self.assertEqual('teacher', assigned['issue']['assignee'])

    async def test_feature_planning_and_status_reply_reach_only_the_verified_member(self):
        identity = await self.create_issue(kind='feature')
        await self.action(identity, 'claim')
        planned = await self.action(identity, 'plan', priority='high', estimate='2 ngày', due_date='2026-12-01')
        self.assertEqual('2 ngày', planned['issue']['estimate'])
        waiting = await self.action(identity, 'status', status='waiting', public_reply='Bạn bổ sung ví dụ nhé.')
        self.assertEqual('waiting', waiting['issue']['status'])
        notice = await self.work.claim_issue_notification()
        self.assertEqual(self.member_a.member_id, notice['member_id'])
        self.assertEqual('dm-member-a', notice['chat_id'])
        self.assertEqual('PRIVATE', notice['chat_type'])
        self.work.managed_issues.finish_notification(notice['id'], 'failed', 'rejected')
        await self.request('POST', '/api/issue/notification/retry', data={'id': notice['id'], 'confirm': True})
        repeated = await self.work.claim_issue_notification()
        self.work.managed_issues.finish_notification(repeated['id'], 'uncertain', 'network')
        await self.assert_api_error(409, 'POST', '/api/issue/notification/retry', data={'id': notice['id'], 'confirm': True})

    async def test_member_supplement_is_scoped_deduplicated_and_visible_to_admin(self):
        identity = await self.create_issue()
        event = self.message('/issue bosung ' + identity + ' | Đây là thông tin bổ sung')
        self.assertIn('Đã gửi', await self.work.handle(event))
        self.assertIn('Đã gửi', await self.work.handle(event))
        await self.send('/issue bosung ' + identity + ' | CROSS-MEMBER-INJECTION', user='member-b')
        detail = await self.request('GET', '/api/issue', query={'id': identity})
        comments = [row['body'] for row in detail['comments']]
        self.assertEqual(1, comments.count('Đây là thông tin bổ sung'))
        self.assertNotIn('CROSS-MEMBER-INJECTION', comments)

    async def test_temporary_roster_failure_leaves_notification_pending_and_can_resume(self):
        identity = await self.create_issue()
        await self.action(identity, 'claim')
        original = self.directory.lookup.side_effect
        self.directory.lookup.side_effect = WorkloadError(READ_ERROR)
        self.assertIsNone(await self.work.claim_issue_notification())
        self.assertEqual('pending', (await self.request('GET', '/api/issue', query={'id': identity}))['notifications'][0]['status'])
        self.directory.lookup.side_effect = original
        self.work._issue_notice_retry_at = 0
        self.assertIsNotNone(await self.work.claim_issue_notification())

    async def test_changed_roster_blocks_only_that_recipient_without_losing_later_notice(self):
        first = await self.create_issue(user='member-a')
        second = await self.create_issue(user='member-b')
        await self.action(first, 'claim')
        await self.action(second, 'claim')
        self.people['0900000001'] = replace(self.people['0900000001'], fingerprint='changed-roster-person')
        self.assertIsNone(await self.work.claim_issue_notification())
        self.assertEqual('blocked', (await self.request('GET', '/api/issue', query={'id': first}))['notifications'][0]['status'])
        self.assertEqual('pending', (await self.request('GET', '/api/issue', query={'id': second}))['notifications'][0]['status'])
        notice = await self.work.claim_issue_notification()
        self.assertEqual(self.member_b.member_id, notice['member_id'])

    async def test_unreconciled_source_blocks_member_and_admin_mutations(self):
        with self.work.managed_issues.transaction() as db:
            db.execute('DELETE FROM managed_issue_migrations')
        self.assertFalse(self.work.managed_issues_ready)
        reply = await self.send('/issue tao Tiêu đề | Nội dung')
        self.assertIn('chưa sẵn sàng', reply)
        self.assertNotIn('/issue xacnhan', reply)
        await self.assert_api_error(503, 'GET', '/api/issues', user='teacher')
        self.assertEqual(0, self.work.managed_issues.db.execute('SELECT COUNT(*) FROM managed_issue_drafts').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
