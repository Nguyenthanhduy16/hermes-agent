"""Admin approval of exchanges through the running WorkService only.

Web identities are separate from Tickets accounts. Never manufacture a manager
account, match a display name, or reopen a service to perform an Admin action.
"""
import asyncio
from contextlib import nullcontext
import re

from .admin_auth import AdminError
from .commands import normalized
from .registration import RegistrationError
from .tickets.providers import ticket_access_scope
from .tickets.service import LABELS, TrustedAdminActor
from .workload import WorkloadError


class TicketAdmin:
    PAGE_SIZE = 20

    def __init__(self, work, auth):
        self.work, self.auth = work, auth

    def _actor(self, session, guard=None):
        user = self.auth.user(session, 'tickets')

        def authorize():
            current = self.auth.user(session, 'tickets')
            return current['username'] == user['username'] and (guard is None or guard())

        # AdminAuth currently has no verified member/account binding. Separate
        # usernames and display names are never evidence of a Tickets identity.
        return TrustedAdminActor(actor_id='web:' + user['username'],
                                 name=user['display_name'], authorize=authorize)

    async def _chat(self, session):
        self.auth.user(session, 'tickets')
        if self.work.registration.requested and not self.work.registration.enabled:
            return None
        chat = await self.work._open_tickets()
        self.auth.user(session, 'tickets')
        return chat

    @staticmethod
    def _disabled():
        return dict(enabled=False, message='Đổi ca chưa bật hoặc chưa sẵn sàng. Kiểm tra cấu hình Tickets trên bot.')

    @staticmethod
    def _identity(ticket_id):
        if not isinstance(ticket_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}', ticket_id):
            raise AdminError('Mã yêu cầu đổi ca chưa hợp lệ.')
        return ticket_id

    async def list_tickets(self, session, *, query='', status='', page=1):
        self.auth.user(session, 'tickets')
        if (not isinstance(query, str) or len(query) > 160 or not isinstance(status, str)
                or status and status not in LABELS or type(page) is not int or not 1 <= page <= 10000):
            raise AdminError('Bộ lọc yêu cầu đổi ca chưa hợp lệ.')
        chat = await self._chat(session)
        if chat is None:
            return self._disabled() | dict(items=[], page=page, pages=1, total=0,
                page_size=self.PAGE_SIZE, summary=dict(all=0, **{status: 0 for status in LABELS}))
        actor = self._actor(session)
        items = await asyncio.to_thread(chat.service.admin_list, actor)
        self.auth.user(session, 'tickets')
        summary = dict(all=len(items), **{key: sum(item['status'] == key for item in items) for key in LABELS})
        query = normalized(query.strip())
        if query:
            items = [item for item in items if query in normalized(' '.join(str(value) for value in (
                item['id'], item['reason'], *(item[side].get(key, '') for side in ('a', 'b')
                    for key in ('member_name', 'course', 'room', 'start', 'period')))))]
        if status:
            items = [item for item in items if item['status'] == status]
        total = len(items)
        return dict(enabled=True, items=items[(page - 1) * self.PAGE_SIZE:page * self.PAGE_SIZE],
                    page=page, pages=max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE),
                    total=total, page_size=self.PAGE_SIZE, summary=summary)

    async def detail(self, session, ticket_id):
        self.auth.user(session, 'tickets')
        ticket_id = self._identity(ticket_id)
        chat = await self._chat(session)
        if chat is None:
            return self._disabled() | dict(ticket=None)
        result = await asyncio.to_thread(chat.service.admin_detail, self._actor(session), ticket_id)
        self.auth.user(session, 'tickets')
        return dict(enabled=True, ticket=result)

    async def action(self, session, data):
        self.auth.user(session, 'tickets')
        if not isinstance(data, dict) or set(data) - {'id', 'action', 'expected_version', 'reason', 'confirm'}:
            raise AdminError('Tham số duyệt đổi ca chưa hợp lệ.')
        ticket_id = self._identity(data.get('id'))
        action, version, reason = data.get('action'), data.get('expected_version'), data.get('reason', '')
        if (data.get('confirm') is not True or action not in ('approve', 'reject', 'reconcile')
                or type(version) is not int or version < 1):
            raise AdminError('Cần xác nhận thao tác và phiên bản yêu cầu đang xem.')
        if not isinstance(reason, str) or len(reason) > 600 or action == 'reject' and not reason.strip():
            raise AdminError('Khi từ chối cần lý do, tối đa 600 ký tự.')
        chat = await self._chat(session)
        if chat is None:
            raise AdminError(self._disabled()['message'], 503)
        actor = self._actor(session)
        account_ids, baseline = (), None
        # Reconciliation only reads a possibly completed earlier write. It must
        # remain possible after participant access is revoked, without retrying
        # that write. Approval still validates every current participant grant.
        if action == 'approve':
            def participants():
                with chat.store.lock:
                    detail = chat.service.admin_detail(actor, ticket_id)
                    ids = [detail['requester_id']]
                    for side in ('a', 'b'):
                        rows = chat.store.db.execute('SELECT id FROM ticket_accounts WHERE member_id=? AND active=1',
                                                    (detail[side]['member_id'],)).fetchall()
                        if len(rows) != 1:
                            raise AdminError('Liên kết người tham gia đã thay đổi; cần kiểm tra trước khi duyệt.', 409)
                        ids.append(rows[0]['id'])
                    return tuple(dict.fromkeys(ids))
            account_ids = await asyncio.to_thread(participants)
            def snapshot():
                with chat.store.lock:
                    return self.work._ticket_participant_snapshot(chat, account_ids)
            baseline = await asyncio.to_thread(snapshot)
            if baseline is None:
                raise AdminError('Thành viên hoặc liên kết đổi ca đã bị khóa, thu hồi hoặc thay đổi.', 409)
            try:
                await self.work._check_registered_ticket_peers(chat, account_ids)
            except RegistrationError as error:
                raise AdminError(str(error), 409) from None
            except WorkloadError:
                raise AdminError('Chưa đối chiếu được người tham gia với Workload. Thử lại sau.', 503) from None
            if await asyncio.to_thread(snapshot) != baseline:
                raise AdminError('Danh tính hoặc quyền thành viên vừa thay đổi. Tải lại trước khi xác nhận.', 409)

        def participant_guard():
            return (baseline is None or
                    self.work._ticket_participant_snapshot(chat, account_ids) == baseline)

        actor = self._actor(session, participant_guard)
        def apply():
            # Use the same ordering as member-control mutations and Tickets.
            # Registration changes cannot race the final external write check.
            registration_store = self.work.registration.store
            with chat.store.lock, registration_store.lock if registration_store else nullcontext():
                self.auth.user(session, 'tickets')
                if not participant_guard():
                    raise AdminError('Quyền đổi ca vừa thay đổi. Tải lại trước khi xác nhận.', 409)
                with ticket_access_scope(actor.authorize):
                    return chat.service.admin_action(actor, ticket_id, action, version, reason.strip())
        result = await asyncio.to_thread(apply)
        self.auth.user(session, 'tickets')
        return dict(enabled=True, ticket=result)
