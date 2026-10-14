"""Opt-in private chat runtime; no demo identity on the real Zalo gateway."""
from contextlib import ExitStack
from pathlib import Path

from .bindings import IdentityStore
from .cli import process_lock
from .providers import GoogleSheetsAssignments, TicketsSheetsSettings
from .lab_provider import GoogleLabAssignments, LabSheetsSettings
from .service import TicketError, TicketService, TicketStore, digest


def data_directory(values, explicit=None):
    value = explicit if explicit is not None else values.get('TICKETS_DATA_DIR') or '.runtime/tickets'
    path = Path(value)
    # Hermes copies this package into its plugin directory. Source-file parents
    # therefore are not the project root. The launcher pins it explicitly.
    root = Path(values.get('TICKETS_PROJECT_ROOT') or Path.cwd()).resolve()
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


class ManagedChat:
    def __init__(self, chat, resources):
        self.chat = chat
        self.resources = resources
        self.closed = False

    def __getattr__(self, name):
        return getattr(self.chat, name)

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.chat.close()
            finally:
                self.resources.close()


def open_chat(settings, values):
    mode = str(values.get('TICKETS_CHAT_MODE') or 'off').strip().lower()
    if mode == 'off':
        return None
    if mode not in ('sheets_test', 'lab_live', 'lab_live_test'):
        raise TicketError('TICKETS_CHAT_MODE chỉ nhận off, sheets_test, lab_live hoặc lab_live_test; không dùng danh tính demo trên Zalo thật.')
    lab = mode in ('lab_live', 'lab_live_test')
    source = LabSheetsSettings.from_mapping(values, test_mode=mode == 'lab_live_test') if lab else TicketsSheetsSettings.from_mapping(values)
    if source is None:
        raise TicketError('Chưa cấu hình bảng lịch cho Tickets trong tin nhắn riêng.')
    flag = str(values.get('TICKETS_REQUIRE_MANAGER') or '').strip().lower()
    flag = flag or ('false' if mode == 'lab_live_test' else 'true')
    if flag not in ('true', 'false'):
        raise TicketError('TICKETS_REQUIRE_MANAGER cần là true hoặc false.')
    if mode == 'lab_live_test' and flag != 'false':
        raise TicketError('lab_live_test chỉ dùng xác nhận hai người; TICKETS_REQUIRE_MANAGER phải là false.')
    token = getattr(settings, 'token', None)
    if not isinstance(token, str) or not token:
        raise TicketError('Chưa cấu hình bot cho Tickets.')
    path = data_directory(values) / (mode + '.sqlite3')
    with ExitStack() as resources:
        # Recovery must never run while another gateway/web server holds this DB.
        resources.enter_context(process_lock(path.with_suffix('.lock')))
        store = TicketStore(path)
        resources.callback(store.close)
        provider = (GoogleLabAssignments if lab else GoogleSheetsAssignments)(source)
        resources.callback(provider.close)
        service = TicketService(store, provider, mode=mode, require_manager=flag == 'true')
        from .chat import TicketsChat
        chat = TicketsChat(service, IdentityStore(store), bot_scope=digest(token))
        return ManagedChat(chat, resources.pop_all())
