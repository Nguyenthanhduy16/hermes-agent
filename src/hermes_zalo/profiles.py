"""Self-declared roster filters, scoped to sender and chat; never an ACL."""
import sqlite3
import time


class ProfileStore:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS profiles (
            kind TEXT NOT NULL, chat TEXT NOT NULL, user TEXT NOT NULL,
            coach TEXT NOT NULL, updated REAL NOT NULL,
            PRIMARY KEY(kind, chat, user))""")
        self.db.commit()

    def get(self, message):
        row = self.db.execute("SELECT coach FROM profiles WHERE kind=? AND chat=? AND user=?",
                              (message.chat_type, message.chat_id, message.user_id)).fetchone()
        return row[0] if row else None

    def set(self, message, coach):
        with self.db:
            self.db.execute("INSERT INTO profiles VALUES (?,?,?,?,?) ON CONFLICT(kind,chat,user) "
                            "DO UPDATE SET coach=excluded.coach,updated=excluded.updated",
                            (message.chat_type, message.chat_id, message.user_id, coach, time.time()))

    def remove(self, message):
        with self.db:
            self.db.execute("DELETE FROM profiles WHERE kind=? AND chat=? AND user=?",
                            (message.chat_type, message.chat_id, message.user_id))

    def close(self):
        self.db.close()
