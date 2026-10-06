"""Private, durable reminders with explicit commands and at-most-once delivery.

Only the adapter's trusted ingress may supply a destination. This module does
not interpret model output, contact Zalo, or authorize a different recipient.
The gateway calls ``recover_claimed`` once at startup before its scheduler.
After a crash, a claimed delivery is uncertain and is never silently retried.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
import uuid


VIETNAM_TZ = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")
MAX_TEXT_LENGTH = 1000
MAX_ACTIVE = 30
MAX_HORIZON = 90 * 86400
MAX_LATENESS = 86400
REMINDER_HELP = (
    "Chưa hiểu thời gian hoặc nội dung nhắc. Ví dụ:\n"
    "/nhac sau 10 phut | Gửi báo cáo\n/nhac mai 09:00 | Họp nhóm\n"
    "Nhớ dấu | giữa thời gian và nội dung. Xem chi tiết: /help nhac"
)


@dataclass(frozen=True)
class ReminderCommand:
    action: str
    due_at: float | None = None
    text: str = ""
    reminder_id: str | None = None


@dataclass(frozen=True)
class Reminder:
    id: str
    user_id: str
    chat_id: str
    chat_type: str
    text: str
    due_at: float
    status: str
    created_at: float
    request_key: str


def _fold(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
        if not unicodedata.combining(char)
    )


def _timestamp(value: float | None, default_now: bool = False) -> float:
    if value is None and default_now:
        return time.time()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Thời điểm nhắc việc không hợp lệ.")
    return float(value)


def _validate_content(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Bạn cần nhập nội dung nhắc việc.")
    text = text.strip()
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"Nội dung nhắc việc tối đa {MAX_TEXT_LENGTH} ký tự.")
    if "\0" in text:
        raise ValueError("Nội dung nhắc việc có ký tự không hợp lệ.")
    return text


def _validate_due(due_at: float, now: float) -> float:
    due_at = _timestamp(due_at)
    if due_at <= now:
        raise ValueError("Thời gian nhắc phải ở tương lai; hãy nhập cả ngày và giờ Việt Nam.")
    if due_at > now + MAX_HORIZON:
        raise ValueError("Chỉ đặt nhắc việc trong vòng 90 ngày tới.")
    return due_at


def parse_reminder_command(text: str, now: float | None = None) -> ReminderCommand | None:
    """Parse explicit commands only; ambiguous dates are never guessed.

    Natural-language support is deliberately limited to the exact prefix
    ``nhắc mình sau ... | ...`` (a colon separator also works). Unrelated
    messages return None so ordinary conversation is still handled by Hermes.
    """
    if not isinstance(text, str):
        return None
    text = unicodedata.normalize("NFC", text).strip()
    folded = _fold(text)
    prefix = re.match(r"^(?:/nhac|nhac minh)(?=\s|$)", folded)
    if not prefix:
        return None
    now = _timestamp(now, default_now=True)
    body = text[prefix.end():].strip()
    normalized_body = _fold(body)
    if normalized_body == "xem":
        return ReminderCommand("list")
    if normalized_body in ("lichsu", "lich su"):
        return ReminderCommand("history")
    match = re.fullmatch(r"huy\s+([a-z0-9]{1,64})", normalized_body)
    if match:
        return ReminderCommand("cancel", reminder_id=match.group(1).upper())
    if normalized_body == "huy" or normalized_body.startswith("huy "):
        raise ValueError("Cần mã nhắc để hủy. Gửi /nhac xem, rồi sao chép mã vào /nhac huy MÃ_NHẮC.")

    schedule, separator, content = body.partition("|")
    if not separator and not folded.startswith("/"):
        schedule, separator, content = body.partition(":")
    if not separator:
        raise ValueError(REMINDER_HELP)
    content = _validate_content(content)
    schedule = _fold(schedule.strip())
    relative = re.fullmatch(r"sau\s+([0-9]{1,6})\s+(phut|gio|ngay)", schedule)
    if relative:
        amount = int(relative.group(1))
        units = {"phut": 60, "gio": 3600, "ngay": 86400}
        due_at = now + amount * units[relative.group(2)]
    else:
        match = re.fullmatch(r"(.+?)\s+([0-9]{2}:[0-9]{2})", schedule)
        if not match:
            raise ValueError(REMINDER_HELP)
        try:
            day_part, clock = match.groups()
            local_now = datetime.fromtimestamp(now, VIETNAM_TZ)
            if day_part in ("mai", "ngay mai", "homnay", "hom nay"):
                day = local_now.date() + timedelta(days=1 if day_part in ("mai", "ngay mai") else 0)
            elif re.fullmatch(r"\d{2}/\d{2}/\d{4}", day_part):
                day = datetime.strptime(day_part, "%d/%m/%Y").date()
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_part):
                day = datetime.strptime(day_part, "%Y-%m-%d").date()
            else:
                raise ValueError()
            due_at = datetime.combine(day, datetime.strptime(clock, "%H:%M").time(), VIETNAM_TZ).timestamp()
        except (ValueError, OverflowError, OSError) as exc:
            raise ValueError("Ngày hoặc giờ không hợp lệ. " + REMINDER_HELP) from exc
    return ReminderCommand("create", _validate_due(due_at, now), content)


def _validate_scope(user_id: str, chat_id: str, chat_type: str) -> None:
    if chat_type not in ("PRIVATE", "GROUP"):
        raise ValueError("Loại cuộc trò chuyện không hợp lệ.")
    for value in (user_id, chat_id):
        if not isinstance(value, str) or not value.strip() or any(c in value for c in (":", "\0", "\r", "\n")):
            raise ValueError("Người dùng hoặc cuộc trò chuyện không hợp lệ.")


class ReminderStore:
    """A single scheduler per gateway; transactions support concurrent readers.

    Database rows contain private reminder text and must live under the ignored
    runtime directory. Active quota and request deduplication share the same
    write transaction, so separate connections cannot bypass either limit.
    """

    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                chat_type TEXT NOT NULL CHECK(chat_type IN ('PRIVATE', 'GROUP')),
                text TEXT NOT NULL,
                due_at REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'pending','claimed','sent','failed','uncertain','blocked','cancelled','expired'
                )),
                created_at REAL NOT NULL,
                request_key TEXT NOT NULL UNIQUE
            )
        """)
        self.connection.execute("CREATE INDEX IF NOT EXISTS reminders_due ON reminders(status,due_at)")
        self.connection.execute("CREATE INDEX IF NOT EXISTS reminders_owner ON reminders(user_id,chat_id,chat_type)")
        self.connection.commit()

    @staticmethod
    def _record(row: sqlite3.Row) -> Reminder:
        return Reminder(**dict(row))

    def create(
        self, user_id: str, chat_id: str, chat_type: str, text: str, due_at: float,
        *, now: float | None = None, request_key: str,
    ) -> Reminder:
        _validate_scope(user_id, chat_id, chat_type)
        text = _validate_content(text)
        now = _timestamp(now, default_now=True)
        if not isinstance(request_key, str) or not request_key or len(request_key) > 256:
            raise ValueError("Thiếu mã yêu cầu nhắc việc hợp lệ.")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                "SELECT * FROM reminders WHERE request_key=?", (request_key,),
            ).fetchone()
            if existing is not None:
                if (existing["user_id"], existing["chat_id"], existing["chat_type"]) != (user_id, chat_id, chat_type):
                    raise ValueError("Mã yêu cầu không thuộc cuộc trò chuyện này.")
                # A duplicate event must preserve its first content/time/status,
                # even if a relative timestamp was recalculated after restart.
                return self._record(existing)
            due_at = _validate_due(due_at, now)
            count = self.connection.execute(
                "SELECT COUNT(*) FROM reminders WHERE user_id=? AND chat_id=? AND chat_type=? "
                "AND status IN ('pending','claimed')", (user_id, chat_id, chat_type),
            ).fetchone()[0]
            if count >= MAX_ACTIVE:
                raise ValueError("Bạn đã có 30 nhắc việc đang chờ trong cuộc trò chuyện này; hãy hủy bớt.")
            reminder = Reminder(
                uuid.uuid4().hex[:12].upper(), user_id, chat_id, chat_type,
                text, due_at, "pending", now, request_key,
            )
            self.connection.execute(
                "INSERT INTO reminders (id,user_id,chat_id,chat_type,text,due_at,status,created_at,request_key) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (reminder.id, user_id, chat_id, chat_type, text, due_at, "pending", now, request_key),
            )
        return reminder

    def list_for(self, user_id: str, chat_id: str, chat_type: str) -> list[Reminder]:
        """Return pending first, then recent outcomes, capped at 50 private rows."""
        _validate_scope(user_id, chat_id, chat_type)
        rows = self.connection.execute(
            "SELECT * FROM reminders WHERE user_id=? AND chat_id=? AND chat_type=? "
            "ORDER BY CASE WHEN status IN ('pending','claimed') THEN 0 ELSE 1 END, "
            "CASE WHEN status IN ('pending','claimed') THEN due_at END ASC, created_at DESC LIMIT 50",
            (user_id, chat_id, chat_type),
        ).fetchall()
        return [self._record(row) for row in rows]

    def cancel(self, id: str, user_id: str, chat_id: str, chat_type: str) -> bool:
        """Only the creator in the original chat may cancel a still-pending row.

        False deliberately conflates missing IDs, another person's reminder,
        and a reminder already claimed: no ownership information is leaked.
        """
        _validate_scope(user_id, chat_id, chat_type)
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE reminders SET status='cancelled' WHERE id=? AND user_id=? AND chat_id=? "
                "AND chat_type=? AND status='pending'", (id.upper(), user_id, chat_id, chat_type),
            )
        return cursor.rowcount == 1

    def recover_claimed(self) -> int:
        """Run once on process startup, before any delivery task is started."""
        with self.connection:
            cursor = self.connection.execute("UPDATE reminders SET status='uncertain' WHERE status='claimed'")
        return cursor.rowcount

    def claim_due(
        self, now: float | None = None, *, limit: int = 20, max_lateness: float = MAX_LATENESS,
    ) -> list[Reminder]:
        now = _timestamp(now, default_now=True)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Số nhắc việc mỗi đợt phải từ 1 đến 100.")
        max_lateness = _timestamp(max_lateness)
        if max_lateness < 0 or max_lateness > MAX_LATENESS:
            raise ValueError("Chỉ gửi bù nhắc việc chậm tối đa 24 giờ.")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                "UPDATE reminders SET status='expired' WHERE status='pending' AND due_at<?",
                (now - max_lateness,),
            )
            rows = self.connection.execute(
                "SELECT * FROM reminders WHERE status='pending' AND due_at<=? ORDER BY due_at,id LIMIT ?",
                (now, limit),
            ).fetchall()
            for row in rows:
                self.connection.execute("UPDATE reminders SET status='claimed' WHERE id=?", (row["id"],))
            reminders = [self._record(dict(row) | {"status": "claimed"}) for row in rows]
        return reminders

    def finish(self, id: str, status: str) -> bool:
        if status not in ("sent", "failed", "uncertain", "blocked"):
            raise ValueError("Trạng thái gửi nhắc việc không hợp lệ.")
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE reminders SET status=? WHERE id=? AND status='claimed'", (status, id),
            )
        return cursor.rowcount == 1

    def close(self) -> None:
        self.connection.close()
