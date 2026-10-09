"""Trusted ticket notifications with simulated Hermes/Zalo; no external APIs."""
import asyncio
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_adapter import adapter_module, update
from hermes_zalo.api import AuthenticationError, DeliveryUncertain, ZaloAPIError
from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.pilot import format_report
from hermes_zalo.state import StateStore
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.chat import TicketsChat
from hermes_zalo.tickets.service import DemoAssignments, TicketService, TicketStore, digest
from hermes_zalo.work import WorkService


def notice(identity='notice-1', user='u2', chat='dm2', text='Có yêu cầu đổi ca.\n/dongy 2345\n/tuchoi 2345'):
    return dict(id=identity, user_id=user, chat_id=chat, text=text)


class FakeQueueWork:
    """Claim consumes a pending row before transport; failed rows never requeue."""
    def __init__(self, pending=()):
        self.pending = list(pending)
        self.claimed = []
        self.finished = []
        self.status = {item['id']: 'pending' for item in pending}
        self.pilot = SimpleNamespace(record=Mock())
        self.close = AsyncMock()
        self.submissions = 0

    async def claim_ticket_notification(self):
        if not self.pending:
            return None
        item = self.pending.pop(0)
        self.claimed.append(item['id'])
        self.status[item['id']] = 'claimed'
        return item

    async def finish_ticket_notification(self, identity, status):
        self.finished.append((identity, status))
        self.status[identity] = status

    async def handle(self, message):
        self.submissions += 1
        item = notice(identity='submitted-notice')
        self.pending.append(item)
        self.status[item['id']] = 'pending'
        return 'Đã gửi yêu cầu. DC-SYNTHETIC'


class TicketNotificationAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings = Settings('fake-token-no-network', frozenset({'u1', 'u2'}), frozenset({'g1'}),
                            state_dir=Path(self.temp.name), knowledge_dir=Path(self.temp.name) / 'knowledge')
        with patch.object(adapter_module, 'scoped_settings', return_value=settings):
            self.adapter = adapter_module.ZaloAdapter(SimpleNamespace())
        self.guard = patch.object(adapter_module, 'assert_chat_only')
        self.guard.start()
        self.adapter.store = StateStore(Path(self.temp.name) / 'adapter.sqlite3')
        self.adapter.client = SimpleNamespace(send_text_chunk=AsyncMock(return_value={'message_id': 'sent'}),
                                               close=AsyncMock())
        self.handler = AsyncMock(side_effect=AssertionError('Notifications must never call a model'))
        self.adapter._message_handler = self.handler
        self.work = FakeQueueWork()
        self.adapter.work = self.work

    async def asyncTearDown(self):
        self.guard.stop()
        await self.adapter.disconnect()
        if hasattr(self, 'tickets_store'):
            self.tickets_store.close()
        self.temp.cleanup()

    def set_queue(self, *items):
        self.work = FakeQueueWork(items)
        self.adapter.work = self.work

    async def test_only_verified_queue_recipient_is_sent_once_without_model(self):
        item = notice()
        self.set_queue(item)
        await self.adapter._deliver_ticket_notifications()
        await self.adapter._deliver_ticket_notifications()
        self.adapter.client.send_text_chunk.assert_awaited_once_with('dm2', item['text'])
        self.assertEqual([('notice-1', 'sent')], self.work.finished)
        self.assertEqual(['notice-1'], self.work.claimed)
        self.handler.assert_not_awaited()

    async def test_notification_does_not_grant_general_cross_chat_send_permission(self):
        self.set_queue(notice())
        for chat in ('dm1', 'dm2', 'g1'):
            denied = await self.adapter.send(chat, 'arbitrary message')
            self.assertFalse(denied.success)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        await self.adapter._deliver_ticket_notifications()
        self.assertIsNone(self.adapter._send_permit.get())
        self.assertFalse((await self.adapter.send('dm2', 'arbitrary follow-up')).success)
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)

    async def test_revoked_recipient_allowlist_blocks_even_a_previously_claimable_queue_row(self):
        self.set_queue(notice())
        self.adapter.settings = replace(self.adapter.settings, allowed_users=frozenset({'u1'}))
        await self.adapter._deliver_ticket_notifications()
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual([('notice-1', 'blocked')], self.work.finished)
        self.handler.assert_not_awaited()

    async def test_unknown_recipient_is_not_sent_and_next_valid_recipient_can_be_processed(self):
        self.set_queue(notice('stranger', user='not-authorized'), notice('allowed'))
        await self.adapter._deliver_ticket_notifications()
        self.assertEqual([('stranger', 'blocked'), ('allowed', 'sent')], self.work.finished)
        self.adapter.client.send_text_chunk.assert_awaited_once_with('dm2', notice()['text'])

    async def test_unicode_is_split_without_loss_or_exceeding_utf16_limit(self):
        text = ('Đổi ca 🚀 · Quy định tiếng Việt.\n' * 250) + '/dongy 2345'
        self.set_queue(notice(text=text))
        await self.adapter._deliver_ticket_notifications()
        calls = self.adapter.client.send_text_chunk.await_args_list
        self.assertGreater(len(calls), 1)
        self.assertEqual(text, ''.join(call.args[1] for call in calls))
        self.assertTrue(all(call.args[0] == 'dm2' for call in calls))
        self.assertTrue(all(len(call.args[1].encode('utf-16-le')) // 2 <= 2000 for call in calls))
        self.assertEqual([('notice-1', 'sent')], self.work.finished)

    async def test_partial_timeout_is_uncertain_and_no_chunk_is_replayed(self):
        self.set_queue(notice(text='🚀' * 2400))
        self.adapter.client.send_text_chunk.side_effect = [{'message_id': 'first-chunk'}, DeliveryUncertain()]
        await self.adapter._deliver_ticket_notifications()
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual([('notice-1', 'uncertain')], self.work.finished)
        await self.adapter._deliver_ticket_notifications()
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)

    async def test_partial_send_stops_before_confirmation_commands_in_later_chunks(self):
        self.set_queue(notice(text=('Nội dung đối chiếu lịch 🚀.\n' * 250) + '\n/dongy 2345\n/tuchoi 2345'))
        self.adapter.client.send_text_chunk.side_effect = [{'message_id': 'first-chunk'}, DeliveryUncertain()]
        await self.adapter._deliver_ticket_notifications()
        attempted = ''.join(call.args[1] for call in self.adapter.client.send_text_chunk.await_args_list)
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)
        self.assertNotIn('/dongy', attempted)
        self.assertNotIn('/tuchoi', attempted)
        self.assertEqual([('notice-1', 'uncertain')], self.work.finished)
        await self.adapter._deliver_ticket_notifications()
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)

    async def test_definite_failure_is_finished_and_not_retried(self):
        self.set_queue(notice())
        self.adapter.client.send_text_chunk.side_effect = ZaloAPIError(code=429)
        await self.adapter._deliver_ticket_notifications()
        await self.adapter._deliver_ticket_notifications()
        self.assertEqual([('notice-1', 'failed')], self.work.finished)
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)

    async def test_notification_failure_does_not_undo_A_submission(self):
        async def send(chat, text):
            if chat == 'dm2':
                raise DeliveryUncertain()
            return {'message_id': 'submit-reply'}
        self.adapter.client.send_text_chunk.side_effect = send
        await self.adapter._dispatch(update(text='/doica gui 1234'))
        await self.adapter._deliver_ticket_notifications()
        row = self.adapter.store.connection.execute('SELECT status FROM messages').fetchone()
        self.assertEqual('completed', row[0])
        self.assertEqual(1, self.work.submissions)
        self.assertEqual('uncertain', self.work.status['submitted-notice'])
        self.assertTrue(any(call.args[0] == 'dm1' and 'Đã gửi yêu cầu' in call.args[1]
                            for call in self.adapter.client.send_text_chunk.await_args_list))
        self.handler.assert_not_awaited()

    async def test_authentication_failure_stops_before_claiming_next_notification(self):
        self.set_queue(notice('first'), notice('second'))
        self.adapter.client.send_text_chunk.side_effect = AuthenticationError()
        await self.adapter._deliver_ticket_notifications()
        self.assertEqual([('first', 'failed')], self.work.finished)
        self.assertEqual(['first'], self.work.claimed)
        self.assertEqual('pending', self.work.status['second'])
        self.assertTrue(self.adapter.has_fatal_error)
        self.assertEqual('zalo_auth', self.adapter.error_code)
        self.assertFalse(self.adapter.retryable)

    async def test_cancelled_delivery_persists_uncertain_before_propagating_cancellation(self):
        self.set_queue(notice())
        entered = asyncio.Event()
        async def blocked_send(*args):
            entered.set()
            await asyncio.Event().wait()
        self.adapter.client.send_text_chunk.side_effect = blocked_send
        task = asyncio.create_task(self.adapter._deliver_ticket_notifications())
        await asyncio.wait_for(entered.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual([('notice-1', 'uncertain')], self.work.finished)
        await self.adapter._deliver_ticket_notifications()
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)

    async def test_one_scheduler_tick_delivers_and_retains_one_second_interval(self):
        self.set_queue(notice())
        self.adapter._running = True
        sleeps = []
        async def end_tick(seconds):
            sleeps.append(seconds)
            self.adapter._running = False
        with patch.object(adapter_module.asyncio, 'sleep', side_effect=end_tick):
            await self.adapter._tickets_loop()
        self.assertEqual([1], sleeps)
        self.assertEqual([('notice-1', 'sent')], self.work.finished)

    async def test_batch_is_bounded_and_work_disabled_is_a_noop(self):
        self.set_queue(*(notice(f'n-{i}') for i in range(25)))
        await self.adapter._deliver_ticket_notifications()
        self.assertEqual(20, len(self.work.claimed))
        self.assertEqual(5, len(self.work.pending))
        await self.adapter._deliver_ticket_notifications()
        self.assertEqual(25, len(self.work.finished))
        self.adapter.work = None
        await self.adapter._deliver_ticket_notifications()

    def real_tickets(self):
        """Actual stores and domain; only the Zalo transport is simulated."""
        now = datetime.fromisoformat('2030-10-01T09:00:00+07:00').timestamp()
        self.tickets_store = TicketStore(Path(self.temp.name) / 'tickets.sqlite3')
        self.provider = DemoAssignments(self.tickets_store, now=now)
        self.service = TicketService(self.tickets_store, self.provider, require_manager=False, clock=lambda: now)
        self.identities = IdentityStore(self.tickets_store)
        self.bot_scope = digest(self.adapter.settings.token)
        for user, dm, account in (('u1', 'dm1', 'demo-a'), ('u2', 'dm2', 'demo-b')):
            self.identities.bind(self.bot_scope, user, dm, account)
        self.chat = TicketsChat(self.service, self.identities, bot_scope=self.bot_scope)
        self.work = WorkService(self.adapter.settings, environ={}, tickets_chat=self.chat)
        self.adapter.work = self.work
        self.seq = 0

    async def say(self, text, *, user='u1', chat='dm1'):
        self.seq += 1
        offset = self.adapter.client.send_text_chunk.await_count
        await self.adapter._dispatch(update(mid=f'e2e-{self.seq}', text=text, user=user, chat=chat))
        return ''.join(call.args[1] for call in self.adapter.client.send_text_chunk.await_args_list[offset:]
                       if call.args[0] == chat)

    async def create_real_ticket(self):
        await self.say('/doica')
        scope = self.chat._scope(IncomingMessage('dm1', 'u1', 'scope', 'PRIVATE', ''))
        for identity in ('CA-11', 'coach-b', 'CA-21'):
            row = self.tickets_store.db.execute('SELECT body FROM ticket_chat_dialogs WHERE scope=?', (scope,)).fetchone()
            choice = next(c for c in json.loads(row[0])['choices'] if c['item']['id'] == identity)
            await self.say(choice['code'])
        draft = await self.say('Việc riêng — lý do mẫu')
        command = re.search(r'/doica gui [0-9]{4}\b', draft)
        self.assertIsNotNone(command, draft)
        self.assertEqual(0, self.tickets_store.db.execute('SELECT COUNT(*) FROM ticket_peer_notifications').fetchone()[0])
        reply = await self.say(command[0])
        self.assertIn('Đã gửi yêu cầu', reply)
        return re.search(r'DC-[A-F0-9]+', reply)[0]

    async def test_real_work_chat_submit_notifies_B_once_and_B_accepts_directly(self):
        self.real_tickets()
        ticket = await self.create_real_ticket()
        self.assertTrue(all(call.args[0] == 'dm1' for call in self.adapter.client.send_text_chunk.await_args_list))
        self.adapter.client.send_text_chunk.reset_mock()
        await self.adapter._deliver_ticket_notifications()
        calls = self.adapter.client.send_text_chunk.await_args_list
        self.assertTrue(calls)
        self.assertTrue(all(call.args[0] == 'dm2' for call in calls))
        notification = ''.join(call.args[1] for call in calls)
        code = re.search(r'/dongy ([0-9]{4})\b', notification)[1]
        self.assertIn('/tuchoi ' + code, notification)
        for expected in (ticket, 'An · Thành viên A', 'Bình · Thành viên B', 'Việc riêng — lý do mẫu', 'SAU KHI ĐỔI'):
            self.assertIn(expected, notification)
        self.assertLess(notification.index('SAU KHI ĐỔI'), notification.index('/dongy'))
        sent_count = self.adapter.client.send_text_chunk.await_count
        await self.adapter._deliver_ticket_notifications()
        self.assertEqual(sent_count, self.adapter.client.send_text_chunk.await_count)
        reply = await self.say('/dongy ' + code, user='u2', chat='dm2')
        self.assertIn('Đã đổi ca', reply)
        row = self.tickets_store.db.execute('SELECT status FROM exchange_tickets WHERE id=?', (ticket,)).fetchone()
        self.assertEqual('completed', row[0])
        source = {item['id']: item for item in self.provider.list_assignments()}
        self.assertEqual(('coach-b', 'coach-a'), (source['CA-11']['member_id'], source['CA-21']['member_id']))
        self.assertEqual('sent', self.tickets_store.db.execute('SELECT status FROM ticket_peer_notifications').fetchone()[0])
        report = format_report(self.work.pilot.report())
        self.assertIn('thông báo đổi ca / đã gửi: 1', report)
        self.assertNotIn('Việc riêng — lý do mẫu', report)
        self.handler.assert_not_awaited()

    async def test_real_work_revoked_binding_blocks_queued_private_details(self):
        self.real_tickets()
        ticket = await self.create_real_ticket()
        self.identities.revoke(self.bot_scope, 'u2', 'dm2')
        self.adapter.client.send_text_chunk.reset_mock()
        await self.adapter._deliver_ticket_notifications()
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual('blocked', self.tickets_store.db.execute('SELECT status FROM ticket_peer_notifications').fetchone()[0])
        row = self.tickets_store.db.execute('SELECT status FROM exchange_tickets WHERE id=?', (ticket,)).fetchone()
        self.assertEqual('pending_peer', row[0])
        self.handler.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
