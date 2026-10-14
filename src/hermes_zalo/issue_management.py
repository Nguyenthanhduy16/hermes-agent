"""Durable issue workflow. The legacy drafts table is intentionally untouched.

Callers must authenticate each administrator immediately before calling this
store and supply current registration grants for member operations/notifications.
No model output is accepted as an administrator identity or a permission grant.
"""
from contextlib import contextmanager
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time


KINDS = frozenset({'support', 'feature'})
STATUSES = frozenset({'new', 'in_progress', 'waiting', 'done'})
MANAGED_TARGET = 'managed-issues:v1'
STATUS_LABELS = {'new': 'Mới', 'in_progress': 'Đang xử lý', 'waiting': 'Chờ bổ sung', 'done': 'Hoàn thành'}


class IssueError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _text(value, label, maximum, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value.strip()) > maximum:
        raise IssueError(f'{label} không hợp lệ (tối đa {maximum} ký tự).')
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise IssueError(f'{label} chứa ký tự không hợp lệ.')
    return value.strip()


def _member(value):
    return _text(value, 'Mã thành viên', 128)


def _admin(actor):
    if not isinstance(actor, dict) or not actor.get('username') or actor.get('active') is False:
        raise IssueError('Vui lòng đăng nhập tài khoản quản trị.', 401)
    if actor.get('role') != 'faculty' and 'issues' not in actor.get('permissions', []):
        raise IssueError('Tài khoản chưa có quyền quản lý Issue.', 403)
    if actor.get('role', 'admin') not in {'admin', 'faculty'}:
        raise IssueError('Tài khoản chưa có quyền quản lý Issue.', 403)
    return actor['username']


def _allowed(guard):
    if guard is None or not guard():
        raise IssueError('Bạn chưa được cấp quyền sử dụng Issue hoặc liên kết đã thay đổi.', 403)


class ManagedIssueStore:
    def __init__(self, path, clock=time.time):
        self.clock, self.lock = clock, threading.RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS managed_issue_drafts (
              id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
              member_id TEXT NOT NULL, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
              chat_type TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
              created REAL NOT NULL, expires REAL NOT NULL, status TEXT NOT NULL, issue_id TEXT,
              target TEXT NOT NULL DEFAULT 'managed-issues:v1');
            CREATE TABLE IF NOT EXISTS managed_issues (
              number INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,
              member_id TEXT, user_id TEXT NOT NULL, chat_id TEXT NOT NULL, chat_type TEXT NOT NULL,
              kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL,
              assignee TEXT, priority TEXT, estimate TEXT, due_date TEXT,
              created REAL NOT NULL, updated REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
              legacy_id TEXT UNIQUE, legacy_status TEXT, owner_resolved INTEGER NOT NULL DEFAULT 1);
            CREATE INDEX IF NOT EXISTS managed_issues_member ON managed_issues(member_id,number);
            CREATE INDEX IF NOT EXISTS managed_issues_assignee ON managed_issues(assignee,status);
            CREATE TABLE IF NOT EXISTS managed_issue_comments (
              id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id TEXT NOT NULL,
              actor TEXT NOT NULL, visibility TEXT NOT NULL, body TEXT NOT NULL,
              at REAL NOT NULL, revision INTEGER NOT NULL,
              FOREIGN KEY(issue_id) REFERENCES managed_issues(id));
            CREATE TABLE IF NOT EXISTS managed_issue_audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id TEXT NOT NULL,
              actor TEXT NOT NULL, action TEXT NOT NULL, at REAL NOT NULL,
              revision INTEGER NOT NULL, details TEXT NOT NULL,
              FOREIGN KEY(issue_id) REFERENCES managed_issues(id));
            CREATE TABLE IF NOT EXISTS managed_issue_outbox (
              id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL,
              issue_id TEXT NOT NULL, member_id TEXT NOT NULL, user_id TEXT NOT NULL,
              chat_id TEXT NOT NULL, chat_type TEXT NOT NULL, text TEXT NOT NULL,
              status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              created REAL NOT NULL, updated REAL NOT NULL, error_code TEXT NOT NULL DEFAULT '',
              FOREIGN KEY(issue_id) REFERENCES managed_issues(id));
            CREATE TABLE IF NOT EXISTS managed_issue_imports (
              legacy_id TEXT PRIMARY KEY, issue_id TEXT NOT NULL, content_hash TEXT NOT NULL,
              target TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS managed_issue_migrations (
              target TEXT PRIMARY KEY, source_rows INTEGER NOT NULL,
              source_hash TEXT NOT NULL, at REAL NOT NULL, actor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS managed_issue_migration_audit (
              id INTEGER PRIMARY KEY, legacy_id TEXT NOT NULL, action TEXT NOT NULL,
              reason TEXT NOT NULL, actor TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS managed_issue_reply_receipts (
              request_key TEXT PRIMARY KEY, issue_id TEXT NOT NULL, member_id TEXT NOT NULL,
              user_id TEXT NOT NULL, chat_id TEXT NOT NULL, chat_type TEXT NOT NULL,
              body TEXT NOT NULL);
        ''')

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self):
        with self.lock:
            self.db.close()

    def recover(self):
        """Call only on exclusive worker startup, never when opening a reader."""
        with self.transaction() as db:
            db.execute("UPDATE managed_issue_outbox SET status='uncertain',error_code='interrupted',updated=? WHERE status='sending'", (self.clock(),))

    def create(self, message, member_id, title, body, kind='support', *, access_guard=None, now=None):
        _allowed(access_guard)
        member_id = _member(member_id)
        title, body = _text(title, 'Tiêu đề', 160), _text(body, 'Mô tả', 1200)
        if kind not in KINDS or message.chat_type not in {'PRIVATE', 'GROUP'}:
            raise IssueError('Loại Issue hoặc cuộc trò chuyện không hợp lệ.')
        now = self.clock() if now is None else now
        with self.transaction() as db:
            previous = db.execute('SELECT * FROM managed_issue_drafts WHERE request_key=?', (message.key,)).fetchone()
            if previous:
                if (previous['member_id'], previous['user_id'], previous['chat_id'], previous['chat_type']) != (
                        member_id, message.user_id, message.chat_id, message.chat_type):
                    raise IssueError('Sự kiện đã được liên kết với người khác.', 409)
                if (previous['title'], previous['body'], previous['kind']) != (title, body, kind):
                    raise IssueError('Sự kiện có nội dung khác với bản nháp đã lưu; hãy gửi tin nhắn mới.', 409)
                return dict(previous)
            count = db.execute("SELECT COUNT(*) FROM managed_issue_drafts WHERE member_id=? AND status='draft' AND expires>?", (member_id, now)).fetchone()[0]
            if count >= 20:
                raise IssueError('Bạn đang có 20 bản nháp; hãy hủy hoặc chờ bản nháp hết hạn.', 429)
            identity = secrets.token_hex(4).upper()
            db.execute('INSERT INTO managed_issue_drafts (id,request_key,member_id,user_id,chat_id,chat_type,kind,title,body,created,expires,status,issue_id,target) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,?)',
                       (identity, message.key, member_id, message.user_id, message.chat_id,
                        message.chat_type, kind, title, body, now, now + 600, 'draft', MANAGED_TARGET))
            return dict(db.execute('SELECT * FROM managed_issue_drafts WHERE id=?', (identity,)).fetchone())

    def scoped(self, identity, message, member_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM managed_issue_drafts WHERE id=? AND member_id=? AND user_id=? AND chat_id=? AND chat_type=?',
                                  (identity, member_id, message.user_id, message.chat_id, message.chat_type)).fetchone()
            return dict(row) if row else None

    def cancel(self, identity, message, member_id, *, access_guard=None):
        _allowed(access_guard)
        with self.transaction() as db:
            result = db.execute("UPDATE managed_issue_drafts SET status='cancelled' WHERE id=? AND member_id=? AND user_id=? AND chat_id=? AND chat_type=? AND status='draft'",
                                (identity, member_id, message.user_id, message.chat_id, message.chat_type))
            return result.rowcount == 1

    def confirm(self, identity, message, member_id, *, access_guard=None, now=None):
        _allowed(access_guard)
        now = self.clock() if now is None else now
        with self.transaction() as db:
            draft = db.execute('SELECT * FROM managed_issue_drafts WHERE id=? AND member_id=? AND user_id=? AND chat_id=? AND chat_type=?',
                               (identity, member_id, message.user_id, message.chat_id, message.chat_type)).fetchone()
            if draft is None:
                raise IssueError('Không tìm thấy bản nháp của bạn trong cuộc trò chuyện này.', 404)
            if draft['status'] == 'created':
                return self._member_view(self._row(db, draft['issue_id']))
            if draft['target'] != MANAGED_TARGET:
                raise IssueError('Nơi lưu Issue đã đổi; hãy tạo bản nháp mới để xác nhận.', 409)
            if draft['status'] != 'draft':
                raise IssueError('Bản nháp đã hủy hoặc hết hạn; hãy tạo bản nháp mới.', 409)
            if draft['expires'] <= now:
                raise IssueError('Bản nháp hết hạn sau 10 phút; hãy tạo bản nháp mới.', 409)
            issue = self._insert(db, dict(draft), now)
            db.execute("UPDATE managed_issue_drafts SET status='created',issue_id=? WHERE id=?", (issue['id'], identity))
            self._audit(db, issue, 'member:' + member_id, 'create', {'kind': issue['kind']}, now)
            return self._member_view(issue)

    def _insert(self, db, value, now, *, legacy_id=None, legacy_status=None):
        cursor = db.execute('INSERT INTO managed_issues (member_id,user_id,chat_id,chat_type,kind,title,body,status,created,updated,legacy_id,legacy_status,owner_resolved) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (value.get('member_id'), value['user_id'], value['chat_id'], value['chat_type'],
                             value['kind'], value['title'], value['body'], value.get('status', 'new') if value.get('status') in STATUSES else 'new',
                             now, now, legacy_id, legacy_status, int(bool(value.get('member_id')))))
        identity = f'ISS-{cursor.lastrowid:06d}'
        db.execute('UPDATE managed_issues SET id=? WHERE number=?', (identity, cursor.lastrowid))
        return self._row(db, identity)

    def _row(self, db, identity):
        row = db.execute('SELECT * FROM managed_issues WHERE id=?', (identity,)).fetchone()
        if row is None:
            row = db.execute('SELECT * FROM managed_issues WHERE legacy_id=?', (identity,)).fetchone()
        if row is None:
            raise IssueError('Không tìm thấy Issue.', 404)
        return dict(row)

    @staticmethod
    def _member_view(issue):
        return {key: issue[key] for key in ('id', 'kind', 'title', 'body', 'status', 'created', 'updated', 'legacy_id')}

    def member_detail(self, identity, member_id):
        _member(member_id)
        with self.lock:
            issue = self._row(self.db, identity)
            if not issue['owner_resolved'] or issue['member_id'] != member_id:
                raise IssueError('Không tìm thấy Issue của bạn.', 404)
            result = self._member_view(issue)
            result['comments'] = [dict(row) for row in self.db.execute("SELECT body,at FROM managed_issue_comments WHERE issue_id=? AND visibility='public' ORDER BY id DESC LIMIT 50", (issue['id'],))][::-1]
            return result

    @staticmethod
    def _page(page, limit):
        if type(page) is not int or not 1 <= page <= 10000 or type(limit) is not int or not 1 <= limit <= 50:
            raise IssueError('Trang hoặc số kết quả không hợp lệ.')
        return limit, (page - 1) * limit

    def member_list(self, member_id, page=1, limit=10):
        _member(member_id)
        limit, offset = self._page(page, limit)
        with self.lock:
            total = self.db.execute('SELECT COUNT(*) FROM managed_issues WHERE member_id=? AND owner_resolved=1', (member_id,)).fetchone()[0]
            rows = self.db.execute('SELECT * FROM managed_issues WHERE member_id=? AND owner_resolved=1 ORDER BY number DESC LIMIT ? OFFSET ?', (member_id, limit, offset))
            return {'items': [self._member_view(dict(row)) for row in rows], 'total': total, 'page': page, 'limit': limit}

    def member_reply(self, identity, message, member_id, text, *, access_guard=None):
        _allowed(access_guard)
        member_id, text = _member(member_id), _text(text, 'Thông tin bổ sung', 1200)
        now = self.clock()
        with self.transaction() as db:
            issue = self._row(db, identity)
            if (not issue['owner_resolved'] or message.chat_type != 'PRIVATE' or
                    (issue['member_id'], issue['user_id'], issue['chat_id'], issue['chat_type']) !=
                    (member_id, message.user_id, message.chat_id, message.chat_type)):
                raise IssueError('Chỉ người gửi được bổ sung Issue tại đúng tin nhắn riêng đã tạo.', 403)
            prior = db.execute('SELECT * FROM managed_issue_reply_receipts WHERE request_key=?', (message.key,)).fetchone()
            if prior:
                if (prior['issue_id'], prior['member_id'], prior['user_id'], prior['chat_id'], prior['chat_type'], prior['body']) != (
                        issue['id'], member_id, message.user_id, message.chat_id, message.chat_type, text):
                    raise IssueError('Tin nhắn bổ sung đã được xử lý với nội dung khác.', 409)
            else:
                if issue['status'] == 'done':
                    raise IssueError('Issue đã hoàn thành; nhờ Admin mở lại trước khi bổ sung.', 409)
                revision = issue['revision'] + 1
                status = 'in_progress' if issue['status'] == 'waiting' and issue['assignee'] else issue['status']
                db.execute('UPDATE managed_issues SET revision=?,updated=?,status=? WHERE id=?', (revision, now, status, issue['id']))
                db.execute('INSERT INTO managed_issue_comments(issue_id,actor,visibility,body,at,revision) VALUES (?,?,?,?,?,?)',
                           (issue['id'], 'member:' + member_id, 'public', text, now, revision))
                db.execute('INSERT INTO managed_issue_reply_receipts VALUES (?,?,?,?,?,?,?)',
                           (message.key, issue['id'], member_id, message.user_id, message.chat_id, message.chat_type, text))
                old_status = issue['status']
                issue.update(revision=revision, updated=now, status=status)
                self._audit(db, issue, 'member:' + member_id, 'member_reply', {'from': old_status, 'to': status}, now)
        return self.member_detail(issue['id'], member_id)

    def list_issues(self, actor, *, kind=None, status=None, member_id=None, assignee=None, q='', page=1, limit=20):
        _admin(actor)
        limit, offset = self._page(page, limit)
        clauses, args = [], []
        for key, value, choices in (('kind', kind, KINDS), ('status', status, STATUSES)):
            if value:
                if value not in choices:
                    raise IssueError('Bộ lọc Issue không hợp lệ.')
                clauses.append(key + '=?')
                args.append(value)
        for key, value in (('member_id', member_id), ('assignee', assignee)):
            if value:
                if key == 'assignee' and value == 'unassigned':
                    clauses.append('assignee IS NULL')
                else:
                    clauses.append(key + '=?')
                    args.append(_text(value, 'Bộ lọc', 128))
        if q:
            q = _text(q, 'Tìm kiếm', 160)
            escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            clauses.append("(id LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR legacy_id LIKE ? ESCAPE '\\')")
            args.extend(['%' + escaped + '%'] * 3)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.lock:
            total = self.db.execute('SELECT COUNT(*) FROM managed_issues' + where, args).fetchone()[0]
            items = [dict(row) for row in self.db.execute('SELECT * FROM managed_issues' + where + ' ORDER BY number DESC LIMIT ? OFFSET ?', [*args, limit, offset])]
        return {'items': items, 'total': total, 'page': page, 'limit': limit}

    def detail(self, identity, actor):
        _admin(actor)
        with self.lock:
            issue = self._row(self.db, identity)
            comments = [dict(row) for row in self.db.execute('SELECT id,actor,visibility,body,at,revision FROM managed_issue_comments WHERE issue_id=? ORDER BY id DESC LIMIT 100', (issue['id'],))][::-1]
            audit = [{**dict(row), 'details': json.loads(row['details'])} for row in self.db.execute('SELECT actor,action,at,revision,details FROM managed_issue_audit WHERE issue_id=? ORDER BY id DESC LIMIT 100', (issue['id'],))][::-1]
            notifications = [dict(row) for row in self.db.execute('SELECT id,status,attempts,updated,error_code FROM managed_issue_outbox WHERE issue_id=? ORDER BY id DESC LIMIT 100', (issue['id'],))][::-1]
            return {'issue': issue, 'comments': comments, 'audit': audit, 'notifications': notifications}

    def counts_by_assignee(self, actor):
        _admin(actor)
        with self.lock:
            return {row['assignee']: row['total'] for row in self.db.execute("SELECT assignee,COUNT(*) AS total FROM managed_issues WHERE assignee IS NOT NULL AND status!='done' GROUP BY assignee")}

    def act(self, identity, actor, action, expected_revision, **payload):
        username = _admin(actor)
        allowed_keys = {'claim': set(), 'assign': {'assignee', 'reason', 'target_actor'},
                        'status': {'status', 'text'}, 'reopen': {'reason'},
                        'comment': {'text', 'visibility'}, 'plan': {'priority', 'estimate', 'due_date'}}
        if action not in allowed_keys or set(payload) - allowed_keys[action]:
            raise IssueError('Thao tác hoặc tham số Issue không hợp lệ.')
        if type(expected_revision) is not int or expected_revision < 1:
            raise IssueError('Thiếu phiên bản Issue hợp lệ.')
        now = self.clock()
        with self.transaction() as db:
            issue = self._row(db, identity)
            if issue['revision'] != expected_revision:
                raise IssueError('Issue vừa được cập nhật; hãy tải lại trước khi thao tác.', 409)
            faculty = actor.get('role') == 'faculty'
            if action in {'status', 'reopen', 'plan'} and not faculty and issue['assignee'] != username:
                raise IssueError('Chỉ người phụ trách hoặc Giảng viên được thực hiện thao tác này.', 403)
            changes, audit = {}, {}
            comment, notice_event = None, None
            if action == 'claim':
                if issue['assignee'] is not None or issue['status'] != 'new':
                    raise IssueError('Issue đã có người phụ trách hoặc không còn ở trạng thái Mới.', 409)
                changes.update(assignee=username, status='in_progress')
                audit['assignee'], notice_event = username, 'accepted'
            elif action == 'assign':
                if not faculty:
                    raise IssueError('Chỉ Giảng viên được phân công hoặc bàn giao Issue.', 403)
                target = payload.get('target_actor')
                _admin(target)
                assignee = _text(payload.get('assignee'), 'Người phụ trách', 48)
                if target['username'] != assignee:
                    raise IssueError('Tài khoản người nhận không khớp.', 409)
                reason = _text(payload.get('reason'), 'Lý do phân công', 500)
                changes['assignee'] = assignee
                if issue['status'] == 'new':
                    changes['status'], notice_event = 'in_progress', 'accepted'
                audit = {'from': issue['assignee'], 'to': assignee, 'reason': reason}
            elif action == 'status':
                status = payload.get('status')
                transitions = {'new': set(), 'in_progress': {'waiting', 'done'}, 'waiting': {'in_progress', 'done'}, 'done': set()}
                if status not in transitions[issue['status']]:
                    raise IssueError('Chuyển trạng thái không hợp lệ; Issue hoàn thành cần được mở lại kèm lý do.', 409)
                reply = _text(payload.get('text', ''), 'Phản hồi cho người gửi', 1200, empty=status == 'in_progress')
                changes['status'] = status
                audit = {'from': issue['status'], 'to': status}
                if reply:
                    comment = ('public', reply)
                if status in {'waiting', 'done'}:
                    notice_event = status
            elif action == 'reopen':
                if issue['status'] != 'done':
                    raise IssueError('Chỉ mở lại Issue đã hoàn thành.', 409)
                reason = _text(payload.get('reason'), 'Lý do mở lại', 500)
                changes['status'] = 'in_progress' if issue['assignee'] else 'new'
                audit = {'reason': reason, 'from': 'done', 'to': changes['status']}
            elif action == 'comment':
                visibility = payload.get('visibility')
                if visibility not in {'internal', 'public'}:
                    raise IssueError('Chọn ghi chú nội bộ hoặc phản hồi cho người gửi.')
                if visibility == 'public' and not faculty and issue['assignee'] != username:
                    raise IssueError('Chỉ người phụ trách hoặc Giảng viên được phản hồi cho người gửi.', 403)
                comment = (visibility, _text(payload.get('text'), 'Nội dung', 1200))
                audit = {'visibility': visibility}
                if visibility == 'public':
                    notice_event = 'reply'
            elif action == 'plan':
                if issue['kind'] != 'feature':
                    raise IssueError('Ưu tiên, estimate và ngày hoàn thành chỉ áp dụng cho đề xuất tính năng.')
                priority = payload.get('priority') or None
                if priority not in {None, 'low', 'normal', 'high', 'urgent'}:
                    raise IssueError('Mức ưu tiên không hợp lệ.')
                estimate = _text(payload.get('estimate') or '', 'Estimate', 100, empty=True) or None
                due = payload.get('due_date') or None
                if due:
                    if not isinstance(due, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', due):
                        raise IssueError('Ngày dự kiến cần có dạng YYYY-MM-DD.')
                    try:
                        date.fromisoformat(due)
                    except ValueError as exc:
                        raise IssueError('Ngày dự kiến không hợp lệ.') from exc
                changes.update(priority=priority, estimate=estimate, due_date=due)
                audit = dict(changes)
            changes.update(revision=issue['revision'] + 1, updated=now)
            db.execute('UPDATE managed_issues SET ' + ','.join(key + '=?' for key in changes) + ' WHERE id=? AND revision=?', [*changes.values(), issue['id'], issue['revision']])
            issue.update(changes)
            if comment:
                db.execute('INSERT INTO managed_issue_comments(issue_id,actor,visibility,body,at,revision) VALUES (?,?,?,?,?,?)',
                           (issue['id'], username, comment[0], comment[1], now, issue['revision']))
            self._audit(db, issue, username, action, audit, now)
            if notice_event:
                self._notify(db, issue, notice_event, comment[1] if comment and comment[0] == 'public' else '', now)
        return self.detail(issue['id'], actor)

    @staticmethod
    def _audit(db, issue, actor, action, details, now):
        db.execute('INSERT INTO managed_issue_audit(issue_id,actor,action,at,revision,details) VALUES (?,?,?,?,?,?)',
                   (issue['id'], actor, action, now, issue['revision'], json.dumps(details, ensure_ascii=False)))

    @staticmethod
    def _notify(db, issue, event, reply, now):
        # Historical unresolved records have no trusted recipient. A group ID is
        # retained as GROUP, never repurposed as a DM address.
        if not issue['member_id'] or not issue['owner_resolved']:
            return
        label = {'accepted': 'Đã có người tiếp nhận', 'waiting': 'Cần bạn bổ sung thông tin',
                 'done': 'Đã hoàn thành', 'reply': 'Có phản hồi mới'}[event]
        text = f"{issue['id']} — {label}\n{issue['title']}"
        if reply:
            text += '\n' + reply
        text += f"\n/issue xem {issue['id']} — xem chi tiết"
        db.execute('INSERT OR IGNORE INTO managed_issue_outbox(event_key,issue_id,member_id,user_id,chat_id,chat_type,text,status,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (f"{issue['id']}:{issue['revision']}:{event}", issue['id'], issue['member_id'], issue['user_id'], issue['chat_id'], issue['chat_type'], text, 'pending', now, now))

    def pending_notifications(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise IssueError('Giới hạn thông báo không hợp lệ.')
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM managed_issue_outbox WHERE status='pending' ORDER BY id LIMIT ?", (limit,))]

    def claim_notification(self, access_guard, *, notification_id=None):
        """Guard checks the *current* member/user/chat triple before each send."""
        if not callable(access_guard):
            raise IssueError('Thiếu kiểm tra quyền thông báo.', 403)
        if notification_id is not None and (type(notification_id) is not int or notification_id < 1):
            raise IssueError('Mã thông báo không hợp lệ.')
        with self.transaction() as db:
            query = "SELECT * FROM managed_issue_outbox WHERE status='pending'"
            args = ()
            if notification_id is not None:
                query += ' AND id=?'
                args = (notification_id,)
            for row in db.execute(query + ' ORDER BY id LIMIT 100', args).fetchall():
                notice = dict(row)
                if not access_guard(notice):
                    db.execute("UPDATE managed_issue_outbox SET status='blocked',error_code='access_revoked',updated=? WHERE id=?", (self.clock(), notice['id']))
                    continue
                db.execute("UPDATE managed_issue_outbox SET status='sending',attempts=attempts+1,updated=? WHERE id=? AND status='pending'", (self.clock(), notice['id']))
                notice['attempts'] += 1
                notice['status'] = 'sending'
                return notice
        return None

    def finish_notification(self, identity, status, error_code=''):
        if status not in {'sent', 'failed', 'uncertain', 'blocked'}:
            raise IssueError('Trạng thái gửi thông báo không hợp lệ.')
        if error_code not in {'', 'timeout', 'network', 'rate_limit', 'rejected', 'access_revoked', 'interrupted'}:
            error_code = 'rejected'
        with self.transaction() as db:
            result = db.execute("UPDATE managed_issue_outbox SET status=?,error_code=?,updated=? WHERE id=? AND status='sending'", (status, error_code, self.clock(), identity))
            return result.rowcount == 1

    def retry_notification(self, identity, actor):
        username = _admin(actor)
        with self.transaction() as db:
            row = db.execute('SELECT * FROM managed_issue_outbox WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise IssueError('Không tìm thấy thông báo.', 404)
            if row['status'] != 'failed' or row['attempts'] >= 3:
                raise IssueError('Chỉ thử lại lỗi chắc chắn chưa gửi, tối đa 3 lần; không tự gửi lại trạng thái chưa rõ.', 409)
            db.execute("UPDATE managed_issue_outbox SET status='pending',error_code='',updated=? WHERE id=?", (self.clock(), identity))
            self._audit(db, self._row(db, row['issue_id']), username, 'notification_retry', {'notification_id': identity}, self.clock())
            return {'id': identity, 'status': 'pending'}

    def import_legacy(self, rows, *, target, resolve_member):
        """Operator-only explicit import of the existing Sheets rows.

        rows: eight-column legacy arrays (without header). resolve_member receives
        the row dict and must return an exact stable member ID, or None. Import is
        atomic/idempotent and refuses changed rows instead of silently overwriting.
        This method never writes to Sheets or activates managed mode.
        """
        target = _text(target, 'Nguồn nhập', 300)
        now, imported = self.clock(), []
        with self.transaction() as db:
            for raw in rows:
                if len(raw) < 8:
                    raise IssueError('Dòng Issue cũ thiếu cột; chưa nhập dữ liệu.', 409)
                identity, created, title, body, status, user, chat, kind = [str(value) for value in raw[:8]]
                identity = _text(identity, 'Mã Issue cũ', 128)
                title, body = _text(title, 'Tiêu đề', 160), _text(body, 'Mô tả', 1200)
                if kind not in {'PRIVATE', 'GROUP'} or not user or not chat:
                    raise IssueError('Issue cũ thiếu định danh cuộc trò chuyện hợp lệ.', 409)
                content = hashlib.sha256(json.dumps(list(raw[:8]), ensure_ascii=False).encode()).hexdigest()
                old = db.execute('SELECT * FROM managed_issue_imports WHERE legacy_id=?', (identity,)).fetchone()
                if old:
                    if old['content_hash'] != content or old['target'] != target:
                        raise IssueError('Issue cũ đã thay đổi sau khi nhập; cần đối soát thủ công.', 409)
                    imported.append(old['issue_id'])
                    continue
                value = {'legacy_id': identity, 'created': created, 'title': title, 'body': body,
                         'legacy_status': status, 'user_id': user, 'chat_id': chat, 'chat_type': kind, 'kind': 'support'}
                member_id = resolve_member(dict(value))
                value['member_id'] = _member(member_id) if member_id else None
                statuses = {'open': 'new', 'new': 'new', 'in_progress': 'in_progress', 'waiting': 'waiting', 'done': 'done', 'closed': 'done'}
                if status not in statuses:
                    raise IssueError('Trạng thái Issue cũ chưa có ánh xạ; cần đối soát trước khi nhập.', 409)
                value['status'] = statuses[status]
                issue = self._insert(db, value, now, legacy_id=identity, legacy_status=status)
                db.execute('INSERT INTO managed_issue_imports VALUES (?,?,?,?,?)', (identity, issue['id'], content, target, now))
                self._audit(db, issue, 'local-operator', 'legacy_import', {'legacy_id': identity, 'target': target, 'original_created': created}, now)
                imported.append(issue['id'])
        return imported

    def migration_report(self):
        """Read-only cutover blockers. Import all legacy source rows separately."""
        with self.lock:
            unresolved = self.db.execute('SELECT COUNT(*) FROM managed_issues WHERE owner_resolved=0').fetchone()[0]
            drafts = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='drafts'").fetchone()
            uncertain = missing = pending = 0
            if drafts:
                uncertain = self.db.execute("SELECT COUNT(*) FROM drafts WHERE status IN ('sending','uncertain')").fetchone()[0]
                missing = self.db.execute("SELECT COUNT(*) FROM drafts d WHERE status='created' AND NOT EXISTS (SELECT 1 FROM managed_issue_imports i WHERE i.legacy_id=d.id AND i.target=d.target)").fetchone()[0]
                pending = self.db.execute("SELECT COUNT(*) FROM drafts WHERE status='draft' AND expires>?", (self.clock(),)).fetchone()[0]
            reconciled = self.db.execute('SELECT COUNT(*) FROM managed_issue_migrations').fetchone()[0] > 0
            return {'unresolved_owners': unresolved, 'uncertain_legacy_writes': uncertain,
                    'unimported_created_drafts': missing, 'pending_legacy_drafts': pending,
                    'source_reconciled': reconciled,
                    'ready': reconciled and not (unresolved or uncertain or missing or pending)}

    def mark_source_reconciled(self, target, rows):
        """Operator attests a complete frozen source snapshot after importing it.

        Never call from startup based only on an empty local database. Empty
        snapshots also need explicit operator verification of the old source.
        """
        target = _text(target, 'Nguồn nhập', 300)
        with self.transaction() as db:
            report = self.migration_report()
            if any(report[key] for key in ('unresolved_owners', 'uncertain_legacy_writes',
                                          'unimported_created_drafts', 'pending_legacy_drafts')):
                raise IssueError('Chưa thể chuyển nguồn Issue: còn bản ghi cần đối soát.', 409)
            identities, digests = set(), []
            for raw in rows:
                if not isinstance(raw, (list, tuple)) or len(raw) < 8:
                    raise IssueError('Snapshot Issue thiếu cột.', 409)
                identity = str(raw[0])
                if identity in identities:
                    raise IssueError('Snapshot có mã Issue bị trùng; cần đối soát.', 409)
                identities.add(identity)
                digest = hashlib.sha256(json.dumps(list(raw[:8]), ensure_ascii=False).encode()).hexdigest()
                existing = db.execute('SELECT * FROM managed_issue_imports WHERE legacy_id=? AND target=?', (identity, target)).fetchone()
                if existing is None or existing['content_hash'] != digest:
                    raise IssueError('Snapshot chưa được nhập đầy đủ hoặc đã thay đổi.', 409)
                digests.append((identity, digest))
            expected = {row[0] for row in db.execute('SELECT legacy_id FROM managed_issue_imports WHERE target=?', (target,))}
            if expected != identities:
                raise IssueError('Snapshot không đầy đủ so với các Issue đã nhập.', 409)
            digest = hashlib.sha256(json.dumps(sorted(digests)).encode()).hexdigest()
            db.execute('INSERT INTO managed_issue_migrations VALUES (?,?,?,?,?) ON CONFLICT(target) DO UPDATE SET source_rows=excluded.source_rows,source_hash=excluded.source_hash,at=excluded.at,actor=excluded.actor',
                       (target, len(rows), digest, self.clock(), 'local-operator'))
        return self.migration_report()

    def reconcile_legacy_writes(self, target, *, not_created=(), reason):
        """Operator-only reconciliation after a complete frozen source import.

        Present rows must match the bound draft fields. Missing writes need an
        explicit not_created ID list; this never replays a Sheets write.
        """
        target = _text(target, 'Nguồn nhập', 300)
        reason = _text(reason, 'Lý do đối soát', 500)
        if not isinstance(not_created, (list, tuple, set, frozenset)):
            raise IssueError('Danh sách đối soát không hợp lệ.')
        not_created = {_text(value, 'Mã bản nháp', 128) for value in not_created}
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='drafts'").fetchone():
                if not_created:
                    raise IssueError('Không có bản nháp cũ để đối soát.', 409)
                return 0
            records = db.execute("SELECT * FROM drafts WHERE target=? AND status IN ('sending','uncertain')", (target,)).fetchall()
            if not_created - {row['id'] for row in records}:
                raise IssueError('Mã bản nháp không nằm trong danh sách cần đối soát.', 409)
            changed = 0
            for draft in records:
                imported = db.execute('SELECT i.* FROM managed_issues i JOIN managed_issue_imports m ON m.issue_id=i.id WHERE m.legacy_id=? AND m.target=?', (draft['id'], target)).fetchone()
                if imported:
                    if draft['id'] in not_created or any(imported[key] != draft[key] for key in ('title', 'body', 'user_id', 'chat_id', 'chat_type')):
                        raise IssueError('Issue đã nhập xung đột với bản nháp cũ; cần kiểm tra lại.', 409)
                    status = 'created'
                elif draft['id'] in not_created:
                    status = 'failed'
                else:
                    continue
                db.execute('UPDATE drafts SET status=? WHERE id=?', (status, draft['id']))
                db.execute('INSERT INTO managed_issue_migration_audit(legacy_id,action,reason,actor,at) VALUES (?,?,?,?,?)',
                           (draft['id'], 'reconcile_' + status, reason, 'local-operator', self.clock()))
                changed += 1
            return changed


def preview(draft):
    kind = 'Báo lỗi / hỗ trợ' if draft['kind'] == 'support' else 'Góp ý / đề xuất'
    return (f"BẢN NHÁP ISSUE — chưa tạo\n{kind}\n{draft['title']}\n{draft['body']}\n\n"
            f"Xác nhận trong 10 phút:\n/issue xacnhan {draft['id']}\n"
            f"Hủy: /issue huy {draft['id']}")
