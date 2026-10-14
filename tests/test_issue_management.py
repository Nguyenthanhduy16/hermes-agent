from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import json
import tempfile
import threading
import unittest

from hermes_zalo.events import IncomingMessage
from hermes_zalo.issue_management import IssueError, ManagedIssueStore, preview
from hermes_zalo.issues import IssueStore
from hermes_zalo.issue_migration import import_snapshot, registration_resolver


ADMIN = {'username': 'admin.one', 'role': 'admin', 'permissions': ['issues']}
OTHER = {'username': 'admin.two', 'role': 'admin', 'permissions': ['issues']}
FACULTY = {'username': 'lecturer', 'role': 'faculty', 'permissions': []}


def message(user='u1', chat='dm1', kind='PRIVATE', mid='m1'):
    return IncomingMessage(chat_id=chat, user_id=user, message_id=mid, chat_type=kind, text='/issue tao lỗi | mô tả')


class ManagedIssueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'work.issues.sqlite3'
        self.now = 1000
        self.store = ManagedIssueStore(self.path, clock=lambda: self.now)
        self.addCleanup(self.store.close)

    def draft(self, event=None, member='member-1', kind='support'):
        return self.store.create(event or message(), member, 'Máy chiếu không chạy', 'Đã kiểm tra nguồn điện.', kind, access_guard=lambda: True)

    def issue(self, event=None, member='member-1', kind='support'):
        event = event or message()
        draft = self.draft(event, member, kind)
        return self.store.confirm(draft['id'], event, member, access_guard=lambda: True)

    def action(self, issue, action, actor=ADMIN, **payload):
        detail = self.store.detail(issue['id'], actor)
        return self.store.act(issue['id'], actor, action, detail['issue']['revision'], **payload)

    def test_draft_is_readonly_and_confirm_once_across_restart(self):
        draft = self.draft()
        self.assertIn('chưa tạo', preview(draft))
        self.assertEqual(0, self.store.list_issues(ADMIN)['total'])
        issue = self.store.confirm(draft['id'], message(), 'member-1', access_guard=lambda: True)
        self.assertEqual('ISS-000001', issue['id'])
        other = ManagedIssueStore(self.path)
        try:
            repeated = other.confirm(draft['id'], message(), 'member-1', access_guard=lambda: True)
            self.assertEqual(issue, repeated)
            self.assertEqual(1, other.list_issues(ADMIN)['total'])
            self.assertEqual('create', other.detail(issue['id'], ADMIN)['audit'][0]['action'])
        finally:
            other.close()

    def test_duplicate_event_preserves_content_and_conflicting_member_is_rejected(self):
        draft = self.draft()
        repeated = self.draft()
        self.assertEqual(draft, repeated)
        with self.assertRaises(IssueError):
            self.store.create(message(), 'member-1', 'different', 'different', 'feature', access_guard=lambda: True)
        with self.assertRaises(IssueError):
            self.draft(member='different-person')

    def test_confirm_and_cancel_require_same_user_member_chat_type(self):
        draft = self.draft(message(chat='group', kind='GROUP'))
        for event, member in ((message(user='u2', chat='group', kind='GROUP'), 'member-1'),
                              (message(chat='other', kind='GROUP'), 'member-1'),
                              (message(chat='group'), 'member-1'),
                              (message(chat='group', kind='GROUP'), 'member-2')):
            with self.subTest(event=event, member=member):
                with self.assertRaises(IssueError):
                    self.store.confirm(draft['id'], event, member, access_guard=lambda: True)
                self.assertFalse(self.store.cancel(draft['id'], event, member, access_guard=lambda: True))

    def test_permission_rechecked_when_confirming_and_no_implicit_guard(self):
        with self.assertRaises(IssueError):
            self.store.create(message(), 'member-1', 't', 'b')
        draft = self.draft()
        with self.assertRaises(IssueError):
            self.store.confirm(draft['id'], message(), 'member-1', access_guard=lambda: False)
        self.assertEqual(0, self.store.list_issues(ADMIN)['total'])

    def test_cancel_expiry_and_target_change_cannot_create(self):
        first = self.draft()
        self.assertTrue(self.store.cancel(first['id'], message(), 'member-1', access_guard=lambda: True))
        with self.assertRaises(IssueError):
            self.store.confirm(first['id'], message(), 'member-1', access_guard=lambda: True)
        expired = self.draft(message(mid='expired'))
        self.now += 600
        with self.assertRaises(IssueError):
            self.store.confirm(expired['id'], message(mid='expired'), 'member-1', access_guard=lambda: True)
        moved = self.draft(message(mid='moved'))
        with self.store.transaction() as db:
            db.execute("UPDATE managed_issue_drafts SET target='changed' WHERE id=?", (moved['id'],))
        with self.assertRaises(IssueError):
            self.store.confirm(moved['id'], message(mid='moved'), 'member-1', access_guard=lambda: True)

    def test_two_connections_claim_atomically_with_one_winner_and_one_notice(self):
        issue = self.issue()
        barrier = threading.Barrier(2)
        def claim(actor):
            connection = ManagedIssueStore(self.path)
            try:
                barrier.wait()
                return connection.act(issue['id'], actor, 'claim', 1)['issue']['assignee']
            except IssueError as error:
                return error.status
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, [ADMIN, OTHER]))
        self.assertEqual(1, results.count(409))
        self.assertEqual(1, len(self.store.detail(issue['id'], ADMIN)['notifications']))
        self.assertIn(self.store.detail(issue['id'], ADMIN)['issue']['assignee'], ['admin.one', 'admin.two'])

    def test_authorization_blocks_non_admin_bad_permission_and_forged_roles(self):
        issue = self.issue()
        actors = [None, {}, {'username': 'u', 'role': 'member', 'permissions': ['issues']},
                  {'username': 'u', 'role': 'admin', 'permissions': ['members']},
                  {**ADMIN, 'active': False}]
        for actor in actors:
            with self.subTest(actor=actor), self.assertRaises(IssueError):
                self.store.act(issue['id'], actor, 'claim', 1)

    def test_only_faculty_can_assign_to_verified_eligible_target(self):
        issue = self.issue()
        with self.assertRaises(IssueError):
            self.action(issue, 'assign', assignee='admin.two', reason='Bàn giao', target_actor=OTHER)
        with self.assertRaises(IssueError):
            self.action(issue, 'assign', FACULTY, assignee='wrong', reason='Bàn giao', target_actor=OTHER)
        with self.assertRaises(IssueError):
            self.action(issue, 'assign', FACULTY, assignee='admin.two', reason='Bàn giao', target_actor={**OTHER, 'active': False})
        result = self.action(issue, 'assign', FACULTY, assignee='admin.two', reason='Phụ trách thiết bị', target_actor=OTHER)
        self.assertEqual('admin.two', result['issue']['assignee'])
        self.assertEqual('in_progress', result['issue']['status'])
        self.assertEqual('Phụ trách thiết bị', result['audit'][-1]['details']['reason'])
        with self.assertRaises(IssueError):
            self.action(issue, 'status', status='waiting', text='Bạn đang ở phòng nào?')
        self.action(issue, 'status', OTHER, status='waiting', text='Bạn đang ở phòng nào?')

    def test_transfer_reason_required_and_notes_do_not_change_owner(self):
        issue = self.issue()
        self.action(issue, 'claim')
        self.action(issue, 'comment', OTHER, text='Tôi có thể hỗ trợ.', visibility='internal')
        with self.assertRaises(IssueError):
            self.action(issue, 'assign', FACULTY, assignee='admin.two', reason='', target_actor=OTHER)
        changed = self.action(issue, 'assign', FACULTY, assignee='admin.two', reason='Bàn giao ca', target_actor=OTHER)
        self.assertEqual('admin.one', changed['audit'][-1]['details']['from'])
        self.assertEqual({'admin.two': 1}, self.store.counts_by_assignee(FACULTY))

    def test_public_reply_is_explicit_and_internal_note_never_reaches_member(self):
        issue = self.issue()
        self.action(issue, 'claim')
        self.action(issue, 'comment', OTHER, visibility='internal', text='PRIVATE NOTE: equipment password')
        with self.assertRaises(IssueError) as denied:
            self.action(issue, 'comment', OTHER, visibility='public', text='Mình đang kiểm tra thiết bị.')
        self.assertEqual(403, denied.exception.status)
        self.action(issue, 'comment', ADMIN, visibility='public', text='Mình đang kiểm tra thiết bị.')
        member = self.store.member_detail(issue['id'], 'member-1')
        self.assertNotIn('PRIVATE NOTE', str(member))
        self.assertEqual(['Mình đang kiểm tra thiết bị.'], [c['body'] for c in member['comments']])
        self.assertNotIn('user_id', member)
        notices = []
        while notice := self.store.claim_notification(lambda _: True):
            notices.append(notice)
            self.store.finish_notification(notice['id'], 'sent')
        self.assertEqual(2, len(notices))
        self.assertNotIn('PRIVATE NOTE', str(notices))
        self.assertIn('Mình đang kiểm tra thiết bị.', notices[1]['text'])

    def test_member_identity_not_name_controls_visibility(self):
        first = self.issue()
        second = self.issue(message(user='u2', chat='dm2', mid='m2'), 'member-2')
        self.assertEqual(1, self.store.member_list('member-1')['total'])
        with self.assertRaises(IssueError):
            self.store.member_detail(second['id'], 'member-1')
        with self.assertRaises(IssueError):
            self.store.member_detail(first['id'], 'member-2')

    def test_waiting_done_need_public_reply_and_reopening_requires_reason(self):
        issue = self.issue()
        self.action(issue, 'claim')
        with self.assertRaises(IssueError):
            self.action(issue, 'status', status='waiting')
        self.action(issue, 'status', status='waiting', text='Bạn bổ sung phòng nhé.')
        self.action(issue, 'status', status='done', text='Đã thay dây nguồn.')
        self.assertEqual({}, self.store.counts_by_assignee(FACULTY))
        with self.assertRaises(IssueError):
            self.action(issue, 'status', status='in_progress')
        with self.assertRaises(IssueError):
            self.action(issue, 'reopen', reason='')
        result = self.action(issue, 'reopen', reason='Thiết bị vẫn chưa hoạt động.')
        self.assertEqual('in_progress', result['issue']['status'])
        self.assertEqual(3, len(result['notifications']))

    def test_revision_blocks_duplicate_transitions_and_deduplicates_notifications(self):
        issue = self.issue()
        self.store.act(issue['id'], ADMIN, 'claim', 1)
        with self.assertRaises(IssueError):
            self.store.act(issue['id'], ADMIN, 'claim', 1)
        self.store.act(issue['id'], ADMIN, 'status', 2, status='done', text='Đã xử lý.')
        with self.assertRaises(IssueError):
            self.store.act(issue['id'], ADMIN, 'status', 2, status='done', text='Đã xử lý.')
        self.assertEqual(2, len(self.store.detail(issue['id'], ADMIN)['notifications']))

    def test_feature_plan_optional_and_validated(self):
        issue = self.issue(kind='feature')
        self.action(issue, 'claim')
        result = self.action(issue, 'plan', priority='high', estimate='2 ngày', due_date='2026-10-30')
        self.assertEqual('2 ngày', result['issue']['estimate'])
        self.assertEqual('2026-10-30', result['issue']['due_date'])
        for payload in ({'due_date': '2026-02-30'}, {'estimate': 'x' * 101}, {'priority': 'arbitrary'}):
            with self.assertRaises(IssueError):
                self.action(issue, 'plan', **payload)
        cleared = self.action(issue, 'plan')
        self.assertIsNone(cleared['issue']['estimate'])

    def test_filters_and_pagination_are_bounded(self):
        self.issue()
        second = self.issue(message(mid='m2'), kind='feature')
        self.action(second, 'claim')
        self.assertEqual(1, self.store.list_issues(ADMIN, kind='feature', assignee='admin.one')['total'])
        self.assertEqual(1, self.store.list_issues(ADMIN, assignee='unassigned')['total'])
        self.assertEqual(0, self.store.list_issues(ADMIN, q='%')['total'])
        self.assertEqual(1, len(self.store.member_list('member-1', page=2, limit=1)['items']))
        for value in (0, True, -1, 10001):
            with self.assertRaises(IssueError):
                self.store.list_issues(ADMIN, page=value)

    def test_private_and_group_destinations_are_preserved_and_revalidated(self):
        private = self.issue()
        group = self.issue(message(chat='g1', kind='GROUP', mid='group'))
        self.action(private, 'claim')
        self.action(group, 'claim')
        checked = []
        def allowed(row):
            checked.append((row['member_id'], row['user_id'], row['chat_id'], row['chat_type']))
            return row['chat_type'] == 'GROUP'
        notice = self.store.claim_notification(allowed)
        self.assertEqual(('member-1', 'u1', 'g1', 'GROUP'), checked[-1])
        self.assertEqual('GROUP', notice['chat_type'])
        self.assertEqual('blocked', self.store.detail(private['id'], ADMIN)['notifications'][0]['status'])
        self.assertTrue(self.store.finish_notification(notice['id'], 'failed', 'SECRET'))

    def test_interrupted_send_becomes_uncertain_without_automatic_retry(self):
        issue = self.issue()
        self.action(issue, 'claim')
        notice = self.store.claim_notification(lambda _: True)
        observer = ManagedIssueStore(self.path)
        try:
            self.assertEqual('sending', observer.detail(issue['id'], ADMIN)['notifications'][0]['status'])
            observer.recover()
            self.assertIsNone(observer.claim_notification(lambda _: True))
            self.assertEqual('uncertain', observer.detail(issue['id'], ADMIN)['notifications'][0]['status'])
            with self.assertRaises(IssueError):
                observer.retry_notification(notice['id'], FACULTY)
        finally:
            observer.close()

    def test_known_failed_retry_is_bounded_and_errors_sanitized(self):
        issue = self.issue()
        self.action(issue, 'claim')
        for attempt in range(1, 4):
            notice = self.store.claim_notification(lambda _: True)
            self.assertEqual(attempt, notice['attempts'])
            self.assertTrue(self.store.finish_notification(notice['id'], 'failed', 'PRIVATE upstream token'))
            state = self.store.detail(issue['id'], ADMIN)['notifications'][0]
            self.assertEqual('rejected', state['error_code'])
            if attempt < 3:
                self.store.retry_notification(notice['id'], ADMIN)
        with self.assertRaises(IssueError):
            self.store.retry_notification(notice['id'], ADMIN)

    def test_observer_does_not_reset_active_notification_claim(self):
        issue = self.issue()
        self.action(issue, 'claim')
        notice = self.store.claim_notification(lambda _: True)
        other = ManagedIssueStore(self.path)
        try:
            self.assertIsNone(other.claim_notification(lambda _: True))
            self.assertTrue(self.store.finish_notification(notice['id'], 'sent'))
            self.assertFalse(other.finish_notification(notice['id'], 'failed'))
        finally:
            other.close()

    def test_legacy_tables_are_preserved_and_import_is_idempotent(self):
        legacy = IssueStore(self.path)
        self.addCleanup(legacy.close)
        row = ['old123', '2026-10-01T00:00:00Z', 'Lỗi cũ', 'Mô tả cũ', 'open', 'u1', 'dm1', 'PRIVATE']
        draft = legacy.create(message(), row[2], row[3], 'sheet/Issues', now=100)
        with legacy.db:
            legacy.db.execute("UPDATE drafts SET status='created',id='old123' WHERE id=?", (draft['id'],))
        self.assertFalse(self.store.migration_report()['ready'])
        first = self.store.import_legacy([row], target='sheet/Issues', resolve_member=lambda _: 'member-1')
        second = self.store.import_legacy([row], target='sheet/Issues', resolve_member=lambda _: 'member-1')
        self.assertEqual(first, second)
        self.assertFalse(self.store.migration_report()['ready'])
        self.assertTrue(self.store.mark_source_reconciled('sheet/Issues', [row])['ready'])
        self.assertEqual('Lỗi cũ', self.store.member_detail('old123', 'member-1')['title'])
        self.assertEqual('created', legacy.scoped('old123', message())['status'])
        self.assertEqual('ISS-000002', self.issue(message(mid='new'))['id'])

    def test_legacy_ambiguous_owner_and_writes_block_cutover(self):
        legacy = IssueStore(self.path)
        self.addCleanup(legacy.close)
        draft = legacy.create(message(), 't', 'b', 'sheet/Issues', now=100)
        with legacy.db:
            legacy.db.execute("UPDATE drafts SET status='uncertain' WHERE id=?", (draft['id'],))
        row = [draft['id'], 'date', 't', 'b', 'open', 'u1', 'dm1', 'PRIVATE']
        self.store.import_legacy([row], target='sheet/Issues', resolve_member=lambda _: None)
        report = self.store.migration_report()
        self.assertEqual(1, report['uncertain_legacy_writes'])
        self.assertEqual(1, report['unresolved_owners'])
        self.assertFalse(report['ready'])
        self.assertEqual(0, self.store.member_list('u1')['total'])

    def test_changed_legacy_content_rolls_back_entire_import(self):
        original = ['old1', 'date', 't', 'b', 'open', 'u1', 'dm1', 'PRIVATE']
        self.store.import_legacy([original], target='sheet/Issues', resolve_member=lambda _: 'member-1')
        changed = original.copy()
        changed[2] = 'different title'
        new = ['old2', 'date', 'new', 'body', 'open', 'u1', 'dm1', 'PRIVATE']
        with self.assertRaises(IssueError):
            self.store.import_legacy([new, changed], target='sheet/Issues', resolve_member=lambda _: 'member-1')
        self.assertEqual(1, self.store.list_issues(ADMIN)['total'])

    def test_legacy_live_draft_blocks_cutover_until_expiry(self):
        legacy = IssueStore(self.path)
        self.addCleanup(legacy.close)
        legacy.create(message(), 't', 'b', 'sheet/Issues', now=self.now)
        self.assertEqual(1, self.store.migration_report()['pending_legacy_drafts'])
        self.now += 600
        self.assertTrue(self.store.mark_source_reconciled('sheet/Issues', [])['ready'])

    def test_member_reply_is_bound_idempotent_and_resumes_waiting(self):
        issue = self.issue()
        self.action(issue, 'claim')
        self.action(issue, 'status', status='waiting', text='Bổ sung tên phòng nhé.')
        event = message(mid='reply1')
        reply = self.store.member_reply(issue['id'], event, 'member-1', 'Phòng H201.', access_guard=lambda: True)
        repeated = self.store.member_reply(issue['id'], event, 'member-1', 'Phòng H201.', access_guard=lambda: True)
        self.assertEqual(reply, repeated)
        self.assertEqual('in_progress', reply['status'])
        self.assertEqual(2, len(reply['comments']))
        self.assertEqual(2, len(self.store.detail(issue['id'], ADMIN)['notifications']))
        for event, member in ((message(user='u2', mid='r2'), 'member-1'),
                              (message(chat='different', mid='r3'), 'member-1'),
                              (message(kind='GROUP', mid='r4'), 'member-1'),
                              (message(mid='r5'), 'member-2')):
            with self.assertRaises(IssueError):
                self.store.member_reply(issue['id'], event, member, 'Wrong account.', access_guard=lambda: True)
        with self.assertRaises(IssueError):
            self.store.member_reply(issue['id'], message(mid='reply1'), 'member-1', 'Different text.', access_guard=lambda: True)
        self.action(issue, 'status', status='done', text='Đã sửa rồi.')
        with self.assertRaises(IssueError):
            self.store.member_reply(issue['id'], message(mid='reply2'), 'member-1', 'More.', access_guard=lambda: True)

    def test_member_reply_denies_revoked_access_and_does_not_notify_self(self):
        issue = self.issue()
        with self.assertRaises(IssueError):
            self.store.member_reply(issue['id'], message(mid='r2'), 'member-1', 'Chi tiết.', access_guard=lambda: False)
        self.store.member_reply(issue['id'], message(mid='r2'), 'member-1', 'Chi tiết.', access_guard=lambda: True)
        self.assertEqual([], self.store.pending_notifications())

    def test_notification_claim_selected_id_does_not_block_other_pending(self):
        first = self.issue()
        second = self.issue(message(mid='another'))
        self.action(first, 'claim')
        self.action(second, 'claim')
        selected = self.store.pending_notifications(limit=1)[0]
        self.assertIsNone(self.store.claim_notification(lambda _: False, notification_id=selected['id']))
        remaining = self.store.pending_notifications()
        self.assertEqual(1, len(remaining))
        self.assertEqual(second['id'], remaining[0]['issue_id'])
        self.assertEqual(remaining[0]['id'], self.store.claim_notification(lambda _: True, notification_id=remaining[0]['id'])['id'])

    def test_migration_marker_requires_complete_imported_snapshot(self):
        row = ['old1', 'date', 't', 'b', 'open', 'u1', 'dm1', 'PRIVATE']
        self.assertFalse(self.store.migration_report()['source_reconciled'])
        with self.assertRaises(IssueError):
            self.store.mark_source_reconciled('sheet/Issues', [row])
        self.store.import_legacy([row], target='sheet/Issues', resolve_member=lambda _: 'member-1')
        with self.assertRaises(IssueError):
            self.store.mark_source_reconciled('sheet/Issues', [])
        self.assertTrue(self.store.mark_source_reconciled('sheet/Issues', [row])['ready'])

    def test_uncertain_legacy_write_reconciled_only_against_matching_source(self):
        legacy = IssueStore(self.path)
        self.addCleanup(legacy.close)
        draft = legacy.create(message(), 't', 'b', 'sheet/Issues', now=100)
        with legacy.db:
            legacy.db.execute("UPDATE drafts SET status='uncertain' WHERE id=?", (draft['id'],))
        row = [draft['id'], 'date', 't', 'b', 'open', 'u1', 'dm1', 'PRIVATE']
        self.store.import_legacy([row], target='sheet/Issues', resolve_member=lambda _: 'member-1')
        with self.assertRaises(IssueError):
            self.store.reconcile_legacy_writes('sheet/Issues', not_created=[draft['id']], reason='Checked')
        self.assertEqual(1, self.store.reconcile_legacy_writes('sheet/Issues', reason='Snapshot đầy đủ đã kiểm tra.'))
        self.assertTrue(self.store.mark_source_reconciled('sheet/Issues', [row])['ready'])

    def test_migration_cli_dry_run_and_apply_preserve_original_and_backup(self):
        snapshot = Path(self.temp.name) / 'snapshot.json'
        snapshot.write_text(json.dumps({'target': 'sheet/Issues', 'rows': []}), encoding='utf-8')
        report = import_snapshot(self.path, snapshot)
        self.assertTrue(report['ready'])
        self.assertFalse(report['applied'])
        self.assertFalse(self.store.migration_report()['source_reconciled'])
        with self.assertRaises(IssueError):
            import_snapshot(self.path, snapshot, apply=True)
        result = import_snapshot(self.path, snapshot, apply=True, bot_stopped=True, source_complete=True)
        self.assertTrue(result['applied'])
        self.assertTrue(self.store.migration_report()['ready'])
        backup = ManagedIssueStore(self.path.parent / result['backup_file'])
        try:
            self.assertFalse(backup.migration_report()['source_reconciled'])
        finally:
            backup.close()

    def test_migration_resolver_rejects_historical_account_rebinding(self):
        registry = Path(self.temp.name) / 'registrations.sqlite3'
        with sqlite3.connect(registry) as db:
            db.execute('CREATE TABLE registration_requests(user_id TEXT,chat_id TEXT,member_id TEXT,status TEXT)')
            db.executemany('INSERT INTO registration_requests VALUES (?,?,?,?)',
                           [('u1', 'dm1', 'old-member', 'revoked'), ('u1', 'dm1', 'new-member', 'approved'),
                            ('u2', 'dm2', 'stable-member', 'rejected'), ('u2', 'dm2', 'stable-member', 'approved')])
        db.close()
        resolve = registration_resolver(registry)
        self.assertIsNone(resolve({'user_id': 'u1', 'chat_id': 'dm1', 'chat_type': 'PRIVATE'}))
        self.assertEqual('stable-member', resolve({'user_id': 'u2', 'chat_id': 'dm2', 'chat_type': 'PRIVATE'}))
        self.assertIsNone(resolve({'user_id': 'u2', 'chat_id': 'different', 'chat_type': 'PRIVATE'}))
        self.assertEqual('stable-member', resolve({'user_id': 'u2', 'chat_id': 'group2', 'chat_type': 'GROUP'}))


if __name__ == '__main__':
    unittest.main()
