"""Opt-in smoke tests using installed, unmodified Hermes classes and loader.

Set HERMES_INTEGRATION_TESTS=1 and put the Hermes source/runtime and this
project's src on PYTHONPATH. Only the model handler and Zalo network are mocked.
"""

import asyncio
import os
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


@unittest.skipUnless(os.getenv("HERMES_INTEGRATION_TESTS") == "1", "Requires installed Hermes runtime")
class RealHermesTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="zalo-hermes-smoke-")
        cls.home = Path(cls.temp.name)
        cls.env = patch.dict(os.environ, {
            "HERMES_HOME": str(cls.home), "ZALO_BOT_TOKEN": "smoke-test-token",
            "ZALO_ALLOWED_USERS": "u1,u2", "ZALO_ALLOWED_GROUPS": "g1",
            "ZALO_STATE_DIR": str(cls.home / "state"),
        })
        cls.env.start()
        root = Path(__file__).parents[1]
        cls.home.joinpath("config.yaml").write_text(
            "plugins:\n  enabled: [zalo-platform]\nplatform_toolsets:\n  zalo: []\n"
            "gateway:\n  multiplex_profiles: false\n  group_sessions_per_user: true\n  delivery_ledger: false\n"
            "  platforms:\n    zalo:\n      enabled: true\n"
            "memory:\n  memory_enabled: false\n  user_profile_enabled: false\n", encoding="utf-8")
        plugin_dir = cls.home / "plugins" / "zalo-platform"
        shutil.copytree(root / "plugin", plugin_dir)
        shutil.copytree(root / "src" / "hermes_zalo", plugin_dir / "hermes_zalo", ignore=shutil.ignore_patterns("__pycache__"))
        from hermes_cli.plugins import PluginManager
        from gateway.platform_registry import platform_registry

        cls.manager = PluginManager(scope_key=str(cls.home))
        cls.manager.discover_and_load()
        cls.entry = platform_registry.get("zalo")
        if cls.entry is None:
            raise AssertionError("Hermes failed to discover and register the official Zalo plugin")

    @classmethod
    def tearDownClass(cls):
        from hermes_state_registry import close_all_under

        cls.manager.unload()
        close_all_under(cls.home)
        # Embedded AIAgent initializes queued log handlers with Windows locks.
        import logging
        from hermes_logging import drain_log_queue
        drain_log_queue(timeout=2)
        logging.shutdown()
        # Windows can still be retiring a Hermes file writer after its task
        # completed. Retry that cleanup briefly; do not mask a persistent error.
        try:
            for attempt in range(3):
                try:
                    cls.temp.cleanup()
                    break
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(0.2)
        finally:
            cls.env.stop()

    async def asyncSetUp(self):
        from gateway.config import PlatformConfig

        self.adapter = self.entry.adapter_factory(PlatformConfig(enabled=True, extra={"group_sessions_per_user": True}))
        # Use the plugin's own StateStore class; no replacement of Hermes base.
        import importlib
        self.module = importlib.import_module(self.adapter.__class__.__module__)
        self.adapter.store = self.module.StateStore(self.home / (self._testMethodName + ".sqlite3"))
        self.adapter.client = SimpleNamespace(send_text_chunk=AsyncMock(return_value={"message_id": "r1"}), close=AsyncMock())
        self.adapter._groups_supported = True
        self.handler = AsyncMock(return_value="Xin chào từ Hermes")
        self.adapter.set_message_handler(self.handler)

    async def asyncTearDown(self):
        await self.adapter.disconnect()

    @staticmethod
    def update(mid, user="u1", chat="g1", kind="GROUP", text="xin chào"):
        return {"event_name": "message.text.received", "message": {
            "message_id": mid, "from": {"id": user, "is_bot": False},
            "chat": {"id": chat, "chat_type": kind}, "text": text,
        }}

    async def test_real_loader_and_model_tools_disabled(self):
        from model_tools import _select_tool_names

        self.assertFalse(self.adapter.__abstractmethods__)
        self.assertEqual(self.entry.allowed_users_env, "ZALO_ALLOWED_USERS")
        self.assertFalse(self.entry.trusted_inbound)
        self.assertFalse(self.entry.allow_update_command)
        self.module.assert_chat_only()
        self.assertEqual(_select_tool_names([], None, True), set())
        self.assertIsNone(await self.adapter._record_delivery_obligation(None, None, None, None, False))

    async def test_admin_assets_use_real_plugin_namespace_without_top_level_package(self):
        """The sealed Docker runtime exposes only the copied plugin package."""
        import importlib
        from http.client import HTTPConnection
        package = self.adapter.__class__.__module__.rsplit('.', 1)[0]
        self.assertNotEqual('hermes_zalo', package)
        web = importlib.import_module(package + '.admin_web')
        config = importlib.import_module(package + '.admin_config').AdminConfig(
            enabled=True, conversations=True, port=0)
        runtime = web.AdminRuntime(SimpleNamespace(), None,
            self.home / 'asset-test.admin.sqlite3', config)
        original_import = importlib.import_module

        def plugin_only(name, *args, **kwargs):
            if name == 'hermes_zalo' or name.startswith('hermes_zalo.'):
                raise ModuleNotFoundError('Top-level adapter is absent in sealed Hermes')
            return original_import(name, *args, **kwargs)

        def request(path):
            client = HTTPConnection('127.0.0.1', runtime.server.server_port, timeout=5)
            try:
                client.request('GET', path)
                response = client.getresponse()
                return response.status, response.getheader('Content-Type'), response.read()
            finally:
                client.close()

        try:
            with patch('importlib.import_module', side_effect=plugin_only):
                for path, name, mime in (
                    ('/', 'index.html', 'text/html'),
                    ('/static/admin.js', 'admin.js', 'text/javascript'),
                    ('/static/admin.css', 'admin.css', 'text/css'),
                ):
                    with self.subTest(path=path):
                        status, content_type, body = await asyncio.to_thread(request, path)
                        self.assertEqual(200, status)
                        self.assertTrue(content_type.startswith(mime))
                        expected = Path(web.__file__).parent / 'admin_static' / name
                        self.assertEqual(expected.read_bytes(), body)
        finally:
            await runtime.close()

    async def test_real_adapter_routes_structured_intent_without_exposing_tools(self):
        data = dict(intent='menu', personal=False, when='', period='', person='', room='', course='',
                    title='', description='', needs='')
        router = SimpleNamespace(classify=AsyncMock(return_value=data), close=AsyncMock())
        self.adapter.work = self.module.WorkService(self.adapter.settings, environ={}, intent_router=router)
        await self.adapter._dispatch(self.update('nlu-real-hermes', text='Bạn hỗ trợ được việc j nhỉ'))
        router.classify.assert_awaited_once()
        self.handler.assert_not_awaited()
        self.assertEqual('g1', self.adapter.client.send_text_chunk.await_args.args[0])
        self.assertIn('MENU BOT', self.adapter.client.send_text_chunk.await_args.args[1])
        self.module.assert_chat_only()

    async def test_actual_base_routes_completes_and_deduplicates(self):
        await self.adapter._dispatch(self.update("m1", text="/update"))
        await self.adapter._dispatch(self.update("m1", text="/update"))
        self.handler.assert_awaited_once()
        event = self.handler.await_args.args[0]
        self.assertFalse(event.is_command())
        self.assertFalse(event.allow_gateway_control)
        self.assertEqual(event.source.user_id, "u1")
        self.adapter.client.send_text_chunk.assert_awaited_once_with("g1", "Xin chào từ Hermes")
        self.assertFalse(self.adapter._session_tasks)
        status = self.adapter.store.connection.execute("SELECT status FROM messages").fetchone()[0]
        self.assertEqual(status, "completed")

    async def test_actual_hermes_session_keys_separate_users_and_dms(self):
        from gateway.session import build_session_key

        await self.adapter._dispatch(self.update("m1", user="u1"))
        await self.adapter._dispatch(self.update("m2", user="u2"))
        await self.adapter._dispatch(self.update("m3", user="u1", chat="dm1", kind="PRIVATE"))
        events = [call.args[0] for call in self.handler.await_args_list]
        keys = [build_session_key(event.source, group_sessions_per_user=True) for event in events]
        self.assertEqual(len(set(keys)), 3)
        self.assertFalse(self.adapter._session_tasks)

    async def test_old_task_has_no_send_permission_during_new_same_chat_turn(self):
        release = asyncio.Event()
        stale_tasks = []

        async def stale_send():
            await release.wait()
            return await self.adapter.send("g1", "old background reply", reply_to="m2")

        async def handler(event):
            if event.message_id == "m1":
                stale_tasks.append(asyncio.create_task(stale_send()))
            else:
                release.set()
                result = await stale_tasks[0]
                self.assertFalse(result.success)
            return "Current reply"

        self.adapter.set_message_handler(handler)
        await self.adapter._dispatch(self.update("m1"))
        await self.adapter._dispatch(self.update("m2"))
        self.assertEqual(self.adapter.client.send_text_chunk.await_count, 2)
        self.assertTrue(all(call.args == ("g1", "Current reply")
                            for call in self.adapter.client.send_text_chunk.await_args_list))

    async def test_native_plugin_work_command_does_not_invoke_model(self):
        import importlib
        work_module = importlib.import_module(self.adapter.__class__.__module__.rsplit(".", 1)[0] + ".work")
        self.adapter.work = work_module.WorkService(self.adapter.settings, environ={})
        await self.adapter._dispatch(self.update("work-1", text="/nhac sau 1 phut | Nhắc thử"))
        self.handler.assert_not_awaited()
        reminders = self.adapter.work.reminders.list_for("u1", "g1", "GROUP")
        self.assertEqual(len(reminders), 1)
        self.assertIn("Đã đặt nhắc", self.adapter.client.send_text_chunk.await_args.args[1])
        self.adapter.client.send_text_chunk.reset_mock()
        await self.adapter._deliver_due_reminders(now=reminders[0].due_at + 1)
        self.adapter.client.send_text_chunk.assert_awaited_once()
        self.assertEqual("g1", self.adapter.client.send_text_chunk.await_args.args[0])
        self.assertIn("Nhắc thử", self.adapter.client.send_text_chunk.await_args.args[1])

    async def test_native_group_member_work_read_never_falls_through_to_model(self):
        import importlib
        from dataclasses import replace
        work_module = importlib.import_module(self.adapter.__class__.__module__.rsplit(".", 1)[0] + ".work")
        self.adapter.settings = replace(self.adapter.settings, group_access="members")
        self.adapter.work = work_module.WorkService(self.adapter.settings, environ={})
        self.adapter._bot_names = ("Bot LabCoach", "bot.synthetic-handle")
        await self.adapter._dispatch(self.update("group-1", user="new-member", text="@Bot LabCoach /menu"))
        self.assertIn("MENU BOT", self.adapter.client.send_text_chunk.await_args.args[1])
        await self.adapter._dispatch(self.update("group-2", user="new-member", text="Ngày mai mình có lịch gì?"))
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)
        self.assertTrue(all(c.args[0] == "g1" for c in self.adapter.client.send_text_chunk.await_args_list))
        self.handler.assert_not_awaited()
        await self.adapter._dispatch(self.update("group-3", user="new-member", chat="not-approved", text="/menu"))
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)

    async def test_native_group_conversation_for_members_keeps_scopes_and_tools(self):
        import importlib
        from dataclasses import replace
        from gateway.session import build_session_key

        work_module = importlib.import_module(self.adapter.__class__.__module__.rsplit(".", 1)[0] + ".work")
        self.adapter.settings = replace(self.adapter.settings, group_access="members")
        self.adapter.work = work_module.WorkService(self.adapter.settings, environ={})
        self.adapter._bot_names = ("Bot LabCoach",)
        for index, user in enumerate(("new-a", "new-b")):
            event = self.update(f"conversation-{index}", user=user, text="@Bot LabCoach Giải thích Python là gì?")
            await self.adapter._dispatch(event)
            await self.adapter._dispatch(event)
        self.assertEqual(2, self.handler.await_count)
        events = [call.args[0] for call in self.handler.await_args_list]
        self.assertEqual({"new-a", "new-b"}, {e.source.user_id for e in events})
        self.assertTrue(all(e.source.chat_id == "g1" and not e.allow_gateway_control for e in events))
        self.assertEqual(2, len({build_session_key(e.source, group_sessions_per_user=True) for e in events}))
        for event in events:
            self.assertEqual([], self.adapter.toolsets_for_source(event.source))
        self.assertTrue(all(call.args[0] == "g1" for call in self.adapter.client.send_text_chunk.await_args_list))
        for index, (chat, kind) in enumerate((("not-approved", "GROUP"), ("dm-new-a", "PRIVATE"))):
            await self.adapter._dispatch(self.update(f"denied-chat-{index}", user="new-a", chat=chat, kind=kind,
                                                     text="Giải thích Python là gì?"))
        self.assertEqual(2, self.handler.await_count)
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)

    async def test_native_registration_bootstrap_approval_and_revocation_keep_private_scope(self):
        import importlib
        from dataclasses import replace

        package = self.adapter.__class__.__module__.rsplit('.', 1)[0]
        work_module = importlib.import_module(package + '.work')
        sheets_module = importlib.import_module(package + '.sheets')
        workload_module = importlib.import_module(package + '.workload')
        self.adapter.settings = replace(self.adapter.settings, group_access='allowlist')
        values = dict(REGISTRATION_ENABLED='true', REGISTRATION_ADMIN_USERS='u1',
                      REGISTRATION_WORKLOAD_TAB='Synthetic Workload',
                      REGISTRATION_WORKLOAD_NAME_COLUMN='A', REGISTRATION_WORKLOAD_PHONE_COLUMN='B',
                      REGISTRATION_WORKLOAD_HEADER_ROW='1', REGISTRATION_WORKLOAD_NAME_HEADER='Họ tên',
                      REGISTRATION_WORKLOAD_PHONE_HEADER='Số điện thoại')
        sheets = SimpleNamespace(settings=sheets_module.SheetsSettings(
            'synthetic-registration-sheet', self.home / 'unused-registration-key.json',
            users=frozenset({'u1'}), calendar_format='lab', calendar_tabs=('Synthetic Lab',), issues_enabled=False),
            close=AsyncMock(), _request=AsyncMock(side_effect=AssertionError('Real API forbidden')))
        config = workload_module.WorkloadSettings.from_mapping(values, sheets.settings)
        person = workload_module.WorkloadPerson('Người Thử Native', '0900000001', config.source_key,
                                                'synthetic-native-fingerprint', True)
        directory = SimpleNamespace(source_key=config.source_key, settings=config,
                                    lookup=AsyncMock(return_value=person), resolve_name=AsyncMock(return_value=person))
        self.adapter.work = work_module.WorkService(self.adapter.settings, environ=values,
            sheets_client=sheets, registration_directory=directory)
        self.assertTrue(self.adapter.work.registration.enabled)
        settings_users = self.adapter.settings.allowed_users
        user, chat = 'new-native-registration', 'dm-native-registration'

        await self.adapter._dispatch(self.update('reg-denied', user=user, chat=chat, kind='PRIVATE', text='/menu'))
        self.adapter.client.send_text_chunk.assert_not_awaited()
        request = self.update('reg-request', user=user, chat=chat, kind='PRIVATE', text='/dangky ' + person.phone)
        await self.adapter._dispatch(request)
        await self.adapter._dispatch(request)
        self.adapter.client.send_text_chunk.assert_awaited_once()
        self.assertNotIn(person.phone, self.adapter.client.send_text_chunk.await_args.args[1])
        self.assertNotIn(person.name, self.adapter.client.send_text_chunk.await_args.args[1])
        self.handler.assert_not_awaited()
        record = self.adapter.work.registration.store.user_state(user)
        self.assertEqual('pending', record.status)
        await self.adapter._dispatch(self.update('reg-approve', user='u1', chat='dm-u1', kind='PRIVATE',
                                                 text='/duyetdangky ' + record.id))
        self.assertEqual('approved', self.adapter.work.registration.store.user_state(user).status)
        await self.adapter._dispatch(self.update('reg-chat', user=user, chat=chat, kind='PRIVATE',
                                                 text='Giải thích Python là gì?'))
        self.handler.assert_awaited_once()
        event = self.handler.await_args.args[0]
        self.assertEqual((user, chat), (event.source.user_id, event.source.chat_id))
        self.assertFalse(event.allow_gateway_control)
        self.assertEqual([], self.adapter.toolsets_for_source(event.source))
        self.assertNotIn(person.phone, event.text)
        self.assertEqual(settings_users, self.adapter.settings.allowed_users)
        sent = self.adapter.client.send_text_chunk.await_count
        for index, (destination, kind) in enumerate((('wrong-dm', 'PRIVATE'), ('g1', 'GROUP'), ('wrong-group', 'GROUP'))):
            await self.adapter._dispatch(self.update(f'reg-wrong-{index}', user=user, chat=destination, kind=kind,
                                                     text='Giải thích Python là gì?'))
        self.assertEqual(sent, self.adapter.client.send_text_chunk.await_count)
        await self.adapter._dispatch(self.update('reg-revoke', user='u1', chat='dm-u1', kind='PRIVATE',
                                                 text='/thuhoidangky ' + record.member_id))
        sent = self.adapter.client.send_text_chunk.await_count
        await self.adapter._dispatch(self.update('reg-after-revoke', user=user, chat=chat, kind='PRIVATE',
                                                 text='Giải thích Python là gì?'))
        self.assertEqual(sent, self.adapter.client.send_text_chunk.await_count)
        self.handler.assert_awaited_once()
        await self.adapter._dispatch(self.update('reg-status', user=user, chat=chat, kind='PRIVATE', text='/trangthaidangky'))
        self.assertEqual(sent + 1, self.adapter.client.send_text_chunk.await_count)
        self.assertNotIn(person.phone, self.adapter.client.send_text_chunk.await_args.args[1])
        self.assertNotIn(person.name, self.adapter.client.send_text_chunk.await_args.args[1])
        sheets._request.assert_not_awaited()


    async def test_managed_chat_real_hermes_engine_without_tools_or_session_database(self):
        """Real AIAgent executes its loop; only provider output is synthetic."""
        import importlib
        from openai.types.chat import ChatCompletion
        managed = importlib.import_module(self.adapter.__class__.__module__.rsplit('.', 1)[0] + '.managed_chat')
        IncomingMessage = importlib.import_module(
            self.adapter.__class__.__module__.rsplit('.', 1)[0] + '.events').IncomingMessage
        response = ChatCompletion(id='synthetic-response', object='chat.completion', created=0,
            model='synthetic/model', choices=[dict(index=0, finish_reason='stop',
                message=dict(role='assistant', content='Đã nhận ngữ cảnh mô phỏng.'))],
            usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20))
        instances = []
        def factory():
            agent = managed.configured_agent()
            self.assertEqual([], agent.tools)
            self.assertIsNone(agent._session_db)
            from unittest.mock import Mock
            agent._interruptible_api_call = Mock(return_value=response)
            agent._interruptible_streaming_api_call = Mock(return_value=response)
            instances.append(agent)
            return agent
        runtime = dict(provider='openrouter', api_mode='chat_completions',
                       base_url='https://model.invalid/api/v1', api_key='synthetic-not-a-key')
        import socket
        original_connect = socket.socket.connect
        def only_loopback(sock, address):
            if isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'):
                return original_connect(sock, address)
            raise AssertionError('External network forbidden')
        with patch('hermes_cli.config_effective.load_user_config_effective', return_value={
                'model': {'provider': 'openrouter', 'default': 'synthetic/model'}}), \
             patch('hermes_cli.runtime_provider.resolve_runtime_provider', return_value=runtime), \
             patch('socket.socket.connect', only_loopback):
            chat = managed.ManagedChat(factory, timeout=30)
            result = await chat.reply(IncomingMessage('demo-private', 'demo-member', 'fake-1', 'PRIVATE',
                'Trả lời ngắn nhé.'), {'payload': {'summary': '', 'messages': [],
                'memories': [{'content': 'Thích câu trả lời ngắn.'}]}})
        self.assertEqual('Đã nhận ngữ cảnh mô phỏng.', result)
        self.assertEqual(1, len(instances))
        self.assertIsNone(instances[0]._session_db)


if __name__ == "__main__":
    unittest.main()
