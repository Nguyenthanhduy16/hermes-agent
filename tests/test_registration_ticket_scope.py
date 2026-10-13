"""Enrollment checks follow actual ticket participants; all data stays local."""
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.sheets import SheetsSettings
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.chat import TicketsChat
from hermes_zalo.tickets.service import DemoAssignments, TicketService, TicketStore, digest
from hermes_zalo.work import WorkService
from hermes_zalo.workload import NAME_ERROR, WorkloadError, WorkloadPerson, WorkloadSettings


class RegistrationTicketScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.sequence = 0
        self.now = datetime.fromisoformat('2030-10-01T09:00:00+07:00').timestamp()
        self.settings = Settings('synthetic-ticket-registration',
            frozenset({'admin', 'a', 'b', 'c', 'manager'}), frozenset(),
            state_dir=Path(self.temp.name), knowledge_dir=Path(self.temp.name) / 'empty-docs')
        self.values = dict(REGISTRATION_ENABLED='true', REGISTRATION_ADMIN_USERS='admin',
            REGISTRATION_WORKLOAD_TAB='Synthetic Workload', REGISTRATION_WORKLOAD_NAME_COLUMN='A',
            REGISTRATION_WORKLOAD_PHONE_COLUMN='B', REGISTRATION_WORKLOAD_HEADER_ROW='1',
            REGISTRATION_WORKLOAD_NAME_HEADER='Họ tên', REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại')
        self.sheets = SimpleNamespace(settings=SheetsSettings('synthetic-sheet',
            Path(self.temp.name) / 'never-read.json', users=self.settings.allowed_users,
            calendar_format='lab', calendar_tabs=('Lịch lab thử',)), close=AsyncMock(),
            _request=AsyncMock(side_effect=AssertionError('No external API expected')))
        cfg = WorkloadSettings.from_mapping(self.values, self.sheets.settings)
        self.store = TicketStore(Path(self.temp.name) / 'synthetic-tickets.sqlite3')
        self.provider = DemoAssignments(self.store, now=self.now)
        self.provider.settings = SimpleNamespace(spreadsheet_id='synthetic-sheet', write_enabled=True,
                                                  exclusive_writer=True)
        members = [dict(member_id='coach-' + who, member_name=self.store.account('demo-' + who)['name'])
                   for who in ('a', 'b', 'c')]
        self.provider.inspect_members = Mock(return_value=members)
        self.provider.swap = Mock(wraps=self.provider.swap)
        self.ticket_service = TicketService(self.store, self.provider, mode='lab_live',
                                             require_manager=False, clock=lambda: self.now)
        self.identities = IdentityStore(self.store)
        self.bot = digest(self.settings.token)
        for who in ('a', 'b', 'c', 'manager'):
            self.identities.bind(self.bot, who, 'dm-' + who, 'demo-' + who)
        self.chat = TicketsChat(self.ticket_service, self.identities, bot_scope=self.bot)
        self.people = {who: WorkloadPerson(member['member_name'], f'090000000{i}', cfg.source_key,
                                            'synthetic-fingerprint-' + who, True)
                       for i, (who, member) in enumerate(zip(('a', 'b', 'c'), members), 1)}
        self.directory = SimpleNamespace(source_key=cfg.source_key, settings=cfg,
            lookup=AsyncMock(side_effect=self.lookup), resolve_name=AsyncMock(side_effect=self.resolve_name))
        self.work = WorkService(self.settings, sheets_client=self.sheets, environ=self.values,
                                tickets_chat=self.chat, registration_directory=self.directory)

    async def asyncTearDown(self):
        await self.work.close()
        self.store.close()
        self.temp.cleanup()

    async def lookup(self, phone):
        return next(person for person in self.people.values() if person.phone == phone)

    async def resolve_name(self, name):
        found = [person for person in self.people.values() if person.name == name and person.name_unique]
        if len(found) != 1:
            raise WorkloadError(NAME_ERROR)
        return found[0]

    def message(self, who, text):
        self.sequence += 1
        return IncomingMessage('dm-' + who, who, str(self.sequence), 'PRIVATE', text)

    async def say(self, who, text):
        return await self.work.handle(self.message(who, text))

    def dialog(self, who):
        scope = self.chat._scope(self.message(who, ''))
        row = self.store.db.execute('SELECT body FROM ticket_chat_dialogs WHERE scope=?', (scope,)).fetchone()
        return json.loads(row[0]) if row else None

    async def choose(self, who, item):
        code = next(choice['code'] for choice in self.dialog(who)['choices'] if choice['item']['id'] == item)
        return await self.say(who, code)

    async def draft(self, who='a', own='CA-11', peer='coach-b', other='CA-21'):
        self.assertIn('Bước 1/4', await self.say(who, '/doica'))
        await self.choose(who, own)
        await self.choose(who, peer)
        await self.choose(who, other)
        reply = await self.say(who, 'Việc riêng mô phỏng')
        self.assertIn('/doica gui', reply)
        return re.search(r'/doica gui [0-9]{4}', reply)[0]

    async def create(self, **kwargs):
        who = kwargs.get('who', 'a')
        command = await self.draft(**kwargs)
        reply = await self.say(who, command)
        self.assertIn('Đã gửi yêu cầu', reply)
        return re.search(r'DC-[A-F0-9]+', reply)[0]

    async def approve(self, who):
        await self.say(who, '/dangky ' + self.people[who].phone)
        pending = self.work.registration.store.user_state(who)
        await self.say('admin', '/duyetdangky ' + pending.id)
        return self.work.registration.identity(who)

    async def revoke(self, who):
        record = self.work.registration.identity(who) or await self.approve(who)
        await self.say('admin', '/thuhoidangky ' + record.member_id)

    def status(self, ticket):
        return self.store.db.execute('SELECT status FROM exchange_tickets WHERE id=?', (ticket,)).fetchone()[0]

    async def test_unrelated_revoked_member_does_not_block_wizard_notice_or_confirmation(self):
        await self.revoke('c')
        self.directory.resolve_name.reset_mock()
        ticket = await self.create()
        notice = await self.work.claim_ticket_notification()
        self.assertIsNotNone(notice)
        self.assertEqual('dm-b', notice['chat_id'])
        await self.work.finish_ticket_notification(notice['id'], 'sent')
        command = re.search(r'/dongy [0-9]{4}', notice['text'])[0]
        reply = await self.say('b', command)
        self.assertIn('Đã đổi ca', reply)
        self.assertEqual('completed', self.status(ticket))
        self.provider.swap.assert_called_once()
        self.assertNotIn(self.people['c'].name, [call.args[0] for call in self.directory.resolve_name.await_args_list])
        self.sheets._request.assert_not_awaited()

    async def test_selected_revoked_peer_blocks_wizard_but_restart_and_lists_still_work(self):
        await self.say('a', '/doica')
        await self.choose('a', 'CA-11')
        await self.revoke('b')
        reply = await self.choose('a', 'coach-b')
        self.assertIn('thu hồi', reply)
        self.assertEqual('person', self.dialog('a')['step'])
        self.assertIn('Bước 1/4', await self.say('a', '/doica'))
        self.assertIn('Chưa có', await self.say('a', '/yeucau'))
        self.provider.swap.assert_not_called()

    async def test_revoked_actual_participant_blocks_saved_confirmation_and_notification(self):
        ticket = await self.create()
        review = await self.say('b', '/yeucau ' + ticket)
        command = re.search(r'/dongy [0-9]{4}', review)[0]
        await self.revoke('a')
        self.assertIn('thu hồi', await self.say('b', command))
        self.assertIsNone(await self.work.claim_ticket_notification())
        self.assertEqual('blocked', self.store.db.execute('SELECT status FROM ticket_peer_notifications').fetchone()[0])
        self.assertEqual('pending_peer', self.status(ticket))
        self.provider.swap.assert_not_called()

    async def test_revoked_creator_in_one_queue_item_does_not_block_another_pair(self):
        blocked_ticket = await self.create(who='c', own='CA-31', peer='coach-a', other='CA-12')
        good_ticket = await self.create()
        await self.revoke('c')
        notice = await self.work.claim_ticket_notification()
        self.assertIsNotNone(notice)
        self.assertIn(good_ticket, notice['text'])
        states = {row['ticket']: row['status'] for row in self.store.db.execute('SELECT ticket,status FROM ticket_peer_notifications')}
        self.assertEqual('blocked', states[blocked_ticket])
        self.assertEqual('sending', states[good_ticket])
        self.provider.swap.assert_not_called()

    async def test_reapproved_creator_invalidates_other_person_old_review_code(self):
        await self.approve('a')
        ticket = await self.create()
        review = await self.say('b', '/yeucau ' + ticket)
        old_command = re.search(r'/dongy [0-9]{4}', review)[0]
        await self.revoke('a')
        await self.approve('a')
        reply = await self.say('b', old_command)
        self.assertIn('Liên kết người tham gia đã thay đổi', reply)
        self.assertEqual('pending_peer', self.status(ticket))
        self.provider.swap.assert_not_called()
        fresh = await self.say('b', '/yeucau ' + ticket)
        self.assertIn('Đã đổi ca', await self.say('b', re.search(r'/dongy [0-9]{4}', fresh)[0]))
        self.provider.swap.assert_called_once()

    async def test_notification_code_checks_its_own_pair_despite_unrelated_open_draft(self):
        ticket = await self.create(who='c', own='CA-31', peer='coach-b', other='CA-22')
        notice = await self.work.claim_ticket_notification()
        self.assertIsNotNone(notice)
        await self.work.finish_ticket_notification(notice['id'], 'sent')
        await self.draft(who='b', own='CA-21', peer='coach-a', other='CA-11')
        before = self.dialog('b')
        await self.revoke('c')
        reply = await self.say('b', re.search(r'/dongy [0-9]{4}', notice['text'])[0])
        self.assertIn('thu hồi', reply)
        self.assertEqual(before, self.dialog('b'))
        self.assertEqual('pending_peer', self.status(ticket))
        self.provider.swap.assert_not_called()

    async def test_foreign_ticket_id_does_not_lookup_its_private_participants(self):
        ticket = await self.create()
        self.directory.resolve_name.reset_mock()
        reply = await self.say('c', '/yeucau ' + ticket)
        self.assertIn('phạm vi được phép', reply)
        self.directory.resolve_name.assert_not_awaited()
        self.provider.swap.assert_not_called()

    async def test_approved_members_outside_env_complete_swap_and_receive_notice(self):
        self.work.settings = replace(self.settings, allowed_users=frozenset({'admin'}))
        self.work.registration.settings = self.work.settings
        self.sheets.settings = replace(self.sheets.settings, users=frozenset())
        await self.approve('a')
        await self.approve('b')
        # Approval already provisions both sides. B need not open Tickets
        # before A can select B and send the first proposal.
        self.assertIsNone(self.dialog('b'))
        ticket = await self.create()
        notice = await self.work.claim_ticket_notification()
        self.assertIsNotNone(notice)
        self.assertEqual(('b', 'dm-b'), (notice['user_id'], notice['chat_id']))
        self.assertTrue(self.work.ticket_delivery_allowed('b', 'dm-b'))
        await self.work.finish_ticket_notification(notice['id'], 'sent')
        reply = await self.say('b', re.search(r'/dongy [0-9]{4}', notice['text'])[0])
        self.assertIn('Đã đổi ca', reply)
        self.assertEqual('completed', self.status(ticket))
        self.provider.swap.assert_called_once()
        self.assertEqual(frozenset({'admin'}), self.work.settings.allowed_users)
        self.assertEqual(frozenset(), self.sheets.settings.users)
        await self.revoke('b')
        self.assertFalse(self.work.ticket_delivery_allowed('b', 'dm-b'))
        self.assertIn('thu hồi', await self.say('b', '/doica'))
        self.sheets._request.assert_not_awaited()

    async def test_auto_registration_provisions_member_without_manual_approval_or_schedule_write(self):
        self.work.registration.auto_approve = True
        self.work.settings = replace(self.settings, allowed_users=frozenset({'admin'}))
        self.work.registration.settings = self.work.settings
        self.sheets.settings = replace(self.sheets.settings, users=frozenset())
        for who in ('a', 'b'):
            reply = await self.say(who, '/dangky ' + self.people[who].phone)
            self.assertIn('Đăng ký thành công', reply)
            record = self.work.registration.identity(who)
            self.assertIsNotNone(record)
            binding = self.identities.resolve(self.bot, who, 'dm-' + who)
            self.assertIsNotNone(binding)
            self.assertEqual(record.name, binding['name'])
        self.assertEqual([], self.work.registration.store.pending('admin'))
        self.assertIsNone(self.dialog('b'))
        self.assertIn('Bước 1/4', await self.say('a', '/doica'))
        self.provider.swap.assert_not_called()
        self.sheets._request.assert_not_awaited()

    async def test_unbound_static_user_gets_phone_registration_guide(self):
        reply = await self.say('admin', '/doica')
        self.assertIn('/dangky SỐ_ĐIỆN_THOẠI', reply)
        self.assertIn('không cần thêm ID riêng', reply)
        self.assertNotIn('chỉ giúp lọc lịch', reply)
        self.directory.lookup.assert_not_awaited()
        self.directory.resolve_name.assert_not_awaited()

    async def test_revocation_during_confirmation_source_read_prevents_swap(self):
        creator = await self.approve('a')
        await self.approve('b')
        ticket = await self.create()
        review = await self.say('b', '/yeucau ' + ticket)
        command = re.search(r'/dongy [0-9]{4}', review)[0]
        original = self.ticket_service._still_valid
        def revoke_after_read(row):
            body = original(row)
            self.work.registration.store.revoke('admin', creator.member_id)
            return body
        self.ticket_service._still_valid = Mock(side_effect=revoke_after_read)
        reply = await self.say('b', command)
        self.assertNotIn('Đã đổi ca', reply)
        self.assertNotEqual('completed', self.status(ticket))
        self.provider.swap.assert_not_called()
        self.assertEqual('revoked', self.work.registration.store.user_state('a').status)

    async def test_reapproval_during_confirmation_does_not_restore_captured_grant(self):
        creator = await self.approve('a')
        await self.approve('b')
        ticket = await self.create()
        review = await self.say('b', '/yeucau ' + ticket)
        command = re.search(r'/dongy [0-9]{4}', review)[0]
        original = self.ticket_service._still_valid
        def replace_after_read(row):
            body = original(row)
            registry = self.work.registration.store
            registry.revoke('admin', creator.member_id)
            pending = registry.request(self.message('a', '/dangky'), self.people['a'])
            registry.approve('admin', pending.id, self.people['a'])
            return body
        self.ticket_service._still_valid = Mock(side_effect=replace_after_read)
        reply = await self.say('b', command)
        self.assertNotIn('Đã đổi ca', reply)
        self.assertNotEqual('completed', self.status(ticket))
        self.provider.swap.assert_not_called()
        self.assertNotEqual(creator.id, self.work.registration.store.user_state('a').id)

    async def test_creator_revoked_during_notification_source_read_is_not_delivered(self):
        creator = await self.approve('a')
        ticket = await self.create()
        original = self.ticket_service._still_valid
        revoked = False
        def revoke_while_reading(row):
            nonlocal revoked
            if not revoked:
                revoked = True
                self.work.registration.store.revoke('admin', creator.member_id)
            return original(row)
        self.ticket_service._still_valid = Mock(side_effect=revoke_while_reading)
        self.assertTrue(self.work.ticket_delivery_allowed('b', 'dm-b'))
        self.assertIsNone(await self.work.claim_ticket_notification())
        self.assertTrue(revoked)
        self.assertTrue(self.work.ticket_delivery_allowed('b', 'dm-b'))
        self.assertEqual('blocked', self.store.db.execute('SELECT status FROM ticket_peer_notifications').fetchone()[0])
        self.assertEqual('pending_peer', self.status(ticket))
        self.provider.swap.assert_not_called()
        self.sheets._request.assert_not_awaited()

    async def test_reapproval_during_notification_source_read_does_not_reuse_old_grant(self):
        first = await self.approve('a')
        ticket = await self.create()
        original = self.ticket_service._still_valid
        second = None
        def replace_grant_while_reading(row):
            nonlocal second
            if second is None:
                registry = self.work.registration.store
                registry.revoke('admin', first.member_id)
                requested = registry.request(self.message('a', '/dangky'), self.people['a'])
                second = registry.approve('admin', requested.id, self.people['a'])
            return original(row)
        self.ticket_service._still_valid = Mock(side_effect=replace_grant_while_reading)
        self.assertIsNone(await self.work.claim_ticket_notification())
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.member_id, second.member_id)
        self.assertTrue(self.work.ticket_delivery_allowed('a', 'dm-a'))
        self.assertTrue(self.work.ticket_delivery_allowed('b', 'dm-b'))
        self.assertEqual('blocked', self.store.db.execute('SELECT status FROM ticket_peer_notifications').fetchone()[0])
        self.assertEqual('pending_peer', self.status(ticket))
        self.provider.swap.assert_not_called()
        self.sheets._request.assert_not_awaited()

    async def test_postclaim_revocation_does_not_hold_up_an_unrelated_pair(self):
        creator = await self.approve('a')
        blocked_ticket = await self.create()
        good_ticket = await self.create(who='c', own='CA-31', peer='coach-b', other='CA-22')
        original = self.ticket_service._still_valid
        revoked = False
        def revoke_one_creator_while_reading(row):
            nonlocal revoked
            if row['id'] == blocked_ticket and not revoked:
                revoked = True
                self.work.registration.store.revoke('admin', creator.member_id)
            return original(row)
        self.ticket_service._still_valid = Mock(side_effect=revoke_one_creator_while_reading)
        notice = await self.work.claim_ticket_notification()
        self.assertIsNotNone(notice)
        self.assertIn(good_ticket, notice['text'])
        states = {row['ticket']: row['status'] for row in self.store.db.execute('SELECT ticket,status FROM ticket_peer_notifications')}
        self.assertEqual('blocked', states[blocked_ticket])
        self.assertEqual('sending', states[good_ticket])
        self.provider.swap.assert_not_called()
        self.sheets._request.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
