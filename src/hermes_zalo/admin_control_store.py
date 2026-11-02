"""Home-scoped, revision-checked persistence for Console management actions.

Reads never create files. Writes use same-directory atomic replacement, 0600
permissions and cross-process locks. Demo uses only an in-memory file map.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import threading
import time

import yaml

from .admin_auth import AdminError

try:
    import fcntl
except ImportError:  # Windows still has process-local serialization.
    fcntl = None

LIMIT = 2 * 1024 * 1024
ENV_NAME = re.compile(r'[A-Z][A-Z0-9_]{0,99}')
ENV_LINE = re.compile(r'^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
_LOCKS = {}
_LOCK_GUARD = threading.Lock()


def _revision(raw):
    return hashlib.sha256(b'missing' if raw is None else b'file\0' + raw).hexdigest()


def _tree(value, depth=0, active=None, budget=None):
    active = set() if active is None else active
    budget = [20000] if budget is None else budget
    budget[0] -= 1
    if depth > 30 or budget[0] < 0:
        raise AdminError('The configuration is too complex to edit safely.', 503)
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if not isinstance(value, (dict, list)) or id(value) in active:
        raise AdminError('The configuration contains an unsupported value.', 503)
    active.add(id(value))
    try:
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise AdminError('Configuration keys must be text.', 503)
                _tree(item, depth + 1, active, budget)
        else:
            for item in value:
                _tree(item, depth + 1, active, budget)
    finally:
        active.remove(id(value))


class ControlStore:
    def __init__(self, home, *, mode='local'):
        if home is None:
            raise ValueError('An explicit bot home is required.')
        self.home = Path(home).absolute()
        self.mode = mode
        self._demo_files = {}
        with _LOCK_GUARD:
            self._thread_lock = _LOCKS.setdefault(str(self.home), threading.RLock())

    @staticmethod
    def _parts(relative):
        if not isinstance(relative, str) or not relative or len(relative) > 512 or '\\' in relative or '\0' in relative:
            raise AdminError('Invalid management data path.')
        path = PurePosixPath(relative)
        if path.is_absolute() or any(p in ('.', '..') or ':' in p for p in relative.split('/')):
            raise AdminError('Invalid management data path.')
        return path.parts

    @contextmanager
    def _directory(self, relative, *, create=False):
        parts = self._parts(relative)
        flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = None
        try:
            fd = os.open(self.home.anchor, flags)
            for part in (*self.home.parts[1:], *parts[:-1]):
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            yield fd, parts[-1]
        finally:
            if fd is not None:
                os.close(fd)

    def _raw(self, relative):
        self._parts(relative)
        if self.mode == 'demo':
            return self._demo_files.get(relative)
        try:
            with self._directory(relative) as (directory, name):
                fd = os.open(name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
                        raise AdminError('The management data is not a supported regular file.', 503)
                    chunks, length = [], 0
                    while True:
                        chunk = os.read(fd, min(65536, LIMIT + 1 - length))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        length += len(chunk)
                        if length > LIMIT:
                            raise AdminError('The management data exceeds its size limit.', 503)
                    return b''.join(chunks)
                finally:
                    os.close(fd)
        except FileNotFoundError:
            return None
        except OSError:
            raise AdminError('The management data cannot be accessed safely.', 503) from None

    def read_text(self, relative, default=''):
        with self._thread_lock:
            raw = self._raw(relative)
            try:
                return (default if raw is None else raw.decode('utf-8')), _revision(raw)
            except UnicodeError:
                raise AdminError('The management data must use UTF-8 text.', 503) from None

    def read_yaml(self, relative='config.yaml', default=None):
        text, revision = self.read_text(relative)
        try:
            value = yaml.safe_load(text) if text.strip() else ({} if default is None else default)
        except yaml.YAMLError:
            raise AdminError('The YAML configuration is invalid. Repair it before editing.', 503) from None
        if not isinstance(value, dict):
            raise AdminError('The YAML configuration must contain a mapping.', 503)
        _tree(value)
        return value, revision

    def read_json(self, relative, default=None):
        text, revision = self.read_text(relative)
        try:
            value = json.loads(text) if text.strip() else default
        except ValueError:
            raise AdminError('The JSON data is invalid. Repair it before editing.', 503) from None
        _tree(value)
        return value, revision

    @contextmanager
    def _lock(self, relative):
        with self._thread_lock:
            if self.mode == 'demo':
                yield
                return
            if os.open not in os.supports_dir_fd or not hasattr(os, 'O_NOFOLLOW'):
                raise AdminError('Safe configuration editing is unavailable on this platform.', 503)
            fd = None
            try:
                with self._directory(relative, create=True) as (directory, name):
                    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0), 0o600, dir_fd=directory)
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise AdminError('The management lock is not a regular file.', 503)
                if fcntl is not None:
                    deadline = time.monotonic() + 3
                    while True:
                        try:
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= deadline:
                                raise AdminError('The data is busy. Refresh before trying again.', 503) from None
                            time.sleep(.02)
                yield
            except OSError:
                raise AdminError('The management store is unavailable.', 503) from None
            finally:
                if fd is not None:
                    os.close(fd)

    @staticmethod
    def _check(expected, actual):
        if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected):
            raise AdminError('A valid data revision is required.')
        if not secrets.compare_digest(expected, actual):
            raise AdminError('This configuration changed. Refresh before trying again.', 409)

    def _replace(self, relative, raw):
        if len(raw) > LIMIT:
            raise AdminError('The updated data exceeds its size limit.', 413)
        if self.mode == 'demo':
            self._demo_files[relative] = raw
            return
        with self._directory(relative, create=True) as (directory, name):
            temporary = '.console-' + secrets.token_hex(12)
            fd = None
            try:
                # Refuse an existing non-regular destination, including a late symlink.
                try:
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        raise AdminError('The destination is not a regular local file.', 503)
                except FileNotFoundError:
                    pass
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, 'wb') as stream:
                    fd = None
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                if fd is not None:
                    os.close(fd)
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass

    def _mutate(self, relative, expected, reader, encoder, callback, *, authorize=None, lock=None):
        if authorize is not None:
            authorize()
        # Reject stale requests before creating the home or a lock file.
        _, revision = reader()
        self._check(expected, revision)
        with self._lock(lock or '.console-control.lock'):
            value, revision = reader()
            self._check(expected, revision)
            if authorize is not None:
                authorize()
            changed = callback(value)
            raw = encoder(changed)
            self._check(expected, _revision(self._raw(relative)))
            if authorize is not None:
                authorize()
            self._replace(relative, raw)
            return _revision(raw)

    def write_text(self, relative, text, expected_revision, *, authorize=None):
        if not isinstance(text, str) or '\0' in text:
            raise AdminError('Management text must not contain null bytes.')
        return self._mutate(relative, expected_revision, lambda: self.read_text(relative),
                            lambda value: value.encode('utf-8'), lambda _: text, authorize=authorize)

    def create_tree(self, relative, files, expected_revision, *, authorize=None):
        """Publish a new small profile directory; never replace an existing tree."""
        self._parts(relative)
        self._check(expected_revision, _revision(None))
        if not isinstance(files, dict) or not files or len(files) > 10:
            raise AdminError('Invalid profile files.')
        for name, value in files.items():
            if len(self._parts(name)) != 1 or not isinstance(value, str) or len(value.encode('utf-8')) > LIMIT:
                raise AdminError('Invalid profile files.')
        if authorize:
            authorize()
        with self._lock('.console-control.lock'):
            if self.mode == 'demo':
                if any(path.startswith(relative + '/') for path in self._demo_files):
                    raise AdminError('This profile already exists. Refresh before trying again.', 409)
                if authorize:
                    authorize()
                self._demo_files.update({relative + '/' + name: value.encode('utf-8') for name, value in files.items()})
                return _revision(files.get('config.yaml', '').encode('utf-8'))
            with self._directory(relative, create=True) as (parent, name):
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise AdminError('This profile already exists. Refresh before trying again.', 409)
                temporary = '.profile-' + secrets.token_hex(12)
                os.mkdir(temporary, 0o700, dir_fd=parent)
                directory = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                published = False
                try:
                    for filename, value in files.items():
                        fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                        with os.fdopen(fd, 'wb') as stream:
                            stream.write(value.encode('utf-8'))
                            stream.flush()
                            os.fsync(stream.fileno())
                    os.fsync(directory)
                    if authorize:
                        authorize()
                    # A directory created outside the Console during staging
                    # is a conflict, including a pre-existing empty directory.
                    try:
                        os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise AdminError('This profile already exists. Refresh before trying again.', 409)
                    os.rename(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                    published = True
                    os.fsync(parent)
                finally:
                    if not published:
                        for filename in os.listdir(directory):
                            os.unlink(filename, dir_fd=directory)
                    os.close(directory)
                    if not published:
                        os.rmdir(temporary, dir_fd=parent)
                return _revision(files.get('config.yaml', '').encode('utf-8'))

    def mutate_yaml(self, relative, expected_revision, callback, *, authorize=None):
        def encode(value):
            _tree(value)
            if not isinstance(value, dict):
                raise AdminError('Configuration must contain a mapping.')
            return yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode('utf-8')
        return self._mutate(relative, expected_revision, lambda: self.read_yaml(relative), encode, callback, authorize=authorize)

    def mutate_json(self, relative, expected_revision, callback, *, default=None, authorize=None, lock=None):
        def encode(value):
            _tree(value)
            return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        return self._mutate(relative, expected_revision, lambda: self.read_json(relative, default), encode,
                            callback, authorize=authorize, lock=lock)

    @staticmethod
    def _env_value(value):
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                return json.loads(value)
            except ValueError:
                return value[1:-1]
        if len(value) >= 2 and value[0] == value[-1] == "'":
            return value[1:-1].replace("\\'", "'").replace('\\\\', '\\')
        return value.split(' #', 1)[0].rstrip()

    def read_env(self):
        text, revision = self.read_text('.env')
        values = {}
        for line in text.splitlines():
            match = ENV_LINE.fullmatch(line.strip())
            if match:
                values[match[1]] = self._env_value(match[2])
        return values, revision

    def mutate_env(self, expected_revision, changes_mapping, *, authorize=None):
        if not isinstance(changes_mapping, dict) or any(not isinstance(key, str) or not ENV_NAME.fullmatch(key) for key in changes_mapping):
            raise AdminError('Invalid environment setting name.')
        for value in changes_mapping.values():
            if value is not None and (not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value)):
                raise AdminError('Environment values must be single-line text of at most 8192 characters.')
        def change(text):
            lines, written = [], set()
            for line in text.splitlines():
                match = ENV_LINE.fullmatch(line.strip())
                if match and match[1] in changes_mapping:
                    key = match[1]
                    if key not in written and changes_mapping[key] is not None:
                        lines.append(key + '=' + quote(changes_mapping[key]))
                    written.add(key)
                else:
                    lines.append(line)
            for key, value in changes_mapping.items():
                if key not in written and value is not None:
                    lines.append(key + '=' + quote(value))
            return '\n'.join(lines).rstrip() + '\n'
        def quote(value):
            # dotenv single quotes avoid interpolation of ${...} inside secrets.
            return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"
        return self._mutate('.env', expected_revision, lambda: self.read_text('.env'),
                            lambda value: value.encode('utf-8'), change, authorize=authorize)
