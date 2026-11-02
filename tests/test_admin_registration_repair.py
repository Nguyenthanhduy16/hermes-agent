"""Web correction orchestration with local synthetic stores, never live Sheets."""
import asyncio
from dataclasses import replace
import json
import sqlite3
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_admin_members as fixtures
from hermes_zalo.admin_members import AdminMemberError, MemberAdmin
from hermes_zalo.conversations import ConversationStore
from hermes_zalo.workload import WorkloadError


class AdminRegistrationRepairTests(unittest.IsolatedAsyncioTestCase):
    add_account = fixtures.MemberAdminTests.add_account

    async def asyncSetUp(self):
        await fixtures.MemberAdminTests.asyncSetUp(self)
        self.target = replace(self.person, name='Người Thử Beta', phone='0900000002', fingerprint='synthetic-beta')
        self.directory.lookup.side_effect = lambda phone: self.target if phone == self.target.phone else self.person
        self.provider.inspect_members.return_value = [
            dict(member_id='lab-alpha', member_name=self.person.name),
            dict(member_id='lab-beta', member_name=self.target.name)]
        self.now = 10000.0
        self.conversations = ConversationStore(self.root / 'conversations.sqlite3')
        self.admin = MemberAdmin(self.work, clock=lambda: self.now, conversations=self.conversations)
        self.store = self.work.registration.store

    async def asyncTearDown(self):
        self.conversations.close()
        await fixtures.MemberAdminTests.asyncTearDown(self)

    async def preview(self, actor=None, phone=None):
        return await self.admin.preview_registration(self.record.member_id, phone or self.target.phone,
                                                      'Đã xác minh đúng chủ tài khoản', actor or self.actor)

    async def confirm(self, preview, actor=None, **kwargs):
        return await self.admin.confirm_registration(preview['preview_token'], actor or self.actor, **kwargs)

    def assert_original(self):
        self.assertEqual(self.record, self.store.approved(self.message))

    def open_exchange(self, status='pending_peer', *, member='lab-alpha', creator='alpha-account'):
        with self.ticket_store.transaction() as db:
            db.execute('DELETE FROM exchange_tickets')
            db.execute('INSERT INTO exchange_tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('ticket-fixture', creator, 'lab-beta', status, 1,
                        json.dumps({'a': {'member_id': member}, 'b': {'member_id': 'lab-beta'}}),
                        1, 9999999999, 'synthetic reason', 'idempotent-fixture', 'preview', 'fixture-source', 0))

    async def test_normalized_phone_preview_changes_nothing_confirm_detaches_then_grants_correct_identity(self):
        self.add_account(route=('member', 'dm-member'))
        before = self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member')
        preview = await self.preview(phone='+84 90 000 0002')
        self.directory.lookup.assert_awaited_with(self.target.phone)
        self.assert_original()
        self.assertEqual(before, self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))
        self.assertNotIn(self.target.phone, json.dumps(preview))
        result = await self.confirm(preview)
        corrected = self.store.approved(self.message)
        self.assertTrue(result['ok'])
        self.assertEqual(self.target.name, corrected.name)
        self.assertNotEqual(self.record.member_id, corrected.member_id)
        self.assertEqual((self.record.user_id, self.record.chat_id), (corrected.user_id, corrected.chat_id))
        self.assertEqual('revoked', self.store.latest_member(self.record.member_id).status)
        self.assertIsNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))
        self.assertIsNotNone(self.ticket_store.account('alpha-account'))
        self.assertEqual(frozenset({'admin'}), self.store.admins)
        self.assertNotIn(self.target.phone, json.dumps(await self.admin.audit(corrected.member_id)))

    async def test_fresh_directory_change_or_lookup_failure_preserves_registration_and_link(self):
        self.add_account(route=('member', 'dm-member'))
        preview = await self.preview()
        self.target = replace(self.target, fingerprint='new-source-row')
        with self.assertRaisesRegex(AdminMemberError, 'Workload has changed'):
            await self.confirm(preview)
        self.assert_original()
        self.assertIsNotNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))
        self.directory.lookup.side_effect = WorkloadError('Chưa đọc được Workload.')
        with self.assertRaisesRegex(AdminMemberError, 'Workload'):
            await self.preview()

    async def test_duplicate_name_and_missing_or_ambiguous_schedule_do_not_guess(self):
        original = self.target
        self.target = replace(original, name_unique=False)
        with self.assertRaisesRegex(AdminMemberError, 'duplicate names'):
            await self.preview()
        self.target = original
        for candidates in ([], [dict(member_id='lab-beta', member_name=original.name)] * 2):
            self.provider.inspect_members.return_value = candidates
            with self.assertRaisesRegex(AdminMemberError, 'unique'):
                await self.preview()
        self.assert_original()

    async def test_roster_phone_not_found_does_not_use_ai_or_leak_input(self):
        self.directory.lookup.side_effect = WorkloadError('Không tìm thấy số điện thoại này trong Workload.')
        with self.assertRaises(AdminMemberError) as raised:
            await self.preview()
        self.assertNotIn(self.target.phone, str(raised.exception))
        self.assert_original()

    async def test_missing_ticket_runtime_blocks_when_enabled_or_open_failed(self):
        with patch.object(self.admin, '_chat', new=AsyncMock(return_value=None)):
            self.work._tickets_values['TICKETS_CHAT_MODE'] = 'lab_live'
            with self.assertRaisesRegex(AdminMemberError, 'could not be verified'):
                await self.preview()
            self.work._tickets_values['TICKETS_CHAT_MODE'] = 'off'
            self.work._tickets_error = True
            with self.assertRaisesRegex(AdminMemberError, 'could not be verified'):
                await self.preview()
        self.assert_original()

    async def test_disabled_tickets_still_allows_registration_correction(self):
        with patch.object(self.admin, '_chat', new=AsyncMock(return_value=None)):
            self.work._tickets_values['TICKETS_CHAT_MODE'] = 'off'
            self.work._tickets_error = False
            result = await self.confirm(await self.preview())
        self.assertTrue(result['ok'])
        self.assertEqual(self.target.name, self.store.approved(self.message).name)

    async def test_schedule_provider_failure_before_preview_or_confirm_never_changes_grant(self):
        preview = await self.preview()
        self.provider.inspect_members.side_effect = RuntimeError('synthetic upstream failure')
        for operation in (self.preview, lambda: self.confirm(preview)):
            with self.assertRaisesRegex(AdminMemberError, 'schedule roster could not be read'):
                await operation()
        self.assert_original()

    async def test_target_tickets_route_belongs_to_other_person_is_never_overwritten(self):
        self.add_account('beta-account', 'lab-beta', self.target.name, ('other-user', 'other-dm'))
        with self.assertRaisesRegex(AdminMemberError, 'another Zalo account'):
            await self.preview()
        self.assertEqual('beta-account', self.identities.resolve(self.chat.bot_scope, 'other-user', 'other-dm')['id'])
        self.assert_original()

    async def test_pending_or_approved_target_registration_blocks_even_without_tickets_binding(self):
        target_record = self.store.request(replace(self.message, user_id='other-user', chat_id='other-dm', message_id='other'), self.target)
        for status in ('pending', 'approved'):
            if status == 'approved':
                self.store.approve('admin', target_record.id, self.target)
            with self.assertRaisesRegex(AdminMemberError, 'đăng ký khác'):
                await self.preview()
        self.assert_original()

    async def test_pending_exchanges_of_old_or_new_identity_block_preview_and_confirmation(self):
        self.add_account(route=('member', 'dm-member'))
        preview = await self.preview()
        for status in ('pending_peer', 'pending_manager', 'applying', 'uncertain'):
            self.open_exchange(status)
            with self.assertRaisesRegex(AdminMemberError, 'shift exchange request'):
                await self.preview()
            with self.assertRaisesRegex(AdminMemberError, 'shift exchange request'):
                await self.confirm(preview)
        self.assert_original()
        self.assertIsNotNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))

    async def test_stale_binding_snapshot_or_member_revision_requires_new_preview(self):
        self.add_account(route=('member', 'dm-member'))
        preview = await self.preview()
        self.identities.bind(self.chat.bot_scope, 'member', 'dm-member', 'alpha-account')
        with self.assertRaisesRegex(AdminMemberError, 'changed'):
            await self.confirm(preview)
        preview = await self.preview()
        with self.store._transaction() as db:
            self.store._bump_revision(db, self.record.member_id)
        with self.assertRaisesRegex(AdminMemberError, 'changed'):
            await self.confirm(preview)
        self.assert_original()

    async def test_replay_actor_mismatch_and_expiry_never_double_apply(self):
        preview = await self.preview()
        with self.assertRaises(AdminMemberError):
            await self.confirm(preview, actor='web:other')
        self.now += 301
        with self.assertRaises(AdminMemberError):
            await self.confirm(preview)
        preview = await self.preview()
        await self.confirm(preview)
        with self.assertRaises(AdminMemberError):
            await self.confirm(preview)
        self.assertEqual(2, self.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])

    async def test_two_admin_previews_have_one_winner(self):
        first, second = await self.preview(), await self.preview(actor='web:second')
        results = await asyncio.gather(self.confirm(first), self.confirm(second, actor='web:second'), return_exceptions=True)
        self.assertEqual(1, sum(isinstance(result, dict) and result.get('ok') for result in results))
        self.assertEqual(1, sum(isinstance(result, AdminMemberError) for result in results))
        self.assertEqual(2, self.store.db.execute('SELECT COUNT(*) FROM registration_requests').fetchone()[0])

    async def test_permission_callback_is_rechecked_after_roster_await_before_mutation(self):
        self.add_account(route=('member', 'dm-member'))
        preview = await self.preview()
        authorize = Mock(side_effect=PermissionError('permission withdrawn'))
        with self.assertRaises(PermissionError):
            await self.confirm(preview, authorize=authorize)
        authorize.assert_called_once_with()
        self.assert_original()
        self.assertIsNotNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))
        # Refused authorization did not consume a valid preview.
        await self.confirm(preview, authorize=Mock())

    async def test_cleanup_stops_old_reminders_and_issue_drafts_preserves_other_users(self):
        reminders, issues = self.work.reminders, self.work.issues
        old_reminder = reminders.create('member', 'dm-member', 'PRIVATE', 'Old identity secret', 200,
                                        now=100, request_key='old-reminder')
        other_reminder = reminders.create('other', 'dm-other', 'PRIVATE', 'Other member', 200,
                                          now=100, request_key='other-reminder')
        old_issue = issues.create(self.message, 'Old draft', 'Old identity detail', 'synthetic/issues', now=100)
        other_message = replace(self.message, user_id='other', chat_id='dm-other')
        other_issue = issues.create(other_message, 'Other draft', 'Other detail', 'synthetic/issues', now=100)
        await self.confirm(await self.preview())
        self.assertEqual([], reminders.list_for('member', 'dm-member', 'PRIVATE'))
        self.assertEqual([other_reminder], reminders.list_for('other', 'dm-other', 'PRIVATE'))
        self.assertEqual('blocked', reminders.connection.execute('SELECT status FROM reminders WHERE id=?', (old_reminder.id,)).fetchone()[0])
        self.assertIsNone(issues.scoped(old_issue['id'], self.message))
        self.assertEqual(other_issue, issues.scoped(other_issue['id'], other_message))
        self.assertEqual('cancelled', issues.db.execute('SELECT status FROM drafts WHERE id=?', (old_issue['id'],)).fetchone()[0])

    async def test_context_is_detached_without_transferring_history_or_personal_memory(self):
        message = replace(self.message, text='Private detail of old identity', message_id='old-context')
        old_row = self.conversations.record_incoming(message, self.record.member_id)
        self.conversations.record_outgoing(message, 'Old answer', delivery_key='old-answer')
        self.conversations.complete_turn(message)
        memory = self.conversations.save_memory({'content': 'Old personal memory', 'scope': 'personal',
                                                  'member_id': self.record.member_id}, self.actor)
        result = await self.confirm(await self.preview())
        new_message = replace(message, text='New identity hello', message_id='new-context')
        self.conversations.record_incoming(new_message, result['member_id'])
        payload = self.conversations.prepare_context(new_message, result['member_id'])
        self.assertNotIn('Old personal memory', payload['payload'])
        self.assertNotIn('Private detail of old identity', payload['payload'])
        self.assertEqual(self.record.member_id, self.conversations.db.execute(
            'SELECT member_id FROM conversation_messages WHERE id=?', (old_row['id'],)).fetchone()[0])
        self.assertEqual(self.record.member_id, self.conversations.db.execute(
            'SELECT member_id FROM conversation_memories WHERE id=?', (memory['id'],)).fetchone()[0])

    async def test_final_registration_failure_only_removes_old_state_retry_preserves_identity_until_commit(self):
        self.add_account(route=('member', 'dm-member'))
        self.work.reminders.create('member', 'dm-member', 'PRIVATE', 'Old reminder', 200, now=100, request_key='old')
        preview = await self.preview()
        with patch.object(self.store, 'correct_registration', side_effect=sqlite3.OperationalError('synthetic failure')):
            with self.assertRaises(sqlite3.OperationalError):
                await self.confirm(preview)
        self.assert_original()
        self.assertIsNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))
        self.assertEqual([], self.work.reminders.list_for('member', 'dm-member', 'PRIVATE'))
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM registration_members').fetchone()[0])
        result = await self.confirm(await self.preview())
        self.assertTrue(result['ok'])
        self.assertEqual(self.target.name, self.store.approved(self.message).name)


if __name__ == '__main__':
    unittest.main()
