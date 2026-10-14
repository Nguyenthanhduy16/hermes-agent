"""Encrypted, data-only SQLite backups; restoration stops at an empty staging directory.

Snapshots are consistent per database, not atomic across databases. API keys,
environment files, Sheets cells, Hermes logs and plugin files are excluded. Admin
password hashes remain in the encrypted data; restore invalidates saved sessions.
Deployment/restoration of live services and off-site upload are separate operations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import sys
import tempfile
import time
import zipfile
import zlib

MAGIC = b"LABCOACH-BACKUP-1\n"
MAX_FILES = 256
MAX_FILE_BYTES = 24 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 1024 * 1024
MAX_ENCRYPTED_BYTES = (MAX_ARCHIVE_BYTES + 1024) * 4 // 3 + len(MAGIC)
MAX_MANIFEST_BYTES = 256 * 1024
SQLITE_TIMEOUT = 30
BACKUP_TIMEOUT = 60
STATE_ROOT = "hermes-home/data/zalo"
STATE_NAME = re.compile(r"(?:zalo-[0-9a-f]{16}|work-[0-9a-f]{16}\."
                        r"(?:profiles|reminders|issues|pilot|registration|admin|conversations|schedule-notices|ai-usage))\.sqlite3")
TICKET_NAME = re.compile(r"(?:lab_live|lab_live_test|sheets_test|demo)\.sqlite3")
ISSUE_MIGRATION_SNAPSHOT = re.compile(r"work-[0-9a-f]{16}\.issues\.pre-managed-"
                                      r"([0-9]{8}-[0-9]{6}-[0-9]{6})\.sqlite3")


class BackupError(ValueError):
    """Safe operational message without credentials, data or filesystem details."""


def _crypto():
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        raise BackupError("Cần cài thư viện sao lưu: pip install '.[backup]'.") from None
    return Fernet, InvalidToken


def _cipher(key):
    Fernet, _ = _crypto()
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        raise BackupError("Khóa sao lưu chưa hợp lệ; dùng khóa Fernet đã tạo riêng.") from None


def _safe_path(path):
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and (
                getattr(item.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024))):
            raise BackupError("Không hỗ trợ symlink, junction hoặc đường dẫn chuyển hướng.")
    return path


def _regular(path):
    path = _safe_path(path)
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise BackupError("Nguồn phải là tệp dữ liệu thông thường.")
    return path


def _allowed(name):
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        return False
    parts = PurePosixPath(name).parts
    if PurePosixPath(name).as_posix() != name:
        return False
    return (len(parts) == 4 and "/".join(parts[:3]) == STATE_ROOT and bool(STATE_NAME.fullmatch(parts[3])) or
            len(parts) == 2 and parts[0] == "tickets" and bool(TICKET_NAME.fullmatch(parts[1])))


def _sources(data_dir):
    sources = []
    for relative in (STATE_ROOT, "tickets"):
        directory = _safe_path(data_dir / relative)
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise BackupError("Thư mục dữ liệu nguồn chưa hợp lệ.")
        for entry in sorted(directory.iterdir()):
            _safe_path(entry)
            name = relative + "/" + entry.name
            if _allowed(name):
                sources.append((_regular(entry), name))
                if len(sources) > MAX_FILES:
                    raise BackupError("Số tệp dữ liệu vượt giới hạn sao lưu.")
            elif relative == STATE_ROOT and _issue_migration_snapshot(entry.name):
                # issue_migration.import_snapshot creates this exact historical
                # safety copy before importing. Only the current .issues DB is
                # backed up; snapshots are excluded rather than restored live.
                continue
            elif entry.suffix.lower() in (".sqlite3", ".db"):
                raise BackupError("Có cơ sở dữ liệu chưa được hỗ trợ; kiểm tra trước khi sao lưu.")
    if not sources or len(sources) > MAX_FILES:
        raise BackupError("Không có dữ liệu được hỗ trợ hoặc số tệp vượt giới hạn.")
    return sources


def _issue_migration_snapshot(name):
    match = ISSUE_MIGRATION_SNAPSHOT.fullmatch(name)
    if match is None:
        return False
    try:
        datetime.strptime(match[1], "%Y%m%d-%H%M%S-%f")
    except ValueError:
        return False
    return True


def _deadline(deadline):
    if deadline is not None and time.monotonic() > deadline:
        raise BackupError("Sao lưu quá hạn; thử lại khi hệ thống ít thao tác.")


def _integrity(path, deadline=None):
    started = time.monotonic()
    _deadline(deadline)
    db = None
    try:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        db.execute("PRAGMA trusted_schema=OFF")
        db.set_progress_handler(lambda: int(time.monotonic() - started > SQLITE_TIMEOUT or
                                            deadline is not None and time.monotonic() > deadline), 1000)
        valid = db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    except sqlite3.Error:
        raise BackupError("SQLite không vượt qua kiểm tra toàn vẹn hoặc quá hạn.") from None
    finally:
        if db is not None:
            db.close()
    if not valid:
        raise BackupError("SQLite không vượt qua kiểm tra toàn vẹn.")


def _snapshot(source, target, deadline=None):
    _deadline(deadline)
    source = _regular(source)
    if source.stat().st_size > MAX_FILE_BYTES:
        raise BackupError("Cơ sở dữ liệu vượt giới hạn sao lưu.")
    started = time.monotonic()
    origin = backup = None

    def progress(_status, _remaining, total):
        _deadline(deadline)
        if time.monotonic() - started > SQLITE_TIMEOUT:
            raise BackupError("Sao lưu SQLite quá hạn; thử lại khi hệ thống ít thao tác.")
        if total * page_size > MAX_FILE_BYTES:
            raise BackupError("Cơ sở dữ liệu vượt giới hạn sao lưu.")

    try:
        origin = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=2)
        origin.execute("PRAGMA trusted_schema=OFF")
        page_size = origin.execute("PRAGMA page_size").fetchone()[0]
        backup = sqlite3.connect(target)
        origin.backup(backup, pages=128, progress=progress, sleep=0.05)
        backup.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error:
        raise BackupError("Chưa tạo được bản sao SQLite hợp lệ; dữ liệu nguồn giữ nguyên.") from None
    finally:
        if backup is not None:
            backup.close()
        if origin is not None:
            origin.close()
    if not 0 < target.stat().st_size <= MAX_FILE_BYTES:
        raise BackupError("Kích thước cơ sở dữ liệu chưa hợp lệ để sao lưu.")
    _integrity(target, deadline)


def _new_file(path, payload):
    path = _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = False
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        written = True
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if written:
            path.unlink(missing_ok=True)
        raise


def create_backup(data_dir: Path, output: Path, key: bytes):
    """Read live app DBs and create one new encrypted file; never overwrite."""
    cipher = _cipher(key)
    deadline = time.monotonic() + BACKUP_TIMEOUT
    data_dir, output = _safe_path(data_dir), _safe_path(output)
    if output.exists():
        raise BackupError("Tệp sao lưu đã tồn tại; chọn tên mới.")
    sources = _sources(data_dir)
    manifest = dict(format="labcoach-backup", version=1, created_at=datetime.now(timezone.utc).isoformat(),
                    cross_database_atomic=False, files={})
    total = 0
    with tempfile.TemporaryDirectory(prefix="labcoach-backup-") as temporary:
        packed = io.BytesIO()
        with zipfile.ZipFile(packed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, (source, name) in enumerate(sources):
                target = Path(temporary) / f"{index}.sqlite3"
                _snapshot(source, target, deadline)
                size = target.stat().st_size
                total += size
                if total > MAX_TOTAL_BYTES:
                    raise BackupError("Tổng dữ liệu vượt giới hạn sao lưu.")
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                manifest["files"][name] = dict(size=size, sha256=digest)
                archive.write(target, name)
                target.unlink()
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        if packed.tell() > MAX_ARCHIVE_BYTES:
            raise BackupError("Gói dữ liệu vượt giới hạn sao lưu.")
        _deadline(deadline)
        encrypted = MAGIC + cipher.encrypt(packed.getvalue())
        _deadline(deadline)
        _new_file(output, encrypted)
    return dict(files=len(sources), bytes=total, encrypted_bytes=len(encrypted), cross_database_atomic=False)


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupError("Manifest có mục trùng nhau.")
        result[key] = value
    return result


def _extract_verified(archive_path, key, stage):
    cipher = _cipher(key)
    _, InvalidToken = _crypto()
    path = _regular(archive_path)
    if path.stat().st_size > MAX_ENCRYPTED_BYTES:
        raise BackupError("Gói sao lưu vượt giới hạn.")
    with path.open("rb") as incoming:
        payload = incoming.read(MAX_ENCRYPTED_BYTES + 1)
    if len(payload) > MAX_ENCRYPTED_BYTES:
        raise BackupError("Gói sao lưu vượt giới hạn.")
    if not payload.startswith(MAGIC):
        raise BackupError("Định dạng sao lưu chưa được hỗ trợ.")
    try:
        clear = cipher.decrypt(payload[len(MAGIC):])
    except InvalidToken:
        raise BackupError("Không xác thực được bản sao lưu; khóa không đúng hoặc dữ liệu đã hỏng.") from None
    if len(clear) > MAX_ARCHIVE_BYTES:
        raise BackupError("Gói dữ liệu vượt giới hạn.")
    try:
        with zipfile.ZipFile(io.BytesIO(clear)) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if not 2 <= len(infos) <= MAX_FILES + 1 or len(names) != len(set(name.casefold() for name in names)):
                raise BackupError("Số tệp hoặc tên tệp trong bản sao lưu chưa hợp lệ.")
            if names.count("manifest.json") != 1:
                raise BackupError("Bản sao lưu thiếu manifest duy nhất.")
            for item in infos:
                mode = stat.S_IFMT(item.external_attr >> 16)
                if item.is_dir() or mode not in (0, stat.S_IFREG) or item.flag_bits & 1:
                    raise BackupError("Bản sao lưu chứa loại tệp không được hỗ trợ.")
                if item.filename != "manifest.json" and not _allowed(item.filename):
                    raise BackupError("Đường dẫn dữ liệu trong bản sao lưu chưa hợp lệ.")
            if archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                raise BackupError("Manifest vượt giới hạn.")
            manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=_unique_json)
            if (not isinstance(manifest, dict) or manifest.get("format") != "labcoach-backup" or
                    type(manifest.get("version")) is not int or manifest["version"] != 1 or
                    manifest.get("cross_database_atomic") is not False):
                raise BackupError("Phiên bản hoặc mô tả bản sao lưu chưa hợp lệ.")
            files = manifest.get("files")
            if not isinstance(files, dict) or set(files) != set(names) - {"manifest.json"}:
                raise BackupError("Danh sách tệp không khớp manifest.")
            total = 0
            for name, detail in files.items():
                if (not isinstance(detail, dict) or type(detail.get("size")) is not int or
                        not 0 < detail["size"] <= MAX_FILE_BYTES or not isinstance(detail.get("sha256"), str) or
                        not re.fullmatch("[a-f0-9]{64}", detail["sha256"]) or
                        archive.getinfo(name).file_size != detail["size"]):
                    raise BackupError("Kích thước hoặc checksum trong manifest chưa hợp lệ.")
                total += detail["size"]
            if total > MAX_TOTAL_BYTES:
                raise BackupError("Tổng dữ liệu vượt giới hạn khôi phục.")
            for name, detail in files.items():
                destination = stage.joinpath(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                with archive.open(name) as incoming, destination.open("xb") as outgoing:
                    while block := incoming.read(1024 * 1024):
                        size += len(block)
                        if size > detail["size"]:
                            raise BackupError("Dữ liệu giải nén vượt kích thước khai báo.")
                        digest.update(block)
                        outgoing.write(block)
                if size != detail["size"] or digest.hexdigest() != detail["sha256"]:
                    raise BackupError("Checksum dữ liệu không khớp; chưa khôi phục.")
                if os.name != "nt":
                    destination.chmod(0o600)
                _integrity(destination)
            return dict(files=len(files), bytes=total, cross_database_atomic=False)
    except (zipfile.BadZipFile, RuntimeError, EOFError, UnicodeError, json.JSONDecodeError, zlib.error, RecursionError):
        raise BackupError("Không đọc được cấu trúc bản sao lưu hợp lệ.") from None


def verify_backup(archive: Path, key: bytes):
    """Authenticate, verify hashes and check every SQLite DB using disposable files."""
    with tempfile.TemporaryDirectory(prefix="labcoach-verify-") as temporary:
        return _extract_verified(archive, key, Path(temporary))


def restore_backup(archive: Path, staging_dir: Path, key: bytes):
    """Restore to empty staging; invalidate saved browser sessions after verification.

    This intentional transform runs only on the verified staging databases, so
    restored Admin accounts retain credentials/permissions but must log in again.
    """
    staging_dir = _safe_path(staging_dir)

    def empty():
        _safe_path(staging_dir)
        if staging_dir.exists() and (not staging_dir.is_dir() or any(staging_dir.iterdir())):
            raise BackupError("Chỉ khôi phục vào thư mục staging trống; không ghi đè dữ liệu hiện có.")

    empty()
    staging_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".labcoach-restore-", dir=staging_dir.parent) as temporary:
        prepared = Path(temporary) / "verified"
        prepared.mkdir(mode=0o700)
        result = _extract_verified(archive, key, prepared)
        invalidated = 0
        for path in (prepared / STATE_ROOT).glob("*.admin.sqlite3"):
            db = None
            try:
                db = sqlite3.connect(path)
                db.execute("PRAGMA trusted_schema=OFF")
                if db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='admin_sessions'").fetchone():
                    invalidated += db.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0]
                    db.execute("DELETE FROM admin_sessions")
                    db.commit()
            except sqlite3.Error:
                raise BackupError("Chưa vô hiệu hóa được phiên Admin trong staging; chưa khôi phục.") from None
            finally:
                if db is not None:
                    db.close()
            _integrity(path)
        result["invalidated_admin_sessions"] = invalidated
        empty()
        if staging_dir.exists():
            staging_dir.rmdir()
        prepared.rename(staging_dir)
        return result


def load_key(key_file=None, environ=None):
    """Key file overrides the environment; never output either value."""
    values = os.environ if environ is None else environ
    if key_file is not None:
        source = _regular(key_file)
        if source.stat().st_size > 1024:
            raise BackupError("Tệp khóa sao lưu chưa hợp lệ.")
        key = source.read_bytes().strip()
    else:
        raw = values.get("LABCOACH_BACKUP_KEY", "")
        try:
            key = raw.encode("ascii")
        except (AttributeError, UnicodeError):
            raise BackupError("Khóa sao lưu chưa hợp lệ.") from None
    _cipher(key)
    return key


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sao lưu mã hóa; khôi phục chỉ tới staging trống.")
    actions = parser.add_subparsers(dest="action", required=True)
    generator = actions.add_parser("generate-key")
    generator.add_argument("--output", type=Path, required=True)
    creator = actions.add_parser("create")
    creator.add_argument("--data-dir", type=Path, default=Path("/data"))
    creator.add_argument("--output", type=Path, required=True)
    verifier = actions.add_parser("verify")
    verifier.add_argument("archive", type=Path)
    restorer = actions.add_parser("restore")
    restorer.add_argument("archive", type=Path)
    restorer.add_argument("--staging-dir", type=Path, required=True)
    for action in (creator, verifier, restorer):
        action.add_argument("--key-file", type=Path, help="Mặc định đọc LABCOACH_BACKUP_KEY; không nhập khóa vào câu lệnh.")
    args = parser.parse_args(argv)
    try:
        if args.action == "generate-key":
            _new_file(args.output, _crypto()[0].generate_key() + b"\n")
            print("Đã lưu khóa mới. Giữ khóa riêng với bản sao lưu; không đưa lên Git.")
            return 0
        key = load_key(args.key_file)
        if args.action == "create":
            result = create_backup(args.data_dir, args.output, key)
        elif args.action == "verify":
            result = verify_backup(args.archive, key)
        else:
            result = restore_backup(args.archive, args.staging_dir, key)
        print(f"Đạt: {result['files']} cơ sở dữ liệu. Bản sao nhất quán theo từng DB; không nguyên tử giữa các DB.")
        if args.action == "restore":
            print("Dữ liệu chỉ ở staging; chưa thay dữ liệu hoặc khởi động bot thật.")
            print("Các phiên đăng nhập Admin đã bị xóa trong staging; cần đăng nhập lại sau khôi phục.")
        return 0
    except (BackupError, OSError) as error:
        print(str(error) if isinstance(error, BackupError) else "Không đọc/ghi được dữ liệu; kiểm tra quyền và đường dẫn.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
