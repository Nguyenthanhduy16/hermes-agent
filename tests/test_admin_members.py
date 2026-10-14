"""Admin member changes use disposable stores and synthetic roster reads only."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from hermes_zalo.admin_members import AdminMemberError, MemberAdmin
from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.registration import RegistrationError, RegistrationStore
from hermes_zalo.sheets import SheetsSettings
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.service import TicketStore, digest
from hermes_zalo.work import WorkService
from hermes_zalo.workload import WorkloadPerson, WorkloadSettings


class MemberAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = Settings('synthetic-admin-member', frozenset({'admin', 'member'}), frozenset({'group'}),
                                 state_dir=self.root, knowledge_dir=self.root / 'empty')
        self.values = dict(REGISTRATION_ENABLED='true', REGISTRATION_ADMIN_USERS='admin', REGISTRATION_AUTO_APPROVE='true',
                           REGISTRATION_WORKLOAD_TAB='Synthetic', REGISTRATION_WORKLOAD_NAME_COLUMN='A',
                           REGISTRATION_WORKLOAD_PHONE_COLUMN='B', REGISTRATION_WORKLOAD_HEADER_ROW='1',
                           REGISTRATION_WORKLOAD_NAME_HEADER='Họ tên', REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại')
        self.sheets = SimpleNamespace(settings=SheetsSettings('synthetic-sheet', self.root / 'unused.json',
            users=frozenset({'member'}), groups=frozenset({'group'}), calendar_format='lab',
            calendar_tabs=('Lịch lab thử',), issues_enabled=False), close=AsyncMock())
        source = WorkloadSettings.from_mapping(self.values, self.sheets.settings).source_key
        self.person = WorkloadPerson('Người Thử Alpha', '0900000001', source, 'synthetic-fingerprint', True)
        self.directory = SimpleNamespace(source_key=source, lookup=AsyncMock(side_effect=lambda phone: self.person))
        self.ticket_store = TicketStore(self.root / 'tickets.sqlite3')
        self.identities = IdentityStore(self.ticket_store)
        self.provider = SimpleNamespace(settings=SimpleNamespace(spreadsheet_id='synthetic-sheet'),
            inspect_members=Mock(return_value=[dict(member_id='lab-alpha', member_name=self.person.name)]))
        self.chat = SimpleNamespace(store=self.ticket_store, identities=self.identities,
            service=SimpleNamespace(mode='lab_live', provider=self.provider), bot_scope=digest(self.settings.token),
            live_test=False, close=self.ticket_store.close)
        self.work = WorkService(self.settings, sheets_client=self.sheets, environ=self.values,
                                tickets_chat=self.chat, registration_directory=self.directory)
        self.message = IncomingMessage('dm-member', 'member', 'enroll', 'PRIVATE', '/dangky synthetic')
        self.record = self.work.registration.store.request(self.message, self.person, auto_approve=True)
        self.admin = MemberAdmin(self.work)
        self.actor = 'web:operator'

    async def asyncTearDown(self):
        await self.work.close()
        self.temp.cleanup()

    async def lock(self, locked=True):
        revision = self.work.registration.store.member_control(self.record.member_id)['revision']
        return await self.admin.set_locked(self.record.member_id, locked, self.actor, revision, confirm=True)

    def add_account(self, account='alpha-account', member='lab-alpha', name=None, route=None):
        with self.ticket_store.transaction() as db:
            db.execute('INSERT INTO ticket_accounts VALUES (?,?,?,?,?,1)',
                       (account, name or self.person.name, 'member', member, '!no-login-fixture'))
        if route:
            self.identities.bind(self.chat.bot_scope, *route, account)
        return account

    async def preview(self):
        detail = await self.admin.detail(self.record.member_id)
        return await self.admin.preview_link(self.record.member_id, detail['candidates'][0]['account_id'], self.actor)

    async def test_member_list_search_status_pagination_and_masking(self):
        store = self.work.registration.store
        other_person = replace(self.person, phone='0900000002', name='Người Thử Beta')
        other = store.request(replace(self.message, user_id='beta', chat_id='dm-beta', message_id='beta'), other_person)
        listed = await self.admin.list_members(query='nguoi thu', page=1, limit=1)
        self.assertEqual(2, listed['total'])
        self.assertEqual(1, len(listed['items']))
        pending = await self.admin.list_members(status='pending')
        self.assertEqual([other.member_id], [item['member_id'] for item in pending['items']])
        self.assertNotIn(self.person.phone, json.dumps(listed))
        self.assertNotIn(other_person.phone, json.dumps(pending))
        await self.lock()
        self.assertEqual(self.record.member_id, (await self.admin.list_members(status='locked'))['items'][0]['member_id'])

    async def test_lock_overrides_static_grants_and_self_enrollment_without_changing_identity(self):
        await self.lock()
        for msg in (self.message, replace(self.message, text='/menu'), replace(self.message, chat_type='GROUP', chat_id='group')):
            self.assertTrue(self.work.registration_blocked(msg))
            self.assertFalse(self.work.member_feature_allowed(msg.user_id, msg.chat_id, msg.chat_type))
            self.assertFalse(self.work._calendar_scope_allowed(msg.user_id, msg.chat_id, msg.chat_type))
        self.assertFalse(self.work.registration_admission(self.message))
        self.assertFalse(self.work.ticket_delivery_allowed('member', 'dm-member'))
        response = await self.work.registration.handle(replace(self.message, text='/dangky 0900000001'))
        self.assertIn('bị khóa', response)
        with self.assertRaises(RegistrationError):
            self.work.registration.store.request(replace(self.message, message_id='new-phone'),
                                                  replace(self.person, phone='0900000099'), auto_approve=True)
        self.assertEqual(self.record, self.work.registration.store.user_state('member'))

    async def test_lock_persists_after_store_reopen_and_audit_excludes_phone(self):
        await self.lock()
        store = self.work.registration.store
        path = Path(store.db.execute('PRAGMA database_list').fetchone()['file'])
        store.close()
        self.work.registration.store = RegistrationStore(path, admins={'admin'})
        self.assertTrue(self.work.member_admin_locked('member', 'dm-member'))
        events = await self.admin.audit(self.record.member_id)
        event = events['items'][0]
        self.assertEqual('locked', event['action'])
        self.assertEqual({'locked': False, 'status': 'approved'}, event['before'])
        self.assertEqual({'locked': True, 'status': 'approved'}, event['after'])
        self.assertNotIn(self.person.phone, json.dumps(events))

    async def test_unlock_revalidates_roster_and_never_undoes_revocation(self):
        await self.lock()
        original = self.person
        self.person = replace(original, fingerprint='source-changed')
        with self.assertRaises(AdminMemberError):
            await self.lock(False)
        self.assertTrue(self.work.member_admin_locked('member'))
        self.person = original
        self.work.registration.store.revoke('admin', self.record.member_id)
        await self.lock(False)
        self.assertFalse(self.work.member_admin_locked('member'))
        self.assertIsNone(self.work.registration.identity('member'))
        self.assertEqual('revoked', self.work.registration.store.user_state('member').status)

    async def test_concurrent_revision_has_exactly_one_winner(self):
        revision = self.work.registration.store.member_control(self.record.member_id)['revision']
        results = await asyncio.gather(*[
            self.admin.set_locked(self.record.member_id, True, 'web:' + who, revision, True)
            for who in ('one', 'two')], return_exceptions=True)
        self.assertEqual(1, sum(isinstance(item, AdminMemberError) for item in results))
        self.assertEqual(1, sum(item['action'] == 'locked' for item in (await self.admin.audit(self.record.member_id))['items']))
        with self.assertRaises(AdminMemberError):
            await self.admin.set_locked(self.record.member_id, False, self.actor, revision, False)

    async def test_duplicate_names_block_link_candidates_and_do_not_guess(self):
        self.person = replace(self.person, name_unique=False)
        detail = await self.admin.detail(self.record.member_id)
        self.assertEqual([], detail['candidates'])
        self.assertIn('Tên trùng', detail['source_reason'])
        self.assertIsNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))

    async def test_preview_does_not_write_and_confirm_creates_verified_member_link(self):
        preview = await self.preview()
        self.assertIsNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))
        with self.assertRaises(AdminMemberError):
            await self.admin.confirm_link(preview['preview_token'], 'web:other-operator')
        result = await self.admin.confirm_link(preview['preview_token'], self.actor)
        self.assertTrue(result['ok'])
        account = self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member')
        self.assertEqual('member', account['role'])
        self.assertEqual('lab-alpha', account['member_id'])
        self.assertNotIn('member', self.work.registration.store.admins)
        self.assertFalse(hasattr(self.provider, 'write'))
        with self.assertRaises(AdminMemberError):
            await self.admin.confirm_link(preview['preview_token'], self.actor)
        self.assertEqual('tickets_link', (await self.admin.audit(self.record.member_id))['items'][0]['action'])

    async def test_repair_wrong_current_link_changes_revision_and_preserves_other_owner(self):
        self.add_account('wrong', 'lab-beta', 'Người Thử Beta', ('member', 'dm-member'))
        old = self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member')
        detail = await self.admin.detail(self.record.member_id)
        self.assertEqual('conflict', detail['link_status'])
        preview = await self.preview()
        await self.admin.confirm_link(preview['preview_token'], self.actor)
        new = self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member')
        self.assertNotEqual(old['_binding'], new['_binding'])
        self.assertEqual('lab-alpha', new['member_id'])
        self.assertIsNotNone(self.ticket_store.account('wrong'))

    async def test_wrong_autoapproved_identity_requires_revoke_and_manual_review_before_link_repair(self):
        # The web repair is not an identity editor: a wrong phone must first be
        # revoked by a configured registration administrator, then submitted
        # again from the actual private route and explicitly reviewed.
        original = self.person
        self.add_account('original-account', 'lab-alpha', original.name, ('member', 'dm-member'))
        store = self.work.registration.store
        await self.lock()
        with self.assertRaises(RegistrationError):
            store.revoke(self.actor, self.record.member_id)
        store.revoke('admin', self.record.member_id)
        await self.lock(False)

        self.person = replace(original, name='Người Thử Beta', phone='0900000002',
                              fingerprint='synthetic-beta-fingerprint')
        self.provider.inspect_members.return_value = [dict(member_id='lab-beta', member_name=self.person.name)]
        correct = store.request(replace(self.message, message_id='correct-phone'), self.person, auto_approve=True)
        self.assertEqual('pending', correct.status)
        self.assertNotEqual(self.record.member_id, correct.member_id)
        self.assertFalse(self.work.registration.member_allows('member', 'dm-member', 'PRIVATE'))
        detail = await self.admin.detail(correct.member_id)
        self.assertEqual([], detail['candidates'])
        self.assertEqual('lab-alpha', self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member')['member_id'])

        correct = store.approve('admin', correct.id, self.person)
        detail = await self.admin.detail(correct.member_id)
        preview = await self.admin.preview_link(correct.member_id, detail['candidates'][0]['account_id'], self.actor)
        await self.admin.confirm_link(preview['preview_token'], self.actor)
        self.assertEqual('lab-beta', self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member')['member_id'])
        self.assertEqual('lab-alpha', self.ticket_store.account('original-account')['member_id'])
        self.assertEqual('revoked', store.latest_member(self.record.member_id).status)
        self.assertEqual(original.name, store.latest_member(self.record.member_id).name)
        self.assertTrue(self.work.registration.member_allows('member', 'dm-member', 'PRIVATE'))
        self.assertFalse(self.work.registration.member_allows('member', 'other-dm', 'PRIVATE'))
        self.assertFalse(self.work.registration.member_allows('member', 'new-group', 'GROUP'))
        self.assertEqual('tickets_link', (await self.admin.audit(correct.member_id))['items'][0]['action'])

    async def test_never_steals_account_from_another_private_route(self):
        self.add_account(route=('different-user', 'dm-different'))
        with self.assertRaisesRegex(AdminMemberError, 'thuộc người khác'):
            await self.preview()
        self.assertEqual('alpha-account', self.identities.resolve(self.chat.bot_scope, 'different-user', 'dm-different')['id'])

    async def test_new_registration_or_binding_revision_invalidates_preview(self):
        self.add_account(route=('member', 'dm-member'))
        preview = await self.preview()
        self.identities.bind(self.chat.bot_scope, 'member', 'dm-member', 'alpha-account')
        with self.assertRaisesRegex(AdminMemberError, 'vừa thay đổi'):
            await self.admin.confirm_link(preview['preview_token'], self.actor)
        preview = await self.preview()
        await self.lock()
        with self.assertRaisesRegex(AdminMemberError, 'vừa thay đổi'):
            await self.admin.confirm_link(preview['preview_token'], self.actor)

    async def test_active_exchange_prevents_repair_including_uncertain_write(self):
        account = self.add_account()
        for status in ('pending_peer', 'pending_manager', 'applying', 'uncertain'):
            with self.subTest(status=status):
                with self.ticket_store.transaction() as db:
                    db.execute('DELETE FROM exchange_tickets')
                    db.execute('INSERT INTO exchange_tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               ('ticket-fixture', account, 'lab-beta', status, 1,
                                json.dumps({'a': {'member_id': 'lab-alpha'}, 'b': {'member_id': 'lab-beta'}}),
                                1, 9999999999, 'synthetic reason', 'idempotent-fixture', 'preview', 'fixture-source', 0))
                with self.assertRaisesRegex(AdminMemberError, 'Còn yêu cầu'):
                    await self.preview()
        self.assertIsNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))

    async def test_expired_preview_and_arbitrary_target_are_rejected(self):
        preview = await self.preview()
        self.admin.clock = lambda: preview['expires'] + 1
        with self.assertRaises(AdminMemberError):
            await self.admin.confirm_link(preview['preview_token'], self.actor)
        with self.assertRaises(AdminMemberError):
            await self.admin.preview_link(self.record.member_id, 'forged-target', self.actor)

    async def test_unlock_restores_only_existing_member_scope_and_keeps_no_admin_role(self):
        original_groups, original_admins = self.settings.allowed_groups, self.work.registration.store.admins
        await self.lock()
        await self.lock(False)
        self.assertTrue(self.work.registration.member_allows('member', 'dm-member', 'PRIVATE'))
        self.assertFalse(self.work.registration.member_allows('member', 'different-dm', 'PRIVATE'))
        self.assertFalse(self.work.registration.member_allows('member', 'new-group', 'GROUP'))
        self.assertEqual(original_groups, self.settings.allowed_groups)
        self.assertEqual(original_admins, self.work.registration.store.admins)

    async def test_exchange_started_after_preview_blocks_confirmation(self):
        self.add_account()
        preview = await self.preview()
        with self.ticket_store.transaction() as db:
            db.execute('INSERT INTO exchange_tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('started-after-preview', 'alpha-account', 'lab-beta', 'applying', 1,
                        json.dumps({'a': {'member_id': 'lab-alpha'}, 'b': {'member_id': 'lab-beta'}}),
                        1, 9999999999, 'synthetic', 'new-idempotent', 'preview', 'fixture-source', 0))
        with self.assertRaisesRegex(AdminMemberError, 'Còn yêu cầu'):
            await self.admin.confirm_link(preview['preview_token'], self.actor)
        self.assertIsNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))

    async def test_lock_removes_static_admin_chat_escape(self):
        admin_person = replace(self.person, phone='0900000002', name='Synthetic Admin')
        message = replace(self.message, user_id='admin', chat_id='dm-admin', message_id='admin-register')
        record = self.work.registration.store.request(message, admin_person, auto_approve=True)
        revision = self.work.registration.store.member_control(record.member_id)['revision']
        await self.admin.set_locked(record.member_id, True, self.actor, revision, True)
        command = replace(message, text='/thanhvien')
        self.assertTrue(self.work.registration_blocked(command))
        self.assertFalse(self.work.registration.admin_command(command))
        self.assertIn('bị khóa', await self.work.registration.handle(command))


if __name__ == '__main__':
    unittest.main()
