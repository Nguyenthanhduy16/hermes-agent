"""Explicit app commands, separate from the general-purpose model's tools."""
from datetime import date, datetime
from hashlib import sha256
import os
from pathlib import Path
import re
import time
import unicodedata

from .issues import IssueStore, issue_target, preview
from .knowledge import KnowledgeBase, NO_MATCH, NO_SOURCES, UNAVAILABLE
from .reminders import ReminderStore, VIETNAM_TZ, parse_reminder_command
from .sheets import SheetsClient, SheetsError, SheetsSettings

HELP = """Các lệnh công việc:
/tailieu — tài liệu bạn được phép xem
/hoi Câu hỏi — tìm câu trả lời kèm trích dẫn nguồn
/nhac sau 1 phut | Kiểm tra bot
/nhac 2026-10-06 09:00 | Nội dung nhắc (giờ Việt Nam)
/nhac xem
/nhac huy MA
/lich homnay hoặc /lich YYYY-MM-DD
/issue tao Tiêu đề | Mô tả — xem bản nháp trước
/issue xacnhan MA hoặc /issue huy MA
/phanhoi Nội dung lỗi hoặc góp ý
Nhắc việc được gửi vào chính cuộc trò chuyện tạo nhắc. Máy chạy bot phải bật và có mạng.
Trong nhóm, trả lời một tin của bot rồi gõ lệnh để giữ nguyên cú pháp."""


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
                   if unicodedata.category(c) != "Mn")


def state_prefix(settings):
    return settings.state_dir / ("work-" + sha256(settings.token.encode()).hexdigest()[:16])


class WorkService:
    def __init__(self, settings, *, sheets_client=None, environ=None):
        values = os.environ if environ is None else environ
        prefix = state_prefix(settings)
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
        if self.sheets:
            await self.sheets.close()
        self.reminders.close()
        self.issues.close()
        self.pilot.close()

    async def handle(self, message):
        text = message.text.strip()
        head, _, argument = text.partition(" ")
        command = normalized(head)
        started = time.monotonic()
        kind = "chat"
        outcome = "ok"
        try:
            if command in ("/trogiup", "/help"):
                return HELP
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
                    return f"Đã đặt nhắc {item.id}: {due:%d/%m/%Y %H:%M:%S} (giờ Việt Nam), tại {destination}.\n{item.text}\nHủy: /nhac huy {item.id}"
                if reminder.action == "cancel":
                    cancelled = self.reminders.cancel(reminder.reminder_id, *scope)
                    outcome = "cancelled" if cancelled else "not_found"
                    return "Đã hủy nhắc việc." if cancelled else "Không tìm thấy nhắc đang chờ của bạn tại đây, hoặc đã bắt đầu gửi."
                rows = self.reminders.list_for(*scope)
                if not rows:
                    return "Bạn chưa có nhắc việc trong cuộc trò chuyện này."
                statuses = {"pending": "chờ", "claimed": "đang gửi", "sent": "đã gửi", "cancelled": "đã hủy", "uncertain": "chưa rõ kết quả gửi", "failed": "gửi lỗi", "blocked": "đã thu hồi quyền", "expired": "quá hạn"}
                lines = [f"{r.id} | {datetime.fromtimestamp(r.due_at, VIETNAM_TZ):%d/%m %H:%M} | {statuses.get(r.status, r.status)} | {r.text[:120]}" for r in rows[:30]]
                return "Nhắc việc của bạn tại đây (giờ Việt Nam):\n" + "\n".join(lines)
            if command in ("/lich", "/issue"):
                kind = "calendar" if command == "/lich" else "issues"
                if self.sheets_error:
                    outcome = "error"
                    return self.sheets_error
                if self.sheets is None:
                    outcome = "unsupported"
                    return "Chưa kết nối Google Sheets. Cần cấu hình bảng thử nghiệm và quyền truy cập trên máy."
                if not self.sheets.settings.allows(message.user_id, message.chat_id, message.chat_type):
                    outcome = "denied"
                    return "Bạn hoặc cuộc trò chuyện này chưa được cấp quyền dùng Google Sheets."
                if command == "/lich":
                    arg = normalized(argument.strip())
                    if arg in ("", "homnay", "hom nay"):
                        day = datetime.now(VIETNAM_TZ).date()
                    else:
                        try:
                            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", arg):
                                raise ValueError()
                            day = date.fromisoformat(arg)
                        except ValueError:
                            outcome = "error"
                            return "Dùng /lich homnay hoặc /lich YYYY-MM-DD."
                    return await self.sheets.calendar(day, message.user_id, message.chat_id, message.chat_type)
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
            return None
        except (ValueError, SheetsError) as error:
            # Only our own bounded validation messages, never raw upstream bodies.
            kind = kind if kind != "chat" else "command"
            outcome = "error"
            return str(error)
        finally:
            if kind != "chat":
                self.pilot.record(kind, outcome, (time.monotonic() - started) * 1000)
