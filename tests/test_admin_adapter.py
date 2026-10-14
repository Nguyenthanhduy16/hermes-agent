"""Offline managed conversation integration; no Hermes/model/Zalo service."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import test_adapter as contract
from hermes_zalo.api import AuthenticationError, DeliveryUncertain
from hermes_zalo.conversations import ConversationStore
from hermes_zalo.events import parse_update


class AdminAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await contract.AdapterTests.asyncSetUp(self)
        self.locked = set()
        self.identities = {'u1': SimpleNamespace(member_id='member-a', chat_id='dm1'),
                           'u2': SimpleNamespace(member_id='member-b', chat_id='dm2')}

        async def handle(message):
            if message.text == '/menu':
                return 'MENU BOT: tra lịch, đổi ca.'
            if message.text.startswith('/dangky'):
                return 'Đã tiếp nhận đăng ký.'
            return None

        self.adapter.work = SimpleNamespace(
            registration=SimpleNamespace(identity=lambda user: self.identities.get(user)),
            registration_admission=lambda message: False,
            registration_blocked=lambda message: message.user_id in self.locked,
            member_admin_locked=lambda user, chat: user in self.locked,
            handle=AsyncMock(side_effect=handle),
            pilot=SimpleNamespace(record=lambda *args: None), close=AsyncMock())
        self.archive = ConversationStore(Path(self.temp.name) / 'conversations.sqlite3')
        self.adapter.conversations = self.archive
        self.ai = SimpleNamespace(reply=AsyncMock(return_value='Câu trả lời AI ngắn gọn.'))
        self.adapter.managed_chat = self.ai

    async def asyncTearDown(self):
        await contract.AdapterTests.asyncTearDown(self)

    def acknowledge_notice(self, event):
        self.archive.mark_notice(parse_update(event))

    async def test_commands_and_ai_are_archived_deduplicated_and_never_legacy_history(self):
        command = contract.update('menu', text='/menu')
        await self.adapter._dispatch(command)
        await self.adapter._dispatch(command)
        self.ai.reply.assert_not_awaited()
        self.assertEqual(3, self.archive.history()['total'])  # received, notice, command response
        sends = self.adapter.client.send_text_chunk.await_count
        await self.adapter._dispatch(contract.update('chat', text='Kể một chuyện vui đi'))
        self.ai.reply.assert_awaited_once()
        self.handler.assert_not_awaited()
        context = self.ai.reply.await_args.args[1]
        self.assertEqual([], context['payload']['messages'])  # /menu and its notice are not chat context
        self.assertEqual(sends + 1, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual(5, self.archive.history()['total'])
        await self.adapter._dispatch(contract.update('followup', text='Rồi sao nữa?'))
        prior = self.ai.reply.await_args.args[1]['payload']['messages']
        self.assertEqual(['Kể một chuyện vui đi', 'Câu trả lời AI ngắn gọn.'], [x['content'] for x in prior])
        self.assertNotIn('/menu', str(prior))

    async def test_unauthorized_users_groups_and_locked_members_leave_no_archive(self):
        await self.adapter._dispatch(contract.update('stranger', user='stranger'))
        await self.adapter._dispatch(contract.update('wronggroup', kind='GROUP', chat='g2'))
        self.locked.add('u1')
        await self.adapter._dispatch(contract.update('locked'))
        self.assertEqual(0, self.archive.history()['total'])
        self.assertEqual(0, self.archive.sessions()['total'])
        self.ai.reply.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_not_awaited()

    async def test_registration_phones_and_bot_secrets_are_redacted_in_history(self):
        event = contract.update('registration', text='/dangky +84 900 000 001')
        await self.adapter._dispatch(event)
        history = str(self.archive.history())
        self.assertNotIn('900 000 001', history)
        self.assertIn('đã ẩn thông tin đăng ký', history)
        self.ai.reply.assert_not_awaited()

    async def test_partial_ambiguous_delivery_records_each_attempt_once(self):
        event = contract.update('chunks')
        self.acknowledge_notice(event)
        self.ai.reply.return_value = 'a' * 4500
        self.adapter.client.send_text_chunk.side_effect = [{'message_id': 'one'}, DeliveryUncertain()]
        await self.adapter._dispatch(event)
        await self.adapter._dispatch(event)
        rows = self.archive.history()['items']
        outgoing = sorted((row for row in rows if row['direction'] == 'outgoing'), key=lambda row: row['id'])
        self.assertEqual(['sent', 'uncertain'], [row['status'] for row in outgoing])
        self.assertEqual([2000, 2000], [len(row['body']) for row in outgoing])
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual([], self.archive.context(parse_update(event).session_key)['messages'])

    async def test_definite_auth_failure_is_recorded_as_failed(self):
        event = contract.update('auth-failure')
        self.acknowledge_notice(event)
        self.adapter.client.send_text_chunk.side_effect = AuthenticationError()
        await self.adapter._dispatch(event)
        self.assertEqual('failed', self.archive.history()['items'][0]['status'])
        self.assertTrue(self.adapter.has_fatal_error)

    async def test_isolation_between_members_private_chat_and_group_context(self):
        dm = contract.update('dm-private', text='Sở thích riêng A.')
        group_a = contract.update('group-a', text='Chủ đề nhóm của A.', kind='GROUP', chat='g1')
        group_b = contract.update('group-b', text='Chủ đề nhóm của B.', kind='GROUP', chat='g1', user='u2')
        for event in (dm, group_a, group_b):
            self.acknowledge_notice(event)
            await self.adapter._dispatch(event)
        contexts = [call.args[1] for call in self.ai.reply.await_args_list]
        self.assertTrue(all(not context['payload']['messages'] for context in contexts))
        self.archive.save_memory({'content': 'Ghi nhớ riêng A.', 'scope': 'personal', 'member_id': 'member-a'}, 'admin')
        await self.adapter._dispatch(contract.update('group-next', text='Tiếp tục nhé', kind='GROUP', chat='g1'))
        group_context = self.ai.reply.await_args.args[1]['payload']
        self.assertIn('Chủ đề nhóm của A.', str(group_context))
        self.assertNotIn('Chủ đề nhóm của B.', str(group_context))
        self.assertNotIn('Sở thích riêng A.', str(group_context))
        self.assertNotIn('Ghi nhớ riêng A.', str(group_context))
        await self.adapter._dispatch(contract.update('dm-next', text='Tiếp tục nhé'))
        self.assertIn('Ghi nhớ riêng A.', str(self.ai.reply.await_args.args[1]['payload']))

    async def test_context_reset_and_stopped_memory_change_next_ai_input(self):
        event = contract.update('first', text='Chuyện cũ cần bỏ.')
        self.acknowledge_notice(event)
        await self.adapter._dispatch(event)
        memory = self.archive.save_memory({'content': 'Thông tin từng ghi nhớ.', 'scope': 'personal', 'member_id': 'member-a'}, 'admin')
        await self.adapter._dispatch(contract.update('second', text='Tiếp tục'))
        self.assertIn('Thông tin từng ghi nhớ.', str(self.ai.reply.await_args.args[1]['payload']))
        key = parse_update(event).session_key
        self.archive.reset_context(key, 'admin', self.archive.context(key)['revision'])
        self.archive.stop_memory(memory['id'], 'admin', memory['revision'])
        await self.adapter._dispatch(contract.update('afterreset', text='Bắt đầu lại'))
        new_context = self.ai.reply.await_args.args[1]['payload']
        self.assertEqual({'summary': '', 'messages': [], 'memories': []}, new_context)
        self.assertGreater(self.archive.history()['total'], 0)

    async def test_lock_during_generation_suppresses_answer_and_context_completion(self):
        event = contract.update('generation')
        self.acknowledge_notice(event)

        async def lock_then_reply(message, context):
            self.locked.add(message.user_id)
            return 'Thông tin không được gửi sau khi khóa.'

        self.ai.reply.side_effect = lock_then_reply
        await self.adapter._dispatch(event)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual(1, self.archive.history()['total'])
        self.assertEqual([], self.archive.context(parse_update(event).session_key)['messages'])

    async def test_lock_between_chunks_suppresses_remaining_chunks(self):
        event = contract.update('mid-send')
        self.acknowledge_notice(event)
        self.ai.reply.return_value = 'x' * 4500

        async def send_then_lock(chat, chunk):
            self.locked.add('u1')
            return {'message_id': 'first-only'}

        self.adapter.client.send_text_chunk.side_effect = send_then_lock
        await self.adapter._dispatch(event)
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)

    async def test_model_failure_does_not_block_commands_or_enter_context(self):
        event = contract.update('modelerror')
        self.acknowledge_notice(event)
        self.ai.reply.side_effect = contract.adapter_module.ManagedChatError('synthetic failure')
        await self.adapter._dispatch(event)
        self.assertIn('các lệnh công việc', self.adapter.client.send_text_chunk.await_args.args[1])
        self.assertEqual([], self.archive.context(parse_update(event).session_key)['messages'])
        await self.adapter._dispatch(contract.update('menu-aftererror', text='/menu'))
        self.assertIn('MENU BOT', self.adapter.client.send_text_chunk.await_args.args[1])

    async def test_model_rate_limit_shows_safe_guidance_without_archiving_as_chat(self):
        event = contract.update('rate-limit')
        self.acknowledge_notice(event)
        self.ai.reply.side_effect = contract.adapter_module.ManagedChatError(
            'private-provider-body', category='rate_limit')
        await self.adapter._dispatch(event)
        reply = self.adapter.client.send_text_chunk.await_args.args[1]
        self.assertIn('giới hạn tốc độ', reply)
        self.assertNotIn('private-provider-body', reply)
        self.assertEqual([], self.archive.context(parse_update(event).session_key)['messages'])


if __name__ == '__main__':
    unittest.main()
