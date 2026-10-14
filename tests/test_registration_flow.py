"""Private enrollment orchestration with fake Workload reads, never live APIs."""
import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.registration import RegistrationError
from hermes_zalo.registration_flow import RegistrationFlow
from hermes_zalo.sheets import SheetsSettings
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.chat import TicketsChat
from hermes_zalo.tickets.service import TicketStore, digest
from hermes_zalo.workload import (
    LOOKUP_ERROR, NAME_ERROR, WorkloadError, WorkloadPerson, WorkloadSettings, normalize_phone,
)


class FakeDirectory:
    def __init__(self, settings):
        self.settings = settings
        self.source_key = settings.source_key
        self.person = WorkloadPerson('Người Thử Alpha', '0900000001', self.source_key,
                                     'synthetic-person-fingerprint', True)
        self.lookup = AsyncMock(side_effect=self._lookup)
        self.resolve_name = AsyncMock(side_effect=self._resolve_name)

    async def _lookup(self, phone):
        if normalize_phone(phone) != self.person.phone:
            raise WorkloadError(LOOKUP_ERROR)
        return self.person

    async def _resolve_name(self, name):
        if name != self.person.name or not self.person.name_unique:
            raise WorkloadError(NAME_ERROR)
        return self.person


class RegistrationFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'synthetic-registration.sqlite3'
        self.sequence = 0
        self.settings = Settings(token='synthetic-bot-secret',
                                 allowed_users=frozenset({'admin-user', 'static-user'}),
                                 allowed_groups=frozenset({'allowed-group'}))
        self.values = dict(REGISTRATION_ENABLED='true', REGISTRATION_ADMIN_USERS='admin-user',
                           REGISTRATION_WORKLOAD_TAB='Workload Test',
                           REGISTRATION_WORKLOAD_NAME_COLUMN='A',
                           REGISTRATION_WORKLOAD_PHONE_COLUMN='B',
                           REGISTRATION_WORKLOAD_HEADER_ROW='1',
                           REGISTRATION_WORKLOAD_NAME_HEADER='Họ tên',
                           REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại')
        self.sheets = SimpleNamespace(settings=SheetsSettings(
            'synthetic-workload-sheet', Path(self.temp.name) / 'unused-key.json',
            users=frozenset({'static-user'}), calendar_format='lab', calendar_tabs=('Lịch lab thử',),
            issues_enabled=False), _request=AsyncMock(side_effect=AssertionError('No direct API expected')))
        cfg = WorkloadSettings.from_mapping(self.values, self.sheets.settings)
        self.directory = FakeDirectory(cfg)
        self.flows = []
        self.flow = self.open()

    def tearDown(self):
        for flow in self.flows:
            if flow.store is not None:
                flow.close()
        self.temp.cleanup()

    def open(self, *, values=None, path=None):
        flow = RegistrationFlow(self.settings, self.values if values is None else values,
                                self.sheets, path or self.path, directory=self.directory)
        self.flows.append(flow)
        return flow

    def message(self, text, *, user='new-user', chat=None, kind='PRIVATE', mid=None):
        self.sequence += 1
        return IncomingMessage(chat or 'dm-' + user, user, mid or str(self.sequence), kind, text)

    async def request(self, *, user='new-user'):
        message = self.message('/dangky ' + self.directory.person.phone, user=user)
        response = await self.flow.handle(message)
        record = self.flow.store.status(message)
        self.assertIsNotNone(record)
        self.assertEqual('pending', record.status)
        return message, record, response

    async def test_copied_phone_registers_once_but_still_requires_admin_approval(self):
        first = self.message('/dangky \u200b0900\u00a0000\u202f001\ufeff')
        response = await self.flow.handle(first)
        record = self.flow.store.status(first)
        self.assertIsNotNone(record)
        self.assertEqual('pending', record.status)
        self.assertEqual('0900000001', record.phone)
        self.assertIsNone(self.flow.approved(first))
        self.assertFalse(self.flow.member_allows(first.user_id, first.chat_id, 'PRIVATE'))
        self.assertNotIn(record.phone, response)
        self.assertNotIn(record.name, response)
        await self.flow.handle(self.message('/dangky +84\u200b900000001'))
        self.assertEqual(record.id, self.flow.store.status(first).id)
        self.assertEqual(1, len(self.flow.store.pending('admin-user')))
        await self.flow.handle(self.message('/duyetdangky ' + record.id, user='new-user'))
        self.assertEqual('pending', self.flow.store.status(first).status)

    async def approve(self, *, user='new-user'):
        message, record, _ = await self.request(user=user)
        response = await self.flow.handle(self.message('/duyetdangky ' + record.id, user='admin-user'))
        current = self.flow.approved(message)
        self.assertIsNotNone(current, response)
        self.assertEqual('approved', current.status)
        return message, current

    def test_bootstrap_opens_only_explicit_private_enrollment_navigation(self):
        self.assertTrue(self.flow.enabled)
        for command in ('/dangky', '/dangky 0900000001', '/dangky trangthai', '/trangthaidangky'):
            with self.subTest(command=command):
                self.assertTrue(self.flow.bootstrap(self.message(command)))
                self.assertFalse(self.flow.bootstrap(self.message(command, kind='GROUP', chat='allowed-group')))
        for command in ('/menu', '/lich homnay', '/dongy 1234', '/dangkychoduyet',
                        '/duyetdangky DK-12345678', 'Tôi là Người Thử Alpha', 'xin chào'):
            with self.subTest(command=command):
                self.assertFalse(self.flow.bootstrap(self.message(command)))
        self.directory.lookup.assert_not_awaited()

    async def test_unrelated_messages_remain_outside_registration_flow(self):
        for text in ('xin chào', '/menu', '/lichcuatoi mai'):
            self.assertIsNone(await self.flow.handle(self.message(text)))
        self.directory.lookup.assert_not_awaited()
        self.assertIsNone(self.flow.identity('new-user'))

    async def test_disabled_or_invalid_setup_never_opens_unknown_dm_bootstrap(self):
        cases = ({'REGISTRATION_ENABLED': 'false'}, {'REGISTRATION_ENABLED': 'maybe'},
                 {'REGISTRATION_ADMIN_USERS': 'not-an-allowed-user'},
                 {'REGISTRATION_ADMIN_USERS': ''}, {'REGISTRATION_WORKLOAD_NAME_HEADER': ''},
                 {'REGISTRATION_AUTO_APPROVE': 'yes'})
        for index, changes in enumerate(cases):
            with self.subTest(changes=changes):
                flow = self.open(values=self.values | changes,
                                 path=Path(self.temp.name) / f'disabled-{index}.sqlite3')
                self.assertFalse(flow.enabled)
                self.assertEqual(changes.get('REGISTRATION_ENABLED') != 'false', flow.requested)
                message = self.message('/dangky ' + self.directory.person.phone)
                self.assertFalse(flow.bootstrap(message))
                self.assertFalse(flow.bootstrap(self.message('/dangky trangthai')))
                self.assertIsNone(flow.identity(message.user_id))
                self.assertFalse(flow.member_allows(message.user_id, message.chat_id, 'PRIVATE'))
                response = await flow.handle(message)
                self.assertIsInstance(response, str)
                self.assertNotIn(self.directory.person.phone, response)
        self.directory.lookup.assert_not_awaited()

    async def test_group_phone_request_has_no_lookup_identity_or_phone_echo(self):
        message = self.message('/dangky ' + self.directory.person.phone, kind='GROUP', chat='allowed-group')
        response = await self.flow.handle(message)
        self.assertIsInstance(response, str)
        self.assertNotIn(self.directory.person.phone, response)
        self.assertNotIn(self.directory.person.name, response)
        self.directory.lookup.assert_not_awaited()
        self.assertEqual([], self.flow.store.pending('admin-user'))

    async def test_invalid_requested_config_cannot_validate_or_sync_existing_approval(self):
        message, record = await self.approve(user='static-user')
        unavailable = self.open(values=self.values | {'REGISTRATION_WORKLOAD_NAME_HEADER': ''})
        self.assertTrue(unavailable.requested)
        self.assertFalse(unavailable.enabled)
        self.assertIsNone(unavailable.approved(message))
        self.directory.lookup.reset_mock()
        chat = SimpleNamespace(service=SimpleNamespace(mode='lab_live'),
                               store=Mock(), identities=Mock(), bot_scope='synthetic-scope')
        with self.assertRaises(RegistrationError):
            await unavailable.validate(record)
        with self.assertRaises(RegistrationError):
            await unavailable.sync_tickets(record, chat)
        response = await unavailable.handle(self.message('/dangky trangthai', user='static-user'))
        self.assertNotIn(record.name, response)
        self.assertNotIn(record.phone, response)
        self.directory.lookup.assert_not_awaited()
        self.assertEqual([], chat.store.mock_calls)
        self.assertEqual([], chat.identities.mock_calls)

    async def test_pending_request_repeats_and_status_survive_restart_without_name_disclosure(self):
        message, record, response = await self.request()
        for text in (response, await self.flow.handle(message),
                     await self.flow.handle(self.message('/dangky ' + self.directory.person.phone)),
                     await self.flow.handle(self.message('/dangky trangthai'))):
            self.assertNotIn(self.directory.person.phone, text)
            self.assertNotIn(self.directory.person.name, text)
        self.assertEqual(1, len(self.flow.store.pending('admin-user')))
        self.assertIsNone(self.flow.approved(message))
        self.assertIsNone(self.flow.identity(message.user_id))
        self.flow.store.close()
        self.flows.remove(self.flow)
        self.flow = self.open()
        self.assertEqual(record.id, self.flow.store.status(message).id)
        self.directory.lookup.reset_mock()
        status = await self.flow.handle(self.message('/trangthaidangky'))
        self.assertIsInstance(status, str)
        self.assertNotIn(self.directory.person.name, status)
        self.assertNotIn(self.directory.person.phone, status)
        self.directory.lookup.assert_not_awaited()

    async def test_bad_phone_or_lookup_failure_never_creates_request_or_echoes_input(self):
        for phone in ('not-a-phone', '900000001', '0900000002'):
            response = await self.flow.handle(self.message('/dangky ' + phone))
            self.assertIsInstance(response, str)
            self.assertNotIn(phone, response)
        self.assertEqual([], self.flow.store.pending('admin-user'))

    async def test_auto_registration_returns_short_success_and_only_own_dm_grant(self):
        flow = self.open(values=self.values | {'REGISTRATION_AUTO_APPROVE': 'true'})
        message = self.message('/dangky \u200b' + self.directory.person.phone)
        reply = await flow.handle(message)
        self.assertTrue(reply.startswith('✅ Đăng ký thành công!'))
        self.assertIn(self.directory.person.name, reply)
        self.assertIn('/menu', reply)
        self.assertNotIn(self.directory.person.phone, reply)
        self.assertLess(len(reply), 200)
        self.assertNotIn('chờ', reply.lower())
        self.assertTrue(flow.member_allows(message.user_id, message.chat_id, 'PRIVATE'))
        self.assertFalse(flow.member_allows(message.user_id, 'other-chat', 'PRIVATE'))
        self.assertFalse(flow.member_allows(message.user_id, message.chat_id, 'GROUP'))
        self.assertFalse(flow.admin_command(self.message('/dangkychoduyet')))
        self.assertNotIn(message.user_id, self.settings.allowed_users)
        self.assertIn('tự động', await flow.handle(self.message('/dangky')))
        self.assertEqual(reply, await flow.handle(self.message('/dangky trangthai')))

    async def test_auto_mode_requires_fresh_successful_lookup_and_keeps_rate_limit(self):
        flow = self.open(values=self.values | {'REGISTRATION_AUTO_APPROVE': 'true'})
        self.directory.lookup.side_effect = WorkloadError('Không tìm thấy hoặc trùng số')
        for _ in range(5):
            reply = await flow.handle(self.message('/dangky ' + self.directory.person.phone))
            self.assertNotIn('thành công', reply)
            self.assertIsNone(flow.identity('new-user'))
        self.assertIn('giới hạn', await flow.handle(self.message('/dangky ' + self.directory.person.phone)))
        self.assertEqual(5, self.directory.lookup.await_count)

    async def test_inflight_lookup_cannot_undo_admin_reset_without_a_fresh_message(self):
        flow = self.open(values=self.values | {'REGISTRATION_AUTO_APPROVE': 'true'})
        first = self.message('/dangky ' + self.directory.person.phone)
        await flow.handle(first)
        original = flow.approved(first)
        entered, resume = asyncio.Event(), asyncio.Event()
        async def delayed_lookup(phone):
            entered.set()
            await resume.wait()
            return self.directory.person
        self.directory.lookup.side_effect = delayed_lookup
        old_turn = asyncio.create_task(flow.handle(self.message('/dangky ' + self.directory.person.phone)))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            flow.store.reset_registration('web:admin', original, flow.store.reset_snapshot(original),
                                          'Thành viên cần đăng ký lại')
        finally:
            resume.set()
        response = await old_turn
        self.assertIn('tin nhắn mới', response)
        self.assertNotIn(self.directory.person.phone, response)
        self.assertEqual('reset', flow.store.user_state(first.user_id).status)
        self.assertIsNone(flow.identity(first.user_id))
        self.assertEqual(1, flow.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])
        self.directory.lookup.side_effect = self.directory._lookup
        response = await flow.handle(self.message('/dangky ' + self.directory.person.phone))
        self.assertIn('Đăng ký thành công', response)
        self.assertNotEqual(original.id, flow.identity(first.user_id).id)

    async def test_member_revision_change_during_lookup_requires_a_fresh_message(self):
        flow = self.open(values=self.values | {'REGISTRATION_AUTO_APPROVE': 'true'})
        first = self.message('/dangky ' + self.directory.person.phone)
        await flow.handle(first)
        original = flow.approved(first)
        entered, resume = asyncio.Event(), asyncio.Event()
        async def delayed_lookup(phone):
            entered.set()
            await resume.wait()
            return self.directory.person
        self.directory.lookup.side_effect = delayed_lookup
        old_turn = asyncio.create_task(flow.handle(self.message('/dangky ' + self.directory.person.phone)))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            # An Admin edit may leave the registration record itself unchanged.
            with flow.store._transaction() as db:
                flow.store._bump_revision(db, original.member_id)
        finally:
            resume.set()
        self.assertIn('tin nhắn mới', await old_turn)
        self.assertEqual(original, flow.identity(first.user_id))
        self.assertEqual(1, flow.store.db.execute('SELECT COUNT(*) FROM registration_receipts').fetchone()[0])

    async def test_pending_status_does_not_claim_auto_success_until_resubmitted(self):
        message, record, _ = await self.request()
        flow = self.open(values=self.values | {'REGISTRATION_AUTO_APPROVE': 'true'})
        reply = await flow.handle(self.message('/dangky trangthai'))
        self.assertIn('gửi lại /dangky', reply)
        self.assertIsNone(flow.approved(message))
        self.assertIn('thành công', await flow.handle(self.message('/dangky ' + record.phone)))
        self.assertEqual(record.member_id, flow.approved(message).member_id)

    async def test_auto_mode_group_registration_never_looks_up_or_discloses_identity(self):
        flow = self.open(values=self.values | {'REGISTRATION_AUTO_APPROVE': 'true'})
        reply = await flow.handle(self.message('/dangky ' + self.directory.person.phone, kind='GROUP'))
        self.assertIn('nhắn riêng', reply)
        self.assertNotIn(self.directory.person.name, reply)
        self.directory.lookup.assert_not_awaited()

    async def test_auto_approval_preserves_duplicate_name_block_for_personal_calendar(self):
        flow = self.open(values=self.values | {'REGISTRATION_AUTO_APPROVE': 'true'})
        self.directory.person = replace(self.directory.person, name_unique=False)
        message = self.message('/dangky ' + self.directory.person.phone)
        await flow.handle(message)
        record = flow.approved(message)
        self.assertIsNotNone(record)
        with self.assertRaises(RegistrationError):
            await flow.validate(record)
        self.assertEqual(self.directory.person, await flow.validate(record, require_unique_name=False))

    async def test_wrong_admin_and_group_admin_cannot_read_or_decide_or_call_source(self):
        _, record, _ = await self.request()
        self.directory.lookup.reset_mock()
        commands = ('/dangkychoduyet', '/thanhvien', '/duyetdangky ' + record.id,
                    '/tuchoidangky ' + record.id, '/thuhoidangky ' + record.member_id)
        for command in commands:
            for user, kind, chat in (('static-user', 'PRIVATE', 'dm-static-user'),
                                     ('admin-user', 'GROUP', 'allowed-group')):
                response = await self.flow.handle(self.message(command, user=user, kind=kind, chat=chat))
                self.assertIsInstance(response, str)
                self.assertNotIn(self.directory.person.name, response)
                self.assertNotIn(self.directory.person.phone, response)
        self.directory.lookup.assert_not_awaited()
        self.directory.resolve_name.assert_not_awaited()
        self.assertEqual('pending', self.flow.store.get_for_admin('admin-user', record.id).status)

    async def test_admin_pending_discloses_verified_name_but_only_masked_phone(self):
        _, record, _ = await self.request()
        response = await self.flow.handle(self.message('/dangkychoduyet', user='admin-user'))
        self.assertIn(self.directory.person.name, response)
        self.assertIn(record.id, response)
        self.assertIn(record.user_id, response)
        self.assertNotIn(self.directory.person.phone, response)
        self.assertIn(self.directory.person.phone[-4:], response)

    async def test_approval_is_exact_dm_identity_and_does_not_mutate_static_resource_acl(self):
        message, record = await self.approve()
        self.assertEqual(record, self.flow.approved(message))
        self.assertEqual(record, self.flow.identity(message.user_id))
        self.assertIsNone(self.flow.approved(self.message('/lichcuatoi mai', chat='wrong-private-chat')))
        self.assertTrue(self.flow.blocked(self.message('/lichcuatoi mai', chat='wrong-private-chat')))
        self.assertIsNone(self.flow.approved(self.message('/lichcuatoi mai', user='other-user')))
        self.assertIsNone(self.flow.approved(self.message('/lichcuatoi mai', kind='GROUP', chat='allowed-group')))
        self.assertNotIn(message.user_id, self.settings.allowed_users)
        self.assertFalse(self.sheets.settings.allows(message.user_id, message.chat_id, 'PRIVATE'))
        self.assertFalse(self.sheets.settings.issues_enabled)
        self.assertTrue(self.flow.member_allows(message.user_id, message.chat_id, 'PRIVATE'))
        self.assertFalse(self.flow.member_allows(message.user_id, 'wrong-private-chat', 'PRIVATE'))
        self.assertFalse(self.flow.member_allows(message.user_id, message.chat_id, 'GROUP'))
        self.assertFalse(self.flow.member_allows('other-user', message.chat_id, 'PRIVATE'))
        self.flow.close()
        self.flows.remove(self.flow)
        self.flow = self.open()
        self.assertTrue(self.flow.member_allows(message.user_id, message.chat_id, 'PRIVATE'))

    async def test_common_calendar_validation_relaxes_only_ambiguous_name(self):
        self.directory.person = replace(self.directory.person, name_unique=False)
        _, record = await self.approve()
        self.assertEqual(self.directory.person, await self.flow.validate(record, require_unique_name=False))
        with self.assertRaises(RegistrationError):
            await self.flow.validate(record)
        self.directory.person = replace(self.directory.person, fingerprint='changed')
        with self.assertRaises(RegistrationError):
            await self.flow.validate(record, require_unique_name=False)

    async def test_member_grant_fails_closed_for_pending_revoked_changed_source_and_disabled_feature(self):
        message, record, _ = await self.request()
        self.assertFalse(self.flow.member_allows(message.user_id, message.chat_id, 'PRIVATE'))
        self.flow.store.approve('admin-user', record.id, self.directory.person)
        self.assertTrue(self.flow.member_allows(message.user_id, message.chat_id, 'PRIVATE'))
        source = self.directory.source_key
        self.directory.source_key = 'changed-source'
        self.assertFalse(self.flow.member_allows(message.user_id, message.chat_id, 'PRIVATE'))
        self.directory.source_key = source
        self.flow.store.revoke('admin-user', record.member_id)
        self.assertFalse(self.flow.member_allows(message.user_id, message.chat_id, 'PRIVATE'))
        off = self.open(values=self.values | {'REGISTRATION_ENABLED': 'false'})
        self.assertFalse(off.member_allows(message.user_id, message.chat_id, 'PRIVATE'))

    async def test_validate_cannot_return_a_grant_revoked_while_source_read_is_in_flight(self):
        _, record = await self.approve()
        async def read_then_revoke(phone):
            self.flow.store.revoke('admin-user', record.member_id)
            return self.directory.person
        self.directory.lookup.side_effect = read_then_revoke
        with self.assertRaises(RegistrationError):
            await self.flow.validate(record, require_unique_name=False)

    async def test_admin_lists_only_current_approved_members_with_masked_phones_and_revoke_commands(self):
        message, record = await self.approve()
        response = await self.flow.handle(self.message('/thanhvien', user='admin-user'))
        self.assertIn(record.name, response)
        self.assertIn(record.member_id, response)
        self.assertIn('/thuhoidangky ' + record.member_id, response)
        self.assertIn('***' + record.phone[-4:], response)
        self.assertNotIn(record.phone, response)
        self.assertNotIn(record.user_id, response)
        self.assertNotIn(record.chat_id, response)
        self.assertIn('Chưa có', await self.flow.handle(self.message('/thanhvien 2', user='admin-user')))
        self.assertIn('/thanhvien 2', await self.flow.handle(self.message('/thanhvien -1', user='admin-user')))
        source = self.directory.source_key
        self.directory.source_key = 'changed-source'
        self.assertNotIn(record.member_id, await self.flow.handle(self.message('/thanhvien', user='admin-user')))
        self.directory.source_key = source
        self.flow.store.revoke('admin-user', record.member_id)
        self.assertNotIn(record.member_id, await self.flow.handle(self.message('/thanhvien', user='admin-user')))

    async def test_admin_member_list_paginates_without_disclosing_pending_people(self):
        records = []
        for index in range(7):
            person = replace(self.directory.person, phone=f'090000{index:04d}', name=f'Thành viên số {index}')
            record = self.flow.store.request(self.message('/dangky', user=f'listed-user-{index}'), person)
            if index < 6:
                record = self.flow.store.approve('admin-user', record.id, person)
            records.append(record)
        first = await self.flow.handle(self.message('/thanhvien', user='admin-user'))
        second = await self.flow.handle(self.message('/thanhvien 2', user='admin-user'))
        self.assertIn('/thanhvien 2', first)
        self.assertNotIn('/thanhvien 3', second)
        for index, record in enumerate(records):
            self.assertEqual(index < 5, record.member_id in first)
            self.assertEqual(index == 5, record.member_id in second)
            self.assertNotIn(record.phone, first + second)

    async def test_approval_rechecks_source_changes_and_does_not_grant_stale_name(self):
        _, record, _ = await self.request()
        self.directory.person = replace(self.directory.person, name='Người Thử Đã Đổi',
                                        fingerprint='changed-roster-fingerprint')
        response = await self.flow.handle(self.message('/duyetdangky ' + record.id, user='admin-user'))
        self.assertNotIn(self.directory.person.phone, response)
        self.assertEqual('pending', self.flow.store.get_for_admin('admin-user', record.id).status)
        self.assertIsNone(self.flow.identity(record.user_id))

    async def test_validate_rejects_changed_source_fingerprint_name_and_ambiguous_name(self):
        _, record = await self.approve()
        original = self.directory.person
        self.assertEqual(original, await self.flow.validate(record))
        for changes in ({'source_key': 'workload:another-source'}, {'fingerprint': 'changed'},
                        {'name': 'Người Thử Khác'}, {'name_unique': False}):
            with self.subTest(changes=changes):
                self.directory.person = replace(original, **changes)
                with self.assertRaises((RegistrationError, WorkloadError)) as error:
                    await self.flow.validate(record)
                self.assertNotIn(original.phone, str(error.exception))
                self.assertNotIn(original.name, str(error.exception))
        self.directory.person = original

    async def test_same_phone_cannot_be_claimed_by_another_zalo_identity(self):
        _, record = await self.approve()
        response = await self.flow.handle(self.message('/dangky ' + record.phone, user='other-user'))
        self.assertNotIn(record.phone, response)
        self.assertNotIn(record.name, response)
        self.assertIsNone(self.flow.identity('other-user'))
        self.assertIsNone(self.flow.store.user_state('other-user'))
        self.assertEqual(record, self.flow.identity(record.user_id))

    async def test_revocation_blocks_even_static_user_and_identity_cannot_be_reused(self):
        message, record = await self.approve(user='static-user')
        response = await self.flow.handle(self.message('/thuhoidangky ' + record.member_id, user='admin-user'))
        self.assertNotIn(record.phone, response)
        self.assertTrue(self.flow.blocked(message))
        self.assertIsNone(self.flow.approved(message))
        self.assertIsNone(self.flow.identity(message.user_id))
        self.assertIn(message.user_id, self.settings.allowed_users)

    async def test_pending_static_user_stays_blocked_until_admin_approval(self):
        message, record, _ = await self.request(user='static-user')
        self.assertTrue(self.flow.blocked(message))
        self.assertTrue(self.flow.bootstrap(message))
        self.assertIsNone(self.flow.approved(message))
        await self.flow.handle(self.message('/duyetdangky ' + record.id, user='admin-user'))
        self.assertFalse(self.flow.blocked(message))
        self.assertEqual(record.member_id, self.flow.approved(message).member_id)

    async def test_revoked_admin_escape_is_only_explicit_admin_commands_in_private_chat(self):
        message, record = await self.approve(user='admin-user')
        await self.flow.handle(self.message('/thuhoidangky ' + record.member_id, user='admin-user'))
        self.assertTrue(self.flow.blocked(message))
        for command in ('/dangkychoduyet', '/thanhvien', '/duyetdangky DK-12345678',
                        '/tuchoidangky DK-12345678', '/thuhoidangky ' + record.member_id):
            with self.subTest(command=command):
                self.assertTrue(self.flow.admin_command(self.message(command, user='admin-user')))
                self.assertFalse(self.flow.admin_command(self.message(command, user='static-user')))
                self.assertFalse(self.flow.admin_command(self.message(command, user='admin-user',
                                                                      kind='GROUP', chat='allowed-group')))
        for command in ('/menu', '/lichcuatoi mai', '/dongy 3123', 'xin chào'):
            self.assertFalse(self.flow.admin_command(self.message(command, user='admin-user')))

    async def test_revocation_cannot_be_bypassed_by_new_pending_request_or_restart(self):
        _, old = await self.approve(user='static-user')
        await self.flow.handle(self.message('/thuhoidangky ' + old.member_id, user='admin-user'))
        message, pending, _ = await self.request(user='static-user')
        self.assertEqual(old.member_id, pending.member_id)
        self.assertNotEqual(old.id, pending.id)
        self.assertTrue(self.flow.blocked(message))
        self.assertTrue(self.flow.bootstrap(message))
        self.flow.close()
        self.flows.remove(self.flow)
        self.flow = self.open()
        self.assertTrue(self.flow.blocked(message))
        self.assertIsNone(self.flow.approved(message))
        await self.flow.handle(self.message('/duyetdangky ' + pending.id, user='admin-user'))
        self.assertFalse(self.flow.blocked(message))
        self.assertEqual(pending.id, self.flow.approved(message).id)

    async def test_sync_tickets_provisions_approved_member_without_any_static_grants(self):
        _, record = await self.approve()
        self.assertNotIn(record.user_id, self.settings.allowed_users)
        self.assertFalse(self.sheets.settings.allows(record.user_id, record.chat_id, 'PRIVATE'))
        chat = self.local_tickets()
        try:
            # Roster payloads cannot give a self-enrolled member a manager role.
            chat.service.provider.inspect_members.return_value[0]['role'] = 'manager'
            await self.flow.sync_tickets(record, chat)
            binding = chat.identities.resolve(chat.bot_scope, record.user_id, record.chat_id)
            self.assertEqual('member', binding['role'])
            self.assertEqual(record.name, binding['name'])
            self.assertNotIn(record.user_id, self.flow.store.admins)
            self.assertNotIn(record.user_id, self.settings.allowed_users)
            self.assertFalse(self.sheets.settings.allows(record.user_id, record.chat_id, 'PRIVATE'))
            response = await self.flow.handle(self.message('/thanhvien', user=record.user_id))
            self.assertIn('chưa được cấp quyền', response)
        finally:
            chat.store.close()

    async def test_sync_tickets_never_uses_stale_or_forged_registration_record(self):
        _, record = await self.approve()
        chat = SimpleNamespace(service=SimpleNamespace(mode='lab_live'),
                               store=Mock(), identities=Mock(), bot_scope='synthetic-scope')
        self.directory.lookup.reset_mock()
        for bad in (None, replace(record, chat_id='wrong-chat'), replace(record, user_id='other-user'),
                    replace(record, status='pending'), replace(record, id='DK-00000000')):
            with self.subTest(record=bad):
                with self.assertRaises(RegistrationError):
                    await self.flow.sync_tickets(bad, chat)
        self.flow.store.revoke('admin-user', record.member_id)
        with self.assertRaises(RegistrationError):
            await self.flow.sync_tickets(record, chat)
        self.directory.lookup.assert_not_awaited()
        self.assertEqual([], chat.store.mock_calls)
        self.assertEqual([], chat.identities.mock_calls)

    async def test_sync_tickets_cannot_provision_if_revoked_during_provider_inspection(self):
        _, record = await self.approve()
        chat = self.local_tickets()
        members = chat.service.provider.inspect_members.return_value
        def inspect_then_revoke():
            self.flow.store.revoke('admin-user', record.member_id)
            return members
        chat.service.provider.inspect_members.side_effect = inspect_then_revoke
        try:
            with self.assertRaises(RegistrationError):
                await self.flow.sync_tickets(record, chat)
            self.assertEqual(0, chat.store.db.execute('SELECT COUNT(*) FROM ticket_accounts').fetchone()[0])
            self.assertIsNone(chat.identities.resolve(chat.bot_scope, record.user_id, record.chat_id))
        finally:
            chat.store.close()

    def local_tickets(self, *, mode='lab_live', member_name=None, test_members=()):
        store = TicketStore(Path(self.temp.name) / ('tickets-' + mode + '.sqlite3'))
        provider = SimpleNamespace(
            settings=SimpleNamespace(spreadsheet_id=self.sheets.settings.spreadsheet_id),
            inspect_members=Mock(return_value=[{'member_id': 'lab-roster-alpha',
                                                'member_name': member_name or self.directory.person.name}]))
        return SimpleNamespace(store=store, identities=IdentityStore(store),
                               bot_scope=digest(self.settings.token), live_test=mode == 'lab_live_test',
                               service=SimpleNamespace(mode=mode, provider=provider, test_members=frozenset(test_members)))

    async def test_sync_tickets_provisions_verified_roster_binding_idempotently_without_remote_writes(self):
        _, record = await self.approve(user='static-user')
        chat = self.local_tickets()
        try:
            await self.flow.sync_tickets(record, chat)
            bound = chat.identities.resolve(chat.bot_scope, record.user_id, record.chat_id)
            self.assertIsNotNone(bound)
            self.assertEqual('lab-roster-alpha', bound['member_id'])
            self.assertEqual(record.name, bound['name'])
            self.assertEqual('member', bound['role'])
            await self.flow.sync_tickets(record, chat)
            self.assertEqual(bound, chat.identities.resolve(chat.bot_scope, record.user_id, record.chat_id))
            self.assertEqual(1, chat.store.db.execute('SELECT COUNT(*) FROM ticket_accounts').fetchone()[0])
            self.assertEqual(1, chat.store.db.execute('SELECT COUNT(*) FROM ticket_zalo_bindings').fetchone()[0])
            self.sheets._request.assert_not_awaited()
        finally:
            chat.store.close()

    async def test_sync_tickets_rejects_existing_different_identity_instead_of_overwriting(self):
        _, record = await self.approve(user='static-user')
        chat = self.local_tickets()
        try:
            chat.store.add_account('existing-member', 'Thành viên khác', 'another-member', 'member',
                                   'synthetic-local-only-password')
            chat.identities.bind(chat.bot_scope, record.user_id, record.chat_id, 'existing-member')
            before = chat.identities.resolve(chat.bot_scope, record.user_id, record.chat_id)
            with self.assertRaises(RegistrationError):
                await self.flow.sync_tickets(record, chat)
            self.assertEqual(before, chat.identities.resolve(chat.bot_scope, record.user_id, record.chat_id))
            self.assertEqual(1, chat.store.db.execute('SELECT COUNT(*) FROM ticket_accounts').fetchone()[0])
        finally:
            chat.store.close()

    async def test_reapproval_rotates_persisted_ticket_binding_and_rejects_old_confirmation(self):
        message, first = await self.approve(user='static-user')
        chat = self.local_tickets()
        try:
            await self.flow.sync_tickets(first, chat)
            old_binding = chat.identities.resolve(chat.bot_scope, first.user_id, first.chat_id)
            chat.service.store = chat.store
            chat.service.clock = lambda: 10000.0
            chat.service.target = 'synthetic-calendar-source'
            chat.service.require_manager = False
            chat.service.action = Mock(side_effect=AssertionError('No mutation expected'))
            ui = TicketsChat(chat.service, chat.identities, bot_scope=chat.bot_scope)
            scope = ui._scope(message)
            review = dict(step='review', ticket='DC-SYNTHETIC', version=1, code='3123',
                          actions=['accept'], display='Synthetic confirmation')
            ui._save(scope, old_binding, review)
            self.assertEqual(review, ui._load(scope, old_binding))

            await self.flow.handle(self.message('/thuhoidangky ' + first.member_id, user='admin-user'))
            message, second = await self.approve(user='static-user')
            self.assertEqual(first.member_id, second.member_id)
            self.assertNotEqual(first.id, second.id)
            # Read the approval again from disk before reconciling the binding.
            self.flow.close()
            self.flows.remove(self.flow)
            self.flow = self.open()
            second = self.flow.approved(message)
            await self.flow.sync_tickets(second, chat)
            new_binding = chat.identities.resolve(chat.bot_scope, second.user_id, second.chat_id)
            self.assertEqual(old_binding['id'], new_binding['id'])
            self.assertNotEqual(old_binding['_binding'], new_binding['_binding'])
            link = chat.store.db.execute('SELECT * FROM ticket_registration_links WHERE bot=? AND member_id=?',
                                         (chat.bot_scope, second.member_id)).fetchone()
            self.assertEqual(second.id, link['registration_id'])
            self.assertEqual(new_binding['id'], link['account'])
            self.assertIsNone(ui._load(scope, new_binding))
            response = ui.handle(self.message('/dongy 3123', user='static-user'))
            self.assertIn('/choxacnhan', response)
            chat.service.action.assert_not_called()

            # Restarting enrollment and repeated sync of this approval must not
            # invalidate a new confirmation or continually rotate its revision.
            ui._save(scope, new_binding, dict(review, code='3124'))
            self.flow.close()
            self.flows.remove(self.flow)
            self.flow = self.open()
            await self.flow.sync_tickets(self.flow.approved(message), chat)
            self.assertEqual(new_binding, chat.identities.resolve(chat.bot_scope, second.user_id, second.chat_id))
            self.assertEqual('3124', ui._load(scope, new_binding)['code'])
            self.assertEqual(1, chat.store.db.execute('SELECT COUNT(*) FROM ticket_registration_links').fetchone()[0])
            self.sheets._request.assert_not_awaited()
        finally:
            chat.store.close()

    async def test_reapproval_invalidates_legacy_binding_even_before_first_registration_sync(self):
        chat = self.local_tickets()
        try:
            chat.store.add_account('legacy-member', self.directory.person.name, 'lab-roster-alpha',
                                   'member', 'synthetic-local-only-password')
            chat.identities.bind(chat.bot_scope, 'static-user', 'dm-static-user', 'legacy-member')
            before = chat.identities.resolve(chat.bot_scope, 'static-user', 'dm-static-user')
            _, first = await self.approve(user='static-user')
            # No Tickets command has run since enrollment; there is no stored
            # registration link, but old legacy confirmations could still exist.
            await self.flow.handle(self.message('/thuhoidangky ' + first.member_id, user='admin-user'))
            _, second = await self.approve(user='static-user')
            await self.flow.sync_tickets(second, chat)
            after = chat.identities.resolve(chat.bot_scope, second.user_id, second.chat_id)
            self.assertEqual(before['id'], after['id'])
            self.assertNotEqual(before['_binding'], after['_binding'])
            await self.flow.sync_tickets(second, chat)
            self.assertEqual(after, chat.identities.resolve(chat.bot_scope, second.user_id, second.chat_id))
            self.sheets._request.assert_not_awaited()
        finally:
            chat.store.close()

    async def test_sync_tickets_does_not_expand_test_members_or_allow_other_modes(self):
        _, record = await self.approve(user='static-user')
        for mode in ('lab_live_test', 'sheets_test', 'demo'):
            with self.subTest(mode=mode):
                chat = self.local_tickets(mode=mode, test_members=('Thành viên Beta', 'Thành viên Gamma'))
                try:
                    with self.assertRaises(RegistrationError):
                        await self.flow.sync_tickets(record, chat)
                    self.assertEqual(0, chat.store.db.execute('SELECT COUNT(*) FROM ticket_accounts').fetchone()[0])
                    self.assertEqual(0, chat.store.db.execute('SELECT COUNT(*) FROM ticket_zalo_bindings').fetchone()[0])
                    chat.service.provider.inspect_members.assert_not_called()
                finally:
                    chat.store.close()

    async def test_sync_tickets_refuses_cross_sheet_source_and_ambiguous_roster_name(self):
        _, record = await self.approve(user='static-user')
        chat = self.local_tickets()
        try:
            chat.service.provider.settings.spreadsheet_id = 'another-synthetic-sheet'
            with self.assertRaises(RegistrationError):
                await self.flow.sync_tickets(record, chat)
            chat.service.provider.inspect_members.assert_not_called()
            chat.service.provider.settings.spreadsheet_id = self.sheets.settings.spreadsheet_id
            chat.service.provider.inspect_members.return_value = [
                {'member_id': 'lab-roster-alpha', 'member_name': record.name},
                {'member_id': 'lab-roster-beta', 'member_name': record.name},
            ]
            with self.assertRaises(RegistrationError):
                await self.flow.sync_tickets(record, chat)
            self.assertEqual(0, chat.store.db.execute('SELECT COUNT(*) FROM ticket_accounts').fetchone()[0])
        finally:
            chat.store.close()


if __name__ == '__main__':
    unittest.main()
