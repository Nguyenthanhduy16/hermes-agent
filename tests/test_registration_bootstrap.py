"""Existing approved members enter a fresh Tickets DB with bounded source reads."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.registration import RegistrationError
from hermes_zalo.registration_flow import RegistrationFlow
from hermes_zalo.sheets import SheetsSettings
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.chat import TicketsChat
from hermes_zalo.tickets.service import TicketService, TicketStore, digest
from hermes_zalo.workload import WorkloadDirectory, WorkloadError, WorkloadSettings


class RegistrationBootstrapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(token='synthetic-bot-token', allowed_users=frozenset({'admin'}),
                                 allowed_groups=frozenset())
        self.values = dict(REGISTRATION_ENABLED='true', REGISTRATION_ADMIN_USERS='admin',
                           REGISTRATION_WORKLOAD_TAB='Workload Test',
                           REGISTRATION_WORKLOAD_NAME_COLUMN='D', REGISTRATION_WORKLOAD_PHONE_COLUMN='C',
                           REGISTRATION_WORKLOAD_HEADER_ROW='5', REGISTRATION_WORKLOAD_NAME_HEADER='Tên',
                           REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại')
        self.sheets = SimpleNamespace(settings=SheetsSettings('synthetic_workload_sheet', Path('unused.json'),
                                     calendar_format='lab', calendar_tabs=('Lịch lab thử',), issues_enabled=False),
                                     _request=AsyncMock(side_effect=AssertionError('No network requests')))
        cfg = WorkloadSettings.from_mapping(self.values, self.sheets.settings)
        self.directory = WorkloadDirectory(cfg, self.sheets)
        self.rows = [(f'Coach {letter}', f'090000000{index}') for index, letter in enumerate('ABCDEFGH', 1)]
        self.directory._read = AsyncMock(side_effect=lambda: list(self.rows))
        self.directory.lookup = AsyncMock(wraps=self.directory.lookup)
        self.flow = RegistrationFlow(self.settings, self.values, self.sheets,
                                     self.root / 'registration.sqlite3', directory=self.directory)
        self.addCleanup(self.flow.close)
        self.store = TicketStore(self.root / 'lab_live.sqlite3')
        self.addCleanup(self.store.close)
        self.members = [dict(member_id=f'lab-member-{i}', member_name=name) for i, (name, _) in enumerate(self.rows)]
        self.assignments = [dict(id=f'CA-{i}', **member, start=f'2035-11-{i+1:02d}T09:00:00+07:00',
                                end=f'2035-11-{i+1:02d}T13:00:00+07:00', course='Synthetic course',
                                room='R101', period='Sáng') for i, member in enumerate(self.members)]
        self.provider = SimpleNamespace(settings=SimpleNamespace(spreadsheet_id=self.sheets.settings.spreadsheet_id),
                                        source_key='synthetic-lab-source', inspect_members=Mock(return_value=self.members),
                                        list_assignments=Mock(return_value=self.assignments))
        service = TicketService(self.store, self.provider, mode='lab_live', require_manager=False, clock=lambda: 1000)
        self.chat = TicketsChat(service, IdentityStore(self.store), bot_scope=digest(self.settings.token))

    def message(self, user, text='/doica'):
        return IncomingMessage('dm-' + user, user, 'm-' + text, 'PRIVATE', text)

    def approve(self, index, *, status='approved', source=None):
        person = self.directory._person(self.rows[index], self.rows)
        if source is not None:
            person = replace(person, source_key=source)
        user = f'user-{index}'
        record = self.flow.store.request(self.message(user, '/dangky'), person)
        if status != 'pending':
            record = self.flow.store.approve('admin', record.id, person)
        if status == 'revoked':
            record = self.flow.store.revoke('admin', record.member_id)
        return record

    def binding(self, record):
        return self.chat.identities.resolve(self.chat.bot_scope, record.user_id, record.chat_id)

    async def test_existing_approvals_become_selectable_without_a_peer_message_or_env_grant(self):
        approved = [self.approve(i) for i in range(3)]
        pending = self.approve(3, status='pending')
        revoked = self.approve(4, status='revoked')
        foreign = self.approve(5, source='workload:another-source')
        self.assertEqual([], self.chat.identities.linked_accounts(self.chat.bot_scope))
        result = await self.flow.bootstrap_tickets(self.chat)
        self.assertEqual({'linked': 3, 'skipped': 0}, result)
        self.directory._read.assert_awaited_once()
        self.directory.lookup.assert_not_awaited()
        self.provider.inspect_members.assert_called_once()
        for record in approved:
            bound = self.binding(record)
            self.assertEqual('member', bound['role'])
            self.assertNotIn(record.user_id, self.settings.allowed_users)
            self.assertNotIn(record.user_id, self.flow.store.admins)
        for record in (pending, revoked, foreign):
            self.assertIsNone(self.binding(record))
        response = self.chat._select_person(self.chat._scope(self.message(approved[0].user_id)),
                                             self.binding(approved[0]), self.assignments[0])
        self.assertIn(approved[1].name, response)
        self.assertIn(approved[2].name, response)
        for record in (pending, revoked, foreign):
            self.assertNotIn(record.name, response)
        self.assertNotIn(self.rows[6][0], response)  # A name in Sheets is not a grant.
        self.sheets._request.assert_not_awaited()

    async def test_repeat_and_restart_preserve_binding_revisions_and_existing_approvals(self):
        records = [self.approve(i) for i in range(3)]
        await self.flow.bootstrap_tickets(self.chat)
        before = [self.binding(record) for record in records]
        flow = RegistrationFlow(self.settings, self.values, self.sheets,
                                self.root / 'registration.sqlite3', directory=self.directory)
        try:
            self.assertEqual({'linked': 3, 'skipped': 0}, await flow.bootstrap_tickets(self.chat))
        finally:
            flow.close()
        self.assertEqual(before, [self.binding(record) for record in records])
        self.assertEqual(3, self.store.db.execute('SELECT COUNT(*) FROM ticket_accounts').fetchone()[0])
        self.assertEqual(records, [self.flow.identity(record.user_id) for record in records])

    async def test_one_stale_ambiguous_or_conflicting_member_does_not_block_valid_people(self):
        records = [self.approve(i) for i in range(5)]
        self.rows[1] = ('Changed Name', self.rows[1][1])
        self.rows.append((self.rows[2][0].lower(), '0900000022'))
        self.rows.append(('Another Name', self.rows[3][1]))  # Duplicate normalized phone.
        self.store.add_account('conflict', 'Other person', 'unrelated-member', 'member', 'synthetic-password')
        self.chat.identities.bind(self.chat.bot_scope, records[4].user_id, records[4].chat_id, 'conflict')
        conflicting = self.binding(records[4])
        self.assertEqual({'linked': 1, 'skipped': 4}, await self.flow.bootstrap_tickets(self.chat))
        self.assertIsNotNone(self.binding(records[0]))
        for record in records[1:4]:
            self.assertIsNone(self.binding(record))
        self.assertEqual(conflicting, self.binding(records[4]))
        self.directory._read.assert_awaited_once()
        self.provider.inspect_members.assert_called_once()

    async def test_revocation_during_each_source_read_never_creates_a_binding(self):
        first, second = self.approve(0), self.approve(1)
        async def read_and_revoke():
            self.flow.store.revoke('admin', first.member_id)
            return list(self.rows)
        def inspect_and_revoke():
            self.flow.store.revoke('admin', second.member_id)
            return self.members
        self.directory._read.side_effect = read_and_revoke
        self.provider.inspect_members.side_effect = inspect_and_revoke
        self.assertEqual({'linked': 0, 'skipped': 2}, await self.flow.bootstrap_tickets(self.chat))
        self.assertIsNone(self.binding(first))
        self.assertIsNone(self.binding(second))
        self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM ticket_accounts').fetchone()[0])

    async def test_identity_cleanup_cannot_be_undone_by_an_older_sync_worker(self):
        record = self.approve(0)
        original_resolve = self.chat.identities.resolve
        attempted, corrected = False, False

        def correction_if_idle():
            # Match Admin correction's lock order. Nonblocking acquisition makes
            # the exact race deterministic without waiting for a scheduler tick.
            if not self.store.lock.acquire(blocking=False):
                return False
            try:
                with self.flow.store.lock:
                    self.chat.identities.revoke(self.chat.bot_scope, record.user_id, record.chat_id)
                    self.flow.store.revoke('admin', record.member_id)
                return True
            finally:
                self.store.lock.release()

        with ThreadPoolExecutor(max_workers=1) as pool:
            def resolve_with_concurrent_correction(*args):
                nonlocal attempted, corrected
                if not attempted:
                    attempted = True
                    # This point follows sync_tickets' last grant check. A
                    # separate thread tries to remove the old identity now.
                    corrected = pool.submit(correction_if_idle).result(timeout=5)
                return original_resolve(*args)

            with patch.object(self.chat.identities, 'resolve', side_effect=resolve_with_concurrent_correction):
                await self.flow.sync_tickets(record, self.chat)
            if not corrected:
                self.assertTrue(pool.submit(correction_if_idle).result(timeout=5))
        self.assertTrue(attempted)
        self.assertIsNone(self.binding(record))
        self.assertIsNone(self.flow.identity(record.user_id))

    async def test_source_failure_has_no_partial_bootstrap_and_retry_is_possible(self):
        records = [self.approve(i) for i in range(3)]
        self.provider.inspect_members.side_effect = RuntimeError('private-source-detail')
        with self.assertRaises(RegistrationError) as caught:
            await self.flow.bootstrap_tickets(self.chat)
        self.assertNotIn('private-source-detail', str(caught.exception))
        self.assertEqual([], self.chat.identities.linked_accounts(self.chat.bot_scope))
        self.provider.inspect_members.side_effect = None
        self.assertEqual({'linked': 3, 'skipped': 0}, await self.flow.bootstrap_tickets(self.chat))
        self.assertTrue(all(self.binding(record) for record in records))

    async def test_empty_approved_list_needs_no_source_reads(self):
        self.approve(0, status='pending')
        self.assertEqual({'linked': 0, 'skipped': 0}, await self.flow.bootstrap_tickets(self.chat))
        self.directory._read.assert_not_awaited()
        self.provider.inspect_members.assert_not_called()

    async def test_test_mode_still_links_only_the_exact_test_pair(self):
        records = [self.approve(i) for i in range(3)]
        self.chat.live_test = True
        self.chat.service.mode = 'lab_live_test'
        self.chat.service.test_members = {records[0].name, records[1].name}
        self.assertEqual({'linked': 2, 'skipped': 1}, await self.flow.bootstrap_tickets(self.chat))
        self.assertIsNone(self.binding(records[2]))

    async def test_wrong_source_is_rejected_before_reads_or_local_changes(self):
        self.approve(0)
        self.provider.settings.spreadsheet_id = 'another_spreadsheet'
        with self.assertRaises(RegistrationError):
            await self.flow.bootstrap_tickets(self.chat)
        self.directory._read.assert_not_awaited()
        self.provider.inspect_members.assert_not_called()
        self.assertEqual([], self.chat.identities.linked_accounts(self.chat.bot_scope))


if __name__ == '__main__':
    unittest.main()
