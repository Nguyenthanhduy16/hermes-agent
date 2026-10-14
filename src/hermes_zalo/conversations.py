"""Bounded, persistent conversation history and explicitly curated context.

This module never checks bot/admin privileges itself: callers must authorize a
capture or mutation first. ``admitted=False`` never records a message. Only a
currently approved registration's stable member id may be passed by the bot.
``complete_turn`` makes successful, non-command turns eligible for later AI
context. The caller must give ``prepare_context`` to an isolated model session;
the store does not modify Hermes' own history or invoke a model.

Admin writes require an actor and optimistic revision on updates. Records are
plain dictionaries; paginated reads return items/total/page/limit. Message bodies
are immutable *redacted* snapshots, not forensic copies of private credentials.
No method logs bodies, IDs, credentials, or phone numbers.
"""

from contextlib import contextmanager
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from .logging_utils import SecretFilter


class ConversationError(ValueError):
    """Invalid, missing, or unauthorized-scope data supplied by the caller."""


class ConversationConflict(ConversationError):
    """Data changed since the admin loaded it; reload before retrying."""


_INVISIBLE = re.compile('[\u200b-\u200f\u2060\ufeff]')
_PHONE = re.compile(r'(?<!\w)(?:\+?84|0)(?:[ .()\-]*\d){8,11}(?!\d)')
_TOKEN = re.compile(r'(?i)\b(?:sk[-_][A-Za-z0-9_\-]{8,}|AIza[A-Za-z0-9_\-]{20,}|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)\b')
_SECRET_ASSIGNMENT = re.compile(r'(?i)(\b(?:api[_ -]?key|bot[_ -]?token|access[_ -]?token|authorization|password|mật khẩu|secret(?:[_ -]?token)?)\b\s*[:=]\s*)([^\r\n,;]+)')
_PRIVATE_KEY = re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)', re.S)


def redact(text):
    """Best-effort masking; dedicated registration input is removed wholesale."""
    if not isinstance(text, str):
        raise ConversationError('Nội dung phải là văn bản.')
    text = _INVISIBLE.sub('', text)
    text = SecretFilter.redact(''.join(char for char in text if ord(char) >= 32 or char in '\n\r\t'))
    if re.match(r'^\s*/dangky(?:\s|$)', text, re.I):
        value = text.strip().split(maxsplit=1)
        return '/dangky trangthai' if len(value) == 2 and value[1].strip().casefold() == 'trangthai' else '/dangky [đã ẩn thông tin đăng ký]'
    text = _PRIVATE_KEY.sub('[đã ẩn khóa bí mật]', text)
    text = _SECRET_ASSIGNMENT.sub(lambda m: m.group(1) + '[đã ẩn]', text)
    text = _TOKEN.sub('[đã ẩn khóa]', text)
    return _PHONE.sub('[đã ẩn số điện thoại]', text)


def _bounded(value, label, maximum=200, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ConversationError(f'{label} không hợp lệ.')
    return value.strip()


def _session(value):
    value = _bounded(value, 'Cuộc trò chuyện', 700)
    parts = value.split(':')
    if len(parts) != 4 or parts[0] != 'zalo' or parts[1] not in ('PRIVATE', 'GROUP') or not all(parts[2:]):
        raise ConversationError('Cuộc trò chuyện không hợp lệ.')
    return value


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ConversationError(f'{label} không hợp lệ.')
    return float(value)


def _page(page, limit):
    if type(page) is not int or type(limit) is not int or not 1 <= page <= 1000000 or not 1 <= limit <= 100:
        raise ConversationError('Trang hoặc số dòng không hợp lệ.')
    return page, limit, (page - 1) * limit


class ConversationStore:
    MAX_BODY = 12000
    MAX_CONTEXT_MESSAGES = 12
    MAX_CONTEXT_SERIALIZED = 40000
    MAX_CONTEXT_MESSAGE_SERIALIZED = 2000
    MAX_CONTEXT_MEMORIES_SERIALIZED = 12000
    MAX_CONTEXT_SUMMARY_SERIALIZED = 3000

    @staticmethod
    def _serialized_size(value):
        return len(json.dumps(value, ensure_ascii=False))

    @classmethod
    def _excerpt(cls, text, budget):
        """Bound the serialized string, accounting for JSON escaping too."""
        if cls._serialized_size(text) <= budget:
            return text
        suffix = ' … [đã rút gọn]'
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if cls._serialized_size(text[:middle] + suffix) <= budget:
                low = middle
            else:
                high = middle - 1
        return text[:low] + suffix

    @staticmethod
    def _payload(summary, messages, memories):
        return {'summary': summary,
                'messages': [{'role': 'user' if m['direction'] == 'incoming' else 'assistant', 'content': m['body']} for m in messages],
                'memories': [{'content': m['content']} for m in memories]}

    def _bound_context(self, summary, summary_ids, messages, memories):
        """The Admin preview and actual model input use these same excerpts."""
        truncated = len(messages) > self.MAX_CONTEXT_MESSAGES
        selected_messages = []
        for original in messages[-self.MAX_CONTEXT_MESSAGES:]:
            item = dict(original)
            item['body'] = self._excerpt(original['body'], self.MAX_CONTEXT_MESSAGE_SERIALIZED)
            item['truncated'] = item['body'] != original['body']
            item['original_length'] = len(original['body'])
            truncated |= item['truncated']
            selected_messages.append(item)
        selected_memories = []
        for item in memories:
            candidate = selected_memories + [item]
            # Keep each curated memory intact; omitting a whole older record is
            # safer than clipping off a condition or negation from its content.
            if len(candidate) <= 20 and self._serialized_size([{'content': m['content']} for m in candidate]) <= self.MAX_CONTEXT_MEMORIES_SERIALIZED:
                selected_memories.append(item)
            else:
                truncated = True
        if self._serialized_size(summary) > self.MAX_CONTEXT_SUMMARY_SERIALIZED:
            # Legacy summaries exceeding the new bound are omitted until an
            # Admin rebuild. Do not claim source IDs whose text was not used.
            summary, summary_ids, truncated = '', [], True
        while self._serialized_size(self._payload(summary, selected_messages, selected_memories)) > self.MAX_CONTEXT_SERIALIZED:
            truncated = True
            if selected_messages:
                selected_messages.pop(0)
            elif selected_memories:
                selected_memories.pop()
            else:
                summary, summary_ids = '', []
        return summary, summary_ids, selected_messages, selected_memories, truncated

    def __init__(self, path, retention_days=30, clock=time.time):
        if type(retention_days) is not int or not 1 <= retention_days <= 365:
            raise ConversationError('Thời hạn lưu phải từ 1 đến 365 ngày.')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock, self.retention_days = clock, retention_days
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS conversation_sessions (
          session_key TEXT PRIMARY KEY, chat_type TEXT NOT NULL, chat_id TEXT NOT NULL,
          user_id TEXT NOT NULL, member_id TEXT, created REAL NOT NULL, updated REAL NOT NULL,
          revision INTEGER NOT NULL DEFAULT 0, cutoff INTEGER NOT NULL DEFAULT 0,
          summary TEXT NOT NULL DEFAULT '', summary_ids TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS conversation_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT, dedup_key TEXT UNIQUE NOT NULL,
          session_key TEXT NOT NULL REFERENCES conversation_sessions(session_key),
          member_id TEXT, direction TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL,
          created REAL NOT NULL, incoming_key TEXT NOT NULL, eligible INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS conversation_message_scope ON conversation_messages(session_key,id);
        CREATE INDEX IF NOT EXISTS conversation_message_time ON conversation_messages(created);
        CREATE TABLE IF NOT EXISTS conversation_memories (
          id TEXT PRIMARY KEY, content TEXT NOT NULL, member_id TEXT, scope TEXT NOT NULL,
          session_key TEXT, group_id TEXT, source_message_id INTEGER, active INTEGER NOT NULL,
          expires REAL, revision INTEGER NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
          source_expired INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS conversation_memory_member ON conversation_memories(member_id);
        CREATE TABLE IF NOT EXISTS conversation_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL,
          member_id TEXT, session_key TEXT, target_id TEXT, details TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS conversation_notices (
          session_key TEXT PRIMARY KEY, delivered REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS conversation_context_usage (
          session_key TEXT PRIMARY KEY, prepared REAL NOT NULL, revision INTEGER NOT NULL,
          message_ids TEXT NOT NULL, memory_ids TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conversation_memory_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL,
          source_message_id INTEGER, revision INTEGER NOT NULL, actor TEXT NOT NULL,
          snapshot TEXT NOT NULL, created REAL NOT NULL,
          UNIQUE(memory_id,revision));
        ''')
        self.db.commit()

    @contextmanager
    def _write(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
            except BaseException:
                self.db.rollback()
                raise
            else:
                self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def _audit(self, actor, action, *, member_id=None, session_key=None, target_id=None, details=None):
        self.db.execute('INSERT INTO conversation_audit(actor,action,member_id,session_key,target_id,details,created) VALUES(?,?,?,?,?,?,?)',
                        (_bounded(actor, 'Người thực hiện'), action, member_id, session_key, str(target_id) if target_id is not None else None,
                         json.dumps(details or {}, ensure_ascii=False), self.clock()))

    @staticmethod
    def _memory_snapshot(row):
        # Audit retains the change, never an undeletable copy of expired content.
        result = {k: row[k] for k in ('id', 'scope', 'member_id', 'session_key', 'group_id', 'source_message_id', 'active', 'expires', 'revision')}
        result['content_sha256'] = sha256(row['content'].encode()).hexdigest()
        result['content_length'] = len(row['content'])
        return result

    def _purge(self):
        cutoff = self.clock() - self.retention_days * 86400
        expired = [r[0] for r in self.db.execute('SELECT id FROM conversation_messages WHERE created < ?', (cutoff,))]
        if expired:
            self.db.execute('DELETE FROM conversation_memory_versions WHERE source_message_id IN (SELECT id FROM conversation_messages WHERE created < ?)', (cutoff,))
            self.db.execute("UPDATE conversation_memories SET content='', active=0, source_expired=1, revision=revision+1, updated=? WHERE source_message_id IN (SELECT id FROM conversation_messages WHERE created < ?) AND source_expired=0", (self.clock(), cutoff))
            expired_set = set(expired)
            for row in self.db.execute("SELECT session_key, summary_ids FROM conversation_sessions WHERE summary <> ''").fetchall():
                if expired_set.intersection(json.loads(row['summary_ids'])):
                    self.db.execute("UPDATE conversation_sessions SET summary='',summary_ids='[]',revision=revision+1 WHERE session_key=?", (row['session_key'],))
            self.db.execute('DELETE FROM conversation_messages WHERE created < ?', (cutoff,))
        self.db.execute('UPDATE conversation_memories SET active=0,revision=revision+1,updated=? WHERE active=1 AND expires IS NOT NULL AND expires<=?', (self.clock(), self.clock()))
        self.db.execute('DELETE FROM conversation_memory_versions WHERE created < ?', (cutoff,))
        return len(expired)

    def _version(self, memory, actor):
        self.db.execute('INSERT INTO conversation_memory_versions(memory_id,source_message_id,revision,actor,snapshot,created) VALUES(?,?,?,?,?,?)',
                        (memory['id'], memory['source_message_id'], memory['revision'], actor, json.dumps(dict(memory), ensure_ascii=False), self.clock()))

    def memory_versions(self, memory_id, page=1, limit=30):
        """Protected before/after content history, bounded by history retention.

        API callers must require the same private-conversation permission as
        memory content. A deleted source removes every source-backed version.
        """
        memory_id = _bounded(memory_id, 'Mã ghi nhớ')
        with self._write():
            self._purge()
            result = self._paged('conversation_memory_versions', ['memory_id=?'], [memory_id], page, limit)
            for row in result['items']:
                row['snapshot'] = json.loads(row['snapshot'])
            return result

    def maintenance(self):
        with self._write():
            return {'deleted_messages': self._purge()}

    def _message_scope(self, message):
        key = _session(message.session_key)
        for value in (message.chat_id, message.user_id, message.message_id):
            if ':' in _bounded(value, 'Mã tin nhắn'):
                raise ConversationError('Mã tin nhắn không hợp lệ.')
        return key

    def record_incoming(self, message, member_id=None, *, admitted=True):
        if admitted is not True:
            return None
        key = self._message_scope(message)
        member_id = _bounded(member_id, 'Mã thành viên', optional=True)
        body = redact(message.text)[:self.MAX_BODY]
        with self._write():
            self._purge()
            existing = self.db.execute('SELECT * FROM conversation_sessions WHERE session_key=?', (key,)).fetchone()
            if existing and member_id and existing['member_id'] not in (None, member_id):
                raise ConversationConflict('Liên kết thành viên của cuộc trò chuyện đã thay đổi; cần kiểm tra trước khi lưu.')
            self.db.execute('INSERT OR IGNORE INTO conversation_sessions(session_key,chat_type,chat_id,user_id,member_id,created,updated) VALUES(?,?,?,?,?,?,?)',
                            (key, message.chat_type, message.chat_id, message.user_id, member_id, self.clock(), self.clock()))
            # A session may acquire its stable identity after registration. Old
            # unapproved message rows deliberately retain their original scope.
            self.db.execute('UPDATE conversation_sessions SET member_id=COALESCE(member_id,?),updated=? WHERE session_key=?', (member_id, self.clock(), key))
            cursor = self.db.execute("INSERT OR IGNORE INTO conversation_messages(dedup_key,session_key,member_id,direction,body,status,created,incoming_key) VALUES(?,?,?,'incoming',?,'received',?,?)", ('in:' + message.key, key, member_id, body, self.clock(), message.key))
            stored = dict(self.db.execute('SELECT * FROM conversation_messages WHERE dedup_key=?', ('in:' + message.key,)).fetchone())
            if stored['session_key'] != key:
                raise ConversationConflict('Tin nhắn đã được ghi nhận ở một phiên khác.')
            if cursor.rowcount:
                self.db.execute('UPDATE conversation_sessions SET revision=revision+1 WHERE session_key=?', (key,))
            return stored

    def record_outgoing(self, message, text, *, status='sent', delivery_key=None):
        key = self._message_scope(message)
        delivery_key = _bounded(delivery_key, 'Mã lần gửi', 300)
        if status not in ('sent', 'failed', 'uncertain'):
            raise ConversationError('Trạng thái gửi không hợp lệ.')
        body = redact(text)[:self.MAX_BODY]
        with self._write():
            self._purge()
            parent = self.db.execute('SELECT * FROM conversation_messages WHERE dedup_key=? AND session_key=?', ('in:' + message.key, key)).fetchone()
            if not parent:
                raise ConversationError('Không có tin nhận được phép lưu cho câu trả lời này.')
            # Delivery keys are scoped to an incoming event, preventing a caller's
            # repeated chunk index from overwriting another person's response.
            dedup_key = 'out:' + message.key + ':' + delivery_key
            cursor = self.db.execute("INSERT OR IGNORE INTO conversation_messages(dedup_key,session_key,member_id,direction,body,status,created,incoming_key,eligible) VALUES(?,?,?,'outgoing',?,?,?,?,?)", (dedup_key, key, parent['member_id'], body, status, self.clock(), message.key, parent['eligible'] if status == 'sent' else 0))
            self.db.execute('UPDATE conversation_sessions SET updated=?,revision=revision+? WHERE session_key=?', (self.clock(), int(cursor.rowcount > 0), key))
            return dict(self.db.execute('SELECT * FROM conversation_messages WHERE dedup_key=?', (dedup_key,)).fetchone())

    def complete_turn(self, message, is_chat=True):
        key = self._message_scope(message)
        allowed = is_chat is True and not message.text.lstrip().startswith('/')
        with self._write():
            cursor = self.db.execute("UPDATE conversation_messages SET eligible=? WHERE incoming_key=? AND session_key=? AND eligible<>? AND (direction='incoming' OR status='sent')", (int(allowed), message.key, key, int(allowed)))
            if cursor.rowcount:
                self.db.execute('UPDATE conversation_sessions SET revision=revision+1 WHERE session_key=?', (key,))

    def _paged(self, table, where, args, page, limit, order='id DESC'):
        page, limit, offset = _page(page, limit)
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        total = self.db.execute('SELECT COUNT(*) FROM ' + table + clause, args).fetchone()[0]
        rows = self.db.execute('SELECT * FROM ' + table + clause + ' ORDER BY ' + order + ' LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
        return {'items': [dict(r) for r in rows], 'total': total, 'page': page, 'limit': limit}

    def sessions(self, member_id=None, chat_id=None, page=1, limit=20):
        where, args = [], []
        for column, value in (('member_id', member_id), ('chat_id', chat_id)):
            if value is not None:
                where.append(column + '=?')
                args.append(_bounded(value, 'Bộ lọc'))
        with self._write():
            self._purge()
            result = self._paged('conversation_sessions', where, args, page, limit, 'updated DESC,session_key')
            for row in result['items']:
                row['updated_at'], row['created_at'] = row['updated'], row['created']
                row['message_count'] = self.db.execute('SELECT COUNT(*) FROM conversation_messages WHERE session_key=?', (row['session_key'],)).fetchone()[0]
            return result

    def history(self, session_key=None, member_id=None, chat_id=None, start=None, end=None, page=1, limit=30, message_id=None):
        where, args = [], []
        if message_id is not None:
            if type(message_id) is not int or message_id < 1:
                raise ConversationError('Mã tin nhắn không hợp lệ.')
            where.append('id=?')
            args.append(message_id)
        for column, value in (('session_key', session_key), ('member_id', member_id)):
            if value is not None:
                where.append(column + '=?')
                args.append(_session(value) if column == 'session_key' else _bounded(value, 'Bộ lọc'))
        if chat_id is not None:
            where.append('session_key IN (SELECT session_key FROM conversation_sessions WHERE chat_id=?)')
            args.append(_bounded(chat_id, 'Cuộc trò chuyện'))
        for operation, value in (('>=', start), ('<=', end)):
            if value is not None:
                where.append('created' + operation + '?')
                args.append(_number(value, 'Thời gian'))
        if start is not None and end is not None and start > end:
            raise ConversationError('Khoảng thời gian không hợp lệ.')
        with self._write():
            self._purge()
            result = self._paged('conversation_messages', where, args, page, limit)
            for row in result['items']:
                session = self.db.execute('SELECT chat_id,chat_type,user_id FROM conversation_sessions WHERE session_key=?', (row['session_key'],)).fetchone()
                row.update(dict(session))
                row['content'], row['created_at'] = row['body'], row['created']
                row['role'] = 'user' if row['direction'] == 'incoming' else 'assistant'
            return result

    def _context(self, key, member_id, exclude_incoming=None):
        row = self.db.execute('SELECT * FROM conversation_sessions WHERE session_key=?', (key,)).fetchone()
        if not row:
            return {'session_key': key, 'revision': 0, 'summary': '', 'messages': [], 'memories': [], 'readiness': 'no_history', 'included_message_ids': [], 'included_memory_ids': [], 'truncated': False}
        if member_id is not None and member_id != row['member_id']:
            raise ConversationConflict('Liên kết thành viên không khớp cuộc trò chuyện.')
        rows = self.db.execute('''SELECT * FROM conversation_messages WHERE session_key=? AND id>? AND eligible=1
                               AND incoming_key IN (SELECT incoming_key FROM conversation_messages WHERE session_key=? AND direction='incoming' AND id>?)
                               AND (? IS NULL OR incoming_key<>?) ORDER BY id DESC LIMIT ?''',
                               (key, row['cutoff'], key, row['cutoff'], exclude_incoming, exclude_incoming, self.MAX_CONTEXT_MESSAGES + 1)).fetchall()
        # A new approved identity must not inherit historical turns captured
        # before that identity was approved (commands are ineligible as well).
        messages = [dict(r) for r in reversed(rows) if r['member_id'] == member_id]
        candidates = self.db.execute('''SELECT * FROM conversation_memories WHERE active=1 AND (expires IS NULL OR expires>?) AND (
            (scope='personal' AND ?='PRIVATE' AND member_id=? AND ? IS NOT NULL) OR
            (scope='session' AND session_key=? AND (member_id IS NULL OR member_id=?)) OR
            (scope='group' AND ?='GROUP' AND group_id=?)) ORDER BY updated DESC,id LIMIT 21''',
            (self.clock(), row['chat_type'], member_id, member_id, key, member_id, row['chat_type'], row['chat_id'])).fetchall()
        memories = []
        for memory in candidates:
            scope = memory['scope']
            permitted = (scope == 'personal' and row['chat_type'] == 'PRIVATE' and member_id is not None and memory['member_id'] == member_id)
            permitted |= scope == 'session' and memory['session_key'] == key and (memory['member_id'] is None or memory['member_id'] == member_id)
            permitted |= scope == 'group' and row['chat_type'] == 'GROUP' and memory['group_id'] == row['chat_id']
            if permitted:
                memories.append(dict(memory))
        summary_ids = json.loads(row['summary_ids'])
        summary = row['summary']
        # Summaries must obey the same identity boundary as the actual turns.
        if summary_ids:
            marks = ','.join('?' for _ in summary_ids)
            source_members = self.db.execute(f'SELECT member_id FROM conversation_messages WHERE id IN ({marks})', summary_ids).fetchall()
            if any(r['member_id'] != member_id for r in source_members):
                summary, summary_ids = '', []
        summary, summary_ids, messages, memories, truncated = self._bound_context(summary, summary_ids, messages, memories)
        return {'session_key': key, 'revision': row['revision'], 'summary': summary, 'messages': messages, 'memories': memories,
                'readiness': 'ready' if messages or summary or memories else 'empty',
                'included_message_ids': sorted(set(summary_ids + [m['id'] for m in messages])),
                'included_memory_ids': [m['id'] for m in memories], 'truncated': truncated}

    def context(self, session_key):
        key = _session(session_key)
        with self._write():
            self._purge()
            row = self.db.execute('SELECT member_id FROM conversation_sessions WHERE session_key=?', (key,)).fetchone()
            result = self._context(key, row['member_id'] if row else None)
            usage = self.db.execute('SELECT * FROM conversation_context_usage WHERE session_key=?', (key,)).fetchone()
            result['last_prepared'] = None if not usage else {'prepared_at': usage['prepared'], 'revision': usage['revision'], 'included_message_ids': json.loads(usage['message_ids']), 'included_memory_ids': json.loads(usage['memory_ids'])}
            return result

    def prepare_context(self, message, member_id=None):
        key = self._message_scope(message)
        member_id = _bounded(member_id, 'Mã thành viên', optional=True)
        with self._write():
            self._purge()
            result = self._context(key, member_id, exclude_incoming=message.key)
            self.db.execute('INSERT INTO conversation_context_usage(session_key,prepared,revision,message_ids,memory_ids) VALUES(?,?,?,?,?) ON CONFLICT(session_key) DO UPDATE SET prepared=excluded.prepared,revision=excluded.revision,message_ids=excluded.message_ids,memory_ids=excluded.memory_ids',
                            (key, self.clock(), result['revision'], json.dumps(result['included_message_ids']), json.dumps(result['included_memory_ids'])))
        # Structured data must remain lower-trust context in the caller's prompt.
        # No Zalo IDs/source IDs are included in the model-facing payload.
        result['payload'] = self._payload(result['summary'], result['messages'], result['memories'])
        return result

    def detach_registration_context(self, user_id, old_member_id, actor, *, stop_personal=False):
        """Discard usable route context before an authorized identity correction.

        The caller must hold the registration/route mutation guards until its
        own change completes. This operation only removes context grants; it
        never attaches the proposed replacement identity. A failed correction
        may therefore lose usable context, but cannot disclose another member's
        memories. Historical messages and personal memories keep their original
        member IDs. A registration reset also stops personal memories so that a
        new request reusing the same fixed member ID cannot revive old context.
        Corrections keep their existing behavior unless stop_personal is true.
        A later admitted message establishes the current identity.
        """
        user_id = _bounded(user_id, 'Tài khoản Zalo')
        old_member_id = _bounded(old_member_id, 'Mã thành viên')
        actor = _bounded(actor, 'Người thực hiện')
        if type(stop_personal) is not bool:
            raise ConversationError('Lựa chọn ngừng ghi nhớ chưa hợp lệ.')
        if ':' in user_id:
            raise ConversationError('Tài khoản Zalo không hợp lệ.')
        changed_sessions, stopped_memories = 0, 0
        with self._write():
            sessions = self.db.execute('SELECT * FROM conversation_sessions WHERE user_id=?', (user_id,)).fetchall()
            # A third identity is not evidence that it belongs to this repair.
            # Validate all sessions before changing any of them.
            if any(row['member_id'] not in (None, old_member_id) for row in sessions):
                raise ConversationConflict('Cuộc trò chuyện còn gắn với danh tính khác; cần kiểm tra trước khi sửa đăng ký.')
            for row in sessions:
                key = row['session_key']
                memories = self.db.execute("SELECT * FROM conversation_memories WHERE scope='session' AND session_key=? "
                                           'AND active=1 AND (member_id IS NULL OR member_id=?)',
                                           (key, old_member_id)).fetchall()
                cutoff = self.db.execute('SELECT COALESCE(MAX(id),0) FROM conversation_messages WHERE session_key=?', (key,)).fetchone()[0]
                usage = self.db.execute('SELECT 1 FROM conversation_context_usage WHERE session_key=?', (key,)).fetchone()
                if (row['member_id'] is None and row['cutoff'] >= cutoff and not row['summary']
                        and row['summary_ids'] == '[]' and not usage and not memories):
                    continue
                for memory in memories:
                    self.db.execute('UPDATE conversation_memories SET active=0,revision=revision+1,updated=? WHERE id=?',
                                    (self.clock(), memory['id']))
                    after = dict(self.db.execute('SELECT * FROM conversation_memories WHERE id=?', (memory['id'],)).fetchone())
                    self._version(after, actor)
                    self._audit(actor, 'memory_stopped_registration_correction', member_id=memory['member_id'],
                                session_key=key, target_id=memory['id'],
                                details={'before': self._memory_snapshot(memory), 'after': self._memory_snapshot(after)})
                self.db.execute("UPDATE conversation_sessions SET member_id=NULL,cutoff=?,summary='',summary_ids='[]',"
                                'revision=revision+1,updated=? WHERE session_key=?', (max(row['cutoff'], cutoff), self.clock(), key))
                self.db.execute('DELETE FROM conversation_context_usage WHERE session_key=?', (key,))
                self._audit(actor, 'registration_context_detached', member_id=old_member_id, session_key=key,
                            details={'before_revision': row['revision'], 'after_revision': row['revision'] + 1,
                                     'cutoff': cutoff, 'stopped_memories': len(memories)})
                changed_sessions += 1
                stopped_memories += len(memories)
            if stop_personal:
                memories = self.db.execute("SELECT * FROM conversation_memories WHERE scope='personal' "
                                           'AND member_id=? AND active=1', (old_member_id,)).fetchall()
                for memory in memories:
                    self.db.execute('UPDATE conversation_memories SET active=0,revision=revision+1,updated=? WHERE id=?',
                                    (self.clock(), memory['id']))
                    after = dict(self.db.execute('SELECT * FROM conversation_memories WHERE id=?', (memory['id'],)).fetchone())
                    self._version(after, actor)
                    self._audit(actor, 'memory_stopped_registration_reset', member_id=old_member_id,
                                session_key=memory['session_key'], target_id=memory['id'],
                                details={'before': self._memory_snapshot(memory), 'after': self._memory_snapshot(after)})
                stopped_memories += len(memories)
        return {'sessions': changed_sessions, 'stopped_memories': stopped_memories}

    def _current_revision(self, key, expected_revision):
        row = self.db.execute('SELECT * FROM conversation_sessions WHERE session_key=?', (key,)).fetchone()
        if not row:
            raise ConversationError('Không tìm thấy cuộc trò chuyện.')
        if type(expected_revision) is not int or row['revision'] != expected_revision:
            raise ConversationConflict('Ngữ cảnh đã thay đổi; tải lại trước khi lưu.')
        return row

    def reset_context(self, session_key, actor, expected_revision):
        key, actor = _session(session_key), _bounded(actor, 'Người thực hiện')
        with self._write():
            row = self._current_revision(key, expected_revision)
            cutoff = self.db.execute('SELECT COALESCE(MAX(id),0) FROM conversation_messages WHERE session_key=?', (key,)).fetchone()[0]
            self.db.execute("UPDATE conversation_sessions SET cutoff=?,summary='',summary_ids='[]',revision=revision+1 WHERE session_key=?", (cutoff, key))
            self._audit(actor, 'context_reset', member_id=row['member_id'], session_key=key, details={'before_revision': row['revision'], 'after_revision': row['revision'] + 1, 'cutoff': cutoff})
        return self.context(key)

    def rebuild_context(self, session_key, actor, expected_revision):
        key, actor = _session(session_key), _bounded(actor, 'Người thực hiện')
        with self._write():
            self._purge()
            row = self._current_revision(key, expected_revision)
            # Honor reset cutoff; rebuilding never resurrects cleared context.
            turns = self.db.execute('''SELECT * FROM conversation_messages WHERE session_key=? AND eligible=1 AND id>? AND member_id IS ?
                                    AND incoming_key IN (SELECT incoming_key FROM conversation_messages WHERE session_key=? AND direction='incoming' AND id>?)
                                    ORDER BY id DESC LIMIT 12''', (key, row['cutoff'], row['member_id'], key, row['cutoff'])).fetchall()
            selected = []
            for turn in turns:  # SQL gives newest first; preserve recent sources.
                line = ('Thành viên: ' if turn['direction'] == 'incoming' else 'Bot: ') + turn['body'][:240]
                trial = '\n'.join(item[1] for item in reversed(selected + [(turn['id'], line)]))
                if self._serialized_size(trial) <= self.MAX_CONTEXT_SUMMARY_SERIALIZED:
                    selected.append((turn['id'], line))
            selected.reverse()
            summary = '\n'.join(item[1] for item in selected)
            ids = [item[0] for item in selected]
            self.db.execute('UPDATE conversation_sessions SET summary=?,summary_ids=?,revision=revision+1 WHERE session_key=?', (summary, json.dumps(ids), key))
            self._audit(actor, 'context_rebuilt', member_id=row['member_id'], session_key=key, details={'before_revision': row['revision'], 'after_revision': row['revision'] + 1, 'message_ids': ids})
        return self.context(key)

    def list_memories(self, member_id=None, page=1, limit=30):
        where, args = [], []
        if member_id is not None:
            where, args = ['member_id=?'], [_bounded(member_id, 'Mã thành viên')]
        with self._write():
            self._purge()
            return self._paged('conversation_memories', where, args, page, limit, 'updated DESC,id')

    def save_memory(self, data, actor, expected_revision=None):
        actor = _bounded(actor, 'Người thực hiện')
        if not isinstance(data, dict) or set(data) - {'id', 'content', 'member_id', 'scope', 'session_key', 'group_id', 'source_message_id', 'active', 'expires', 'confirm_shared', 'confirm_share'}:
            raise ConversationError('Trường ghi nhớ không hợp lệ.')
        raw_content = data.get('content')
        if not isinstance(raw_content, str) or not raw_content.strip() or len(raw_content) > 2000 or any(ord(c) < 32 and c not in '\n\r\t' for c in raw_content):
            raise ConversationError('Nội dung ghi nhớ không hợp lệ.')
        content = redact(raw_content.strip())
        member = _bounded(data.get('member_id'), 'Mã thành viên', optional=True)
        scope = data.get('scope', 'personal')
        session_key = _session(data['session_key']) if data.get('session_key') else None
        group = _bounded(data.get('group_id'), 'Mã nhóm', optional=True)
        active, expires = data.get('active', True), data.get('expires')
        sharing_confirmed = data.get('confirm_shared') is True or data.get('confirm_share') is True
        if type(active) is not bool:
            raise ConversationError('Trạng thái ghi nhớ không hợp lệ.')
        if expires is not None:
            expires = _number(expires, 'Hạn sử dụng')
            if expires <= self.clock():
                raise ConversationError('Hạn sử dụng phải ở tương lai.')
        if scope == 'personal':
            if not member or session_key or group:
                raise ConversationError('Ghi nhớ cá nhân cần mã thành viên và không được gắn nhóm hoặc phiên.')
        elif scope == 'session':
            if not session_key or group:
                raise ConversationError('Ghi nhớ cuộc trò chuyện cần đúng mã phiên.')
        elif scope == 'group':
            if not group or session_key or not sharing_confirmed:
                raise ConversationError('Cần xác nhận chia sẻ ghi nhớ với nhóm được chọn.')
        else:
            raise ConversationError('Phạm vi ghi nhớ không hợp lệ.')
        source_id = data.get('source_message_id')
        if source_id is not None and (type(source_id) is not int or source_id < 1):
            raise ConversationError('Tin nhắn nguồn không hợp lệ.')
        with self._write():
            self._purge()
            shared_scope = scope == 'group'
            if session_key:
                session = self.db.execute('SELECT * FROM conversation_sessions WHERE session_key=?', (session_key,)).fetchone()
                if not session or (member is not None and member != session['member_id']):
                    raise ConversationError('Thành viên không khớp cuộc trò chuyện.')
                if session['chat_type'] == 'GROUP':
                    shared_scope = True
                    if not sharing_confirmed:
                        raise ConversationError('Ngữ cảnh phiên nhóm có thể xuất hiện trong câu trả lời chung; cần xác nhận chia sẻ.')
            if source_id is not None:
                source = self.db.execute('SELECT * FROM conversation_messages WHERE id=?', (source_id,)).fetchone()
                if not source:
                    raise ConversationError('Tin nhắn nguồn không còn được lưu.')
                if scope == 'personal' and source['member_id'] != member:
                    raise ConversationError('Tin nhắn nguồn không thuộc thành viên này.')
                if scope == 'session' and source['session_key'] != session_key:
                    raise ConversationError('Tin nhắn nguồn không thuộc cuộc trò chuyện này.')
                if scope == 'group' and member is not None and source['member_id'] != member:
                    raise ConversationError('Chủ thể ghi nhớ không khớp tin nhắn nguồn.')
            memory_id = _bounded(data.get('id'), 'Mã ghi nhớ', optional=True)
            before = self.db.execute('SELECT * FROM conversation_memories WHERE id=?', (memory_id,)).fetchone() if memory_id else None
            if memory_id and not before:
                raise ConversationError('Không tìm thấy ghi nhớ.')
            if before and (type(expected_revision) is not int or before['revision'] != expected_revision):
                raise ConversationConflict('Ghi nhớ đã thay đổi; tải lại trước khi lưu.')
            if before and before['source_expired']:
                raise ConversationError('Nguồn đã hết hạn; không thể khôi phục ghi nhớ cũ.')
            if not before and expected_revision is not None:
                raise ConversationConflict('Không thể cập nhật ghi nhớ chưa tồn tại.')
            memory_id = memory_id or 'memory-' + uuid.uuid4().hex
            revision = before['revision'] + 1 if before else 1
            values = (content, member, scope, session_key, group, source_id, int(active), expires, revision, self.clock(), memory_id)
            if before:
                self.db.execute('UPDATE conversation_memories SET content=?,member_id=?,scope=?,session_key=?,group_id=?,source_message_id=?,active=?,expires=?,revision=?,updated=? WHERE id=?', values)
            else:
                self.db.execute('INSERT INTO conversation_memories(content,member_id,scope,session_key,group_id,source_message_id,active,expires,revision,updated,id,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (*values, self.clock()))
            after = dict(self.db.execute('SELECT * FROM conversation_memories WHERE id=?', (memory_id,)).fetchone())
            self._version(after, actor)
            self._audit(actor, 'memory_updated' if before else 'memory_created', member_id=member, session_key=session_key, target_id=memory_id,
                        details={'before': self._memory_snapshot(before) if before else None, 'after': self._memory_snapshot(after), 'shared_confirmed': shared_scope})
            return after

    def stop_memory(self, memory_id, actor, expected_revision):
        actor, memory_id = _bounded(actor, 'Người thực hiện'), _bounded(memory_id, 'Mã ghi nhớ')
        with self._write():
            row = self.db.execute('SELECT * FROM conversation_memories WHERE id=?', (memory_id,)).fetchone()
            if not row:
                raise ConversationError('Không tìm thấy ghi nhớ.')
            if type(expected_revision) is not int or row['revision'] != expected_revision:
                raise ConversationConflict('Ghi nhớ đã thay đổi; tải lại trước khi lưu.')
            self.db.execute('UPDATE conversation_memories SET active=0,revision=revision+1,updated=? WHERE id=?', (self.clock(), memory_id))
            after = dict(self.db.execute('SELECT * FROM conversation_memories WHERE id=?', (memory_id,)).fetchone())
            self._version(after, actor)
            self._audit(actor, 'memory_stopped', member_id=row['member_id'], session_key=row['session_key'], target_id=memory_id,
                        details={'before': self._memory_snapshot(row), 'after': self._memory_snapshot(after)})
            return after

    def audit(self, member_id=None, session_key=None, page=1, limit=30):
        where, args = [], []
        for column, value in (('member_id', member_id), ('session_key', session_key)):
            if value is not None:
                where.append(column + '=?')
                args.append(_session(value) if column == 'session_key' else _bounded(value, 'Bộ lọc'))
        with self.lock:
            result = self._paged('conversation_audit', where, args, page, limit)
            for row in result['items']:
                row['details'] = json.loads(row['details'])
            return result

    def notice_needed(self, message):
        key = self._message_scope(message)
        with self.lock:
            return self.db.execute('SELECT 1 FROM conversation_notices WHERE session_key=?', (key,)).fetchone() is None

    def mark_notice(self, message):
        key = self._message_scope(message)
        with self._write():
            self.db.execute('INSERT OR IGNORE INTO conversation_notices(session_key,delivered) VALUES(?,?)', (key, self.clock()))
