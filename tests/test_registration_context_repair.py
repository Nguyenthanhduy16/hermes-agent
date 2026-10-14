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

    def test_reset_stops_personal_memories_and_preserves_history_shared_and_other_member_data(self):
        message, source = self.turn('Tin trước khi xóa đăng ký')
        personal = self.memory('Ghi nhớ riêng cũ', scope='personal', member_id='member-old', source_message_id=source['id'])
        session = self.memory('Ghi nhớ phiên cũ', scope='session', session_key=message.session_key)
        other = self.memory('Ghi nhớ người khác', scope='personal', member_id='member-other')
        shared = self.memory('Thông tin nhóm', scope='group', group_id='group', confirm_shared=True)
        history = self.store.history()['items']

        result = self.store.detach_registration_context('u', 'member-old', 'web:admin', stop_personal=True)

        self.assertEqual({'sessions': 1, 'stopped_memories': 2}, result)
        self.assertEqual(history, self.store.history()['items'])
        memories = {item['id']: item for item in self.store.list_memories()['items']}
        for stopped in (personal, session):
            self.assertFalse(memories[stopped['id']]['active'])
            self.assertEqual(stopped['revision'] + 1, memories[stopped['id']]['revision'])
            self.assertEqual(2, len(self.store.memory_versions(stopped['id'])['items']))
        for unchanged in (other, shared):
            self.assertEqual(unchanged, memories[unchanged['id']])
        self.assertEqual(1, len([item for item in self.store.audit()['items']
                                 if item['action'] == 'memory_stopped_registration_reset']))
        # Registering again with the same Workload phone preserves member_id,
        # but must not re-use its old summary, messages or personal memories.
        new, _ = self.turn('Tin sau đăng ký lại')
        context = self.store.context(new.session_key)
        self.assertEqual([], context['included_memory_ids'])
        self.assertEqual(['Tin sau đăng ký lại', 'Trả lời: Tin sau đăng ký lại'],
                         [item['body'] for item in context['messages']])

    def test_reset_stops_personal_memories_without_existing_session_and_is_idempotent_after_restart(self):
        memory = self.memory('Ghi nhớ đã nhập bởi Admin', scope='personal', member_id='member-old')
        self.assertEqual({'sessions': 0, 'stopped_memories': 1},
                         self.store.detach_registration_context('u', 'member-old', 'web:admin', stop_personal=True))
        before = (self.store.list_memories(), self.store.memory_versions(memory['id']), self.store.audit())
        self.store.close()
        self.store = ConversationStore(self.path)
        self.assertEqual({'sessions': 0, 'stopped_memories': 0},
                         self.store.detach_registration_context('u', 'member-old', 'web:admin', stop_personal=True))
        self.assertEqual(before, (self.store.list_memories(), self.store.memory_versions(memory['id']), self.store.audit()))

    def test_personal_reset_failure_rolls_back_already_detached_session_memory_and_history(self):
        message, _ = self.turn('Tin cũ')
        session = self.memory('Ghi nhớ phiên', scope='session', session_key=message.session_key)
        personal = self.memory('Ghi nhớ riêng', scope='personal', member_id='member-old')
        snapshot = lambda: (self.store.sessions(), self.store.list_memories(), self.store.history(), self.store.audit(),
                            self.store.memory_versions(session['id']), self.store.memory_versions(personal['id']))
        before = snapshot()
        audit = self.store._audit
        def fail_personal(actor, action, **kwargs):
            if action == 'memory_stopped_registration_reset':
                raise RuntimeError('synthetic personal reset failure')
            return audit(actor, action, **kwargs)
        with patch.object(self.store, '_audit', side_effect=fail_personal):
            with self.assertRaises(RuntimeError):
                self.store.detach_registration_context('u', 'member-old', 'web:admin', stop_personal=True)
        self.assertEqual(before, snapshot())

    def test_reset_rejects_ambiguous_personal_memory_flag_without_mutation(self):
        self.turn('Tin cũ')
        self.memory('Ghi nhớ riêng', scope='personal', member_id='member-old')
        before = (self.store.sessions(), self.store.list_memories(), self.store.audit())
        for flag in (None, 1, 'true', 'false'):
            with self.subTest(flag=flag), self.assertRaises(ConversationError):
                self.store.detach_registration_context('u', 'member-old', 'web:admin', stop_personal=flag)
        self.assertEqual(before, (self.store.sessions(), self.store.list_memories(), self.store.audit()))


if __name__ == '__main__':
    unittest.main()
