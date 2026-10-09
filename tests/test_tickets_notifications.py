"""Private peer outbox and independent short confirmations, no APIs."""
from datetime import datetime
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hermes_zalo.events import IncomingMessage
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.chat import TicketsChat
from hermes_zalo.tickets.service import DemoAssignments, TicketError, TicketService, TicketStore, digest, packed


class PeerNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'notices.sqlite3'
        self.now = datetime.fromisoformat('2030-10-01T09:00:00+07:00').timestamp()
        self.scope, self.seq = digest('synthetic-notification-bot'), 0
        self.open()
        for user in ('a', 'b', 'c'):
            self.ids.bind(self.scope, user, 'dm-' + user, 'demo-' + user)

    def open(self):
        self.store = TicketStore(self.path)
        self.provider = DemoAssignments(self.store, now=self.now)
        self.service = TicketService(self.store, self.provider, require_manager=False, clock=lambda: self.now)
        self.ids = IdentityStore(self.store)
        self.chat = TicketsChat(self.service, self.ids, bot_scope=self.scope)

    def restart(self):
        self.chat.close()
        self.store.close()
        self.open()

    def tearDown(self):
        self.chat.close()
        self.store.close()
        self.temp.cleanup()

    def message(self, user, text, *, chat=None, kind='PRIVATE', mid=None):
        self.seq += 1
        return IncomingMessage(chat or 'dm-' + user, user, mid or str(self.seq), kind, text)

    def say(self, user, text, **kwargs):
        return self.chat.handle(self.message(user, text, **kwargs))

    def body(self, user):
        scope = self.chat._scope(self.message(user, ''))
        row = self.store.db.execute('SELECT body FROM ticket_chat_dialogs WHERE scope=?', (scope,)).fetchone()
        return json.loads(row[0]) if row else None

    def draft(self, own='CA-11', other='CA-21'):
        self.say('a', '/doica')
        for item in (own, 'coach-b', other):
            choice = next(c for c in self.body('a')['choices'] if c['item']['id'] == item)
            self.say('a', choice['code'])
        self.say('a', 'Synthetic private reason')
        return '/doica gui ' + self.body('a')['code']

    def create(self, own='CA-11', other='CA-21'):
        result = self.say('a', self.draft(own, other))
        self.assertIn('Đã gửi yêu cầu', result)
        self.assertIn('Bot sẽ gửi thông báo riêng', result)
        return re.search(r'DC-[A-F0-9]+', result)[0]

    def claim(self):
        return self.chat.claim_notification({'a', 'b', 'c'})

    def notice(self, nid=None):
        if nid is None:
            return self.store.db.execute('SELECT * FROM ticket_peer_notifications ORDER BY id DESC LIMIT 1').fetchone()
        return self.store.db.execute('SELECT * FROM ticket_peer_notifications WHERE id=?', (nid,)).fetchone()

    def status(self, ticket):
        return self.store.db.execute('SELECT status FROM exchange_tickets WHERE id=?', (ticket,)).fetchone()[0]

    def code(self, notification):
        code = re.search(r'/dongy ([0-9]{4})\b', notification['text'])[1]
        self.assertIn('/tuchoi ' + code, notification['text'])
        return code

    def test_only_submitted_chat_requests_queue_once_and_accept_without_opening_review(self):
        command = self.draft()
        self.assertIsNone(self.claim())
        result = self.say('a', command, mid='same-submit')
        tid = re.search(r'DC-[A-F0-9]+', result)[0]
        self.say('a', command, mid='same-submit')
        self.say('a', command)
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM ticket_peer_notifications').fetchone()[0])
        notice = self.claim()
        self.assertEqual(('b', 'dm-b'), (notice['user_id'], notice['chat_id']))
        for text in ('An · Thành viên A', 'Bình · Thành viên B', 'Synthetic private reason', 'SAU KHI ĐỔI'):
            self.assertIn(text, notice['text'])
        self.assertLess(notice['text'].index('SAU KHI ĐỔI'), notice['text'].index('/dongy'))
        code = self.code(notice)
        self.chat.finish_notification(notice['id'], 'sent')
        self.assertIsNone(self.claim())
        self.assertIn('Đã đổi ca', self.say('b', '/dongy ' + code))
        self.assertEqual('completed', self.status(tid))
        self.say('b', '/tuchoi ' + code)
        self.assertEqual('completed', self.status(tid))
        self.assertEqual(1, self.notice()['used'])

    def test_legacy_service_create_and_existing_requests_are_not_backfilled(self):
        user = self.store.account('demo-a')
        preview = self.service.preview(user, 'CA-11', 'CA-21')
        self.service.create(user, preview['preview_token'], '', 'existing-request-001')
        self.restart()
        self.assertIsNone(self.claim())

    def test_reject_preserves_schedule_and_existing_recipient_draft(self):
        before = self.provider.list_assignments()
        self.say('b', '/doica')
        body = self.body('b')
        tid = self.create()
        notice = self.claim()
        self.assertEqual(body, self.body('b'))
        self.chat.finish_notification(notice['id'], 'sent')
        self.assertIn('Đã từ chối', self.say('b', '/tuchoi ' + self.code(notice)))
        self.assertEqual('rejected', self.status(tid))
        self.assertEqual(before, self.provider.list_assignments())
        self.assertEqual(body, self.body('b'))

    def test_wrong_actor_chat_bot_group_plain_number_and_action_do_not_approve(self):
        tid = self.create()
        notice = self.claim()
        code = self.code(notice)
        for user in ('a', 'c'):
            self.say(user, '/dongy ' + code)
        self.say('b', '/dongy ' + code, chat='wrong-dm')
        self.say('b', '/dongy ' + code, chat='group', kind='GROUP')
        self.say('b', code)
        self.say('b', '/duyet ' + code)
        other_scope = digest('different-bot')
        self.ids.bind(other_scope, 'b', 'dm-b', 'demo-b')
        other = TicketsChat(self.service, self.ids, bot_scope=other_scope)
        other.handle(self.message('b', '/dongy ' + code))
        self.assertEqual('pending_peer', self.status(tid))
        self.assertEqual(0, self.notice()['used'])
        self.say('b', '/dongy ' + code)
        self.assertEqual('completed', self.status(tid))

    def test_multiple_notices_target_their_own_request_and_leave_draft(self):
        first = self.create()
        second = self.create('CA-12', 'CA-22')
        n1, n2 = self.claim(), self.claim()
        self.assertNotEqual(self.code(n1), self.code(n2))
        self.say('b', '/doica')
        body = self.body('b')
        self.say('b', '/tuchoi ' + self.code(n2))
        self.assertEqual('pending_peer', self.status(first))
        self.assertEqual('rejected', self.status(second))
        self.say('b', '/dongy ' + self.code(n1))
        self.assertEqual('completed', self.status(first))
        self.assertEqual(body, self.body('b'))

    def test_allowlist_and_binding_changes_block_queued_notice(self):
        for mode in ('recipient-not-allowed', 'creator-not-allowed', 'recipient-rebound', 'creator-rebound', 'recipient-revoked'):
            with self.subTest(mode=mode):
                tid = self.create()
                allowed = {'a', 'b'}
                if mode.endswith('not-allowed'):
                    allowed.remove('b' if mode.startswith('recipient') else 'a')
                elif mode.endswith('revoked'):
                    self.ids.revoke(self.scope, 'b', 'dm-b')
                else:
                    who = 'b' if mode.startswith('recipient') else 'a'
                    self.ids.bind(self.scope, who, 'dm-' + who, 'demo-' + who)
                self.assertIsNone(self.chat.claim_notification(allowed))
                self.assertEqual('blocked', self.notice()['status'])
                row = self.service.detail(self.store.account('demo-a'), tid)
                self.service.action(self.store.account('demo-a'), tid, 'cancel', row['version'])
                if mode.endswith('revoked'):
                    self.ids.bind(self.scope, 'b', 'dm-b', 'demo-b')

    def test_both_bindings_and_target_are_checked_again_when_using_delivered_code(self):
        for mode in ('creator', 'recipient', 'target'):
            with self.subTest(mode=mode):
                tid = self.create()
                notice = self.claim()
                self.chat.finish_notification(notice['id'], 'sent')
                target = self.service.target
                if mode == 'target':
                    self.service.target = 'changed-target'
                else:
                    who = 'a' if mode == 'creator' else 'b'
                    self.ids.bind(self.scope, who, 'dm-' + who, 'demo-' + who)
                self.say('b', '/dongy ' + self.code(notice))
                self.assertEqual('pending_peer', self.status(tid))
                self.service.target = target
                row = self.service.detail(self.store.account('demo-a'), tid)
                self.service.action(self.store.account('demo-a'), tid, 'cancel', row['version'])

    def test_cancelled_expired_and_changed_target_are_not_notified(self):
        for mode in ('cancelled', 'expired', 'target'):
            with self.subTest(mode=mode):
                tid = self.create()
                target = self.service.target
                if mode == 'cancelled':
                    self.service.action(self.store.account('demo-a'), tid, 'cancel', 1)
                elif mode == 'expired':
                    with self.store.transaction() as db:
                        db.execute('UPDATE exchange_tickets SET expires=? WHERE id=?', (self.now, tid))
                else:
                    self.service.target = 'different-target'
                self.assertIsNone(self.claim())
                self.assertEqual('blocked', self.notice()['status'])
                self.service.target = target
                row = self.service.detail(self.store.account('demo-a'), tid)
                if 'cancel' in row['actions']:
                    self.service.action(self.store.account('demo-a'), tid, 'cancel', row['version'])

    def test_changed_source_is_blocked_before_delivery_and_cannot_apply_after_delivery(self):
        tid = self.create()
        original = self.provider.list_assignments()
        changed = dict(original[0], course='Updated synthetic lesson')
        with self.store.transaction() as db:
            db.execute('UPDATE demo_assignments SET body=? WHERE id=?', (packed(changed), changed['id']))
        self.assertIsNone(self.claim())
        self.assertEqual('blocked', self.notice()['status'])
        self.service.action(self.store.account('demo-a'), tid, 'cancel', 1)
        tid = self.create()
        notice = self.claim()
        with self.store.transaction() as db:
            db.execute('UPDATE demo_assignments SET body=? WHERE id=?', (packed(original[0]), original[0]['id']))
        self.say('b', '/dongy ' + self.code(notice))
        self.assertEqual('stale', self.status(tid))
        self.assertEqual('coach-a', self.service._source()['CA-11']['member_id'])

    def test_restart_preserves_queued_and_never_retries_ambiguous_delivery(self):
        tid = self.create()
        self.restart()
        notice = self.claim()
        expiry = self.notice()['expires']
        self.now += 100
        self.restart()
        self.assertEqual('uncertain', self.notice()['status'])
        self.assertEqual(expiry, self.notice()['expires'])
        self.assertIsNone(self.claim())
        self.say('b', '/dongy ' + self.code(notice))
        self.assertEqual('completed', self.status(tid))
        self.restart()
        self.assertIsNone(self.claim())
        self.say('b', '/dongy ' + self.code(notice))
        self.assertEqual('completed', self.status(tid))

    def test_exact_expiry_and_failed_delivery_require_new_review(self):
        for result in ('failed', 'blocked', 'expired'):
            with self.subTest(result=result):
                tid = self.create()
                notice = self.claim()
                self.assertEqual(self.now + 900, self.notice()['expires'])
                if result == 'expired':
                    self.chat.finish_notification(notice['id'], 'sent')
                    self.now += 900
                else:
                    self.chat.finish_notification(notice['id'], result)
                self.say('b', '/dongy ' + self.code(notice))
                self.assertEqual('pending_peer', self.status(tid))
                self.assertIsNone(self.claim())
                self.service.action(self.store.account('demo-a'), tid, 'cancel', 1)
        tid = self.create()
        notice = self.claim()
        self.now += 899
        self.say('b', '/dongy ' + self.code(notice))
        self.assertEqual('completed', self.status(tid))

    def test_enqueue_failure_rolls_back_ticket_and_claims(self):
        command = self.draft()
        def fail(db, ticket):
            raise RuntimeError('Synthetic outbox disk failure')
        with patch.object(self.chat.notifications, 'enqueue_callback', return_value=fail):
            response = self.say('a', command)
        self.assertNotIn('Đã gửi yêu cầu', response)
        for table in ('exchange_tickets', 'exchange_claims', 'ticket_peer_notifications'):
            self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0])

    def test_ticket_expiring_during_source_read_is_not_notified_or_given_a_code(self):
        tid = self.create()
        with self.store.transaction() as db:
            db.execute('UPDATE exchange_tickets SET expires=? WHERE id=?', (self.now + 1, tid))
        read_source = self.service._source
        def slow_read():
            rows = read_source()
            self.now += 2
            return rows
        with patch.object(self.service, '_source', side_effect=slow_read), \
                patch.object(self.chat, '_confirmation_code', wraps=self.chat._confirmation_code) as allocate:
            self.assertIsNone(self.claim())
            allocate.assert_not_called()
        self.assertEqual('expired', self.status(tid))
        self.assertEqual('blocked', self.notice()['status'])
        self.assertIsNone(self.notice()['code'])
        self.assertIsNone(self.claim())

    def test_read_error_and_code_exhaustion_fail_safely_without_leaking_or_retry(self):
        for mode in ('source', 'code'):
            with self.subTest(mode=mode):
                tid = self.create()
                if mode == 'source':
                    patched = patch.object(self.service, '_still_valid', side_effect=TicketError('Synthetic PRIVATE SECRET', 503))
                else:
                    patched = patch.object(self.chat, '_confirmation_code', side_effect=TicketError('Synthetic pool exhausted'))
                with patched:
                    self.assertIsNone(self.claim())
                self.assertIsNone(self.claim())
                self.assertIsNone(self.notice()['code'])
                self.assertEqual('pending_peer', self.status(tid))
                self.service.action(self.store.account('demo-a'), tid, 'cancel', 1)


if __name__ == '__main__':
    unittest.main()
