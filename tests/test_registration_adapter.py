"""Registration admission at the adapter boundary; all APIs/model are fakes."""
from pathlib import Path
from types import SimpleNamespace
import unittest
import time
from unittest.mock import AsyncMock, patch

from hermes_zalo.sheets import SheetsSettings
from hermes_zalo.work import WorkService
from hermes_zalo.nlu import OUT_OF_SCOPE
from hermes_zalo.workload import WorkloadSettings
from test_registration_flow import FakeDirectory
import test_adapter as contract


class RegistrationAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await contract.AdapterTests.asyncSetUp(self)
        self.sequence = 0
        self.values = dict(REGISTRATION_ENABLED='true', REGISTRATION_ADMIN_USERS='u1',
                           REGISTRATION_WORKLOAD_TAB='Workload Test',
                           REGISTRATION_WORKLOAD_NAME_COLUMN='A', REGISTRATION_WORKLOAD_PHONE_COLUMN='B',
                           REGISTRATION_WORKLOAD_HEADER_ROW='1', REGISTRATION_WORKLOAD_NAME_HEADER='Họ tên',
                           REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại')
        self.sheets = SimpleNamespace(settings=SheetsSettings('synthetic-workload-sheet', Path(self.temp.name) / 'unused-key.json',
            users=frozenset({'u1', 'u2'}), calendar_format='lab', calendar_tabs=('Lịch lab thử',), issues_enabled=False),
            close=AsyncMock(), _request=AsyncMock(side_effect=AssertionError('No direct API expected')),
            lab_calendar=AsyncMock(return_value='Synthetic approved calendar'))
        self.directory = FakeDirectory(WorkloadSettings.from_mapping(self.values, self.sheets.settings))
        self.adapter.work = WorkService(self.adapter.settings, environ=self.values, sheets_client=self.sheets,
                                        registration_directory=self.directory)
        self.assertTrue(self.adapter.work.registration.enabled)

    async def asyncTearDown(self):
        await contract.AdapterTests.asyncTearDown(self)

    async def send(self, text, *, user='new-user', chat=None, kind='PRIVATE', mid=None):
        self.sequence += 1
        await self.adapter._dispatch(contract.update(mid or str(self.sequence), user=user, chat=chat or 'dm-' + user,
                                                     kind=kind, text=text))

    async def enroll(self, user='new-user'):
        await self.send('/dangky ' + self.directory.person.phone, user=user)
        return self.adapter.work.registration.store.user_state(user)

    async def approve(self, user='new-user'):
        record = await self.enroll(user)
        await self.send('/duyetdangky ' + record.id, user='u1')
        self.assertEqual('approved', self.adapter.work.registration.store.user_state(user).status)
        return self.adapter.work.registration.store.user_state(user)

    def replies(self):
        return [call.args[1] for call in self.adapter.client.send_text_chunk.await_args_list]

    async def test_only_registration_navigation_bootstraps_unknown_private_user(self):
        for text in ('xin chào', '/menu', '/lich homnay', '/nhac sau 1 phut | thử', '/duyetdangky DK-12345678'):
            await self.send(text)
        self.handler.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_not_awaited()
        await self.send('/dangky')
        await self.send('/trangthaidangky')
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)
        self.directory.lookup.assert_not_awaited()
        self.assertIsNone(self.adapter.work.registration.store.user_state('new-user'))

    async def test_auto_registration_replies_then_opens_member_access_without_model_receiving_phone(self):
        self.adapter.work.registration.auto_approve = True
        await self.send('/dangky ' + self.directory.person.phone, mid='auto-first')
        self.assertIn('Đăng ký thành công', self.replies()[-1])
        self.assertNotIn(self.directory.person.phone, self.replies()[-1])
        self.handler.assert_not_awaited()
        await self.send('/dangky ' + self.directory.person.phone, mid='auto-first')
        self.assertEqual(1, len(self.replies()))
        await self.send('/lich homnay')
        self.assertEqual('Synthetic approved calendar', self.replies()[-1])
        await self.send('Giải thích Python là gì?')
        self.handler.assert_not_awaited()
        self.assertEqual(OUT_OF_SCOPE, self.replies()[-1])
        await self.send('/dangkychoduyet')
        self.assertIn('chưa được cấp quyền quản trị', self.replies()[-1])
        self.assertNotIn('new-user', self.adapter.settings.allowed_users)
        self.assertNotIn('new-user', self.sheets.settings.users)

    async def test_pending_registration_and_invalid_phone_never_reach_model_or_echo_identity(self):
        await self.send('/dangky invalid-private-input')
        self.directory.lookup.assert_not_awaited()
        self.assertNotIn('invalid-private-input', self.replies()[-1])
        await self.send('/dangky ' + self.directory.person.phone, mid='repeat-request')
        await self.send('/dangky ' + self.directory.person.phone, mid='repeat-request')
        self.assertEqual(1, self.directory.lookup.await_count)
        await self.send('/trangthaidangky')
        before = self.adapter.client.send_text_chunk.await_count
        await self.send('Giải thích Python là gì?')
        self.assertEqual(before, self.adapter.client.send_text_chunk.await_count)
        self.handler.assert_not_awaited()
        for text in self.replies():
            self.assertNotIn(self.directory.person.phone, text)
            self.assertNotIn(self.directory.person.name, text)
        self.assertEqual('pending', self.adapter.work.registration.store.user_state('new-user').status)

    async def test_approved_exact_private_route_can_use_member_features_without_expanding_static_or_group_acl(self):
        allowed = self.adapter.settings.allowed_users
        sheets_users = self.sheets.settings.users
        await self.approve()
        self.handler.assert_not_awaited()
        await self.send('Giải thích Python là gì?')
        self.handler.assert_not_awaited()
        self.assertEqual(OUT_OF_SCOPE, self.replies()[-1])
        self.assertEqual('dm-new-user', self.adapter.client.send_text_chunk.await_args.args[0])
        before = self.adapter.client.send_text_chunk.await_count
        for chat, kind in (('wrong-dm', 'PRIVATE'), ('g1', 'GROUP'), ('other-group', 'GROUP')):
            await self.send('Giải thích Python là gì?', chat=chat, kind=kind)
        self.assertEqual(before, self.adapter.client.send_text_chunk.await_count)
        self.handler.assert_not_awaited()
        await self.send('/lich homnay')
        self.assertEqual('Synthetic approved calendar', self.replies()[-1])
        self.sheets.lab_calendar.assert_awaited_once()
        for text in ('/nhac sau 1 phut | thử', '/issue tao Tiêu đề | Mô tả'):
            await self.send(text)
        self.handler.assert_not_awaited()
        self.assertEqual(allowed, self.adapter.settings.allowed_users)
        self.assertEqual(sheets_users, self.sheets.settings.users)
        self.assertFalse(self.sheets.settings.issues_enabled)
        self.sheets._request.assert_not_awaited()
        self.assertEqual(1, len(self.adapter.work.reminders.list_for('new-user', 'dm-new-user', 'PRIVATE')))
        self.assertEqual(0, self.adapter.work.issues.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0])

    async def test_approved_member_outside_static_lists_receives_only_verified_dm_notice(self):
        await self.approve()
        self.assertNotIn('new-user', self.adapter.settings.allowed_users)
        self.assertNotIn('new-user', self.sheets.settings.users)
        self.adapter.client.send_text_chunk.reset_mock()
        notice = dict(id=101, user_id='new-user', chat_id='dm-new-user', text='Synthetic private proposal')
        with patch.object(self.adapter.work, 'claim_ticket_notification', new=AsyncMock(side_effect=[notice, None])), \
                patch.object(self.adapter.work, 'finish_ticket_notification', new=AsyncMock()) as finish:
            await self.adapter._deliver_ticket_notifications()
            finish.assert_awaited_once_with(notice['id'], 'sent')
        self.adapter.client.send_text_chunk.assert_awaited_once_with('dm-new-user', notice['text'])
        self.handler.assert_not_awaited()
        self.assertIsNone(self.adapter._send_permit.get())
        for chat in ('dm-new-user', 'other-dm', 'g1'):
            self.assertFalse((await self.adapter.send(chat, 'arbitrary send')).success)
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)

    async def test_approved_member_outside_static_lists_receives_reminder_at_verified_private_destination(self):
        await self.approve()
        self.assertNotIn('new-user', self.adapter.settings.allowed_users)
        self.assertNotIn('new-user', self.sheets.settings.users)
        reminder = self.adapter.work.reminders.create('new-user', 'dm-new-user', 'PRIVATE', text='Synthetic member reminder',
            due_at=time.time() + 60, request_key='member-private-reminder')
        self.adapter.client.send_text_chunk.reset_mock()
        await self.adapter._deliver_due_reminders(now=reminder.due_at + 1)
        self.adapter.client.send_text_chunk.assert_awaited_once()
        route, text = self.adapter.client.send_text_chunk.await_args.args
        self.assertEqual('dm-new-user', route)
        self.assertIn('Synthetic member reminder', text)
        self.assertEqual('sent', self.adapter.work.reminders.list_for('new-user', 'dm-new-user', 'PRIVATE')[0].status)
        self.handler.assert_not_awaited()
        self.assertIsNone(self.adapter._send_permit.get())
        self.assertFalse((await self.adapter.send('dm-new-user', 'arbitrary background send')).success)

    async def test_pending_member_cannot_receive_previously_queued_reminder(self):
        await self.enroll()
        reminder = self.adapter.work.reminders.create('new-user', 'dm-new-user', 'PRIVATE', text='Synthetic pending reminder',
            due_at=time.time() + 60, request_key='pending-private-reminder')
        self.adapter.client.send_text_chunk.reset_mock()
        await self.adapter._deliver_due_reminders(now=reminder.due_at + 1)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual('blocked', self.adapter.work.reminders.list_for('new-user', 'dm-new-user', 'PRIVATE')[0].status)

    async def test_member_reminder_wrong_dm_group_and_changed_source_are_blocked(self):
        await self.approve()
        self.adapter.client.send_text_chunk.reset_mock()
        for index, (chat, kind) in enumerate((('wrong-dm', 'PRIVATE'), ('g1', 'GROUP'),
                                             ('other-group', 'GROUP'), ('dm-new-user', 'PRIVATE'))):
            with self.subTest(chat=chat):
                if chat == 'dm-new-user':
                    self.directory.source_key = 'workload:changed-source'
                reminder = self.adapter.work.reminders.create('new-user', chat, kind, text='Synthetic private reminder',
                    due_at=time.time() + 60, request_key='invalid-member-route-' + str(index))
                await self.adapter._deliver_due_reminders(now=reminder.due_at + 1)
                self.assertEqual('blocked', self.adapter.work.reminders.list_for('new-user', chat, kind)[0].status)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.handler.assert_not_awaited()

    async def test_revoked_member_without_static_access_cannot_receive_previously_queued_reminder(self):
        record = await self.approve()
        reminder = self.adapter.work.reminders.create('new-user', 'dm-new-user', 'PRIVATE', text='Synthetic revoked reminder',
            due_at=time.time() + 60, request_key='revoked-member-reminder')
        await self.send('/thuhoidangky ' + record.member_id, user='u1')
        self.adapter.client.send_text_chunk.reset_mock()
        await self.adapter._deliver_due_reminders(now=reminder.due_at + 1)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual('blocked', self.adapter.work.reminders.list_for('new-user', 'dm-new-user', 'PRIVATE')[0].status)
        self.handler.assert_not_awaited()

    async def test_pending_member_cannot_receive_previously_queued_notice(self):
        await self.enroll()
        self.adapter.client.send_text_chunk.reset_mock()
        notice = dict(id=102, user_id='new-user', chat_id='dm-new-user', text='Synthetic private proposal')
        with patch.object(self.adapter.work, 'claim_ticket_notification', new=AsyncMock(side_effect=[notice, None])), \
                patch.object(self.adapter.work, 'finish_ticket_notification', new=AsyncMock()) as finish:
            await self.adapter._deliver_ticket_notifications()
            finish.assert_awaited_once_with(notice['id'], 'blocked')
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.handler.assert_not_awaited()

    async def test_approved_member_wrong_dm_group_and_changed_source_fail_after_queue_claim(self):
        await self.approve()
        self.adapter.client.send_text_chunk.reset_mock()
        for index, chat in enumerate(('wrong-dm', 'g1', 'other-group', 'dm-new-user')):
            with self.subTest(chat=chat):
                if chat == 'dm-new-user':
                    self.directory.source_key = 'workload:changed-source'
                notice = dict(id=110 + index, user_id='new-user', chat_id=chat, text='Synthetic private proposal')
                with patch.object(self.adapter.work, 'claim_ticket_notification', new=AsyncMock(side_effect=[notice, None])), \
                        patch.object(self.adapter.work, 'finish_ticket_notification', new=AsyncMock()) as finish:
                    await self.adapter._deliver_ticket_notifications()
                    finish.assert_awaited_once_with(notice['id'], 'blocked')
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.handler.assert_not_awaited()

    async def test_group_phone_cannot_bootstrap_or_lookup_even_for_an_existing_static_user(self):
        await self.send('/dangky ' + self.directory.person.phone, chat='g1', kind='GROUP')
        self.adapter.client.send_text_chunk.assert_not_awaited()
        await self.send('/dangky ' + self.directory.person.phone, user='u2', chat='g1', kind='GROUP')
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)
        self.assertNotIn(self.directory.person.phone, self.replies()[-1])
        self.directory.lookup.assert_not_awaited()
        self.handler.assert_not_awaited()

    async def test_revocation_blocks_original_static_allowlist_and_queued_reminder(self):
        record = await self.approve('u2')
        await self.send('/nhac sau 1 phut | Nhắc thử riêng', user='u2')
        reminders = self.adapter.work.reminders.list_for('u2', 'dm-u2', 'PRIVATE')
        self.assertEqual(1, len(reminders))
        await self.send('/thuhoidangky ' + record.member_id, user='u1')
        before = self.adapter.client.send_text_chunk.await_count
        await self.send('Giải thích Python là gì?', user='u2')
        await self.send('/menu', user='u2')
        await self.adapter._deliver_due_reminders(now=reminders[0].due_at + 1)
        self.assertEqual(before, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual('blocked', self.adapter.work.reminders.list_for('u2', 'dm-u2', 'PRIVATE')[0].status)
        await self.send('/trangthaidangky', user='u2')
        self.assertEqual(before + 1, self.adapter.client.send_text_chunk.await_count)
        self.assertIn('thu hồi', self.replies()[-1])
        self.assertNotIn(self.directory.person.phone, self.replies()[-1])
        self.assertNotIn(self.directory.person.name, self.replies()[-1])
        self.handler.assert_not_awaited()

    async def test_wrong_admin_cannot_approve_or_leak_pending_identity(self):
        record = await self.enroll()
        self.directory.lookup.reset_mock()
        await self.send('/duyetdangky ' + record.id, user='u2')
        await self.send('/dangkychoduyet', user='u2')
        self.assertEqual('pending', self.adapter.work.registration.store.user_state('new-user').status)
        self.directory.lookup.assert_not_awaited()
        self.handler.assert_not_awaited()
        for reply in self.replies():
            self.assertNotIn(self.directory.person.name, reply)
            self.assertNotIn(self.directory.person.phone, reply)

    async def test_ticket_notice_rechecks_wrong_route_and_revocation_after_claim(self):
        record = await self.approve('u2')
        for index, chat in enumerate(('wrong-dm', 'dm-u2')):
            if index:
                await self.send('/thuhoidangky ' + record.member_id, user='u1')
            self.adapter.client.send_text_chunk.reset_mock()
            notice = dict(id=100 + index, user_id='u2', chat_id=chat, text='Synthetic private proposal')
            # Simulate a previously verified queue item becoming invalid before
            # delivery, independent of the queue's own registration checks.
            with patch.object(self.adapter.work, 'claim_ticket_notification', new=AsyncMock(side_effect=[notice, None])), \
                    patch.object(self.adapter.work, 'finish_ticket_notification', new=AsyncMock()) as finish:
                await self.adapter._deliver_ticket_notifications()
                finish.assert_awaited_once_with(notice['id'], 'blocked')
            self.adapter.client.send_text_chunk.assert_not_awaited()
        self.handler.assert_not_awaited()

    async def test_invalid_enabled_registration_stops_model_and_persisted_background_delivery(self):
        await self.adapter.work.close()
        self.adapter.work = WorkService(self.adapter.settings, environ=self.values | {'REGISTRATION_ADMIN_USERS': 'not-allowed'},
                                        sheets_client=self.sheets, registration_directory=self.directory)
        self.assertTrue(self.adapter.work.registration.requested)
        self.assertFalse(self.adapter.work.registration.enabled)
        reminder = self.adapter.work.reminders.create('u2', 'dm-u2', 'PRIVATE', text='Legacy private reminder',
                                                     due_at=time.time() + 60, request_key='synthetic-legacy-reminder')
        await self.send('Giải thích Python là gì?', user='u2')
        self.handler.assert_not_awaited()
        self.assertIn('chưa sẵn sàng', self.replies()[-1])
        self.adapter.client.send_text_chunk.reset_mock()
        await self.adapter._deliver_due_reminders(now=reminder.due_at + 1)
        await self.adapter._deliver_ticket_notifications()
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual('blocked', self.adapter.work.reminders.list_for('u2', 'dm-u2', 'PRIVATE')[0].status)
        self.directory.lookup.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
