"""Per-operation permission checks; all remote transports are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
import unittest

import httpx

from hermes_zalo.tickets.lab_provider import GoogleLabAssignments
from hermes_zalo.tickets.providers import (
    ASSIGNMENT_KEYS, GoogleSheetsAssignments, TicketsAccessDenied,
    require_ticket_access, ticket_access_scope,
)
from hermes_zalo.tickets.service import DemoAssignments, TicketStore
from test_tickets_sheets import assignment, grid, settings


class TicketAccessGuardTests(unittest.TestCase):
    def test_nested_scopes_restore_parent_and_clear_after_exception(self):
        require_ticket_access()
        with ticket_access_scope(lambda: True):
            require_ticket_access()
            with self.assertRaises(TicketsAccessDenied):
                with ticket_access_scope(lambda: False):
                    require_ticket_access()
            require_ticket_access()
        require_ticket_access()

    def test_guard_failure_is_closed_and_does_not_leak_exception(self):
        def broken():
            raise RuntimeError('PRIVATE_TOKEN and private identity')
        with ticket_access_scope(broken), self.assertRaises(TicketsAccessDenied) as caught:
            require_ticket_access()
        self.assertNotIn('PRIVATE_TOKEN', str(caught.exception))
        require_ticket_access()

    def test_concurrent_workers_do_not_share_operation_permissions(self):
        barrier = Barrier(2)
        def worker(allowed):
            with ticket_access_scope(lambda: allowed):
                barrier.wait(timeout=5)
                try:
                    require_ticket_access()
                except TicketsAccessDenied:
                    return False
                return True
        with ThreadPoolExecutor(max_workers=2) as executor:
            denied = executor.submit(worker, False)
            allowed = executor.submit(worker, True)
            self.assertFalse(denied.result(timeout=5))
            self.assertTrue(allowed.result(timeout=5))
        require_ticket_access()

    def test_both_sheet_providers_recheck_after_credential_refresh_before_post(self):
        for provider_type in (GoogleSheetsAssignments, GoogleLabAssignments):
            with self.subTest(provider=provider_type.__name__):
                active = True
                requests = []
                def refresh():
                    nonlocal active
                    active = False
                    return 'synthetic-token'
                def handler(request):
                    requests.append(request)
                    self.fail('Revocation during refresh must prevent POST')
                client = httpx.Client(transport=httpx.MockTransport(handler))
                provider = provider_type(SimpleNamespace(spreadsheet_id='synthetic-sheet'),
                                         client=client, token_provider=refresh)
                self.addCleanup(provider.close)
                with ticket_access_scope(lambda: active), self.assertRaises(TicketsAccessDenied):
                    provider._request('POST', json={'requests': [{}, {}]})
                self.assertEqual([], requests)

    def test_approved_guard_allows_one_post_on_each_provider(self):
        for provider_type in (GoogleSheetsAssignments, GoogleLabAssignments):
            with self.subTest(provider=provider_type.__name__):
                methods = []
                def handler(request):
                    methods.append(request.method)
                    return httpx.Response(200, json={'spreadsheetId': 'synthetic-sheet', 'replies': [{}, {}]})
                client = httpx.Client(transport=httpx.MockTransport(handler))
                provider = provider_type(SimpleNamespace(spreadsheet_id='synthetic-sheet'),
                                         client=client, token_provider=lambda: 'synthetic-token')
                self.addCleanup(provider.close)
                with ticket_access_scope(lambda: True):
                    provider._request('POST', json={'requests': [{}, {}]})
                self.assertEqual(['POST'], methods)

    def test_revocation_during_provider_fresh_read_prevents_swap_post(self):
        active = True
        methods = []
        def handler(request):
            nonlocal active
            methods.append(request.method)
            self.assertEqual('GET', request.method)
            active = False
            return httpx.Response(200, json=grid())
        provider = GoogleSheetsAssignments(settings(), client=httpx.Client(transport=httpx.MockTransport(handler)),
                                          token_provider=lambda: 'synthetic-token')
        self.addCleanup(provider.close)
        a = dict(zip(ASSIGNMENT_KEYS, assignment()))
        b = dict(zip(ASSIGNMENT_KEYS, assignment('ca-B', 'B', 'Binh', 11)))
        # Use the same approved name as the synthetic source.
        b['member_name'] = grid()['sheets'][0]['data'][0]['rowData'][2]['values'][2]['effectiveValue']['stringValue']
        with ticket_access_scope(lambda: active), self.assertRaises(TicketsAccessDenied):
            provider.swap(a, b, 'synthetic-ticket')
        self.assertEqual(['GET'], methods)

    def test_demo_swap_denial_preserves_both_assignments(self):
        with TemporaryDirectory() as directory:
            store = TicketStore(Path(directory) / 'tickets.sqlite3')
            try:
                provider = DemoAssignments(store, now=1900000000)
                before = provider.list_assignments()
                with ticket_access_scope(lambda: False), self.assertRaises(TicketsAccessDenied):
                    provider.swap(before[0], before[2], 'synthetic-ticket')
                self.assertEqual(before, provider.list_assignments())
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
