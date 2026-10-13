"""Conversation persistence/scope tests; only synthetic users and messages."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hermes_zalo.conversations import ConversationConflict, ConversationError, ConversationStore, redact
from hermes_zalo.events import IncomingMessage


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'history.sqlite3'
        self.now, self.seq = 2000000.0, 0
        self.open()

    def open(self):
        self.store = ConversationStore(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def message(self, text='Xin chào', *, user='a', chat=None, kind='PRIVATE', mid=None):
        self.seq += 1
        return IncomingMessage(chat or 'dm-' + user, user, mid or str(self.seq), kind, text)

    def turn(self, text='Tôi thích câu trả lời ngắn.', *, user='a', chat=None, kind='PRIVATE', member='member-a', answer='Mình đã hiểu.'):
        message = self.message(text, user=user, chat=chat, kind=kind)
        row = self.store.record_incoming(message, member)
        self.store.record_outgoing(message, answer, delivery_key='chunk-0')
        self.store.complete_turn(message)
        return message, row

    def memory(self, content='Trả lời ngắn.', **changes):
        return self.store.save_memory({'content': content, 'scope': 'personal', 'member_id': 'member-a', **changes}, 'admin')

    def test_only_admitted_messages_and_matching_replies_are_captured(self):
        rejected = self.message()
        self.assertIsNone(self.store.record_incoming(rejected, admitted=False))
        self.assertEqual(0, self.store.history()['total'])
        with self.assertRaises(ConversationError):
            self.store.record_outgoing(rejected, 'No', delivery_key='0')
        accepted = self.message()
        self.store.record_incoming(accepted, 'member-a')
        with self.assertRaises(ConversationError):
            self.store.record_outgoing(accepted, 'No stable delivery key')
        for status in ('sent', 'failed', 'uncertain'):
            self.store.record_outgoing(accepted, 'Reply ' + status, status=status, delivery_key=status)
        self.assertEqual(4, self.store.history()['total'])

    def test_redaction_registration_credentials_phones_and_immutable_dedup(self):
        source = self.message('/dangky +84 900 000 001')
        incoming = self.store.record_incoming(source, 'member-a')
        self.assertNotIn('900', incoming['body'])
        self.assertEqual('/dangky trangthai', redact('/dangky trangthai'))
        self.assertNotIn('0900000001', redact('Gọi 0900000001 nhé'))
        self.assertNotIn('0900', redact('Gọi 0\u200b900 000 001 nhé'))
        with patch.dict(os.environ, {'SYNTHETIC_API_KEY': 'unlabelled-secret-example'}):
            out = self.store.record_outgoing(source, 'unlabelled-secret-example API_KEY=supersecret\nphone 0900000001', delivery_key='0')
        self.assertNotIn('unlabelled-secret-example', out['body'])
        self.assertNotIn('supersecret', out['body'])
        duplicate = IncomingMessage(source.chat_id, source.user_id, source.message_id, source.chat_type, 'Changed body')
        self.assertEqual(incoming['id'], self.store.record_incoming(duplicate, 'member-a')['id'])
        self.assertEqual(incoming['body'], self.store.record_incoming(duplicate, 'member-a')['body'])
        self.assertEqual(out, self.store.record_outgoing(source, 'Changed reply', delivery_key='0'))
        self.assertEqual(2, self.store.history()['total'])

    def test_duplicate_group_event_cannot_change_sender_or_identity(self):
        message = self.message(chat='g', kind='GROUP', mid='same')
        self.store.record_incoming(message, 'member-a')
        impostor = self.message(user='b', chat='g', kind='GROUP', mid='same')
        with self.assertRaises(ConversationConflict):
            self.store.record_incoming(impostor, 'member-b')
        with self.assertRaises(ConversationConflict):
            self.store.record_incoming(self.message(chat='g', kind='GROUP'), 'member-b')
        self.assertEqual(1, self.store.sessions()['total'])

    def test_context_only_contains_completed_chat_and_successful_replies(self):
        chat, _ = self.turn()
        command = self.message('/menu')
        self.store.record_incoming(command, 'member-a')
        self.store.record_outgoing(command, 'Command output', delivery_key='0')
        self.store.complete_turn(command, is_chat=True)
        unfinished = self.message('Câu đang xử lý')
        self.store.record_incoming(unfinished, 'member-a')
        self.store.record_outgoing(unfinished, 'Failed answer', status='failed', delivery_key='0')
        self.store.complete_turn(unfinished)
        current = self.message('Câu hỏi mới')
        self.store.record_incoming(current, 'member-a')
        ctx = self.store.prepare_context(current, 'member-a')
        bodies = [x['content'] for x in ctx['payload']['messages']]
        self.assertIn(chat.text, bodies)
        self.assertNotIn('/menu', bodies)
        self.assertNotIn('Command output', bodies)
        self.assertNotIn('Failed answer', bodies)
        self.assertNotIn(current.text, bodies)
        self.assertNotIn('member-a', str(ctx['payload']))
        self.assertEqual(ctx['revision'], self.store.context(current.session_key)['last_prepared']['revision'])

    def test_same_name_people_groups_and_private_conversations_are_isolated(self):
        private, private_row = self.turn(text='Thông tin riêng A')
        group_a, _ = self.turn(text='Thông tin nhóm A', chat='g', kind='GROUP')
        group_b, _ = self.turn(text='Thông tin nhóm B', user='b', member='member-b', chat='g', kind='GROUP')
        other, _ = self.turn(text='Thông tin riêng B', user='b', member='member-b')
        self.memory('Ghi nhớ riêng A', source_message_id=private_row['id'])
        self.memory('Ghi nhớ riêng B', member_id='member-b')
        for message, member, allowed, forbidden in ((private, 'member-a', 'Thông tin riêng A', 'Thông tin nhóm'),
                (group_a, 'member-a', 'Thông tin nhóm A', 'Thông tin riêng'),
                (group_b, 'member-b', 'Thông tin nhóm B', 'Thông tin nhóm A'),
                (other, 'member-b', 'Thông tin riêng B', 'Thông tin riêng A')):
            context = self.store.context(message.session_key)
            combined = str(context)
            self.assertIn(allowed, combined)
            self.assertNotIn(forbidden, combined)
            if message.chat_type == 'GROUP':
                self.assertFalse(context['memories'])
            else:
                self.assertEqual(member, context['memories'][0]['member_id'])
        with self.assertRaises(ConversationConflict):
            self.store.prepare_context(private, 'member-b')

    def test_group_sharing_requires_explicit_confirmation_and_correct_group(self):
        private, row = self.turn()
        group, _ = self.turn(chat='g', kind='GROUP')
        group_other, _ = self.turn(chat='g-other', kind='GROUP')
        data = {'content': 'Hướng dẫn đã duyệt', 'scope': 'group', 'group_id': 'g', 'member_id': 'member-a', 'source_message_id': row['id']}
        with self.assertRaises(ConversationError):
            self.store.save_memory(data, 'admin')
        saved = self.store.save_memory({**data, 'confirm_share': True}, 'admin')
        self.assertIn(saved['id'], self.store.context(group.session_key)['included_memory_ids'])
        self.assertNotIn(saved['id'], self.store.context(group_other.session_key)['included_memory_ids'])
        self.assertNotIn(saved['id'], self.store.context(private.session_key)['included_memory_ids'])
        self.assertTrue(self.store.audit()['items'][0]['details']['shared_confirmed'])

    def test_group_session_scope_cannot_bypass_explicit_sharing_confirmation(self):
        group, _ = self.turn(chat='g', kind='GROUP')
        private, _ = self.turn()
        memory = self.memory('Ghi nhớ cá nhân trước đây')
        payload = {'id': memory['id'], 'member_id': 'member-a', 'scope': 'session',
                   'session_key': group.session_key, 'content': 'Được chọn cho phiên nhóm'}
        with self.assertRaises(ConversationError):
            self.store.save_memory(payload, 'admin', memory['revision'])
        self.assertIn(memory['id'], self.store.context(private.session_key)['included_memory_ids'])
        updated = self.store.save_memory({**payload, 'confirm_share': True}, 'admin', memory['revision'])
        self.assertIn(updated['id'], self.store.context(group.session_key)['included_memory_ids'])
        self.assertNotIn(updated['id'], self.store.context(private.session_key)['included_memory_ids'])
        self.assertTrue(self.store.audit()['items'][0]['details']['shared_confirmed'])

    def test_memory_source_scope_and_session_identity_are_checked(self):
        a, source = self.turn()
        b, _ = self.turn(user='b', member='member-b')
        bad = [dict(member_id='member-b', source_message_id=source['id']),
               dict(scope='session', session_key=b.session_key, source_message_id=source['id'], member_id='member-b'),
               dict(scope='session', session_key=b.session_key),
               dict(source_message_id=999999),
               dict(scope='personal', member_id=None),
               dict(scope='admin'), dict(scope='group', group_id='g', confirm_shared=False)]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ConversationError):
                self.memory(**change)
        valid = self.memory(scope='session', session_key=a.session_key, source_message_id=source['id'])
        self.assertIn(valid['id'], self.store.context(a.session_key)['included_memory_ids'])
        self.assertNotIn(valid['id'], self.store.context(b.session_key)['included_memory_ids'])

    def test_reset_and_rebuild_are_effective_and_require_current_revision(self):
        message, _ = self.turn()
        before = self.store.context(message.session_key)
        built = self.store.rebuild_context(message.session_key, 'admin', before['revision'])
        self.assertIn(message.text, built['summary'])
        with self.assertRaises(ConversationConflict):
            self.store.reset_context(message.session_key, 'admin', before['revision'])
        memory = self.memory()
        cleared = self.store.reset_context(message.session_key, 'admin', built['revision'])
        self.assertEqual([], cleared['messages'])
        self.assertEqual('', cleared['summary'])
        self.assertIn(memory['id'], cleared['included_memory_ids'])
        self.assertEqual(2, self.store.history()['total'])
        rebuilt = self.store.rebuild_context(message.session_key, 'admin', cleared['revision'])
        self.assertEqual('', rebuilt['summary'])
        new, _ = self.turn(text='Chỉ tin mới sau khi xóa.')
        self.assertEqual([new.text, 'Mình đã hiểu.'], [m['body'] for m in self.store.context(new.session_key)['messages']])

    def test_new_message_invalidates_stale_admin_context_revision(self):
        message, _ = self.turn()
        revision = self.store.context(message.session_key)['revision']
        self.turn('Tin vừa tới')
        with self.assertRaises(ConversationConflict):
            self.store.reset_context(message.session_key, 'admin', revision)

    def test_reply_finishing_after_reset_cannot_resurrect_cleared_turn(self):
        message = self.message('Câu đang chờ AI')
        self.store.record_incoming(message, 'member-a')
        self.store.reset_context(message.session_key, 'admin', self.store.context(message.session_key)['revision'])
        self.store.record_outgoing(message, 'Câu trả lời cũ đến muộn', delivery_key='0')
        self.store.complete_turn(message)
        context = self.store.context(message.session_key)
        self.assertEqual([], context['messages'])
        rebuilt = self.store.rebuild_context(message.session_key, 'admin', context['revision'])
        self.assertEqual('', rebuilt['summary'])

    def test_multiline_memory_content_is_supported_and_redacted(self):
        memory = self.memory('Dòng thứ nhất\nDòng thứ hai API_KEY=synthetic-private-key')
        self.assertIn('\n', memory['content'])
        self.assertNotIn('synthetic-private-key', memory['content'])

    def test_memory_edits_stop_immediately_and_revision_history_has_text(self):
        message, _ = self.turn()
        saved = self.memory()
        data = {'id': saved['id'], 'content': 'Trả lời bằng gạch đầu dòng.', 'scope': 'personal', 'member_id': 'member-a'}
        edited = self.store.save_memory(data, 'admin', expected_revision=saved['revision'])
        with self.assertRaises(ConversationConflict):
            self.store.save_memory(data, 'admin', expected_revision=saved['revision'])
        self.assertEqual(edited['content'], self.store.context(message.session_key)['memories'][0]['content'])
        stopped = self.store.stop_memory(edited['id'], 'admin', edited['revision'])
        self.assertFalse(self.store.context(message.session_key)['memories'])
        versions = self.store.memory_versions(stopped['id'])['items']
        self.assertEqual(3, len(versions))
        self.assertEqual('Trả lời ngắn.', versions[-1]['snapshot']['content'])
        self.assertEqual(edited['content'], versions[0]['snapshot']['content'])
        self.assertNotIn(edited['content'], str(self.store.audit()))

    def test_source_expiration_purges_history_summaries_and_source_backed_content(self):
        message, source = self.turn(text='Thông tin cần hết hạn')
        memory = self.memory('Tóm tắt từ nguồn', source_message_id=source['id'])
        independent = self.memory('Ghi chú độc lập')
        self.store.rebuild_context(message.session_key, 'admin', self.store.context(message.session_key)['revision'])
        self.now += 31 * 86400
        self.assertEqual(2, self.store.maintenance()['deleted_messages'])
        context = self.store.context(message.session_key)
        self.assertEqual('', context['summary'])
        self.assertEqual([], context['messages'])
        self.assertEqual([independent['id']], context['included_memory_ids'])
        rows = {m['id']: m for m in self.store.list_memories()['items']}
        self.assertEqual('', rows[memory['id']]['content'])
        self.assertEqual(1, rows[memory['id']]['source_expired'])
        self.assertEqual(0, self.store.memory_versions(memory['id'])['total'])
        self.assertEqual(0, self.store.history()['total'])
        with self.assertRaises(ConversationError):
            self.store.save_memory({'id': memory['id'], 'content': 'Restored', 'scope': 'personal', 'member_id': 'member-a'}, 'admin', expected_revision=rows[memory['id']]['revision'])

    def test_memory_expiry_and_bounded_context(self):
        message, _ = self.turn()
        expiring = self.memory(expires=self.now + 5)
        self.assertIn(expiring['id'], self.store.context(message.session_key)['included_memory_ids'])
        self.now += 6
        self.assertNotIn(expiring['id'], self.store.context(message.session_key)['included_memory_ids'])
        for index in range(15):
            self.turn(str(index))
        self.assertEqual(12, len(self.store.context(message.session_key)['messages']))

    def test_large_context_has_serialized_budget_and_history_is_unchanged(self):
        body = ('Nội dung dài "\\\n' * 1000)[:12000]
        sources = []
        for index in range(8):
            old, row = self.turn(text=body, answer=body)
            sources.append(row['id'])
        memory_ids = []
        for index in range(21):
            self.now += 1
            memory_ids.append(self.memory(('Ghi nhớ %s ' % index) + ('"\\\n' * 600))['id'])
        key = old.session_key
        self.store.rebuild_context(key, 'admin', self.store.context(key)['revision'])
        current = self.message('Câu mới')
        context = self.store.prepare_context(current, 'member-a')
        preview = self.store.context(key)
        self.assertLessEqual(len(json.dumps(context['payload'], ensure_ascii=False)), 40000)
        self.assertTrue(context['truncated'])
        self.assertTrue(all(m['truncated'] for m in context['messages']))
        self.assertEqual(context['messages'], preview['messages'])
        self.assertEqual(context['memories'], preview['memories'])
        self.assertEqual(context['included_memory_ids'], [m['id'] for m in context['memories']])
        self.assertIn(memory_ids[-1], context['included_memory_ids'])
        self.assertNotIn(memory_ids[0], context['included_memory_ids'])
        self.assertEqual(body, self.store.history(message_id=sources[0])['items'][0]['body'])
        self.assertEqual(12000, context['messages'][0]['original_length'])
        self.assertIn('đã rút gọn', context['payload']['messages'][0]['content'])

    def test_summary_budget_tracks_only_sources_actually_selected(self):
        # Quotes/backslashes survive redaction and expand when JSON-encoded.
        # Unsupported controls are removed before storage, so they cannot be
        # used as a meaningful fixture for the summary's serialization budget.
        body = '"\\' * 500
        for index in range(8):
            old, _ = self.turn(text=str(index) + body, answer=str(index) + body)
        recent = sorted(self.store.history(session_key=old.session_key, limit=12)['items'], key=lambda item: item['id'])
        def summary_line(item):
            return ('Thành viên: ' if item['direction'] == 'incoming' else 'Bot: ') + item['body'][:240]
        unbounded = '\n'.join(summary_line(item) for item in recent)
        self.assertGreater(len(json.dumps(unbounded, ensure_ascii=False)), 3000)
        rebuilt = self.store.rebuild_context(old.session_key, 'admin', self.store.context(old.session_key)['revision'])
        self.assertLessEqual(len(json.dumps(rebuilt['summary'], ensure_ascii=False)), 3000)
        row = self.store.db.execute('SELECT summary_ids FROM conversation_sessions WHERE session_key=?', (old.session_key,)).fetchone()
        ids = json.loads(row['summary_ids'])
        self.assertGreater(len(ids), 0)
        self.assertLess(len(ids), 12)
        self.assertEqual('\n'.join(summary_line(item) for item in recent if item['id'] in ids), rebuilt['summary'])
        self.assertTrue(set(ids) <= set(rebuilt['included_message_ids']))

    def test_pagination_filters_aliases_and_notice_survive_restart(self):
        a, _ = self.turn()
        b, _ = self.turn(user='b', member='member-b', chat='g', kind='GROUP')
        self.assertTrue(self.store.notice_needed(a))
        self.store.mark_notice(a)
        self.store.close()
        self.open()
        self.assertFalse(self.store.notice_needed(a))
        self.assertTrue(self.store.notice_needed(b))
        self.assertEqual(2, self.store.history(member_id='member-a')['total'])
        group = self.store.history(chat_id='g', limit=1)
        self.assertEqual(2, group['total'])
        self.assertEqual(1, len(group['items']))
        self.assertEqual('GROUP', group['items'][0]['chat_type'])
        self.assertEqual(group['items'][0]['body'], group['items'][0]['content'])
        self.assertEqual(0, self.store.history(start=self.now + 1)['total'])
        self.assertEqual(2, self.store.sessions(member_id='member-a')['items'][0]['message_count'])
        self.assertEqual(1, len(self.store.history(page=2, limit=1)['items']))

    def test_source_message_id_lookup_obeys_other_scope_filters(self):
        _, source = self.turn()
        self.turn(user='b', member='member-b')
        selected = self.store.history(message_id=source['id'])
        self.assertEqual(1, selected['total'])
        self.assertEqual(source['id'], selected['items'][0]['id'])
        self.assertEqual(0, self.store.history(message_id=source['id'], member_id='member-b')['total'])
        self.assertEqual(0, self.store.history(message_id=999999)['total'])
        for invalid in (True, '1', '1 OR 1=1', 0, -1):
            with self.subTest(invalid=invalid), self.assertRaises(ConversationError):
                self.store.history(message_id=invalid)

    def test_concurrent_two_connections_dedup_and_optimistic_updates(self):
        other = ConversationStore(self.path, clock=lambda: self.now)
        try:
            message = self.message()
            with ThreadPoolExecutor(max_workers=2) as pool:
                rows = list(pool.map(lambda s: s.record_incoming(message, 'member-a'), [self.store, other]))
            self.assertEqual(rows[0]['id'], rows[1]['id'])
            memory = self.memory()
            def update(store):
                try:
                    return store.stop_memory(memory['id'], 'admin', memory['revision'])['revision']
                except ConversationConflict:
                    return 'conflict'
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(update, [self.store, other]))
            self.assertCountEqual([2, 'conflict'], outcomes)
        finally:
            other.close()

    def test_invalid_payloads_are_rejected(self):
        for page, limit in ((0, 2), (1, 101), (True, 20), (1, '20')):
            with self.assertRaises(ConversationError):
                self.store.history(page=page, limit=limit)
        for change in ({'active': 'true'}, {'expires': float('nan')}, {'expires': self.now}, {'content': ''}, {'source_message_id': True}, {'unknown': 'x'}):
            with self.subTest(change=change), self.assertRaises(ConversationError):
                self.memory(**change)
        with self.assertRaises(ConversationError):
            self.store.context('arbitrary session')
        with self.assertRaises(ConversationError):
            self.store.history(start=self.now, end=self.now - 1)
        with self.assertRaises(ConversationError):
            self.store.save_memory({'content': 'x', 'member_id': 'member-a'}, '')


if __name__ == '__main__':
    unittest.main()
