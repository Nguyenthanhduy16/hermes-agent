"""Automatic calendar delivery with disposable SQLite and fake Zalo only."""
import asyncio
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

import test_adapter as contract
import test_schedule_reminder_flow as flow_contract
from hermes_zalo.api import AuthenticationError, DeliveryBlocked, DeliveryUncertain, ZaloAPIError, ZaloClient
from hermes_zalo.schedule_reminders import AutoReminderSettings, ScheduleNotice, ScheduleReminderStore
from hermes_zalo.pilot import PilotStore, format_report
from hermes_zalo.reminders import VIETNAM_TZ


class ScheduleReminderAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await contract.AdapterTests.asyncSetUp(self)
        self.path = Path(self.temp.name) / 'auto-notices.sqlite3'
        self.ledger = ScheduleReminderStore(self.path)
        self.pilot = PilotStore(Path(self.temp.name) / 'pilot.sqlite3')
        self.notice = ScheduleNotice('notice-one', 'before_class', 'member-a', 'u1',
            'verified-private-a', '🔔 Lịch mô phỏng 09:00–13:00. Chuẩn bị check-in nhé.', 'event-one', 1000, 2000)
        self.allowed = True
        self.work = SimpleNamespace(
            schedule_reminder_settings=AutoReminderSettings(enabled=True),
            schedule_reminders=self.ledger,
            automatic_schedule_candidates=AsyncMock(side_effect=lambda **kw: [self.notice]),
            revalidate_automatic_schedule_candidate=AsyncMock(side_effect=lambda notice, **kw: notice),
            automatic_schedule_delivery_allowed=lambda notice, **kw: self.allowed and notice.chat_id == 'verified-private-a',
            pilot=self.pilot, close=AsyncMock(),
        )
        self.adapter.work = self.work

        async def guarded_send(chat, text, *, before_send=None):
            if before_send and not await before_send():
                raise DeliveryBlocked()
            return {'message_id': 'mock-reply'}
        self.adapter.client.send_text_chunk.side_effect = guarded_send

    async def asyncTearDown(self):
        await self.adapter.disconnect()
        self.ledger.close()
        self.pilot.close()
        await contract.AdapterTests.asyncTearDown(self)

    async def test_success_uses_recorded_dm_not_user_or_group_and_never_replays_after_restart(self):
        await self.adapter._deliver_schedule_reminders(now=1050)
        self.assertEqual('sent', self.ledger.get(self.notice.id)['status'])
        self.adapter.client.send_text_chunk.assert_awaited_once()
        self.assertEqual(('verified-private-a', self.notice.text), self.adapter.client.send_text_chunk.await_args.args)
        reopened = ScheduleReminderStore(self.path)
        try:
            reopened.recover_claimed()
            self.work.schedule_reminders = reopened
            await self.adapter._deliver_schedule_reminders(now=1100)
        finally:
            reopened.close()
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)
        metric = self.pilot.report()['events'][0]
        self.assertEqual(('schedule_reminders', 'sent', 1), (metric['kind'], metric['outcome'], metric['count']))
        self.assertIn('nhắc lịch tự động / đã gửi: 1', format_report(self.pilot.report()))

    async def test_two_delivery_ticks_cannot_claim_same_notice(self):
        async def revalidate(notice, **kwargs):
            await asyncio.sleep(0)
            return notice
        self.work.revalidate_automatic_schedule_candidate.side_effect = revalidate
        await asyncio.gather(self.adapter._deliver_schedule_reminders(now=1050),
                             self.adapter._deliver_schedule_reminders(now=1050))
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual('sent', self.ledger.get(self.notice.id)['status'])

    async def test_access_or_private_binding_revoked_before_claim_sends_nothing(self):
        self.allowed = False
        await self.adapter._deliver_schedule_reminders(now=1050)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertIsNone(self.ledger.get(self.notice.id))
        self.work.revalidate_automatic_schedule_candidate.assert_awaited_once()

    async def test_changed_source_before_claim_drops_stale_candidate(self):
        self.work.revalidate_automatic_schedule_candidate.return_value = None
        self.work.revalidate_automatic_schedule_candidate.side_effect = None
        await self.adapter._deliver_schedule_reminders(now=1050)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertIsNone(self.ledger.get(self.notice.id))

    async def test_changed_source_after_claim_blocks_stale_text_without_replay(self):
        self.work.revalidate_automatic_schedule_candidate.side_effect = [self.notice, None]
        await self.adapter._deliver_schedule_reminders(now=1050)
        self.assertEqual('blocked', self.ledger.get(self.notice.id)['status'])
        await self.adapter._deliver_schedule_reminders(now=1060)
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)

    async def test_route_change_after_429_prevents_second_transport_attempt(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(429, json={})
        async def revoke(*args):
            self.allowed = False
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            self.adapter.client = ZaloClient('simulated-token', http)
            with patch.object(self.adapter.client, '_backoff', side_effect=revoke):
                await self.adapter._deliver_schedule_reminders(now=1050)
        self.assertEqual(1, len(requests))
        self.assertEqual('blocked', self.ledger.get(self.notice.id)['status'])

    async def test_expired_snapshot_after_429_is_refreshed_before_permission_check(self):
        snapshot_fresh = True
        requests = []
        def handler(request):
            requests.append(request)
            return (httpx.Response(429, json={}) if len(requests) == 1 else
                    httpx.Response(200, json={'ok': True, 'result': {'message_id': 'mock-id'}}))
        async def expire(*args):
            nonlocal snapshot_fresh
            snapshot_fresh = False
        async def revalidate(notice, **kwargs):
            nonlocal snapshot_fresh
            snapshot_fresh = True
            return notice
        self.work.revalidate_automatic_schedule_candidate.side_effect = revalidate
        self.work.automatic_schedule_delivery_allowed = lambda notice, **kw: snapshot_fresh
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            self.adapter.client = ZaloClient('simulated-token', http)
            with patch.object(self.adapter.client, '_backoff', side_effect=expire):
                await self.adapter._deliver_schedule_reminders(now=1050)
        self.assertEqual(2, len(requests))
        self.assertEqual(3, self.work.revalidate_automatic_schedule_candidate.await_count)
        self.assertEqual('sent', self.ledger.get(self.notice.id)['status'])

    async def test_lock_between_unicode_chunks_stops_partial_message_and_never_replays(self):
        self.notice = replace(self.notice, text='😊' * 1200)
        async def first_then_lock(chat, text, **kwargs):
            if not await kwargs['before_send']():
                raise DeliveryBlocked()
            self.allowed = False
            return {'message_id': 'first'}
        self.adapter.client.send_text_chunk.side_effect = first_then_lock
        await self.adapter._deliver_schedule_reminders(now=1050)
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual('uncertain', self.ledger.get(self.notice.id)['status'])
        self.allowed = True
        await self.adapter._deliver_schedule_reminders(now=1051)
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)

    async def test_network_ambiguity_is_terminal(self):
        self.adapter.client.send_text_chunk.side_effect = DeliveryUncertain()
        await self.adapter._deliver_schedule_reminders(now=1050)
        await self.adapter._deliver_schedule_reminders(now=1051)
        self.assertEqual('uncertain', self.ledger.get(self.notice.id)['status'])
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)

    async def test_explicit_rejection_is_terminal_failure(self):
        self.adapter.client.send_text_chunk.side_effect = ZaloAPIError(code=429)
        await self.adapter._deliver_schedule_reminders(now=1050)
        await self.adapter._deliver_schedule_reminders(now=1051)
        self.assertEqual('failed', self.ledger.get(self.notice.id)['status'])
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)

    async def test_unclassified_send_error_stays_uncertain(self):
        self.adapter.client.send_text_chunk.side_effect = ZaloAPIError()
        await self.adapter._deliver_schedule_reminders(now=1050)
        self.assertEqual('uncertain', self.ledger.get(self.notice.id)['status'])

    async def test_token_rejection_uses_existing_fatal_policy(self):
        self.adapter.client.send_text_chunk.side_effect = AuthenticationError(code=401)
        await self.adapter._deliver_schedule_reminders(now=1050)
        self.assertEqual('failed', self.ledger.get(self.notice.id)['status'])
        self.assertTrue(self.adapter.has_fatal_error)
        self.assertEqual('zalo_auth', self.adapter.error_code)

    async def test_shutdown_marks_inflight_delivery_uncertain(self):
        started = asyncio.Event()
        async def stalled_send(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        self.adapter.client.send_text_chunk.side_effect = stalled_send
        task = asyncio.create_task(self.adapter._deliver_schedule_reminders(now=1050))
        self.adapter._schedule_reminder_task = task
        await asyncio.wait_for(started.wait(), 1)
        await self.adapter.disconnect()
        self.assertTrue(task.cancelled())
        self.assertIsNone(self.adapter._schedule_reminder_task)
        self.assertEqual('uncertain', self.ledger.get(self.notice.id)['status'])

    async def test_disabled_performs_no_source_read_or_transport(self):
        self.work.schedule_reminder_settings = AutoReminderSettings(enabled=False)
        await self.adapter._deliver_schedule_reminders(now=1050)
        self.work.automatic_schedule_candidates.assert_not_awaited()
        self.work.revalidate_automatic_schedule_candidate.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_not_awaited()

    async def test_outage_is_sanitized_and_scheduler_continues_independently(self):
        self.adapter._running = True
        self.work.schedule_reminder_settings = SimpleNamespace(enabled=True, poll_seconds=0.001)
        async def source(**kwargs):
            if self.work.automatic_schedule_candidates.await_count == 1:
                raise RuntimeError('PRIVATE-SCHEDULE-CONTENT')
            self.adapter._running = False
            return []
        self.work.automatic_schedule_candidates.side_effect = source
        with self.assertLogs(contract.adapter_module.logger.name, level='WARNING') as logs:
            await asyncio.wait_for(self.adapter._schedule_reminder_loop(), 1)
        self.assertEqual(2, self.work.automatic_schedule_candidates.await_count)
        self.assertNotIn('PRIVATE-SCHEDULE-CONTENT', str(logs.output))
        self.assertFalse(self.adapter.has_fatal_error)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        report = self.pilot.report()['events']
        self.assertEqual({('schedule_reminders', 'error', 1), ('schedule_reminders', 'ok', 1)},
                         {(row['kind'], row['outcome'], row['count']) for row in report})

    async def test_batch_limit_does_not_starve_later_unsent_members(self):
        candidates = [replace(self.notice, id=f'notice-{index}', event_key=f'event-{index}',
                              member_id=f'member-{index}') for index in range(25)]
        self.work.automatic_schedule_candidates.side_effect = lambda **kw: candidates
        self.assertTrue(await self.adapter._deliver_schedule_reminders(now=1050))
        self.assertEqual(20, self.adapter.client.send_text_chunk.await_count)
        self.assertFalse(await self.adapter._deliver_schedule_reminders(now=1051))
        self.assertEqual(25, self.adapter.client.send_text_chunk.await_count)
        self.assertTrue(all(self.ledger.get(n.id)['status'] == 'sent' for n in candidates))

    async def test_expired_candidate_never_claimed(self):
        await self.adapter._deliver_schedule_reminders(now=2000)
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertIsNone(self.ledger.get(self.notice.id))

    async def test_connection_starts_separate_task_only_when_enabled_and_cancels_on_shutdown(self):
        self.work.recover = Mock()
        await self.adapter.disconnect()
        async def stalled():
            await asyncio.Event().wait()
        client = SimpleNamespace(get_me=AsyncMock(return_value={'can_join_groups': True}),
            get_webhook_info=AsyncMock(return_value={'url': ''}), close=AsyncMock())
        for enabled in (False, True):
            self.work.schedule_reminder_settings = AutoReminderSettings(enabled=enabled)
            with patch.object(contract.adapter_module, 'ZaloClient', return_value=client), \
                 patch.object(contract.adapter_module, 'WorkService', return_value=self.work), \
                 patch.object(contract.adapter_module.AdminConfig, 'from_mapping',
                     return_value=SimpleNamespace(conversations=False, enabled=False)), \
                 patch.object(self.adapter, '_poll_loop', side_effect=stalled), \
                 patch.object(self.adapter, '_reminder_loop', side_effect=stalled), \
                 patch.object(self.adapter, '_tickets_loop', side_effect=stalled), \
                 patch.object(self.adapter, '_schedule_reminder_loop', side_effect=stalled) as sender:
                self.assertTrue(await self.adapter.connect())
                await asyncio.sleep(0)
                task = self.adapter._schedule_reminder_task
                self.assertEqual(enabled, task is not None)
                self.assertEqual(int(enabled), sender.await_count)
                await self.adapter.disconnect()
                if task:
                    self.assertTrue(task.cancelled())
                self.assertIsNone(self.adapter._schedule_reminder_task)

    async def test_source_read_failure_inside_transport_guard_blocks_without_sensitive_logs(self):
        self.work.revalidate_automatic_schedule_candidate.side_effect = [self.notice, RuntimeError('PRIVATE-ROSTER-CONTENT')]
        with self.assertLogs(contract.adapter_module.logger.name, level='WARNING') as logs:
            await self.adapter._deliver_schedule_reminders(now=1050)
        self.assertEqual('blocked', self.ledger.get(self.notice.id)['status'])
        self.assertNotIn('PRIVATE-ROSTER-CONTENT', str(logs.output))

    async def test_loop_uses_fast_drain_only_for_bounded_batch_backlog(self):
        for backlog, expected_sleep in ((True, 5), (False, 60)):
            self.adapter._running = True
            async def end_tick(seconds):
                self.adapter._running = False
            with patch.object(self.adapter, '_deliver_schedule_reminders', return_value=backlog), \
                 patch.object(contract.adapter_module.asyncio, 'sleep', side_effect=end_tick) as sleeping:
                await self.adapter._schedule_reminder_loop()
            sleeping.assert_awaited_once_with(expected_sleep)


class ScheduleReminderEndToEndTests(unittest.IsolatedAsyncioTestCase):
    """Actual WorkService, registration, flow, ledger, metrics and HTTP client.

    Only roster/Sheets reads and HTTP transport are synthetic; no external
    service receives a request and no production schedule is changed.
    """

    async def asyncSetUp(self):
        await contract.AdapterTests.asyncSetUp(self)
        self.fixture = flow_contract.ScheduleReminderFlowTests()
        await self.fixture.asyncSetUp()
        self.adapter.work = self.fixture.work
        self.requests = []
        def transport(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, json={'ok': True, 'result': {'message_id': str(len(self.requests))}})
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        self.adapter.client = ZaloClient('synthetic-token', self.http)

    async def asyncTearDown(self):
        # The fixture owns WorkService's stores; close each owner exactly once.
        self.adapter.work = None
        await self.fixture.asyncTearDown()
        await contract.AdapterTests.asyncTearDown(self)
        await self.http.aclose()

    async def test_real_services_deliver_evening_and_before_class_once_to_each_approved_dm(self):
        await self.adapter._deliver_schedule_reminders(now=self.fixture.now)
        self.assertEqual({'private-1', 'private-2'}, {request['chat_id'] for request in self.requests})
        self.assertEqual(2, len(self.requests))
        self.assertTrue(all('ngày mai' in request['text'] for request in self.requests))
        # The same evening is durable even across another source read.
        await self.adapter._deliver_schedule_reminders(now=self.fixture.now + 30)
        self.assertEqual(2, len(self.requests))
        self.fixture.tick += 60
        morning = datetime(2026, 10, 15, 8, 30, tzinfo=VIETNAM_TZ).timestamp()
        await self.adapter._deliver_schedule_reminders(now=morning)
        self.assertEqual(4, len(self.requests))
        self.assertTrue(all('check in' in request['text'] for request in self.requests[2:]))
        await self.adapter._deliver_schedule_reminders(now=morning + 30)
        self.assertEqual(4, len(self.requests))
        rows = self.fixture.work.schedule_reminders.connection.execute(
            'SELECT kind,status,COUNT(*) AS total FROM schedule_notices GROUP BY kind,status').fetchall()
        self.assertEqual({('evening', 'sent', 2), ('before_class', 'sent', 2)},
                         {tuple(row) for row in rows})
        metric = self.fixture.work.pilot.report()['events'][0]
        self.assertEqual(('schedule_reminders', 'sent', 4),
                         (metric['kind'], metric['outcome'], metric['count']))
        self.assertFalse(any('090000000' in request['text'] for request in self.requests))


if __name__ == '__main__':
    unittest.main()
