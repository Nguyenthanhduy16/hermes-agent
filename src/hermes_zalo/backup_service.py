"""Opt-in machine download of encrypted backups; never a browser Admin capability."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import secrets
import tempfile
import threading
import time

from .backup import BackupError, MAGIC, MAX_ENCRYPTED_BYTES, create_backup, load_key


class BackupHTTPError(ValueError):
    def __init__(self, message, status, retry_after=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


@dataclass(frozen=True)
class BackupDownload:
    body: bytes = field(repr=False)
    sha256: str


class BackupService:
    SUCCESS_INTERVAL = 600
    ATTEMPT_INTERVAL = 60

    def __init__(self, *, factory=create_backup, clock=time.monotonic):
        self.enabled = False
        self.config_valid = True
        self._factory, self._clock = factory, clock
        self._token = self._key = b''
        self._data_dir = None
        self._lock = threading.Lock()
        self._last_success = self._last_attempt = None

    @classmethod
    def from_mapping(cls, values, *, factory=create_backup, clock=time.monotonic):
        service = cls(factory=factory, clock=clock)
        try:
            flag = str(values.get('LABCOACH_BACKUP_ENABLED') or 'false').strip().lower()
            if flag not in ('true', 'false'):
                raise ValueError()
            service.enabled = flag == 'true'
            if not service.enabled:
                return service
            token = values.get('LABCOACH_BACKUP_ACCESS_TOKEN', '')
            if (not isinstance(token, str) or not 32 <= len(token) <= 256
                    or not token.isascii() or any(not 33 <= ord(char) <= 126 for char in token)):
                raise ValueError()
            data_dir = Path(values.get('BOT_DATA_DIR') or '')
            if not data_dir.is_absolute() or not data_dir.is_dir():
                raise ValueError()
            key_file = values.get('LABCOACH_BACKUP_KEY_FILE') or None
            if key_file and not Path(key_file).is_absolute():
                raise ValueError()
            key = load_key(key_file=key_file, environ=values)
            service._data_dir, service._key = data_dir, key
            service._token = token.encode('ascii')
        except (BackupError, OSError, ValueError, TypeError):
            # A bad backup setting must not stop the bot or reveal its value.
            service.config_valid = False
        return service

    def authorize(self, supplied):
        if not self.config_valid:
            raise BackupHTTPError('Chức năng sao lưu chưa được cấu hình hợp lệ.', 503)
        if not self.enabled:
            raise BackupHTTPError('Không tìm thấy chức năng.', 404)
        valid = (isinstance(supplied, list) and len(supplied) == 1
                 and isinstance(supplied[0], str) and 32 <= len(supplied[0]) <= 256
                 and supplied[0].isascii() and all(33 <= ord(char) <= 126 for char in supplied[0]))
        if not valid or not secrets.compare_digest(supplied[0].encode('ascii'), self._token):
            raise BackupHTTPError('Yêu cầu sao lưu không được phép.', 403)

    def download(self, supplied):
        self.authorize(supplied)
        if not self._lock.acquire(blocking=False):
            raise BackupHTTPError('Đang tạo bản sao lưu; thử lại sau.', 429, 60)
        try:
            now = self._clock()
            for previous, interval in ((self._last_success, self.SUCCESS_INTERVAL),
                                       (self._last_attempt, self.ATTEMPT_INTERVAL)):
                if previous is not None and now - previous < interval:
                    remaining = max(1, int(interval - (now - previous)) + 1)
                    raise BackupHTTPError('Chưa đến lượt sao lưu tiếp theo; thử lại sau.', 429, remaining)
            self._last_attempt = now
            try:
                # This directory is separate from BOT_DATA_DIR. The core takes
                # SQLite snapshots and encrypts before the HTTP layer reads.
                with tempfile.TemporaryDirectory(prefix='labcoach-download-') as temporary:
                    root = Path(temporary)
                    root.chmod(0o700)
                    output = root / 'backup.lcbak'
                    self._factory(self._data_dir, output, self._key)
                    if output.is_symlink() or not output.is_file() or output.stat().st_size > MAX_ENCRYPTED_BYTES:
                        raise BackupError('Tệp sao lưu chưa hợp lệ.')
                    with output.open('rb') as handle:
                        body = handle.read(MAX_ENCRYPTED_BYTES + 1)
                    if len(body) > MAX_ENCRYPTED_BYTES or not body.startswith(MAGIC):
                        raise BackupError('Tệp sao lưu chưa hợp lệ.')
                result = BackupDownload(body, hashlib.sha256(body).hexdigest())
            except Exception:
                # Database names, paths, keys and source messages stay private.
                raise BackupHTTPError('Chưa tạo được bản sao lưu; nhờ người vận hành kiểm tra.', 503) from None
            self._last_success = self._clock()
            return result
        finally:
            self._lock.release()
