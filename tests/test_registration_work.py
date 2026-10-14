"""Verified personal calendar routing using only local roster fixtures."""
from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.lab_calendar import LabEvent, LabSchedule
from hermes_zalo.registration_flow import UNAVAILABLE
from hermes_zalo.sheets import SheetsClient, SheetsSettings
from hermes_zalo.work import WorkService
from hermes_zalo.workload import WorkloadSettings
from test_registration_flow import FakeDirectory


class RegistrationWorkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.sequence = 0
        self.settings = Settings('synthetic-work-registration', frozenset({'admin', 'member'}),
            frozenset({'allowed-group'}), state_dir=Path(self.temp.name), knowledge_dir=Path(self.temp.name) / 'empty-docs')
        self.values = dict(REGISTRATION_ENABLED='true', REGISTRATION_ADMIN_USERS='admin',
            REGISTRATION_WORKLOAD_TAB='Synthetic Workload', REGISTRATION_WORKLOAD_NAME_COLUMN='A',
            REGISTRATION_WORKLOAD_PHONE_COLUMN='B', REGISTRATION_WORKLOAD_HEADER_ROW='1',
            REGISTRATION_WORKLOAD_NAME_HEADER='Họ tên', REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại')
        settings = SheetsSettings('synthetic-sheet', Path(self.temp.name) / 'never-read.json',
            users=frozenset({'member'}), calendar_format='lab', calendar_tabs=('Lịch lab thử',), issues_enabled=False)
        def reject_network(request):
            self.fail('Unexpected external request')
        self.sheets = SheetsClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(reject_network)),
                                   token_provider=AsyncMock(side_effect=AssertionError('No Google credentials expected')))
        events = [LabEvent(date(2026, 10, 6), f'MY-COURSE-{i}', f'MY-SESSION-{i}', 'Coach Key', 'R101', 'Sáng',
                           10 + i, 10 + i, ['Coach Alpha [Rảnh]']) for i in range(1, 7)]
        events += [LabEvent(date(2026, 10, 6), 'PREFIX-OTHER-COURSE', 'OTHER-SESSION', '', 'R101', 'Sáng', 25, 25,
                            ['Coach Alpha Beta']),
                   LabEvent(date(2026, 10, 6), 'STALE-PROFILE-COURSE', 'STALE-SESSION', '', 'R101', 'Sáng', 26, 26,
                            ['Coach Gamma'])]
        self.schedule = LabSchedule('Lịch lab thử', 123, {date(2026, 10, 6)}, events)
        self.sheets._lab_schedules = AsyncMock(return_value=[self.schedule])
        self.directory = FakeDirectory(WorkloadSettings.from_mapping(self.values, settings))
        self.directory.person = replace(self.directory.person, name='Coach Alpha')
        self.service = WorkService(self.settings, sheets_client=self.sheets, environ=self.values,
                                    registration_directory=self.directory)

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    def message(self, text, *, user='member', chat=None, kind='PRIVATE'):
        self.sequence += 1
        return IncomingMessage(chat or 'dm-' + user, user, str(self.sequence), kind, text)

    async def send(self, text, **kwargs):
        return await self.service.handle(self.message(text, **kwargs))

    async def approve(self, user='member'):
        await self.send('/dangky ' + self.directory.person.phone, user=user)
        record = self.service.registration.store.user_state(user)
        await self.send('/duyetdangky ' + record.id, user='admin')
        self.assertEqual('approved', self.service.registration.store.user_state(user).status)
        self.directory.lookup.reset_mock()
        return self.service.registration.store.user_state(user)

    async def test_tickets_bootstrap_retries_source_failure_without_reopening_or_blocking_calendar(self):
        from hermes_zalo.registration import RegistrationError
        chat = SimpleNamespace(close=Mock())
        self.service.registration.bootstrap_tickets = AsyncMock(side_effect=[RegistrationError('source unavailable'), {'linked': 3, 'skipped': 0}])
        self.service._tickets_values['TICKETS_CHAT_MODE'] = 'lab_live'
        with patch('hermes_zalo.tickets.runtime.open_chat', return_value=chat) as opening:
            self.assertIsNone(await self.service._open_tickets())
            self.assertTrue(self.service._tickets_error)
            self.assertIsNone(await self.service._open_tickets())
            self.service.registration.bootstrap_tickets.assert_awaited_once_with(chat)
            self.service._tickets_bootstrap_retry_at = 0
            self.assertIs(chat, await self.service._open_tickets())
            self.assertIs(chat, await self.service._open_tickets())
            self.assertFalse(self.service._tickets_error)
            self.assertEqual(2, self.service.registration.bootstrap_tickets.await_count)
            opening.assert_called_once()

    async def test_fresh_verified_person_overrides_legacy_self_declared_profile(self):
        await self.approve()
        self.service.profiles.set(self.message('profile'), 'Coach Gamma')
        reply = await self.send('/lichcuatoi 06/10/2026')
        self.assertIn('MY-COURSE-1', reply)
        self.assertNotIn('STALE-PROFILE-COURSE', reply)
        self.assertNotIn('PREFIX-OTHER-COURSE', reply)
        self.assertIn('6 mục', reply)
        self.assertTrue(self.directory.lookup.await_count >= 1)
        self.assertTrue(all(call.args == (self.directory.person.phone,)
                            for call in self.directory.lookup.await_args_list))
        self.sheets._lab_schedules.assert_awaited_once()
        # A stored old name never grants identity, even after a profile command.
        await self.send('/hoso xoa')
        again = await self.send('/lichcuatoi 06/10/2026')
        self.assertIn('MY-COURSE-1', again)
        self.assertTrue(self.directory.lookup.await_count >= 2)
        self.assertEqual('approved', self.service.registration.store.user_state('member').status)

    async def test_each_personal_read_revalidates_source_name_fingerprint_and_uniqueness(self):
        await self.approve()
        original = self.directory.person
        for changes in ({'name_unique': False}, {'source_key': 'workload:changed-source'},
                        {'fingerprint': 'changed-fingerprint'}, {'name': 'Coach Gamma'}):
            with self.subTest(changes=changes):
                self.directory.person = replace(original, **changes)
                reply = await self.send('/lichcuatoi 06/10/2026')
                self.assertIsInstance(reply, str)
                self.assertNotIn('MY-COURSE', reply)
                self.assertNotIn('STALE-PROFILE-COURSE', reply)
                self.assertNotIn(original.phone, reply)
                self.sheets._lab_schedules.assert_not_awaited()
        self.assertEqual(4, self.directory.lookup.await_count)

    async def test_existing_approval_grants_calendar_without_static_sheets_ids(self):
        await self.approve()
        self.sheets.settings = replace(self.sheets.settings, users=frozenset())
        for text in ('/lichcuatoi 06/10/2026', '/lich 06/10/2026'):
            reply = await self.send(text)
            self.assertIn('MY-COURSE', reply)
        self.assertEqual(2, self.sheets._lab_schedules.await_count)
        self.assertTrue(self.directory.lookup.await_count >= 2)
        self.assertEqual(frozenset(), self.sheets.settings.users)

    async def test_auto_registered_new_member_immediately_reads_calendar_without_static_ids(self):
        self.service.registration.auto_approve = True
        reply = await self.send('/dangky ' + self.directory.person.phone, user='new-user')
        self.assertIn('Đăng ký thành công', reply)
        self.assertNotIn('new-user', self.settings.allowed_users)
        self.assertNotIn('new-user', self.sheets.settings.users)
        self.assertIn('MY-COURSE', await self.send('/lichcuatoi 06/10/2026', user='new-user'))
        self.assertIn('Đã đặt nhắc', await self.send('/nhac sau 1 phut | Việc thử', user='new-user'))

    async def test_approval_opens_ordinary_features_without_static_ids(self):
        await self.approve('new-user')
        for text in ('/lichcuatoi 06/10/2026', '/lich 06/10/2026'):
            self.assertIn('MY-COURSE', await self.send(text, user='new-user'))
        created = await self.send('/nhac sau 1 phut | Nội dung của tôi', user='new-user')
        self.assertIn('Đã đặt nhắc', created)
        reminders = self.service.reminders.list_for('new-user', 'dm-new-user', 'PRIVATE')
        self.assertEqual(1, len(reminders))
        self.assertIn('Nội dung của tôi', await self.send('/nhac xem', user='new-user'))
        self.assertNotIn('Nội dung của tôi', await self.send('/nhac xem', user='member'))
        self.assertIn('Đã hủy', await self.send('/nhac huy ' + reminders[0].id, user='new-user'))
        self.assertIn('Đã ghi nhận', await self.send('/phanhoi Góp ý thử', user='new-user'))
        # Approval never silently enables a globally disabled integration.
        self.assertIn('đang tắt', await self.send('/issue tao T | B', user='new-user'))
        self.assertEqual(0, self.service.issues.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0])
        self.assertNotIn('new-user', self.settings.allowed_users)
        self.assertNotIn('new-user', self.sheets.settings.users)

    async def test_approved_member_issue_requires_own_confirmation_and_active_grant(self):
        record = await self.approve('new-user')
        self.sheets.settings = replace(self.sheets.settings, issues_enabled=True)
        self.sheets.append_issue = AsyncMock()
        preview = await self.send('/issue tao Lỗi lịch | Cần bổ sung phòng', user='new-user')
        self.assertIn('BẢN NHÁP', preview)
        self.sheets.append_issue.assert_not_awaited()
        draft = self.service.issues.db.execute('SELECT id FROM drafts').fetchone()[0]
        self.assertNotIn('Đã tạo', await self.send('/issue xacnhan ' + draft, user='member'))
        self.sheets.append_issue.assert_not_awaited()
        self.assertIn('Đã tạo', await self.send('/issue xacnhan ' + draft, user='new-user'))
        self.assertEqual(1, self.sheets.append_issue.await_count)
        row = self.sheets.append_issue.await_args.args[0]
        self.assertEqual(['new-user', 'dm-new-user', 'PRIVATE'], row[5:])
        self.assertTrue(self.sheets.append_issue.await_args.kwargs['access_guard']())
        await self.send('/issue xacnhan ' + draft, user='new-user')
        self.assertEqual(1, self.sheets.append_issue.await_count)
        await self.send('/issue tao Nháp mới | Chưa được ghi', user='new-user')
        second = self.service.issues.db.execute("SELECT id FROM drafts WHERE status='draft'").fetchone()[0]
        await self.send('/thuhoidangky ' + record.member_id, user='admin')
        self.assertNotIn('Đã tạo', await self.send('/issue xacnhan ' + second, user='new-user'))
        self.assertEqual(1, self.sheets.append_issue.await_count)
        self.assertFalse(self.sheets.append_issue.await_args.kwargs['access_guard']())

    async def test_issue_grant_changed_during_roster_read_never_creates_draft(self):
        record = await self.approve('new-user')
        self.sheets.settings = replace(self.sheets.settings, issues_enabled=True)
        async def revoke_during_lookup(phone):
            self.service.registration.store.revoke('admin', record.member_id)
            return self.directory.person
        self.directory.lookup.side_effect = revoke_during_lookup
        self.assertNotIn('BẢN NHÁP', await self.send('/issue tao T | B', user='new-user'))
        self.assertEqual(0, self.service.issues.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0])

    async def test_approved_member_menu_exposes_calendar_and_tickets_without_privileged_actions(self):
        await self.approve('new-user')
        self.service._tickets_values['TICKETS_CHAT_MODE'] = 'lab_live'
        reply = await self.send('/menu', user='new-user')
        for expected in ('/lich homnay', '/lichcuatoi homnay', '/doica', '/thongbao', '/issue'):
            self.assertIn(expected, reply)
        for forbidden in ('/dangkychoduyet', '/thanhvien', '/nhac'):
            self.assertNotIn(forbidden, reply)
        self.directory.lookup.assert_not_awaited()  # Menu does not fetch the roster.
        self.sheets._lab_schedules.assert_not_awaited()
        self.assertNotIn('new-user', self.settings.allowed_users)
        self.assertNotIn('new-user', self.sheets.settings.users)

    async def test_approval_survives_restart_without_reapproval_or_allowlist_changes(self):
        record = await self.approve('new-user')
        sheet_settings = self.sheets.settings
        await self.service.close()
        def reject_network(request):
            self.fail('Restart test must remain local')
        self.sheets = SheetsClient(sheet_settings,
            client=httpx.AsyncClient(transport=httpx.MockTransport(reject_network)),
            token_provider=AsyncMock(side_effect=AssertionError('No credentials expected')))
        self.sheets._lab_schedules = AsyncMock(return_value=[self.schedule])
        self.service = WorkService(self.settings, sheets_client=self.sheets, environ=self.values,
            registration_directory=self.directory)
        current = self.service.registration.store.user_state('new-user')
        self.assertEqual((record.id, record.member_id, 'approved'),
                         (current.id, current.member_id, current.status))
        reply = await self.send('/lichcuatoi 06/10/2026', user='new-user')
        self.assertIn('MY-COURSE-1', reply)
        self.assertNotIn('new-user', self.settings.allowed_users)
        self.assertNotIn('new-user', self.sheets.settings.users)

    async def test_dynamic_member_grant_never_opens_another_private_chat_or_unshared_group(self):
        await self.approve('new-user')
        for kind, chat in (('PRIVATE', 'wrong-dm'), ('GROUP', 'unshared-group'),
                           ('GROUP', 'allowed-group')):
            with self.subTest(kind=kind, chat=chat):
                reply = await self.send('/lich 06/10/2026', user='new-user', chat=chat, kind=kind)
                self.assertIsInstance(reply, str)
                self.assertNotIn('MY-COURSE', reply)
        self.sheets._lab_schedules.assert_not_awaited()

    async def test_pending_and_revoked_members_do_not_receive_dynamic_calendar_access(self):
        await self.send('/dangky ' + self.directory.person.phone, user='new-user')
        pending = self.service.registration.store.user_state('new-user')
        self.assertEqual('pending', pending.status)
        reply = await self.send('/lich 06/10/2026', user='new-user')
        self.assertNotIn('MY-COURSE', reply)
        self.sheets._lab_schedules.assert_not_awaited()
        await self.send('/duyetdangky ' + pending.id, user='admin')
        reply = await self.send('/lich 06/10/2026', user='new-user')
        self.assertIn('MY-COURSE', reply)
        await self.send('/thuhoidangky ' + pending.member_id, user='admin')
        self.sheets._lab_schedules.reset_mock()
        for text in ('/lich 06/10/2026', '/lichcuatoi 06/10/2026', '/menu'):
            reply = await self.send(text, user='new-user')
            self.assertNotIn('MY-COURSE', reply)
            self.assertNotIn('Tra lịch:', reply)
        self.sheets._lab_schedules.assert_not_awaited()

    async def test_legacy_profile_without_approval_does_not_bypass_registration(self):
        self.service.profiles.set(self.message('profile'), 'Coach Alpha')
        reply = await self.send('/lichcuatoi 06/10/2026')
        self.assertIn('/dangky', reply)
        self.assertNotIn('MY-COURSE', reply)
        self.directory.lookup.assert_not_awaited()
        self.sheets._lab_schedules.assert_not_awaited()

    async def test_personal_pagination_keeps_identity_scope_filters_and_fresh_validation(self):
        await self.approve()
        first = await self.send('/lichcuatoi 06/10/2026 | phong=R101')
        self.assertIn('Trang 1/2', first)
        command = next(line for line in first.splitlines() if line.startswith('/lichcuatoi '))
        self.assertIn('trang=2', command)
        self.assertIn('phong=R101', command)
        self.assertNotIn('coach=', command)
        second = await self.send(command)
        self.assertIn('Trang 2/2', second)
        self.assertIn('MY-COURSE-6', second)
        self.assertNotIn('MY-COURSE-1', second)
        self.assertNotIn('PREFIX-OTHER-COURSE', second)
        self.assertTrue(self.directory.lookup.await_count >= 2)
        too_far = await self.send('/lichcuatoi 06/10/2026 | phong=R101 | trang=3')
        back = next(line for line in too_far.splitlines() if line.startswith('/lichcuatoi '))
        self.assertIn('trang=1', back)
        self.assertNotIn('coach=', back)
        self.assertIn('Trang 1/2', await self.send(back))

    async def test_personal_name_cannot_be_replaced_by_filter_or_wrong_private_route(self):
        await self.approve()
        reply = await self.send('/lichcuatoi 06/10/2026 | coach=Coach Gamma')
        self.assertIn('tên đã đăng ký', reply)
        self.sheets._lab_schedules.assert_not_awaited()
        self.directory.lookup.reset_mock()
        reply = await self.send('/lichcuatoi 06/10/2026', chat='different-private-chat')
        self.assertNotIn('MY-COURSE', reply)
        self.directory.lookup.assert_not_awaited()
        self.sheets._lab_schedules.assert_not_awaited()

    async def test_revoked_static_user_cannot_restore_writes_by_requesting_registration_again(self):
        record = await self.approve()
        await self.send('/thuhoidangky ' + record.member_id, user='admin')
        await self.send('/dangky ' + self.directory.person.phone)
        self.assertEqual('pending', self.service.registration.store.user_state('member').status)
        await self.send('/nhac sau 1 phut | Không được tạo')
        self.assertEqual([], self.service.reminders.list_for('member', 'dm-member', 'PRIVATE'))
        self.assertTrue(self.service.background_registration_blocked('member', 'dm-member'))

    async def test_requested_but_invalid_registration_never_falls_back_to_legacy_identity_or_tickets(self):
        for variant in ('admin', 'source'):
            with self.subTest(variant=variant):
                settings = replace(self.settings, state_dir=Path(self.temp.name) / variant)
                values = self.values | ({'REGISTRATION_ADMIN_USERS': 'unapproved-admin'} if variant == 'admin' else {})
                directory = self.directory if variant == 'admin' else SimpleNamespace(
                    source_key='workload:wrong-source', lookup=AsyncMock(), resolve_name=AsyncMock())
                old_tickets = SimpleNamespace(handle=Mock(side_effect=AssertionError('Legacy Tickets must not run')),
                    claim_notification=Mock(side_effect=AssertionError('Old notification must not be claimed')), close=Mock())
                service = WorkService(settings, sheets_client=self.sheets, environ=values,
                                      registration_directory=directory, tickets_chat=old_tickets)
                try:
                    self.assertTrue(service.registration.requested)
                    self.assertFalse(service.registration.enabled)
                    service.profiles.set(self.message('legacy-profile'), 'Coach Alpha')
                    for text in ('/lichcuatoi 06/10/2026', '/tickets', '/dongy 1234',
                                 '/nhac sau 1 phut | Không được tạo', 'Giải thích Python là gì?',
                                 '/dangky ' + self.directory.person.phone):
                        reply = await service.handle(self.message(text))
                        self.assertEqual(UNAVAILABLE, reply)
                    self.assertIsNone(await service.claim_ticket_notification())
                    self.assertTrue(service.background_registration_blocked('member', 'dm-member'))
                    self.assertTrue(service.background_registration_blocked('member', 'allowed-group', 'GROUP'))
                    old_tickets.handle.assert_not_called()
                    old_tickets.claim_notification.assert_not_called()
                    self.directory.lookup.assert_not_awaited()
                    self.sheets._lab_schedules.assert_not_awaited()
                    self.assertEqual([], service.reminders.list_for('member', 'dm-member', 'PRIVATE'))
                finally:
                    await service.close()


if __name__ == '__main__':
    unittest.main()
