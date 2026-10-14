"""Synthetic shift-review fixtures; the provider never uses Google or Zalo."""
from contextlib import ExitStack
import json
from types import SimpleNamespace

from .tickets.bindings import IdentityStore
from .tickets.chat import TicketsChat
from .tickets.runtime import ManagedChat
from .tickets.service import DemoAssignments, TicketService, TicketStore, digest, packed


def seed_tickets(work, members, root):
    store = TicketStore(root / 'admin-demo-tickets.sqlite3')

    class OfflineAssignments(DemoAssignments):
        settings = SimpleNamespace(spreadsheet_id=work.sheets.settings.spreadsheet_id)

        def inspect_members(self):
            return [dict(member_id=row['member_id'], member_name=row['name'])
                    for row in self.store.db.execute(
                        "SELECT member_id,name FROM ticket_accounts WHERE role='member' AND active=1")]

    provider = OfflineAssignments(store)
    with store.transaction() as db:
        for letter, record in zip(('a', 'b', 'c'), members):
            db.execute('UPDATE ticket_accounts SET member_id=?,name=? WHERE id=?',
                       (record.member_id, record.name, 'demo-' + letter))
            for row in db.execute('SELECT id,body FROM demo_assignments').fetchall():
                item = json.loads(row['body'])
                if item['member_id'] == 'coach-' + letter:
                    item.update(member_id=record.member_id, member_name=record.name)
                    db.execute('UPDATE demo_assignments SET body=? WHERE id=?', (packed(item), row['id']))
    # Exercise the real registered-member path with an entirely offline provider.
    service = TicketService(store, provider, mode='lab_live', require_manager=True)
    identities = IdentityStore(store)
    scope = digest(work.settings.token)
    for letter, record in zip(('a', 'b', 'c'), members):
        identities.bind(scope, record.user_id, record.chat_id, 'demo-' + letter)
    with store.transaction() as db:
        db.execute('CREATE TABLE ticket_registration_links (bot TEXT NOT NULL,member_id TEXT NOT NULL,'
                   'registration_id TEXT NOT NULL,account TEXT NOT NULL,PRIMARY KEY(bot,member_id))')
        for letter, record in zip(('a', 'b', 'c'), members):
            db.execute('INSERT INTO ticket_registration_links VALUES (?,?,?,?)',
                       (scope, record.member_id, record.id, 'demo-' + letter))
    chat = TicketsChat(service, identities, bot_scope=scope)
    resources = ExitStack()
    resources.callback(store.close)
    resources.callback(provider.close)
    work._tickets_chat = ManagedChat(chat, resources)
    work._tickets_initialized = work._tickets_bootstrapped = True
    a, b = store.account('demo-a'), store.account('demo-b')
    preview = service.preview(a, 'CA-11', 'CA-21')
    ticket = service.create(a, preview['preview_token'], 'Đổi ca do trùng lịch học — dữ liệu mô phỏng.',
                            'admin-demo-pending-manager')
    service.action(b, ticket['id'], 'accept', ticket['version'])
    preview = service.preview(a, 'CA-12', 'CA-31')
    service.create(a, preview['preview_token'], 'Đang chờ người đổi cùng xác nhận — dữ liệu mô phỏng.',
                   'admin-demo-pending-peer')
