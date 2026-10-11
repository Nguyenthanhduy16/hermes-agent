"""Deterministic private-message Tickets UI; no model, web or outgoing API.

Every write reuses TicketService. Persistent receipts prevent replay across
process restarts; monotonically allocated choice numbers never select a later
step accidentally. Administrative bindings are checked before reading receipts.
"""
from datetime import datetime
import json
import re
import secrets

from ..commands import normalized, ticket_route
from .service import ACTIVE, VN, LIVE_TEST_NOTICE, CONFIRM_TTL_SECONDS, TicketError, digest, packed, stamp
from .notifications import PeerNotifications

PAGE_SIZE = 5
ACTIONS = {'/dongy': ('accept', 'Đồng ý'), '/tuchoi': ('reject', 'Từ chối'),
           '/huydoica': ('cancel', 'Hủy yêu cầu'), '/duyet': ('approve', 'Duyệt'),
           '/doisoat': ('reconcile', 'Kiểm tra lại kết quả')}
UNVERIFIED = ('Bạn chưa được quản trị xác minh liên kết Zalo với thành viên trong lịch. '
              'Nhờ quản trị liên kết tài khoản trước khi đổi ca. '
              '/dangky chỉ giúp lọc lịch, không cấp quyền đổi ca. Không gửi mật khẩu hoặc khóa vào chat.')
GROUP_HELP = ('Đổi ca được xử lý trong tin nhắn riêng để giữ thông tin của người liên quan. '
              'Hãy mở hồ sơ bot, chọn nhắn tin riêng và gửi /doica. '
              'Nếu chưa dùng được, nhờ quản trị cấp quyền nhắn riêng và xác minh tài khoản.')


def folded(text):
    return ' '.join(normalized(text).strip().split())


def when(value):
    return datetime.fromisoformat(value).astimezone(VN).strftime('%d/%m/%Y %H:%M')


def shift_date(item, *, simple_period=False):
    period = (re.sub(r'^(Sáng|Chiều|Tối)\s+\d{2}:\d{2}.*$', r'\1', item['period'])
              if simple_period else item['period'])
    return datetime.fromisoformat(item['start']).astimezone(VN).strftime('%d/%m/%Y') + ' · ' + period


def shift(item, date_only=False):
    schedule = shift_date(item, simple_period=not date_only)
    if not date_only:
        start, end = [datetime.fromisoformat(item[field]).astimezone(VN) for field in ('start', 'end')]
        finish = end.strftime('%H:%M') if start.date() == end.date() else end.strftime('%H:%M %d/%m/%Y')
        schedule += f" · {start:%H:%M}–{finish}"
    course, source = item['course'], ''
    parts = course.rsplit(' · ', 1)
    if len(parts) == 2 and re.fullmatch(r'.+ D[1-9]\d*', parts[1]):
        course, source = parts
    return f"{schedule}\n{course} · Phòng {item['room']}" + (f"\nNguồn: {source}" if source else '')


def comparison(a, b, date_only=False):
    return (f"SAU KHI ĐỔI\n{a['member_name']} nhận:\n{shift(b, date_only)}\n\n"
            f"{b['member_name']} nhận:\n{shift(a, date_only)}")


class TicketsChat:
    def __init__(self, service, identities, *, bot_scope):
        self.service, self.identities, self.bot_scope = service, identities, bot_scope
        self.store, self.clock = service.store, service.clock
        self.live_test = service.mode == 'lab_live_test'
        with self.store.lock:
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS ticket_chat_dialogs (
                  scope TEXT PRIMARY KEY, binding TEXT NOT NULL, target TEXT NOT NULL,
                  body TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS ticket_chat_receipts (
                  scope TEXT NOT NULL, message_key TEXT NOT NULL, binding TEXT NOT NULL,
                  input_hash TEXT NOT NULL, response TEXT, PRIMARY KEY(scope,message_key));
                CREATE TABLE IF NOT EXISTS ticket_chat_counters (
                  scope TEXT PRIMARY KEY, next_number INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS ticket_chat_confirmation_codes (
                  code TEXT PRIMARY KEY, issued REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS ticket_chat_code_migrations (
                  name TEXT PRIMARY KEY);
            ''')
            # Old six-character hexadecimal codes may happen to contain only
            # digits. Reserve those before issuing the new numeric vocabulary.
            with self.store.transaction() as db:
                if not db.execute("SELECT 1 FROM ticket_chat_code_migrations WHERE name='numeric-v1'").fetchone():
                    old_codes = set()
                    for row in db.execute('SELECT body FROM ticket_chat_dialogs'):
                        code = json.loads(row['body']).get('code', '')
                        if isinstance(code, str) and re.fullmatch(r'[0-9]{6}', code):
                            old_codes.add(code)
                    pattern = r'/(?:doica\s+gui|(?:dongy|tuchoi|huydoica|duyet|doisoat)(?:\s+DC-[A-Za-z0-9-]+)?)\s+([0-9]{6})(?![A-Za-z0-9])'
                    for row in db.execute('SELECT response FROM ticket_chat_receipts WHERE response IS NOT NULL'):
                        old_codes.update(re.findall(pattern, row['response']))
                    db.executemany('INSERT OR IGNORE INTO ticket_chat_confirmation_codes VALUES (?,?)',
                                   ((code, self.clock()) for code in old_codes))
                    db.execute("INSERT INTO ticket_chat_code_migrations VALUES ('numeric-v1')")
        self.notifications = PeerNotifications(self)

    def claim_notification(self, allowed_users, *, notification_id=None):
        return self.notifications.claim(allowed_users, notification_id=notification_id)

    def registration_notifications(self):
        """Local queue participants; no source reads or delivery claims."""
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT id,context FROM ticket_peer_notifications WHERE bot=? AND status='queued' ORDER BY id",
                (self.bot_scope,)).fetchall()
            return [(row['id'], tuple(json.loads(row['context'])[side]['id']
                                     for side in ('creator', 'recipient'))) for row in rows]

    def block_registration_notification(self, notification_id):
        with self.store.transaction() as db:
            db.execute("UPDATE ticket_peer_notifications SET status='blocked' WHERE bot=? AND id=? AND status='queued'",
                       (self.bot_scope, notification_id))

    def registration_accounts(self, message):
        """Select only identities used by this exact input before async checks.

        The normal handler still checks authorization, binding revisions, input
        codes and source versions after those checks. Lists remain read-only;
        a later selected person, ticket or confirmation gets its own check.
        """
        if message.chat_type != 'PRIVATE':
            return ()
        with self.store.lock:
            user = self.identities.resolve(self.bot_scope, message.user_id, message.chat_id)
            if not user:
                return ()
            accounts = {user['id']}

            def member(member_id):
                matches = [person['id'] for person in self.identities.linked_accounts(self.bot_scope)
                           if person['member_id'] == member_id]
                if len(matches) != 1:
                    raise TicketError('Liên kết người đổi cùng chưa xác định duy nhất; nhờ quản trị kiểm tra.')
                accounts.update(matches)

            def ticket(ticket_id):
                # Reuse the service's exact read authorization before looking
                # up anyone else's roster identity from a supplied ticket ID.
                row = self.service._row(user, ticket_id)
                accounts.add(row['creator'])
                member(row['recipient'])

            text = message.text.strip()
            route = ticket_route(text)
            body = self._load(self._scope(message), user)
            if route:
                command, argument = route
                parts = argument.split()
                if command in ACTIONS:
                    if len(parts) == 1 and re.fullmatch(r'[0-9]{4}', parts[0]):
                        notice = self.store.db.execute(
                            'SELECT context FROM ticket_peer_notifications WHERE bot=? AND code=?',
                            (self.bot_scope, parts[0])).fetchone()
                        if notice:
                            context = json.loads(notice['context'])
                            peer = context['recipient']
                            if (peer['id'] == user['id'] and peer['user_id'] == message.user_id
                                    and peer['chat_id'] == message.chat_id):
                                accounts.update(context[side]['id'] for side in ('creator', 'recipient'))
                            return tuple(sorted(accounts))
                    if parts and parts[0].upper().startswith('DC-'):
                        ticket(parts[0].upper())
                    elif body and body['step'] == 'review':
                        ticket(body['ticket'])
                    return tuple(sorted(accounts))
                if command in ('/yeucau', '/lichsudoica') and parts and parts[0].upper().startswith('DC-'):
                    ticket(parts[0].upper())
                    return tuple(sorted(accounts))
                if command in ('/yeucau', '/choxacnhan', '/lichsudoica', '/choduyet'):
                    return tuple(sorted(accounts))
                if command == '/doica' and not argument.strip():
                    return tuple(sorted(accounts))  # Starting over abandons the old peer.
                if command not in ('/tickets', '/doica'):
                    return tuple(sorted(accounts))
            elif folded(text) in ('thoat', 'quay lai'):
                return tuple(sorted(accounts))  # Navigation cannot create/confirm a request.
            if body:
                if body['step'] == 'review':
                    ticket(body['ticket'])
                elif body.get('person'):
                    member(body['person']['id'])
                elif body.get('b'):
                    member(body['b']['member_id'])
                elif body['step'] == 'person' and not route:
                    selected = next((choice['item'] for choice in self._visible(body)
                                     if choice['code'] == text), None)
                    if selected:
                        member(selected['id'])
            return tuple(sorted(accounts))

    def finish_notification(self, notification_id, status):
        self.notifications.finish(notification_id, status)

    def _notification_display(self, ticket, code, expires):
        effect = ('Đồng ý xong sẽ chờ quản lý duyệt.' if self.service.require_manager else
                  'Đồng ý sẽ cập nhật lịch Lab thật.' if self.service.mode in ('lab_live', 'lab_live_test') else
                  'Đồng ý sẽ cập nhật bảng thử.' if self.service.mode == 'sheets_test' else
                  'Đồng ý sẽ cập nhật dữ liệu mô phỏng.')
        return (f"YÊU CẦU ĐỔI CA TỪ {ticket['a']['member_name']}\n"
                f"{ticket['id']}\n\n" +
                (LIVE_TEST_NOTICE + '\n\n' if self.live_test else '') +
                comparison(ticket['a'], ticket['b'], self.live_test) +
                f"\n\nLý do: {ticket['reason'] or 'Không ghi'}\n{effect}\n\n"
                f"Đồng ý: /dongy {code}\nTừ chối: /tuchoi {code}\n"
                f"Mã có hiệu lực tối đa 15 phút, đến {when(stamp(expires))}.\n"
                "Mã hết hạn? Gửi /choxacnhan để xem lại.")

    def close(self):
        # Runtime owns the provider, database and exclusive process lock.
        with self.store.lock:
            pass  # Wait for an in-flight synchronous handler before releasing resources.

    def _scope(self, message):
        return digest(packed([self.bot_scope, message.chat_type, message.user_id, message.chat_id]))

    def has_dialog(self, message):
        if message.chat_type != 'PRIVATE':
            return False
        with self.store.lock:
            return self.store.db.execute('SELECT 1 FROM ticket_chat_dialogs WHERE scope=?',
                                         (self._scope(message),)).fetchone() is not None

    def has_reason_context(self, message):
        """Only free-form reason input takes precedence over fuzzy policy intent.

        Include expired/revoked drafts: handle() must explain loss of context
        instead of sending the user's private reason to the general model.
        """
        if message.chat_type != 'PRIVATE':
            return False
        with self.store.lock:
            row = self.store.db.execute('SELECT body FROM ticket_chat_dialogs WHERE scope=?',
                                        (self._scope(message),)).fetchone()
            return bool(row and json.loads(row['body'])['step'] == 'reason')

    def _load(self, scope, user):
        row = self.store.db.execute('SELECT * FROM ticket_chat_dialogs WHERE scope=?', (scope,)).fetchone()
        if not row:
            return None
        target = packed([self.service.target, self.service.require_manager])
        if row['binding'] != user['_binding'] or row['expires'] <= self.clock() or row['target'] != target:
            self._clear(scope)
            return None
        return json.loads(row['body'])

    def _clear(self, scope):
        with self.store.transaction() as db:
            db.execute('DELETE FROM ticket_chat_dialogs WHERE scope=?', (scope,))

    def _save(self, scope, user, body, ttl=CONFIRM_TTL_SECONDS):
        with self.store.transaction() as db:
            db.execute('INSERT OR REPLACE INTO ticket_chat_dialogs VALUES (?,?,?,?,?)',
                       (scope, user['_binding'], packed([self.service.target, self.service.require_manager]),
                        packed(body), self.clock() + ttl))

    def _confirmation_code(self):
        # Prefixes keep a copied code from another operating mode from ever
        # matching, including modes that use separate databases. The registry
        # persists forever; clearing or expiring a dialog never recycles a code.
        prefix = {'demo': '1', 'sheets_test': '2', 'lab_live': '3', 'lab_live_test': '4'}[self.service.mode]
        with self.store.transaction() as db:
            # Visit the complete four-digit pool from a random starting point:
            # collisions must not reject a request while unused codes remain.
            start = secrets.randbelow(1000)
            for offset in range(1000):
                code = prefix + f'{(start + offset) % 1000:03d}'
                inserted = db.execute('INSERT OR IGNORE INTO ticket_chat_confirmation_codes VALUES (?,?)',
                                      (code, self.clock())).rowcount
                if inserted:
                    return code
        raise TicketError('Đã hết mã xác nhận mới. Chưa thực hiện thao tác; nhờ quản trị kiểm tra, không xóa dữ liệu để cấp lại mã.')

    def _choices(self, scope, items):
        # Never recycle numeric choices, even after expiry, cancellation/restart.
        with self.store.transaction() as db:
            row = db.execute('SELECT next_number FROM ticket_chat_counters WHERE scope=?', (scope,)).fetchone()
            start = row[0] if row else 1
            db.execute('INSERT OR REPLACE INTO ticket_chat_counters VALUES (?,?)', (scope, start + len(items)))
        return [dict(code=str(start + index), item=item) for index, item in enumerate(items)]

    def handle(self, message):
        route = ticket_route(message.text)
        if message.chat_type != 'PRIVATE':
            return GROUP_HELP if route else None
        text = message.text.strip()
        control = folded(text)
        with self.store.lock:
            scope = self._scope(message)
            had_dialog = bool(self.store.db.execute('SELECT 1 FROM ticket_chat_dialogs WHERE scope=?', (scope,)).fetchone())
            user = self.identities.resolve(self.bot_scope, message.user_id, message.chat_id)
            if not user:
                self._clear(scope)
                return UNVERIFIED if route or had_dialog else None
            if self.live_test and (user['role'] != 'member' or user['name'] not in self.service.test_members):
                self._clear(scope)
                return 'Tài khoản không thuộc hai thành viên đã được xác minh cho phép thử trên lịch thật.' if route or had_dialog else None
            body = self._load(scope, user)
            if not route and text.startswith('/'):
                return None  # /lich, /hoi, /nhac and other work commands stay independent.
            if not route and had_dialog and not body:
                return ('Bước nhập đã hết hạn hoặc liên kết/cấu hình đã thay đổi. '
                        'Chưa thực hiện thao tác từ tin nhắn này. Gửi /doica để chọn lại hoặc /yeucau để kiểm tra yêu cầu đã gửi.')
            if not route and not body and not (text.isdecimal() or control in ('quay lai', 'thoat', 'tiep', 'truoc')):
                return None
            old = self.store.db.execute('SELECT * FROM ticket_chat_receipts WHERE scope=? AND message_key=?',
                                        (scope, message.key)).fetchone()
            if old:
                if old['binding'] != user['_binding'] or old['input_hash'] != digest(text):
                    return 'Tin nhắn cũ không còn hợp lệ. Gửi /tickets để bắt đầu lại.'
                # Do not replay old selection lists after the wizard has advanced.
                return ('Tin nhắn này đã được tiếp nhận; không thực hiện lại. '
                        'Gửi /tickets để xem bước hiện tại hoặc /yeucau để kiểm tra kết quả.')
            with self.store.transaction() as db:
                db.execute('INSERT INTO ticket_chat_receipts VALUES (?,?,?,?,NULL)',
                           (scope, message.key, user['_binding'], digest(text)))
            try:
                result = self._dispatch(scope, user, route, text, body)
            except TicketError as exc:
                result = str(exc) + '\nGửi /tickets để xem hướng dẫn; /doica để chọn lại từ đầu.'
            except Exception:
                # A transport/private-data exception must not leak into Zalo.
                result = ('Chưa xác minh được kết quả. Gửi /yeucau để kiểm tra yêu cầu đã lưu; '
                          'không gửi lặp xác nhận. Nhờ quản trị kiểm tra nếu lỗi tiếp diễn.')
            with self.store.transaction() as db:
                db.execute('UPDATE ticket_chat_receipts SET response=? WHERE scope=? AND message_key=?',
                           (result, scope, message.key))
            return result

    def _menu(self, user):
        mode = {'demo': 'MÔ PHỎNG, không ghi Sheets', 'sheets_test': 'Bảng Sheets thử riêng',
                'lab_live': 'Lịch Lab hiện có · chỉ đổi Lab Coach (cột D)',
                'lab_live_test': LIVE_TEST_NOTICE}[self.service.mode]
        if self.service.mode in ('lab_live', 'lab_live_test'):
            settings = self.service.provider.settings
            if not settings.write_enabled or not settings.exclusive_writer:
                mode += '\nGhi lịch đang khóa; quản trị cần hoàn tất cấu hình trước khi đổi ca.'
        return (f"ĐỔI CA · {user['name']}\n\n"
                '/doica — bắt đầu chọn ca\n/choxacnhan — chờ tôi xử lý\n'
                '/yeucau — yêu cầu của tôi\n/lichsudoica — lịch sử\n'
                + ('/choduyet — chờ quản lý duyệt\n' if user['role'] == 'manager' else '') +
                f'\n{mode}\n\n'
                'Bot sẽ gửi thông báo riêng cho người đổi cùng. Chưa thấy thông báo? Gửi /choxacnhan.\n'
                'Trong lúc chọn: nhắn quay lại hoặc thoát. Thoát không hủy yêu cầu đã gửi.')

    def _dispatch(self, scope, user, route, text, body):
        if route:
            command, argument = route
            if command == '/tickets':
                if body and body['step'] == 'review' and not self._future_ticket(self.service.detail(user, body['ticket'])):
                    self._clear(scope)
                    return self._menu(user) + '\n\nYêu cầu cũ và kết quả cần đối soát: /lichsudoica.'
                if body and body['step'] != 'review':
                    body = self._refresh_dialog(scope, body)
                return self._render(body) + '\n/menu — hướng dẫn chung' if body else self._menu(user)
            if command in ('/yeucau', '/choxacnhan', '/lichsudoica', '/choduyet'):
                parts = argument.strip().split()
                if command == '/lichsudoica' and parts and parts[0].upper().startswith('DC-'):
                    page = parts[2] if len(parts) == 3 and folded(parts[1]) == 'trang' else '1' if len(parts) == 1 else ''
                    if not page.isdecimal() or len(page) > 5:
                        raise TicketError('Xem lịch sử: /lichsudoica MÃ_YÊU_CẦU trang 1')
                    return self._history(user, parts[0].upper(), int(page))
                if command == '/yeucau' and parts and parts[0].upper().startswith('DC-'):
                    if len(parts) != 1:
                        raise TicketError('Xem chi tiết: /yeucau MÃ_YÊU_CẦU')
                    return self._review(scope, user, parts[0].upper())
                return self._list(scope, user, command, argument)
            if command in ACTIONS:
                return self._action(scope, user, command, argument, body)
            if command == '/doica':
                page_command = folded(argument)
                if page_command.startswith('trang') or page_command in ('tiep', 'truoc'):
                    return self._page(scope, user, body, page_command)
                if folded(argument).startswith('gui '):
                    code = argument.split()[-1]
                    if (len(argument.split()) != 2 or not body or body['step'] != 'preview'
                            or code.upper() != body['code']):
                        raise TicketError('Mã gửi chưa đúng hoặc đã hết hạn. Gửi /doica để xem trước lại.')
                    peer = self._linked_pair(body['a'], body['b'], body.get('peer_binding'))
                    ticket = self.service.create(user, body['preview_token'], body['reason'], body['idem'],
                                                 on_created=self.notifications.enqueue_callback(user, peer))
                    self._clear(scope)
                    return (f"Đã gửi yêu cầu · {ticket['id']}\n\n"
                            f"Đang chờ {ticket['b']['member_name']}.\n"
                            "Bot sẽ gửi thông báo riêng để người đổi cùng đồng ý hoặc từ chối.\n"
                            "Nếu chưa thấy, nhờ người đổi cùng nhắn:\n/choxacnhan\n\n"
                            "Lịch chưa thay đổi.\n"
                            f"Xem hoặc hủy yêu cầu: /yeucau {ticket['id']}")
                if argument.strip():
                    raise TicketError('Gửi /doica để chọn ca từng bước. Chỉ dùng lệnh gửi đúng như bản xem trước.')
                return self._select_own(scope, user)
        control = folded(text)
        if control == 'thoat':
            self._clear(scope)
            return 'Đã thoát bước nhập. Các yêu cầu đã gửi vẫn giữ nguyên; xem /yeucau.'
        if not body:
            return 'Không có bước lựa chọn còn hiệu lực. Gửi /tickets hoặc /doica để bắt đầu.'
        if body['step'] != 'review':
            body = self._refresh_dialog(scope, body)
        if control == 'quay lai':
            step = body['step']
            if step in ('own', 'review'):
                self._clear(scope)
                return self._menu(user)
            if step == 'person':
                return self._select_own(scope, user)
            if step == 'other':
                return self._select_person(scope, user, body['a'])
            if step == 'reason':
                return self._select_other(scope, user, body['a'], body['person'])
            body = dict(body, step='reason')
            self._save(scope, user, body)
            return self._render(body)
        if body['step'] in ('own', 'person', 'other'):
            if control in ('tiep', 'truoc'):
                return self._page(scope, user, body, control)
            if control.startswith('tim '):
                body['filter'], body['page'] = control[4:], 1
                return self._save_menu(scope, user, body)
            if control == 'tat ca':
                body['filter'], body['page'] = '', 1
                return self._save_menu(scope, user, body)
            candidates = self._visible(body)
            chosen = next((c['item'] for c in candidates if c['code'] == text), None)
            if not chosen:
                return 'Mã chọn không thuộc trang đang hiển thị. Hãy dùng mã số ở bước hiện tại.\n' + self._render(body)
            if body['step'] == 'own':
                self._unchanged(chosen)
                return self._select_person(scope, user, chosen)
            if body['step'] == 'person':
                return self._select_other(scope, user, body['a'], chosen)
            self._unchanged(body['a'], chosen)
            peer = self._linked_pair(body['a'], chosen, body['person']['binding'])
            # Validate conflicts and current claims before even asking for a reason.
            self.service.preview(user, body['a']['id'], chosen['id'])
            result = dict(step='reason', a=body['a'], b=chosen, person=body['person'], peer_binding=peer['_binding'])
            self._save(scope, user, result)
            return self._render(result)
        if body['step'] == 'reason':
            reason = '' if control == 'bo qua' else text
            if len(reason) > 600:
                raise TicketError('Lý do tối đa 600 ký tự; gửi lại lý do hoặc nhắn bỏ qua.')
            self._unchanged(body['a'], body['b'])
            self._linked_pair(body['a'], body['b'], body['peer_binding'])
            preview = self.service.preview(user, body['a']['id'], body['b']['id'])
            body.update(step='preview', reason=reason, preview_token=preview['preview_token'],
                        code=self._confirmation_code(), idem=secrets.token_hex(20),
                        expires_at=preview['expires_at'], requires_manager=preview['requires_manager'])
            self._save(scope, user, body, ttl=CONFIRM_TTL_SECONDS)
            return self._render(body)
        return 'Chưa thực hiện thay đổi. Hãy sao chép đúng lệnh xác nhận bên dưới.\n' + self._render(body)

    def _unchanged(self, *items):
        rows = self.service._source()
        if any(rows.get(item['id']) != item for item in items):
            raise TicketError('Lịch đã thay đổi từ lúc hiển thị. Gửi /doica để chọn và xác nhận lại.', 409)
        if any(not self.service.is_future(item) for item in items):
            raise TicketError('Ca không còn ở tương lai hoặc chưa đủ giờ để xác định. Gửi /doica để chọn lại.', 409)
        return rows

    def _linked_pair(self, a, b, expected=None):
        accounts = self.identities.linked_accounts(self.bot_scope)
        if self.live_test:
            accounts = [account for account in accounts if account['role'] == 'member' and account['name'] in self.service.test_members]
        peer = next((x for x in accounts if x['member_id'] == b['member_id']), None)
        if (not any(x['member_id'] == a['member_id'] for x in accounts) or not peer
                or (expected is not None and expected != peer['_binding'])):
            raise TicketError('Liên kết Zalo của người đổi cùng đã thay đổi hoặc chưa được xác minh. Nhờ quản trị kiểm tra.', 409)
        return peer

    def _available(self, rows):
        self.service._expire()
        held = {r[0] for r in self.store.db.execute('SELECT assignment FROM exchange_claims')}
        return sorted((r for r in rows if r['id'] not in held and
                       self.service.is_future(r)),
                      key=lambda r: (r['start'], r['id']))

    def _refresh_choices(self, body):
        """Drop stale choices without recycling their numbers or choosing for users."""
        step = body['step']
        if step not in ('own', 'person', 'other'):
            if step in ('reason', 'preview'):
                self._unchanged(body['a'], body['b'])
            return body
        rows = {r['id']: r for r in self._available(self.service._source().values())}
        if step != 'own' and rows.get(body['a']['id']) != body['a']:
            raise TicketError('Ca đã chọn không còn khả dụng trong tương lai. Gửi /doica để chọn lại.', 409)
        if step == 'person':
            owners = {r['member_id'] for r in rows.values()}
            people = {p['member_id']: dict(id=p['member_id'], name=p['name'], binding=p['_binding'])
                      for p in self.identities.linked_accounts(self.bot_scope)
                      if p['member_id'] in owners and (not self.live_test or
                          p['role'] == 'member' and p['name'] in self.service.test_members)}
            choices = [c for c in body['choices'] if people.get(c['item']['id']) == c['item']]
        else:
            if step == 'other':
                person = body['person']
                if not any(p['member_id'] == person['id'] and p['_binding'] == person['binding']
                           for p in self.identities.linked_accounts(self.bot_scope)):
                    raise TicketError('Liên kết Zalo của người đổi cùng đã thay đổi. Gửi /doica để chọn lại.', 409)
            choices = [c for c in body['choices'] if rows.get(c['item']['id']) == c['item']]
        updated = dict(body, choices=choices)
        pages = max(1, (len(self._filtered(updated)) + PAGE_SIZE - 1) // PAGE_SIZE)
        updated['page'] = min(body['page'], pages)
        return updated

    def _refresh_dialog(self, scope, body):
        updated = self._refresh_choices(body)
        if updated != body:
            # Reading a saved page does not renew its original time limit.
            with self.store.transaction() as db:
                db.execute('UPDATE ticket_chat_dialogs SET body=? WHERE scope=?', (packed(updated), scope))
        return updated

    def _page(self, scope, user, body, argument):
        if not body or body['step'] not in ('own', 'person', 'other'):
            raise TicketError('Không có danh sách lựa chọn còn hiệu lực. Gửi /doica để bắt đầu.')
        parts = argument.split()
        if argument in ('tiep', 'truoc'):
            requested = body['page'] + (1 if argument == 'tiep' else -1)
        elif len(parts) == 2 and parts[0] == 'trang' and re.fullmatch(r'[0-9]{1,5}', parts[1]):
            requested = int(parts[1])
        else:
            raise TicketError('Chuyển trang bằng /doica trang 2, /doica tiep hoặc /doica truoc.')
        refreshed = self._refresh_choices(body)
        pages = max(1, (len(self._filtered(refreshed)) + PAGE_SIZE - 1) // PAGE_SIZE)
        if not 1 <= requested <= pages:
            raise TicketError(f'Trang cần nằm từ 1 đến {pages}. Danh sách hiện tại chưa thay đổi.')
        return self._save_menu(scope, user, dict(refreshed, page=requested))

    @staticmethod
    def _page_links(command, page, pages):
        lines = []
        if page > 1:
            lines.append(f'Trang trước: {command} trang {page-1}')
        if page < pages:
            lines.append(f'Trang sau: {command} trang {page+1}')
        if pages > 1:
            lines.append(f'Đến trang bất kỳ: {command} trang N (1–{pages})')
        return '\n'.join(lines)

    def _select_own(self, scope, user):
        rows = self._available(self.service._source().values())
        choices = [r for r in rows if r['member_id'] == user['member_id']]
        return self._save_menu(scope, user, dict(step='own', choices=self._choices(scope, choices), page=1, filter=''))

    def _select_person(self, scope, user, a):
        rows = self._available(self._unchanged(a).values())
        owners = {r['member_id'] for r in rows if r['member_id'] != user['member_id']}
        people = sorted((dict(id=p['member_id'], name=p['name'], binding=p['_binding'])
                         for p in self.identities.linked_accounts(self.bot_scope) if p['member_id'] in owners
                         and (not self.live_test or p['role'] == 'member' and p['name'] in self.service.test_members)), key=lambda p: p['name'])
        return self._save_menu(scope, user, dict(step='person', a=a, choices=self._choices(scope, people), page=1, filter=''))

    def _select_other(self, scope, user, a, person):
        rows = self._available(self._unchanged(a).values())
        choices = [r for r in rows if r['member_id'] == person['id']]
        return self._save_menu(scope, user, dict(step='other', a=a, person=person,
                                                choices=self._choices(scope, choices), page=1, filter=''))

    def _filtered(self, body):
        return [c for c in body['choices'] if body.get('filter', '') in folded(' '.join(
            str(v) for k, v in c['item'].items() if k != 'binding'))]

    def _visible(self, body):
        return self._filtered(body)[(body['page'] - 1) * PAGE_SIZE:body['page'] * PAGE_SIZE]

    def _save_menu(self, scope, user, body):
        pages = max(1, (len(self._filtered(body)) + PAGE_SIZE - 1) // PAGE_SIZE)
        body['page'] = max(1, min(body['page'], pages))
        self._save(scope, user, body)
        return self._render(body)

    def _render(self, body):
        step = body['step']
        if step in ('own', 'person', 'other'):
            heading = {'own': 'Bước 1/4 · Chọn ca của bạn',
                       'person': 'Bước 2/4 · Chọn người đổi cùng',
                       'other': 'Bước 3/4 · Chọn ca bạn muốn nhận'}[step]
            pages = max(1, (len(self._filtered(body)) + PAGE_SIZE - 1) // PAGE_SIZE)
            lines = []
            for choice in self._visible(body):
                item = choice['item']
                summary = item['name'] if step == 'person' else shift(item, self.live_test)
                lines.append(f"{choice['code']}. {summary}")
            if not lines:
                return (f'{heading}\n\nChưa có ca tương lai phù hợp hoặc ca đang có yêu cầu khác. '
                        'Ca hôm nay cần giờ bắt đầu rõ ràng.\nNhắn tất cả để bỏ lọc, quay lại hoặc thoát.')
            context = f"\nNgười đổi cùng: {body['person']['name']}" if step == 'other' else ''
            return (f"{heading}{context}\nNhắn số ở đầu lựa chọn bên dưới.\n\n" + '\n\n'.join(lines) +
                    f"\n\nĐang ở trang {body['page']}/{pages} · tối đa {PAGE_SIZE} mục/trang.\n" +
                    (self._page_links('/doica', body['page'], pages) + '\n' if pages > 1 else '') +
                    'Tìm nhanh: tìm TỪ_KHÓA · Bỏ lọc: tất cả\nQuay lại / thoát.')
        if step == 'reason':
            return ('Bước 4/4 · Lý do và kiểm tra\nNhập lý do đổi ca hoặc nhắn bỏ qua.\n\n' +
                    comparison(body['a'], body['b'], self.live_test) + '\n\nChưa gửi yêu cầu. Quay lại để chọn lại ca; thoát để dừng.')
        if step == 'preview':
            return (f"CHƯA GỬI YÊU CẦU\nNếu nội dung đúng, gửi:\n/doica gui {body['code']}\n"
                    f"Mã hết hạn lúc {when(body['expires_at'])}.\n\n" +
                    (LIVE_TEST_NOTICE + '\n\n' if self.live_test else '') +
                    comparison(body['a'], body['b'], self.live_test) +
                    f"\n\nLý do: {body['reason'] or 'Không ghi'}\n" +
                    ('Cần người đổi cùng đồng ý, sau đó quản lý duyệt.' if body['requires_manager'] else
                     'Người đổi cùng đồng ý thì cập nhật lịch Lab thật.' if self.service.mode in ('lab_live', 'lab_live_test') else
                     'Người đổi cùng đồng ý thì cập nhật dữ liệu mô phỏng.' if self.service.mode == 'demo' else
                     'Người đổi cùng đồng ý thì cập nhật bảng thử.') +
                    '\n\nQuay lại để sửa lý do · Thoát để bỏ bản nháp.')
        return body['display']

    def _list(self, scope, user, command, argument):
        if command == '/choduyet' and user['role'] != 'manager':
            raise TicketError('Chỉ quản lý được xem danh sách chờ duyệt.', 403)
        parts = folded(argument).split()
        if parts and (len(parts) != 2 or parts[0] != 'trang' or not parts[1].isdecimal() or len(parts[1]) > 5):
            raise TicketError(f'Xem danh sách: {command} hoặc {command} trang 2')
        page = int(parts[1]) if parts else 1
        tickets = self.service.bootstrap(user)['tickets']
        if command == '/choxacnhan':
            tickets = [t for t in tickets if 'accept' in t['actions'] or 'approve' in t['actions'] or 'reconcile' in t['actions']]
        elif command == '/choduyet':
            tickets = [t for t in tickets if 'approve' in t['actions'] or 'reconcile' in t['actions']]
        elif command == '/lichsudoica':
            tickets = [t for t in tickets if t['status'] not in ACTIVE or not self._future_ticket(t)]
        else:
            tickets = [t for t in tickets if t['requester_id'] == user['id'] or t['recipient_member_id'] == user['member_id']]
        if command != '/lichsudoica':
            tickets = [t for t in tickets if self._future_ticket(t)]
        if not tickets:
            if command == '/lichsudoica':
                return 'Chưa có lịch sử đổi ca. /doica — tạo yêu cầu mới.'
            if command != '/lichsudoica':
                row = self.store.db.execute('SELECT body FROM ticket_chat_dialogs WHERE scope=?', (scope,)).fetchone()
                if row and json.loads(row['body'])['step'] == 'review':
                    self._clear(scope)
            if command == '/choxacnhan':
                return ('CHƯA CÓ YÊU CẦU CHỜ BẠN\n\n'
                        'Chỉ hiện yêu cầu có cả hai ca chưa bắt đầu. Yêu cầu cũ: /lichsudoica.\n\n'
                        'Người tạo cần gửi lệnh /doica gui theo bản xem trước và nhận “Đã gửi yêu cầu” kèm mã DC-. '
                        'Bản nháp chưa xuất hiện ở đây.\n\nSau đó bạn nhắn lại /choxacnhan.')
            return 'Chưa có yêu cầu phù hợp cho ca tương lai. /doica — tạo yêu cầu; /lichsudoica — yêu cầu cũ.'
        pages = (len(tickets) + PAGE_SIZE - 1) // PAGE_SIZE
        if not 1 <= page <= pages:
            raise TicketError(f'Trang cần nằm từ 1 đến {pages}.')
        if command == '/choxacnhan' and len(tickets) == 1:
            return self._review(scope, user, tickets[0]['id'])
        if command == '/choxacnhan':
            self._clear(scope)  # A multi-ticket list is not a selection to confirm.
        lines = [f"{t['id']} · {t['status_label']}\n{t['a']['member_name']} ↔ {t['b']['member_name']}\n"
                 f"{shift_date(t['a']) if self.live_test else when(t['a']['start'])} ↔ {shift_date(t['b']) if self.live_test else when(t['b']['start'])}\n/yeucau {t['id']}"
                 for t in tickets[(page-1)*PAGE_SIZE:page*PAGE_SIZE]]
        title = {'/choxacnhan': 'CHỜ BẠN XÁC NHẬN', '/choduyet': 'CHỜ QUẢN LÝ DUYỆT',
                 '/lichsudoica': 'LỊCH SỬ ĐỔI CA', '/yeucau': 'YÊU CẦU CỦA BẠN'}[command]
        links = self._page_links(command, page, pages)
        return f'{title} · trang {page}/{pages}\nMở một yêu cầu bằng lệnh dưới mỗi mục.\n\n' + '\n\n'.join(lines) + ('\n\n' + links if links else '')

    def _future_ticket(self, ticket):
        return all(self.service.is_future(ticket[side]) for side in ('a', 'b'))

    def _review(self, scope, user, ticket_id):
        ticket = self.service.detail(user, ticket_id)
        code = self._confirmation_code() if ticket['actions'] else ''
        commands = [f"{label}: {command} {code}"
                    for command, (action, label) in ACTIONS.items() if action in ticket['actions']]
        latest = ticket['message'].removeprefix(LIVE_TEST_NOTICE).strip()
        display = (('\n'.join(commands) + '\nMã xác nhận có hiệu lực tối đa 15 phút.\n\n' if commands else '') +
                   f"{ticket['id']} · {ticket['status_label']}\n\n" +
                   (LIVE_TEST_NOTICE + '\n\n' if self.live_test else '') +
                   comparison(ticket['a'], ticket['b'], self.live_test) +
                   f"\n\nLý do: {ticket['reason'] or 'Không ghi'}" +
                   (f"\nHạn phản hồi yêu cầu: {when(ticket['expires_at'])}" if ticket['status'] in ACTIVE else '') +
                   (f"\nKết quả: {latest}" if ticket['status'] not in ('pending_peer', 'pending_manager') else '') +
                   f"\n\nLịch sử: /lichsudoica {ticket['id']}\nDanh sách: /yeucau")
        body = dict(step='review', ticket=ticket_id, version=ticket['version'], code=code,
                    participant_bindings=self._participant_bindings(ticket),
                    actions=ticket['actions'], display=display)
        self._save(scope, user, body, ttl=CONFIRM_TTL_SECONDS)
        return display

    def _participant_bindings(self, ticket):
        """Both people must still have the bindings shown when code was issued."""
        creator = self.identities.destination(self.bot_scope, ticket['requester_id'])
        peers = [person for person in self.identities.linked_accounts(self.bot_scope)
                 if person['member_id'] == ticket['recipient_member_id']]
        if not creator or len(peers) != 1:
            return None
        return {person['id']: person['_binding'] for person in (creator, peers[0])}

    def _history(self, user, ticket_id, page):
        ticket = self.service.detail(user, ticket_id)
        events = list(reversed(ticket['events']))
        pages = max(1, (len(events) + PAGE_SIZE - 1) // PAGE_SIZE)
        if not 1 <= page <= pages:
            raise TicketError(f'Trang cần nằm từ 1 đến {pages}.')
        lines = [f"{when(e['at'])} · {e['actor']}\n{e['text']}" for e in events[(page-1)*PAGE_SIZE:page*PAGE_SIZE]]
        return (f'LỊCH SỬ {ticket_id}\nĐang ở trang {page}/{pages} · mới nhất trước\n\n' + '\n\n'.join(lines) +
                ('\n\n' + self._page_links(f'/lichsudoica {ticket_id}', page, pages) if pages > 1 else '') +
                f'\n\nMở yêu cầu: /yeucau {ticket_id}')

    def _action(self, scope, user, command, argument, body):
        parts = argument.split()
        if len(parts) == 1 and re.fullmatch(r'[0-9]{4}', parts[0]):
            ticket = self.notifications.action(scope, user, parts[0], ACTIONS[command][0])
            if ticket is not None:
                # Notifications have their own confirmation state. Keep any
                # unrelated wizard draft that B was already filling out.
                return (f"{ticket['id']} · {ticket['status_label']}\n\n" +
                        (LIVE_TEST_NOTICE + '\n\n' if self.live_test else '') +
                        comparison(ticket['a'], ticket['b'], self.live_test) +
                        f"\n\n{ticket['message']}\nXem yêu cầu: /yeucau {ticket['id']}")
        if len(parts) == 1 and re.fullmatch(r'(?:[0-9]{4}|[0-9]{6})', parts[0]):
            if not body or body['step'] != 'review':
                raise TicketError('Chưa có yêu cầu đang được xem hoặc mã đã hết hạn. Gửi /choxacnhan để xem lại trước khi xác nhận.')
            ticket_id, code = body['ticket'], parts[0]
        elif 1 <= len(parts) <= 2 and re.fullmatch(r'DC-[A-Za-z0-9-]{1,40}', parts[0], re.I):
            ticket_id = parts[0].upper()
            if len(parts) == 1:
                return self._review(scope, user, ticket_id)
            code = parts[1].upper()
        else:
            raise TicketError(f'Dùng {command} MÃ_4_SỐ từ yêu cầu đang xem; /choxacnhan để xem yêu cầu chờ bạn.')
        action = ACTIONS[command][0]
        if (not body or body['step'] != 'review' or body['ticket'] != ticket_id
                or body['code'] != code or action not in body['actions']):
            raise TicketError('Mã xác nhận không thuộc yêu cầu/bước hiện tại hoặc đã hết hạn. '
                              f'Gửi /yeucau {ticket_id} để xem lại.', 409)
        detail = self.service.detail(user, ticket_id)
        if (not body.get('participant_bindings') or
                body['participant_bindings'] != self._participant_bindings(detail)):
            self._clear(scope)
            raise TicketError('Liên kết người tham gia đã thay đổi; gửi /choxacnhan hoặc /yeucau để xem lại trước khi xác nhận.', 409)
        if action in ('accept', 'approve'):
            self._linked_pair(detail['a'], detail['b'])
        ticket = self.service.action(user, ticket_id, action, body['version'])
        self._clear(scope)
        return self._review(scope, user, ticket['id'])
