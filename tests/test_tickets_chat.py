"""Conversation acceptance tests: synthetic private Zalo messages, no APIs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hermes_zalo.events import IncomingMessage
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.chat import TicketsChat
from hermes_zalo.tickets.providers import TicketsWriteUncertain
from hermes_zalo.tickets.service import DemoAssignments, TicketService, TicketStore, digest, packed


class TicketsChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'chat.sqlite3'
        self.now = datetime.fromisoformat('2030-10-01T09:00:00+07:00').timestamp()
        self.scope = digest('synthetic-bot')
        self.seq = 0
        self.open()
        for who in ('a', 'b', 'c', 'manager'):
            self.ids.bind(self.scope, who, 'dm-' + who, 'demo-' + who)

    def open(self):
        self.store = TicketStore(self.path)
        self.provider = DemoAssignments(self.store, now=self.now)
        self.service = TicketService(self.store, self.provider, clock=lambda: self.now)
        self.ids = IdentityStore(self.store)
        self.chat = TicketsChat(self.service, self.ids, bot_scope=self.scope)

    def tearDown(self):
        self.chat.close()
        self.provider.close()
        self.store.close()
        self.temp.cleanup()

    def message(self, who, text, *, mid=None, kind='PRIVATE', chat=None):
        self.seq += 1
        return IncomingMessage(chat or 'dm-' + who, who, mid or str(self.seq), kind, text)

    def say(self, who, text, **kwargs):
        return self.chat.handle(self.message(who, text, **kwargs))

    def choose(self, who, response, item):
        scope = self.chat._scope(self.message(who, ''))
        body = json.loads(self.store.db.execute('SELECT body FROM ticket_chat_dialogs WHERE scope=?', (scope,)).fetchone()[0])
        choice = next(c for c in body['choices'] if c['item']['id'] == item)
        self.assertRegex(response, r'(?m)^' + re.escape(choice['code']) + r'\. ')
        return self.say(who, choice['code'])

    def draft(self, a='a', own='CA-11', peer='coach-b', other='CA-21'):
        response = self.say(a, '/doica')
        response = self.choose(a, response, own)
        response = self.choose(a, response, peer)
        response = self.choose(a, response, other)
        self.assertIn('Nhập lý do', response)
        response = self.say(a, 'Việc riêng — dữ liệu thử')
        for identity in (own, other):
            self.assertIn(self.service._source()[identity]['member_name'], response)
        return response

    def submit(self, who, response):
        command = re.search(r'/doica gui [A-F0-9]+', response)[0]
        response = self.say(who, command)
        self.assertIn('Đã gửi yêu cầu', response)
        return re.search(r'DC-[A-F0-9]+', response)[0], response

    def create(self, **kwargs):
        return self.submit(kwargs.get('a', 'a'), self.draft(**kwargs))

    def act(self, who, tid, command):
        detail = self.say(who, '/yeucau ' + tid)
        full = re.search(re.escape(command) + r' [0-9]{4}\b', detail)
        self.assertIsNotNone(full, detail)
        return self.say(who, full[0])

    def status(self, ticket):
        return self.store.db.execute('SELECT status FROM exchange_tickets WHERE id=?', (ticket,)).fetchone()[0]

    def test_private_a_b_manager_complete_flow_no_website(self):
        tid, response = self.create()
        self.assertEqual('pending_peer', self.status(tid))
        self.assertIn('Đang chờ Bình', response)
        self.assertIn('/choxacnhan', response)
        self.assertNotIn('/huydoica', response)
        self.assertIn(tid, self.say('b', 'Yêu cầu chờ tôi'))
        self.assertEqual('coach-a', self.service._source()['CA-11']['member_id'])
        self.assertIn('Chờ quản lý', self.act('b', tid, '/dongy'))
        self.assertIn(tid, self.say('manager', '/choduyet'))
        self.assertIn('Đã đổi ca', self.act('manager', tid, '/duyet'))
        self.assertEqual('coach-b', self.service._source()['CA-11']['member_id'])
        self.assertEqual('coach-a', self.service._source()['CA-21']['member_id'])
        self.assertIn(tid, self.say('a', '/lichsudoica'))
        self.assertNotIn('http', response)

    def test_sender_identity_cannot_be_claimed_group_or_unrelated(self):
        tid, _ = self.create()
        for who in ('stranger', 'c'):
            response = self.say(who, '/yeucau ' + tid)
            self.assertNotIn('Việc riêng', response)
            self.assertNotIn('CA-11', response)
        self.assertIn('nhắn tin riêng', self.say('a', '/yeucau ' + tid, kind='GROUP', chat='group'))
        self.assertIn('chưa được quản trị', self.say('a', '/yeucau ' + tid, chat='another-dm'))
        self.assertEqual('pending_peer', self.status(tid))

    def test_unverified_self_registration_does_not_authorize(self):
        self.assertIn('chưa được quản trị', self.say('stranger', '/doica'))
        self.assertIsNone(self.say('stranger', '/dangky An'))
        self.assertIn('chưa được quản trị', self.say('stranger', '/doica'))
        self.assertIn('Chỉ quản lý', self.say('a', '/choduyet'))

    def test_explicit_preview_and_action_confirmations_only(self):
        preview = self.draft()
        self.say('a', 'đồng ý')
        self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM exchange_tickets').fetchone()[0])
        tid, _ = self.submit('a', preview)
        self.say('b', '/dongy ' + tid)  # opens review; never accepts by itself
        self.assertEqual('pending_peer', self.status(tid))
        self.say('b', 'đồng ý')
        self.say('b', '/dongy ' + tid + ' WRONG')
        self.assertEqual('pending_peer', self.status(tid))

    def test_duplicate_message_and_repeated_submit_are_idempotent(self):
        preview = self.draft()
        command = re.search(r'/doica gui [A-F0-9]+', preview)[0]
        incoming = self.message('a', command)
        self.assertIn('Đã gửi', self.chat.handle(incoming))
        self.assertIn('không thực hiện lại', self.chat.handle(incoming))
        self.say('a', command)
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM exchange_tickets').fetchone()[0])

    def test_selection_numbers_are_not_reused_on_back_or_restart(self):
        first = self.say('a', '/doica')
        number = re.search(r'^(\d+)\. ', first, re.M)[1]
        self.say('a', number)
        self.assertIn('không thuộc trang', self.say('a', number))
        self.store.close()
        self.open()
        self.assertIn('Chọn người', self.say('a', '/tickets'))
        self.assertIn('không thuộc trang', self.say('a', number))
        self.say('a', 'quay lại')
        self.assertIn('không thuộc trang', self.say('a', number))

    def test_restart_after_create_or_inflight_receipt_never_reexecutes(self):
        tid, _ = self.create()
        incoming = self.message('b', '/tickets')
        with self.store.transaction() as db:
            db.execute('INSERT INTO ticket_chat_receipts VALUES (?,?,?,?,NULL)',
                       (self.chat._scope(incoming), incoming.key, self.ids.resolve(self.scope, 'b', 'dm-b')['_binding'], digest(incoming.text)))
        self.store.close()
        self.open()
        self.assertIn('không thực hiện lại', self.chat.handle(incoming))
        self.assertIn(tid, self.say('b', '/choxacnhan'))
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM exchange_tickets').fetchone()[0])

    def test_revoke_rebind_blocks_old_dialog_and_receipts(self):
        preview = self.draft()
        incoming = self.message('a', '/tickets')
        self.chat.handle(incoming)
        self.ids.revoke(self.scope, 'a', 'dm-a')
        self.assertIn('chưa được quản trị', self.chat.handle(incoming))
        self.ids.bind(self.scope, 'a', 'dm-a', 'demo-a')
        self.assertIn('không còn hợp lệ', self.chat.handle(incoming))
        self.assertIn('chưa đúng hoặc đã hết hạn', self.say('a', re.search(r'/doica gui [A-F0-9]+', preview)[0]))

    def test_changed_recipient_binding_requires_fresh_preview(self):
        preview = self.draft()
        self.ids.bind(self.scope, 'b', 'dm-b', 'demo-b')
        response = self.say('a', re.search(r'/doica gui [A-F0-9]+', preview)[0])
        self.assertIn('Liên kết Zalo', response)
        self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM exchange_tickets').fetchone()[0])

    def test_changed_schedule_and_confirmation_version_do_not_write(self):
        tid, _ = self.create()
        detail = self.say('b', '/yeucau ' + tid)
        command = re.search(r'/dongy [0-9]{4}\b', detail)[0]
        row = self.service._source()['CA-11']
        with self.store.transaction() as db:
            db.execute('UPDATE demo_assignments SET body=? WHERE id=?', (packed(dict(row, room='Phòng đã sửa')), row['id']))
        self.assertIn('Lịch hoặc quyền đã thay đổi', self.say('b', command))
        self.assertEqual('stale', self.status(tid))
        self.assertEqual('coach-a', self.service._source()['CA-11']['member_id'])

    def test_decline_cancel_expire_and_exit_leave_roster_unchanged(self):
        tid, _ = self.create()
        self.say('a', 'thoát')
        self.assertEqual('pending_peer', self.status(tid))
        self.assertIn('Đã từ chối', self.act('b', tid, '/tuchoi'))
        tid, _ = self.create()
        self.assertIn('Đã hủy', self.act('a', tid, '/huydoica'))
        tid, _ = self.create()
        self.now += 49 * 3600
        self.assertIn('Đã hết hạn', self.say('a', '/yeucau ' + tid))
        self.assertEqual('coach-a', self.service._source()['CA-11']['member_id'])

    def test_preview_expiry_and_wrong_ticket_confirmation(self):
        preview = self.draft()
        self.now += 901
        self.assertIn('hết hạn', self.say('a', re.search(r'/doica gui [A-F0-9]+', preview)[0]))
        tid, _ = self.create()
        detail = self.say('b', '/yeucau ' + tid)
        code = re.search(r'/dongy ([0-9]{4})\b', detail)[1]
        self.assertIn('không thuộc', self.say('b', '/dongy DC-OTHER ' + code))
        self.assertEqual('pending_peer', self.status(tid))

    def test_parallel_requests_claim_same_assignment_once(self):
        preview_a = self.draft()
        preview_b = self.draft(a='b', own='CA-21', peer='coach-a', other='CA-11')
        messages = [self.message(who, re.search(r'/doica gui [A-F0-9]+', preview)[0])
                    for who, preview in (('a', preview_a), ('b', preview_b))]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(self.chat.handle, messages))
        self.assertEqual(1, sum('Đã gửi yêu cầu' in r for r in responses))
        self.assertEqual(1, self.store.db.execute('SELECT COUNT(*) FROM exchange_tickets').fetchone()[0])

    def test_timeout_after_write_reconciles_without_resending(self):
        tid, _ = self.create()
        self.act('b', tid, '/dongy')
        original = self.provider.swap
        def uncertain(a, b, key):
            original(a, b, key)
            raise TicketsWriteUncertain('fake timeout')
        with patch.object(self.provider, 'swap', side_effect=uncertain) as calls:
            self.assertIn('Cần kiểm tra kết quả', self.act('manager', tid, '/duyet'))
            self.assertIn('Đã đổi ca', self.act('manager', tid, '/doisoat'))
            self.assertEqual(1, calls.call_count)

    def test_readback_mismatch_stays_uncertain(self):
        tid, _ = self.create()
        self.act('b', tid, '/dongy')
        with patch.object(self.provider, 'swap', return_value=None) as calls:
            self.act('manager', tid, '/duyet')
            self.assertEqual('uncertain', self.status(tid))
            self.act('manager', tid, '/doisoat')
            self.assertEqual('uncertain', self.status(tid))
            self.assertEqual(1, calls.call_count)

    def test_pagination_search_only_accepts_visible_choices(self):
        template = self.service._source()['CA-11']
        with self.store.transaction() as db:
            for i in range(7):
                start = datetime.fromisoformat(template['start']) + timedelta(days=20+i)
                item = dict(template, id=f'EXTRA-{i}', course=f'Bổ sung {i}', start=start.isoformat(), end=(start+timedelta(hours=2)).isoformat())
                db.execute('INSERT INTO demo_assignments VALUES (?,?)', (item['id'], packed(item)))
        first = self.say('a', '/doica')
        self.assertIn('trang 1/2', first)
        self.assertIn('/doica trang 2', first)
        first_code = re.search(r'^(\d+)\. ', first, re.M)[1]
        second = self.say('a', '/doica trang 2')
        self.assertIn('trang 2/2', second)
        self.assertIn('/doica trang 1', second)
        self.assertIn('không thuộc trang', self.say('a', first_code))
        self.assertIn('trang 1/2', self.say('a', '/doica truoc'))
        self.assertIn('trang 2/2', self.say('a', '/doica tiep'))
        found = self.say('a', 'tìm Bổ sung 6')
        self.assertIn('Bổ sung 6', found)
        self.assertNotIn('Bổ sung 5', found)
        self.assertIn('Chọn người', self.choose('a', found, 'EXTRA-6'))

    def dialog(self, who):
        scope = self.chat._scope(self.message(who, ''))
        row = self.store.db.execute('SELECT body,expires FROM ticket_chat_dialogs WHERE scope=?', (scope,)).fetchone()
        return (json.loads(row['body']), row['expires']) if row else None

    def replace_shift(self, identity, **changes):
        item = dict(self.service._source()[identity], **changes)
        with self.store.transaction() as db:
            db.execute('UPDATE demo_assignments SET body=? WHERE id=?', (packed(item), identity))
        return item

    def test_restored_choice_page_drops_shift_at_start_boundary_without_renewing_expiry(self):
        begin = datetime.fromtimestamp(self.now + 30, datetime.fromisoformat('2030-01-01T00:00:00+07:00').tzinfo)
        self.replace_shift('CA-11', course='Ca cũ cần ẩn', start=begin.isoformat(), end=(begin+timedelta(hours=1)).isoformat())
        self.say('a', '/doica')
        before, expiry = self.dialog('a')
        old_code = next(c['code'] for c in before['choices'] if c['item']['id'] == 'CA-11')
        self.now += 30
        self.store.close()
        self.open()
        response = self.say('a', '/tickets')
        self.assertNotIn('Ca cũ cần ẩn', response)
        after, current_expiry = self.dialog('a')
        self.assertEqual(expiry, current_expiry)
        self.assertEqual(['CA-12'], [c['item']['id'] for c in after['choices']])
        rejected = self.say('a', old_code)
        self.assertIn('không thuộc trang', rejected)
        self.assertNotIn('Ca cũ cần ẩn', rejected)

    def test_people_with_no_future_shifts_disappear_from_saved_choices(self):
        begin = datetime.fromisoformat('2030-10-01T09:00:30+07:00')
        for identity in ('CA-21', 'CA-22'):
            self.replace_shift(identity, start=begin.isoformat(), end=(begin+timedelta(hours=1)).isoformat())
        response = self.say('a', '/doica')
        self.choose('a', response, 'CA-11')
        before, _ = self.dialog('a')
        old_code = next(c['code'] for c in before['choices'] if c['item']['id'] == 'coach-b')
        self.now += 30
        response = self.say('a', '/tickets')
        self.assertNotIn('Bình · Thành viên B', response)
        self.assertIn('Chi · Thành viên C', response)
        self.assertIn('không thuộc trang', self.say('a', old_code))

    def test_selected_shift_that_starts_does_not_reappear_in_reason_or_preview(self):
        begin = datetime.fromisoformat('2030-10-01T09:00:30+07:00')
        self.replace_shift('CA-11', course='Lớp hết hạn cần ẩn', start=begin.isoformat(), end=(begin+timedelta(hours=1)).isoformat())
        self.draft()
        self.now += 30
        response = self.say('a', '/tickets')
        self.assertNotIn('Lớp hết hạn cần ẩn', response)
        self.assertNotIn('/doica gui', response)
        self.assertIn('tương lai', response)
        self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM exchange_tickets').fetchone()[0])

    def test_lab_test_excludes_past_and_unknown_today_but_keeps_explicit_future_time(self):
        names = tuple(self.store.account('demo-' + who)['name'] for who in ('a', 'b'))
        self.provider.settings = SimpleNamespace(test_mode=True, test_members=names, write_enabled=True, exclusive_writer=True)
        self.service = TicketService(self.store, self.provider, mode='lab_live_test', require_manager=False, clock=lambda: self.now)
        self.chat = TicketsChat(self.service, self.ids, bot_scope=self.scope)
        self.replace_shift('CA-11', course='Hôm nay chưa rõ giờ', start='2030-10-01T11:00:00+07:00',
                           end='2030-10-01T12:00:00+07:00', period='Sáng')
        self.replace_shift('CA-12', course='Ca rõ giờ sắp tới', start='2030-10-01T11:00:00+07:00',
                           end='2030-10-01T12:00:00+07:00', period='Sáng 11:00–12:00')
        response = self.say('a', '/doica')
        self.assertNotIn('Hôm nay chưa rõ giờ', response)
        self.assertIn('Ca rõ giờ sắp tới', response)
        self.assertIn('Sáng 11:00–12:00', response)
        self.now = datetime.fromisoformat('2030-10-02T00:00:00+07:00').timestamp()
        response = self.say('a', '/doica')
        self.assertNotIn('Ca rõ giờ sắp tới', response)
        self.assertIn('Chưa có ca tương lai', response)

    def test_bad_pages_do_not_change_dialog_or_allocate_choices(self):
        self.assertIn('Không có danh sách', self.say('a', '/doica trang 2'))
        self.assertIsNone(self.dialog('a'))
        self.say('a', '/doica')
        before = self.dialog('a')
        counter = self.store.db.execute('SELECT SUM(next_number) FROM ticket_chat_counters').fetchone()[0]
        for command in ('/doica trang 0', '/doica trang 99999', '/doica trang -1', '/doica trang hai',
                        '/doica trang 1 extra', '/doica truoc', '/doica tiep'):
            with self.subTest(command=command):
                self.say('a', command)
                self.assertEqual(before, self.dialog('a'))
                self.assertEqual(counter, self.store.db.execute('SELECT SUM(next_number) FROM ticket_chat_counters').fetchone()[0])
        self.now += 901
        self.assertIn('Không có danh sách', self.say('a', '/doica trang 1'))
        self.assertIsNone(self.dialog('a'))

    def test_other_shift_pagination_preserves_owner_peer_filter_and_current_page_scope(self):
        template = self.service._source()['CA-21']
        with self.store.transaction() as db:
            for i in range(8):
                start = datetime.fromisoformat(template['start']) + timedelta(days=20+i)
                item = dict(template, id=f'B-EXTRA-{i}', course=f'Lớp bổ sung {i}', start=start.isoformat(), end=(start+timedelta(hours=2)).isoformat())
                db.execute('INSERT INTO demo_assignments VALUES (?,?)', (item['id'], packed(item)))
        response = self.say('a', '/doica')
        response = self.choose('a', response, 'CA-11')
        self.choose('a', response, 'coach-b')
        self.say('a', 'tìm Lớp bổ sung')
        before, _ = self.dialog('a')
        first_code = before['choices'][0]['code']
        response = self.say('a', '/doica trang 2')
        after, _ = self.dialog('a')
        self.assertIn('Bước 3/4', response)
        self.assertIn('/doica trang 1', response)
        for key in ('step', 'a', 'person', 'filter', 'choices'):
            self.assertEqual(before[key], after[key])
        self.assertEqual(2, after['page'])
        self.assertIn('không thuộc trang', self.say('a', first_code))
        self.assertEqual('other', self.dialog('a')[0]['step'])

    def test_past_uncertain_request_moves_to_history_and_still_allows_explicit_reconciliation(self):
        tid, _ = self.create()
        self.act('b', tid, '/dongy')
        with patch.object(self.provider, 'swap', return_value=None):
            self.act('manager', tid, '/duyet')
        self.assertEqual('uncertain', self.status(tid))
        self.now = datetime.fromisoformat('2030-10-20T09:00:00+07:00').timestamp()
        for who, command in (('a', '/yeucau'), ('manager', '/choxacnhan'), ('manager', '/choduyet')):
            self.assertNotIn(tid, self.say(who, command))
        self.assertIsNone(self.dialog('manager'))
        self.assertIn(tid, self.say('manager', '/lichsudoica'))
        self.assertIn('/doisoat', self.say('manager', '/yeucau ' + tid))
        self.assertIn('Cần kiểm tra kết quả', self.act('manager', tid, '/doisoat'))

    def test_request_pages_have_copyable_backward_links(self):
        a, b = (self.service._source()[identity] for identity in ('CA-11', 'CA-21'))
        for i in range(7):
            pair = []
            with self.store.transaction() as db:
                for side, template in enumerate((a, b)):
                    begin = datetime.fromisoformat(a['start']) + timedelta(days=30+i*3+side)
                    item = dict(template, id=f'LIST-{i}-{side}', start=begin.isoformat(), end=(begin+timedelta(hours=2)).isoformat())
                    db.execute('INSERT INTO demo_assignments VALUES (?,?)', (item['id'], packed(item)))
                    pair.append(item)
            user = self.store.account('demo-a')
            preview = self.service.preview(user, pair[0]['id'], pair[1]['id'])
            self.service.create(user, preview['preview_token'], '', f'list-request-pagination-{i}')
        for who, command in (('a', '/yeucau'), ('b', '/choxacnhan')):
            first = self.say(who, command)
            second = self.say(who, command + ' trang 2')
            self.assertIn(command + ' trang 2', first)
            self.assertIn(command + ' trang 1', second)
            self.assertEqual(5, len(re.findall(r'/yeucau DC-', first)))
            self.assertEqual(2, len(re.findall(r'/yeucau DC-', second)))

    def test_regular_commands_not_swallowed_during_wizard(self):
        self.say('a', '/doica')
        for text in ('/lich homnay', '/hoi quy định', '/nhac xem', '/dangky An', '/menu'):
            self.assertIsNone(self.say('a', text))
        self.assertIn('Chọn ca', self.say('a', '/tickets'))

    def test_distinct_bot_cannot_access_bindings_or_private_receipts(self):
        tid, _ = self.create()
        other = TicketsChat(self.service, self.ids, bot_scope=digest('different-bot'))
        self.assertIn('chưa được quản trị', other.handle(self.message('a', '/yeucau ' + tid)))

    def test_expired_reason_or_revoked_context_never_falls_through_to_model(self):
        for revoke in (False, True):
            with self.subTest(revoke=revoke):
                reply = self.say('a', '/doica')
                reply = self.choose('a', reply, 'CA-11')
                reply = self.choose('a', reply, 'coach-b')
                self.choose('a', reply, 'CA-21')
                self.assertTrue(self.chat.has_reason_context(self.message('a', 'Việc gia đình')))
                if revoke:
                    self.ids.revoke(self.scope, 'a', 'dm-a')
                else:
                    self.now += 901
                reply = self.say('a', 'Việc gia đình')
                self.assertIn('chưa được quản trị' if revoke else 'hết hạn', reply)
        self.assertEqual(0, self.store.db.execute('SELECT COUNT(*) FROM exchange_tickets').fetchone()[0])

    def test_audit_pagination_does_not_renew_or_execute_confirmation(self):
        tid, _ = self.create()
        with self.store.transaction() as db:
            for i in range(9):
                self.service._event(db, tid, 'Hệ thống thử', f'Dòng kiểm tra {i}')
        first = self.say('a', f'/lichsudoica {tid} trang 1')
        second = self.say('a', f'/lichsudoica {tid} trang 2')
        self.assertIn('trang 1/2', first)
        self.assertIn('Dòng kiểm tra 8', first)
        self.assertNotIn('Dòng kiểm tra 0', first)
        self.assertIn('Dòng kiểm tra 0', second)
        self.assertIn(f'/lichsudoica {tid} trang 1', second)
        self.assertEqual('pending_peer', self.status(tid))


if __name__ == '__main__':
    unittest.main()
