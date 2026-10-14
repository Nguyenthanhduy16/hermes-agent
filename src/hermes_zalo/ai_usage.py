"""Durable limits for attempted AI classification turns, using Vietnam dates.

Reserve before calling a provider. Failed calls still consume the reservation;
a turn can include only the provider's separately bounded retry policy. Store
only hashes of routing keys, never message text, phone numbers or credentials.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
from threading import RLock
import time


VIETNAM_TZ = timezone(timedelta(hours=7))


class AIUsageError(Exception):
    """Fixed error category; callers must fail closed without breaking commands."""

    def __init__(self, reason="unavailable"):
        self.reason = reason
        super().__init__(reason)


class AIUsageLimit(AIUsageError):
    """A daily allowance was exhausted or this message was already reserved."""


def _limit(value, maximum, name):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} cần là số nguyên từ 1 đến {maximum}.")
    return value


def options(values):
    result = {}
    for flag, name, default, maximum in (
        ("ZALO_AI_USER_DAILY_LIMIT", "user_daily_limit", 10, 1000),
        ("ZALO_AI_TOTAL_DAILY_LIMIT", "total_daily_limit", 300, 100000),
    ):
        raw = values.get(flag)
        if raw is None or raw == "":
            raw = str(default)
        try:
            if isinstance(raw, bool):
                raise ValueError()
            value = int(raw) if isinstance(raw, (str, int)) else None
            result[name] = _limit(value, maximum, flag)
        except (TypeError, ValueError):
            raise ValueError(f"{flag} cần là số nguyên từ 1 đến {maximum}.") from None
    return result


@dataclass(frozen=True)
class AIUsageSnapshot:
    day: str
    user_count: int
    total_count: int
    user_daily_limit: int
    total_daily_limit: int


class AIUsageStore:
    def __init__(self, path: Path, *, user_daily_limit=10, total_daily_limit=300,
                 clock=time.time):
        self.user_daily_limit = _limit(user_daily_limit, 1000, "ZALO_AI_USER_DAILY_LIMIT")
        self.total_daily_limit = _limit(total_daily_limit, 100000, "ZALO_AI_TOTAL_DAILY_LIMIT")
        self._clock = clock
        self._lock = RLock()
        self.connection = None
        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("""CREATE TABLE IF NOT EXISTS ai_usage (
                message_hash TEXT PRIMARY KEY,
                user_hash TEXT NOT NULL,
                usage_day TEXT NOT NULL
            )""")
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS ai_usage_day_user ON ai_usage(usage_day, user_hash)"
            )
            self.connection.commit()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            if self.connection is not None:
                self.connection.close()
            raise AIUsageError() from None

    @staticmethod
    def _hash(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise AIUsageError("invalid_key")
        try:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()
        except UnicodeError:
            raise AIUsageError("invalid_key") from None

    def _day(self):
        try:
            return datetime.fromtimestamp(self._clock(), VIETNAM_TZ).date().isoformat()
        except (OSError, OverflowError, TypeError, ValueError):
            raise AIUsageError() from None

    def _counts(self, day, user_hash):
        row = self.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(user_hash = ?), 0) FROM ai_usage WHERE usage_day = ?",
            (user_hash, day),
        ).fetchone()
        return row[1], row[0]

    def reserve(self, user_key: str, message_key: str) -> None:
        """Atomically charge a user across all chats; never retry a reserved message.

        user_key must identify the same Zalo user in DM and groups. message_key
        must be the stable incoming event key, including its conversation scope.
        """
        user_hash = self._hash(user_key)
        message_hash = self._hash(user_hash + ":" + self._hash(message_key))
        day = self._day()
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                if self.connection.execute(
                    "SELECT 1 FROM ai_usage WHERE message_hash = ?", (message_hash,)
                ).fetchone():
                    raise AIUsageLimit("duplicate")
                user_count, total_count = self._counts(day, user_hash)
                if user_count >= self.user_daily_limit:
                    raise AIUsageLimit("user_daily")
                if total_count >= self.total_daily_limit:
                    raise AIUsageLimit("total_daily")
                self.connection.execute(
                    "INSERT INTO ai_usage(message_hash, user_hash, usage_day) VALUES (?, ?, ?)",
                    (message_hash, user_hash, day),
                )
                self.connection.commit()
            except (sqlite3.Error, AIUsageError) as exc:
                try:
                    self.connection.rollback()
                except sqlite3.Error:
                    pass
                if isinstance(exc, AIUsageError):
                    raise
                raise AIUsageError() from None

    def snapshot(self, user_key=None) -> AIUsageSnapshot:
        """Read today's counters; without a user, user_count is zero."""
        user_hash = self._hash(user_key) if user_key is not None else None
        day = self._day()
        with self._lock:
            try:
                user_count, total_count = self._counts(day, user_hash)
            except sqlite3.Error:
                raise AIUsageError() from None
        return AIUsageSnapshot(day, user_count, total_count,
                               self.user_daily_limit, self.total_daily_limit)

    def close(self):
        with self._lock:
            self.connection.close()
