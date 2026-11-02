"""Durable daily turn quota per person and for the whole bot (Vietnam dates).

A turn is reserved before the agent runs; a failed turn still consumes it.
Only hashes of person and message keys are stored, never message text.
"""

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
from threading import RLock


VIETNAM_TZ = timezone(timedelta(hours=7))


class QuotaExceeded(Exception):
    def __init__(self, scope: str):
        self.scope = scope  # "user" or "total"
        super().__init__(scope)


def person_key(platform: str, user_id: str) -> str:
    """Channel-local identity until accounts are linked across channels."""
    return f"{platform}:{user_id}"


class QuotaStore:
    def __init__(self, path: Path, *, user_daily_limit: int, total_daily_limit: int, clock=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.user_daily_limit = user_daily_limit
        self.total_daily_limit = total_daily_limit
        self._clock = clock or (lambda: datetime.now(VIETNAM_TZ))
        self._lock = RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS turns (day TEXT NOT NULL, person TEXT NOT NULL, "
                        "message TEXT NOT NULL, PRIMARY KEY (day, message))")

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def _day(self) -> str:
        return self._clock().astimezone(VIETNAM_TZ).strftime("%Y-%m-%d")

    def usage(self, person: str) -> tuple[int, int]:
        day, who = self._day(), self._hash(person)
        with self._lock:
            used = self.db.execute("SELECT COUNT(*) FROM turns WHERE day=? AND person=?", (day, who)).fetchone()[0]
            total = self.db.execute("SELECT COUNT(*) FROM turns WHERE day=?", (day,)).fetchone()[0]
        return used, total

    def reserve(self, person: str, message: str) -> bool:
        """Reserve one turn; False if this message was already counted."""
        day, who, key = self._day(), self._hash(person), self._hash(message)
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if self.db.execute("SELECT 1 FROM turns WHERE day=? AND message=?", (day, key)).fetchone():
                    self.db.execute("COMMIT")
                    return False
                used = self.db.execute("SELECT COUNT(*) FROM turns WHERE day=? AND person=?", (day, who)).fetchone()[0]
                total = self.db.execute("SELECT COUNT(*) FROM turns WHERE day=?", (day,)).fetchone()[0]
                if used >= self.user_daily_limit:
                    raise QuotaExceeded("user")
                if total >= self.total_daily_limit:
                    raise QuotaExceeded("total")
                self.db.execute("INSERT INTO turns (day, person, message) VALUES (?, ?, ?)", (day, who, key))
                self.db.execute("COMMIT")
                return True
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self):
        with self._lock:
            self.db.close()
