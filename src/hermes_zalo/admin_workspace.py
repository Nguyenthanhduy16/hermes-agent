"""Workspace files, local task board and an explicitly configured chat gateway.

No process execution, host filesystem browser or implicit gateway discovery.
The HTTP layer must enforce owner permissions and CSRF for every mutation.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import threading
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

import httpx

from .admin_assistant import _connect_ro, _Unavailable, redact
from .admin_auth import AdminError
from .conversations import redact as redact_content


COLUMNS = ('todo', 'in_progress', 'review', 'done')
PREVIEW_SUFFIXES = frozenset(('.md', '.txt', '.csv', '.tsv', '.json', '.log', '.py', '.yaml', '.yml', '.toml'))
PRIVATE_NAME = re.compile(r'(?i)(?:secret|credential|password|private[-_]?key|token|\.env)')
MAX_PREVIEW = 32768
MAX_TASKS = 500


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def safe_text(text):
    value = redact_content(text)
    for old, new in (('[đã ẩn thông tin đăng ký]', '[registration details hidden]'),
                     ('[đã ẩn khóa bí mật]', '[private key hidden]'),
                     ('[đã ẩn khóa]', '[key hidden]'), ('[đã ẩn số điện thoại]', '[phone hidden]'),
                     ('[đã ẩn]', '[redacted]')):
        value = value.replace(old, new)
    return value


def task_text(value, name, maximum, *, optional=False):
    if not isinstance(value, str) or len(value) > maximum or (not value.strip() and not optional):
        raise AdminError(f'{name} must be text of at most {maximum} characters.')
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise AdminError(f'{name} contains unsupported control characters.')
    return safe_text(value.strip())


class WorkspaceAdmin:
    def __init__(self, home, *, mode='local', environ=None, transport=None):
        self.home, self.mode = Path(home), mode
        self.environ = os.environ if environ is None else environ
        self.workspace = Path(self.environ.get('HERMES_CONSOLE_WORKSPACE') or self.home / 'workspace')
        self.transport = transport
        self._lock = threading.RLock()
        self._demo_tasks = {
            'demo-research': dict(id='demo-research', title='Prepare a research brief',
                description='Compare sources and summarize the findings.', status='in_progress', revision=1,
                updated_at='2026-10-31T08:00:00+00:00'),
            'demo-chart': dict(id='demo-chart', title='Review the sales chart',
                description='Check totals before sharing the report.', status='review', revision=1,
                updated_at='2026-10-31T08:00:00+00:00'),
        }

    # ---- Files: a single configured artifact/workspace directory ---------

    @staticmethod
    def _parts(path):
        if not isinstance(path, str) or len(path) > 512 or '\\' in path or '\0' in path:
            raise AdminError('Invalid workspace path.')
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or any(p.startswith('.') or ':' in p or PRIVATE_NAME.search(p) for p in parsed.parts):
            raise AdminError('This workspace path is not allowed.', 403)
        return parsed.parts

    def files(self, path=''):
        parts = self._parts(path)
        relative = '/'.join(parts)
        if self.mode == 'demo':
            fixtures = {'sales.csv': 'month,revenue\nJan,1200\nFeb,1450\nMar,1800\n',
                        'research-brief.md': '# Research brief\nDemo artifact. No external data was fetched.\n'}
            if not relative:
                items = [dict(name=name, path=name, kind='file', size=len(body.encode()),
                              modified='2026-10-31T08:00:00+00:00') for name, body in fixtures.items()]
                return dict(available=True, source='demo', path='', parent=None, items=items, updated_at=now())
            if relative not in fixtures:
                raise AdminError('Workspace file not found.', 404)
            return dict(available=True, source='demo', path=relative, parent='', items=[],
                        content=fixtures[relative], truncated=False, updated_at=now())
        if not self.workspace.is_dir() or self.workspace.is_symlink():
            return dict(available=False, source='workspace', path=relative, parent=None, items=[],
                        message='No workspace is connected. Set HERMES_CONSOLE_WORKSPACE on the server.', updated_at=now())
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        directory_flags = flags | getattr(os, 'O_DIRECTORY', 0)
        # On platforms without openat, refuse rather than weakening path isolation.
        if os.open not in os.supports_dir_fd:
            return dict(available=False, source='workspace', path=relative, parent=None, items=[],
                        message='Workspace browsing is not supported on this server platform.', updated_at=now())
        fd = None
        try:
            fd = os.open(self.workspace, directory_flags)
            for index, part in enumerate(parts):
                next_fd = os.open(part, flags | (getattr(os, 'O_DIRECTORY', 0) if index < len(parts) - 1 else 0), dir_fd=fd)
                os.close(fd)
                fd = next_fd
            info = os.fstat(fd)
            parent = '/'.join(parts[:-1]) if parts else None
            if stat.S_ISDIR(info.st_mode):
                items, scanned = [], 0
                with os.scandir(fd) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > 2000 or len(items) >= 200:
                            break
                        if entry.name.startswith('.') or PRIVATE_NAME.search(entry.name) or entry.is_symlink():
                            continue
                        details = entry.stat(follow_symlinks=False)
                        if not (stat.S_ISDIR(details.st_mode) or stat.S_ISREG(details.st_mode)):
                            continue
                        if Path(entry.name).suffix.lower() in ('.key', '.pem', '.p12', '.pfx', '.db', '.sqlite', '.sqlite3'):
                            continue
                        items.append(dict(name=entry.name, path='/'.join((*parts, entry.name)),
                            kind='directory' if stat.S_ISDIR(details.st_mode) else 'file', size=details.st_size,
                            modified=datetime.fromtimestamp(details.st_mtime, timezone.utc).isoformat(timespec='seconds')))
                items.sort(key=lambda item: (item['kind'] != 'directory', item['name'].casefold()))
                return dict(available=True, source='workspace', path=relative, parent=parent, items=items,
                            truncated=scanned > 2000 or len(items) >= 200, updated_at=now())
            if not stat.S_ISREG(info.st_mode) or Path(relative).suffix.lower() not in PREVIEW_SUFFIXES:
                raise AdminError('Only supported text artifacts can be previewed.', 415)
            data = os.read(fd, MAX_PREVIEW + 1)
            if b'\0' in data:
                raise AdminError('Binary artifacts cannot be previewed.', 415)
            text = data[:MAX_PREVIEW].decode('utf-8', 'replace')
            content = '\n'.join(redact(line, 1000) or '' for line in text.splitlines()[:200])
            return dict(available=True, source='workspace', path=relative, parent=parent, items=[], content=content,
                        truncated=len(data) > MAX_PREVIEW or len(text.splitlines()) > 200, updated_at=now())
        except FileNotFoundError:
            raise AdminError('Workspace item not found.', 404) from None
        except OSError:
            raise AdminError('This workspace item cannot be accessed safely.', 403) from None
        finally:
            if fd is not None:
                os.close(fd)

    # ---- Kanban: explicit local planning, independent of agent execution --

    @property
    def task_path(self):
        return self.home / 'console' / 'tasks.sqlite3'

    def _task_db(self):
        if self.task_path.is_symlink() or self.task_path.parent.is_symlink():
            raise AdminError('The task store is not a regular local database.', 503)
        self.task_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.task_path, timeout=3)
        db.execute('''CREATE TABLE IF NOT EXISTS console_tasks (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
            status TEXT NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL)''')
        return db

    def kanban(self):
        with self._lock:
            if self.mode == 'demo':
                items = [dict(item) for item in self._demo_tasks.values()]
            elif not self.task_path.exists():
                items = []
            else:
                if self.task_path.is_symlink() or self.task_path.parent.is_symlink():
                    raise AdminError('The task store is not a regular local database.', 503)
                try:
                    with closing(_connect_ro(self.task_path)) as db:
                        rows = db.execute('SELECT id,title,description,status,revision,updated_at '
                                          'FROM console_tasks ORDER BY updated_at DESC,id LIMIT ?', (MAX_TASKS,))
                        items = [dict(zip(('id', 'title', 'description', 'status', 'revision', 'updated_at'), row)) for row in rows]
                except (OSError, sqlite3.Error, _Unavailable):
                    raise AdminError('The local task board is unavailable.', 503) from None
            return dict(available=True, source='demo' if self.mode == 'demo' else 'console-tasks',
                columns=list(COLUMNS), items=items, updated_at=now(),
                message='A local planning board. Moving a card does not start or stop an agent task.')

    def task_action(self, data, actor, *, authorize=None):
        if not isinstance(data, dict) or set(data) - {'action', 'id', 'title', 'description', 'status', 'expected_revision', 'confirm'}:
            raise AdminError('Unsupported task fields.')
        action = data.get('action')
        if action not in ('create', 'update', 'delete') or data.get('confirm') is not True:
            raise AdminError('Confirm a valid task action before submitting.')
        if action != 'create' and (not isinstance(data.get('id'), str) or len(data['id']) > 80 or
                type(data.get('expected_revision')) is not int):
            raise AdminError('A task ID and expected revision are required.')
        if action == 'create' and ('id' in data or 'expected_revision' in data):
            raise AdminError('New task identifiers are assigned by the server.')
        changes = {}
        for key, maximum in (('title', 160), ('description', 2000)):
            if key in data or action == 'create':
                changes[key] = task_text(data.get(key, ''), key.title(), maximum, optional=key == 'description')
        if 'status' in data or action == 'create':
            status = data.get('status', 'todo')
            if status not in COLUMNS:
                raise AdminError('Invalid board column.')
            changes['status'] = status
        if action == 'delete' and changes:
            raise AdminError('Deleting a task cannot also change its fields.')
        if action == 'update' and not changes:
            raise AdminError('Choose at least one task field to update.')
        with self._lock:
            if authorize is not None:
                authorize()
            if self.mode == 'demo':
                tasks = self._demo_tasks
                if action == 'create' and len(tasks) >= MAX_TASKS:
                    raise AdminError('The board has reached its task limit.', 409)
                old = tasks.get(data.get('id'))
                if action != 'create' and (old is None or old['revision'] != data['expected_revision']):
                    raise AdminError('This card changed. Refresh the board before trying again.', 409)
                if action == 'delete':
                    del tasks[old['id']]
                    return {'ok': True, 'id': old['id']}
                item = dict(old or {}, **changes, id=old['id'] if old else uuid.uuid4().hex,
                            revision=old['revision'] + 1 if old else 1, updated_at=now())
                tasks[item['id']] = item
                return dict(ok=True, item=dict(item))
            with closing(self._task_db()) as db:
                try:
                    db.execute('BEGIN IMMEDIATE')
                    if action == 'create':
                        if db.execute('SELECT COUNT(*) FROM console_tasks').fetchone()[0] >= MAX_TASKS:
                            raise AdminError('The board has reached its task limit.', 409)
                        item = dict(changes, id=uuid.uuid4().hex, revision=1, updated_at=now())
                        db.execute('INSERT INTO console_tasks VALUES (:id,:title,:description,:status,:revision,:updated_at)', item)
                    else:
                        row = db.execute('SELECT id,title,description,status,revision,updated_at FROM console_tasks WHERE id=?',
                                         (data['id'],)).fetchone()
                        if row is None or row[4] != data['expected_revision']:
                            raise AdminError('This card changed. Refresh the board before trying again.', 409)
                        item = dict(zip(('id', 'title', 'description', 'status', 'revision', 'updated_at'), row))
                        if action == 'delete':
                            db.execute('DELETE FROM console_tasks WHERE id=? AND revision=?', (data['id'], data['expected_revision']))
                        else:
                            item.update(changes, revision=item['revision'] + 1, updated_at=now())
                            db.execute('UPDATE console_tasks SET title=:title,description=:description,status=:status,'
                                       'revision=:revision,updated_at=:updated_at WHERE id=:id', item)
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            return dict(ok=True, id=item['id']) if action == 'delete' else dict(ok=True, item=item)

    # ---- Chat: a configured server-side Hermes API connection -------------

    def _chat_configuration(self):
        raw = self.environ.get('HERMES_CONSOLE_CHAT_API_URL', '').strip()
        key = self.environ.get('HERMES_CONSOLE_CHAT_API_KEY') or self.environ.get('API_SERVER_KEY') or ''
        if not raw or not key:
            raise AdminError('Connect a Hermes API endpoint and key on the server to enable web chat.', 503)
        try:
            parsed = urlsplit(raw)
            local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
            if parsed.scheme not in ('https', 'http') or parsed.scheme == 'http' and not local:
                raise ValueError()
            if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError()
            if parsed.path.rstrip('/') not in ('', '/v1', '/v1/chat/completions'):
                raise ValueError()
            parsed.port  # validate, never return the endpoint to the browser
        except ValueError:
            raise AdminError('The configured chat endpoint is invalid. Check the server configuration.', 503) from None
        if '\n' in key or '\r' in key or len(key) > 4096:
            raise AdminError('The configured gateway key is invalid.', 503)
        endpoint = urlunsplit((parsed.scheme, parsed.netloc, '/v1/chat/completions', '', ''))
        return endpoint, key, self.environ.get('HERMES_CONSOLE_CHAT_MODEL') or 'hermes'

    def chat_status(self):
        if self.mode == 'demo':
            return dict(available=True, configured=True, enabled=True, source='demo', items=[], updated_at=now(),
                        message='Demo chat returns a clearly labeled sample response and never calls a model.')
        try:
            self._chat_configuration()
        except AdminError as error:
            return dict(available=False, configured=False, enabled=False, source='gateway-api', items=[],
                        message=str(error), updated_at=now())
        return dict(available=True, configured=True, enabled=True, source='gateway-api', items=[], updated_at=now(),
                    message='Connected configuration. The gateway is contacted only when you send a message.')

    async def chat(self, data):
        if not isinstance(data, dict) or set(data) - {'message', 'history'}:
            raise AdminError('Unsupported chat fields.')
        message = task_text(data.get('message'), 'Message', 4000)
        history = data.get('history', [])
        if not isinstance(history, list) or len(history) > 12:
            raise AdminError('Chat history is limited to 12 previous messages.')
        messages = []
        for entry in history:
            if not isinstance(entry, dict) or set(entry) != {'role', 'content'} or entry['role'] not in ('user', 'assistant'):
                raise AdminError('Only user and assistant chat history is accepted.')
            messages.append(dict(role=entry['role'], content=task_text(entry['content'], 'History message', 4000)))
        if sum(len(item['content']) for item in messages) > 24000:
            raise AdminError('Chat history is too long. Start a new chat.')
        messages.append(dict(role='user', content=message))
        if self.mode == 'demo':
            return dict(reply='Demo response: your request was received. No model or external tools were called.',
                        model='demo', source='demo')
        endpoint, key, model = self._chat_configuration()
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                async with client.stream('POST', endpoint, headers={'Authorization': 'Bearer ' + key},
                        json={'model': model, 'messages': messages, 'stream': False, 'max_tokens': 1600}) as response:
                    if response.status_code != 200:
                        raise AdminError('The Hermes gateway could not complete this message. No retry was sent.',
                                         429 if response.status_code == 429 else 502)
                    chunks, length = [], 0
                    async for chunk in response.aiter_bytes():
                        length += len(chunk)
                        if length > 262144:
                            raise AdminError('The gateway response exceeded the web chat limit.', 502)
                        chunks.append(chunk)
            payload = json.loads(b''.join(chunks))
            reply = payload['choices'][0]['message']['content']
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError()
            return dict(reply=safe_text(reply[:16000]), model=redact(payload.get('model') or model, 120), source='gateway-api')
        except AdminError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            raise AdminError('The Hermes gateway is unavailable or returned an invalid response. No retry was sent.', 502) from None
