"""PowerShell-friendly local launcher and verified-account administration."""
import argparse
from contextlib import contextmanager, ExitStack
import getpass
import os
from pathlib import Path
import sys
import secrets

from ..config import ConfigError, load_env
from .providers import GoogleSheetsAssignments, TicketsSheetsSettings, TicketsSourceError
from .lab_provider import GoogleLabAssignments, LabSheetsSettings
from .service import DemoAssignments, TicketError, TicketService, TicketStore, digest
from .bindings import IdentityStore
from .web import TicketsServer
from .user_import import read_users_csv, match_members, import_users


@contextmanager
def process_lock(path):
    """OS-released lock; stale process files never require unsafe deletion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    acquired = False
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield
    except OSError:
        if not acquired:
            raise TicketError('Tickets đang chạy với dữ liệu này. Dừng cửa sổ cũ trước khi mở lại.') from None
        raise
    finally:
        if acquired:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def parser():
    p = argparse.ArgumentParser(description='LabCoach Tickets — quản trị liên kết và đổi ca')
    sub = p.add_subparsers(dest='command', required=True)
    for command in ('serve', 'add-user', 'revoke-user', 'bind-zalo', 'unbind-zalo', 'check-lab', 'import-zalo-users'):
        s = sub.add_parser(command)
        choices = ('demo', 'sheets_test') if command == 'serve' else ('lab_live', 'lab_live_test') if command in ('check-lab', 'import-zalo-users') else ('sheets_test', 'lab_live', 'lab_live_test')
        default = 'demo' if command == 'serve' else 'lab_live' if command in ('check-lab', 'import-zalo-users') else 'sheets_test'
        s.add_argument('--mode', choices=choices, default=default)
        s.add_argument('--env-file', type=Path, default=Path('.env'))
        s.add_argument('--data-dir', type=Path)
        if command in ('check-lab', 'import-zalo-users'):
            s.add_argument('--csv', type=Path, default=Path('data/tickets-users.csv'))
        elif command == 'serve':
            s.add_argument('--port', type=int, default=8765)
        else:
            if command != 'unbind-zalo':
                s.add_argument('--username', required=True)
            if command in ('bind-zalo', 'unbind-zalo'):
                s.add_argument('--user-id', required=True)
                s.add_argument('--chat-id', required=True)
            if command == 'add-user':
                s.add_argument('--name', required=True)
                s.add_argument('--member-id', default='')
                s.add_argument('--role', choices=('member', 'manager'), default='member')
                s.add_argument('--chat-only', action='store_true',
                               help='Tạo mật khẩu ngẫu nhiên không công bố; dùng xác minh Zalo thay đăng nhập web.')
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    args = parser().parse_args(argv)
    resources = ExitStack()
    try:
        load_env(args.env_file)
        from .runtime import data_directory
        if args.command in ('check-lab', 'import-zalo-users'):
            rows = read_users_csv(args.csv, require_ids=args.command == 'import-zalo-users')
            source = LabSheetsSettings.from_mapping(os.environ, test_mode=args.mode == 'lab_live_test')
            if source is None:
                raise TicketError('Chưa cấu hình nguồn lịch Lab hiện có.')
            if args.mode == 'lab_live_test' and (len(rows) != 2 or {row['member_name'] for row in rows} != set(source.test_members)):
                raise TicketError('CSV thử phải chứa đúng hai tên đã cấu hình cho lab_live_test.')
            provider = GoogleLabAssignments(source)
            resources.callback(provider.close)
            matched = match_members(rows, provider.inspect_members())
            if args.command == 'check-lab':
                print(f'Đã đối chiếu chính xác {len(matched)}/{len(rows)} tên với cột Lab Coach.')
                print('Kiểm tra chỉ đọc; chưa ghi lịch, chưa tạo tài khoản hoặc liên kết Zalo.')
                return 0
        path = data_directory(os.environ, args.data_dir) / (args.mode + '.sqlite3')
        # Administrative identity changes cannot race a pending network write.
        resources.enter_context(process_lock(path.with_suffix('.lock')))
        store = TicketStore(path)
        resources.callback(store.close)
        if args.command == 'revoke-user':
            IdentityStore(store)
            with store.transaction() as db:
                if not db.execute('SELECT 1 FROM ticket_accounts WHERE id=?', (args.username,)).fetchone():
                    raise TicketError('Không tìm thấy tài khoản.')
                db.execute('UPDATE ticket_accounts SET active=0 WHERE id=?', (args.username,))
                db.execute('DELETE FROM ticket_sessions WHERE account=?', (args.username,))
                db.execute('DELETE FROM ticket_zalo_bindings WHERE account=?', (args.username,))
            print('Đã thu hồi quyền và phiên đăng nhập. Yêu cầu đang chờ sẽ được kiểm tra quyền lại.')
            return 0
        if args.command in ('bind-zalo', 'unbind-zalo', 'import-zalo-users'):
            token = os.getenv('ZALO_BOT_TOKEN', '').strip()
            if not token or token.startswith('YOUR_') or any(c.isspace() or c in '/?#\\' for c in token):
                raise TicketError('Cần Bot Token hợp lệ trong cấu hình trên máy; không gửi token vào chat.')
            scope = digest(token)
            identities = IdentityStore(store)
            if args.command == 'import-zalo-users':
                allowed = {value.strip() for value in os.getenv('ZALO_ALLOWED_USERS', '').split(',') if value.strip()}
                missing = sum(row['user_id'] not in allowed for row in matched)
                if missing:
                    raise TicketError(f'Còn {missing} người trong CSV chưa được cấp quyền dùng bot; kiểm tra ZALO_ALLOWED_USERS trước khi nhập.')
                if args.mode == 'lab_live_test':
                    expected = {row['member_id']: row['member_name'] for row in matched}
                    existing = store.db.execute('SELECT name,role,member_id FROM ticket_accounts WHERE active=1').fetchall()
                    if any(row['role'] != 'member' or row['name'] != expected.get(row['member_id']) for row in existing):
                        raise TicketError('Dữ liệu thử có tài khoản ngoài đúng hai tên đã xác minh; cần quản trị kiểm tra trước khi nhập.')
                added, unchanged = import_users(store, scope, matched)
                print(f'Đã tạo {added} liên kết; giữ nguyên {unchanged} liên kết đúng đã có.')
                print('Chỉ cập nhật dữ liệu trên máy; chưa ghi lịch. Quyền dùng bot cần quản trị cấp riêng.')
                return 0
            if args.command == 'unbind-zalo':
                identities.revoke(scope, args.user_id, args.chat_id)
                print('Đã thu hồi liên kết Zalo của đúng người và cuộc trò chuyện riêng.')
                return 0
        if args.mode == 'demo':
            provider = DemoAssignments(store)
        elif args.mode in ('lab_live', 'lab_live_test'):
            settings = LabSheetsSettings.from_mapping(os.environ, test_mode=args.mode == 'lab_live_test')
            if settings is None:
                raise TicketError('Chưa cấu hình nguồn lịch Lab hiện có.')
            provider = GoogleLabAssignments(settings)
        else:
            settings = TicketsSheetsSettings.from_mapping(os.environ)
            if settings is None:
                raise TicketError('Chưa cấu hình bảng thử Tickets; xem docs/tickets.md.')
            provider = GoogleSheetsAssignments(settings)
        resources.callback(provider.close)
        if args.command == 'bind-zalo':
            account = store.account(args.username)
            if not account:
                raise TicketError('Tài khoản chưa được tạo hoặc đã thu hồi; hãy cấp tài khoản trước.')
            members = provider.inspect_members() if args.mode in ('lab_live', 'lab_live_test') else provider.list_assignments()
            if args.mode == 'lab_live_test' and (account['role'] != 'member' or account['name'] not in provider.settings.test_members):
                raise TicketError('Chỉ hai tài khoản thành viên đã chỉ định được liên kết trong lab_live_test.')
            if account['member_id'] and account['member_id'] not in {item['member_id'] for item in members}:
                raise TicketError('Mã thành viên không còn trong lịch đã cấu hình; kiểm tra trước khi liên kết Zalo.')
            identities.bind(scope, args.user_id, args.chat_id, args.username)
            print('Đã liên kết tài khoản với đúng tin nhắn riêng. Cấp ZALO_ALLOWED_USERS riêng nếu chưa có.')
            return 0
        if args.command == 'add-user':
            if args.member_id:
                assignments = provider.inspect_members() if args.mode in ('lab_live', 'lab_live_test') else provider.list_assignments()
                if args.member_id not in {a['member_id'] for a in assignments}:
                    raise TicketError('Mã thành viên chưa có trong lịch đã cấu hình; cần đối chiếu trước khi cấp quyền.')
            elif args.role == 'member':
                raise TicketError('Thành viên cần --member-id đã được quản trị xác minh.')
            if args.mode == 'lab_live_test' and (args.role != 'member' or args.name not in provider.settings.test_members
                    or not any(item['member_id'] == args.member_id and item['member_name'] == args.name for item in assignments)):
                raise TicketError('lab_live_test chỉ cấp tài khoản cho đúng hai tên và mã thành viên đã chỉ định.')
            if args.chat_only:
                password = secrets.token_urlsafe(48)
            else:
                password = getpass.getpass('Nhập mật khẩu mới (ít nhất 12 ký tự): ')
                if password != getpass.getpass('Nhập lại mật khẩu: '):
                    raise TicketError('Hai lần nhập mật khẩu chưa khớp.')
            store.add_account(args.username, args.name, args.member_id, args.role, password)
            # Recreating/reassigning an account must not resurrect an old DM
            # identity or confirmation. A fresh administrative bind is required.
            IdentityStore(store)
            with store.transaction() as db:
                db.execute('DELETE FROM ticket_zalo_bindings WHERE account=?', (args.username,))
            print('Đã cấp tài khoản. Dùng bind-zalo để xác minh liên kết Zalo mới; không gửi mật khẩu trong chat.')
            return 0
        if not 1024 <= args.port <= 65535:
            raise TicketError('Cổng cần nằm trong khoảng 1024–65535.')
        flag = os.getenv('TICKETS_REQUIRE_MANAGER', 'true').lower()
        if flag not in ('true', 'false'):
            raise TicketError('TICKETS_REQUIRE_MANAGER cần là true hoặc false.')
        service = TicketService(store, provider, mode=args.mode, require_manager=flag == 'true')
        with TicketsServer(args.port, service) as server:
            print('LabCoach Tickets — ' + ('MÔ PHỎNG, không gọi Google/Zalo.' if args.mode == 'demo' else 'BẢNG THỬ, tài khoản được quản trị xác minh.'))
            print(f'Mở trên máy này: http://127.0.0.1:{args.port}', flush=True)
            print('Dừng bằng Ctrl+C. Chưa mở cho thành viên trên máy khác.', flush=True)
            server.serve_forever(poll_interval=0.3)
        return 0
    except (TicketError, TicketsSourceError, ConfigError) as exc:
        print(str(exc))
        return 2
    except KeyboardInterrupt:
        print('\nĐã dừng Tickets; yêu cầu đã lưu được giữ lại.')
        return 0
    except OSError:
        print('Không mở được dữ liệu hoặc cổng chạy Tickets. Kiểm tra quyền thư mục và cửa sổ đang chạy.')
        return 2
    finally:
        resources.close()


if __name__ == '__main__':
    raise SystemExit(main())
