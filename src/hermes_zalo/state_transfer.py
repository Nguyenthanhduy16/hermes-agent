"""Offline, data-only transfer from the Windows bot to one container volume.

The operator must stop all copies of the bot before export/import. The required
flag is an acknowledgement, not a distributed lock or a process detector.
No environment files, credentials, model configuration or Hermes conversations
are included. Keep the same bot token and roster mapping on the destination.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
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

FORMAT_VERSION = 1
ROOTS = ("hermes-home/data/zalo", "tickets", "knowledge")
STATE_NAME = re.compile(r"(?:zalo-[0-9a-f]{16}|work-[0-9a-f]{16}\.(?:profiles|reminders|issues|pilot|registration))\.sqlite3")
TICKET_NAME = re.compile(r"(?:lab_live|lab_live_test|sheets_test|demo)\.sqlite3")
MAX_FILES = 10000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


class TransferError(ValueError):
    """Only fixed, non-sensitive messages are shown by the command line."""


def _acknowledge(bot_stopped):
    if bot_stopped is not True:
        raise TransferError("Dừng mọi bản bot trước, rồi thêm --bot-stopped để xác nhận.")


def _is_link(path):
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _no_links(path):
    """Reject symlinks/junctions in existing ancestors, before resolving."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if _is_link(item):
            raise TransferError("Không hỗ trợ đường dẫn chứa symlink hoặc junction.")
    return path


def _allowed(name):
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or any(
        part in ("", ".", "..") or ":" in part or part.endswith((".", " "))
        for part in path.parts
    ):
        return False
    if any(re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part) for part in path.parts):
        return False
    if len(path.parts) == 4 and path.parts[:3] == ("hermes-home", "data", "zalo"):
        return STATE_NAME.fullmatch(path.name) is not None
    if len(path.parts) == 2 and path.parts[0] == "tickets":
        return TICKET_NAME.fullmatch(path.name) is not None
    if len(path.parts) >= 2 and path.parts[0] == "knowledge":
        return (name == "knowledge/manifest.json" or path.suffix.lower() in (".md", ".txt")) and not any(
            part.startswith(".") for part in path.parts[1:])
    return False


def _source_files(project_root):
    for relative, exported_root in (
        (".runtime/hermes-home/data/zalo", ROOTS[0]),
        (".runtime/tickets", ROOTS[1]),
        ("data/knowledge", ROOTS[2]),
    ):
        source_root = _no_links(project_root / relative)
        if not source_root.exists():
            continue
        if not source_root.is_dir():
            raise TransferError("Thư mục dữ liệu nguồn không hợp lệ.")
        for directory, folders, files in os.walk(source_root, followlinks=False):
            for name in folders + files:
                entry = Path(directory) / name
                if _is_link(entry):
                    raise TransferError("Nguồn dữ liệu chứa symlink hoặc junction; chưa tạo bản chuyển.")
            # Runtime SQLite stores are flat. Nested folders are historical
            # maintenance snapshots, not active registrations or Tickets.
            # Do not transfer those old copies or recurse into them.
            if exported_root != "knowledge":
                folders[:] = []
            for name in sorted(files):
                source = Path(directory) / name
                relative_name = source.relative_to(source_root).as_posix()
                archive_name = exported_root + "/" + relative_name
                # Only explicitly supported data files enter the archive.
                if _allowed(archive_name):
                    if not stat.S_ISREG(source.stat().st_mode):
                        raise TransferError("Nguồn dữ liệu phải là tệp thông thường.")
                    yield source, archive_name
                elif name.endswith((".sqlite3", ".db")):
                    raise TransferError("Có cơ sở dữ liệu chưa được hỗ trợ; cần kiểm tra trước khi chuyển.")


def _hash_file(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(block)
            if size > MAX_FILE_BYTES:
                raise TransferError("Tệp dữ liệu vượt giới hạn chuyển an toàn.")
            digest.update(block)
    return digest.hexdigest(), size


def _sqlite_snapshot(source, destination):
    started = time.monotonic()

    def progress(_status, _remaining, _total):
        if time.monotonic() - started > 30:
            raise TransferError("Sao lưu SQLite quá hạn; kiểm tra mọi bản bot đã dừng.")

    try:
        origin = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            backup = sqlite3.connect(destination)
            try:
                origin.backup(backup, pages=256, progress=progress, sleep=0.05)
                backup.execute("PRAGMA journal_mode=DELETE")
                if backup.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise TransferError("Bản sao SQLite không vượt qua kiểm tra toàn vẹn.")
            finally:
                backup.close()
        finally:
            origin.close()
    except sqlite3.Error:
        raise TransferError("Không tạo được bản sao SQLite hợp lệ; dữ liệu nguồn được giữ nguyên.") from None


def export_state(project_root: Path, output: Path, *, bot_stopped: bool):
    """Create a new ZIP; never overwrite an existing export or change source DBs."""
    _acknowledge(bot_stopped)
    project_root, output = _no_links(project_root), _no_links(output)
    sources = list(_source_files(project_root))
    if not sources:
        raise TransferError("Không tìm thấy dữ liệu được hỗ trợ để chuyển.")
    if len(sources) > MAX_FILES:
        raise TransferError("Có quá nhiều tệp dữ liệu.")
    names = [name.casefold() for _, name in sources]
    if len(set(names)) != len(names):
        raise TransferError("Tên tệp trùng nhau khi chuyển giữa Windows và Linux.")
    if output.exists():
        raise TransferError("Tệp đích đã tồn tại; chọn tên tệp chuyển mới.")
    output.parent.mkdir(parents=True, exist_ok=True)
    written = False
    try:
        with tempfile.TemporaryDirectory(prefix="labcoach-transfer-") as temporary:
            manifest = {"format": "labcoach-state", "version": FORMAT_VERSION,
                        "created_at": datetime.now(timezone.utc).isoformat(), "files": {}}
            total = 0
            with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                written = True
                for index, (source, archive_name) in enumerate(sources):
                    _no_links(source)
                    payload = source
                    if archive_name.endswith(".sqlite3"):
                        payload = Path(temporary) / f"snapshot-{index}.sqlite3"
                        _sqlite_snapshot(source, payload)
                    checksum, size = _hash_file(payload)
                    total += size
                    if total > MAX_TOTAL_BYTES:
                        raise TransferError("Tổng dữ liệu vượt giới hạn chuyển an toàn.")
                    manifest["files"][archive_name] = {"sha256": checksum, "size": size}
                    archive.write(payload, archive_name)
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        if os.name != "nt":
            output.chmod(0o600)
        return {"files": len(sources), "bytes": total}
    except Exception:
        if written:
            output.unlink(missing_ok=True)
        raise


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransferError("Manifest có mục trùng nhau.")
        result[key] = value
    return result


def _archive_manifest(archive):
    infos = archive.infolist()
    if len(infos) > MAX_FILES + 1:
        raise TransferError("Gói chuyển có quá nhiều tệp.")
    names = [item.filename for item in infos]
    if len(names) != len(set(name.casefold() for name in names)):
        raise TransferError("Gói chuyển có tên tệp trùng nhau.")
    if names.count("manifest.json") != 1:
        raise TransferError("Gói chuyển thiếu manifest duy nhất.")
    for item in infos:
        mode = item.external_attr >> 16
        if item.is_dir() or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
            raise TransferError("Gói chuyển chứa loại tệp không được hỗ trợ.")
        if item.flag_bits & 1:
            raise TransferError("Không hỗ trợ ZIP được mã hóa.")
        if item.filename != "manifest.json" and not _allowed(item.filename):
            raise TransferError("Gói chuyển chứa đường dẫn hoặc loại dữ liệu không được phép.")
    manifest_info = archive.getinfo("manifest.json")
    if manifest_info.file_size > 4 * 1024 * 1024:
        raise TransferError("Manifest vượt giới hạn.")
    try:
        manifest = json.loads(archive.read(manifest_info), object_pairs_hook=_unique_json)
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, TransferError):
            raise
        raise TransferError("Manifest không hợp lệ.") from None
    if not isinstance(manifest, dict) or manifest.get("format") != "labcoach-state" or type(manifest.get("version")) is not int or manifest.get("version") != FORMAT_VERSION:
        raise TransferError("Định dạng hoặc phiên bản gói chuyển không được hỗ trợ.")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files or set(files) != set(names) - {"manifest.json"}:
        raise TransferError("Danh sách dữ liệu không khớp manifest.")
    total = 0
    for name, details in files.items():
        if not isinstance(details, dict) or type(details.get("size")) is not int or not 0 <= details["size"] <= MAX_FILE_BYTES:
            raise TransferError("Kích thước dữ liệu trong manifest không hợp lệ.")
        if not isinstance(details.get("sha256"), str) or not re.fullmatch("[a-f0-9]{64}", details["sha256"]):
            raise TransferError("Checksum trong manifest không hợp lệ.")
        if archive.getinfo(name).file_size != details["size"]:
            raise TransferError("Kích thước dữ liệu không khớp manifest.")
        total += details["size"]
    if total > MAX_TOTAL_BYTES:
        raise TransferError("Tổng dữ liệu vượt giới hạn chuyển an toàn.")
    return files, total


def _empty_targets(data_dir):
    targets = {}
    for relative in ROOTS:
        path = _no_links(data_dir / relative)
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise TransferError("Dữ liệu đích đã tồn tại; không ghi đè đăng ký, Tickets hoặc tài liệu.")
        targets[relative] = path
    return targets


def import_state(archive: Path, data_dir: Path, *, bot_stopped: bool):
    """Verify the complete archive before publishing into empty data directories."""
    _acknowledge(bot_stopped)
    archive, data_dir = _no_links(archive), _no_links(data_dir)
    _empty_targets(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as source, tempfile.TemporaryDirectory(
            prefix=".state-transfer-", dir=data_dir
        ) as temporary:
            files, total = _archive_manifest(source)
            stage = Path(temporary)
            for name, details in files.items():
                destination = stage.joinpath(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with source.open(name) as incoming, destination.open("xb") as outgoing:
                    for block in iter(lambda: incoming.read(1024 * 1024), b""):
                        size += len(block)
                        if size > details["size"]:
                            raise TransferError("Dữ liệu giải nén vượt kích thước khai báo.")
                        digest.update(block)
                        outgoing.write(block)
                if size != details["size"] or digest.hexdigest() != details["sha256"]:
                    raise TransferError("Checksum dữ liệu không khớp; chưa nhập bất kỳ dữ liệu nào.")
                if os.name != "nt":
                    destination.chmod(0o600)
                if name.endswith(".sqlite3"):
                    try:
                        connection = sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)
                        try:
                            valid = connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
                        finally:
                            connection.close()
                        if not valid:
                            raise TransferError("SQLite trong gói chuyển không hợp lệ.")
                    except sqlite3.Error:
                        raise TransferError("SQLite trong gói chuyển không hợp lệ.") from None
            targets = _empty_targets(data_dir)
            published = []
            try:
                for relative, destination in targets.items():
                    prepared = stage / relative
                    if not prepared.exists():
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        destination.rmdir()  # Only an empty directory may be replaced.
                    prepared.replace(destination)
                    published.append((destination, prepared))
            except OSError:
                # Roll back only the exact directories this import published.
                for destination, prepared in reversed(published):
                    prepared.parent.mkdir(parents=True, exist_ok=True)
                    destination.replace(prepared)
                raise TransferError("Không thể hoàn tất nhập dữ liệu; kiểm tra quyền ghi và bot đã dừng.") from None
            return {"files": len(files), "bytes": total}
    except (zipfile.BadZipFile, RuntimeError, EOFError):
        raise TransferError("Không đọc được gói chuyển hợp lệ.") from None


def main(argv=None):
    # Direct PowerShell invocation does not pass run.ps1's UTF-8 environment.
    # Keep Vietnamese success/error output safe under legacy Windows encodings.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Chuyển dữ liệu LabCoach ngoại tuyến; không đưa gói chuyển lên Git.")
    actions = parser.add_subparsers(dest="action", required=True)
    exporter = actions.add_parser("export")
    exporter.add_argument("--project-root", type=Path, default=Path.cwd())
    exporter.add_argument("--output", type=Path, required=True)
    importer = actions.add_parser("import")
    importer.add_argument("archive", type=Path)
    importer.add_argument("--data-dir", type=Path, default=Path("/data"))
    for action in (exporter, importer):
        action.add_argument("--bot-stopped", action="store_true",
                            help="Xác nhận đã dừng mọi bản bot; công cụ không tự dừng hay xác minh tiến trình.")
    args = parser.parse_args(argv)
    try:
        if args.action == "export":
            result = export_state(args.project_root, args.output, bot_stopped=args.bot_stopped)
        else:
            result = import_state(args.archive, args.data_dir, bot_stopped=args.bot_stopped)
    except (TransferError, OSError):
        # OSError may carry paths and implementation details; don't print them.
        error = sys.exception()
        print(str(error) if isinstance(error, TransferError) else "Không thể đọc/ghi dữ liệu; kiểm tra đường dẫn và quyền.", file=sys.stderr)
        return 2
    print(f"Đạt: {result['files']} tệp dữ liệu. Giữ nguyên bot token và cấu hình Workload trên máy đích.")
    print("Gói chuyển chứa dữ liệu nội bộ; lưu riêng, không đưa lên Git. Hội thoại Hermes không được chuyển.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
