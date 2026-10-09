"""Verified DM identity and opt-in runtime tests; no Zalo/Google network."""
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hermes_zalo.config import Settings
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.cli import main, process_lock
from hermes_zalo.tickets.providers import TicketsSourceError
from hermes_zalo.tickets.runtime import data_directory, open_chat
from hermes_zalo.tickets.service import DemoAssignments, TicketError, TicketStore, digest


SCOPE = digest('a-fake-token-not-used-in-network')


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = TicketStore(Path(self.directory.name) / 'identities.db')
        self.addCleanup(self.store.close)
        DemoAssignments(self.store)
        self.identities = IdentityStore(self.store)

    def test_exact_bot_user_and_private_chat_scope_is_required(self):
        bound = self.identities.bind(SCOPE, 'zalo-a', 'private-a', 'demo-a')
        self.assertEqual('coach-a', bound['member_id'])
        self.assertNotIn('password', bound)
        self.assertTrue(bound['_binding'])
        for scope, user, chat in ((digest('other-bot'), 'zalo-a', 'private-a'),
                                  (SCOPE, 'zalo-b', 'private-a'), (SCOPE, 'zalo-a', 'private-b')):
            self.assertIsNone(self.identities.resolve(scope, user, chat))

    def test_rebinding_changes_revision_and_revoke_removes_access(self):
        first = self.identities.bind(SCOPE, 'zalo-a', 'private-a', 'demo-a')
        second = self.identities.bind(SCOPE, 'zalo-a', 'private-a', 'demo-a')
        self.assertNotEqual(first['_binding'], second['_binding'])
        self.identities.revoke(SCOPE, 'zalo-a', 'wrong-chat')
        self.assertIsNotNone(self.identities.resolve(SCOPE, 'zalo-a', 'private-a'))
        self.identities.revoke(SCOPE, 'zalo-a', 'private-a')
        self.assertIsNone(self.identities.resolve(SCOPE, 'zalo-a', 'private-a'))

    def test_one_bot_identity_cannot_take_another_accounts_binding(self):
        self.identities.bind(SCOPE, 'zalo-a', 'private-a', 'demo-a')
        for user, chat, account in (('zalo-b', 'private-b', 'demo-a'),
                                     ('zalo-a', 'private-b', 'demo-b'),
                                     ('zalo-b', 'private-a', 'demo-b')):
            with self.assertRaises(TicketError):
                self.identities.bind(SCOPE, user, chat, account)
        self.assertEqual('demo-a', self.identities.resolve(SCOPE, 'zalo-a', 'private-a')['id'])

    def test_changed_member_role_or_inactive_account_invalidates_link(self):
        self.identities.bind(SCOPE, 'zalo-a', 'private-a', 'demo-a')
        for statement in ("UPDATE ticket_accounts SET member_id='other-person' WHERE id='demo-a'",
                          "UPDATE ticket_accounts SET member_id='coach-a',role='manager' WHERE id='demo-a'",
                          "UPDATE ticket_accounts SET role='member',active=0 WHERE id='demo-a'"):
            with self.store.transaction() as db:
                db.execute(statement)
            self.assertIsNone(self.identities.resolve(SCOPE, 'zalo-a', 'private-a'))
            self.assertEqual([], self.identities.linked_accounts(SCOPE))

    def test_invalid_roles_unverified_accounts_and_invalid_scope_rejected(self):
        with self.store.transaction() as db:
            db.execute("UPDATE ticket_accounts SET role='owner' WHERE id='demo-a'")
        for scope, user, chat, account in ((SCOPE, 'a', 'dm', 'demo-a'), (SCOPE, 'a', 'dm', 'missing'),
                                          ('raw-secret-token', 'a', 'dm', 'demo-b'),
                                          (SCOPE, 'has space', 'dm', 'demo-b')):
            with self.assertRaises(TicketError):
                self.identities.bind(scope, user, chat, account)

    def test_opaque_non_ascii_ids_are_exact_and_controls_are_rejected(self):
        user, chat = 'nguời-dùng/+==', 'hội-thoại/=?#'
        linked = self.identities.bind(SCOPE, user, chat, 'demo-a')
        self.assertEqual('demo-a', linked['id'])
        self.assertEqual(linked, self.identities.resolve(SCOPE, user, chat))
        self.assertIsNone(self.identities.resolve(SCOPE, user.upper(), chat))
        for bad in ('', 'a' * 201, 'a\tb', 'a\x00b', 'a\x7fb', 'a\u200bb', ' a', 'a\u0085b'):
            with self.subTest(bad=repr(bad)), self.assertRaises(TicketError):
                self.identities.bind(SCOPE, bad, 'other-dm', 'demo-b')

    def test_linked_accounts_only_returns_active_bindings_for_same_bot(self):
        self.identities.bind(SCOPE, 'a', 'dm-a', 'demo-a')
        self.identities.bind(digest('other-bot'), 'b', 'dm-b', 'demo-b')
        self.identities.bind(SCOPE, 'm', 'dm-m', 'demo-manager')
        result = self.identities.linked_accounts(SCOPE)
        self.assertEqual({'demo-a', 'demo-manager'}, {a['id'] for a in result})
        self.assertTrue(all('_binding' in a for a in result))


class FakeProvider:
    source_key = 'fake:runtime:test'
    def __init__(self, settings):
        self.closed = False
    def list_assignments(self):
        return [{'member_id': 'member-a'}]
    def close(self):
        self.closed = True


class FakeChat:
    def __init__(self, service, identities, *, bot_scope):
        self.service, self.identities, self.bot_scope = service, identities, bot_scope
        self.closed = False
    def close(self):
        self.closed = True


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Settings(token='runtime-fake-token', allowed_users=frozenset({'user'}))
        self.values = dict(TICKETS_CHAT_MODE='sheets_test', TICKETS_DATA_DIR=self.directory.name,
                           ZALO_SHEETS_ID='existing_live_sheet', TICKETS_TEST_SHEETS_ID='isolated_test_sheet',
                           TICKETS_TEST_GOOGLE_CREDENTIALS='fake-local-only.json',
                           TICKETS_TEST_WRITE_ENABLED='true', TICKETS_TEST_EXCLUSIVE_WRITER='true')
        self.addCleanup(patch.stopall)
        patch('hermes_zalo.tickets.runtime.GoogleSheetsAssignments', FakeProvider).start()
        patch.dict('sys.modules', {'hermes_zalo.tickets.chat': SimpleNamespace(TicketsChat=FakeChat)}).start()

    def test_off_never_opens_data_and_demo_is_rejected_on_real_gateway(self):
        with patch('hermes_zalo.tickets.runtime.TicketStore') as store:
            self.assertIsNone(open_chat(self.settings, {}))
            with self.assertRaises(TicketError):
                open_chat(self.settings, self.values | {'TICKETS_CHAT_MODE': 'demo'})
            store.assert_not_called()

    def test_write_guard_rejected_before_database_or_recovery(self):
        with patch('hermes_zalo.tickets.runtime.TicketStore') as store:
            with self.assertRaises(TicketsSourceError):
                open_chat(self.settings, self.values | {'TICKETS_TEST_EXCLUSIVE_WRITER': 'false'})
            store.assert_not_called()

    def test_lifetime_lock_and_cleanup_and_scope_hash(self):
        chat = open_chat(self.settings, self.values)
        try:
            self.assertEqual(digest(self.settings.token), chat.bot_scope)
            with patch('hermes_zalo.tickets.runtime.TicketStore') as store:
                with self.assertRaises(TicketError):
                    open_chat(self.settings, self.values)
                store.assert_not_called()
        finally:
            chat.close()
        self.assertTrue(chat.closed)
        self.assertTrue(chat.service.provider.closed)
        chat.close()
        with process_lock(Path(self.directory.name) / 'sheets_test.lock'):
            pass

    def test_initialization_failure_releases_lock(self):
        with patch('hermes_zalo.tickets.runtime.TicketService', side_effect=TicketError('safe failure')):
            with self.assertRaises(TicketError):
                open_chat(self.settings, self.values)
        with process_lock(Path(self.directory.name) / 'sheets_test.lock'):
            pass

    def test_relative_data_directory_is_project_relative(self):
        self.assertEqual((Path.cwd() / '.runtime/tickets').resolve(), data_directory({}))
        project = Path(self.directory.name)
        values = {'TICKETS_PROJECT_ROOT': str(project), 'TICKETS_DATA_DIR': 'private/tickets'}
        with patch('hermes_zalo.tickets.runtime.Path.cwd', return_value=project / 'copied-plugin'):
            self.assertEqual((project / 'private/tickets').resolve(), data_directory(values))


class ChatAdminCLITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.values = dict(TICKETS_DATA_DIR=self.directory.name, ZALO_BOT_TOKEN='cli-fake-secret-token',
                           ZALO_SHEETS_ID='existing_live_sheet', TICKETS_TEST_SHEETS_ID='isolated_test_sheet',
                           TICKETS_TEST_GOOGLE_CREDENTIALS='fake-local-only.json')
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.values, clear=True).start()
        patch('hermes_zalo.tickets.cli.load_env').start()
        patch('hermes_zalo.tickets.cli.GoogleSheetsAssignments', FakeProvider).start()

    def invoke(self, args):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(args)
        self.assertNotIn(self.values['ZALO_BOT_TOKEN'], output.getvalue())
        return result

    def test_chat_only_account_bind_and_unbind_do_not_prompt_for_password(self):
        with patch('hermes_zalo.tickets.cli.getpass.getpass', side_effect=AssertionError('No password prompt')):
            result = self.invoke(['add-user', '--username', 'coach-a', '--name', 'Coach A',
                                  '--member-id', 'member-a', '--chat-only'])
        self.assertEqual(0, result)
        self.assertEqual(0, self.invoke(['bind-zalo', '--username', 'coach-a', '--user-id', 'zalo-a', '--chat-id', 'dm-a']))
        store = TicketStore(Path(self.directory.name) / 'sheets_test.sqlite3')
        try:
            identity = IdentityStore(store)
            self.assertEqual('coach-a', identity.resolve(digest(self.values['ZALO_BOT_TOKEN']), 'zalo-a', 'dm-a')['id'])
        finally:
            store.close()
        self.assertEqual(0, self.invoke(['unbind-zalo', '--user-id', 'zalo-a', '--chat-id', 'dm-a']))

    def test_admin_mutation_is_blocked_while_runtime_owns_store(self):
        with process_lock(Path(self.directory.name) / 'sheets_test.lock'):
            self.assertEqual(2, self.invoke(['add-user', '--username', 'coach-a', '--name', 'A',
                                           '--member-id', 'member-a', '--chat-only']))
        self.assertFalse((Path(self.directory.name) / 'sheets_test.sqlite3').exists())

    def test_missing_member_cannot_receive_verified_account(self):
        self.assertEqual(2, self.invoke(['add-user', '--username', 'unknown', '--name', 'Unknown',
                                       '--member-id', 'not-in-test-roster', '--chat-only']))

    def test_recreated_account_does_not_resurrect_old_zalo_binding(self):
        args = ['add-user', '--username', 'coach-a', '--name', 'A', '--member-id', 'member-a', '--chat-only']
        self.assertEqual(0, self.invoke(args))
        self.assertEqual(0, self.invoke(['bind-zalo', '--username', 'coach-a', '--user-id', 'zalo-a', '--chat-id', 'dm-a']))
        self.assertEqual(0, self.invoke(['revoke-user', '--username', 'coach-a']))
        self.assertEqual(0, self.invoke(args))
        store = TicketStore(Path(self.directory.name) / 'sheets_test.sqlite3')
        try:
            self.assertIsNone(IdentityStore(store).resolve(digest(self.values['ZALO_BOT_TOKEN']), 'zalo-a', 'dm-a'))
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
