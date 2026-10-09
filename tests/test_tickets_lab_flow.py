"""Two-party service flow through the actual Lab HTTP provider, fully offline."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from hermes_zalo.tickets.lab_provider import GoogleLabAssignments
from hermes_zalo.tickets.service import TicketService, TicketStore
from test_tickets_lab_provider import SID, NOW, config, fixture, set_cell


class LabServiceFlowTests(unittest.TestCase):
    def test_two_person_flow_changes_only_two_D_cells_and_timeout_reconcile_never_reposts(self):
        for timeout_after_write in (False, True):
            with self.subTest(timeout_after_write=timeout_after_write), TemporaryDirectory() as directory:
                payload = fixture()
                before = deepcopy(payload)
                requests = []
                now = [NOW]

                def transport(request):
                    requests.append(request)
                    self.assertEqual('sheets.googleapis.com', request.url.host)
                    if request.method == 'GET':
                        return httpx.Response(200, json=payload)
                    self.assertEqual('POST', request.method)
                    updates = json.loads(request.content)['requests']
                    self.assertEqual(2, len(updates))
                    for operation in updates:
                        change = operation['updateCells']
                        region = change['range']
                        self.assertEqual('userEnteredValue', change['fields'])
                        self.assertEqual(17, region['sheetId'])
                        self.assertEqual(3, region['startColumnIndex'])
                        self.assertEqual(4, region['endColumnIndex'])
                        self.assertEqual(region['startRowIndex'] + 1, region['endRowIndex'])
                        cell = change['rows'][0]['values'][0]['userEnteredValue']['stringValue']
                        set_cell(payload, region['startRowIndex'], region['startColumnIndex'], cell)
                    if timeout_after_write:
                        raise httpx.ReadTimeout('Synthetic response lost after atomic write', request=request)
                    return httpx.Response(200, json={'spreadsheetId': SID, 'replies': [{}, {}]})

                provider = GoogleLabAssignments(
                    config(), client=httpx.Client(transport=httpx.MockTransport(transport)),
                    token_provider=lambda: 'synthetic-offline-token', clock=lambda: now[0])
                store = TicketStore(Path(directory) / 'lab.sqlite3')
                try:
                    by_name = {row['member_name']: row for row in provider.list_assignments()}
                    a, b = by_name['Coach Alpha'], by_name['Coach Beta']
                    for username, assignment in (('a', a), ('b', b)):
                        store.add_account(username, assignment['member_name'], assignment['member_id'],
                                          'member', 'synthetic-unused-password')
                    member_a, member_b = store.account('a'), store.account('b')
                    service = TicketService(store, provider, mode='lab_live', require_manager=False,
                                            clock=lambda: now[0].timestamp())
                    preview = service.preview(member_a, a['id'], b['id'])
                    ticket = service.create(member_a, preview['preview_token'], 'Synthetic private reason',
                                            'offline-two-person-request-0001')
                    self.assertEqual('pending_peer', ticket['status'])
                    self.assertEqual(before, payload)
                    self.assertTrue(all(request.method == 'GET' for request in requests))
                    result = service.action(member_b, ticket['id'], 'accept', ticket['version'])
                    if timeout_after_write:
                        self.assertEqual('uncertain', result['status'])
                        # Reconciliation remains possible after the shift dates;
                        # it only verifies the result of the original write.
                        now[0] += timedelta(days=4)
                        result = service.action(member_a, ticket['id'], 'reconcile', result['version'])
                    self.assertEqual('completed', result['status'])
                    self.assertIn('lịch hiện có', result['message'])
                    self.assertEqual(1, sum(request.method == 'POST' for request in requests))
                    self.assertEqual('GET', requests[-1].method)
                    self.assertEqual(0, store.db.execute('SELECT COUNT(*) FROM exchange_claims').fetchone()[0])
                    expected = deepcopy(before)
                    set_cell(expected, 3, 3, 'Coach Beta')
                    set_cell(expected, 8, 3, 'Coach Alpha')
                    # Exact comparison also protects Key Coach, every other
                    # column, headers, merges and source metadata.
                    self.assertEqual(expected, payload)
                    after = {row['id']: row for row in provider.list_assignments()}
                    self.assertEqual(b['member_id'], after[a['id']]['member_id'])
                    self.assertEqual(a['member_id'], after[b['id']]['member_id'])
                    self.assertEqual(1, sum(request.method == 'POST' for request in requests))
                finally:
                    provider.close()
                    store.close()


if __name__ == '__main__':
    unittest.main()
