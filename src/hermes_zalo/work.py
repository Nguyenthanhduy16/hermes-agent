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
from .knowledge import KnowledgeBase, NO_MATCH, NO_SOURCES, UNAVAILABLE
from .reminders import ReminderStore, VIETNAM_TZ, parse_reminder_command
from .sheets import SheetsClient, SheetsError, SheetsSettings
from .commands import normalized, help_text, help_topic, parse_calendar, route, TICKETS_COMMANDS, TICKETS_TOPICS, TICKETS_DM_GUIDE
from .events import is_allowed
from .natural import interpret, is_work_question
from .profiles import ProfileStore


logger = logging.getLogger(__name__)
_LAZY_TICKETS = object()
_TICKETS_UNAVAILABLE = ("Tính năng đổi ca qua chat chưa sẵn sàng. Nhờ quản trị kiểm tra cấu hình và liên kết tài khoản. "
                        "Các lệnh tra lịch, tài liệu và nhắc việc vẫn dùng như trước.\n/tickets — hướng dẫn")
_TICKETS_FAILURE = ("Bot chưa xác định được kết quả xử lý yêu cầu đổi ca này. "
                    "Hãy kiểm tra /yeucau trước khi gửi lại thao tác; nếu vẫn lỗi, nhờ quản trị kiểm tra.")


def state_prefix(settings):
    return settings.state_dir / ("work-" + sha256(settings.token.encode()).hexdigest()[:16])


class WorkService:
    def __init__(self, settings, *, sheets_client=None, environ=None, tickets_chat=_LAZY_TICKETS):
        values = os.environ if environ is None else environ
        prefix = state_prefix(settings)
        self.settings = settings
        self._tickets_values = dict(values)
        self._tickets_chat = None if tickets_chat is _LAZY_TICKETS else tickets_chat
        self._tickets_initialized = tickets_chat is not _LAZY_TICKETS
        self._tickets_error = False
        self._tickets_notifications_error = False
        self._tickets_lock = asyncio.Lock()
        self.profiles = ProfileStore(prefix.with_suffix(".profiles.sqlite3"))
        self.knowledge = KnowledgeBase(settings.knowledge_dir)
        self.reminders = ReminderStore(prefix.with_suffix(".reminders.sqlite3"))
        self.issues = IssueStore(prefix.with_suffix(".issues.sqlite3"))
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

    def recover(self):
        self.reminders.recover_claimed()
        self.issues.recover()

    async def close(self):
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
        self.pilot.close()
        self.profiles.close()

    async def _open_tickets(self):
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

    async def claim_ticket_notification(self):
        """Only the verified Tickets queue can supply an outbound destination."""
        if self._tickets_notifications_error:
            return None
        chat = await self._open_tickets()
        if chat is None:
            return None
        try:
            return await asyncio.to_thread(chat.claim_notification, self.settings.allowed_users)
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
        ticket_help = command == "/trogiup" and normalized(" ".join(argument.split())).lstrip("/") in TICKETS_TOPICS
        requested = command in TICKETS_COMMANDS or ticket_help
        allowed = is_allowed(message, self.settings.allowed_users, self.settings.allowed_groups,
                             group_members=self.settings.group_access == "members")
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
                if reason_candidate:
                    has_reason_context = getattr(chat, "has_reason_context", None)
                    if has_reason_context is None or not await asyncio.to_thread(has_reason_context, message):
                        return None
                # Preserve raw text and key for wizard choices, confirmations,
                # idempotency and the member binding enforced by TicketsChat.
                response = await asyncio.to_thread(chat.handle, message)
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
            state = "ready" if self.sheets.settings.allows_calendar(message.user_id, message.chat_id, message.chat_type) else "denied"
        can_write = is_allowed(message, self.settings.allowed_users, self.settings.allowed_groups)
        issue_access = bool(can_write and self.sheets and
                            self.sheets.settings.allows(message.user_id, message.chat_id, message.chat_type))
        answer = help_text(topic, sheets_state=state,
                         lab=bool(self.sheets and self.sheets.settings.calendar_format == "lab"),
                         issues=bool(issue_access and self.sheets.settings.issues_enabled),
                         issue_access=issue_access, reminders=can_write,
                         tickets=bool(not self._tickets_error and can_write and
                                      (self._tickets_chat is not None or
                                       str(self._tickets_values.get("TICKETS_CHAT_MODE") or "off").strip().lower()
                                       in ("sheets_test", "lab_live", "lab_live_test"))),
                         private_chat=message.chat_type == "PRIVATE")
        if topic.strip() and message.chat_type == "GROUP" and message.user_id not in self.settings.allowed_users:
            answer += "\nQuyền của bạn tại nhóm: hỏi lịch/tài liệu đã chia sẻ và đăng ký tên. Nhắc việc/tạo issue cần cấp quyền riêng."
        return answer

    async def handle(self, message):
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
            ticket_response = await self._handle_tickets(message, command, argument)
            if ticket_response is not None:
                kind = "command"
                return ticket_response
            # New group members can read only within explicitly shared scopes.
            # Self-registration never adds IDs to any global or resource ACL.
            member_only = (message.chat_type == "GROUP" and self.settings.group_access == "members"
                           and message.user_id not in self.settings.allowed_users)
            if member_only and (command in ("/nhac", "/issue") or normalized(text).startswith("nhac minh")):
                outcome = "denied"
                return "Bạn có thể hỏi lịch và tài liệu đã chia sẻ với nhóm. Nhắc việc/tạo issue cần quản trị cấp quyền riêng.\n/menu — hướng dẫn"
            if command == "/trogiup":
                return self.help(argument, message)
            if command == "/thongbao":
                return self.help("thongbao", message)
            if command in ("/dangky", "/hoso"):
                kind = "command"
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
                    response = self.knowledge.list_sources(*scope)
                else:
                    response = self.knowledge.answer(argument.strip(), *scope)
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
            if command in ("/lich", "/lichcuatoi", "/issue"):
                kind = "issues" if command == "/issue" else "calendar"
                if self.sheets_error:
                    outcome = "error"
                    return self.sheets_error
                if self.sheets is None:
                    outcome = "unsupported"
                    return "Chưa kết nối Google Sheets. Cần cấu hình bảng thử nghiệm và quyền truy cập trên máy."
                allows = self.sheets.settings.allows if command == "/issue" else self.sheets.settings.allows_calendar
                if not allows(message.user_id, message.chat_id, message.chat_type):
                    outcome = "denied"
                    return "Bạn hoặc cuộc trò chuyện này chưa được cấp quyền dùng Google Sheets."
                if command in ("/lich", "/lichcuatoi"):
                    query = parse_calendar(argument)
                    if command == "/lichcuatoi":
                        name = self.profiles.get(message)
                        if not name:
                            return "Mình chưa biết tên Coach của bạn tại chat này. Gửi /dangky Họ tên đầy đủ đúng trong bảng, rồi hỏi lại."
                        if any(key == "coach" for key, _ in query.filters):
                            return "Lịch của tôi dùng tên đã đăng ký. Muốn tìm tên khác, dùng /lich homnay | coach=Họ tên."
                        query = replace(query, filters=query.filters + (("coach", name),))
                    if self.sheets.settings.calendar_format == "lab":
                        return await self.sheets.lab_calendar(query, message.user_id, message.chat_id, message.chat_type)
                    if query.start != query.end or query.filters or query.page != 1:
                        outcome = "unsupported"
                        return "Bảng lịch mẫu hiện hỗ trợ tra từng ngày. Dùng /lich homnay hoặc /lich 06/10/2026. Lọc và xem tuần dành cho lịch Lab."
                    return await self.sheets.calendar(query.start, message.user_id, message.chat_id, message.chat_type)
                if not self.sheets.settings.issues_enabled:
                    outcome = "unsupported"
                    return "Tạo issue đang tắt; bảng này hiện chỉ dùng để đọc lịch."
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
                    response = await self.issues.confirm(body.strip(), message, self.sheets)
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
        except (ValueError, SheetsError) as error:
            # Only our own bounded validation messages, never raw upstream bodies.
            kind = kind if kind != "chat" else "command"
            outcome = "error"
            return str(error)
        finally:
            if kind != "chat":
                self.pilot.record(kind, outcome, (time.monotonic() - started) * 1000)
