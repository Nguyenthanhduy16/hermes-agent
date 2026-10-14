"""Explicit app commands, separate from the general-purpose model's tools."""
import asyncio
from datetime import datetime
from hashlib import sha256
import logging
import os
from pathlib import Path
import time
from dataclasses import replace

from .issues import IssueStore, issue_target, preview
from .issue_management import ManagedIssueStore
from .knowledge import KnowledgeBase, NO_MATCH, NO_SOURCES, UNAVAILABLE
from .reminders import ReminderStore, VIETNAM_TZ, parse_reminder_command
from .sheets import SheetsClient, SheetsError, SheetsSettings
from .commands import normalized, help_text, help_topic, parse_calendar, route, TICKETS_COMMANDS, TICKETS_TOPICS, TICKETS_DM_GUIDE
from .events import is_allowed
from .natural import interpret, is_work_question
from .profiles import ProfileStore
from .registration_flow import RegistrationFlow, SUCCESS_PREFIX, UNAVAILABLE as REGISTRATION_UNAVAILABLE
from .registration import RegistrationError
from .workload import WorkloadError
from . import nlu


logger = logging.getLogger(__name__)
_LAZY_TICKETS = object()
_TICKETS_UNAVAILABLE = ("Tính năng đổi ca qua chat chưa sẵn sàng. Nhờ quản trị kiểm tra cấu hình và liên kết tài khoản. "
                        "Các lệnh tra lịch, tài liệu và nhắc việc vẫn dùng như trước.\n/tickets — hướng dẫn")
_TICKETS_FAILURE = ("Bot chưa xác định được kết quả xử lý yêu cầu đổi ca này. "
                    "Hãy kiểm tra /yeucau trước khi gửi lại thao tác; nếu vẫn lỗi, nhờ quản trị kiểm tra.")


def state_prefix(settings):
    return settings.state_dir / ("work-" + sha256(settings.token.encode()).hexdigest()[:16])


class WorkService:
    def __init__(self, settings, *, sheets_client=None, environ=None, tickets_chat=_LAZY_TICKETS,
                 registration_directory=None, intent_router=None):
        values = os.environ if environ is None else environ
        self._nlu_error = False
        try:
            nlu_enabled, nlu_timeout = nlu.options(values)
        except ValueError:
            nlu_enabled, nlu_timeout, self._nlu_error = True, 12, True
            logger.warning('Natural-language configuration invalid; explicit commands remain available')
        self.intent_router = intent_router or (nlu.IntentRouter(timeout=nlu_timeout) if nlu_enabled and not self._nlu_error else None)
        prefix = state_prefix(settings)
        self.settings = settings
        self._tickets_values = dict(values)
        self._tickets_chat = None if tickets_chat is _LAZY_TICKETS else tickets_chat
        self._tickets_initialized = tickets_chat is not _LAZY_TICKETS
        self._tickets_error = False
        self._tickets_notifications_error = False
        self._registration_notice_retry_at = 0
        self._issue_notice_retry_at = 0
        self._tickets_lock = asyncio.Lock()
        self._tickets_registration_lock = asyncio.Lock()
        self._tickets_bootstrapped = tickets_chat is not _LAZY_TICKETS
        self._tickets_bootstrap_retry_at = 0
        self.profiles = ProfileStore(prefix.with_suffix(".profiles.sqlite3"))
        self.knowledge = KnowledgeBase(settings.knowledge_dir)
        self.reminders = ReminderStore(prefix.with_suffix(".reminders.sqlite3"))
        self.issues = IssueStore(prefix.with_suffix(".issues.sqlite3"))
        self._managed_issue_store = ManagedIssueStore(prefix.with_suffix('.issues.sqlite3'))
        self.issue_mode = str(values.get('ZALO_ISSUES_MODE') or 'legacy').strip().lower()
        if self.issue_mode not in {'legacy', 'managed', 'off'}:
            self.issue_mode = 'off'
            logger.warning('Invalid Issue mode; only Issue is disabled. Use legacy, managed or off')
        self.managed_issues = self._managed_issue_store if self.issue_mode == 'managed' else None
        from .pilot import PilotStore
        self.pilot = PilotStore(prefix.with_suffix(".pilot.sqlite3"))
        self.sheets = sheets_client
        self.sheets_error = None
        if self.sheets is None:
            try:
                sheet_settings = SheetsSettings.from_mapping(values)
                self.sheets = SheetsClient(sheet_settings) if sheet_settings else None
            except SheetsError:
                self.sheets_error = "Cấu hình Google Sheets chưa hợp lệ; kiểm tra .env theo hướng dẫn."
        self.registration = RegistrationFlow(settings, values, self.sheets, prefix.with_suffix('.registration.sqlite3'),
                                             directory=registration_directory)
        if self.sheets is not None:
            self.sheets.calendar_authorizer = self._calendar_authorized
            self.sheets.calendar_access_guard = self._calendar_scope_allowed

    def registration_admission(self, message):
        return bool(self.registration.bootstrap(message) or self.registration.approved(message))

    def group_registration_response(self, message):
        """Enrollment gates group usage without expanding any existing ACL."""
        if message.chat_type != 'GROUP' or not self.registration.requested:
            return None
        if not is_allowed(message, self.settings.allowed_users, self.settings.allowed_groups,
                          group_members=self.settings.group_access == 'members'):
            return 'Nhóm hoặc tài khoản này chưa được cấp quyền dùng bot.'
        if not self.registration.enabled:
            return REGISTRATION_UNAVAILABLE
        if self.registration.admin_locked(message):
            return 'Quyền dùng bot đang bị khóa; nhờ quản trị kiểm tra.'
        if self.registration.identity(message.user_id) is None:
            from .registration_flow import GROUP_GUIDE
            return GROUP_GUIDE
        return None

    def member_admin_locked(self, user_id, chat_id=None):
        return bool(self.registration.store and self.registration.store.route_locked(user_id, chat_id))

    def registration_blocked(self, message):
        return self.registration.admin_locked(message) or (self.registration.blocked(message) and not self.registration.bootstrap(message)
                and not self.registration.admin_command(message))

    def background_registration_blocked(self, user_id, chat_id, chat_type='PRIVATE'):
        from .events import IncomingMessage
        if self.registration.requested and not self.registration.enabled:
            return True
        return self.registration.blocked(IncomingMessage(chat_id, user_id, 'background', chat_type, ''))

    def member_feature_allowed(self, user_id, chat_id, chat_type):
        """Ordinary member features, with explicit group and private-route scope."""
        from .events import IncomingMessage
        if self.background_registration_blocked(user_id, chat_id, chat_type):
            return False
        message = IncomingMessage(chat_id, user_id, 'permission-check', chat_type, '')
        return (is_allowed(message, self.settings.allowed_users, self.settings.allowed_groups) or
                self.registration.member_allows(user_id, chat_id, chat_type))

    def _issue_scope_allowed(self, user_id, chat_id, chat_type):
        if self.issue_mode != 'legacy':
            return bool(self.managed_issues_ready and chat_type == 'PRIVATE' and
                        self.registration.member_allows(user_id, chat_id, chat_type) and
                        self.member_feature_allowed(user_id, chat_id, chat_type))
        return bool(self.sheets and self.member_feature_allowed(user_id, chat_id, chat_type) and (
            self.sheets.settings.allows(user_id, chat_id, chat_type) or
            self.registration.member_allows(user_id, chat_id, chat_type)))

    @property
    def managed_issues_ready(self):
        return bool(self.managed_issues and self.managed_issues.migration_report().get('ready'))

    def issue_delivery_allowed(self, notice):
        record = self.registration.identity(notice['user_id'])
        return bool(self.managed_issues_ready and notice['chat_type'] == 'PRIVATE' and record and
                    record.member_id == notice['member_id'] and record.chat_id == notice['chat_id'] and
                    self.member_feature_allowed(notice['user_id'], notice['chat_id'], 'PRIVATE'))

    async def claim_issue_notification(self):
        if not self.managed_issues_ready or time.monotonic() < self._issue_notice_retry_at:
            return None
        # Inspect only the first eligible pending notice; transient roster errors
        # leave it pending. Never claim before the read-only roster check.
        rows = self.managed_issues.pending_notifications(limit=1)
        if not rows:
            return None
        notice = rows[0]
        if self.issue_delivery_allowed(notice):
            record = self.registration.identity(notice['user_id'])
            try:
                await self.registration.validate(record, require_unique_name=False)
            except RegistrationError:
                return self.managed_issues.claim_notification(lambda item: False, notification_id=notice['id'])
            except WorkloadError as error:
                from .workload import NOT_FOUND_ERROR, DUPLICATE_PHONE_ERROR
                if str(error) in (NOT_FOUND_ERROR, DUPLICATE_PHONE_ERROR):
                    return self.managed_issues.claim_notification(lambda item: False, notification_id=notice['id'])
                self._issue_notice_retry_at = time.monotonic() + 30
                return None
            except SheetsError:
                self._issue_notice_retry_at = time.monotonic() + 30
                return None
        return self.managed_issues.claim_notification(self.issue_delivery_allowed, notification_id=notice['id'])

    def ticket_delivery_allowed(self, user_id, chat_id):
        if self.background_registration_blocked(user_id, chat_id):
            return False
        return (user_id in self.settings.allowed_users or
                self.registration.member_allows(user_id, chat_id, 'PRIVATE'))

    def _calendar_scope_allowed(self, user_id, chat_id, chat_type):
        if self.background_registration_blocked(user_id, chat_id, chat_type):
            return False
        return bool(self.sheets and (
            self.sheets.settings.allows_calendar(user_id, chat_id, chat_type) or
            self.registration.member_allows(user_id, chat_id, chat_type)))

    async def _calendar_authorized(self, user_id, chat_id, chat_type):
        if not self._calendar_scope_allowed(user_id, chat_id, chat_type):
            return False
        if self.registration.member_allows(user_id, chat_id, chat_type):
            await self.registration.validate(self.registration.identity(user_id), require_unique_name=False)
            return self.registration.member_allows(user_id, chat_id, chat_type)
        return bool(self.sheets and self.sheets.settings.allows_calendar(user_id, chat_id, chat_type))

    async def _check_registered_ticket_peers(self, chat, account_ids):
        """Revalidate only participants selected by this input or notice."""
        allowed_users = set(self.settings.allowed_users)
        if not self.registration.enabled:
            return frozenset(allowed_users)
        for account_id in dict.fromkeys(account_ids):
            destination = await asyncio.to_thread(chat.identities.destination, chat.bot_scope, account_id)
            if destination is None:
                raise RegistrationError('Liên kết Tickets đã thay đổi; nhờ quản trị kiểm tra người liên quan.')
            record = self.registration.store.user_state(destination['user_id'])
            if record is None:
                if destination['member_id']:
                    await self.registration.directory.resolve_name(destination['name'])
                continue  # Preserve prior verified bindings only for unambiguous roster people.
            if (record.status != 'approved' or record.chat_id != destination['chat_id']):
                raise RegistrationError('Có liên kết đăng ký đã thay đổi hoặc thu hồi; nhờ quản trị kiểm tra Tickets.')
            if record.name != destination['name']:
                raise RegistrationError('Danh tính và liên kết Tickets chưa khớp; nhờ quản trị kiểm tra.')
            await self.registration.sync_tickets(record, chat)
            if self.registration.member_allows(record.user_id, record.chat_id, 'PRIVATE'):
                allowed_users.add(record.user_id)
        return frozenset(allowed_users)

    def recover(self):
        self.reminders.recover_claimed()
        self.issues.recover()
        self._managed_issue_store.recover()

    async def close(self):
        if self.intent_router is not None:
            await self.intent_router.close()
        if self._tickets_chat is not None:
            chat, self._tickets_chat = self._tickets_chat, None
            try:
                await asyncio.to_thread(chat.close)
            except Exception:
                logger.warning("Tickets chat cleanup failed; no request details logged")
        if self.sheets:
            await self.sheets.close()
        self.reminders.close()
        self.issues.close()
        self._managed_issue_store.close()
        self.pilot.close()
        self.profiles.close()
        self.registration.close()

    async def _open_tickets(self):
        chat = await self._get_tickets()
        if chat is None or not self.registration.enabled or self._tickets_bootstrapped:
            return chat
        async with self._tickets_registration_lock:
            if self._tickets_bootstrapped:
                return chat
            if time.monotonic() < self._tickets_bootstrap_retry_at:
                return None
            try:
                await self.registration.bootstrap_tickets(chat)
            except (RegistrationError, WorkloadError):
                self._tickets_error = True
                self._tickets_bootstrap_retry_at = time.monotonic() + 60
                logger.warning('Tickets member synchronization unavailable; retrying later, no personal details logged')
                return None
            self._tickets_bootstrapped = True
            self._tickets_error = False
            return chat

    async def _get_tickets(self):
        """A broken optional Tickets configuration must not disable work tools."""
        if self._tickets_initialized:
            return self._tickets_chat
        async with self._tickets_lock:
            if self._tickets_initialized:
                return self._tickets_chat
            if (self._tickets_values.get("TICKETS_CHAT_MODE") or "off").strip().lower() == "off":
                self._tickets_initialized = True
                return None
            try:
                from .tickets.runtime import open_chat
                opening = asyncio.create_task(asyncio.to_thread(open_chat, self.settings, self._tickets_values))
                try:
                    self._tickets_chat = await asyncio.shield(opening)
                except asyncio.CancelledError:
                    # Cancelling the await cannot stop its worker thread. Take
                    # ownership of the result before the gateway closes, so a
                    # just-opened database/process lock cannot be orphaned.
                    try:
                        chat = await opening
                        if chat is not None:
                            await asyncio.to_thread(chat.close)
                    except Exception:
                        logger.warning("Tickets chat interrupted during initialization; resources withheld")
                    raise
            except Exception:
                self._tickets_error = True
                logger.warning("Tickets chat unavailable; verify its configuration locally")
            finally:
                self._tickets_initialized = True
            return self._tickets_chat

    def _ticket_participant_snapshot(self, chat, account_ids):
        """Local grant and route snapshot, safe to recheck after source reads."""
        snapshot = []
        for account_id in dict.fromkeys(account_ids):
            destination = chat.identities.destination(chat.bot_scope, account_id)
            if destination is None or not self.ticket_delivery_allowed(destination['user_id'], destination['chat_id']):
                return None
            record = self.registration.store.user_state(destination['user_id']) if self.registration.enabled else None
            snapshot.append((account_id, *(destination[key] for key in
                            ('user_id', 'chat_id', '_binding', 'member_id', 'role', 'name')), record))
        return tuple(snapshot)

    async def claim_ticket_notification(self):
        """Only the verified Tickets queue can supply an outbound destination."""
        if self.registration.requested and not self.registration.enabled:
            return None
        if self._tickets_notifications_error:
            return None
        if time.monotonic() < self._registration_notice_retry_at:
            return None
        chat = await self._open_tickets()
        if chat is None:
            return None
        try:
            if self.registration.enabled:
                queued = await asyncio.to_thread(chat.registration_notifications)
                for notification_id, account_ids in queued:
                    try:
                        allowed_users = await self._check_registered_ticket_peers(chat, account_ids)
                    except RegistrationError:
                        await asyncio.to_thread(chat.block_registration_notification, notification_id)
                        continue
                    except WorkloadError:
                        continue  # A temporary source failure must not hold up another pair.
                    before = await asyncio.to_thread(self._ticket_participant_snapshot, chat, account_ids)
                    if before is None:
                        await asyncio.to_thread(chat.block_registration_notification, notification_id)
                        continue
                    notice = await asyncio.to_thread(chat.claim_notification, allowed_users,
                                                     notification_id=notification_id)
                    if notice is not None:
                        after = await asyncio.to_thread(self._ticket_participant_snapshot, chat, account_ids)
                        if after is None or after != before:
                            # Claiming performs slow source reads. A creator's
                            # revocation matters even if the recipient remains
                            # allowed; a later reapproval is a different grant.
                            await asyncio.to_thread(chat.finish_notification, notice['id'], 'blocked')
                            continue
                        return notice
                if queued:
                    self._registration_notice_retry_at = time.monotonic() + 60
                return None
            return await asyncio.to_thread(chat.claim_notification, self.settings.allowed_users)
        except (RegistrationError, WorkloadError):
            self._registration_notice_retry_at = time.monotonic() + 60
            return None
        except Exception:
            # Optional notification failures must not disable normal bot commands.
            # Stop this queue until restart instead of repeatedly claiming work.
            self._tickets_notifications_error = True
            logger.warning("Tickets notifications stopped; inspect local storage and restart. No request details logged")
            return None

    async def finish_ticket_notification(self, notification_id, status):
        chat = self._tickets_chat
        if chat is None:
            raise RuntimeError("Tickets notification runtime is closed")
        await asyncio.to_thread(chat.finish_notification, notification_id, status)

    async def _handle_tickets(self, message, command, argument):
        from .tickets.chat import UNVERIFIED as TICKETS_UNVERIFIED
        from .tickets.service import TicketError
        ticket_help = command == "/trogiup" and normalized(" ".join(argument.split())).lstrip("/") in TICKETS_TOPICS
        requested = command in TICKETS_COMMANDS or ticket_help
        allowed = is_allowed(message, self.settings.allowed_users, self.settings.allowed_groups,
                             group_members=self.settings.group_access == "members") or bool(self.registration.approved(message))
        if not allowed:
            return "Bạn hoặc cuộc trò chuyện này chưa được cấp quyền dùng bot. Nhờ quản trị cấp quyền trước." if requested else None
        if message.chat_type != "PRIVATE":
            return TICKETS_DM_GUIDE if requested else None
        natural_reminder = normalized(message.text).strip().split(maxsplit=2)[:2] == ["nhac", "minh"]
        # Fuzzy document keywords such as "xin nghỉ phép" can be a reason.
        # Explicit commands and calendar/reminder intents retain precedence.
        reason_candidate = command == "/hoi" and not route(message.text)[0]
        if not requested and (natural_reminder or command and not reason_candidate):
            return None
        chat = await self._open_tickets()
        if chat is not None:
            try:
                participant_snapshot = None
                account_ids = ()
                if self.registration.enabled and (requested or
                        getattr(chat, 'has_dialog', None) is not None and await asyncio.to_thread(chat.has_dialog, message)):
                    record = self.registration.identity(message.user_id)
                    if record:
                        await self.registration.sync_tickets(record, chat)
                    account_ids = await asyncio.to_thread(chat.registration_accounts, message)
                    await self._check_registered_ticket_peers(chat, account_ids)
                    participant_snapshot = await asyncio.to_thread(self._ticket_participant_snapshot, chat, account_ids)
                    if participant_snapshot is None:
                        raise RegistrationError('Liên kết người tham gia đã thay đổi; hãy mở lại yêu cầu đổi ca.')
                if reason_candidate:
                    has_reason_context = getattr(chat, "has_reason_context", None)
                    if has_reason_context is None or not await asyncio.to_thread(has_reason_context, message):
                        return None
                # Preserve raw text and key for wizard choices, confirmations,
                # idempotency and the member binding enforced by TicketsChat.
                def dispatch():
                    if participant_snapshot is None:
                        return chat.handle(message)
                    from .tickets.providers import ticket_access_scope
                    def guard():
                        return self._ticket_participant_snapshot(chat, account_ids) == participant_snapshot
                    with ticket_access_scope(guard):
                        return chat.handle(message)
                response = await asyncio.to_thread(dispatch)
                if self.registration.enabled and response == TICKETS_UNVERIFIED:
                    response = (self.registration.guide + '\nĐăng ký thành công bạn được tra lịch và đổi ca theo phạm vi đã bật, '
                                'không cần thêm ID riêng vào cấu hình.')
            except (RegistrationError, WorkloadError, TicketError) as error:
                return str(error)
            except Exception:
                logger.warning("Tickets chat request failed; response details withheld")
                # Plain text could be an active wizard step. Do not pass it to
                # the model after a potentially successful remote mutation.
                return _TICKETS_FAILURE
            if response is not None:
                return response
        if requested:
            if ticket_help or command == "/tickets" and help_topic(argument):
                return self.help("tickets", message)
            if self._tickets_error:
                return _TICKETS_UNAVAILABLE
            if chat is not None:
                return self.help("tickets", message)
            return (self.help("tickets", message) +
                    "\n\nĐổi ca qua chat chưa được bật cho bot này. Nhờ quản trị bật tính năng và xác minh tài khoản.")
        return None

    def help(self, topic, message):
        state = "invalid" if self.sheets_error else "not_configured"
        if self.sheets:
            state = "ready" if (self.sheets.settings.allows_calendar(message.user_id, message.chat_id, message.chat_type) or
                                self.registration.member_allows(message.user_id, message.chat_id, message.chat_type)) else "denied"
        can_write = self.member_feature_allowed(message.user_id, message.chat_id, message.chat_type)
        issue_access = self._issue_scope_allowed(message.user_id, message.chat_id, message.chat_type)
        answer = help_text(topic, sheets_state=state,
                         lab=bool(self.sheets and self.sheets.settings.calendar_format == "lab"),
                         issues=bool(issue_access and (self.managed_issues_ready or
                                     self.sheets and self.sheets.settings.issues_enabled)),
                         issue_access=issue_access, reminders=can_write,
                         tickets=bool(not self._tickets_error and (can_write or self.registration.approved(message)) and
                                      (self._tickets_chat is not None or
                                       str(self._tickets_values.get("TICKETS_CHAT_MODE") or "off").strip().lower()
                                       in ("sheets_test", "lab_live", "lab_live_test"))),
                         private_chat=message.chat_type == "PRIVATE")
        if topic.strip() and message.chat_type == "GROUP" and message.user_id not in self.settings.allowed_users:
            answer += "\nQuyền của bạn tại nhóm: hỏi lịch/tài liệu đã chia sẻ và đăng ký tên. Nhắc việc/tạo issue cần cấp quyền riêng."
        if self.registration.enabled:
            answer = answer.replace('Lịch của bạn: /dangky Họ tên đầy đủ, rồi /lichcuatoi homnay.',
                                    'Lịch của bạn: /lichcuatoi homnay sau khi đăng ký số điện thoại thành công.')
        if self.managed_issues_ready and normalized(topic.strip().lstrip('/')) == 'issue' and issue_access:
            from .issue_chat import GUIDE
            answer = GUIDE
        if self.registration.enabled and not topic.strip():
            answer = answer.replace('Chưa đăng ký tên? /dangky Họ tên đầy đủ', 'Đăng ký danh tính: nhắn riêng /dangky SỐ_ĐIỆN_THOẠI')
            if message.chat_type == 'PRIVATE':
                answer += '\n/dangky trangthai — trạng thái đăng ký'
                if message.user_id in self.registration.store.admins:
                    answer += '\n/dangkychoduyet — quản trị duyệt đăng ký\n/thanhvien — xem và thu hồi quyền thành viên'
        return answer

    def _natural_intents(self, message):
        allowed = {'menu', 'chat', 'documents', 'clarify'}
        scope = (message.user_id, message.chat_id, message.chat_type)
        if self._calendar_scope_allowed(*scope):
            allowed.add('calendar')
        if self.member_feature_allowed(*scope):
            allowed.update(('reminder_list', 'reminder_history', 'reminder_help'))
        if self._issue_scope_allowed(*scope) and (self.managed_issues_ready or self.sheets and self.sheets.settings.issues_enabled):
            allowed.update(('issue_help', 'issue_draft'))
        if self._tickets_chat is not None or str(self._tickets_values.get('TICKETS_CHAT_MODE') or 'off').lower() in ('sheets_test', 'lab_live', 'lab_live_test'):
            allowed.update(('tickets_start', 'tickets_pending', 'tickets_list', 'tickets_history'))
        return frozenset(allowed)

    async def _natural_response(self, message):
        # Adapter admission is repeated here so this service remains safe in isolation.
        if not (is_allowed(message, self.settings.allowed_users, self.settings.allowed_groups,
                           group_members=self.settings.group_access == 'members') or self.registration.approved(message)):
            return 'Bạn hoặc cuộc trò chuyện này chưa được cấp quyền dùng bot.'
        if self._nlu_error:
            return nlu.UNAVAILABLE
        started = time.monotonic()
        outcome = 'ok'
        grant = self.registration.identity(message.user_id)
        try:
            allowed = self._natural_intents(message)
            data = await self.intent_router.classify(message.text, allowed=allowed,
                                                     user_key=sha256(message.user_id.encode()).hexdigest())
            # Revalidate even injected classifiers; none may bypass the closed schema.
            data = nlu.validate(data, message.text, allowed)
            if self.registration.identity(message.user_id) != grant or self.registration.blocked(message):
                outcome = 'denied'
                return 'Quyền đăng ký đã thay đổi. Gửi /dangky trangthai trong chat riêng rồi thử lại.'
            action = nlu.to_action(data, message.text)
            if action.reply:
                return action.reply
            if not action.command:
                return None  # Hermes general chat, still no tools or internal context.
            argument = action.argument
            if action.person:
                if not self._calendar_scope_allowed(message.user_id, message.chat_id, message.chat_type):
                    return 'Bạn hoặc cuộc trò chuyện này chưa được cấp quyền dùng Google Sheets.'
                if not self.registration.enabled:
                    return nlu.CLARIFY['person']
                # Certify an exact, unique Workload identity before a name-only schedule read.
                person = await self.registration.directory.resolve_name(action.person)
                argument += ' | coach=' + person.name
            if self.registration.identity(message.user_id) != grant or self.registration.blocked(message):
                outcome = 'denied'
                return 'Quyền đăng ký đã thay đổi. Gửi /dangky trangthai trong chat riêng rồi thử lại.'
            response = await self.handle(replace(message, text=action.command + (' ' + argument if argument else '')),
                                         _exact_coach=bool(action.person))
            # Do not imply a wizard started when scope/config/identity checks rejected it.
            if action.prefix and response and 'Bước 1/4' in response:
                return action.prefix + response
            return response
        except nlu.IntentError as error:
            outcome = 'timeout' if str(error) == 'timeout' else 'rate_limited' if str(error) == 'rate_limited' else 'error'
            logger.warning('Natural-language routing unavailable (%s); no message or provider details logged', outcome)
            return nlu.UNAVAILABLE
        except (WorkloadError, RegistrationError):
            outcome = 'blocked'
            return ('Chưa xác định được duy nhất người này trên Workload. Hãy dùng họ tên chính xác; '
                    'nếu trùng tên, nhờ quản trị bổ sung ánh xạ mã thành viên. Bot chưa thực hiện thao tác.')
        except asyncio.CancelledError:
            raise
        except Exception:
            outcome = 'error'
            logger.warning('Natural-language routing unavailable; no private details logged')
            return nlu.UNAVAILABLE
        finally:
            self.pilot.record('nlu', outcome, (time.monotonic() - started) * 1000)

    async def handle(self, message, *, _exact_coach=False):
        command, argument = interpret(message.text)
        if (message.chat_type == "PRIVATE" and command == "/dangky" and
                not message.text.lstrip().startswith("/") and
                not normalized(message.text.lstrip()).startswith("dang ky")):
            command, argument = "", message.text  # preserve ordinary DM introductions
        text = command + " " + argument if command else message.text.strip()
        started = time.monotonic()
        kind = "chat"
        outcome = "ok"
        try:
            group_guide = self.group_registration_response(message)
            if group_guide is not None:
                kind, outcome = 'command', 'denied'
                return group_guide
            registration_response = await self.registration.handle(message)
            if registration_response is not None:
                kind = 'command'
                record = None
                auto_success = (self.registration.auto_approve and command == '/dangky'
                                and normalized(argument.strip()) not in ('trangthai', 'trang thai')
                                and registration_response.startswith(SUCCESS_PREFIX))
                if (command == '/duyetdangky' and self.registration.admin_command(message)
                        and registration_response.startswith('Đã duyệt ')):
                    record = self.registration.store.get_for_admin(message.user_id, argument.strip())
                elif auto_success:
                    record = self.registration.approved(message)
                if record is not None:
                    # Provision the private Tickets route immediately after
                    # approval, so a peer can select this member without CSV
                    # or requiring the member to open the wizard first.
                    chat = await self._open_tickets()
                    if chat is not None:
                        try:
                            await self.registration.sync_tickets(record, chat)
                            if not auto_success:
                                registration_response += '\nLiên kết đổi ca đã sẵn sàng.'
                        except (RegistrationError, WorkloadError) as error:
                            registration_response += '\nĐã lưu quyền thành viên; liên kết đổi ca chưa sẵn sàng: ' + str(error)
                return registration_response
            if self.registration.requested and not self.registration.enabled:
                return REGISTRATION_UNAVAILABLE
            if self.registration.enabled:
                if self.registration.blocked(message):
                    return 'Liên kết đăng ký chưa hợp lệ hoặc đã bị thu hồi. Gửi /dangky trangthai trong chat riêng.'
                if (message.chat_type == 'PRIVATE' and message.user_id not in self.settings.allowed_users
                        and not self.registration.approved(message)):
                    return 'Bạn chưa được duyệt đăng ký. Chỉ dùng /dangky SỐ_ĐIỆN_THOẠI hoặc /dangky trangthai.'
            if (self.intent_router is not None or self._nlu_error) and not message.text.lstrip().startswith('/'):
                # Existing Tickets dialog owns free-form reasons and numeric choices.
                # They must never enter an AI request, including expired dialogs.
                if (message.chat_type == 'PRIVATE' and (not command or command == '/hoi') and
                        not normalized(message.text).startswith('nhac minh')):
                    chat = await self._open_tickets()
                    if chat is not None and await asyncio.to_thread(chat.has_dialog, message):
                        response = await self._handle_tickets(message, command, argument)
                        if response is not None:
                            return response
                candidate = nlu.should_classify(message.text, command, argument)
                if candidate and nlu.private_input(message.text):
                    return self.registration.guide
                if (not normalized(message.text).startswith('nhac minh') and nlu.multiple_requests(message.text)
                        and not (command == '/hoi' and not candidate)):
                    return nlu.MULTIPLE
                if candidate:
                    return await self._natural_response(message)
            ticket_response = await self._handle_tickets(message, command, argument)
            if ticket_response is not None:
                kind = "command"
                return ticket_response
            # Approval grants ordinary actions in the verified private chat.
            # Group enrollment does not grant private actions or widen read scope.
            if ((command in ('/nhac', '/issue', '/phanhoi') or normalized(text).startswith('nhac minh'))
                    and not self.member_feature_allowed(message.user_id, message.chat_id, message.chat_type)):
                outcome = 'denied'
                if not self.registration.enabled:
                    return 'Nhắc việc/tạo issue cần quản trị cấp quyền riêng trong nhóm này.\n/menu — hướng dẫn'
                return ('Tính năng này cần quyền thành viên trong tin nhắn riêng. '
                        'Nhắn riêng /dangky SỐ_ĐIỆN_THOẠI để được duyệt, rồi dùng tại đó.\n/menu — hướng dẫn')
            if command == "/trogiup":
                return self.help(argument, message)
            if command == "/thongbao":
                return self.help("thongbao", message)
            if command in ("/dangky", "/hoso"):
                kind = "command"
                if self.registration.enabled:
                    record = self.registration.identity(message.user_id)
                    if record:
                        return (f'Danh tính đã duyệt: {record.name}\nMã thành viên: {record.member_id}\n'
                                'Không đổi danh tính bằng tên tự khai. Nhờ quản trị xử lý nếu cần sửa hoặc thu hồi.')
                    return self.registration.guide
                if command == "/hoso":
                    if normalized(argument.strip()) == "xoa":
                        self.profiles.remove(message)
                        return "Đã xóa tên tra lịch của bạn tại chat này. Đăng ký lại: /dangky Họ tên đầy đủ"
                    if argument.strip():
                        return "Xem tên: /hoso\nXóa tên của mình tại chat này: /hoso xoa"
                    name = self.profiles.get(message)
                    return (f"Tên tra lịch của bạn tại chat này: {name}\nLịch cá nhân: /lichcuatoi homnay\nĐổi tên: /dangky Họ tên đầy đủ\nXóa: /hoso xoa"
                            if name else "Bạn chưa đăng ký tên Coach tại chat này.\nGửi /dangky Họ tên đầy đủ đúng trong bảng lịch.")
                name = " ".join(argument.split())
                if not name or len(name) > 120 or "|" in name or any(c in name for c in "[]\0"):
                    return "Gửi /dangky Họ tên đầy đủ đúng trong bảng lịch. Ví dụ: /dangky Nguyễn Văn An"
                if not self.sheets or self.sheets_error:
                    return "Chưa kết nối lịch để kiểm tra tên Coach. Nhờ người quản lý bot kiểm tra kết nối."
                canonical = await self.sheets.coach_name(name, message.user_id, message.chat_id, message.chat_type)
                self.profiles.set(message, canonical)
                return (f"Đã lưu tên tra lịch của bạn tại chat này: {canonical}\n"
                        "Thử hỏi: Ngày mai mình có lịch gì?\nHoặc: /lichcuatoi homnay\n"
                        "Đây là tên bạn tự khai để lọc lịch, không phải xác minh danh tính hay cấp thêm quyền.")
            if command in ("/lich", "/lichcuatoi", "/nhac", "/hoi", "/tailieu", "/issue", "/phanhoi"):
                if help_topic(argument) or (not argument.strip() and command in ("/nhac", "/hoi", "/issue", "/phanhoi")):
                    return self.help(command, message)
            if command in ("/hoi", "/tailieu"):
                kind = "documents"
                scope = (message.user_id, message.chat_id, message.chat_type)
                if command == "/tailieu" and not argument.strip():
                    response = self.knowledge.list_sources(*scope, approved_member=bool(self.registration.approved(message)))
                else:
                    response = self.knowledge.answer(argument.strip(), *scope, approved_member=bool(self.registration.approved(message)))
                if response in (NO_MATCH, NO_SOURCES):
                    outcome = "not_found"
                elif response == UNAVAILABLE:
                    outcome = "error"
                return response
            reminder = parse_reminder_command(text)
            if reminder:
                kind = "reminders"
                scope = (message.user_id, message.chat_id, message.chat_type)
                if reminder.action == "create":
                    item = self.reminders.create(*scope, text=reminder.text, due_at=reminder.due_at, request_key=message.key)
                    due = datetime.fromtimestamp(item.due_at, VIETNAM_TZ)
                    destination = "nhóm này" if message.chat_type == "GROUP" else "cuộc trò chuyện riêng này"
                    outcome = "created"
                    return (f"Đã đặt nhắc {item.id}\nNội dung: {item.text}\n"
                            f"Thời gian: {due:%d/%m/%Y %H:%M:%S} (giờ Việt Nam)\nNơi nhận: {destination}\n"
                            f"Hủy nhắc này: /nhac huy {item.id}\nXem nhắc đang chờ: /nhac xem")
                if reminder.action == "cancel":
                    cancelled = self.reminders.cancel(reminder.reminder_id, *scope)
                    outcome = "cancelled" if cancelled else "not_found"
                    return "Đã hủy nhắc việc." if cancelled else "Không tìm thấy nhắc đang chờ của bạn tại đây, hoặc đã bắt đầu gửi.\nGửi /nhac xem để lấy đúng mã nhắc."
                rows = self.reminders.list_for(*scope)
                if reminder.action == "list":
                    rows = [r for r in rows if r.status in ("pending", "claimed")]
                if not rows:
                    return "Bạn chưa có nhắc việc phù hợp trong cuộc trò chuyện này.\nTạo thử: /nhac sau 10 phut | Gửi báo cáo\nXem lịch sử: /nhac lichsu"
                statuses = {"pending": "chờ", "claimed": "đang gửi", "sent": "đã gửi", "cancelled": "đã hủy", "uncertain": "chưa rõ kết quả gửi", "failed": "gửi lỗi", "blocked": "đã thu hồi quyền", "expired": "quá hạn"}
                lines = [f"{i}. {datetime.fromtimestamp(r.due_at, VIETNAM_TZ):%d/%m/%Y %H:%M} · {statuses.get(r.status, r.status)}\n"
                         f"{' '.join(r.text.split())[:120]}\nMã: {r.id}"
                         for i, r in enumerate(rows[:30], 1)]
                return ("Nhắc việc của bạn tại đây (giờ Việt Nam):\n\n" + "\n\n".join(lines) +
                        "\n\nHủy một nhắc đang chờ: /nhac huy MÃ_NHẮC\nXem lịch sử gần đây: /nhac lichsu")
            if command == '/issue' and self.issue_mode != 'legacy':
                from .issue_chat import handle as handle_issue
                kind = 'issues'
                return await handle_issue(self, message, argument)
            if command == '/issue' and self._managed_issue_store.migration_report().get('source_reconciled'):
                return 'Issue đã chuyển sang trang Admin. Nhờ người vận hành bật chế độ managed; không ghi vào Sheets cũ.'
            if command in ("/lich", "/lichcuatoi", "/issue"):
                kind = "issues" if command == "/issue" else "calendar"
                if self.sheets_error:
                    outcome = "error"
                    return self.sheets_error
                if self.sheets is None:
                    outcome = "unsupported"
                    return "Chưa kết nối Google Sheets. Cần cấu hình bảng thử nghiệm và quyền truy cập trên máy."
                scope = (message.user_id, message.chat_id, message.chat_type)
                allowed = self._issue_scope_allowed(*scope) if command == '/issue' else self._calendar_scope_allowed(*scope)
                if not allowed:
                    outcome = "denied"
                    return "Bạn hoặc cuộc trò chuyện này chưa được cấp quyền dùng Google Sheets."
                if command in ("/lich", "/lichcuatoi"):
                    query = parse_calendar(argument)
                    if command == "/lichcuatoi":
                        if self.registration.enabled:
                            record = self.registration.identity(message.user_id)
                            if not record:
                                return self.registration.guide
                            name = (await self.registration.validate(record)).name
                        else:
                            name = self.profiles.get(message)
                        if not name:
                            return "Mình chưa biết tên Coach của bạn tại chat này. Gửi /dangky Họ tên đầy đủ đúng trong bảng, rồi hỏi lại."
                        if any(key == "coach" for key, _ in query.filters):
                            return "Lịch của tôi dùng tên đã đăng ký. Muốn tìm tên khác, dùng /lich homnay | coach=Họ tên."
                        query = replace(query, filters=query.filters + (("coach", name),))
                    if self.sheets.settings.calendar_format == "lab":
                        options = {'exact_coach': True} if _exact_coach or command == '/lichcuatoi' and self.registration.enabled else {}
                        return await self.sheets.lab_calendar(query, message.user_id, message.chat_id, message.chat_type, **options)
                    if query.start != query.end or query.filters or query.page != 1:
                        outcome = "unsupported"
                        return "Bảng lịch mẫu hiện hỗ trợ tra từng ngày. Dùng /lich homnay hoặc /lich 06/10/2026. Lọc và xem tuần dành cho lịch Lab."
                    return await self.sheets.calendar(query.start, message.user_id, message.chat_id, message.chat_type)
                if not self.sheets.settings.issues_enabled:
                    outcome = "unsupported"
                    return "Tạo issue đang tắt; bảng này hiện chỉ dùng để đọc lịch."
                # Revalidate the approved identity before preparing or confirming
                # a write; capture this grant so revocation/reapproval during I/O
                # cannot authorize an old in-flight action.
                grant = self.registration.identity(message.user_id)
                if grant:
                    await self.registration.validate(grant, require_unique_name=False)
                def issue_guard():
                    return (self._issue_scope_allowed(*scope) and
                            self.registration.identity(message.user_id) == grant)
                if not issue_guard():
                    return 'Quyền tạo issue đã thay đổi; hãy gửi lại sau khi được duyệt.'
                action, _, body = argument.strip().partition(" ")
                action = normalized(action)
                if action == "tao":
                    title, separator, description = body.partition("|")
                    if not separator:
                        outcome = "error"
                        return "Dùng /issue tao Tiêu đề | Mô tả. Bot sẽ gửi bản nháp để xác nhận."
                    draft = self.issues.create(message, title, description, issue_target(self.sheets))
                    outcome = "pending"
                    return preview(draft, self.sheets.settings.issues_tab)
                if action == "xacnhan":
                    before = self.issues.scoped(body.strip(), message)
                    response = await self.issues.confirm(body.strip(), message, self.sheets, access_guard=issue_guard)
                    after = self.issues.scoped(body.strip(), message)
                    if before is None:
                        outcome = "not_found"
                    elif before["status"] != "draft":
                        outcome = "ignored"
                    else:
                        outcome = {"created": "created", "failed": "error", "uncertain": "uncertain", "expired": "expired"}.get(after["status"], "blocked")
                    return response
                if action == "huy":
                    cancelled = self.issues.cancel(body.strip(), message)
                    outcome = "cancelled" if cancelled else "not_found"
                    return "Đã hủy bản nháp." if cancelled else "Không tìm thấy bản nháp có thể hủy của bạn tại đây."
                outcome = "error"
                return "Dùng /issue tao Tiêu đề | Mô tả; /issue xacnhan MA; /issue huy MA."
            if command == "/phanhoi":
                kind = "feedback"
                identity = self.pilot.feedback(argument.strip())
                return f"Đã ghi nhận góp ý {identity} để tổng hợp đợt dùng thử."
            if command.startswith("/"):
                outcome = "error"
                kind = "command"
                return "Mình chưa nhận ra lệnh này.\nGửi /menu để xem các mục đang dùng."
            if message.chat_type == "GROUP":
                # General chat may use Hermes after normal group admission.
                # Unrecognized internal-work questions remain grounded; missing
                # documents never become permission to invent an internal answer.
                if not is_work_question(message.text):
                    return None
                kind = "documents"
                response = self.knowledge.answer(message.text, message.user_id, message.chat_id, message.chat_type)
                if response in (NO_MATCH, NO_SOURCES, UNAVAILABLE):
                    outcome = "not_found" if response != UNAVAILABLE else "error"
                    response += "\nBạn cũng có thể hỏi: Lịch hôm nay? hoặc Quy định xin nghỉ phép thế nào?\n/menu — các tính năng"
                return response
            return None
        except (ValueError, SheetsError, WorkloadError) as error:
            # Only our own bounded validation messages, never raw upstream bodies.
            kind = kind if kind != "chat" else "command"
            outcome = "error"
            return str(error)
        finally:
            if kind != "chat":
                self.pilot.record(kind, outcome, (time.monotonic() - started) * 1000)
