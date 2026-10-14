"""Fresh, private schedule notices and a durable at-most-once delivery ledger.

The caller supplies an approved member and a uniquely verified exact roster
name, and rechecks access and the private route immediately before sending.
This module never reads Sheets, calls Zalo, or turns a name into an identity.
There is no future payload queue: each poll plans from a fresh calendar read.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import unicodedata

from .lab_calendar import LabSchedule
from .reminders import VIETNAM_TZ


MAX_NOTICE_LENGTH = 1800
_CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_EXPLICIT_PERIOD = re.compile(
    r"(?:Sáng|Chiều|Tối) ((?:[01]\d|2[0-3]):[0-5]\d)\s*[–-]\s*"
    r"((?:[01]\d|2[0-3]):[0-5]\d)"
)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                                     sort_keys=True).encode("utf-8")).hexdigest()


def _id(value, limit=512):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise ValueError("Định danh nhắc lịch không hợp lệ.")
    return value


def _instant(value):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Thời điểm nhắc lịch phải có múi giờ.")
        value = value.timestamp()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Thời điểm nhắc lịch không hợp lệ.")
    try:
        datetime.fromtimestamp(value, VIETNAM_TZ)
    except (OverflowError, OSError, ValueError):
        raise ValueError("Thời điểm nhắc lịch không hợp lệ.") from None
    return float(value)


@dataclass(frozen=True)
class AutoReminderSettings:
    enabled: bool = False
    evening_time: str = "20:00"
    lead_minutes: int = 30
    poll_seconds: int = 60
    periods: dict = field(default_factory=dict)

    def __post_init__(self):
        try:
            if (type(self.enabled) is not bool or not isinstance(self.evening_time, str)
                    or not _CLOCK.fullmatch(self.evening_time) or self.evening_time >= "23:00"
                    or type(self.lead_minutes) is not int or not 1 <= self.lead_minutes <= 180
                    or type(self.poll_seconds) is not int or not 15 <= self.poll_seconds <= 3600
                    or not isinstance(self.periods, dict)
                    or not set(self.periods).issubset({"Sáng", "Chiều", "Tối"})):
                raise ValueError()
            for hours in self.periods.values():
                if (not isinstance(hours, dict) or set(hours) != {"start", "end"}
                        or any(not isinstance(hours[k], str) or not _CLOCK.fullmatch(hours[k])
                               for k in ("start", "end")) or hours["start"] >= hours["end"]):
                    raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("Cấu hình nhắc lịch tự động không hợp lệ: giờ HH:MM trước 23:00, "
                             "nhắc trước 1–180 phút, chu kỳ 15–3600 giây và giờ buổi start/end HH:MM.") from None

    @classmethod
    def from_mapping(cls, values):
        try:
            flag = str(values.get("ZALO_AUTO_REMINDERS_ENABLED", "false")).strip().lower()
            if flag not in ("true", "false"):
                raise ValueError()
            number = lambda key, default: int(str(values.get(key, default)).strip())
            return cls(
                enabled=flag == "true",
                evening_time=str(values.get("ZALO_AUTO_REMINDERS_EVENING_TIME", "20:00")).strip(),
                lead_minutes=number("ZALO_AUTO_REMINDERS_LEAD_MINUTES", 30),
                poll_seconds=number("ZALO_AUTO_REMINDERS_POLL_SECONDS", 60),
                periods=json.loads(values.get("TICKETS_LAB_PERIODS_JSON") or "{}"),
            )
        except (TypeError, ValueError):
            raise ValueError("Cấu hình nhắc lịch tự động không hợp lệ; kiểm tra biến ZALO_AUTO_REMINDERS_* "
                             "và TICKETS_LAB_PERIODS_JSON.") from None


@dataclass(frozen=True)
class ScheduleNotice:
    id: str
    kind: str
    member_id: str = field(repr=False)
    user_id: str = field(repr=False)
    chat_id: str = field(repr=False)
    text: str = field(repr=False)
    event_key: str
    due_at: float
    expires_at: float

    def __post_init__(self):
        if self.kind not in ("evening", "before_class"):
            raise ValueError("Loại nhắc lịch không hợp lệ.")
        for value in (self.id, self.member_id, self.user_id, self.chat_id, self.event_key):
            _id(value)
        if (not isinstance(self.text, str) or not self.text.strip() or "\0" in self.text
                or len(self.text) > MAX_NOTICE_LENGTH
                or _instant(self.due_at) >= _instant(self.expires_at)):
            raise ValueError("Nội dung hoặc thời gian nhắc lịch không hợp lệ.")


def _hours(event, settings):
    explicit = _EXPLICIT_PERIOD.fullmatch(event.period)
    if explicit:
        start, end = explicit.groups()
        if start >= end:
            return None
        return start, end
    hours = settings.periods.get(event.period)
    return (hours["start"], hours["end"]) if hours else None


def _short(value, maximum=95):
    text = " ".join(str(value).split())
    return text[:maximum] + ("…" if len(text) > maximum else "")


def _event_line(event, hours):
    timing = "–".join(hours) if hours else (_short(event.period) or "Chưa ghi buổi") + " (chưa có giờ)"
    course = _short(event.course) or "Chưa ghi khóa"
    session = " · " + _short(event.session) if event.session else ""
    return f"• {timing} · {course}{session}\n  Phòng: {_short(event.room) or 'Chưa ghi'}"


def plan_notices(settings: AutoReminderSettings, schedules: list[LabSchedule], *, source_key: str,
                 member_id: str, member_name: str, user_id: str, chat_id: str,
                 now: datetime | float) -> list[ScheduleNotice]:
    """Plan only currently due notices for one approved, unambiguous member.

    ``member_name`` must be the exact Lab Coach cell name certified upstream;
    key-coach names, partial names, combined cells and missing hours are never
    inferred. ``source_key`` identifies the spreadsheet, not its mutable rows.
    Names and routing IDs do not participate in persistent delivery identity.
    """
    if not settings.enabled:
        return []
    for value in (source_key, member_id, member_name, user_id, chat_id):
        _id(value)
    stamp = _instant(now)
    local = datetime.fromtimestamp(stamp, VIETNAM_TZ)
    tomorrow = local.date() + timedelta(days=1)
    exact_name = unicodedata.normalize("NFC", member_name)
    grouped = {}
    for schedule in schedules:
        for event in schedule.events:
            if event.day not in (local.date(), tomorrow):
                continue
            hours = _hours(event, settings)
            event_key = _hash([source_key, event.day.isoformat(), event.course,
                               event.session, event.room, event.period, hours])
            owners = frozenset(unicodedata.normalize("NFC", name) for name in event.coach_names)
            grouped.setdefault(event_key, []).append((schedule, event, hours, owners))
    entries = {}
    for event_key, copies in grouped.items():
        owner_sets = {copy[3] for copy in copies}
        # Copied monthly tabs may contain the same session. Identical owners
        # permit one notice; conflicting copies cannot establish who is on duty.
        if len(owner_sets) != 1 or exact_name not in next(iter(owner_sets)):
            continue
        entries[event_key] = copies[0][:3]
    ordered = sorted(entries.items(), key=lambda item: (
        item[1][1].day, item[1][2][0] if item[1][2] else "99:99",
        item[1][1].room, item[1][1].course, item[1][1].session))

    def make(kind, key, text, due, expires):
        notice_id = "SN-" + _hash([kind, member_id, key])
        return ScheduleNotice(notice_id, kind, member_id, user_id, chat_id, text, key, due, expires)

    notices = []
    evening_due = datetime.combine(local.date(), time.fromisoformat(settings.evening_time), VIETNAM_TZ).timestamp()
    evening_end = datetime.combine(local.date(), time(23), VIETNAM_TZ).timestamp()
    selected = [(event, hours) for _, (_, event, hours) in ordered if event.day == tomorrow]
    if evening_due <= stamp < evening_end and selected:
        lines = [f"📅 Lịch ngày mai · {tomorrow:%d/%m/%Y}"]
        shown = 0
        for event, hours in selected:
            line = _event_line(event, hours)
            if len("\n".join(lines)) + len(line) + 180 > MAX_NOTICE_LENGTH:
                break
            lines.append(line)
            shown += 1
        if shown < len(selected):
            lines.append(f"Còn {len(selected) - shown} buổi chưa hiển thị.")
        lines.append("Bạn chuẩn bị cho buổi học nhé.\n/lichcuatoi ngaymai — xem lịch đầy đủ")
        notices.append(make("evening", f"evening:{tomorrow.isoformat()}", "\n".join(lines), evening_due, evening_end))
    for event_key, (_, event, hours) in ordered:
        if not hours:
            continue
        starts = datetime.combine(event.day, time.fromisoformat(hours[0]), VIETNAM_TZ).timestamp()
        due = starts - settings.lead_minutes * 60
        if due <= stamp < starts:
            # A stable payload remains valid while a 429 retry or source read
            # crosses a minute boundary. The exact start time is shown below.
            message = (f"🔔 Sắp đến buổi học · {event.day:%d/%m}\n"
                       f"{_event_line(event, hours)}\nBạn chuẩn bị đến lớp và check in theo quy định nhé.")
            notices.append(make("before_class", event_key, message, due, starts))
    return notices


class ScheduleReminderStore:
    """Claim fresh notices before transport, never automatically replay them.

    ``recover_claimed`` is called once at process startup, before scheduling.
    A crash or ambiguous network result cannot safely be retried because Zalo
    has no delivery-idempotency guarantee. Failed/blocked/uncertain records are
    terminal, so operators can inspect them without accidental resends.
    """

    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS schedule_notices (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('evening','before_class')),
            member_id TEXT NOT NULL, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
            text TEXT NOT NULL, event_key TEXT NOT NULL, due_at REAL NOT NULL,
            expires_at REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('claimed','sent','failed','uncertain','blocked','expired')),
            UNIQUE(kind,member_id,event_key)
        )""")
        self.connection.execute("CREATE INDEX IF NOT EXISTS schedule_notices_status ON schedule_notices(status,created_at)")
        self.connection.commit()

    def recover_claimed(self) -> int:
        with self.lock, self.connection:
            result = self.connection.execute("UPDATE schedule_notices SET status='uncertain' WHERE status='claimed'")
        return result.rowcount

    def claim(self, notice: ScheduleNotice, now: datetime | float) -> bool:
        if not isinstance(notice, ScheduleNotice):
            raise ValueError("Yêu cầu nhắc lịch không hợp lệ.")
        stamp = _instant(now)
        if not notice.due_at <= stamp < notice.expires_at:
            return False
        with self.lock, self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO schedule_notices "
                "(id,kind,member_id,user_id,chat_id,text,event_key,due_at,expires_at,created_at,updated_at,status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'claimed')",
                (notice.id, notice.kind, notice.member_id, notice.user_id, notice.chat_id, notice.text,
                 notice.event_key, notice.due_at, notice.expires_at, stamp, stamp),
            )
        return cursor.rowcount == 1

    def finish(self, notice_id: str, status: str, *, now: datetime | float | None = None) -> bool:
        if status not in ("sent", "failed", "uncertain", "blocked", "expired"):
            raise ValueError("Trạng thái nhắc lịch không hợp lệ.")
        _id(notice_id)
        stamp = _instant(now if now is not None else datetime.now(VIETNAM_TZ))
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "UPDATE schedule_notices SET status=?,updated_at=? WHERE id=? AND status='claimed'",
                (status, stamp, notice_id),
            )
        return cursor.rowcount == 1

    def get(self, notice_id: str):
        _id(notice_id)
        with self.lock:
            row = self.connection.execute("SELECT * FROM schedule_notices WHERE id=?", (notice_id,)).fetchone()
        return dict(row) if row is not None else None

    def close(self):
        with self.lock:
            self.connection.close()
