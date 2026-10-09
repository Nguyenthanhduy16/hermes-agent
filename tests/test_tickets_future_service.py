"""Future-only exchange rules, including legacy drafts and live-test mode."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from hermes_zalo.tickets.providers import TicketsWriteUncertain
from hermes_zalo.tickets.service import DemoAssignments, TicketError, TicketService, TicketStore


class FutureServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime.fromisoformat('2030-10-01T09:00:00+07:00').timestamp()
        self.store = TicketStore(Path(self.temp.name) / 'future.sqlite3')
        self.addCleanup(self.store.close)
        demo = DemoAssignments(self.store, now=self.now)
        self.a, self.b = self.store.account('demo-a'), self.store.account('demo-b')
        self.rows = {row['id']: row for row in demo.list_assignments()}
        self.writes = []
        self.provider = SimpleNamespace(source_key='synthetic-future-only',
            settings=SimpleNamespace(test_mode=True, test_members=(self.a['name'], self.b['name'])),
            list_assignments=lambda: deepcopy(list(self.rows.values())), swap=self.swap)

    def swap(self, a, b, ticket):
        self.writes.append(ticket)
        self.rows[a['id']] = dict(a, member_id=b['member_id'], member_name=b['member_name'])
        self.rows[b['id']] = dict(b, member_id=a['member_id'], member_name=a['member_name'])

    def service(self, mode='lab_live_test'):
        return TicketService(self.store, self.provider, mode=mode, require_manager=False, clock=lambda: self.now)

    def set_a(self, start, *, period='Sáng 09:05–11:00', end='2030-10-01T11:00:00+07:00'):
        self.rows['CA-11'].update(start=start, end=end, period=period)

    def test_helper_known_times_and_unknown_same_day_use_vietnam_date(self):
        service = self.service()
        self.set_a('2030-10-01T09:05:00+07:00')
        self.assertTrue(service.is_future(self.rows['CA-11']))
        self.rows['CA-11']['period'] = 'Sáng'
        self.assertFalse(service.is_future(self.rows['CA-11']))
        self.set_a('2030-10-02T00:00:00+07:00', period='Sáng', end='2030-10-03T00:00:00+07:00')
        self.now = datetime.fromisoformat('2030-10-01T16:59:59+00:00').timestamp()
        self.assertTrue(service.is_future(self.rows['CA-11']))
        self.now += 1
        self.assertFalse(service.is_future(self.rows['CA-11']))

    def test_exact_start_or_past_preview_is_rejected_in_every_mode(self):
        self.set_a('2030-10-01T09:00:00+07:00')
        for mode in ('demo', 'sheets_test', 'lab_live', 'lab_live_test'):
            with self.subTest(mode=mode):
                service = self.service(mode)
                self.assertFalse(service.is_future(self.rows['CA-11']))
                with self.assertRaises(TicketError):
                    service.preview(self.a, 'CA-11', 'CA-21')
        self.assertEqual([], self.writes)

    def test_live_test_preview_and_ticket_deadlines_stop_at_explicit_start(self):
        service = self.service()
        self.set_a('2030-10-01T09:05:00+07:00')
        deadline = self.now + 300
        preview = service.preview(self.a, 'CA-11', 'CA-21')
        self.assertEqual(deadline, datetime.fromisoformat(preview['expires_at']).timestamp())
        ticket = service.create(self.a, preview['preview_token'], '', 'future-explicit-001')
        self.assertEqual(deadline, datetime.fromisoformat(ticket['expires_at']).timestamp())
        self.now = deadline
        with self.assertRaises(TicketError):
            service.action(self.b, ticket['id'], 'accept', ticket['version'])
        self.assertEqual('expired', service.detail(self.b, ticket['id'])['status'])
        self.assertEqual([], self.writes)

    def test_unknown_hours_preview_and_ticket_deadlines_stop_at_midnight(self):
        self.now = datetime.fromisoformat('2030-10-02T23:58:00+07:00').timestamp()
        self.set_a('2030-10-03T00:00:00+07:00', period='Sáng', end='2030-10-04T00:00:00+07:00')
        service = self.service()
        preview = service.preview(self.a, 'CA-11', 'CA-21')
        deadline = self.now + 120
        self.assertEqual(deadline, datetime.fromisoformat(preview['expires_at']).timestamp())
        ticket = service.create(self.a, preview['preview_token'], '', 'future-unknown-001')
        self.assertEqual(deadline, datetime.fromisoformat(ticket['expires_at']).timestamp())

    def test_legacy_preview_with_long_expiry_cannot_submit_a_started_shift(self):
        service = self.service()
        self.set_a('2030-10-01T09:05:00+07:00')
        preview = service.preview(self.a, 'CA-11', 'CA-21')
        # Legacy test-mode previews had no shift-start clamp.
        with self.store.transaction() as db:
            db.execute('UPDATE ticket_previews SET expires=?', (self.now + 900,))
        self.now += 300
        with self.assertRaises(TicketError):
            service.create(self.a, preview['preview_token'], '', 'legacy-preview-001')
        self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM exchange_tickets').fetchone()[0])
        self.assertEqual([], self.writes)

    def test_legacy_sent_request_with_long_expiry_cannot_accept_a_started_shift(self):
        service = self.service()
        self.set_a('2030-10-01T09:05:00+07:00')
        preview = service.preview(self.a, 'CA-11', 'CA-21')
        ticket = service.create(self.a, preview['preview_token'], '', 'legacy-request-001')
        with self.store.transaction() as db:
            db.execute('UPDATE exchange_tickets SET expires=?', (self.now + 48 * 3600,))
        self.now += 300
        result = service.detail(self.b, ticket['id'])
        self.assertEqual('expired', result['status'])
        self.assertNotIn('accept', result['actions'])
        with self.assertRaises(TicketError):
            service.action(self.b, ticket['id'], 'accept', ticket['version'])
        self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM exchange_claims').fetchone()[0])
        self.assertEqual([], self.writes)

    def test_reconcile_after_start_can_confirm_a_write_that_started_before_deadline(self):
        service = self.service()
        self.set_a('2030-10-01T09:05:00+07:00')
        preview = service.preview(self.a, 'CA-11', 'CA-21')
        ticket = service.create(self.a, preview['preview_token'], '', 'reconcile-start-001')
        def late_response(a, b, tid):
            self.swap(a, b, tid)
            self.now += 360
            raise TicketsWriteUncertain('Synthetic response lost')
        self.provider.swap = late_response
        uncertain = service.action(self.b, ticket['id'], 'accept', ticket['version'])
        self.assertEqual('uncertain', uncertain['status'])
        done = service.action(self.b, ticket['id'], 'reconcile', uncertain['version'])
        self.assertEqual('completed', done['status'])
        self.assertEqual([ticket['id']], self.writes)


if __name__ == '__main__':
    unittest.main()
