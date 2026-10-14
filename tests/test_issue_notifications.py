"""Issue outbox delivery with real disposable SQLite and simulated Zalo only."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import test_adapter as contract
from hermes_zalo.api import AuthenticationError, DeliveryUncertain, ZaloAPIError
from hermes_zalo.events import IncomingMessage
from hermes_zalo.issue_management import IssueError, ManagedIssueStore


ACTOR = dict(username='support.admin', role='admin', permissions=['issues'])


class IssueNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await contract.AdapterTests.asyncSetUp(self)
        self.outbox = ManagedIssueStore(Path(self.temp.name) / 'issues.sqlite3')
        self.allowed = True
        self.destination = ('member-a', 'u1', 'private-a', 'PRIVATE')
        def allowed(notice):
            return self.allowed and tuple(notice[key] for key in ('member_id', 'user_id', 'chat_id', 'chat_type')) == self.destination
        async def claim():
            return self.outbox.claim_notification(allowed)
        self.adapter.work = SimpleNamespace(managed_issues=self.outbox, issue_delivery_allowed=allowed,
            claim_issue_notification=AsyncMock(side_effect=claim), pilot=SimpleNamespace(record=Mock()), close=AsyncMock())

    async def asyncTearDown(self):
        await self.adapter.disconnect()
        self.outbox.close()
        await contract.AdapterTests.asyncTearDown(self)

    def notice(self, text=None):
        event = IncomingMessage('private-a', 'u1', 'issue-command', 'PRIVATE', '/issue tao Lỗi | Mô tả')
        draft = self.outbox.create(event, 'member-a', 'Lỗi mô phỏng', 'Mô tả mô phỏng', access_guard=lambda: True)
        issue = self.outbox.confirm(draft['id'], event, 'member-a', access_guard=lambda: True)
        self.outbox.act(issue['id'], ACTOR, 'claim', 1)
        if text is not None:
            with self.outbox.transaction() as db:
                db.execute('UPDATE managed_issue_outbox SET text=?', (text,))
        return self.row()

    def row(self):
        return dict(self.outbox.db.execute('SELECT * FROM managed_issue_outbox ORDER BY id LIMIT 1').fetchone())

    async def test_success_uses_trusted_private_chat_full_unicode_and_never_replays(self):
        text = 'Thông báo ☀️ ' * 300
        self.notice(text)
        await self.adapter._deliver_issue_notifications()
        calls = self.adapter.client.send_text_chunk.await_args_list
        self.assertGreater(len(calls), 1)
        self.assertEqual({'private-a'}, {call.args[0] for call in calls})
        self.assertEqual(text, ''.join(call.args[1] for call in calls))
        self.assertEqual('sent', self.row()['status'])
        count = len(calls)
        await self.adapter._deliver_issue_notifications()
        self.assertEqual(count, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual(1, self.row()['attempts'])

    async def test_revoked_or_rebound_member_route_blocks_without_sending(self):
        self.notice()
        self.destination = ('member-b', 'u1', 'private-a', 'PRIVATE')
        await self.adapter._deliver_issue_notifications()
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual('blocked', self.row()['status'])
        self.assertEqual('access_revoked', self.row()['error_code'])

    async def test_adapter_never_reinterprets_group_route_as_private_even_if_guard_allows(self):
        self.notice()
        with self.outbox.transaction() as db:
            db.execute("UPDATE managed_issue_outbox SET chat_type='GROUP',chat_id='allowed-group'")
        self.adapter.work.issue_delivery_allowed = lambda notice: True
        async def claim():
            return self.outbox.claim_notification(lambda notice: True)
        self.adapter.work.claim_issue_notification.side_effect = claim
        await self.adapter._deliver_issue_notifications()
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual('blocked', self.row()['status'])

    async def test_lock_between_chunks_stops_and_partial_message_cannot_retry(self):
        notice = self.notice('a' * 4500)
        async def send_then_lock(chat, text):
            self.allowed = False
            return {'message_id': 'only-first'}
        self.adapter.client.send_text_chunk.side_effect = send_then_lock
        await self.adapter._deliver_issue_notifications()
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual('uncertain', self.row()['status'])
        self.assertEqual('access_revoked', self.row()['error_code'])
        with self.assertRaises(IssueError):
            self.outbox.retry_notification(notice['id'], ACTOR)

    async def test_reset_during_send_stops_old_notice_even_if_same_member_is_allowed_again(self):
        self.notice('a' * 4500)
        async def send_then_reset(chat, text):
            self.outbox.detach_registration('member-a', 'u1', 'private-a')
            self.allowed = True  # Same member immediately registers again.
            return {'message_id': 'only-first'}
        self.adapter.client.send_text_chunk.side_effect = send_then_reset
        await self.adapter._deliver_issue_notifications()
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual('uncertain', self.row()['status'])
        self.assertEqual('access_revoked', self.row()['error_code'])

    async def test_new_registration_request_interrupts_old_issue_notification(self):
        self.notice('a' * 4500)
        grant = SimpleNamespace(member_id='member-a', id='DK-OLD')
        self.adapter.work.registration = SimpleNamespace(identity=lambda user: grant)
        async def send_then_reregister(chat, text):
            grant.id = 'DK-NEW'
            return {'message_id': 'only-first'}
        self.adapter.client.send_text_chunk.side_effect = send_then_reregister
        await self.adapter._deliver_issue_notifications()
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual('uncertain', self.row()['status'])

    async def test_explicit_first_chunk_rejection_is_failed_and_only_manual_retry_can_resume(self):
        notice = self.notice()
        self.adapter.client.send_text_chunk.side_effect = ZaloAPIError(code=429)
        await self.adapter._deliver_issue_notifications()
        self.assertEqual('failed', self.row()['status'])
        self.assertEqual('rate_limit', self.row()['error_code'])
        await self.adapter._deliver_issue_notifications()
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)
        self.outbox.retry_notification(notice['id'], ACTOR)
        self.adapter.client.send_text_chunk.side_effect = None
        await self.adapter._deliver_issue_notifications()
        self.assertEqual('sent', self.row()['status'])
        self.assertEqual(2, self.row()['attempts'])

    async def test_explicit_rejection_after_sent_chunk_is_uncertain_and_cannot_retry(self):
        notice = self.notice('a' * 4500)
        self.adapter.client.send_text_chunk.side_effect = [{'message_id': 'first'}, ZaloAPIError(code=429)]
        await self.adapter._deliver_issue_notifications()
        self.assertEqual('uncertain', self.row()['status'])
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)
        with self.assertRaises(IssueError):
            self.outbox.retry_notification(notice['id'], ACTOR)

    async def test_auth_rejection_before_sending_fails_and_stops_transport(self):
        self.notice()
        self.adapter.client.send_text_chunk.side_effect = AuthenticationError(code=401)
        await self.adapter._deliver_issue_notifications()
        self.assertEqual('failed', self.row()['status'])
        self.assertTrue(self.adapter.has_fatal_error)
        self.assertEqual('zalo_auth', self.adapter.error_code)
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)

    async def test_auth_rejection_after_sent_chunk_stays_uncertain(self):
        self.notice('a' * 4500)
        self.adapter.client.send_text_chunk.side_effect = [{'message_id': 'first'}, AuthenticationError(code=401)]
        await self.adapter._deliver_issue_notifications()
        self.assertEqual('uncertain', self.row()['status'])
        self.assertTrue(self.adapter.has_fatal_error)

    async def test_network_ambiguity_has_no_automatic_replay_after_restart(self):
        self.notice()
        self.adapter.client.send_text_chunk.side_effect = DeliveryUncertain()
        await self.adapter._deliver_issue_notifications()
        self.assertEqual('uncertain', self.row()['status'])
        self.assertEqual('network', self.row()['error_code'])
        reopened = ManagedIssueStore(self.outbox.db.execute('PRAGMA database_list').fetchone()['file'])
        try:
            self.assertIsNone(reopened.claim_notification(lambda notice: True))
        finally:
            reopened.close()

    async def test_unclassified_api_rejection_never_becomes_retryable_failure(self):
        self.notice()
        self.adapter.client.send_text_chunk.side_effect = ZaloAPIError()
        await self.adapter._deliver_issue_notifications()
        self.assertEqual('uncertain', self.row()['status'])

    async def test_cancellation_during_send_records_uncertainty_before_shutdown(self):
        self.notice()
        started = asyncio.Event()
        async def hanging_send(chat, text):
            started.set()
            await asyncio.Event().wait()
        self.adapter.client.send_text_chunk.side_effect = hanging_send
        task = asyncio.create_task(self.adapter._deliver_issue_notifications())
        self.adapter._issues_task = task
        await asyncio.wait_for(started.wait(), 1)
        await self.adapter.disconnect()
        self.assertTrue(task.cancelled())
        self.assertIsNone(self.adapter._issues_task)
        self.assertEqual('uncertain', self.row()['status'])
        self.assertEqual('interrupted', self.row()['error_code'])

    async def test_sender_exception_is_sanitized_and_does_not_disable_other_features(self):
        self.notice()
        self.adapter._running = True
        self.adapter.client.send_text_chunk.side_effect = RuntimeError('PRIVATE-ISSUE-CONTENT')
        with self.assertLogs(contract.adapter_module.logger.name, level='ERROR') as logs:
            await self.adapter._issues_loop()
        self.assertNotIn('PRIVATE-ISSUE-CONTENT', str(logs.output))
        self.assertEqual('uncertain', self.row()['status'])
        self.assertFalse(self.adapter.has_fatal_error)
        self.assertTrue(self.adapter._running)

    async def test_disabled_mode_does_not_claim_and_empty_queue_yields_loop(self):
        self.adapter.work.managed_issues = None
        await self.adapter._deliver_issue_notifications()
        self.adapter.work.claim_issue_notification.assert_not_awaited()
        self.adapter.work.managed_issues = self.outbox
        self.adapter._running = True
        task = asyncio.create_task(self.adapter._issues_loop())
        await asyncio.sleep(0.01)
        self.assertEqual(1, self.adapter.work.claim_issue_notification.await_count)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_connection_starts_optional_sender_only_when_enabled_and_disconnect_cancels_it(self):
        work = self.adapter.work
        work.recover = Mock()
        await self.adapter.disconnect()
        async def wait_until_cancelled():
            await asyncio.Event().wait()
        client = SimpleNamespace(get_me=AsyncMock(return_value={'can_join_groups': True}),
            get_webhook_info=AsyncMock(return_value={'url': ''}), close=AsyncMock())
        for enabled in (False, True):
            work.managed_issues = self.outbox if enabled else None
            with patch.object(contract.adapter_module, 'ZaloClient', return_value=client), \
                 patch.object(contract.adapter_module, 'WorkService', return_value=work), \
                 patch.object(contract.adapter_module.AdminConfig, 'from_mapping', return_value=SimpleNamespace(conversations=False, enabled=False)), \
                 patch.object(self.adapter, '_poll_loop', side_effect=wait_until_cancelled), \
                 patch.object(self.adapter, '_reminder_loop', side_effect=wait_until_cancelled), \
                 patch.object(self.adapter, '_tickets_loop', side_effect=wait_until_cancelled), \
                 patch.object(self.adapter, '_issues_loop', side_effect=wait_until_cancelled) as sender:
                self.assertTrue(await self.adapter.connect())
                await asyncio.sleep(0)
                task = self.adapter._issues_task
                self.assertEqual(enabled, task is not None)
                self.assertEqual(int(enabled), sender.await_count)
                await self.adapter.disconnect()
                if task:
                    self.assertTrue(task.cancelled())
                self.assertIsNone(self.adapter._issues_task)


if __name__ == '__main__':
    unittest.main()
