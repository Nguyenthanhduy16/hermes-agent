"""Identity correction isolation; synthetic stores only, no model or Zalo."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hermes_zalo.conversations import ConversationConflict, ConversationError, ConversationStore
from hermes_zalo.events import IncomingMessage


class RegistrationContextRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'conversations.sqlite3'
        self.store = ConversationStore(self.path)
        self.sequence = 0

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def turn(self, text, member='member-old', user='u', chat='dm', kind='PRIVATE'):
        self.sequence += 1
        message = IncomingMessage(chat, user, str(self.sequence), kind, text)
        row = self.store.record_incoming(message, member)
        self.store.record_outgoing(message, 'Trả lời: ' + text, delivery_key='reply')
        self.store.complete_turn(message)
        return message, row

    def memory(self, text, **scope):
        return self.store.save_memory(dict(content=text, **scope), 'web:admin')

    def test_detach_clears_private_and_group_context_preserving_history_and_other_users(self):
        dm, source = self.turn('Nội dung riêng của danh tính cũ')
        group, _ = self.turn('Nội dung nhóm của danh tính cũ', chat='group', kind='GROUP')
        other, _ = self.turn('Nội dung của người khác', user='other', member='member-other', chat='group', kind='GROUP')
        old_memory = self.memory('Ghi nhớ cá nhân cũ', scope='personal', member_id='member-old', source_message_id=source['id'])
        new_memory = self.memory('Ghi nhớ đúng thành viên mới', scope='personal', member_id='member-new')
        session_memory = self.memory('Ghi nhớ DM cũ', scope='session', session_key=dm.session_key, member_id='member-old')
        anonymous_memory = self.memory('Ghi nhớ phiên không gắn tên', scope='session', session_key=dm.session_key)
        group_session_memory = self.memory('Ghi nhớ phiên nhóm', scope='session', session_key=group.session_key,
                                           member_id='member-old', confirm_shared=True)
        shared_memory = self.memory('Thông tin dùng chung đã duyệt', scope='group', group_id='group', confirm_shared=True)
        before = self.store.context(dm.session_key)
        self.store.rebuild_context(dm.session_key, 'web:admin', before['revision'])
        self.store.prepare_context(dm, 'member-old')
        history = self.store.history()['items']
        other_context = self.store.context(other.session_key)

        result = self.store.detach_registration_context('u', 'member-old', 'web:admin')

        self.assertEqual({'sessions': 2, 'stopped_memories': 3}, result)
        self.assertEqual(history, self.store.history()['items'])
        self.assertEqual(other_context, self.store.context(other.session_key))
        for message in (dm, group):
            session = self.store.db.execute('SELECT * FROM conversation_sessions WHERE session_key=?', (message.session_key,)).fetchone()
            self.assertIsNone(session['member_id'])
            self.assertEqual('', session['summary'])
            context = self.store.context(message.session_key)
            self.assertFalse(context['messages'])
            self.assertIsNone(context['last_prepared'])
        memories = {row['id']: row for row in self.store.list_memories()['items']}
        for item in (session_memory, anonymous_memory, group_session_memory):
            self.assertFalse(memories[item['id']]['active'])
            self.assertEqual(item['member_id'], memories[item['id']]['member_id'])
        for item in (old_memory, new_memory, shared_memory):
            self.assertTrue(memories[item['id']]['active'])
            self.assertEqual(item['member_id'], memories[item['id']]['member_id'])

        new, _ = self.turn('Tin mới sau sửa', member='member-new')
        context = self.store.context(new.session_key)
        self.assertEqual([new_memory['id']], context['included_memory_ids'])
        self.assertEqual(['Tin mới sau sửa', 'Trả lời: Tin mới sau sửa'], [row['body'] for row in context['messages']])
        with self.assertRaises(ConversationConflict):
            self.store.prepare_context(new, 'member-old')

    def test_retry_is_idempotent_including_after_restart(self):
        message, _ = self.turn('Nội dung cũ')
        self.memory('Ghi nhớ phiên', scope='session', session_key=message.session_key)
        self.store.detach_registration_context('u', 'member-old', 'web:admin')
        audit = self.store.audit()['items']
        revision = self.store.context(message.session_key)['revision']
        self.store.close()
        self.store = ConversationStore(self.path)
        self.assertEqual({'sessions': 0, 'stopped_memories': 0},
                         self.store.detach_registration_context('u', 'member-old', 'web:admin'))
        self.assertEqual(audit, self.store.audit()['items'])
        self.assertEqual(revision, self.store.context(message.session_key)['revision'])

    def test_failed_registration_commit_cannot_grant_replacement_context(self):
        message, _ = self.turn('Tin trước khi sửa')
        self.memory('Riêng người cũ', scope='personal', member_id='member-old')
        self.memory('Riêng người mới', scope='personal', member_id='member-new')
        self.store.detach_registration_context('u', 'member-old', 'web:admin')
        self.assertFalse(self.store.context(message.session_key)['memories'])
        # Simulate registration write failure: the old approved record survives.
        retry, _ = self.turn('Tiếp tục danh tính cũ')
        context = self.store.context(retry.session_key)
        self.assertEqual(['Riêng người cũ'], [memory['content'] for memory in context['memories']])
        self.assertNotIn('Tin trước khi sửa', str(context['payload']) if 'payload' in context else str(context['messages']))

    def test_unrelated_identity_aborts_every_session_without_partial_changes(self):
        message, _ = self.turn('Tin cũ')
        self.turn('Danh tính khác trên phiên nhóm', member='member-third', chat='group', kind='GROUP')
        self.memory('Ghi nhớ cũ', scope='session', session_key=message.session_key)
        before = (self.store.sessions(), self.store.list_memories(), self.store.audit())
        with self.assertRaises(ConversationConflict):
            self.store.detach_registration_context('u', 'member-old', 'web:admin')
        self.assertEqual(before, (self.store.sessions(), self.store.list_memories(), self.store.audit()))

    def test_write_failure_rolls_back_session_memory_versions_and_audit(self):
        message, _ = self.turn('Tin cũ')
        memory = self.memory('Ghi nhớ cũ', scope='session', session_key=message.session_key)
        before = (self.store.sessions(), self.store.list_memories(), self.store.memory_versions(memory['id']), self.store.audit())
        with patch.object(self.store, '_audit', side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(RuntimeError):
                self.store.detach_registration_context('u', 'member-old', 'web:admin')
        self.assertEqual(before, (self.store.sessions(), self.store.list_memories(), self.store.memory_versions(memory['id']), self.store.audit()))

    def test_unapproved_context_is_detached_and_input_is_validated(self):
        message, _ = self.turn('Tin trước đăng ký', member=None)
        self.memory('Ghi nhớ trước đăng ký', scope='session', session_key=message.session_key)
        result = self.store.detach_registration_context('u', 'member-old', 'web:admin')
        self.assertEqual({'sessions': 1, 'stopped_memories': 1}, result)
        for args in (('', 'member-old', 'web:admin'), ('u:invalid', 'member-old', 'web:admin'),
                     ('u', None, 'web:admin'), ('u', 'member-old', '')):
            with self.subTest(args=args), self.assertRaises(ConversationError):
                self.store.detach_registration_context(*args)


if __name__ == '__main__':
    unittest.main()
