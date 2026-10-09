"""Numeric confirmations remain scoped, durable and bounded; no external APIs."""
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
from hermes_zalo.tickets.service import CONFIRM_TTL_SECONDS, DemoAssignments, TicketError, TicketService, TicketStore, digest, packed


class ShortConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'short-actions.sqlite3'
        self.now = datetime.fromisoformat('2030-10-01T09:00:00+07:00').timestamp()
        self.scope = digest('synthetic-short-code-bot')
        self.sequence = 0
        self.open()
        for user in ('a', 'b', 'c'):
            self.identities.bind(self.scope, user, 'dm-' + user, 'demo-' + user)

    def open(self):
        self.store = TicketStore(self.path)
        self.provider = DemoAssignments(self.store, now=self.now)
        self.service = TicketService(self.store, self.provider, require_manager=False, clock=lambda: self.now)
        self.identities = IdentityStore(self.store)
        self.chat = TicketsChat(self.service, self.identities, bot_scope=self.scope)

    def tearDown(self):
        self.chat.close()
        self.provider.close()
        self.store.close()
        self.temp.cleanup()

    def restart(self):
        self.chat.close()
        self.provider.close()
        self.store.close()
        self.open()

    def message(self, user, text, *, chat_id=None, kind='PRIVATE', mid=None):
        self.sequence += 1
        return IncomingMessage(chat_id or 'dm-' + user, user, mid or str(self.sequence), kind, text)

    def say(self, user, text, **kwargs):
        return self.chat.handle(self.message(user, text, **kwargs))

    def body_row(self, user):
        scope = self.chat._scope(IncomingMessage('dm-' + user, user, 'inspection', 'PRIVATE', ''))
        return self.store.db.execute('SELECT * FROM ticket_chat_dialogs WHERE scope=?', (scope,)).fetchone()

    def body(self, user):
        row = self.body_row(user)
        return json.loads(row['body']) if row else None

    def pick(self, user, reply, identity):
        choice = next(choice for choice in self.body(user)['choices'] if choice['item']['id'] == identity)
        self.assertRegex(reply, rf"(?m)^{choice['code']}\. ")
        return self.say(user, choice['code'])

    def draft(self):
        reply = self.say('a', '/doica')
        reply = self.pick('a', reply, 'CA-11')
        reply = self.pick('a', reply, 'coach-b')
        reply = self.pick('a', reply, 'CA-21')
        self.assertIn('lý do', reply)
        reply = self.say('a', 'Synthetic reason')
        code = self.body('a')['code']
        self.assertRegex(code, r'^1[0-9]{3}$')
        self.assertIn('/doica gui ' + code, reply)
        return code

    def create(self, own='CA-11', other='CA-21'):
        self.sequence += 1
        preview = self.service.preview(self.store.account('demo-a'), own, other)
        return self.service.create(self.store.account('demo-a'), preview['preview_token'], '',
                                   'synthetic-request-' + str(self.sequence).zfill(6))['id']

    def review(self, user, ticket):
        reply = self.say(user, '/yeucau ' + ticket)
        code = self.body(user)['code']
        self.assertRegex(code, r'^1[0-9]{3}$')
        return code, reply

    def status(self, ticket):
        return self.store.db.execute('SELECT status FROM exchange_tickets WHERE id=?', (ticket,)).fetchone()[0]

    def test_short_send_and_accept_after_ten_minutes_keep_ticket_deadline_at_48_hours(self):
        code = self.draft()
        self.assertEqual(900, CONFIRM_TTL_SECONDS)
        self.assertEqual(self.now + 900, self.body_row('a')['expires'])
        self.now += 601
        sent = self.say('a', '/doica gui ' + code)
        self.assertIn('Đã gửi yêu cầu', sent)
        ticket = re.search(r'DC-[A-F0-9]+', sent)[0]
        row = self.store.db.execute('SELECT expires FROM exchange_tickets WHERE id=?', (ticket,)).fetchone()
        self.assertGreater(row['expires'] - self.now, 46 * 3600)
        self.assertLessEqual(row['expires'] - self.now, 48 * 3600)
        code, reply = self.review('b', ticket)
        self.assertIn('/dongy ' + code, reply)
        self.now += 899
        self.assertIn('Đã đổi ca', self.say('b', '/dongy ' + code))
        self.assertEqual('completed', self.status(ticket))

    def test_exact_expiry_rejects_input_preview_and_action_without_renewing(self):
        self.say('a', '/doica')
        self.now += 900
        self.assertIn('hết hạn', self.say('a', '1'))
        code = self.draft()
        self.now += 900
        self.say('a', '/doica gui ' + code)
        self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM exchange_tickets').fetchone()[0])
        ticket = self.create()
        code, _ = self.review('b', ticket)
        expires = self.body_row('b')['expires']
        self.say('b', '/tickets')
        self.assertEqual(expires, self.body_row('b')['expires'])
        self.now += 900
        self.say('b', '/dongy ' + code)
        self.assertEqual('pending_peer', self.status(ticket))
        new_code, _ = self.review('b', ticket)
        self.assertNotEqual(code, new_code)

    def test_wrong_user_bot_chat_group_or_plain_number_cannot_execute_short_code(self):
        ticket = self.create()
        code, _ = self.review('b', ticket)
        for user in ('a', 'c'):
            self.say(user, '/dongy ' + code)
        self.say('b', '/dongy ' + code, chat_id='wrong-dm')
        self.say('b', '/dongy ' + code, kind='GROUP', chat_id='group')
        self.say('b', code)
        self.say('b', '/duyet ' + code)
        other_scope = digest('another-synthetic-bot')
        self.identities.bind(other_scope, 'b', 'dm-b', 'demo-b')
        other = TicketsChat(self.service, self.identities, bot_scope=other_scope)
        other.handle(self.message('b', '/dongy ' + code))
        self.assertEqual('pending_peer', self.status(ticket))
        self.assertIn('Đã đổi ca', self.say('b', '/dongy ' + code))

    def test_current_review_selects_one_of_multiple_requests_and_old_code_is_rejected(self):
        first = self.create()
        second = self.create('CA-12', 'CA-22')
        first_code, _ = self.review('b', first)
        second_code, _ = self.review('b', second)
        self.assertNotEqual(first_code, second_code)
        self.say('b', '/dongy ' + first_code)
        self.assertEqual('pending_peer', self.status(first))
        self.assertEqual('pending_peer', self.status(second))
        self.assertIn('Đã đổi ca', self.say('b', '/dongy ' + second_code))
        self.assertEqual('pending_peer', self.status(first))

    def test_waiting_list_requires_choice_for_many_and_opens_only_single_request(self):
        first = self.create()
        second = self.create('CA-12', 'CA-22')
        old_code, _ = self.review('b', first)
        reply = self.say('b', '/choxacnhan')
        self.assertNotIn('/dongy ', reply)
        self.assertIsNone(self.body('b'))
        self.say('b', '/dongy ' + old_code)
        self.assertEqual('pending_peer', self.status(first))
        second_code, _ = self.review('b', second)
        self.say('b', '/dongy ' + second_code)
        reply = self.say('b', '/choxacnhan')
        current = self.body('b')
        self.assertEqual(first, current['ticket'])
        self.assertNotEqual(old_code, current['code'])
        self.assertIn('/dongy ' + current['code'], reply)
        self.assertEqual('pending_peer', self.status(first))
        self.assertEqual('completed', self.status(second))

    def test_rebinding_target_change_and_stale_ticket_version_reject_old_code(self):
        ticket = self.create()
        code, _ = self.review('b', ticket)
        self.identities.bind(self.scope, 'b', 'dm-b', 'demo-b')
        self.say('b', '/dongy ' + code)
        self.assertEqual('pending_peer', self.status(ticket))
        code, _ = self.review('b', ticket)
        target = self.service.target
        self.service.target = 'a-different-target'
        self.say('b', '/dongy ' + code)
        self.service.target = target
        self.assertEqual('pending_peer', self.status(ticket))
        code, _ = self.review('b', ticket)
        detail = self.service.detail(self.store.account('demo-a'), ticket)
        self.service.action(self.store.account('demo-a'), ticket, 'cancel', detail['version'])
        self.say('b', '/dongy ' + code)
        self.assertEqual('cancelled', self.status(ticket))

    def test_restart_preserves_unexpired_review_but_used_code_or_message_never_replays(self):
        ticket = self.create()
        code, _ = self.review('b', ticket)
        expiry = self.body_row('b')['expires']
        self.now += 100
        self.restart()
        self.assertEqual(expiry, self.body_row('b')['expires'])
        self.assertIn('Đã đổi ca', self.say('b', '/dongy ' + code, mid='execute-once'))
        self.restart()
        self.assertIn('không thực hiện lại', self.say('b', '/dongy ' + code, mid='execute-once'))
        self.say('b', '/dongy ' + code)
        self.assertEqual('completed', self.status(ticket))
        self.assertEqual(1, self.store.db.execute("SELECT COUNT(*) FROM exchange_events WHERE ticket=? AND text LIKE '%bắt đầu cập nhật%'", (ticket,)).fetchone()[0])

    def test_mode_prefix_and_database_registry_never_reuse_even_after_restart(self):
        original_mode = self.service.mode
        codes = []
        try:
            for mode, prefix in (('demo', '1'), ('sheets_test', '2'), ('lab_live', '3'), ('lab_live_test', '4')):
                self.service.mode = mode
                with patch('hermes_zalo.tickets.chat.secrets.randbelow', return_value=42):
                    code = self.chat._confirmation_code()
                self.assertEqual(prefix + '042', code)
                codes.append(code)
        finally:
            self.service.mode = original_mode
        self.restart()
        with patch('hermes_zalo.tickets.chat.secrets.randbelow', return_value=42):
            self.assertEqual('1043', self.chat._confirmation_code())
        self.assertEqual(5, self.store.db.execute('SELECT COUNT(*) FROM ticket_chat_confirmation_codes').fetchone()[0])
        # Only actual exhaustion may reject allocation, never random collisions.
        with self.store.transaction() as db:
            db.executemany('INSERT OR IGNORE INTO ticket_chat_confirmation_codes VALUES (?,?)',
                           ((f'1{number:03d}', self.now) for number in range(1000) if number != 41))
        with patch('hermes_zalo.tickets.chat.secrets.randbelow', return_value=42):
            self.assertEqual('1041', self.chat._confirmation_code())
        with patch('hermes_zalo.tickets.chat.secrets.randbelow', return_value=42) as random:
            with self.assertRaises(TicketError):
                self.chat._confirmation_code()
            random.assert_called_once_with(1000)

    def test_legacy_numeric_review_seeded_and_old_expiry_not_extended(self):
        ticket = self.create()
        self.review('b', ticket)
        row = self.body_row('b')
        body = json.loads(row['body']) | {'code': '123456'}
        expiry = self.now + 45
        with self.store.transaction() as db:
            db.execute('UPDATE ticket_chat_dialogs SET body=?,expires=? WHERE scope=?', (packed(body), expiry, row['scope']))
            db.execute('DROP TABLE ticket_chat_confirmation_codes')
            db.execute('DROP TABLE ticket_chat_code_migrations')
        self.restart()
        self.assertEqual(expiry, self.body_row('b')['expires'])
        self.assertIsNotNone(self.store.db.execute(
            'SELECT code FROM ticket_chat_confirmation_codes WHERE code=?', ('123456',)).fetchone())
        with patch('hermes_zalo.tickets.chat.secrets.randbelow', return_value=789):
            self.assertEqual('1789', self.chat._confirmation_code())
        self.now += 45
        self.say('b', '/dongy ' + ticket + ' 123456')
        self.assertEqual('pending_peer', self.status(ticket))

    def test_legacy_long_action_and_bare_ticket_review_remain_supported(self):
        ticket = self.create()
        self.say('b', '/dongy ' + ticket)
        self.assertEqual('pending_peer', self.status(ticket))
        row = self.body_row('b')
        body = json.loads(row['body']) | {'code': 'A1B2C3'}
        with self.store.transaction() as db:
            db.execute('UPDATE ticket_chat_dialogs SET body=? WHERE scope=?', (packed(body), row['scope']))
        self.restart()
        self.assertIn('Đã đổi ca', self.say('b', '/dongy ' + ticket + ' A1B2C3'))

    def test_existing_six_digit_short_action_survives_upgrade_without_truncation(self):
        ticket = self.create()
        self.review('b', ticket)
        row = self.body_row('b')
        body = json.loads(row['body']) | {'code': '123456'}
        with self.store.transaction() as db:
            db.execute('UPDATE ticket_chat_dialogs SET body=? WHERE scope=?', (packed(body), row['scope']))
        self.restart()
        self.say('b', '/dongy 1234')
        self.assertEqual('pending_peer', self.status(ticket))
        self.assertIn('Đã đổi ca', self.say('b', '/dongy 123456'))

    def test_new_code_has_exactly_four_digits_even_at_pool_boundaries(self):
        for number, expected in ((0, '1000'), (999, '1999')):
            with patch('hermes_zalo.tickets.chat.secrets.randbelow', return_value=number):
                self.assertEqual(expected, self.chat._confirmation_code())
        ticket = self.create()
        code, _ = self.review('b', ticket)
        for wrong in (code[:3], code + '0', code + '00', code + 'x'):
            self.say('b', '/dongy ' + wrong)
            self.assertEqual('pending_peer', self.status(ticket))
        self.assertIn('Đã đổi ca', self.say('b', '/dongy ' + code))


if __name__ == '__main__':
    unittest.main()
