"""Lab runtime/admin boundaries with fake providers; no Google or Zalo APIs."""
from contextlib import redirect_stdout
from copy import deepcopy
import csv
from datetime import datetime
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.chat import TicketsChat
from hermes_zalo.tickets.cli import main, parser, process_lock
from hermes_zalo.tickets.providers import TicketsSourceError, TicketsWriteUncertain
from hermes_zalo.tickets.runtime import open_chat
from hermes_zalo.tickets.service import TicketError, TicketService, TicketStore, digest
from hermes_zalo.tickets.user_import import import_users, match_members, read_users_csv


PEOPLE = [{'member_id': 'lab-a', 'member_name': 'Trần Văn Tài'},
          {'member_id': 'lab-b', 'member_name': 'Ngô Đắc Lãm'}]
CSV_ROWS = [{'nguoi': 'A', 'user_id': 'synthetic-user-a', 'chat_id': 'synthetic-dm-a',
             'member_name': PEOPLE[0]['member_name']},
            {'nguoi': 'B', 'user_id': 'synthetic-user-b', 'chat_id': 'synthetic-dm-b',
             'member_name': PEOPLE[1]['member_name']}]


class FakeLabProvider:
    source_key = 'fake:existing-lab-source'
    def __init__(self, settings):
        self.settings, self.closed = settings, False
    def inspect_members(self):
        return deepcopy(PEOPLE)
    def list_assignments(self):
        raise AssertionError('Admin commands must not require configured periods')
    def close(self):
        self.closed = True


class LabWiringTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.csv = self.root / 'synthetic-users.csv'
        self.write_csv(CSV_ROWS)
        self.values = dict(TICKETS_DATA_DIR=str(self.root), ZALO_BOT_TOKEN='synthetic-secret-token',
                           ZALO_ALLOWED_USERS='synthetic-user-a,synthetic-user-b',
                           ZALO_SHEETS_ID='existing_live_sheet', ZALO_CALENDAR_FORMAT='lab',
                           ZALO_CALENDAR_TABS_JSON='["Thang10"]',
                           ZALO_GOOGLE_CREDENTIALS='synthetic-credentials.json')
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.values, clear=True).start()
        patch('hermes_zalo.tickets.cli.load_env').start()
        patch('hermes_zalo.tickets.cli.GoogleLabAssignments', FakeLabProvider).start()
        patch('hermes_zalo.tickets.runtime.GoogleLabAssignments', FakeLabProvider).start()

    def write_csv(self, rows):
        with self.csv.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['nguoi', 'user_id', 'chat_id', 'member_name'])
            writer.writeheader()
            writer.writerows(rows)

    def invoke(self, command, *extra):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main([command, '--csv', str(self.csv), *extra])
        text = output.getvalue()
        for private in ('synthetic-user', 'synthetic-dm', 'synthetic-secret', 'Trần Văn Tài', 'Ngô Đắc Lãm'):
            self.assertNotIn(private, text)
        return result, text

    def store(self):
        store = TicketStore(self.root / 'lab_live.sqlite3')
        self.addCleanup(store.close)
        return store

    def test_check_reads_names_without_hours_ids_or_database(self):
        self.write_csv([dict(row, user_id='', chat_id='') for row in CSV_ROWS])
        result, output = self.invoke('check-lab')
        self.assertEqual(0, result)
        self.assertIn('2/2', output)
        self.assertFalse((self.root / 'lab_live.sqlite3').exists())
        self.assertFalse((self.root / 'lab_live.lock').exists())

    def test_import_preserves_every_record_on_identical_rerun_including_pending_ticket(self):
        self.assertEqual(0, self.invoke('import-zalo-users', '--mode', 'lab_live')[0])
        store = self.store()
        scope = digest(self.values['ZALO_BOT_TOKEN'])
        identities = IdentityStore(store)
        a = identities.resolve(scope, 'synthetic-user-a', 'synthetic-dm-a')
        b = identities.resolve(scope, 'synthetic-user-b', 'synthetic-dm-b')
        assignments = [dict(id='ca-' + str(i), member_id=person['member_id'], member_name=person['member_name'],
                            start=f'2030-10-0{3+i}T08:00:00+07:00', end=f'2030-10-0{3+i}T11:00:00+07:00',
                            course='Test', room='A1', period='Sáng') for i, person in enumerate(PEOPLE)]
        provider = SimpleNamespace(source_key='fake:lab', list_assignments=lambda: deepcopy(assignments))
        service = TicketService(store, provider, mode='lab_live', require_manager=False,
                                clock=lambda: datetime.fromisoformat('2030-10-01T09:00:00+07:00').timestamp())
        preview = service.preview(a, 'ca-0', 'ca-1')
        ticket = service.create(a, preview['preview_token'], 'Lý do riêng', 'synthetic-request-0001')
        tables = ('ticket_accounts', 'ticket_zalo_bindings', 'exchange_tickets', 'exchange_claims',
                  'exchange_events', 'ticket_previews')
        before = {table: [tuple(row) for row in store.db.execute('SELECT * FROM ' + table)] for table in tables}
        self.assertEqual(0, self.invoke('import-zalo-users')[0])
        after = {table: [tuple(row) for row in store.db.execute('SELECT * FROM ' + table)] for table in tables}
        self.assertEqual(before, after)
        self.assertEqual(a, identities.resolve(scope, 'synthetic-user-a', 'synthetic-dm-a'))
        self.assertEqual('pending_peer', service.detail(b, ticket['id'])['status'])

    def test_bad_second_row_or_binding_conflict_rolls_back_whole_import(self):
        store = self.store()
        identities = IdentityStore(store)
        store.add_account('existing-b', 'Existing display', 'lab-b', 'member', 'synthetic-password')
        identities.bind(digest(self.values['ZALO_BOT_TOKEN']), 'other-user', 'other-dm', 'existing-b')
        before = [tuple(row) for row in store.db.execute('SELECT * FROM ticket_accounts')]
        result, _ = self.invoke('import-zalo-users')
        self.assertEqual(2, result)
        self.assertEqual(before, [tuple(row) for row in store.db.execute('SELECT * FROM ticket_accounts')])
        self.assertEqual(1, store.db.execute('SELECT COUNT(*) FROM ticket_zalo_bindings').fetchone()[0])

    def test_exact_names_and_allowlist_required_without_rewriting_config(self):
        self.write_csv([CSV_ROWS[0], dict(CSV_ROWS[1], member_name='Ngo Dac Lam')])
        self.assertEqual(2, self.invoke('import-zalo-users')[0])
        self.assertFalse((self.root / 'lab_live.sqlite3').exists())
        self.write_csv(CSV_ROWS)
        with patch.dict(os.environ, {'ZALO_ALLOWED_USERS': 'synthetic-user-a'}):
            self.assertEqual(2, self.invoke('import-zalo-users')[0])
            self.assertEqual('synthetic-user-a', os.environ['ZALO_ALLOWED_USERS'])
        self.assertEqual(0, self.store().db.execute('SELECT COUNT(*) FROM ticket_accounts').fetchone()[0])

    def test_revoked_accounts_are_not_reactivated_by_import(self):
        self.assertEqual(0, self.invoke('import-zalo-users')[0])
        store = self.store()
        with store.transaction() as db:
            db.execute("UPDATE ticket_accounts SET active=0 WHERE member_id='lab-b'")
        self.assertEqual(2, self.invoke('import-zalo-users')[0])
        self.assertEqual(0, store.db.execute("SELECT active FROM ticket_accounts WHERE member_id='lab-b'").fetchone()[0])

    def test_import_blocked_by_gateway_lock(self):
        with process_lock(self.root / 'lab_live.lock'):
            self.assertEqual(2, self.invoke('import-zalo-users')[0])
        self.assertFalse((self.root / 'lab_live.sqlite3').exists())

    def test_live_runtime_has_separate_database_no_manager_and_write_disabled(self):
        settings = Settings('synthetic-secret-token', frozenset({'synthetic-user-a'}))
        chat = open_chat(settings, self.values | {'TICKETS_CHAT_MODE': 'lab_live'})
        try:
            self.assertFalse(chat.service.require_manager)
            self.assertFalse(chat.service.provider.settings.write_enabled)
            self.assertFalse(chat.service.provider.settings.exclusive_writer)
            self.assertEqual({}, chat.service.provider.settings.periods)
            self.assertTrue((self.root / 'lab_live.sqlite3').exists())
            self.assertFalse((self.root / 'sheets_test.sqlite3').exists())
            self.assertIn('Lịch Lab hiện có', chat._menu({'name': 'A', 'role': 'member'}))
            self.assertIn('Ghi lịch đang khóa', chat._menu({'name': 'A', 'role': 'member'}))
            with self.assertRaises(TicketError):
                open_chat(settings, self.values | {'TICKETS_CHAT_MODE': 'lab_live'})
        finally:
            chat.close()
        with process_lock(self.root / 'lab_live.lock'):
            pass

    def test_live_admin_modes_are_available_but_live_web_is_rejected(self):
        for command in ('add-user', 'revoke-user', 'bind-zalo', 'unbind-zalo'):
            args = ['--username', 'unused'] if command != 'unbind-zalo' else []
            if command == 'add-user':
                args += ['--name', 'Unused']
            if command in ('bind-zalo', 'unbind-zalo'):
                args += ['--user-id', 'u', '--chat-id', 'dm']
            self.assertEqual('lab_live', parser().parse_args([command, '--mode', 'lab_live', *args]).mode)
        with redirect_stdout(io.StringIO()), patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(['serve', '--mode', 'lab_live'])

    def test_duplicate_csv_member_or_opaque_ids_rejected_before_mutation(self):
        for changed in (dict(CSV_ROWS[1], user_id=CSV_ROWS[0]['user_id']),
                        dict(CSV_ROWS[1], member_name=CSV_ROWS[0]['member_name']),
                        dict(CSV_ROWS[1], chat_id='has space')):
            self.write_csv([CSV_ROWS[0], changed])
            with self.assertRaises(TicketError):
                read_users_csv(self.csv)

    def test_live_test_import_requires_exact_pair_and_keeps_normal_database_separate(self):
        test_values = {'TICKETS_LAB_TEST_MEMBERS_JSON': json.dumps([p['member_name'] for p in PEOPLE])}
        with patch.dict(os.environ, test_values):
            self.assertEqual(0, self.invoke('check-lab', '--mode', 'lab_live_test')[0])
            self.assertEqual(0, self.invoke('import-zalo-users', '--mode', 'lab_live_test')[0])
            self.assertTrue((self.root / 'lab_live_test.sqlite3').exists())
            self.assertFalse((self.root / 'lab_live.sqlite3').exists())
            self.assertEqual(0, self.invoke('import-zalo-users', '--mode', 'lab_live_test')[0])
            self.write_csv(CSV_ROWS[:1])
            self.assertEqual(2, self.invoke('import-zalo-users', '--mode', 'lab_live_test')[0])

    def test_live_test_runtime_requires_two_members_no_manager_and_its_own_flags(self):
        settings = Settings('synthetic-secret-token', frozenset({'synthetic-user-a'}))
        values = self.values | {'TICKETS_CHAT_MODE': 'lab_live_test',
                               'TICKETS_LAB_TEST_MEMBERS_JSON': json.dumps([p['member_name'] for p in PEOPLE]),
                               'TICKETS_LAB_TEST_WRITE_ENABLED': 'true', 'TICKETS_LAB_TEST_EXCLUSIVE_WRITER': 'true'}
        with self.assertRaises(TicketError):
            open_chat(settings, values | {'TICKETS_REQUIRE_MANAGER': 'true'})
        chat = open_chat(settings, values)
        try:
            self.assertFalse(chat.service.require_manager)
            self.assertTrue(chat.service.provider.settings.test_mode)
            self.assertTrue(chat.service.provider.settings.write_enabled)
            self.assertEqual(set(p['member_name'] for p in PEOPLE), set(chat.service.test_members))
            self.assertTrue((self.root / 'lab_live_test.sqlite3').exists())
            self.assertFalse((self.root / 'lab_live.sqlite3').exists())
            self.assertIn('THỬ TRÊN SHEETS THẬT', chat._menu({'name': PEOPLE[0]['member_name'], 'role': 'member'}))
        finally:
            chat.close()


class FutureLiveChatTests(unittest.TestCase):
    def test_future_real_sheet_test_chat_confirms_actual_D_write_without_invented_hours(self):
        import re
        import httpx
        from hermes_zalo.tickets.lab_provider import GoogleLabAssignments, LabSheetsSettings
        from test_tickets_lab_provider import SID, NOW, fixture, set_cell, LabLiveProviderTests

        payload = fixture()
        set_cell(payload, 3, 4, 'Coach Beta')  # Deliberate Key Coach overlap, skipped only in this test mode.
        before = deepcopy(payload)
        transport_state = SimpleNamespace(payload=payload, calls=[], post_behavior='success')
        now = NOW
        settings = LabSheetsSettings.from_mapping(dict(
            ZALO_CALENDAR_FORMAT='lab', ZALO_SHEETS_ID=SID, ZALO_GOOGLE_CREDENTIALS='fake-key.json',
            ZALO_CALENDAR_TABS_JSON='["Lịch lab thử"]',
            TICKETS_LAB_TEST_MEMBERS_JSON='["Coach Alpha","Coach Beta"]',
            TICKETS_LAB_TEST_WRITE_ENABLED='true', TICKETS_LAB_TEST_EXCLUSIVE_WRITER='true'), test_mode=True)
        provider = GoogleLabAssignments(settings, client=httpx.Client(transport=httpx.MockTransport(
            lambda request: LabLiveProviderTests.handler(transport_state, request))),
            token_provider=lambda: 'synthetic-only-token', clock=lambda: now)
        with tempfile.TemporaryDirectory() as directory:
            store = TicketStore(Path(directory) / 'test.sqlite3')
            chat = None
            try:
                rows = provider.list_assignments()
                self.assertEqual({'Coach Alpha', 'Coach Beta'}, {row['member_name'] for row in rows})
                identities = IdentityStore(store)
                scope = digest('synthetic-live-test-bot')
                for user, name in (('a', 'Coach Alpha'), ('b', 'Coach Beta')):
                    assignment = next(row for row in rows if row['member_name'] == name)
                    store.add_account(user, name, assignment['member_id'], 'member', 'synthetic-test-password')
                    identities.bind(scope, user, 'dm-' + user, user)
                store.add_account('other', 'Coach Other', 'other-id', 'member', 'synthetic-test-password')
                identities.bind(scope, 'other', 'dm-other', 'other')
                service = TicketService(store, provider, mode='lab_live_test', require_manager=False,
                                        clock=lambda: now.timestamp())
                chat = TicketsChat(service, identities, bot_scope=scope)
                sequence = 0
                responses = []
                def say(user, text):
                    nonlocal sequence
                    sequence += 1
                    result = chat.handle(IncomingMessage('dm-' + user, user, str(sequence), 'PRIVATE', text))
                    responses.append(result)
                    return result
                def choose(reply):
                    return say('a', re.search(r'^(\d+)\. ', reply, re.M)[1])

                self.assertIn('không thuộc hai', say('other', '/tickets'))
                self.assertIn('THỬ TRÊN SHEETS THẬT', say('a', '/tickets'))
                reply = say('a', '/doica')
                self.assertIn('10/10/2035', reply)
                self.assertIn('· Sáng', reply)
                for _ in range(3):
                    reply = choose(reply)
                preview = say('a', 'Thử đổi trên bảng thật')
                self.assertIn('THỬ TRÊN SHEETS THẬT', preview)
                self.assertIn('chỉ ca tương lai', preview)
                self.assertIn('chưa kiểm tra trùng lịch', preview)
                result = say('a', re.search(r'/doica gui [A-F0-9]+', preview)[0])
                ticket = re.search(r'DC-[A-F0-9]+', result)[0]
                self.assertEqual('pending_peer', service.detail(store.account('a'), ticket)['status'])
                self.assertGreater(datetime.fromisoformat(service.detail(store.account('a'), ticket)['expires_at']).timestamp(), now.timestamp())
                detail = say('b', '/yeucau ' + ticket)
                self.assertIn('THỬ TRÊN SHEETS THẬT', detail)
                done = say('b', re.search(r'/dongy [0-9]{4}\b', detail)[0])
                self.assertIn('Đã đổi ca', done)
                self.assertIn('THỬ TRÊN SHEETS THẬT', done)
                self.assertIn('· Sáng', say('a', '/lichsudoica'))
                # Unknown shift hours must stay hidden. A real response cutoff
                # at the start of its date may separately be shown as 00:00.
                for reply in responses:
                    self.assertNotRegex(reply, r'(?:Sáng|Chiều|Tối)[^\n]*00:00')
                expected = deepcopy(before)
                set_cell(expected, 3, 3, 'Coach Beta')
                set_cell(expected, 8, 3, 'Coach Alpha')
                self.assertEqual(expected, payload)
                self.assertEqual(1, sum(request.method == 'POST' for request in transport_state.calls))
            finally:
                if chat:
                    chat.close()
                store.close()
                provider.close()


class TwoPartyReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = TicketStore(Path(self.directory.name) / 'synthetic.sqlite3')
        self.addCleanup(self.store.close)
        from hermes_zalo.tickets.service import DemoAssignments
        from test_tickets_service import FakeAssignments
        self.now = datetime.fromisoformat('2030-10-01T09:00:00+07:00').timestamp()
        demo = DemoAssignments(self.store, now=self.now)
        self.provider = FakeAssignments(demo.list_assignments())
        self.service = TicketService(self.store, self.provider, mode='lab_live', require_manager=False, clock=lambda: self.now)
        self.a, self.b, self.c = (self.store.account('demo-' + who) for who in ('a', 'b', 'c'))

    def test_two_involved_people_can_reconcile_read_only_but_unrelated_cannot(self):
        preview = self.service.preview(self.a, 'CA-11', 'CA-21')
        ticket = self.service.create(self.a, preview['preview_token'], '', 'request-key-00001')
        self.provider.behavior = 'timeout_after'
        result = self.service.action(self.b, ticket['id'], 'accept', ticket['version'])
        self.assertEqual('uncertain', result['status'])
        for user in (self.a, self.b):
            self.assertIn('reconcile', self.service.detail(user, ticket['id'])['actions'])
        with self.assertRaises(TicketError):
            self.service.action(self.c, ticket['id'], 'reconcile', result['version'])
        done = self.service.action(self.a, ticket['id'], 'reconcile', result['version'])
        self.assertEqual('completed', done['status'])
        self.assertIn('lịch hiện có', done['message'])
        self.assertNotIn('thử nghiệm', done['message'])
        self.assertEqual([ticket['id']], self.provider.calls)

    def test_optional_readonly_source_validation_runs_at_preview_submit_and_accept(self):
        self.provider.validate_pair = Mock()
        preview = self.service.preview(self.a, 'CA-11', 'CA-21')
        self.provider.validate_pair.assert_called_once()
        ticket = self.service.create(self.a, preview['preview_token'], '', 'request-key-00002')
        self.assertEqual(2, self.provider.validate_pair.call_count)
        self.provider.validate_pair.side_effect = TicketsSourceError('Trùng lịch Key Coach.')
        with self.assertRaises(TicketError):
            self.service.action(self.b, ticket['id'], 'accept', ticket['version'])
        self.assertEqual([], self.provider.calls)
        self.assertEqual('pending_peer', self.service.detail(self.a, ticket['id'])['status'])


if __name__ == '__main__':
    unittest.main()
