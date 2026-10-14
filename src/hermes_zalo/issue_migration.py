"""Local-operator cutover from a frozen Sheets snapshot; no network or bot.

Snapshot JSON: {"target":"sheet-id/Issues", "rows":[[id,created,title,
body,status,user_id,chat_id,chat_type], ...]}. Do not include the header row.
Keep snapshots beside private runtime data, never in Git. Empty rows are valid
only when the operator verified the configured old source has no issues.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timezone

from .issue_management import IssueError, ManagedIssueStore


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IssueError('Snapshot có trường bị lặp.')
        result[key] = value
    return result


def read_snapshot(path):
    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise IssueError('Snapshot quá lớn; cần đối soát theo kế hoạch riêng.')
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise IssueError('Snapshot JSON không hợp lệ.') from exc
    if (not isinstance(data, dict) or set(data) != {'target', 'rows'} or
            not isinstance(data['target'], str) or not data['target'].strip() or
            not isinstance(data['rows'], list) or len(data['rows']) > 10000):
        raise IssueError('Snapshot cần target và rows (tối đa 10.000 dòng).')
    seen = set()
    for row in data['rows']:
        if not isinstance(row, list) or len(row) != 8 or not all(isinstance(value, str) for value in row):
            raise IssueError('Mỗi dòng snapshot cần đúng 8 cột văn bản theo mẫu Issue cũ.')
        if row[0] in seen:
            raise IssueError('Snapshot có mã Issue bị trùng.')
        seen.add(row[0])
    return data


def registration_resolver(path):
    """Read only the immutable mapping; never use names or phone numbers."""
    if path is None:
        return lambda _: None
    path = Path(path).resolve(strict=True)
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        records = list(db.execute('SELECT user_id,chat_id,member_id,status FROM registration_requests'))
    finally:
        db.close()
    by_user = {}
    for row in records:
        by_user.setdefault(row['user_id'], []).append(dict(row))
    def resolve(row):
        candidates = by_user.get(row['user_id'], [])
        # The same account may have been corrected/relinked to another member.
        # Old issues carry only a Zalo ID, so current approval alone cannot
        # establish which historical member submitted them.
        if len({candidate['member_id'] for candidate in candidates}) != 1:
            return None
        approved = [candidate for candidate in candidates if candidate['status'] == 'approved']
        if len(approved) != 1:
            return None
        candidate = approved[0]
        if row['chat_type'] == 'PRIVATE' and row['chat_id'] != candidate['chat_id']:
            return None
        return candidate['member_id']
    return resolve


def copy_database(source, destination):
    """SQLite's consistent backup includes WAL; output is owner-readable only."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    target = sqlite3.connect(destination)
    try:
        if source.exists():
            original = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
            try:
                original.backup(target)
            finally:
                original.close()
    finally:
        target.close()


def import_snapshot(database, snapshot, *, registrations=None, apply=False,
                    bot_stopped=False, source_complete=False, reconcile_found=False,
                    not_created=(), reason='Đối soát snapshot nguồn đầy đủ khi bot đã dừng.'):
    if apply and not (bot_stopped and source_complete):
        raise IssueError('Để ghi chuyển đổi, cần xác nhận --bot-stopped và --source-complete.')
    data = read_snapshot(snapshot)
    resolver = registration_resolver(registrations)
    database = Path(database)
    with tempfile.TemporaryDirectory(prefix='labcoach-issue-migration-') as temporary:
        trial_path = Path(temporary) / 'trial.sqlite3'
        copy_database(database, trial_path)
        trial = ManagedIssueStore(trial_path)
        try:
            trial.import_legacy(data['rows'], target=data['target'], resolve_member=resolver)
            if reconcile_found or not_created:
                trial.reconcile_legacy_writes(data['target'], not_created=not_created, reason=reason)
            report = trial.migration_report()
            blockers = any(report[key] for key in ('unresolved_owners', 'uncertain_legacy_writes',
                                                  'unimported_created_drafts', 'pending_legacy_drafts'))
            if not blockers:
                report = trial.mark_source_reconciled(data['target'], data['rows'])
            report = {**report, 'source_rows': len(data['rows']), 'applied': False}
        finally:
            trial.close()
        if not apply:
            return report
        if blockers:
            raise IssueError('Chuyển đổi bị chặn bởi các bản ghi chưa đối soát; chạy xem trước để kiểm tra số lượng.', 409)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
        backup = database.with_name(database.stem + '.pre-managed-' + stamp + '.sqlite3')
        copy_database(database, backup)
        live = ManagedIssueStore(database)
        try:
            live.import_legacy(data['rows'], target=data['target'], resolve_member=resolver)
            if reconcile_found or not_created:
                live.reconcile_legacy_writes(data['target'], not_created=not_created, reason=reason)
            report = live.mark_source_reconciled(data['target'], data['rows'])
        finally:
            live.close()
        return {**report, 'source_rows': len(data['rows']), 'applied': True, 'backup_file': backup.name}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Đối soát/chuyển nguồn Issue bằng snapshot riêng tư; không gọi Zalo hoặc Sheets.')
    parser.add_argument('--database', type=Path, required=True, help='Đúng file work-....issues.sqlite3 đang dùng.')
    parser.add_argument('--snapshot', type=Path, required=True, help='JSON target/rows xuất từ toàn bộ tab Issues đã dừng ghi.')
    parser.add_argument('--registrations', type=Path, help='File đăng ký cùng bot để tra mã thành viên cố định.')
    parser.add_argument('--apply', action='store_true', help='Ghi sau khi sao lưu; mặc định chỉ kiểm tra trên bản sao tạm.')
    parser.add_argument('--bot-stopped', action='store_true', help='Xác nhận đã dừng tất cả tiến trình bot ghi dữ liệu.')
    parser.add_argument('--source-complete', action='store_true', help='Xác nhận snapshot đủ toàn bộ nguồn Issue cũ, kể cả nguồn trống.')
    parser.add_argument('--reconcile-found', action='store_true', help='Đối soát sending/uncertain có dòng đích khớp đầy đủ.')
    parser.add_argument('--not-created', action='append', default=[], help='Mã sending/uncertain đã xác minh không được ghi; có thể lặp tùy chọn.')
    parser.add_argument('--reason', default='Đối soát snapshot nguồn đầy đủ khi bot đã dừng.')
    args = parser.parse_args(argv)
    try:
        result = import_snapshot(args.database, args.snapshot, registrations=args.registrations,
                                 apply=args.apply, bot_stopped=args.bot_stopped, source_complete=args.source_complete,
                                 reconcile_found=args.reconcile_found, not_created=args.not_created, reason=args.reason)
    except (IssueError, OSError, sqlite3.Error) as error:
        # OSError/SQLite messages may include private paths or row values.
        print(str(error) if isinstance(error, IssueError) else 'Không đọc/ghi được dữ liệu chuyển đổi; kiểm tra đường dẫn và quyền truy cập.')
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
