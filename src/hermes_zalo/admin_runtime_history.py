"""Bounded, read-only Hermes history. The HTTP caller enforces conversations permission.

Only user/assistant text is returned, never system prompts, reasoning, tool calls,
tool results or attachment payloads. No fallback to the operator's personal home.
"""
from contextlib import closing
import json
import sqlite3

from .admin_assistant import _connect_ro, _columns, _Unavailable, redact
from .admin_auth import AdminError
from .conversations import redact as redact_content


def _text(value):
    if not isinstance(value, str):
        return ''
    try:
        blocks = json.loads(value)
    except ValueError:
        if value.lstrip().startswith(('[', '{')):
            return '[Structured content hidden]'
        blocks = None
    if isinstance(blocks, list):
        value = '\n'.join(str(b.get('text', '')) for b in blocks
                          if isinstance(b, dict) and b.get('type') == 'text')
    elif isinstance(blocks, dict):
        value = '[Media content hidden]'
    original = value[:8000]
    redacted = redact_content(original)
    if redacted != original:
        # The shared archive keeps its historical Vietnamese masking labels.
        # Translate only newly redacted output, leaving stored/user-authored
        # text untouched when no masking was applied.
        for old, new in (
            ('[đã ẩn thông tin đăng ký]', '[registration details hidden]'),
            ('[đã ẩn khóa bí mật]', '[private key hidden]'),
            ('[đã ẩn số điện thoại]', '[phone number hidden]'),
            ('[đã ẩn khóa]', '[key hidden]'),
            ('[đã ẩn]', '[hidden]'),
        ):
            redacted = redacted.replace(old, new)
    return redacted


class RuntimeHistory:
    def __init__(self, home):
        self.home = home

    def read(self, *, page=1, session_id=None):
        if type(page) is not int or not 1 <= page <= 1000000:
            raise AdminError('Invalid page number.')
        if session_id is not None and (not isinstance(session_id, str) or
                not 1 <= len(session_id) <= 200 or any(ord(c) < 32 for c in session_id)):
            raise AdminError('Invalid session ID.')
        limit = 20 if session_id is None else 30
        try:
            with closing(_connect_ro(self.home / 'state.db')) as db:
                if not {'id', 'source', 'started_at'} <= _columns(db, 'sessions'):
                    raise _Unavailable('unsupported session schema')
                return self._sessions(db, page, limit) if session_id is None else self._messages(db, session_id, page, limit)
        except (OSError, sqlite3.Error, _Unavailable):
            return dict(available=False, source='hermes-runtime', items=[], total=None, page=page,
                        limit=limit, message='Hermes history is unavailable or its schema is not supported.')

    @staticmethod
    def _visible(columns):
        return "hidden=0" if 'hidden' in columns else '1=1'

    def _sessions(self, db, page, limit):
        columns = _columns(db, 'sessions')
        where = self._visible(columns)
        total = db.execute('SELECT COUNT(*) FROM sessions WHERE ' + where).fetchone()[0]
        # Column expressions come only from this fixed allowlist, never the request.
        fields = ['id', 'source', 'started_at']
        optional = ['title', 'ended_at', 'message_count', 'last_activity_at', 'chat_type']
        fields += [name if name in columns else 'NULL AS ' + name for name in optional]
        cursor = db.execute('SELECT ' + ','.join(fields) + ' FROM sessions WHERE ' + where +
                            ' ORDER BY started_at DESC,id LIMIT ? OFFSET ?', (limit, (page - 1) * limit))
        items = []
        for sid, source, started, title, ended, count, updated, chat_type in cursor:
            items.append(dict(runtime_session_id=sid, session_key=sid,
                title=redact(title, 100) or 'Session ' + (redact(source, 30) or 'Hermes'),
                source=redact(source, 30), chat_type=chat_type,
                message_count=count, updated_at=updated or ended or started))
        return dict(available=True, source='hermes-runtime', items=items, total=total, page=page, limit=limit)

    def _messages(self, db, session_id, page, limit):
        where = self._visible(_columns(db, 'sessions'))
        if not db.execute('SELECT 1 FROM sessions WHERE id=? AND ' + where, (session_id,)).fetchone():
            raise AdminError('Session not found.', 404)
        columns = _columns(db, 'messages')
        if not {'id', 'session_id', 'role', 'content', 'timestamp'} <= columns:
            raise _Unavailable('unsupported message schema')
        conditions = "session_id=? AND role IN ('user','assistant')"
        if '_compressed_summary' in columns:
            conditions += ' AND COALESCE(_compressed_summary,0)=0'
        total = db.execute('SELECT COUNT(*) FROM messages WHERE ' + conditions, (session_id,)).fetchone()[0]
        rows = db.execute('SELECT id,role,substr(content,1,16000),timestamp FROM messages WHERE ' +
                          conditions + ' ORDER BY timestamp DESC,id DESC LIMIT ? OFFSET ?',
                          (session_id, limit, (page - 1) * limit))
        items = [dict(id=id_, role=role, content=_text(content), created_at=at) for id_, role, content, at in rows]
        return dict(available=True, source='hermes-runtime', items=items, total=total, page=page, limit=limit)
