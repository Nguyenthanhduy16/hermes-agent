"""Private enrollment and durable basic-member permissions after admin approval."""
import asyncio
import re

from .commands import normalized, route
from .config import id_set
from .events import IncomingMessage
from .registration import RegistrationError, RegistrationStore
from .workload import WorkloadDirectory, WorkloadError, WorkloadSettings, normalize_phone


ADMIN_COMMANDS = frozenset(('/dangkychoduyet', '/duyetdangky', '/tuchoidangky', '/thuhoidangky', '/thanhvien'))
ENTRY_COMMANDS = frozenset(('/dangky', '/trangthaidangky'))
GUIDE = ('Đăng ký bằng tin nhắn riêng: /dangky SỐ_ĐIỆN_THOẠI\n'
         'Dùng số điện thoại hiện tại trên Workload. Quản trị xác minh và duyệt để cấp quyền thành viên theo tính năng đã bật.\n'
         'Xem trạng thái: /dangky trangthai')
AUTO_GUIDE = ('Nhắn riêng: /dangky SỐ_ĐIỆN_THOẠI\n'
              'Dùng số điện thoại hiện tại trên Workload; số khớp duy nhất sẽ được đăng ký tự động.\n'
              '/dangky trangthai — xem trạng thái')
GROUP_GUIDE = ('Bạn cần đăng ký trước khi dùng bot.\n'
               'Nhắn riêng bot: /dangky SỐ_ĐIỆN_THOẠI\n'
               'Lưu ý: dùng số điện thoại hiện tại trên Workload; không gửi số trong nhóm.')
SUCCESS_PREFIX = '✅ Đăng ký thành công!'
UNAVAILABLE = 'Đăng ký bằng số điện thoại chưa sẵn sàng. Nhờ quản trị kiểm tra cấu hình đăng ký.'
AMBIGUOUS = ('Chưa có ánh xạ lịch xác định duy nhất đúng người. Nhờ quản trị bổ sung mã người '
             'vào phân công hoặc kiểm tra tên trùng; bot chưa tra lịch cá nhân hay đổi ca theo tên này.')


class RegistrationFlow:
    def __init__(self, settings, values, sheets_client, path, *, directory=None):
        self.settings, self.sheets = settings, sheets_client
        self.enabled, self.store, self.directory = False, None, None
        self.auto_approve = False
        self.requested = str(values.get('REGISTRATION_ENABLED') or 'false').strip().lower() != 'false'
        self.error = None
        if not self.requested:
            return
        try:
            config = WorkloadSettings.from_mapping(values, sheets_client.settings if sheets_client else None)
            auto = str(values.get('REGISTRATION_AUTO_APPROVE') or 'false').strip().lower()
            if auto not in ('true', 'false'):
                raise RegistrationError('REGISTRATION_AUTO_APPROVE cần là true hoặc false.')
            self.auto_approve = auto == 'true'
            admins = id_set(values.get('REGISTRATION_ADMIN_USERS', ''))
            if not admins or not admins <= settings.allowed_users:
                raise RegistrationError('Cần khai báo quản trị đăng ký thuộc danh sách được phép dùng bot.')
            self.directory = directory or WorkloadDirectory(config, sheets_client)
            if self.directory.source_key != config.source_key:
                raise RegistrationError('Nguồn đăng ký chưa khớp cấu hình.')
            self.store = RegistrationStore(path, admins=admins)
            self.enabled = True
        except (ValueError, WorkloadError):
            self.error = UNAVAILABLE

    def close(self):
        if self.store:
            self.store.close()

    @property
    def guide(self):
        return AUTO_GUIDE if self.auto_approve else GUIDE

    def bootstrap(self, message):
        return bool(self.enabled and not self.admin_locked(message) and message.chat_type == 'PRIVATE' and route(message.text)[0] in ENTRY_COMMANDS)

    def admin_locked(self, message):
        return bool(self.store and self.store.route_locked(message.user_id,
                    message.chat_id if message.chat_type == 'PRIVATE' else None))

    def identity(self, user_id):
        record = self.store.user_state(user_id) if self.enabled else None
        return record if (record and not self.store.route_locked(user_id) and record.status == 'approved' and
                          record.source_key == self.directory.source_key) else None

    def approved(self, message):
        record = self.identity(message.user_id)
        return record if message.chat_type == 'PRIVATE' and record and record.chat_id == message.chat_id else None

    def member_allows(self, user_id, chat_id, chat_type):
        """Local basic-member gate; consumers still validate the live roster.

        No public/group grant or administrator role is implied. Existing approvals work without migrating a second ACL.
        """
        if chat_type != 'PRIVATE':
            return False
        record = self.identity(user_id)
        return bool(record and record.chat_id == chat_id)

    def admin_command(self, message):
        return bool(self.enabled and not self.admin_locked(message) and message.chat_type == 'PRIVATE' and
                    message.user_id in self.store.admins and message.user_id in self.settings.allowed_users and
                    route(message.text)[0] in ADMIN_COMMANDS)

    def blocked(self, message):
        if self.admin_locked(message):
            return True
        record = self.store.user_state(message.user_id) if self.enabled else None
        return bool(record and (record.status != 'approved' or
                    record.source_key != self.directory.source_key or
                    message.chat_type == 'PRIVATE' and record.chat_id != message.chat_id))

    async def validate(self, record, *, require_unique_name=True):
        if not self.enabled or record is None or self.identity(record.user_id) != record:
            raise RegistrationError('Liên kết chưa được duyệt hoặc đã thay đổi; hãy xem /dangky trangthai.')
        person = await self.directory.lookup(record.phone)
        return self._validated_person(record, person, require_unique_name=require_unique_name)

    def _validated_person(self, record, person, *, require_unique_name=True):
        # A revoke can arrive while the read-only Sheets request is in flight.
        if self.identity(record.user_id) != record:
            raise RegistrationError('Liên kết chưa được duyệt hoặc đã thay đổi; hãy xem /dangky trangthai.')
        if any(getattr(record, key) != getattr(person, key) for key in ('phone', 'name', 'source_key', 'fingerprint')):
            raise RegistrationError('Thông tin nhân sự đã thay đổi; nhờ quản trị xác minh lại liên kết.')
        if require_unique_name and not person.name_unique:
            raise RegistrationError(AMBIGUOUS)
        return person

    def _status(self, record, *, manual_review=False):
        if record is None:
            return self.guide
        if self.store.route_locked(record.user_id, record.chat_id):
            return 'Quyền dùng bot đang bị khóa; nhờ quản trị kiểm tra.'
        if record.status == 'approved':
            if self.auto_approve:
                return f'{SUCCESS_PREFIX}\n{record.name}\nBạn đã có quyền dùng bot.\n/menu — xem các tính năng'
            return (f'Đã duyệt đăng ký: {record.name}\nMã thành viên: {record.member_id}\n'
                    'Đã có quyền thành viên: lịch, đổi ca, nhắc việc, góp ý và issue khi đã bật.\n'
                    'Đổi ca vẫn theo phạm vi lịch đang bật; tên trùng cần quản trị bổ sung ánh xạ.\n'
                    '/menu — các tính năng')
        pending = ('Cần quản trị duyệt lại đăng ký từng bị từ chối hoặc thu hồi.' if manual_review else
                   'Đăng ký đang chờ; gửi lại /dangky SỐ_ĐIỆN_THOẠI để đối chiếu.' if self.auto_approve else
                   'Đang chờ quản trị xác minh và duyệt')
        labels = {'pending': pending,
                  'rejected': 'Đăng ký đã bị từ chối; nhờ quản trị kiểm tra',
                  'revoked': 'Liên kết đã bị thu hồi; nhờ quản trị kiểm tra'}
        # A phone lookup is not proof of ownership: do not reveal a roster name
        # or any full phone number to an unapproved requester.
        return f'{labels.get(record.status, "Chưa được duyệt")}\nMã: {record.id}\n/dangky trangthai — kiểm tra lại'

    async def handle(self, message):
        command, argument = route(message.text)
        if command not in ENTRY_COMMANDS | ADMIN_COMMANDS:
            return None
        # Preserve legacy name filters while enrollment is off, but never send
        # a phone-shaped registration to the model or the public name lookup.
        phone_like = bool(re.match(r'^[+0-9]', argument.strip()))
        if not self.requested and command == '/dangky' and not phone_like and normalized(argument) not in ('trangthai', 'trang thai'):
            return None
        if message.chat_type != 'PRIVATE':
            return 'Hãy nhắn riêng bot để đăng ký hoặc quản lý đăng ký. Không gửi số điện thoại trong nhóm.'
        if not self.enabled:
            return UNAVAILABLE
        if self.admin_locked(message):
            return 'Quyền dùng bot đang bị khóa; nhờ quản trị kiểm tra.'
        try:
            if command in ENTRY_COMMANDS:
                if command == '/trangthaidangky' or normalized(argument.strip()) in ('trangthai', 'trang thai'):
                    return self._status(self.store.status(message))
                if not argument.strip() or normalized(argument.strip()) in ('help', 'huong dan'):
                    return self.guide
                self.store.check_attempt(message)
                phone = normalize_phone(argument.strip())
                person = await self.directory.lookup(phone)
                record = self.store.request(message, person, auto_approve=self.auto_approve)
                return self._status(record, manual_review=self.auto_approve and record.status == 'pending')
            # Check admin before a network lookup or showing a pending record.
            if message.user_id not in self.store.admins or message.user_id not in self.settings.allowed_users:
                raise RegistrationError('Bạn chưa được cấp quyền quản trị đăng ký.')
            if command == '/thanhvien':
                if argument.strip() and not re.fullmatch(r'[1-9][0-9]{0,3}', argument.strip()):
                    return 'Xem thành viên: /thanhvien hoặc /thanhvien 2'
                page = int(argument.strip() or '1')
                records = self.store.members(message.user_id, source_key=self.directory.source_key, page=page, limit=5)
                if not records:
                    return 'Chưa có thành viên đã duyệt ở trang này. /thanhvien — về trang đầu'
                lines = [f'THÀNH VIÊN ĐÃ DUYỆT · Trang {page}', 'Quyền thành viên: lịch, đổi ca, nhắc việc, góp ý và issue khi đã bật; dùng trong tin nhắn riêng.']
                for record in records:
                    lines += [f'\n{record.name} · SĐT ***{record.phone[-4:]}',
                              f'Mã thành viên: {record.member_id}', f'/thuhoidangky {record.member_id}']
                if len(records) == 5:
                    lines.append(f'\nXem tiếp: /thanhvien {page + 1}')
                return '\n'.join(lines)
            if command == '/dangkychoduyet':
                if argument.strip() and not re.fullmatch(r'[1-9][0-9]{0,3}', argument.strip()):
                    return 'Xem danh sách: /dangkychoduyet hoặc /dangkychoduyet 2'
                page = int(argument.strip() or '1')
                records = self.store.pending(message.user_id, page=page, limit=5)
                if not records:
                    return 'Chưa có đăng ký chờ ở trang này. /dangkychoduyet — về trang đầu'
                lines = [f'ĐĂNG KÝ CHỜ DUYỆT · Trang {page}', 'Xác minh đúng chủ tài khoản trước khi duyệt.']
                for record in records:
                    lines += [f'\n{record.id} · {record.name} · SĐT ***{record.phone[-4:]}',
                              f'Zalo user_id: {record.user_id}\nChat riêng: {record.chat_id}',
                              f'/duyetdangky {record.id}\n/tuchoidangky {record.id}']
                if len(records) == 5:
                    lines.append(f'\nXem tiếp: /dangkychoduyet {page + 1}')
                return '\n'.join(lines)
            if command == '/thuhoidangky':
                record = self.store.revoke(message.user_id, argument.strip())
                return f'Đã thu hồi liên kết {record.member_id}. Các quyền từ đăng ký không còn hiệu lực.'
            record = self.store.get_for_admin(message.user_id, argument.strip())
            if command == '/tuchoidangky':
                self.store.reject(message.user_id, record.id)
                return f'Đã từ chối đăng ký {record.id}.'
            person = await self.directory.lookup(record.phone)
            record = self.store.approve(message.user_id, record.id, person)
            return (f'Đã duyệt {record.id} · {record.name}\nMã thành viên: {record.member_id}\n'
                    'Đã tự cấp quyền thành viên trong tin nhắn riêng: lịch, đổi ca, nhắc việc, góp ý và issue khi đã bật; không cần thêm ID vào .env.\n'
                    'Đổi ca vẫn theo phạm vi lịch đang bật; tên trùng cần quản trị bổ sung ánh xạ.\n'
                    'Người dùng gửi /dangky trangthai để xem kết quả. /thanhvien — quản lý thành viên.\n'
                    f'Thu hồi: /thuhoidangky {record.member_id}')
        except (RegistrationError, WorkloadError) as error:
            return str(error)

    async def bootstrap_tickets(self, chat):
        """Restore approved DM bindings into a new Tickets DB with two reads.

        Missing, stale or conflicting individuals do not block valid people.
        No approval, role, live schedule or legacy test database is changed.
        Every binding is rechecked against the current local grant after I/O.
        """
        result = {'linked': 0, 'skipped': 0}
        if not self.enabled:
            return result
        if not self.sheets or chat.service.mode not in ('lab_live', 'lab_live_test'):
            return result
        provider = chat.service.provider
        if provider.settings.spreadsheet_id != self.sheets.settings.spreadsheet_id:
            raise RegistrationError('Nguồn lịch và nguồn đăng ký khác nhau; cần quản trị kiểm tra ánh xạ.')
        records = self.store.approved_records(self.directory.source_key)
        if not records:
            return result
        try:
            people = await self.directory.lookup_many(record.phone for record in records)
            members = await asyncio.to_thread(provider.inspect_members)
        except Exception:
            raise RegistrationError('Chưa đồng bộ được thành viên đã duyệt; kiểm tra kết nối và nguồn lịch rồi thử lại.') from None
        for record in records:
            person = people.get(record.phone)
            if person is None:
                result['skipped'] += 1
                continue
            try:
                await self.sync_tickets(record, chat, _person=person, _members=members)
                result['linked'] += 1
            except (RegistrationError, WorkloadError):
                result['skipped'] += 1
        return result

    async def sync_tickets(self, record, chat, *, _person=None, _members=None):
        """Idempotent local provisioning; source writes remain TicketService's job.

        Approval is the member grant. Current roster validation and Tickets
        source/mode limits remain required before any binding is created.
        """
        if (not self.enabled or not self.sheets or record is None or
                not self.member_allows(record.user_id, record.chat_id, 'PRIVATE') or
                self.identity(record.user_id) != record):
            raise RegistrationError('Cần đăng ký thành công trong đúng chat riêng để dùng đổi ca.')
        person = (await self.validate(record) if _person is None else self._validated_person(record, _person))
        if chat.service.mode not in ('lab_live', 'lab_live_test'):
            raise RegistrationError('Nguồn Tickets này cần quản trị liên kết mã thành viên riêng; chưa tự ghép theo tên.')
        if chat.live_test and person.name not in chat.service.test_members:
            raise RegistrationError('Đổi ca đang giới hạn ở hai người thử nghiệm; đăng ký không mở rộng phạm vi này.')

        def bind():
            from .tickets.user_import import import_users
            provider = chat.service.provider
            if provider.settings.spreadsheet_id != self.sheets.settings.spreadsheet_id:
                raise RegistrationError('Nguồn lịch và nguồn đăng ký khác nhau; cần quản trị kiểm tra ánh xạ.')
            members = provider.inspect_members() if _members is None else _members
            matches = [item for item in members if item['member_name'] == person.name]
            if len(matches) != 1:
                raise RegistrationError(AMBIGUOUS)
            if self.identity(record.user_id) != record:
                raise RegistrationError('Liên kết đã thay đổi; hãy xem /dangky trangthai trước khi đổi ca.')
            # Reuse a verified legacy binding, but never overwrite a different
            # account/route or promote an existing member to a manager.
            current = chat.identities.resolve(chat.bot_scope, record.user_id, record.chat_id)
            legacy_binding = current is not None
            if current:
                if current['member_id'] != matches[0]['member_id'] or current['name'] != person.name:
                    raise RegistrationError('Liên kết Tickets đang thuộc người khác; nhờ quản trị xử lý xung đột.')
            else:
                import_users(chat.store, chat.bot_scope, [dict(matches[0], user_id=record.user_id, chat_id=record.chat_id)])
                current = chat.identities.resolve(chat.bot_scope, record.user_id, record.chat_id)
            with chat.store.transaction() as db:
                db.execute('CREATE TABLE IF NOT EXISTS ticket_registration_links ('
                           'bot TEXT NOT NULL, member_id TEXT NOT NULL, registration_id TEXT NOT NULL, '
                           'account TEXT NOT NULL, PRIMARY KEY(bot,member_id))')
                previous = db.execute('SELECT registration_id,account FROM ticket_registration_links '
                                      'WHERE bot=? AND member_id=?', (chat.bot_scope, record.member_id)).fetchone()
            if (previous and (previous['registration_id'] != record.id or previous['account'] != current['id'])
                    or previous is None and legacy_binding):
                # First adoption or a new approval must not revive confirmations
                # from the old binding. Repeated sync preserves this revision.
                chat.identities.bind(chat.bot_scope, record.user_id, record.chat_id, current['id'])
            with chat.store.transaction() as db:
                db.execute('INSERT INTO ticket_registration_links VALUES (?,?,?,?) '
                           'ON CONFLICT(bot,member_id) DO UPDATE SET registration_id=excluded.registration_id,account=excluded.account',
                           (chat.bot_scope, record.member_id, record.id, current['id']))
        try:
            await asyncio.to_thread(bind)
        except RegistrationError:
            raise
        except Exception:
            raise RegistrationError('Chưa liên kết được danh tính với Tickets; nhờ quản trị kiểm tra quyền và nguồn lịch.') from None
