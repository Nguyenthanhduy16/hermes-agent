"""Enrollment deletion uses isolated stores, never live Zalo or Sheets."""
import asyncio
from dataclasses import replace
import json
import sqlite3
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_admin_members as fixtures
from hermes_zalo.admin_members import AdminMemberError, MemberAdmin
from hermes_zalo.conversations import ConversationStore


class AdminRegistrationResetTests(unittest.IsolatedAsyncioTestCase):
    add_account = fixtures.MemberAdminTests.add_account

    async def asyncSetUp(self):
        await fixtures.MemberAdminTests.asyncSetUp(self)
        self.store = self.work.registration.store
        self.conversations = ConversationStore(self.root / 'conversations.sqlite3')
        self.admin = MemberAdmin(self.work, conversations=self.conversations)

    async def asyncTearDown(self):
        self.conversations.close()
        await fixtures.MemberAdminTests.asyncTearDown(self)

    async def reset(self, **overrides):
        args = dict(member_id=self.record.member_id, request_id=self.record.id,
                    expected_revision=self.store.member_control(self.record.member_id)['revision'],
                    reason='Thành viên cần đăng ký lại đúng thông tin', actor=self.actor, confirm=True)
        args.update(overrides)
        return await self.admin.reset_registration(**args)

    def active_exchange(self, status):
        with self.ticket_store.transaction() as db:
            db.execute('DELETE FROM exchange_tickets')
            db.execute('INSERT INTO exchange_tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('synthetic-ticket', 'alpha-account', 'lab-beta', status, 1,
                 json.dumps({'a': {'member_id': 'lab-alpha'}, 'b': {'member_id': 'lab-beta'}}),
                 1, 9999999999, 'synthetic reason', 'synthetic-key', 'preview', 'source', 0))

    async def test_reset_without_roster_reads_blocks_legacy_grants_and_allows_fresh_registration(self):
        self.add_account(route=('member', 'dm-member'))
        self.directory.lookup.side_effect = RuntimeError('Workload temporarily unavailable')
        self.provider.inspect_members.side_effect = RuntimeError('Schedule temporarily invalid')
        result = await self.reset()
        self.assertTrue(result['ok'])
        self.assertEqual('reset', result['status'])
        self.assertNotIn(self.person.phone, json.dumps(result))
        self.directory.lookup.assert_not_awaited()
        self.provider.inspect_members.assert_not_called()
        self.assertIsNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))
        self.assertIsNotNone(self.ticket_store.account('alpha-account'))
        self.assertEqual([], (await self.admin.list_members())['items'])
        self.assertEqual(self.record.member_id, (await self.admin.list_members(status='reset'))['items'][0]['member_id'])
        for kind, chat_id in [('PRIVATE', 'dm-member'), ('GROUP', 'group')]:
            self.assertFalse(self.work.member_feature_allowed('member', chat_id, kind))
            self.assertFalse(self.work._calendar_scope_allowed('member', chat_id, kind))
        self.assertFalse(self.work.ticket_delivery_allowed('member', 'dm-member'))
        self.assertTrue(self.work.registration.bootstrap(replace(self.message, text='/dangky trangthai')))
        text = await self.work.registration.handle(replace(self.message, text='/dangky trangthai'))
        self.assertIn('đăng ký lại', text)
        self.assertIn('Workload', text)
        self.directory.lookup.side_effect = lambda phone: self.person
        reply = await self.work.registration.handle(replace(self.message, message_id='fresh-enrollment',
                                                           text='/dangky '+self.person.phone))
        self.assertIn('Đăng ký thành công', reply)
        latest = self.store.approved(self.message)
        self.assertEqual(self.record.member_id, latest.member_id)
        self.assertNotEqual(self.record.id, latest.id)
        self.assertTrue(self.work.member_feature_allowed('member', 'dm-member', 'PRIVATE'))
        self.assertNotIn(self.person.phone, json.dumps(await self.admin.audit(self.record.member_id)))

    async def test_invalid_confirmation_stale_revision_and_repeat_do_not_clear_links(self):
        self.add_account(route=('member', 'dm-member'))
        for overrides in ({'confirm':False}, {'expected_revision':True}, {'expected_revision':999},
                          {'request_id':'DK-FFFFFFFF'}, {'reason':'x'}, {'actor':'member'}):
            with self.subTest(overrides=overrides), self.assertRaises(AdminMemberError):
                await self.reset(**overrides)
            self.assertEqual(self.record, self.store.approved(self.message))
            self.assertIsNotNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))
        await self.reset()
        with self.assertRaises(AdminMemberError):
            await self.reset()

    async def test_locked_member_and_open_swaps_block_reset(self):
        self.add_account(route=('member', 'dm-member'))
        for status in ('pending_peer', 'pending_manager', 'applying', 'uncertain'):
            self.active_exchange(status)
            with self.assertRaisesRegex(AdminMemberError, 'shift exchange request'):
                await self.reset()
        self.active_exchange('completed')
        await self.admin.set_locked(self.record.member_id, True, self.actor,
            self.store.member_control(self.record.member_id)['revision'], confirm=True)
        with self.assertRaisesRegex(AdminMemberError, 'khóa'):
            await self.reset()
        self.assertIsNotNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))

    async def test_other_users_routes_and_admin_bindings_are_not_detached(self):
        self.add_account(route=('member', 'other-dm'))
        with self.assertRaisesRegex(AdminMemberError, 'conflicts'):
            await self.reset()
        self.assertIsNotNone(self.identities.resolve(self.chat.bot_scope, 'member', 'other-dm'))
        self.identities.revoke(self.chat.bot_scope, 'member', 'other-dm')
        with self.ticket_store.transaction() as db:
            db.execute("UPDATE ticket_accounts SET role='manager' WHERE id='alpha-account'")
        self.identities.bind(self.chat.bot_scope, 'member', 'dm-member', 'alpha-account')
        with self.assertRaisesRegex(AdminMemberError, 'administrative privileges'):
            await self.reset()
        self.assertEqual(self.record, self.store.approved(self.message))

    async def test_permission_rechecked_after_async_runtime_lookup(self):
        self.add_account(route=('member', 'dm-member'))
        guard = Mock(side_effect=PermissionError('permission revoked'))
        with self.assertRaises(PermissionError):
            await self.reset(authorize=guard)
        guard.assert_called_once_with()
        self.assertEqual(self.record, self.store.approved(self.message))
        self.assertIsNotNone(self.identities.resolve(self.chat.bot_scope, 'member', 'dm-member'))

    async def test_new_registration_during_runtime_await_blocks_stale_reset(self):
        async def changed():
            with self.store._transaction() as db:
                self.store._bump_revision(db, self.record.member_id)
            return self.chat
        with patch.object(self.admin, '_chat', side_effect=changed):
            with self.assertRaisesRegex(AdminMemberError, 'have been updated'):
                await self.reset()
        self.assertEqual(self.record, self.store.approved(self.message))

    async def test_cleanup_preserves_history_and_other_users(self):
        self.add_account(route=('member', 'dm-member'))
        row = self.conversations.record_incoming(replace(self.message, text='Old private detail'), self.record.member_id)
        self.conversations.complete_turn(self.message)
        self.conversations.save_memory({'content':'Old context', 'scope':'session',
                                       'session_key':self.message.session_key}, self.actor)
        personal = self.conversations.save_memory({'content':'Old personal memory', 'scope':'personal',
                                                  'member_id':self.record.member_id}, self.actor)
        old = self.work.reminders.create('member','dm-member','PRIVATE','Old reminder',200,now=100,request_key='old')
        other = self.work.reminders.create('other','dm-other','PRIVATE','Other reminder',200,now=100,request_key='other')
        await self.reset()
        self.assertEqual([],self.work.reminders.list_for('member','dm-member','PRIVATE'))
        self.assertEqual([other],self.work.reminders.list_for('other','dm-other','PRIVATE'))
        self.assertEqual('blocked',self.work.reminders.connection.execute('SELECT status FROM reminders WHERE id=?',(old.id,)).fetchone()[0])
        context = self.conversations.context(self.message.session_key)
        self.assertFalse(context['messages'])
        self.assertFalse(context['memories'])
        self.assertEqual(0,self.conversations.db.execute('SELECT active FROM conversation_memories WHERE id=?',(personal['id'],)).fetchone()[0])
        self.assertEqual(self.record.member_id,self.conversations.db.execute('SELECT member_id FROM conversation_messages WHERE id=?',(row['id'],)).fetchone()[0])

    async def test_final_store_failure_does_not_grant_new_identity_and_allows_retry(self):
        self.add_account(route=('member','dm-member'))
        with patch.object(self.store,'reset_registration',side_effect=sqlite3.OperationalError('synthetic failure')):
            with self.assertRaises(sqlite3.OperationalError):
                await self.reset()
        self.assertEqual(self.record,self.store.approved(self.message))
        self.assertIsNone(self.identities.resolve(self.chat.bot_scope,'member','dm-member'))
        self.assertTrue((await self.reset())['ok'])

    async def test_disabled_tickets_can_reset_but_broken_enabled_runtime_cannot(self):
        with patch.object(self.admin,'_chat',new=AsyncMock(return_value=None)):
            self.work._tickets_values['TICKETS_CHAT_MODE']='lab_live'
            with self.assertRaisesRegex(AdminMemberError,'could not be verified'):
                await self.reset()
            self.work._tickets_values['TICKETS_CHAT_MODE']='off'
            self.assertTrue((await self.reset())['ok'])


if __name__=='__main__':
    unittest.main()
