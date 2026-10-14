"""Case-only roster resolution, existing IDs and approval flow; synthetic HTTP only."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from hermes_zalo.tickets.lab_provider import GoogleLabAssignments, _member
from hermes_zalo.tickets.providers import TicketsSourceError
from hermes_zalo.tickets.service import TicketService, TicketStore, TrustedAdminActor
from test_tickets_lab_provider import config, fixture, set_cell, NOW, SID
from test_workload import settings as workload_settings, payload as workload_payload, mapping


class CaseNamesTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.roster_cfg = workload_settings(spreadsheet_id=SID)
        self.rows = [('TRẦN VĂN TÀI', '0912345678'), ('NGÔ ĐẮC LÃM', '0387654321'),
                     ('Coach Other', ''), ('Key Gamma', '')]
        set_cell(self.data, 3, 3, 'Trần Văn Tài')
        set_cell(self.data, 8, 3, 'Ngô Đắc Lãm')
        self.calls = []
        self.fail_roster = False

    def provider(self):
        def transport(request):
            self.calls.append(request)
            if request.method == 'GET':
                if any(self.roster_cfg.tab in r for r in request.url.params.get_list('ranges')):
                    if self.fail_roster:
                        raise httpx.ReadTimeout('synthetic private failure')
                    return httpx.Response(200, json=workload_payload(self.rows, cfg=self.roster_cfg))
                return httpx.Response(200, json=self.data)
            self.assertEqual('POST', request.method)
            for operation in json.loads(request.content)['requests']:
                update = operation['updateCells']
                self.assertEqual(3, update['range']['startColumnIndex'])
                self.assertEqual(4, update['range']['endColumnIndex'])
                set_cell(self.data, update['range']['startRowIndex'], 3,
                         update['rows'][0]['values'][0]['userEnteredValue']['stringValue'])
            return httpx.Response(200, json={'spreadsheetId': SID, 'replies': [{}, {}]})
        provider = GoogleLabAssignments(replace(config(), workload=self.roster_cfg),
            client=httpx.Client(transport=httpx.MockTransport(transport)),
            token_provider=lambda: 'synthetic-token', clock=lambda: NOW)
        self.addCleanup(provider.close)
        return provider

    @staticmethod
    def pair(provider):
        rows = provider.list_assignments()
        return (next(a for a in rows if a['course'].endswith(' D4')),
                next(a for a in rows if a['course'].endswith(' D9')))

    def test_live_config_uses_existing_registration_schema_without_new_variables(self):
        settings = config(**mapping())
        self.assertEqual(self.roster_cfg, settings.workload)
        with self.assertRaises(TicketsSourceError):
            config(REGISTRATION_ENABLED='true')
        self.assertEqual(GoogleLabAssignments(config()).source_key,
                         GoogleLabAssignments(settings).source_key)

    def test_variants_select_same_existing_member_without_rekeying_or_source_edits(self):
        set_cell(self.data, 4, 3, 'trần văn tài')
        before = deepcopy(self.data)
        provider = self.provider()
        members = provider.inspect_members()
        tai = [m for m in members if m['member_name'] == 'TRẦN VĂN TÀI']
        self.assertEqual([{'member_name': 'TRẦN VĂN TÀI', 'member_id': _member('TRẦN VĂN TÀI')}], tai)
        rows = [a for a in provider.list_assignments() if a['member_id'] == tai[0]['member_id']]
        self.assertEqual(2, len(rows))
        self.assertEqual(before, self.data)
        self.assertTrue(all(c.method == 'GET' for c in self.calls))

    def test_duplicate_roster_names_fail_even_if_other_phone_is_invalid(self):
        for duplicate in ('TRẦN VĂN TÀI', 'Trần Văn Tài'):
            with self.subTest(duplicate=duplicate):
                self.rows.append((duplicate, ''))
                with self.assertRaisesRegex(TicketsSourceError, 'trùng nhiều người'):
                    self.provider().inspect_members()
                self.rows.pop()
        self.assertTrue(all(c.method == 'GET' for c in self.calls))

    def test_accents_are_not_removed_and_unknown_names_are_not_claimed(self):
        set_cell(self.data, 3, 3, 'Tran Van Tai')
        a, _ = self.pair(self.provider())
        self.assertEqual('Tran Van Tai', a['member_name'])
        self.assertNotEqual(_member('TRẦN VĂN TÀI'), a['member_id'])

    def test_case_resolution_does_not_relax_literal_or_single_person_checks(self):
        for name in (' Trần Văn Tài', 'Trần Văn Tài và Ngô Đắc Lãm'):
            set_cell(self.data, 3, 3, name)
            with self.subTest(name=name), self.assertRaises(TicketsSourceError):
                self.provider().list_assignments()

    def test_roster_read_failure_blocks_instead_of_falling_back_to_guessing(self):
        self.fail_roster = True
        with self.assertRaises(TicketsSourceError):
            self.provider().list_assignments()
        self.assertTrue(all(c.method == 'GET' for c in self.calls))

    def test_key_coach_conflict_uses_same_canonical_identity(self):
        set_cell(self.data, 8, 4, 'trần văn tài')
        provider = self.provider()
        a, b = self.pair(provider)
        with self.assertRaisesRegex(TicketsSourceError, 'trùng giờ'):
            provider.validate_pair(a, b)

    def test_lab_coach_conflict_uses_same_canonical_identity(self):
        set_cell(self.data, 4, 3, 'ngô đắc lãm')
        provider = self.provider()
        a, b = self.pair(provider)
        with self.assertRaisesRegex(TicketsSourceError, 'trùng giờ'):
            provider.validate_pair(a, b)

    def test_ambiguity_added_after_preview_blocks_write(self):
        provider = self.provider()
        a, b = self.pair(provider)
        self.rows.append(('trần văn tài', '0900000001'))
        with self.assertRaisesRegex(TicketsSourceError, 'trùng nhiều người'):
            provider.validate_pair(a, b)
        self.assertTrue(all(c.method == 'GET' for c in self.calls))

    def test_legacy_spelling_is_compatible_only_for_readback_not_new_approval(self):
        provider = self.provider()
        a, b = self.pair(provider)
        old_a = dict(a, member_name='Trần Văn Tài', member_id=_member('Trần Văn Tài'))
        old_b = dict(b, member_name='Ngô Đắc Lãm', member_id=_member('Ngô Đắc Lãm'))
        with self.assertRaisesRegex(TicketsSourceError, 'đã thay đổi'):
            provider.validate_pair(old_a, old_b)
        set_cell(self.data, 3, 3, old_b['member_name'])
        set_cell(self.data, 8, 3, old_a['member_name'])
        provider.validate_result(old_a, old_b)
        self.assertTrue(all(c.method == 'GET' for c in self.calls))
        forged = dict(old_a, member_id='another-provider-identity')
        self.assertEqual(forged, provider.reconciliation_snapshot(forged))

    def test_old_accounts_keep_ids_and_peer_acceptance_still_requires_admin(self):
        provider = self.provider()
        a, b = self.pair(provider)
        before = deepcopy(self.data)
        with TemporaryDirectory() as directory:
            store = TicketStore(Path(directory) / 'tickets.sqlite3')
            try:
                for account, name in (('a', 'TRẦN VĂN TÀI'), ('b', 'NGÔ ĐẮC LÃM')):
                    store.add_account(account, name, _member(name), 'member', 'synthetic-only-password')
                service = TicketService(store, provider, mode='lab_live', require_manager=True,
                                        clock=lambda: NOW.timestamp())
                first, second = store.account('a'), store.account('b')
                preview = service.preview(first, a['id'], b['id'])
                ticket = service.create(first, preview['preview_token'], 'Mô phỏng', 'synthetic-case-swap')
                pending = service.action(second, ticket['id'], 'accept', ticket['version'])
                self.assertEqual('pending_manager', pending['status'])
                self.assertEqual(before, self.data)
                self.assertFalse(any(c.method == 'POST' for c in self.calls))
                admin = TrustedAdminActor('web:synthetic-admin', 'Admin', lambda: True)
                done = service.admin_action(admin, ticket['id'], 'approve', pending['version'])
                self.assertEqual('completed', done['status'])
                self.assertEqual(1, sum(c.method == 'POST' for c in self.calls))
                self.assertEqual(first['member_id'], store.account('a')['member_id'])
                expected = deepcopy(before)
                set_cell(expected, 3, 3, 'NGÔ ĐẮC LÃM')
                set_cell(expected, 8, 3, 'TRẦN VĂN TÀI')
                self.assertEqual(expected, self.data)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
