"""Durable deduplication: reserve before dispatch, never replay an uncertain send."""

from pathlib import Path
import sqlite3
import time


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS messages (key TEXT PRIMARY KEY, status TEXT NOT NULL, created REAL NOT NULL)"
        )
        self.connection.commit()

    def reserve(self, key: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO messages VALUES (?, 'processing', ?)", (key, time.time())
            )
        return cursor.rowcount == 1

    def finish(self, key: str, status: str) -> None:
        with self.connection:
            self.connection.execute("UPDATE messages SET status=? WHERE key=?", (status, key))

    def prune(self, max_age_days: int = 30) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM messages WHERE created < ?", (time.time() - max_age_days * 86400,))

    def close(self) -> None:
        self.connection.close()
