"""Offline Admin demo and local-operator account provisioning. No Zalo polling."""
import argparse
import asyncio
from dataclasses import replace
import getpass
from pathlib import Path
import tempfile
from types import SimpleNamespace

from .admin_auth import AdminAuth, AdminError, PERMISSIONS
from .admin_config import AdminConfig
from .config import Settings, load_env
from .conversations import ConversationStore
from .events import IncomingMessage
from .work import WorkService, state_prefix
from .workload import WorkloadSettings, WorkloadError
from .sheets import SheetsSettings


def provisioning_settings(values, root, *, windows):
    values = dict(values)
    # scripts/launch.py deliberately overrides this path on Windows. Account
    # creation must target that same bot-scoped DB, even if .env says data/.
    if windows and (root / 'scripts' / 'launch.py').is_file():
        values['ZALO_STATE_DIR'] = str(root / '.runtime' / 'hermes-home' / 'data' / 'zalo')
    return Settings.from_mapping(values)


async def demo(root, port):
    """Every identity and message here is synthetic and confined to root."""
    from .admin_web import AdminRuntime
    settings = Settings('admin-demo-synthetic', frozenset({'demo-operator'}), frozenset({'demo-lab'}),
        state_dir=root, knowledge_dir=root / 'knowledge')
    values = dict(REGISTRATION_ENABLED='true', REGISTRATION_AUTO_APPROVE='true',
        REGISTRATION_ADMIN_USERS='demo-operator', REGISTRATION_WORKLOAD_TAB='Demo Workload',
        REGISTRATION_WORKLOAD_NAME_COLUMN='A', REGISTRATION_WORKLOAD_PHONE_COLUMN='B',
        REGISTRATION_WORKLOAD_HEADER_ROW='1', REGISTRATION_WORKLOAD_NAME_HEADER='Họ tên',
        REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại', TICKETS_CHAT_MODE='off')
    sheet_settings = SheetsSettings('demo-sheet-not-connected', root / 'no-key.json')
    source = WorkloadSettings.from_mapping(values, sheet_settings).source_key
    people = {}
    class Directory:
        source_key = source
        async def lookup(self, phone):
            if phone not in people:
                raise WorkloadError('Không có thành viên mẫu.')
            return people[phone]
    class OfflineSheets:
        settings = sheet_settings
        async def close(self):
            pass
    work = WorkService(settings, environ=values, registration_directory=Directory(), sheets_client=OfflineSheets(),
                       tickets_chat=None)
    prefix = state_prefix(settings)
    history = ConversationStore(prefix.with_suffix('.conversations.sqlite3'))
    members = []
    for index, name in enumerate(('Thành viên An', 'Thành viên Bình', 'Thành viên Chi', 'Thành viên Dũng'), 1):
        phone = '09000000' + str(index).zfill(2)
        people[phone] = SimpleNamespace(phone=phone, name=name, source_key=source,
            fingerprint='synthetic-' + str(index), name_unique=True)
        msg = IncomingMessage('demo-private-' + str(index), 'demo-user-' + str(index),
                              'register-' + str(index), 'PRIVATE', '/dangky ' + phone)
        record = work.registration.store.request(msg, people[phone], auto_approve=True)
        members.append(record)
        history.record_incoming(msg, record.member_id)
        history.record_outgoing(msg, '✅ Đăng ký thành công! ' + name, delivery_key='registration')
        for step, (question, answer) in enumerate((
            ('/menu', 'Bạn có thể tra lịch và đổi ca qua /doica.'),
            ('Mình thích câu trả lời ngắn, chia thành từng ý.', 'Mình sẽ trả lời ngắn và rõ ý nhé.'),
            ('Xin chào, mình cần hỗ trợ.', 'Chào bạn! Bạn muốn tra lịch hay cần hỗ trợ việc gì?')), 1):
            chat = replace(msg, message_id='demo-' + str(index) + '-' + str(step), text=question)
            item = history.record_incoming(chat, record.member_id)
            history.record_outgoing(chat, answer, delivery_key='answer')
            history.complete_turn(chat, is_chat=step > 1)
            if index == 1 and step == 2:
                history.save_memory(dict(content='Thích câu trả lời ngắn và chia thành từng ý.',
                    member_id=record.member_id, scope='personal', source_message_id=item['id'], active=True), 'web:admin')
        history.mark_notice(msg)
    group = IncomingMessage('demo-lab', 'demo-user-1', 'group-demo', 'GROUP', 'Chào bot, nhóm mình cần hỗ trợ.')
    history.record_incoming(group, members[0].member_id)
    history.record_outgoing(group, 'Chào nhóm! Hãy @bot kèm câu hỏi nhé.', delivery_key='answer')
    history.complete_turn(group)
    config = AdminConfig(enabled=True, conversations=True, port=port)
    runtime = AdminRuntime(work, history, prefix.with_suffix('.admin.sqlite3'), config, mode='demo')
    runtime.auth.create_account('admin', 'LabCoach-Demo-2026', PERMISSIONS)
    runtime.auth.create_account('quanly', 'LabCoach-Demo-2026', ['members'])
    runtime.auth.create_account('hoithoai', 'LabCoach-Demo-2026', ['conversations', 'memories'])
    print(f'LabCoach Admin DEMO: http://127.0.0.1:{runtime.server.server_port}/', flush=True)
    print('Dữ liệu mô phỏng; không kết nối Zalo, Sheets hoặc model.\n'
          'Đăng nhập: admin / LabCoach-Demo-2026\n'
          'Thử quyền riêng: quanly hoặc hoithoai, cùng mật khẩu demo. Ctrl+C để dừng.', flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runtime.close()
        history.close()
        await work.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Quản trị LabCoach; không khởi động nhận tin Zalo.')
    commands = parser.add_subparsers(dest='action', required=True)
    sample = commands.add_parser('demo', help='Mở giao diện bằng dữ liệu mẫu, không cần khóa.')
    sample.add_argument('--port', type=int, default=8787)
    create = commands.add_parser('create-account', help='Tạo tài khoản web riêng; mật khẩu nhập ẩn.')
    create.add_argument('--username')
    create.add_argument('--permissions', default='members,conversations,memories')
    create.add_argument('--replace', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action == 'demo':
            with tempfile.TemporaryDirectory(prefix='labcoach-admin-demo-') as folder:
                asyncio.run(demo(Path(folder), args.port))
            return 0
        load_env(Path('.env'))
        import os
        settings = provisioning_settings(os.environ, Path.cwd(), windows=os.name == 'nt')
        username = args.username or input('Tên đăng nhập Admin: ').strip()
        password = getpass.getpass('Mật khẩu mới (ít nhất 12 ký tự): ')
        if password != getpass.getpass('Nhập lại mật khẩu: '):
            raise AdminError('Hai lần nhập mật khẩu chưa giống nhau.')
        auth = AdminAuth(state_prefix(settings).with_suffix('.admin.sqlite3'))
        try:
            auth.create_account(username, password, args.permissions.split(','), replace=args.replace)
        finally:
            auth.close()
        print('Đã lưu tài khoản Admin trên máy/volume này. Chưa bật trang web hoặc bot.')
        return 0
    except KeyboardInterrupt:
        print('Đã dừng Admin demo.')
        return 0
    except (AdminError, ValueError) as error:
        print(str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
