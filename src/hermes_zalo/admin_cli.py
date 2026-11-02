"""Offline Admin demo and local-operator account provisioning. No Zalo polling."""
import argparse
import asyncio
from dataclasses import replace
import getpass
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import urllib.error
import urllib.request

from .admin_auth import AdminAuth, AdminError, PERMISSIONS, ROLES
from .admin_config import AdminConfig
from .config import Settings, load_env
from .conversations import ConversationStore
from .events import IncomingMessage
from .work import WorkService, state_prefix
from .workload import WorkloadSettings, WorkloadError
from .sheets import SheetsSettings


def console_paths(home=None, auth_db=None):
    """Explicit bot home; never infer the operator's personal Hermes directory."""
    root = Path(home or os.environ.get('HERMES_HOME') or
                Path(__file__).resolve().parents[2] / '.runtime' / 'hermes-home').resolve()
    return root, Path(auth_db).resolve() if auth_db else root / 'console-admin.sqlite3'


def read_new_password():
    password = getpass.getpass('New password (at least 12 characters): ')
    if password != getpass.getpass('Repeat password: '):
        raise AdminError('The passwords do not match.')
    return password


def ensure_auth_file(auth_db):
    auth_db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(auth_db, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass  # Keep the permissions of an explicitly selected existing store.
    else:
        os.close(fd)


def live_preflight(auth_db):
    """Refuse a live console without an active account or with a published demo password."""
    auth = AdminAuth(auth_db)
    try:
        if not auth.has_account():
            raise AdminError('No active console account exists. Run: python -m hermes_zalo.admin_cli start '
                             '(it asks for the owner account on first run).')
        weak = auth.default_password_accounts()
    finally:
        auth.close()
    if weak:
        raise AdminError('Refusing to start: these accounts still use the public demo password: ' + ', '.join(weak) +
                         '. Reset each with create-console-account --username NAME --replace.')


def first_run_owner(auth_db, username=None, *, interactive=None):
    """Create the first system owner once; later runs only start the console."""
    if auth_db.is_file():
        auth = AdminAuth(auth_db)
        try:
            if auth.has_account('faculty'):
                return False
        finally:
            auth.close()
    if not (sys.stdin.isatty() if interactive is None else interactive):
        raise AdminError('No console owner exists. Run this command once in an interactive terminal, '
                         'or use create-console-account --username NAME first.')
    print('First run: create the console owner account (full permissions). The password input is hidden.', flush=True)
    username = username or input('Owner username [owner]: ').strip() or 'owner'
    password = read_new_password()
    ensure_auth_file(auth_db)
    auth = AdminAuth(auth_db)
    try:
        auth.create_account(username, password, sorted(PERMISSIONS), role='faculty', display_name='Console owner')
    finally:
        auth.close()
    print(f'Owner account "{username}" created in {auth_db}.', flush=True)
    return True


def healthcheck(values, timeout=4):
    """Exit status for container probes; 0 when no console is expected to listen."""
    def enabled(key):
        return str(values.get(key) or 'false').strip().lower() == 'true'
    if 'BOT_START_ENABLED' in values and not enabled('BOT_START_ENABLED'):
        return 0
    if not enabled('ZALO_ADMIN_ENABLED'):
        return 0
    try:
        port = AdminConfig.from_mapping(values).port
    except ValueError:
        return 1
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=timeout) as response:
            return 0 if response.status == 200 else 1
    except (OSError, urllib.error.URLError):
        return 1


async def serve_console(home, auth_db, port=None):
    """Standalone console: no gateway, polling, model calls or Lab service."""
    from .admin_assistant import AssistantAdmin
    from .admin_web import AdminRuntime
    values = dict(os.environ)
    # These flags validate the existing HTTP boundary only. No conversation
    # capture/store is created by this standalone read-only service.
    values.update(ZALO_ADMIN_ENABLED='true', ZALO_CONVERSATIONS_ENABLED='true')
    if port is not None:
        values['ZALO_ADMIN_PORT'] = str(port)
    config = AdminConfig.from_mapping(values)
    runtime = AdminRuntime(None, None, auth_db, config, mode='standalone', backup_values={})
    runtime.app._assistant = AssistantAdmin(None, None, mode='standalone', home=home)
    runtime.app.runtime_home = home
    address = config.public_url + '/' if config.public_url else f'http://127.0.0.1:{runtime.server.server_port}/'
    print(f'Hermes Agent Console: {address}', flush=True)
    print('Running independently. No bot is started and no model is called. Press Ctrl+C to stop.', flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runtime.close()


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
        REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại', TICKETS_CHAT_MODE='off',
        ZALO_ISSUES_MODE='managed')
    sheet_settings = SheetsSettings('demo-sheet-not-connected', root / 'no-key.json')
    source = WorkloadSettings.from_mapping(values, sheet_settings).source_key
    people = {}
    class Directory:
        source_key = source
        async def lookup(self, phone):
            if phone not in people:
                raise WorkloadError('Demo member not found.')
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
    for index, name in enumerate(('Member An', 'Member Binh', 'Member Chi', 'Member Dung'), 1):
        phone = '09000000' + str(index).zfill(2)
        people[phone] = SimpleNamespace(phone=phone, name=name, source_key=source,
            fingerprint='synthetic-' + str(index), name_unique=True)
        msg = IncomingMessage('demo-private-' + str(index), 'demo-user-' + str(index),
                              'register-' + str(index), 'PRIVATE', '/dangky ' + phone)
        record = work.registration.store.request(msg, people[phone], auto_approve=True)
        members.append(record)
        history.record_incoming(msg, record.member_id)
        history.record_outgoing(msg, 'Registration successful! ' + name, delivery_key='registration')
        for step, (question, answer) in enumerate((
            ('What can you help with?', 'I can research with sources, analyze data and help with the tasks you assign.'),
            ('I prefer short answers organized into clear points.', 'I will keep my replies short and clear.'),
            ('Hello, I need some help.', 'Hello! Send your request or the data you would like to analyze.')), 1):
            chat = replace(msg, message_id='demo-' + str(index) + '-' + str(step), text=question)
            item = history.record_incoming(chat, record.member_id)
            history.record_outgoing(chat, answer, delivery_key='answer')
            history.complete_turn(chat, is_chat=step > 1)
            if index == 1 and step == 2:
                history.save_memory(dict(content='Prefers short answers organized into clear points.',
                    member_id=record.member_id, scope='personal', source_message_id=item['id'], active=True), 'web:admin')
        history.mark_notice(msg)
    group = IncomingMessage('demo-lab', 'demo-user-1', 'group-demo', 'GROUP', 'Hello bot, our group needs help.')
    history.record_incoming(group, members[0].member_id)
    history.record_outgoing(group, 'Hello everyone! Mention @bot with your question.', delivery_key='answer')
    history.complete_turn(group)
    from .issue_demo import seed_issues
    seed_issues(work, members)
    from .admin_ticket_demo import seed_tickets
    seed_tickets(work, members, root)
    config = AdminConfig(enabled=True, conversations=True, port=port)
    runtime = AdminRuntime(work, history, prefix.with_suffix('.admin.sqlite3'), config, mode='demo')
    runtime.auth.create_account('admin', 'LabCoach-Demo-2026', PERMISSIONS, role='faculty', display_name='Demo owner')
    runtime.auth.create_account('hotro', 'LabCoach-Demo-2026', ['issues'], display_name='Demo support administrator')
    runtime.auth.create_account('quanly', 'LabCoach-Demo-2026', ['members'])
    runtime.auth.create_account('hoithoai', 'LabCoach-Demo-2026', ['conversations', 'memories'])
    print(f'Hermes Agent Console DEMO: http://127.0.0.1:{runtime.server.server_port}/', flush=True)
    print('Synthetic demo data. No Zalo, Sheets or model connection.\nSign in: admin / LabCoach-Demo-2026\nTry restricted accounts: hotro, quanly or hoithoai with the same demo password. Press Ctrl+C to stop.', flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await runtime.close()
        history.close()
        await work.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Hermes administration. Does not start Zalo polling.')
    commands = parser.add_subparsers(dest='action', required=True)
    sample = commands.add_parser('demo', help='Open the console with synthetic data; no keys required.')
    sample.add_argument('--port', type=int, default=8787)
    start = commands.add_parser('start', help='Recommended: create the owner on first run, then open the standalone console.')
    start.add_argument('--home', help='The bot data directory (HERMES_HOME), not its source directory.')
    start.add_argument('--auth-db', help='Administrator account database; defaults to <home>/console-admin.sqlite3.')
    start.add_argument('--port', type=int)
    start.add_argument('--username', help='Owner username used only on first run (default: owner).')
    commands.add_parser('healthcheck', help='Probe the local console /healthz; for Docker HEALTHCHECK.')
    serve = commands.add_parser('serve', help='Open the standalone Agent Console; no Zalo token required.')
    serve.add_argument('--home', help='The bot data directory (HERMES_HOME), not its source directory.')
    serve.add_argument('--auth-db', help='Existing administrator account database; defaults to <home>/console-admin.sqlite3.')
    serve.add_argument('--port', type=int)
    console_account = commands.add_parser('create-console-account', help='Create a standalone console account; password input is hidden.')
    console_account.add_argument('--home')
    console_account.add_argument('--auth-db')
    console_account.add_argument('--username', required=True)
    console_account.add_argument('--permissions', default='', help='Grant conversations explicitly to view runtime history.')
    console_account.add_argument('--role', choices=sorted(ROLES), default='faculty')
    console_account.add_argument('--replace', action='store_true')
    create = commands.add_parser('create-account', help='Create a separate web account; password input is hidden.')
    create.add_argument('--username')
    create.add_argument('--permissions', default='members,issues',
                        help='Separate permissions; defaults to members,issues. Grant conversations and memories explicitly.')
    create.add_argument('--replace', action='store_true')
    create.add_argument('--role', choices=sorted(ROLES), default='admin')
    create.add_argument('--display-name')
    faculty = commands.add_parser('designate-faculty', help='Explicitly designate an existing account as a system owner, preserving its content permissions.')
    faculty.add_argument('--username', required=True)
    grant = commands.add_parser('grant-permissions', help='Grant additional permissions to an account while keeping its password and role.')
    grant.add_argument('--username', required=True)
    grant.add_argument('--permissions', required=True, help='Comma-separated permissions to add, for example tickets.')
    args = parser.parse_args(argv)
    try:
        if args.action == 'demo':
            with tempfile.TemporaryDirectory(prefix='labcoach-admin-demo-') as folder:
                asyncio.run(demo(Path(folder), args.port))
            return 0
        if args.action == 'healthcheck':
            return healthcheck(os.environ)
        load_env(Path('.env'))
        if args.action in ('serve', 'start', 'create-console-account'):
            home, auth_db = console_paths(args.home, args.auth_db)
            if args.action in ('serve', 'start'):
                if args.action == 'start':
                    first_run_owner(auth_db, args.username)
                elif not auth_db.is_file():
                    raise AdminError('No console account exists. Run create-console-account with the same --home/--auth-db first.')
                live_preflight(auth_db)
                asyncio.run(serve_console(home, auth_db, args.port))
                return 0
            password = read_new_password()
            ensure_auth_file(auth_db)
            auth = AdminAuth(auth_db)
            try:
                auth.create_account(args.username, password,
                    [item.strip() for item in args.permissions.split(',') if item.strip()],
                    replace=args.replace, role=args.role)
            finally:
                auth.close()
            print('Console account created. Run serve with the same --home/--auth-db to open the console.')
            return 0
        settings = provisioning_settings(os.environ, Path.cwd(), windows=os.name == 'nt')
        if args.action == 'grant-permissions':
            auth = AdminAuth(state_prefix(settings).with_suffix('.admin.sqlite3'))
            try:
                auth.grant_permissions(args.username, [item.strip() for item in args.permissions.split(',') if item.strip()])
            finally:
                auth.close()
            print('Permissions granted on this machine or volume. Sign in again if the old session was revoked.')
            return 0
        if args.action == 'designate-faculty':
            auth = AdminAuth(state_prefix(settings).with_suffix('.admin.sqlite3'))
            try:
                auth.designate_faculty(args.username)
            finally:
                auth.close()
            print('System owner designated on this machine or volume. Previous sessions were revoked; please sign in again.')
            return 0
        username = args.username or input('Administrator username: ').strip()
        password = read_new_password()
        auth = AdminAuth(state_prefix(settings).with_suffix('.admin.sqlite3'))
        try:
            auth.create_account(username, password, [item.strip() for item in args.permissions.split(',') if item.strip()],
                                replace=args.replace, role=args.role, display_name=args.display_name)
        finally:
            auth.close()
        print('Administrator account saved on this machine or volume. The web console and bot have not been started.')
        return 0
    except KeyboardInterrupt:
        print('Administration console stopped.')
        return 0
    except (AdminError, ValueError) as error:
        print(str(error))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
