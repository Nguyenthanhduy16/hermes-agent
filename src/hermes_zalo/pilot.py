"""Local pilot counters and aggregate cost reporting; no conversation collection."""

from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
import time
import uuid


KINDS = frozenset(("chat", "documents", "reminders", "calendar", "issues", "feedback", "poll", "access", "command", "tickets_notification", "nlu"))
OUTCOMES = frozenset((
    "ok", "error", "denied", "blocked", "uncertain", "created", "cancelled", "sent",
    "not_found", "unsupported", "pending", "expired", "timeout", "rate_limited", "ignored", "duplicate",
))


def _usage(path: Path | None) -> dict:
    unavailable = {"available": False}
    if path is None:
        return unavailable
    try:
        # URI encoding also protects paths containing '?' or '#' from changing
        # SQLite flags. mode=ro cannot create or modify the source database.
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            valid = lambda col: f"typeof({col}) IN ('integer','real') AND {col}>=0 AND {col}<1e100"
            known = "cost_status IN ('actual','estimated','included')"
            actual = f"({known} AND {valid('actual_cost_usd')})"
            estimated = f"({known} AND NOT COALESCE({actual},0) AND {valid('estimated_cost_usd')})"
            row = db.execute(f"""
                SELECT COUNT(*) AS sessions, MIN(started_at) AS since,
                    SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens,
                    SUM(api_call_count) AS api_call_count,
                    SUM(CASE WHEN input_tokens IS NULL OR output_tokens IS NULL
                        OR api_call_count IS NULL THEN 1 ELSE 0 END) AS unknown_usage_sessions,
                    SUM(CASE WHEN {actual} THEN actual_cost_usd END) AS actual_cost_usd,
                    SUM(CASE WHEN {estimated} THEN estimated_cost_usd END) AS estimated_cost_usd,
                    COUNT(CASE WHEN {actual} THEN 1 END) AS actual_sessions,
                    COUNT(CASE WHEN {estimated} THEN 1 END) AS estimated_sessions,
                    COUNT(CASE WHEN NOT COALESCE({actual} OR {estimated},0) THEN 1 END) AS unknown_cost_sessions,
                    COUNT(CASE WHEN cost_status='included' THEN 1 END) AS included_sessions
                FROM sessions WHERE source='zalo'
            """).fetchone()
            return {"available": True, **dict(row)}
        finally:
            db.close()
    except (sqlite3.Error, ValueError, OSError):
        # Never echo database paths, SQL errors, or raw provider metadata.
        return unavailable


class PilotStore:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("CREATE TABLE IF NOT EXISTS pilot_meta (key TEXT PRIMARY KEY,value REAL NOT NULL)")
            self.connection.execute("INSERT OR IGNORE INTO pilot_meta VALUES ('created_at',?)", (time.time(),))
            self.connection.execute("""CREATE TABLE IF NOT EXISTS pilot_metrics (
                kind TEXT NOT NULL,outcome TEXT NOT NULL,count INTEGER NOT NULL,total_ms REAL NOT NULL,
                measured_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(kind,outcome))""")
            columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(pilot_metrics)")}
            if "measured_count" not in columns:
                self.connection.execute("ALTER TABLE pilot_metrics ADD COLUMN measured_count INTEGER NOT NULL DEFAULT 0")
                # Legacy totals mixed measured durations with implicit zeros.
                # Their denominator is unknowable; retain event counts while
                # starting a new, explicitly measured latency accumulator.
                self.connection.execute("UPDATE pilot_metrics SET total_ms=0")
            self.connection.execute("""CREATE TABLE IF NOT EXISTS pilot_feedback (
                id TEXT PRIMARY KEY,created_at REAL NOT NULL,text TEXT NOT NULL)""")

    def record(self, kind: str, outcome: str, elapsed_ms: float | None = None) -> None:
        if kind not in KINDS or outcome not in OUTCOMES:
            raise ValueError("Nhãn thống kê không hợp lệ; không dùng ID hoặc nội dung hội thoại.")
        if elapsed_ms is not None and (isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, (int, float)) or not math.isfinite(elapsed_ms) or not 0 <= elapsed_ms <= 86400000):
            raise ValueError("Thời lượng thống kê không hợp lệ.")
        with self.connection:
            self.connection.execute("""INSERT INTO pilot_metrics (kind,outcome,count,total_ms,measured_count) VALUES (?,?,1,?,?)
                ON CONFLICT(kind,outcome) DO UPDATE SET count=count+1,total_ms=total_ms+excluded.total_ms,
                    measured_count=measured_count+excluded.measured_count""",
                (kind, outcome, elapsed_ms if elapsed_ms is not None else 0, int(elapsed_ms is not None)))

    def feedback(self, text: str) -> str:
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 2000 or "\0" in text:
            raise ValueError("Góp ý cần có nội dung và tối đa 2000 ký tự.")
        identifier = uuid.uuid4().hex[:12].upper()
        with self.connection:
            self.connection.execute("INSERT INTO pilot_feedback VALUES (?,?,?)", (identifier, time.time(), text.strip()))
            # The explicit feedback channel is private; cap its disk growth.
            self.connection.execute("""DELETE FROM pilot_feedback WHERE id NOT IN (
                SELECT id FROM pilot_feedback ORDER BY created_at DESC,rowid DESC LIMIT 1000)""")
        return identifier

    def report(self, hermes_db: Path | None = None) -> dict:
        return {
            "since": self.connection.execute("SELECT value FROM pilot_meta WHERE key='created_at'").fetchone()[0],
            "events": [dict(row) for row in self.connection.execute(
                "SELECT kind,outcome,count,measured_count,total_ms/NULLIF(measured_count,0) AS average_ms "
                "FROM pilot_metrics ORDER BY kind,outcome")],
            "feedback_count": self.connection.execute("SELECT COUNT(*) FROM pilot_feedback").fetchone()[0],
            "usage": _usage(hermes_db),
        }

    def close(self) -> None:
        self.connection.close()


def _date(value) -> str:
    if value is None:
        return "chưa có dữ liệu"
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _money(value) -> str:
    if value is None:
        return "chưa xác định"
    if 0 < value < 0.000001:
        return "<0.000001 USD"
    return f"{value:.6f} USD"


def format_report(report: dict) -> str:
    kinds = {"chat": "hội thoại", "documents": "tài liệu", "reminders": "nhắc việc", "calendar": "lịch",
             "issues": "issue", "feedback": "góp ý", "poll": "nhận tin", "access": "phân quyền", "command": "lệnh",
             "tickets_notification": "thông báo đổi ca"}
    outcomes = {"ok": "thành công", "error": "lỗi", "denied": "từ chối", "blocked": "đã chặn",
                "uncertain": "chưa rõ kết quả", "created": "đã tạo", "cancelled": "đã hủy", "sent": "đã gửi",
                "not_found": "không tìm thấy", "unsupported": "chưa hỗ trợ", "pending": "đang chờ",
                "expired": "hết hạn", "timeout": "quá thời gian", "rate_limited": "chạm hạn mức",
                "ignored": "bỏ qua", "duplicate": "trùng"}
    lines = [f"Báo cáo dùng thử — số liệu tích lũy từ {_date(report['since'])} (không phải theo ngày)."]
    for row in report["events"]:
        timing = "thời gian: chưa đo" if row["average_ms"] is None else f"trung bình {row['average_ms']:.0f} ms ({row['measured_count']} lượt có đo)"
        lines.append(f"{kinds[row['kind']]} / {outcomes[row['outcome']]}: {row['count']}; {timing}.")
    lines.append(f"Góp ý đang lưu riêng: {report['feedback_count']} (tối đa 1000); báo cáo không chứa nội dung góp ý.")
    usage = report["usage"]
    if not usage["available"]:
        lines.append("Chưa đọc được thống kê Hermes; token và chi phí chưa xác định.")
        return "\n".join(lines)
    lines.append(f"Hermes: toàn bộ {usage['sessions']} phiên Zalo còn lưu, từ {_date(usage['since'])}.")
    for key, label in (("input_tokens", "Token vào"), ("output_tokens", "Token ra"), ("api_call_count", "Lượt gọi AI")):
        lines.append(f"{label}: {usage[key] if usage[key] is not None else 'chưa xác định'}.")
    lines.append(f"Phiên thiếu thống kê token/lượt gọi: {usage['unknown_usage_sessions'] or 0}.")
    lines.append(f"Chi phí thực tế đã ghi nhận ({usage['actual_sessions']} phiên): {_money(usage['actual_cost_usd'])}.")
    lines.append(f"Ước tính cho phiên chưa có chi phí thực tế ({usage['estimated_sessions']} phiên): {_money(usage['estimated_cost_usd'])}.")
    lines.append(f"Phiên chưa xác định chi phí: {usage['unknown_cost_sessions']}; không xem phần thiếu là miễn phí.")
    if usage["included_sessions"]:
        lines.append(f"{usage['included_sessions']} phiên được Hermes ghi là đã gồm trong thuê bao; chưa tính phí thuê bao.")
    return "\n".join(lines)
