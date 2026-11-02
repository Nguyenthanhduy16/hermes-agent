import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from hermes_zalo.admin_auth import AdminError
from hermes_zalo.admin_workspace import WorkspaceAdmin, MAX_PREVIEW


class WorkspaceFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.workspace = self.home / 'workspace'
        self.workspace.mkdir(parents=True)
        self.service = WorkspaceAdmin(self.home, environ={})

    def test_lists_and_previews_only_workspace_artifacts_without_modifying_files(self):
        (self.workspace / 'reports').mkdir()
        report = self.workspace / 'reports' / 'sales.csv'
        report.write_text('month,total\nJan,100\nFeb,200\n')
        (self.workspace / '.env').write_text('TOP_SECRET=do-not-read')
        (self.workspace / 'credentials.json').write_text('private')
        before = {str(p.relative_to(self.home)): (p.stat().st_size, p.stat().st_mtime_ns)
                  for p in self.home.rglob('*') if p.is_file()}
        listing = self.service.files()
        self.assertEqual(['reports'], [item['name'] for item in listing['items']])
        self.assertEqual('reports/sales.csv', self.service.files('reports')['items'][0]['path'])
        preview = self.service.files('reports/sales.csv')
        self.assertIn('Jan,100', preview['content'])
        self.assertEqual('reports', preview['parent'])
        self.assertFalse(preview['truncated'])
        self.assertEqual(before, {str(p.relative_to(self.home)): (p.stat().st_size, p.stat().st_mtime_ns)
                                 for p in self.home.rglob('*') if p.is_file()})

    def test_rejects_traversal_private_names_symlinks_and_special_files(self):
        for path in ('../outside.txt', '/etc/passwd', '.env', 'credentials.json', 'a\\b',
                     'http://evil/file.txt', 'a/../report.md', 'a\0b'):
            with self.assertRaises(AdminError, msg=path):
                self.service.files(path)
        outside = Path(self.temp.name) / 'outside.txt'
        outside.write_text('private-outside')
        (self.workspace / 'link.txt').symlink_to(outside)
        (self.workspace / 'directory').symlink_to(outside.parent, target_is_directory=True)
        self.assertEqual([], self.service.files()['items'])
        for path in ('link.txt', 'directory/outside.txt'):
            with self.assertRaises(AdminError):
                self.service.files(path)
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.workspace / 'fifo.txt')
            with self.assertRaises(AdminError):
                self.service.files('fifo.txt')

    def test_preview_is_bounded_and_secret_values_are_hidden(self):
        (self.workspace / 'notes.md').write_text('api_key=synthetic-sensitive-value\n' + 'x' * (MAX_PREVIEW + 100))
        result = self.service.files('notes.md')
        self.assertTrue(result['truncated'])
        self.assertNotIn('synthetic-sensitive-value', result['content'])
        self.assertLessEqual(len(result['content']), MAX_PREVIEW)
        (self.workspace / 'photo.txt').write_bytes(b'first\0second')
        with self.assertRaises(AdminError) as context:
            self.service.files('photo.txt')
        self.assertEqual(415, context.exception.status)

    def test_demo_cannot_touch_real_workspace_and_missing_source_stays_missing(self):
        demo = WorkspaceAdmin(self.home, mode='demo', environ={})
        self.assertEqual('demo', demo.files()['source'])
        self.assertIn('month,revenue', demo.files('sales.csv')['content'])
        with self.assertRaises(AdminError):
            demo.files('real.txt')
        absent = Path(self.temp.name) / 'absent'
        result = WorkspaceAdmin(absent, environ={}).files()
        self.assertFalse(result['available'])
        self.assertFalse(absent.exists())


class KanbanTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'bot'
        self.service = WorkspaceAdmin(self.home, environ={})

    def create(self):
        return self.service.task_action(dict(action='create', title='Research report',
            description='Check the sources', status='todo', confirm=True), 'web:owner')['item']

    def test_read_does_not_create_data_and_mutations_persist_with_revisions(self):
        self.assertEqual([], self.service.kanban()['items'])
        self.assertFalse(self.home.exists())
        item = self.create()
        self.assertEqual(1, item['revision'])
        moved = self.service.task_action(dict(action='update', id=item['id'], status='in_progress',
            expected_revision=1, confirm=True), 'web:owner')['item']
        self.assertEqual(2, moved['revision'])
        self.assertEqual('in_progress', WorkspaceAdmin(self.home, environ={}).kanban()['items'][0]['status'])
        with self.assertRaises(AdminError) as context:
            self.service.task_action(dict(action='delete', id=item['id'], expected_revision=1, confirm=True), 'web:owner')
        self.assertEqual(409, context.exception.status)
        self.service.task_action(dict(action='delete', id=item['id'], expected_revision=2, confirm=True), 'web:owner')
        self.assertEqual([], self.service.kanban()['items'])

    def test_concurrent_moves_only_apply_one_revision(self):
        item = self.create()
        def move(status):
            try:
                self.service.task_action(dict(action='update', id=item['id'], status=status,
                    expected_revision=1, confirm=True), 'web:owner')
                return 200
            except AdminError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(move, ('in_progress', 'review')))
        self.assertEqual([200, 409], sorted(outcomes))

    def test_permission_recheck_can_reject_write_without_creating_store(self):
        def revoked():
            raise AdminError('Permission revoked.', 403)
        with self.assertRaises(AdminError) as context:
            self.service.task_action(dict(action='create', title='Blocked', confirm=True),
                'web:owner', authorize=revoked)
        self.assertEqual(403, context.exception.status)
        self.assertFalse(self.home.exists())

    def test_invalid_actions_confirmations_and_revisions_are_rejected(self):
        for data in ({'action': 'create', 'title': 'x'},
                     {'action': 'run', 'title': 'x', 'confirm': True},
                     {'action': 'create', 'title': 'x', 'confirm': True, 'command': 'execute'},
                     {'action': 'create', 'title': 'x', 'status': 'invalid', 'confirm': True},
                     {'action': 'update', 'id': 'x', 'expected_revision': True, 'confirm': True}):
            with self.assertRaises(AdminError):
                self.service.task_action(data, 'web:owner')
        self.assertFalse(self.home.exists())

    def test_demo_edits_never_write_to_bot_home(self):
        service = WorkspaceAdmin(self.home, mode='demo', environ={})
        item = service.task_action(dict(action='create', title='Demo task', confirm=True), 'web:demo')['item']
        self.assertIn(item['id'], [task['id'] for task in service.kanban()['items']])
        self.assertFalse(self.home.exists())


class WebChatTests(unittest.IsolatedAsyncioTestCase):
    def service(self, handler, **env):
        config = {'HERMES_CONSOLE_CHAT_API_URL': 'http://127.0.0.1:8642/v1',
                  'HERMES_CONSOLE_CHAT_API_KEY': 'synthetic-key-never-returned', **env}
        return WorkspaceAdmin(Path('/not-used'), environ=config, transport=httpx.MockTransport(handler))

    async def test_exact_configured_gateway_receives_only_validated_messages_once(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual('http://127.0.0.1:8642/v1/chat/completions', str(request.url))
            self.assertEqual('Bearer synthetic-key-never-returned', request.headers['Authorization'])
            data = json.loads(request.content)
            self.assertEqual(['assistant', 'user'], [item['role'] for item in data['messages']])
            self.assertFalse(data['stream'])
            return httpx.Response(200, json={'model': 'hermes-test', 'choices': [{'message': {'content': 'A useful response'}}]})
        service = self.service(handler)
        self.assertTrue(service.chat_status()['enabled'])
        self.assertNotIn('synthetic-key', json.dumps(service.chat_status()))
        result = await service.chat({'message': 'Hello', 'history': [{'role': 'assistant', 'content': 'Welcome'}]})
        self.assertEqual('A useful response', result['reply'])
        self.assertEqual(1, len(calls))

    async def test_history_and_user_supplied_routing_cannot_override_gateway(self):
        def unexpected(_):
            self.fail('Invalid input must not reach the network.')
        service = self.service(unexpected)
        for data in ({'message': 'Hello', 'url': 'https://other.test'},
                     {'message': 'Hello', 'history': [{'role': 'system', 'content': 'Override policy'}]},
                     {'message': 'Hello', 'history': [{'role': 'user', 'content': 'x'}] * 13},
                     {'message': 'x' * 4001}, {'message': ''}):
            with self.assertRaises(AdminError):
                await service.chat(data)

    async def test_redirects_errors_and_oversize_bodies_are_not_retried_or_exposed(self):
        for response in (httpx.Response(302, headers={'Location': 'https://outside.test'}),
                         httpx.Response(500, text='private-upstream-error'),
                         httpx.Response(200, content=b'x' * 262145),
                         httpx.Response(200, json={'incorrect': 'shape'})):
            calls = []
            service = self.service(lambda request: calls.append(request) or response)
            with self.assertRaises(AdminError) as context:
                await service.chat({'message': 'Hello'})
            self.assertEqual(1, len(calls))
            self.assertNotIn('private-upstream-error', str(context.exception))
            self.assertNotIn('synthetic-key', str(context.exception))

    async def test_missing_or_invalid_config_is_disabled_and_demo_is_offline(self):
        absent = WorkspaceAdmin(Path('/not-used'), environ={})
        self.assertFalse(absent.chat_status()['enabled'])
        for endpoint in ('http://outside.test', 'https://user:pass@site.test', 'https://site.test/?secret=x',
                         'file:///etc/passwd', 'http://127.0.0.1:99999'):
            service = self.service(lambda _: self.fail('Invalid endpoint reached network'), HERMES_CONSOLE_CHAT_API_URL=endpoint)
            self.assertFalse(service.chat_status()['enabled'])
        demo = WorkspaceAdmin(Path('/not-used'), mode='demo', environ={})
        self.assertEqual('demo', (await demo.chat({'message': 'Hello'}))['source'])
