"""Issue drafts: human confirmation bound to author, chat, content and target."""
from datetime import datetime, timezone
from pathlib import Path
import secrets
import sqlite3
import time

from .sheets import SheetsError, SheetsUncertain


class IssueStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
            user_id TEXT NOT NULL, chat_id TEXT NOT NULL, chat_type TEXT NOT NULL,
            title TEXT NOT NULL, body TEXT NOT NULL, target TEXT NOT NULL,
            created REAL NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL)""")
        self.db.commit()
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS issue_detached_registrations (id TEXT PRIMARY KEY)')

    def recover(self):
        with self.db:
            self.db.execute("UPDATE drafts SET status='uncertain' WHERE status='sending'")

    def close(self):
        self.db.close()

    def create(self, message, title, body, target, *, now=None):
        now = time.time() if now is None else now
        title, body = title.strip(), body.strip()
        if not title or not body or len(title) > 160 or len(body) > 1200:
            raise ValueError("Dùng /issue tao Tiêu đề | Mô tả. Giới hạn tiêu đề 160, mô tả 1.200 ký tự.")
        with self.db:
            previous = self.db.execute("SELECT * FROM drafts WHERE request_key=?", (message.key,)).fetchone()
            if previous:
                if self.db.execute('SELECT 1 FROM issue_detached_registrations WHERE id=?', (previous['id'],)).fetchone():
                    raise ValueError('Bản nháp thuộc đăng ký cũ; hãy tạo yêu cầu mới.')
                return dict(previous)
            count = self.db.execute("SELECT COUNT(*) FROM drafts WHERE user_id=? AND status='draft' AND expires>?",
                                    (message.user_id, now)).fetchone()[0]
            if count >= 20:
                raise ValueError("Bạn đang có 20 bản nháp; hãy hủy hoặc chờ bản nháp cũ hết hạn.")
            identity = secrets.token_hex(6)
            self.db.execute("INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (identity, message.key, message.user_id, message.chat_id, message.chat_type,
                             title, body, target, now, now + 600, "draft"))
        return dict(self.db.execute("SELECT * FROM drafts WHERE id=?", (identity,)).fetchone())

    def scoped(self, identity, message):
        row = self.db.execute("SELECT * FROM drafts WHERE id=? AND user_id=? AND chat_id=? AND chat_type=? "
                              'AND id NOT IN (SELECT id FROM issue_detached_registrations)',
                              (identity, message.user_id, message.chat_id, message.chat_type)).fetchone()
        return dict(row) if row else None

    def detach_registration(self, user_id):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO issue_detached_registrations SELECT id FROM drafts WHERE user_id=?', (user_id,))
            self.db.execute("UPDATE drafts SET status='cancelled' WHERE user_id=? AND status='draft'", (user_id,))

    def cancel(self, identity, message):
        with self.db:
            cursor = self.db.execute("UPDATE drafts SET status='cancelled' WHERE id=? AND user_id=? AND chat_id=? AND chat_type=? AND status='draft'",
                                     (identity, message.user_id, message.chat_id, message.chat_type))
        return cursor.rowcount == 1

    async def confirm(self, identity, message, client, *, now=None, access_guard=None):
        now = time.time() if now is None else now
        if client is None or not (access_guard() if access_guard is not None else
                                 client.settings.allows(message.user_id, message.chat_id, message.chat_type)):
            return "Chưa cấu hình Sheets hoặc bạn/cuộc trò chuyện này chưa được cấp quyền tạo issue."
        draft = self.scoped(identity, message)
        if draft is None:
            return "Không tìm thấy bản nháp của bạn trong cuộc trò chuyện này."
        if draft["status"] != "draft":
            return f"Bản nháp đã xử lý ({draft['status']}); không tự tạo lại."
        if draft["expires"] <= now:
            with self.db:
                self.db.execute("UPDATE drafts SET status='expired' WHERE id=? AND status='draft'", (identity,))
            return "Bản nháp hết hạn sau 10 phút. Hãy tạo bản nháp mới."
        if draft["target"] != issue_target(client):
            return "Nơi nhận issue đã đổi; hãy tạo bản nháp mới để xem lại đích ghi."
        # Commit the send claim before any HTTP. A crash cannot replay a write.
        with self.db:
            claimed = self.db.execute("UPDATE drafts SET status='sending' WHERE id=? AND status='draft'", (identity,))
        if claimed.rowcount != 1:
            return "Bản nháp đang được xử lý; không tạo lặp."
        row = [identity, datetime.fromtimestamp(now, timezone.utc).isoformat(), draft["title"], draft["body"],
               "open", draft["user_id"], draft["chat_id"], draft["chat_type"]]
        try:
            if access_guard is None:
                await client.append_issue(row)
            else:
                await client.append_issue(row, access_guard=access_guard)
        except SheetsUncertain:
            status = "uncertain"
            answer = f"Chưa xác nhận được issue {identity} đã ghi hay chưa. Hãy kiểm tra cột id trên Sheets; bot không tự tạo lại."
        except SheetsError:
            status = "failed"
            answer = "Chưa tạo được issue. Kiểm tra quyền và mẫu Sheets; sau đó tạo bản nháp mới."
        except BaseException:
            with self.db:
                self.db.execute("UPDATE drafts SET status='uncertain' WHERE id=?", (identity,))
            raise
        else:
            status = "created"
            answer = f"Đã tạo issue {identity} trong tab {client.settings.issues_tab}: {draft['title']}"
        with self.db:
            self.db.execute("UPDATE drafts SET status=? WHERE id=?", (status, identity))
        return answer


def issue_target(client):
    return client.settings.spreadsheet_id + "/" + client.settings.issues_tab


def preview(draft, tab):
    return (f"BẢN NHÁP ISSUE — chưa ghi vào Sheets\n"
            f"Nơi nhận: Google Sheets / {tab}\n"
            f"https://docs.google.com/spreadsheets/d/{draft['target'].split('/')[0]}/edit\n"
            f"Tiêu đề: {draft['title']}\nMô tả: {draft['body']}\n"
            f"Trạng thái: open\n"
            f"Sẽ lưu ID người yêu cầu và cuộc trò chuyện cùng issue.\n"
            f"Trong 10 phút, chính bạn xác nhận tại đây:\n/issue xacnhan {draft['id']}\n"
            f"Hoặc hủy: /issue huy {draft['id']}")
