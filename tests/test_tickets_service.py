"""Domain acceptance tests with temporary SQLite and a fake roster, no APIs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hermes_zalo.tickets.providers import (
    TicketsAccessDenied, TicketsSourceError, TicketsWriteUncertain,
    require_ticket_access, ticket_access_scope,
)
from hermes_zalo.tickets.service import DemoAssignments, TicketError, TicketService, TicketStore, TrustedAdminActor


class FakeAssignments:
    source_key = "fake-sheets:isolated-test:TicketsTest"

    def __init__(self, assignments):
        self.rows = {item["id"]: deepcopy(item) for item in assignments}
        self.calls = []
        self.behavior = "success"
        self.read_error = False

    def list_assignments(self):
        if self.read_error:
            raise TicketsSourceError("Nguồn thử tạm mất kết nối.")
        return deepcopy(list(self.rows.values()))

    def swap(self, a, b, ticket_id):
        self.calls.append(ticket_id)
        if self.behavior == "reject":
            raise TicketsSourceError("Nguồn thử từ chối ghi.")
        if self.behavior == "timeout_before":
            raise TicketsWriteUncertain("Chưa biết nguồn đã nhận lệnh hay chưa.")
        if self.rows.get(a["id"]) != a or self.rows.get(b["id"]) != b:
            raise TicketsSourceError("Ca đã thay đổi.")
        self.rows[a["id"]] = dict(a, member_id=b["member_id"], member_name=b["member_name"])
        if self.behavior != "partial":
            self.rows[b["id"]] = dict(b, member_id=a["member_id"], member_name=a["member_name"])
        if self.behavior == "corrupt_course":
            self.rows[a["id"]]["course"] = "Unexpected changed class"
        if self.behavior == "new_conflict":
            self.rows["concurrent-third-ca"] = dict(self.rows[a["id"]], id="concurrent-third-ca")
        if self.behavior == "readback_failure":
            self.read_error = True
        if self.behavior == "timeout_after":
            raise TicketsWriteUncertain("Nguồn đã ghi nhưng không nhận được phản hồi.")
        if self.behavior == "unexpected_after":
            raise RuntimeError("A private transport message must not escape")

    def close(self):
        pass


class TicketsServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "tickets.sqlite3"
        self.now = datetime.fromisoformat("2030-10-01T09:00:00+07:00").timestamp()
        self.store = TicketStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.demo = DemoAssignments(self.store, now=self.now)
        self.provider = FakeAssignments(self.demo.list_assignments())
        self.service = self.new_service()
        self.a = self.store.account("demo-a")
        self.b = self.store.account("demo-b")
        self.c = self.store.account("demo-c")
        self.manager = self.store.account("demo-manager")
        self.sequence = 0

    def new_service(self, **kwargs):
        return TicketService(self.store, self.provider, clock=lambda: self.now, **kwargs)

    def create(self, *, service=None, a="CA-11", b="CA-21", user=None):
        service = service or self.service
        user = user or self.a
        preview = service.preview(user, a, b)
        self.sequence += 1
        return service.create(user, preview["preview_token"], "Đổi ca thử", f"request-{self.sequence:016d}")

    def act(self, user, ticket, action, *, service=None):
        service = service or self.service
        detail = service.detail(user, ticket["id"])
        return service.action(user, ticket["id"], action, detail["version"])

    def claims(self):
        return self.store.db.execute("SELECT count(*) FROM exchange_claims").fetchone()[0]

    def test_two_parties_then_independent_manager_before_one_pair_write(self):
        before = deepcopy(self.provider.rows)
        ticket = self.create()
        self.assertEqual("pending_peer", ticket["status"])
        self.assertEqual(2, self.claims())
        self.assertEqual([], self.provider.calls)
        waiting = self.act(self.b, ticket, "accept")
        self.assertEqual("pending_manager", waiting["status"])
        self.assertEqual([], self.provider.calls)
        done = self.act(self.manager, ticket, "approve")
        self.assertEqual("completed", done["status"])
        self.assertEqual([ticket["id"]], self.provider.calls)
        self.assertEqual(0, self.claims())
        self.assertEqual(before["CA-21"]["member_id"], self.provider.rows["CA-11"]["member_id"])
        self.assertEqual(before["CA-11"]["member_id"], self.provider.rows["CA-21"]["member_id"])
        for identity in ("CA-11", "CA-21"):
            for field in ("id", "start", "end", "course", "room", "period"):
                self.assertEqual(before[identity][field], self.provider.rows[identity][field])
        for identity in set(before) - {"CA-11", "CA-21"}:
            self.assertEqual(before[identity], self.provider.rows[identity])
        self.assertGreaterEqual(len(done["events"]), 4)

    def test_peer_confirmation_completes_when_manager_explicitly_disabled(self):
        service = self.new_service(require_manager=False)
        ticket = self.create(service=service)
        done = self.act(self.b, ticket, "accept", service=service)
        self.assertEqual("completed", done["status"])
        self.assertEqual(1, len(self.provider.calls))

    def test_forged_identity_fields_do_not_grant_ticket_access(self):
        ticket = self.create()
        forged = dict(self.c, role="manager", member_id=self.b["member_id"])
        with self.assertRaises(TicketError) as cm:
            self.service.detail(forged, ticket["id"])
        self.assertEqual(404, cm.exception.status)
        self.assertEqual([], self.service.bootstrap(self.c)["tickets"])
        self.assertNotIn("password", self.service.bootstrap(self.a)["user"])
        for user, action in ((self.a, "accept"), (self.manager, "approve"), (self.b, "cancel")):
            with self.subTest(action=action), self.assertRaises(TicketError):
                self.service.action(user, ticket["id"], action, ticket["version"])
        self.assertEqual([], self.provider.calls)

    def test_manager_involved_in_exchange_cannot_self_approve(self):
        with self.store.transaction() as db:
            db.execute("UPDATE ticket_accounts SET role='manager' WHERE id='demo-b'")
        ticket = self.create()
        waiting = self.act(self.b, ticket, "accept")
        actions = self.service.detail(self.b, ticket["id"])["actions"]
        self.assertNotIn("approve", actions)
        with self.assertRaises(TicketError):
            self.service.action(self.b, ticket["id"], "approve", waiting["version"])
        self.assertEqual("completed", self.act(self.manager, ticket, "approve")["status"])

    def test_only_own_future_ca_with_known_hours_and_verified_recipient(self):
        for first, second in (("CA-21", "CA-31"), ("CA-11", "CA-11"), ("CA-11", "CA-12")):
            with self.subTest(first=first, second=second), self.assertRaises(TicketError):
                self.service.preview(self.a, first, second)
        original = deepcopy(self.provider.rows["CA-11"])
        self.provider.rows["CA-11"]["start"] = "2020-01-01T08:00:00+07:00"
        with self.assertRaises(TicketError):
            self.service.preview(self.a, "CA-11", "CA-21")
        self.provider.rows["CA-11"] = dict(original, start="2030-10-03T08:00:00")
        with self.assertRaises(TicketError):
            self.service.preview(self.a, "CA-11", "CA-21")
        self.provider.rows["CA-11"] = original
        with self.store.transaction() as db:
            db.execute("UPDATE ticket_accounts SET active=0 WHERE id='demo-b'")
        with self.assertRaises(TicketError):
            self.service.preview(self.a, "CA-11", "CA-21")
        self.assertEqual([], self.provider.calls)

    def test_preview_is_bound_to_author_expiration_and_latest_content(self):
        preview = self.service.preview(self.a, "CA-11", "CA-21")
        with self.assertRaises(TicketError):
            self.service.create(self.b, preview["preview_token"], "", "request-cross-user-0001")
        self.provider.rows["CA-11"]["room"] = "Other lab"
        with self.assertRaises(TicketError):
            self.service.create(self.a, preview["preview_token"], "", "request-stale-data-0001")
        preview = self.service.preview(self.a, "CA-11", "CA-21")
        self.now += 901
        with self.assertRaises(TicketError):
            self.service.create(self.a, preview["preview_token"], "", "request-expired-000001")
        self.assertEqual(0, self.claims())

    def test_duplicate_create_returns_same_ticket_and_changed_payload_is_rejected(self):
        preview = self.service.preview(self.a, "CA-11", "CA-21")
        args = (self.a, preview["preview_token"], "Một yêu cầu", "idempotency-key-000001")
        ticket = self.service.create(*args)
        retry = self.service.create(*args)
        self.assertEqual(ticket["id"], retry["id"])
        self.assertEqual(len(ticket["events"]), len(retry["events"]))
        self.assertEqual(1, self.store.db.execute("SELECT count(*) FROM exchange_tickets").fetchone()[0])
        with self.assertRaises(TicketError):
            self.service.create(self.a, preview["preview_token"], "Nội dung khác", args[-1])

    def test_simultaneous_duplicate_create_has_one_ticket(self):
        preview = self.service.preview(self.a, "CA-11", "CA-21")
        def create(_):
            return self.service.create(self.a, preview["preview_token"], "", "parallel-idem-00000001")["id"]
        with ThreadPoolExecutor(max_workers=2) as executor:
            ids = list(executor.map(create, range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(2, self.claims())

    def test_two_previews_cannot_claim_same_shift(self):
        first = self.service.preview(self.a, "CA-11", "CA-21")
        second = self.service.preview(self.a, "CA-11", "CA-31")
        ticket = self.service.create(self.a, first["preview_token"], "", "first-ticket-00000001")
        with self.assertRaises(TicketError):
            self.service.create(self.a, second["preview_token"], "", "other-ticket-00000001")
        self.assertEqual(2, self.claims())
        self.assertEqual(1, self.store.db.execute("SELECT count(*) FROM exchange_tickets").fetchone()[0])
        self.act(self.a, ticket, "cancel")
        self.assertEqual(0, self.claims())
        self.assertIn("preview_token", self.service.preview(self.a, "CA-11", "CA-31"))

    def test_cancel_reject_and_expire_do_not_write_and_release_claims(self):
        before = deepcopy(self.provider.rows)
        cancelled = self.act(self.a, self.create(), "cancel")
        self.assertEqual("cancelled", cancelled["status"])
        rejected = self.act(self.b, self.create(), "reject")
        self.assertEqual("rejected", rejected["status"])
        expired_ticket = self.create()
        self.now += 49 * 3600
        expired = self.service.detail(self.a, expired_ticket["id"])
        self.assertEqual("expired", expired["status"])
        self.assertEqual([], expired["actions"])
        self.assertEqual(0, self.claims())
        self.assertEqual([], self.provider.calls)
        self.assertEqual(before, self.provider.rows)

    def test_stale_version_and_repeated_accept_never_write(self):
        ticket = self.create()
        with self.assertRaises(TicketError):
            self.service.action(self.b, ticket["id"], "accept", ticket["version"] + 1)
        self.act(self.b, ticket, "accept")
        with self.assertRaises(TicketError):
            self.service.action(self.b, ticket["id"], "accept", ticket["version"])
        self.assertEqual([], self.provider.calls)

    def test_parallel_final_approval_only_crosses_write_boundary_once(self):
        ticket = self.create()
        waiting = self.act(self.b, ticket, "accept")
        def approve(_):
            try:
                return self.service.action(self.manager, ticket["id"], "approve", waiting["version"])["status"]
            except TicketError:
                return "rejected_stale_action"
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(approve, range(2)))
        self.assertEqual(["completed", "rejected_stale_action"], sorted(results))
        self.assertEqual([ticket["id"]], self.provider.calls)

    def test_changed_assignment_marks_ticket_stale_without_writing(self):
        ticket = self.create()
        self.provider.rows["CA-11"]["room"] = "Moved room"
        stale = self.act(self.b, ticket, "accept")
        self.assertEqual("stale", stale["status"])
        self.assertEqual([], self.provider.calls)
        self.assertEqual(0, self.claims())

    def test_new_third_shift_conflict_blocks_approval(self):
        ticket = self.create()
        self.act(self.b, ticket, "accept")
        self.provider.rows["third"] = dict(self.provider.rows["CA-11"], id="third",
                                           member_id=self.b["member_id"], member_name=self.b["name"])
        stale = self.act(self.manager, ticket, "approve")
        self.assertEqual("stale", stale["status"])
        self.assertEqual([], self.provider.calls)

    def test_revoked_creator_authority_is_checked_again_before_approval(self):
        ticket = self.create()
        self.act(self.b, ticket, "accept")
        with self.store.transaction() as db:
            db.execute("UPDATE ticket_accounts SET active=0 WHERE id='demo-a'")
        stale = self.act(self.manager, ticket, "approve")
        self.assertEqual("stale", stale["status"])
        self.assertEqual([], self.provider.calls)
        with self.assertRaises(TicketError) as cm:
            self.service.detail(self.a, ticket["id"])
        self.assertEqual(401, cm.exception.status)

    def test_rebound_recipient_after_consent_requires_new_ticket(self):
        ticket = self.create()
        self.act(self.b, ticket, "accept")
        with self.store.transaction() as db:
            db.execute("UPDATE ticket_accounts SET active=0 WHERE id='demo-b'")
            db.execute("INSERT INTO ticket_accounts VALUES (?,?,?,?,?,1)",
                       ("replacement-b", "Người mới", "member", "coach-b", "!test"))
        stale = self.act(self.manager, ticket, "approve")
        self.assertEqual("stale", stale["status"])
        self.assertEqual([], self.provider.calls)

    def test_changed_destination_never_redirects_existing_approval(self):
        ticket = self.create()
        self.act(self.b, ticket, "accept")
        self.provider.source_key = "fake-sheets:other-workbook:TicketsTest"
        changed = self.new_service()
        stale = self.act(self.manager, ticket, "approve", service=changed)
        self.assertEqual("stale", stale["status"])
        self.assertEqual([], self.provider.calls)

    def test_enabling_manager_policy_cannot_leave_old_ticket_auto_approved(self):
        old_service = self.new_service(require_manager=False)
        ticket = self.create(service=old_service)
        preview = old_service.preview(self.a, 'CA-12', 'CA-22')
        tightened = self.new_service(require_manager=True)
        result = tightened.detail(self.b, ticket['id'])
        self.assertEqual('stale', result['status'])
        self.assertIn('Admin', result['message'])
        self.assertEqual(ticket['version'] + 1, result['version'])
        self.assertEqual([], result['actions'])
        self.assertEqual(0, self.claims())
        with self.assertRaises(TicketError):
            self.act(self.b, ticket, "accept", service=tightened)
        with self.assertRaises(TicketError):
            tightened.create(self.a, preview['preview_token'], '', 'migrated-preview-000001')
        self.assertEqual([], self.provider.calls)

    def test_admin_waits_for_peer_and_does_not_create_ticket_accounts(self):
        actor = TrustedAdminActor('web:operator', 'Admin hiển thị', lambda: True)
        accounts = self.store.db.execute('SELECT count(*) FROM ticket_accounts').fetchone()[0]
        ticket = self.create()
        self.provider.read_error = True
        listing = self.service.admin_list(actor)
        self.assertEqual(ticket['id'], listing[0]['id'])
        self.assertEqual([], listing[0]['actions'])
        with self.assertRaises(TicketError):
            self.service.admin_action(actor, ticket['id'], 'approve', ticket['version'])
        self.provider.read_error = False
        waiting = self.act(self.b, ticket, 'accept')
        self.assertIn('Admin', waiting['status_label'])
        self.assertEqual([], self.provider.calls)
        completed = self.service.admin_action(actor, ticket['id'], 'approve', waiting['version'])
        self.assertEqual('completed', completed['status'])
        self.assertEqual([ticket['id']], self.provider.calls)
        self.assertIn('admin:web:operator', [event['actor'] for event in completed['events']])
        self.assertEqual(accounts, self.store.db.execute('SELECT count(*) FROM ticket_accounts').fetchone()[0])

    def test_admin_identity_cannot_be_supplied_as_ordinary_user_fields(self):
        ticket = self.create()
        forged = dict(self.c, role='admin', actor_id='web:operator', authorize=lambda: True)
        with self.assertRaises(TicketError):
            self.service.admin_list(forged)
        with self.assertRaises(TicketError):
            self.service.detail(forged, ticket['id'])
        for guard in (None, lambda: False, lambda: 1):
            with self.subTest(guard=guard), self.assertRaises(TicketError):
                self.service.admin_list(TrustedAdminActor('web:operator', 'Admin', guard))
        self.assertEqual([], self.provider.calls)

    def test_admin_listing_expires_once_and_defers_event_history_to_detail(self):
        actor = TrustedAdminActor('web:operator', 'Admin', lambda: True)
        ticket = self.create()
        self.create(a='CA-12', b='CA-22')
        statements = []
        self.store.db.set_trace_callback(statements.append)
        try:
            with patch.object(self.service, '_expire', wraps=self.service._expire) as expire:
                listing = self.service.admin_list(actor)
                self.assertEqual(1, expire.call_count)
        finally:
            self.store.db.set_trace_callback(None)
        self.assertEqual(2, len(listing))
        self.assertTrue(all(item['events'] == [] for item in listing))
        self.assertFalse(any('FROM exchange_events' in sql for sql in statements))
        self.assertTrue(self.service.admin_detail(actor, ticket['id'])['events'])

    def test_linked_admin_cannot_approve_their_own_exchange(self):
        ticket = self.create()
        waiting = self.act(self.b, ticket, 'accept')
        for identity in ({'account_id': self.a['id']}, {'account_id': self.b['id']}, {'member_id': self.a['member_id']},
                         {'member_id': self.b['member_id']}):
            actor = TrustedAdminActor('web:participant', 'Admin', lambda: True, **identity)
            self.assertNotIn('approve', self.service.admin_detail(actor, ticket['id'])['actions'])
            with self.assertRaises(TicketError):
                self.service.admin_action(actor, ticket['id'], 'approve', waiting['version'])
        self.assertEqual([], self.provider.calls)

    def test_admin_reject_requires_and_audits_reason_without_writes(self):
        actor = TrustedAdminActor('web:operator', 'Admin', lambda: True)
        ticket = self.create()
        waiting = self.act(self.b, ticket, 'accept')
        before = deepcopy(self.provider.rows)
        for reason in ('', '  ', None, 'x' * 601):
            with self.subTest(reason=reason), self.assertRaises(TicketError):
                self.service.admin_action(actor, ticket['id'], 'reject', waiting['version'], reason)
        rejected = self.service.admin_action(actor, ticket['id'], 'reject', waiting['version'], 'Thiếu người trực')
        self.assertEqual('rejected', rejected['status'])
        self.assertIn('Thiếu người trực', rejected['message'])
        self.assertEqual('admin:web:operator', rejected['events'][-1]['actor'])
        self.assertEqual(before, self.provider.rows)
        self.assertEqual([], self.provider.calls)
        self.assertEqual(0, self.claims())

    def test_two_admin_approvals_serialize_to_exactly_one_write(self):
        actor = TrustedAdminActor('web:operator', 'Admin', lambda: True)
        ticket = self.create()
        waiting = self.act(self.b, ticket, 'accept')
        with self.assertRaises(TicketError):
            self.service.admin_action(actor, ticket['id'], 'approve', ticket['version'])

        def approve(_):
            try:
                return self.service.admin_action(actor, ticket['id'], 'approve', waiting['version'])['status']
            except TicketError:
                return 'conflict'

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(approve, range(2)))
        self.assertEqual(['completed', 'conflict'], sorted(results))
        self.assertEqual([ticket['id']], self.provider.calls)

    def test_admin_approval_revalidates_pair_after_peer_consent(self):
        actor = TrustedAdminActor('web:operator', 'Admin', lambda: True)
        ticket = self.create()
        waiting = self.act(self.b, ticket, 'accept')
        self.provider.rows['CA-11']['room'] = 'Changed after consent'
        stale = self.service.admin_action(actor, ticket['id'], 'approve', waiting['version'])
        self.assertEqual('stale', stale['status'])
        self.assertEqual([], self.provider.calls)

    def test_admin_guard_preserves_runtime_guard_and_resets_after_call(self):
        actor = TrustedAdminActor('web:operator', 'Admin', lambda: True)
        ticket = self.create()
        waiting = self.act(self.b, ticket, 'accept')
        with ticket_access_scope(lambda: False), self.assertRaises(TicketsAccessDenied):
            self.service.admin_action(actor, ticket['id'], 'approve', waiting['version'])
        self.assertEqual('pending_manager', self.service.detail(self.b, ticket['id'])['status'])
        self.assertEqual([], self.provider.calls)
        require_ticket_access()

    def test_admin_revocation_at_provider_boundary_prevents_external_write(self):
        active = True
        actor = TrustedAdminActor('web:operator', 'Admin', lambda: active)
        ticket = self.create()
        waiting = self.act(self.b, ticket, 'accept')
        swap = self.provider.swap

        def guarded_swap(a, b, ticket_id):
            nonlocal active
            active = False  # For example, revocation during credential refresh.
            require_ticket_access()
            swap(a, b, ticket_id)

        self.provider.swap = guarded_swap
        with self.assertRaises(TicketError):
            self.service.admin_action(actor, ticket['id'], 'approve', waiting['version'])
        self.assertEqual([], self.provider.calls)
        self.assertEqual('stale', self.service.detail(self.b, ticket['id'])['status'])
        require_ticket_access()

    def test_admin_uncertain_write_reconciles_without_reposting(self):
        actor = TrustedAdminActor('web:operator', 'Admin', lambda: True)
        ticket = self.create()
        waiting = self.act(self.b, ticket, 'accept')
        self.provider.behavior = 'timeout_after'
        uncertain = self.service.admin_action(actor, ticket['id'], 'approve', waiting['version'])
        self.assertEqual('uncertain', uncertain['status'])
        done = self.service.admin_action(actor, ticket['id'], 'reconcile', uncertain['version'])
        self.assertEqual('completed', done['status'])
        self.assertEqual('admin:web:operator', done['events'][-1]['actor'])
        self.assertEqual([ticket['id']], self.provider.calls)

    def test_admin_revocation_during_manual_reconciliation_preserves_uncertainty(self):
        active = True
        actor = TrustedAdminActor('web:operator', 'Admin', lambda: active)
        ticket = self.create()
        waiting = self.act(self.b, ticket, 'accept')
        self.provider.behavior = 'timeout_after'
        uncertain = self.service.admin_action(actor, ticket['id'], 'approve', waiting['version'])
        read = self.provider.list_assignments

        def revoke_and_read():
            nonlocal active
            active = False
            return read()

        self.provider.list_assignments = revoke_and_read
        with self.assertRaises(TicketsAccessDenied):
            self.service.admin_action(actor, ticket['id'], 'reconcile', uncertain['version'])
        detail = self.service.detail(self.b, ticket['id'])
        self.assertEqual('uncertain', detail['status'])
        self.assertEqual(uncertain['version'], detail['version'])
        self.assertEqual([ticket['id']], self.provider.calls)

    def test_policy_migration_preserves_uncertain_ticket_and_claims(self):
        legacy = self.new_service(require_manager=False)
        ticket = self.create(service=legacy)
        self.provider.behavior = 'timeout_after'
        uncertain = self.act(self.b, ticket, 'accept', service=legacy)
        self.assertEqual('uncertain', uncertain['status'])
        tightened = self.new_service(require_manager=True)
        actor = TrustedAdminActor('web:operator', 'Admin', lambda: True)
        preserved = tightened.admin_detail(actor, ticket['id'])
        self.assertEqual('uncertain', preserved['status'])
        self.assertEqual(uncertain['version'], preserved['version'])
        self.assertEqual(2, self.claims())
        done = tightened.admin_action(actor, ticket['id'], 'reconcile', preserved['version'])
        self.assertEqual('completed', done['status'])
        self.assertEqual([ticket['id']], self.provider.calls)

    def test_source_read_failure_preserves_pending_ticket_for_retry(self):
        ticket = self.create()
        self.provider.read_error = True
        with self.assertRaises(TicketError) as cm:
            self.act(self.b, ticket, "accept")
        self.assertEqual(503, cm.exception.status)
        detail = self.service.detail(self.a, ticket["id"])
        self.assertEqual("pending_peer", detail["status"])
        self.assertEqual(2, self.claims())
        bootstrap = self.service.bootstrap(self.a)
        self.assertTrue(bootstrap["source_error"])
        self.assertEqual(ticket["id"], bootstrap["tickets"][0]["id"])

    def test_timeout_after_write_reconciles_read_only_without_duplicate_post(self):
        ticket = self.create()
        self.act(self.b, ticket, "accept")
        self.provider.behavior = "timeout_after"
        uncertain = self.act(self.manager, ticket, "approve")
        self.assertEqual("uncertain", uncertain["status"])
        self.assertEqual(2, self.claims())
        self.assertNotIn("reconcile", self.service.detail(self.a, ticket["id"])["actions"])
        done = self.act(self.manager, ticket, "reconcile")
        self.assertEqual("completed", done["status"])
        self.assertEqual([ticket["id"]], self.provider.calls)
        self.assertEqual(0, self.claims())

    def test_timeout_before_write_never_auto_retries_even_after_reconcile(self):
        ticket = self.create()
        self.act(self.b, ticket, "accept")
        before = deepcopy(self.provider.rows)
        self.provider.behavior = "timeout_before"
        self.assertEqual("uncertain", self.act(self.manager, ticket, "approve")["status"])
        for _ in range(2):
            self.assertEqual("uncertain", self.act(self.manager, ticket, "reconcile")["status"])
        self.assertEqual([ticket["id"]], self.provider.calls)
        self.assertEqual(before, self.provider.rows)
        self.assertEqual(2, self.claims())

    def test_partial_or_corrupted_readback_does_not_complete_or_unlock(self):
        for behavior in ("partial", "corrupt_course", "readback_failure", "new_conflict"):
            with self.subTest(behavior=behavior):
                # Each subcase gets an independent durable store and ticket.
                with tempfile.TemporaryDirectory() as directory:
                    store = TicketStore(Path(directory) / "tickets.db")
                    try:
                        demo = DemoAssignments(store, now=self.now)
                        provider = FakeAssignments(demo.list_assignments())
                        service = TicketService(store, provider, clock=lambda: self.now)
                        preview = service.preview(self.a, "CA-11", "CA-21")
                        ticket = service.create(self.a, preview["preview_token"], "", "readback-check-000001")
                        waiting = service.action(self.b, ticket["id"], "accept", ticket["version"])
                        provider.behavior = behavior
                        detail = service.action(self.manager, ticket["id"], "approve", waiting["version"])
                        self.assertEqual("uncertain", detail["status"])
                        self.assertEqual(2, store.db.execute("SELECT count(*) FROM exchange_claims").fetchone()[0])
                        self.assertEqual(1, len(provider.calls))
                    finally:
                        store.close()

    def test_unexpected_exception_after_write_is_safe_and_recoverable(self):
        ticket = self.create()
        self.act(self.b, ticket, "accept")
        self.provider.behavior = "unexpected_after"
        uncertain = self.act(self.manager, ticket, "approve")
        self.assertEqual("uncertain", uncertain["status"])
        self.assertNotIn("private transport", str(uncertain))
        self.assertEqual("completed", self.act(self.manager, ticket, "reconcile")["status"])
        self.assertEqual(1, len(self.provider.calls))

    def test_restart_marks_applying_uncertain_and_never_reissues_write(self):
        ticket = self.create()
        self.act(self.b, ticket, "accept")
        with self.store.transaction() as db:
            db.execute("UPDATE exchange_tickets SET status='applying',version=version+1 WHERE id=?", (ticket["id"],))
        # Google accepted a write, then the process stopped before recording success.
        self.provider.swap(ticket["a"], ticket["b"], ticket["id"])
        self.store.close()
        self.store = TicketStore(self.path)
        restarted = self.new_service()
        recovered = restarted.detail(self.manager, ticket["id"])
        self.assertEqual("uncertain", recovered["status"])
        self.assertEqual(2, self.claims())
        done = self.act(self.manager, ticket, "reconcile", service=restarted)
        self.assertEqual("completed", done["status"])
        self.assertEqual([ticket["id"]], self.provider.calls)

    def test_demo_roster_and_finished_tickets_persist_across_restart(self):
        demo_service = TicketService(self.store, self.demo, clock=lambda: self.now)
        ticket = self.create(service=demo_service)
        self.act(self.b, ticket, "accept", service=demo_service)
        self.act(self.manager, ticket, "approve", service=demo_service)
        after = self.demo.list_assignments()
        self.store.close()
        self.store = TicketStore(self.path)
        restarted_demo = DemoAssignments(self.store, now=self.now + 86400)
        self.assertEqual(after, restarted_demo.list_assignments())
        restarted = TicketService(self.store, restarted_demo, clock=lambda: self.now)
        self.assertEqual("completed", restarted.detail(self.a, ticket["id"])["status"])


if __name__ == "__main__":
    unittest.main()
