"""Admin member operations on the running bot's stores, never on a copy.

HTTP authentication and the ``members`` capability are enforced by the caller.
An actor label is audit attribution, not an alternative registration-admin ACL.
All public methods are async and must run on the gateway's event loop.
"""
import asyncio
from contextlib import nullcontext
import hashlib
import json
import re
import secrets
import time

from .commands import normalized
from .registration import RegistrationError
from .tickets.service import ACTIVE, digest, password_hash
from .workload import WorkloadError, normalize_phone


class AdminMemberError(ValueError):
    pass


class MemberAdmin:
    def __init__(self, work, *, clock=time.time, conversations=None):
        self.work, self.clock = work, clock
        self.conversations = conversations
        self._previews = {}
        self._registration_previews = {}

    @property
    def flow(self):
        flow = self.work.registration
        if not flow.enabled or not flow.store:
            raise AdminMemberError('Đăng ký thành viên chưa được cấu hình đầy đủ.')
        return flow

    @staticmethod
    def _actor(actor):
        if not isinstance(actor, str) or not re.fullmatch(r'web:[A-Za-z0-9_.@-]{1,100}', actor):
            raise AdminMemberError('Phiên quản trị chưa hợp lệ.')

    @staticmethod
    def _page(page, limit):
        if type(page) is not int or not 1 <= page <= 10000 or type(limit) is not int or not 1 <= limit <= 100:
            raise AdminMemberError('Trang hoặc số dòng mỗi trang chưa hợp lệ.')

    def _record(self, member_id):
        if not isinstance(member_id, str) or not re.fullmatch(r'member-[a-f0-9]{32}', member_id):
            raise AdminMemberError('Mã thành viên chưa hợp lệ.')
        record = self.flow.store.latest_member(member_id)
        if record is None:
            raise AdminMemberError('Không tìm thấy thành viên.')
        return record

    def _basic(self, record):
        control = self.flow.store.member_control(record.member_id)
        return dict(member_id=record.member_id, name=record.name, status=record.status,
                    locked=control['locked'], revision=control['revision'],
                    phone_masked='******' + record.phone[-4:], request_id=record.id,
                    zalo_user_id=record.user_id, private_chat_id=record.chat_id,
                    group_chat_ids=[], group_note='Quyền nhóm cấu hình riêng; không suy ra từ chat riêng.',
                    schedule_name=record.name, name_unique=record.name_unique,
                    created=record.created, updated=max(record.updated, control['updated']))

    async def _chat(self):
        # Opening does not bootstrap or repair existing bindings implicitly.
        return await self.work._get_tickets()

    def _local_link(self, chat, record):
        if chat is None:
            return dict(link_status='disabled', link_reason='Đổi ca chưa bật hoặc chưa sẵn sàng.', tickets_account=None)
        current = chat.identities.resolve(chat.bot_scope, record.user_id, record.chat_id)
        if not current:
            return dict(link_status='missing', link_reason='Chưa có liên kết Tickets còn hiệu lực.', tickets_account=None)
        account = {key: current[key] for key in ('id', 'member_id', 'name', 'role')}
        if not record.name_unique:
            state, reason = 'conflict', 'Tên trùng: cần mã phân biệt trong nguồn lịch; chưa thể xác nhận ánh xạ.'
        elif current['name'] != record.name:
            state, reason = 'conflict', 'Liên kết Tickets đang thuộc tên khác với đăng ký.'
        else:
            state, reason = 'linked', 'Có liên kết cục bộ; chức năng lịch vẫn kiểm tra nguồn trước khi sử dụng.'
        return dict(link_status=state, link_reason=reason, tickets_account=account)

    async def list_members(self, query='', status='', link='', page=1, limit=20):
        self._page(page, limit)
        if not isinstance(query, str) or len(query) > 160 or status not in ('', 'pending', 'approved', 'rejected', 'revoked', 'reset', 'locked'):
            raise AdminMemberError('Bộ lọc thành viên chưa hợp lệ.')
        if link not in ('', 'linked', 'missing', 'conflict', 'disabled'):
            raise AdminMemberError('Bộ lọc liên kết chưa hợp lệ.')
        chat = await self._chat()
        def read():
            with chat.store.lock if chat else nullcontext():
                result = []
                for record in self.flow.store.latest_members():
                    if not status and record.status == 'reset':
                        continue
                    item = self._basic(record) | self._local_link(chat, record)
                    if query and normalized(query) not in normalized(record.name + ' ' + record.member_id):
                        continue
                    if status and not (item['locked'] if status == 'locked' else record.status == status):
                        continue
                    if link and item['link_status'] != link:
                        continue
                    result.append(item)
                return dict(items=result[(page - 1) * limit:page * limit], total=len(result), page=page, limit=limit)
        return await asyncio.to_thread(read)

    async def _validated(self, record, *, unique=False):
        if record.source_key != self.flow.directory.source_key:
            raise AdminMemberError('Nguồn đăng ký đã thay đổi; cần xác minh lại trước khi sửa quyền hoặc liên kết.')
        try:
            person = await self.flow.directory.lookup(record.phone)
        except (RegistrationError, WorkloadError):
            raise AdminMemberError('Chưa đối chiếu được đúng người trong Workload; kiểm tra nguồn trước khi thay đổi.') from None
        if any(getattr(record, key) != getattr(person, key) for key in ('phone', 'name', 'source_key', 'fingerprint')):
            raise AdminMemberError('Thông tin Workload đã thay đổi; cần xác minh lại đăng ký.')
        if unique and not person.name_unique:
            raise AdminMemberError('Tên trùng trong Workload; cần mã phân biệt và ánh xạ lịch, chưa được tự chọn theo tên.')
        if self._record(record.member_id) != record:
            raise AdminMemberError('Đăng ký vừa thay đổi. Tải lại thông tin thành viên.')
        return person

    async def _source_candidate(self, chat, record):
        if not chat or chat.service.mode not in ('lab_live', 'lab_live_test'):
            raise AdminMemberError('Bản này chỉ sửa liên kết cho nguồn lịch Lab đã cấu hình.')
        if record.status != 'approved':
            raise AdminMemberError('Chỉ sửa liên kết của thành viên đã được duyệt; thao tác này không cấp quyền đăng ký.')
        await self._validated(record, unique=True)
        provider = chat.service.provider
        if not self.work.sheets or provider.settings.spreadsheet_id != self.work.sheets.settings.spreadsheet_id:
            raise AdminMemberError('Nguồn lịch và Workload không khớp.')
        if chat.service.mode == 'lab_live_test' and record.name not in chat.service.test_members:
            raise AdminMemberError('Thành viên nằm ngoài phạm vi đổi ca đang thử nghiệm.')
        try:
            members = await asyncio.to_thread(provider.inspect_members)
        except Exception:
            raise AdminMemberError('Chưa đọc được danh sách trên lịch. Thử lại sau; chưa sửa dữ liệu.') from None
        found = [item for item in members if item.get('member_name') == record.name]
        if len(found) != 1 or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', str(found[0].get('member_id', ''))):
            raise AdminMemberError('Tên chưa khớp duy nhất một mã trên lịch. Cần bổ sung ánh xạ; không tự đoán.')
        source = found[0]
        def candidate():
            with chat.store.lock:
                rows = chat.store.db.execute('SELECT id,name,role,member_id,active FROM ticket_accounts WHERE member_id=?',
                                             (source['member_id'],)).fetchall()
                active = [dict(row) for row in rows if row['active']]
                if len(active) > 1 or rows and not active:
                    raise AdminMemberError('Tài khoản Tickets bị thu hồi hoặc trùng; cần xử lý dữ liệu trước.')
                if active:
                    account = active[0]
                    if account['name'] != record.name or account['role'] != 'member':
                        raise AdminMemberError('Tên hoặc vai trò tài khoản Tickets không khớp; chưa thay đổi liên kết.')
                else:
                    account = dict(id='zalo-' + digest(source['member_id'])[:24], name=record.name,
                                   role='member', member_id=source['member_id'], active=1)
                return dict(account_id=account['id'], member_id=account['member_id'], name=account['name'],
                            create_account=not bool(active))
        return await asyncio.to_thread(candidate)

    async def detail(self, member_id):
        record, chat = self._record(member_id), await self._chat()
        def read():
            with chat.store.lock if chat else nullcontext():
                return self._basic(record) | self._local_link(chat, record)
        result = await asyncio.to_thread(read)
        result['candidates'] = []
        try:
            candidate = await self._source_candidate(chat, record)
            result['candidates'] = [candidate]
            result['source_checked'] = True
            current = result['tickets_account']
            if current and (current['member_id'] != candidate['member_id'] or current['id'] != candidate['account_id']):
                result.update(link_status='conflict', link_reason='Liên kết hiện tại khác mã đã đối chiếu trên lịch.')
        except AdminMemberError as error:
            result.update(source_checked=False, source_reason=str(error))
        return result

    async def set_locked(self, member_id, locked, actor, expected_revision, confirm=False):
        self._actor(actor)
        if type(locked) is not bool or type(expected_revision) is not int or expected_revision < 0 or confirm is not True:
            raise AdminMemberError('Cần xác nhận thao tác và phiên bản thông tin đang xem.')
        record, chat = self._record(member_id), await self._chat()
        if not locked:
            await self._validated(record)
        def change():
            # Same order as Tickets guards: wait for an in-flight network write
            # to finish before changing grants; no second DB writer/process.
            with chat.store.lock if chat else nullcontext():
                store = self.flow.store
                with store._transaction() as db:
                    if self._record(member_id) != record:
                        raise AdminMemberError('Đăng ký vừa thay đổi; tải lại trước khi xác nhận.')
                    before = store.member_control(member_id)
                    if before['revision'] != expected_revision:
                        raise AdminMemberError('Thông tin vừa được cập nhật; tải lại trước khi xác nhận.')
                    if before['locked'] != locked:
                        store._bump_revision(db, member_id)
                        db.execute('UPDATE registration_member_controls SET locked=? WHERE member_id=?', (int(locked), member_id))
                        store._audit_admin(db, member_id, actor, 'locked' if locked else 'unlocked',
                                           {'locked': before['locked'], 'status': record.status},
                                           {'locked': locked, 'status': record.status})
                    return self._basic(record) | {'message': 'Đã khóa quyền dùng bot.' if locked else 'Đã bỏ khóa; trạng thái đăng ký và quyền nhóm giữ nguyên.'}
        return await asyncio.to_thread(change)

    def _snapshot(self, chat, record, candidate):
        with chat.store.lock:
            account = chat.store.account(candidate['account_id'])
            if candidate['create_account']:
                if chat.store.db.execute('SELECT 1 FROM ticket_accounts WHERE id=? OR (member_id=? AND active=1)',
                                         (candidate['account_id'], candidate['member_id'])).fetchone():
                    raise AdminMemberError('Tài khoản đích vừa thay đổi; tải lại trước khi sửa.')
            elif (not account or any(account[key] != candidate[source] for key, source in
                    (('id', 'account_id'), ('member_id', 'member_id'), ('name', 'name'))) or account['role'] != 'member'):
                raise AdminMemberError('Tài khoản đích vừa thay đổi; tải lại trước khi sửa.')
            rows = [dict(row) for row in chat.store.db.execute(
                'SELECT * FROM ticket_zalo_bindings WHERE bot=? AND (user_id=? OR chat_id=? OR account=?) ORDER BY account',
                (chat.bot_scope, record.user_id, record.chat_id, candidate['account_id']))]
            if any(row['user_id'] != record.user_id or row['chat_id'] != record.chat_id for row in rows):
                raise AdminMemberError('Tài khoản đích hoặc chat đã thuộc người khác. Chưa ghi đè; cần xác minh và thu hồi đúng liên kết cũ.')
            accounts = {row['account'] for row in rows} | {candidate['account_id']}
            members = {row['member_id'] for row in rows} | {candidate['member_id']}
            for ticket in chat.store.db.execute('SELECT creator,recipient,body FROM exchange_tickets WHERE status IN (?,?,?,?)', ACTIVE):
                body = json.loads(ticket['body'])
                if ticket['creator'] in accounts or ticket['recipient'] in members or any(
                        body.get(side, {}).get('member_id') in members for side in ('a', 'b')):
                    raise AdminMemberError('Còn yêu cầu đổi ca đang chờ hoặc cần kiểm tra kết quả. Hoàn tất/hủy đúng yêu cầu trước khi sửa liên kết.')
            return dict(record_id=record.id, user_id=record.user_id, chat_id=record.chat_id,
                        registration_revision=self.flow.store.member_control(record.member_id)['revision'],
                        bindings=rows, candidate=candidate)

    async def preview_link(self, member_id, target_account_id, actor):
        self._actor(actor)
        record, chat = self._record(member_id), await self._chat()
        candidate = await self._source_candidate(chat, record)
        if target_account_id != candidate['account_id']:
            raise AdminMemberError('Chọn đúng tài khoản đã đối chiếu từ danh sách đề xuất.')
        snapshot = await asyncio.to_thread(self._snapshot, chat, record, candidate)
        self._previews = {key: value for key, value in self._previews.items() if value['expires'] > self.clock()}
        if len(self._previews) >= 100:
            raise AdminMemberError('Có nhiều bản xem trước đang mở; thử lại sau ít phút.')
        token = secrets.token_urlsafe(32)
        self._previews[hashlib.sha256(token.encode()).hexdigest()] = dict(
            actor=actor, member_id=member_id, snapshot=snapshot, expires=self.clock() + 300)
        return dict(preview_token=token, expires=self.clock() + 300,
                    before=snapshot['bindings'], after=dict(candidate, zalo_user_id=record.user_id, private_chat_id=record.chat_id),
                    message='Chỉ sửa liên kết nội bộ của đúng thành viên; không sửa lịch, cấp vai trò hoặc chuyển tài khoản của người khác.')

    async def confirm_link(self, preview_token, actor):
        self._actor(actor)
        if not isinstance(preview_token, str) or not 20 <= len(preview_token) <= 100:
            raise AdminMemberError('Bản xem trước chưa hợp lệ.')
        key = hashlib.sha256(preview_token.encode()).hexdigest()
        preview = self._previews.get(key)
        if not preview or preview['actor'] != actor or preview['expires'] <= self.clock():
            raise AdminMemberError('Bản xem trước hết hạn hoặc không thuộc phiên quản trị này.')
        record, chat = self._record(preview['member_id']), await self._chat()
        candidate = await self._source_candidate(chat, record)
        def apply():
            with chat.store.lock:
                store = self.flow.store
                with store._transaction() as reg_db:
                    if preview['expires'] <= self.clock() or self._previews.get(key) is not preview:
                        raise AdminMemberError('Bản xem trước hết hạn hoặc đã được sử dụng.')
                    if self._record(record.member_id) != record or self._snapshot(chat, record, candidate) != preview['snapshot']:
                        raise AdminMemberError('Thông tin hoặc liên kết vừa thay đổi; tạo bản xem trước mới.')
                    before = preview['snapshot']['bindings']
                    with chat.store.transaction() as db:
                        if candidate['create_account']:
                            db.execute('INSERT INTO ticket_accounts VALUES (?,?,?,?,?,1)',
                                       (candidate['account_id'], candidate['name'], 'member', candidate['member_id'], password_hash(secrets.token_urlsafe(48))))
                        # Same checks and mutation as IdentityStore.bind, inside
                        # this transaction together with an immutable audit row.
                        db.execute('INSERT INTO ticket_zalo_bindings VALUES (?,?,?,?,?,?,?) '
                                   'ON CONFLICT(bot,user_id,chat_id) DO UPDATE SET account=excluded.account,member_id=excluded.member_id,'
                                   'role=excluded.role,revision=excluded.revision',
                                   (chat.bot_scope, record.user_id, record.chat_id, candidate['account_id'], candidate['member_id'], 'member', secrets.token_urlsafe(32)))
                        db.execute('CREATE TABLE IF NOT EXISTS ticket_registration_links (bot TEXT NOT NULL,member_id TEXT NOT NULL,'
                                   'registration_id TEXT NOT NULL,account TEXT NOT NULL,PRIMARY KEY(bot,member_id))')
                        db.execute('INSERT INTO ticket_registration_links VALUES (?,?,?,?) ON CONFLICT(bot,member_id) '
                                   'DO UPDATE SET registration_id=excluded.registration_id,account=excluded.account',
                                   (chat.bot_scope, record.member_id, record.id, candidate['account_id']))
                        after = dict(candidate, user_id=record.user_id, chat_id=record.chat_id)
                        db.execute('CREATE TABLE IF NOT EXISTS ticket_admin_member_audit (id INTEGER PRIMARY KEY,member_id TEXT NOT NULL,'
                                   'actor TEXT NOT NULL,at REAL NOT NULL,before_json TEXT NOT NULL,after_json TEXT NOT NULL)')
                        db.execute('INSERT INTO ticket_admin_member_audit(member_id,actor,at,before_json,after_json) VALUES (?,?,?,?,?)',
                                   (record.member_id, actor, self.clock(), json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))
                        store._bump_revision(reg_db, record.member_id)
                        store._audit_admin(reg_db, record.member_id, actor, 'tickets_link', {'bindings': before}, after)
                    self._previews.pop(key, None)
                    return dict(ok=True, member_id=record.member_id, revision=store.member_control(record.member_id)['revision'],
                                message='Đã cập nhật liên kết Tickets; lịch thật không thay đổi.')
        return await asyncio.to_thread(apply)

    async def _correction_person(self, phone):
        try:
            person = await self.flow.directory.lookup(normalize_phone(phone))
            self.flow.store._person(person)
        except (RegistrationError, WorkloadError) as error:
            raise AdminMemberError(str(error)) from None
        if person.source_key != self.flow.directory.source_key:
            raise AdminMemberError('Nguồn Workload đã thay đổi; tải lại trước khi sửa đăng ký.')
        if not person.name_unique:
            raise AdminMemberError('Tên trùng trong Workload; cần bổ sung mã phân biệt trước khi sửa đăng ký.')
        return person

    async def _correction_schedule(self, chat, person):
        if chat is None:
            if (str(getattr(self.work, '_tickets_values', {}).get('TICKETS_CHAT_MODE') or 'off').lower() != 'off' or
                    getattr(self.work, '_tickets_error', False)):
                raise AdminMemberError('Đổi ca đang bật nhưng chưa kiểm tra được liên kết. Thử lại sau.')
            return None
        if (chat.service.mode not in ('lab_live', 'lab_live_test') or not self.work.sheets or
                chat.service.provider.settings.spreadsheet_id != self.work.sheets.settings.spreadsheet_id):
            raise AdminMemberError('Nguồn Tickets chưa hỗ trợ sửa đăng ký từ Workload này.')
        if chat.service.mode == 'lab_live_test' and person.name not in chat.service.test_members:
            raise AdminMemberError('Người được chọn nằm ngoài phạm vi đổi ca đang thử nghiệm.')
        try:
            members = await asyncio.to_thread(chat.service.provider.inspect_members)
        except Exception:
            raise AdminMemberError('Chưa đọc được danh sách lịch; thử lại sau. Chưa sửa đăng ký.') from None
        found = [row for row in members if row.get('member_name') == person.name]
        if len(found) != 1 or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', str(found[0].get('member_id', ''))):
            raise AdminMemberError('Tên chưa khớp duy nhất một mã trên lịch. Cần bổ sung ánh xạ trước khi sửa.')
        return dict(member_id=found[0]['member_id'], name=person.name)

    def _correction_tickets_snapshot(self, chat, record, schedule):
        if chat is None:
            return None
        # Caller holds the Tickets lock, including through final registration
        # commit. Never detach a different account's route or an admin binding.
        rows = [dict(row) for row in chat.store.db.execute(
            'SELECT * FROM ticket_zalo_bindings WHERE bot=? AND (user_id=? OR chat_id=? OR member_id=?) ORDER BY account',
            (chat.bot_scope, record.user_id, record.chat_id, schedule['member_id']))]
        if any(row['user_id'] != record.user_id or row['chat_id'] != record.chat_id for row in rows):
            raise AdminMemberError('Liên kết lịch đích đã thuộc tài khoản Zalo khác; chưa ghi đè.')
        if any(row['role'] != 'member' for row in rows):
            raise AdminMemberError('Liên kết có quyền quản trị; cần người vận hành kiểm tra riêng.')
        accounts = [dict(row) for row in chat.store.db.execute(
            'SELECT id,name,role,member_id,active FROM ticket_accounts WHERE member_id=? ORDER BY id',
            (schedule['member_id'],))]
        if accounts and (len(accounts) != 1 or accounts[0]['active'] != 1 or
                         accounts[0]['role'] != 'member' or accounts[0]['name'] != schedule['name']):
            raise AdminMemberError('Tài khoản lịch đích bị khóa hoặc xung đột; cần kiểm tra trước khi sửa.')
        old_accounts = [dict(row) for row in chat.store.db.execute(
            'SELECT id,member_id FROM ticket_accounts WHERE name=?', (record.name,))]
        member_ids = {row['member_id'] for row in rows + accounts + old_accounts} | {schedule['member_id']}
        account_ids = {row['account'] for row in rows} | {row['id'] for row in accounts + old_accounts}
        for ticket in chat.store.db.execute('SELECT creator,recipient,body FROM exchange_tickets WHERE status IN (?,?,?,?)', ACTIVE):
            body = json.loads(ticket['body'])
            if ticket['creator'] in account_ids or ticket['recipient'] in member_ids or any(
                    body.get(side, {}).get('member_id') in member_ids for side in ('a', 'b')):
                raise AdminMemberError('Còn yêu cầu đổi ca đang xử lý. Hoàn tất hoặc hủy yêu cầu trước khi sửa đăng ký.')
        return dict(bindings=rows, accounts=accounts, old_accounts=old_accounts)

    async def preview_registration(self, member_id, phone, reason, actor):
        self._actor(actor)
        record = self._record(member_id)
        person = await self._correction_person(phone)
        chat = await self._chat()
        schedule = await self._correction_schedule(chat, person)
        try:
            reason = self.flow.store._correction_actor_reason(actor, reason)
            with chat.store.lock if chat else nullcontext():
                registration = self.flow.store.correction_snapshot(record, person)
                tickets = self._correction_tickets_snapshot(chat, record, schedule)
        except RegistrationError as error:
            raise AdminMemberError(str(error)) from None
        self._registration_previews = {key: value for key, value in self._registration_previews.items()
                                       if value['expires'] > self.clock()}
        if len(self._registration_previews) >= 100:
            raise AdminMemberError('Có nhiều bản xem trước đang mở; thử lại sau ít phút.')
        token = secrets.token_urlsafe(32)
        self._registration_previews[hashlib.sha256(token.encode()).hexdigest()] = dict(
            record=record, person=person, actor=actor, registration=registration, tickets=tickets,
            schedule=schedule, reason=reason, expires=self.clock() + 300)
        return dict(preview_token=token, expires=self.clock() + 300,
                    before=self._basic(record),
                    after=dict(name=person.name, phone_masked='******' + person.phone[-4:],
                               member_id=registration.get('target_member_id'),
                               zalo_user_id=record.user_id, private_chat_id=record.chat_id),
                    message='Xác minh đúng chủ tài khoản trước khi lưu. Đăng ký cũ sẽ bị thu hồi và người đúng được duyệt. '
                            'Ngữ cảnh cũ được làm mới, nhắc việc thủ công cũ được dừng; lịch sử và issue không chuyển sang người mới. '
                            'Không sửa ô lịch trên Sheets. Liên kết đổi ca được tạo lại khi thành viên dùng bot.')

    async def confirm_registration(self, preview_token, actor, *, authorize=None):
        self._actor(actor)
        if not isinstance(preview_token, str) or not 20 <= len(preview_token) <= 100:
            raise AdminMemberError('Bản xem trước chưa hợp lệ.')
        key = hashlib.sha256(preview_token.encode()).hexdigest()
        preview = self._registration_previews.get(key)
        if not preview or preview['actor'] != actor or preview['expires'] <= self.clock():
            raise AdminMemberError('Bản xem trước hết hạn hoặc không thuộc quản trị này.')
        person = await self._correction_person(preview['person'].phone)
        if person != preview['person']:
            raise AdminMemberError('Workload vừa thay đổi; tạo bản xem trước mới.')
        chat = await self._chat()
        schedule = await self._correction_schedule(chat, person)
        record, store = preview['record'], self.flow.store
        # No awaits once mutations start: ReminderStore belongs to the bot loop.
        # Dependent cleanup only removes old capabilities. The new identity is
        # activated by the final, atomic registration transaction, so interruption
        # cannot grant the target identity before cleanup has succeeded.
        with chat.store.lock if chat else nullcontext():
            with store.lock:
                if authorize:
                    authorize()
                if preview['expires'] <= self.clock() or self._registration_previews.get(key) is not preview:
                    raise AdminMemberError('Bản xem trước hết hạn hoặc đã được sử dụng.')
                try:
                    if (store.correction_snapshot(record, person) != preview['registration'] or
                            schedule != preview['schedule'] or
                            self._correction_tickets_snapshot(chat, record, schedule) != preview['tickets']):
                        raise AdminMemberError('Thông tin vừa thay đổi; tạo bản xem trước mới.')
                    self._registration_previews.pop(key, None)
                    self.work.reminders.detach_registration(record.user_id)
                    self.work.issues.detach_registration(record.user_id)
                    if self.conversations is not None:
                        self.conversations.detach_registration_context(record.user_id, record.member_id, actor)
                    if chat:
                        with chat.store.transaction() as db:
                            db.execute('DELETE FROM ticket_zalo_bindings WHERE bot=? AND user_id=? AND chat_id=?',
                                       (chat.bot_scope, record.user_id, record.chat_id))
                            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_registration_links'").fetchone():
                                db.execute('DELETE FROM ticket_registration_links WHERE bot=? AND member_id=?',
                                           (chat.bot_scope, record.member_id))
                    result = store.correct_registration(actor, record, person, preview['registration'], preview['reason'])
                except RegistrationError as error:
                    raise AdminMemberError(str(error)) from None
        return self._basic(result) | dict(ok=True, message='Đã sửa đăng ký thành công: ' + result.name +
                '. Thành viên có thể gửi /dangky trangthai, /lichcuatoi hoặc /doica; không cần đăng ký lại.')

    def _reset_tickets_snapshot(self, chat, record):
        """Inspect only local ownership and open work; roster reads are unnecessary.

        A reset must work when the phone/name in Workload was corrected. It
        removes a route, never grants or selects a replacement person.
        """
        if chat is None:
            if (str(getattr(self.work, '_tickets_values', {}).get('TICKETS_CHAT_MODE') or 'off').lower() != 'off' or
                    getattr(self.work, '_tickets_error', False)):
                raise AdminMemberError('Đổi ca đang bật nhưng chưa kiểm tra được liên kết. Thử lại sau.')
            return None
        db = chat.store.db
        bindings = [dict(row) for row in db.execute(
            'SELECT * FROM ticket_zalo_bindings WHERE bot=? AND (user_id=? OR chat_id=?) ORDER BY account',
            (chat.bot_scope, record.user_id, record.chat_id))]
        if any(row['user_id'] != record.user_id or row['chat_id'] != record.chat_id for row in bindings):
            raise AdminMemberError('Liên kết chat đang xung đột với tài khoản khác; cần kiểm tra trước khi xóa đăng ký.')
        if any(row['role'] != 'member' for row in bindings):
            raise AdminMemberError('Liên kết có quyền quản trị; cần người vận hành kiểm tra riêng.')
        links = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_registration_links'").fetchone():
            links = [dict(row) for row in db.execute(
                'SELECT * FROM ticket_registration_links WHERE bot=? AND member_id=?',
                (chat.bot_scope, record.member_id))]
            if any(row['registration_id'] != record.id for row in links):
                raise AdminMemberError('Liên kết đổi ca thuộc đăng ký khác; tải lại và kiểm tra trước khi xóa.')
        accounts = {row['account'] for row in bindings + links}
        names = [dict(row) for row in db.execute('SELECT id,member_id FROM ticket_accounts WHERE name=?', (record.name,))]
        # A legacy account may exist without a route. Its outstanding work must
        # still be resolved, but the reset never changes its account or owners.
        accounts.update(row['id'] for row in names)
        members = {row['member_id'] for row in bindings + names}
        for link in links:
            owner = db.execute('SELECT user_id,chat_id,role FROM ticket_zalo_bindings WHERE bot=? AND account=?',
                               (chat.bot_scope, link['account'])).fetchone()
            if owner and (owner['user_id'] != record.user_id or owner['chat_id'] != record.chat_id or owner['role'] != 'member'):
                raise AdminMemberError('Liên kết đổi ca đang thuộc tài khoản khác; chưa xóa đăng ký.')
            account = db.execute('SELECT member_id,role FROM ticket_accounts WHERE id=?', (link['account'],)).fetchone()
            if account:
                if account['role'] != 'member':
                    raise AdminMemberError('Liên kết có quyền quản trị; cần người vận hành kiểm tra riêng.')
                members.add(account['member_id'])
        for ticket in db.execute('SELECT creator,recipient,body FROM exchange_tickets WHERE status IN (?,?,?,?)', ACTIVE):
            body = json.loads(ticket['body'])
            if (ticket['creator'] in accounts or ticket['recipient'] in members or any(
                    body.get(side, {}).get('member_id') in members for side in ('a', 'b'))):
                raise AdminMemberError('Còn yêu cầu đổi ca đang xử lý. Hoàn tất hoặc hủy yêu cầu trước khi xóa đăng ký.')
        return dict(bindings=bindings, links=links)

    async def reset_registration(self, member_id, request_id, expected_revision, reason, actor, *, confirm=False, authorize=None):
        """Withdraw this enrollment, keeping a tombstone until a fresh DM request.

        Cleanup only removes capabilities and completes before the final store
        transaction. No await between cleanup and commit: stale model turns or
        background deliveries cannot inherit a newly registered identity.
        """
        self._actor(actor)
        if (confirm is not True or type(expected_revision) is not int or expected_revision < 0 or
                not isinstance(request_id, str) or not re.fullmatch(r'DK-[A-F0-9]{8}', request_id)):
            raise AdminMemberError('Cần xác nhận đúng đăng ký và phiên bản thông tin đang xem.')
        record = self._record(member_id)
        if record.id != request_id:
            raise AdminMemberError('Đăng ký vừa thay đổi; tải lại trước khi xóa.')
        chat = await self._chat()
        store = self.flow.store
        with chat.store.lock if chat else nullcontext():
            with store.lock:
                if authorize:
                    authorize()
                try:
                    reason = store._correction_actor_reason(actor, reason)
                    snapshot = store.reset_snapshot(record)
                    if store.member_control(member_id)['revision'] != expected_revision:
                        raise AdminMemberError('Thông tin vừa được cập nhật; tải lại trước khi xóa.')
                    self._reset_tickets_snapshot(chat, record)
                    self.work.reminders.detach_registration(record.user_id)
                    self.work.issues.detach_registration(record.user_id)
                    self.work._managed_issue_store.detach_registration(record.member_id, record.user_id, record.chat_id)
                    if self.conversations is not None:
                        self.conversations.detach_registration_context(record.user_id, record.member_id, actor, stop_personal=True)
                    if chat:
                        with chat.store.transaction() as db:
                            db.execute('DELETE FROM ticket_zalo_bindings WHERE bot=? AND user_id=? AND chat_id=?',
                                       (chat.bot_scope, record.user_id, record.chat_id))
                            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_registration_links'").fetchone():
                                db.execute('DELETE FROM ticket_registration_links WHERE bot=? AND member_id=? AND registration_id=?',
                                           (chat.bot_scope, record.member_id, record.id))
                    result = store.reset_registration(actor, record, snapshot, reason)
                except RegistrationError as error:
                    raise AdminMemberError(str(error)) from None
        return self._basic(result) | dict(ok=True, message='Đã xóa đăng ký. Thành viên nhắn riêng bot: '
            '/dangky SỐ_ĐIỆN_THOẠI, dùng số hiện tại trên Workload để đăng ký lại.')

    async def audit(self, member_id, page=1, limit=20):
        self._record(member_id)
        self._page(page, limit)
        with self.flow.store.lock:
            rows = self.flow.store.db.execute('SELECT id,actor,action,at,before_json,after_json FROM registration_admin_audit '
                                              'WHERE member_id=? ORDER BY id DESC', (member_id,)).fetchall()
            items = [dict(id=row['id'], actor=row['actor'], action=row['action'], at=row['at'],
                          before=json.loads(row['before_json']), after=json.loads(row['after_json'])) for row in rows]
        return dict(items=items[(page - 1) * limit:page * limit], total=len(items), page=page, limit=limit)
