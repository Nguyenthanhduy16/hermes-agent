"""Group onboarding uses synthetic events, identities and storage; no live APIs."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from hermes_zalo.conversations import ConversationStore
from hermes_zalo.events import IncomingMessage, parse_update
from hermes_zalo.registration_flow import GROUP_GUIDE, UNAVAILABLE
import test_registration_adapter as registration_contract
import test_adapter as contract


class GroupRegistrationGateTests(unittest.IsolatedAsyncioTestCase):
    # Reuse fixture construction, without inheriting its separate regression suite.
    asyncSetUpRegistration = registration_contract.RegistrationAdapterTests.asyncSetUp
    asyncTearDown = registration_contract.RegistrationAdapterTests.asyncTearDown

    async def asyncSetUp(self):
        await self.asyncSetUpRegistration()
        self.adapter.settings = replace(self.adapter.settings, group_access='members')
        self.adapter.work.settings = self.adapter.settings
        self.sheets.settings = replace(self.sheets.settings, groups=frozenset({'g1'}), group_members=True)
        self.adapter._bot_names = ('Bot LabCoach',)
        self.archive = ConversationStore(Path(self.temp.name) / 'onboarding-conversations.sqlite3')
        self.adapter.conversations = self.archive
        self.adapter.managed_chat = SimpleNamespace(reply=AsyncMock(return_value='Synthetic group answer'))
        self.member_sequence = 0

    def group_event(self, text='/menu', *, user='new-user', chat='g1', mid=None):
        self.sequence += 1
        return contract.update(mid or str(self.sequence), user=user, chat=chat, kind='GROUP',
                               text='@Bot LabCoach ' + text)

    def register(self, state='approved', *, user='new-user'):
        self.member_sequence += 1
        person = replace(self.directory.person, phone=f'090000{self.member_sequence:04d}',
                         fingerprint=f'synthetic-group-person-{self.member_sequence}')
        message = IncomingMessage('dm-' + user, user, f'reg-{self.member_sequence}', 'PRIVATE', '/dangky')
        store = self.adapter.work.registration.store
        record = store.request(message, person)
        if state in ('approved', 'revoked'):
            record = store.approve('u1', record.id, person)
        if state == 'revoked':
            record = store.revoke('u1', record.member_id)
        elif state == 'rejected':
            record = store.reject('u1', record.id)
        return record

    def assert_no_private_work(self):
        self.handler.assert_not_awaited()
        self.adapter.managed_chat.reply.assert_not_awaited()
        self.directory.lookup.assert_not_awaited()
        self.directory.resolve_name.assert_not_awaited()
        self.sheets.lab_calendar.assert_not_awaited()
        self.sheets._request.assert_not_awaited()
        self.assertEqual(0, self.archive.history()['total'])
        self.assertEqual(0, self.archive.sessions()['total'])

    async def test_unknown_group_caller_gets_one_safe_guide_before_archive_work_or_model(self):
        event = self.group_event('Lịch hôm nay có gì?', mid='duplicate-guide')
        with patch.object(self.adapter.work, 'handle', new=AsyncMock()) as handle:
            await self.adapter._dispatch(event)
            await self.adapter._dispatch(event)
            handle.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_awaited_once_with('g1', GROUP_GUIDE)
        self.assertIn('/dangky SỐ_ĐIỆN_THOẠI', GROUP_GUIDE)
        self.assertIn('Workload', GROUP_GUIDE)
        self.assertIn('hiện tại', GROUP_GUIDE.lower())
        self.assertIn('nhắn riêng', GROUP_GUIDE.lower())
        self.assert_no_private_work()
        self.assertIsNone(self.adapter._send_permit.get())
        self.assertFalse((await self.adapter.send('dm-new-user', 'unrequested DM')).success)

    async def test_pending_rejected_revoked_and_static_allowlisted_users_only_receive_group_guide(self):
        for state in ('pending', 'rejected', 'revoked'):
            user = 'synthetic-' + state
            self.register(state, user=user)
            await self.adapter._dispatch(self.group_event('/menu', user=user))
        # Being explicitly allowlisted, including registration-admin u1, is not enrollment.
        for user in ('u1', 'u2'):
            await self.adapter._dispatch(self.group_event('/lich homnay', user=user))
        self.assertEqual(5, self.adapter.client.send_text_chunk.await_count)
        self.assertTrue(all(call.args == ('g1', GROUP_GUIDE)
                            for call in self.adapter.client.send_text_chunk.await_args_list))
        self.assert_no_private_work()

    async def test_changed_workload_source_does_not_keep_group_access(self):
        self.register()
        self.directory.source_key = 'workload:changed-synthetic-source'
        await self.adapter._dispatch(self.group_event('Giải thích Python là gì?'))
        self.adapter.client.send_text_chunk.assert_awaited_once_with('g1', GROUP_GUIDE)
        self.assert_no_private_work()

    async def test_phone_sent_to_group_is_not_echoed_looked_up_or_archived(self):
        phone = self.directory.person.phone
        await self.adapter._dispatch(self.group_event('/dangky ' + phone))
        self.adapter.client.send_text_chunk.assert_awaited_once_with('g1', GROUP_GUIDE)
        self.assertNotIn(phone, GROUP_GUIDE)
        self.assertNotIn(self.directory.person.name, GROUP_GUIDE)
        self.assert_no_private_work()

    async def test_unshared_group_and_unsupported_group_never_receive_guide(self):
        await self.adapter._dispatch(self.group_event(chat='g2'))
        self.adapter._groups_supported = False
        await self.adapter._dispatch(self.group_event())
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assert_no_private_work()

    async def test_locked_member_remains_silent_instead_of_registration_bootstrap(self):
        record = self.register()
        db = self.adapter.work.registration.store.db
        db.execute('UPDATE registration_member_controls SET locked=1,revision=revision+1 WHERE member_id=?',
                   (record.member_id,))
        db.commit()
        await self.adapter._dispatch(self.group_event('/dangky'))
        await self.adapter._dispatch(self.group_event('/menu'))
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assert_no_private_work()

    async def test_approved_member_keeps_group_menu_calendar_and_normal_chat(self):
        record = self.register()
        for text in ('/menu', '/lich homnay', 'Giải thích Python là gì?'):
            event = self.group_event(text)
            self.archive.mark_notice(parse_update(event))
            await self.adapter._dispatch(event)
        replies = [call.args[1] for call in self.adapter.client.send_text_chunk.await_args_list]
        self.assertTrue(any('MENU BOT' in text for text in replies))
        self.assertIn('Synthetic approved calendar', replies)
        self.assertIn('Synthetic group answer', replies)
        self.sheets.lab_calendar.assert_awaited_once()
        self.adapter.managed_chat.reply.assert_awaited_once()
        self.assertNotIn('new-user', self.adapter.settings.allowed_users)
        self.assertEqual('dm-new-user', record.chat_id)
        self.assertTrue(all(call.args[0] == 'g1' for call in self.adapter.client.send_text_chunk.await_args_list))
        self.assertIsNone(self.adapter.work.registration.approved(parse_update(self.group_event())))
        self.assertFalse(self.adapter.work.registration.member_allows('new-user', 'g1', 'GROUP'))

    async def test_approval_does_not_expand_restricted_group_allowlist_or_sheets_acl(self):
        self.register()
        self.adapter.settings = replace(self.adapter.settings, group_access='allowlist')
        self.adapter.work.settings = self.adapter.settings
        await self.adapter._dispatch(self.group_event())
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.adapter.settings = replace(self.adapter.settings, group_access='members')
        self.adapter.work.settings = self.adapter.settings
        self.sheets.settings = replace(self.sheets.settings, group_members=False)
        event = self.group_event('/lich homnay')
        self.archive.mark_notice(parse_update(event))
        await self.adapter._dispatch(event)
        self.sheets.lab_calendar.assert_not_awaited()
        self.assertIn('chưa được cấp quyền', self.adapter.client.send_text_chunk.await_args.args[1])

    async def test_direct_work_service_blocks_every_feature_before_dependencies(self):
        router = SimpleNamespace(classify=AsyncMock(side_effect=AssertionError('No NLU for unregistered group')),
                                 close=AsyncMock())
        self.adapter.work.intent_router = router
        with patch.object(self.adapter.work, '_open_tickets', new=AsyncMock()) as tickets:
            for text in ('/menu', '/lich homnay', '/hoi Quy định', '/doica', '/issue tao Lỗi | Nội dung',
                         '/nhac sau 1 phut | Nội dung', 'Giải thích Python là gì?',
                         '/dangky ' + self.directory.person.phone):
                message = parse_update(self.group_event(text))
                message = replace(message, text=text)
                self.assertEqual(GROUP_GUIDE, await self.adapter.work.handle(message))
            tickets.assert_not_awaited()
        router.classify.assert_not_awaited()
        self.assert_no_private_work()

    async def test_disabled_registration_preserves_existing_group_behavior(self):
        self.adapter.work.registration.enabled = False
        self.adapter.work.registration.requested = False
        event = self.group_event('/menu')
        self.archive.mark_notice(parse_update(event))
        await self.adapter._dispatch(event)
        self.assertIn('MENU BOT', self.adapter.client.send_text_chunk.await_args.args[1])
        self.assertNotEqual(GROUP_GUIDE, self.adapter.client.send_text_chunk.await_args.args[1])

    async def test_invalid_requested_registration_fails_closed_without_source_or_model(self):
        self.adapter.work.registration.enabled = False
        await self.adapter._dispatch(self.group_event())
        self.adapter.client.send_text_chunk.assert_awaited_once_with('g1', UNAVAILABLE)
        self.assert_no_private_work()

    async def test_revoke_during_group_generation_blocks_answer(self):
        record = self.register()
        event = self.group_event('Giải thích Python là gì?')
        self.archive.mark_notice(parse_update(event))

        async def revoke_then_answer(*args):
            self.adapter.work.registration.store.revoke('u1', record.member_id)
            return 'Must not leave server after revoke'

        self.adapter.managed_chat.reply.side_effect = revoke_then_answer
        await self.adapter._dispatch(event)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual([], self.archive.context(parse_update(event).session_key)['messages'])

    async def test_revoke_between_chunks_blocks_remaining_answer(self):
        record = self.register()
        event = self.group_event('Giải thích Python là gì?')
        self.archive.mark_notice(parse_update(event))
        self.adapter.managed_chat.reply.return_value = 'x' * 4500

        async def send_then_revoke(*args):
            self.adapter.work.registration.store.revoke('u1', record.member_id)
            return {'message_id': 'one-synthetic-chunk'}

        self.adapter.client.send_text_chunk.side_effect = send_then_revoke
        await self.adapter._dispatch(event)
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual([], self.archive.context(parse_update(event).session_key)['messages'])


if __name__ == '__main__':
    unittest.main()
