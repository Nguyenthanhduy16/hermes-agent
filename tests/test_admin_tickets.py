"""Admin exchanges reuse live in-process stores with synthetic assignments only."""
import asyncio
import threading
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from hermes_zalo.admin_auth import AdminAuth, AdminError
from hermes_zalo.admin_tickets import TicketAdmin
from hermes_zalo.config import Settings
from hermes_zalo.registration import RegistrationError
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.providers import TicketsSourceError, TicketsWriteUncertain
from hermes_zalo.tickets.service import DemoAssignments, TicketError, TicketService, TicketStore, digest
from hermes_zalo.work import WorkService


class AdminTicketsTests(unittest.IsolatedAsyncioTestCase):
    PASSWORD = 'Synthetic-admin-tickets-2026'

    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.now = 1900000000
        self.store = TicketStore(root / 'tickets.sqlite3')
        self.provider = DemoAssignments(self.store, now=self.now)
        self.service = TicketService(self.store, self.provider, clock=lambda: self.now)
        self.identities = IdentityStore(self.store)
        settings = Settings('synthetic-admin-tickets', frozenset({'alpha', 'beta'}), frozenset(),
                            state_dir=root, knowledge_dir=root / 'empty')
        self.scope = digest(settings.token)
        self.identities.bind(self.scope, 'alpha', 'private-alpha', 'demo-a')
        self.identities.bind(self.scope, 'beta', 'private-beta', 'demo-b')
        self.chat = SimpleNamespace(store=self.store, service=self.service, identities=self.identities,
                                    bot_scope=self.scope, close=self.store.close)
        self.work = WorkService(settings, environ={}, tickets_chat=self.chat)
        self.auth = AdminAuth(root / 'auth.sqlite3')
        self.auth.create_account('operator', self.PASSWORD, ['tickets'])
        anonymous, _ = self.auth.session('', create=True)
        self.session, _ = self.auth.login(anonymous, 'operator', self.PASSWORD)
        self.admin = TicketAdmin(self.work, self.auth)
        preview = self.service.preview(self.store.account('demo-a'), 'CA-11', 'CA-21')
        self.ticket = self.service.create(self.store.account('demo-a'), preview['preview_token'],
                                           'Cần đổi lịch thử nghiệm', 'synthetic-request-001')

    async def asyncTearDown(self):
        await self.work.close()
        self.auth.close()
        self.temp.cleanup()

    def accepted(self):
        self.ticket = self.service.action(self.store.account('demo-b'), self.ticket['id'], 'accept', self.ticket['version'])
        self.assertEqual('pending_manager', self.ticket['status'])
        return self.ticket

    def payload(self, action='approve', **changes):
        return dict(id=self.ticket['id'], action=action, expected_version=self.ticket['version'],
                    reason='', confirm=True) | changes

    async def test_list_and_detail_use_existing_runtime_without_source_reads_or_new_service(self):
        self.accepted()
        with patch.object(self.provider, 'list_assignments', side_effect=AssertionError('No source reads for listing')):
            with patch('hermes_zalo.tickets.service.TicketService.__init__', side_effect=AssertionError('Do not reopen')):
                listed = await self.admin.list_tickets(self.session, query='can doi', status='pending_manager')
                detail = await self.admin.detail(self.session, self.ticket['id'])
        self.assertEqual(1, listed['total'])
        self.assertEqual(1, listed['summary']['pending_manager'])
        self.assertEqual(1, listed['pages'])
        self.assertEqual(['approve', 'reject'], detail['ticket']['actions'])
        self.assertEqual([], (await self.admin.list_tickets(self.session, query='unmatched'))['items'])
        self.assertEqual([], (await self.admin.list_tickets(self.session, page=2))['items'])

    async def test_peer_acceptance_is_required_and_admin_confirmation_writes_once(self):
        before = self.provider.list_assignments()
        self.assertEqual([], (await self.admin.detail(self.session, self.ticket['id']))['ticket']['actions'])
        with self.assertRaises(TicketError):
            await self.admin.action(self.session, self.payload())
        self.assertEqual(before, self.provider.list_assignments())
        self.accepted()
        with patch.object(self.provider, 'swap', wraps=self.provider.swap) as swap:
            results = await asyncio.gather(self.admin.action(self.session, self.payload()),
                                          self.admin.action(self.session, self.payload()), return_exceptions=True)
        self.assertEqual(1, swap.call_count)
        self.assertEqual(1, sum(isinstance(result, dict) and result['ticket']['status'] == 'completed' for result in results))
        self.assertEqual(1, sum(isinstance(result, TicketError) for result in results))
        events = (await self.admin.detail(self.session, self.ticket['id']))['ticket']['events']
        self.assertTrue(any(event['actor'] == 'admin:web:operator' for event in events))

    async def test_reject_requires_reason_and_keeps_schedule_unchanged(self):
        self.accepted()
        before = self.provider.list_assignments()
        with self.assertRaises(AdminError):
            await self.admin.action(self.session, self.payload('reject'))
        result = await self.admin.action(self.session, self.payload('reject', reason='Không đủ người hỗ trợ'))
        self.assertEqual('rejected', result['ticket']['status'])
        self.assertEqual(before, self.provider.list_assignments())
        self.assertIn('Không đủ người hỗ trợ', result['ticket']['events'][-1]['text'])

    async def test_cancelled_admin_waiter_keeps_reminders_paused_until_write_thread_exits(self):
        self.accepted()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original_action = self.service.admin_action
        original_end = self.work.end_schedule_change
        def delayed_action(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Synthetic write not released')
            return original_action(*args, **kwargs)
        def ended():
            original_end()
            finished.set()
        with patch.object(self.service, 'admin_action', side_effect=delayed_action), \
                patch.object(self.work, 'end_schedule_change', side_effect=ended):
            task = asyncio.create_task(self.admin.action(self.session, self.payload()))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.assertEqual(1, self.work._automatic_schedule._writers)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(1, self.work._automatic_schedule._writers)
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 3))
        self.assertEqual(0, self.work._automatic_schedule._writers)
        self.assertEqual('completed', (await self.admin.detail(self.session, self.ticket['id']))['ticket']['status'])

    async def test_invalid_or_spoofed_payloads_do_not_write(self):
        self.accepted()
        with patch.object(self.provider, 'swap', wraps=self.provider.swap) as swap:
            for changes in (dict(confirm=False), dict(expected_version=True), dict(expected_version=None),
                            dict(actor='admin:forged'), dict(member_id='coach-c'), dict(reason='x' * 601),
                            dict(action='accept')):
                with self.subTest(changes=changes), self.assertRaises(AdminError):
                    await self.admin.action(self.session, self.payload(**changes))
            with self.assertRaises(TicketError):
                await self.admin.action(self.session, self.payload(expected_version=1))
        swap.assert_not_called()

    async def test_permission_revoked_during_async_participant_check_blocks_write(self):
        self.accepted()
        async def revoke(*args):
            self.auth.logout(self.session)
        self.work._check_registered_ticket_peers = AsyncMock(side_effect=revoke)
        with patch.object(self.provider, 'swap', wraps=self.provider.swap) as swap:
            with self.assertRaises(AdminError) as caught:
                await self.admin.action(self.session, self.payload())
        self.assertEqual(401, caught.exception.status)
        swap.assert_not_called()

    async def test_auth_revocation_during_source_read_blocks_external_write(self):
        self.accepted()
        read = self.provider.list_assignments
        def revoke():
            rows = read()
            self.auth.logout(self.session)
            return rows
        with patch.object(self.provider, 'list_assignments', side_effect=revoke):
            with patch.object(self.provider, 'swap', wraps=self.provider.swap) as swap:
                with self.assertRaises((TicketError, TicketsSourceError)):
                    await self.admin.action(self.session, self.payload())
        swap.assert_not_called()

    async def test_binding_change_or_participant_scope_loss_blocks_write(self):
        self.accepted()
        async def rebind(*args):
            self.identities.bind(self.scope, 'alpha', 'private-alpha', 'demo-a')
        self.work._check_registered_ticket_peers = AsyncMock(side_effect=rebind)
        with patch.object(self.provider, 'swap', wraps=self.provider.swap) as swap:
            with self.assertRaisesRegex(AdminError, 'changed'):
                await self.admin.action(self.session, self.payload())
            self.work.settings = replace(self.work.settings, allowed_users=frozenset({'beta'}))
            with self.assertRaisesRegex(AdminError, 'revoked'):
                await self.admin.action(self.session, self.payload())
        swap.assert_not_called()

    async def test_source_identity_validation_failure_blocks_write(self):
        self.accepted()
        self.work._check_registered_ticket_peers = AsyncMock(side_effect=RegistrationError('Nguồn đăng ký đã thay đổi.'))
        with patch.object(self.provider, 'swap', wraps=self.provider.swap) as swap:
            with self.assertRaisesRegex(AdminError, 'Nguồn đăng ký'):
                await self.admin.action(self.session, self.payload())
        swap.assert_not_called()

    async def test_uncertain_write_only_reconciles_and_never_retries(self):
        self.accepted()
        before = self.provider.list_assignments()
        with patch.object(self.provider, 'swap', side_effect=TicketsWriteUncertain('Synthetic timeout')) as swap:
            self.ticket = (await self.admin.action(self.session, self.payload()))['ticket']
            self.assertEqual('uncertain', self.ticket['status'])
            result = await self.admin.action(self.session, self.payload('reconcile'))
        self.assertEqual(1, swap.call_count)
        self.assertEqual('uncertain', result['ticket']['status'])
        self.assertEqual(before, self.provider.list_assignments())

    async def test_reconcile_remains_read_only_after_member_permission_revocation(self):
        self.accepted()
        swap = self.provider.swap
        def uncertain_after_write(*args):
            swap(*args)
            raise TicketsWriteUncertain('Synthetic lost response')
        with patch.object(self.provider, 'swap', side_effect=uncertain_after_write) as write:
            self.ticket = (await self.admin.action(self.session, self.payload()))['ticket']
        self.assertEqual('uncertain', self.ticket['status'])
        self.assertEqual(1, write.call_count)
        before = self.provider.list_assignments()
        self.work.settings = replace(self.work.settings, allowed_users=frozenset())
        self.work._check_registered_ticket_peers = AsyncMock(side_effect=RegistrationError('Member revoked'))
        with patch.object(self.provider, 'swap', side_effect=AssertionError('Never retry')) as write:
            result = await self.admin.action(self.session, self.payload('reconcile'))
        self.assertEqual('completed', result['ticket']['status'])
        write.assert_not_called()
        self.work._check_registered_ticket_peers.assert_not_awaited()
        self.assertEqual(before, self.provider.list_assignments())

    async def test_disabled_view_is_safe_and_actions_fail_without_opening_another_runtime(self):
        self.work._open_tickets = AsyncMock(return_value=None)
        result = await self.admin.list_tickets(self.session)
        self.assertFalse(result['enabled'])
        self.assertEqual([], result['items'])
        self.assertIsNone((await self.admin.detail(self.session, self.ticket['id']))['ticket'])
        with self.assertRaises(AdminError) as caught:
            await self.admin.action(self.session, self.payload())
        self.assertEqual(503, caught.exception.status)

    async def test_faculty_has_no_implicit_tickets_and_tickets_has_no_private_grants(self):
        self.auth.create_account('faculty', self.PASSWORD, [], role='faculty')
        anonymous, _ = self.auth.session('', create=True)
        faculty, _ = self.auth.login(anonymous, 'faculty', self.PASSWORD)
        with self.assertRaises(AdminError) as caught:
            await self.admin.list_tickets(faculty)
        self.assertEqual(403, caught.exception.status)
        for permission in ('conversations', 'memories', 'members'):
            with self.subTest(permission=permission), self.assertRaises(AdminError):
                self.auth.user(self.session, permission)


if __name__ == '__main__':
    unittest.main()
