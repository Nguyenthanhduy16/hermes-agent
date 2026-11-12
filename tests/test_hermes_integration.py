"""Opt-in smoke tests using installed, unmodified Hermes classes and loader.

Set HERMES_INTEGRATION_TESTS=1 and put the Hermes source/runtime and this
project's src on PYTHONPATH. Work/model responses and Zalo network are simulated.
These checks never send messages or call a real model/Sheets endpoint.
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
        self.sources = []
        original_build_source = self.adapter.build_source
        def capture_source(**values):
            source = original_build_source(**values)
            self.sources.append(source)
            return source
        self.adapter.build_source = capture_source
        self.work_handler = AsyncMock(return_value='Kết quả công việc')
        self.adapter.work = SimpleNamespace(handle=self.work_handler,
            pilot=SimpleNamespace(record=lambda *args: None), close=AsyncMock())

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
                    ('/', 'console.html', 'text/html'),
                    ('/legacy.html', 'index.html', 'text/html'),
                    ('/static/admin.js', 'admin.js', 'text/javascript'),
                    ('/static/admin.css', 'admin.css', 'text/css'),
                    ('/static/console.js', 'console.js', 'text/javascript'),
                ):
                    with self.subTest(path=path):
                        status, content_type, body = await asyncio.to_thread(request, path)
                        self.assertEqual(200, status)
                        self.assertTrue(content_type.startswith(mime))
                        expected = (Path(web.__file__).parent / 'admin_static' / name).read_bytes()
                        if mime == 'text/html':
                            # Served pages carry ?v=<content hash> on their asset tags.
                            expected = web.versioned_html(expected, web.asset_table())
                        self.assertEqual(expected, body)
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

    async def test_actual_plugin_routes_work_completes_and_deduplicates_without_gateway(self):
        await self.adapter._dispatch(self.update("m1", text="/update"))
        await self.adapter._dispatch(self.update("m1", text="/update"))
        self.work_handler.assert_awaited_once()
        self.handler.assert_not_awaited()
        message = self.work_handler.await_args.args[0]
        self.assertEqual((message.user_id, message.chat_id), ('u1', 'g1'))
        self.adapter.client.send_text_chunk.assert_awaited_once_with("g1", "Kết quả công việc")
        self.assertFalse(self.adapter._session_tasks)
        status = self.adapter.store.connection.execute("SELECT status FROM messages").fetchone()[0]
        self.assertEqual(status, "completed")

    async def test_actual_hermes_session_keys_separate_users_and_dms(self):
        from gateway.session import build_session_key

        await self.adapter._dispatch(self.update("m1", user="u1"))
        await self.adapter._dispatch(self.update("m2", user="u2"))
        await self.adapter._dispatch(self.update("m3", user="u1", chat="dm1", kind="PRIVATE"))
        keys = [build_session_key(source, group_sessions_per_user=True) for source in self.sources]
        self.assertEqual(len(set(keys)), 3)
        self.assertFalse(self.adapter._session_tasks)
        self.handler.assert_not_awaited()

    async def test_registered_hermes_session_rotates_after_identity_correction(self):
        from gateway.session import build_session_key
        identity = SimpleNamespace(member_id='member-old', id='DK-00000001')
        self.adapter.work = SimpleNamespace(registration=SimpleNamespace(identity=lambda user: identity),
            handle=AsyncMock(return_value=None), pilot=SimpleNamespace(record=lambda *args: None), close=AsyncMock())
        await self.adapter._dispatch(self.update('before-correction', chat='dm1', kind='PRIVATE'))
        identity = SimpleNamespace(member_id='member-new', id='DK-00000002')
        await self.adapter._dispatch(self.update('after-correction', chat='dm1', kind='PRIVATE'))
        self.assertEqual(len(self.sources), 2)
        self.assertNotEqual(build_session_key(self.sources[0]), build_session_key(self.sources[1]))
        self.assertEqual(self.sources[0].chat_id, self.sources[1].chat_id)
        self.handler.assert_not_awaited()

    async def test_old_task_has_no_send_permission_during_new_same_chat_turn(self):
        release = asyncio.Event()
        stale_tasks = []

        async def stale_send():
            await release.wait()
            return await self.adapter.send("g1", "old background reply", reply_to="m2")

        async def handler(message):
            if message.message_id == "m1":
                stale_tasks.append(asyncio.create_task(stale_send()))
            else:
                release.set()
                result = await stale_tasks[0]
                self.assertFalse(result.success)
            return "Current reply"

        self.work_handler.side_effect = handler
        await self.adapter._dispatch(self.update("m1"))
        await self.adapter._dispatch(self.update("m2"))
        self.assertEqual(self.adapter.client.send_text_chunk.await_count, 2)
        self.assertTrue(all(call.args == ("g1", "Current reply")
                            for call in self.adapter.client.send_text_chunk.await_args_list))
        self.handler.assert_not_awaited()

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

    async def test_native_group_out_of_scope_refuses_without_model_and_keeps_permissions(self):
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
        self.handler.assert_not_awaited()
        self.assertEqual({"new-a", "new-b"}, {s.user_id for s in self.sources})
        self.assertEqual(2, len({build_session_key(s, group_sessions_per_user=True) for s in self.sources}))
        for source in self.sources:
            self.assertEqual([], self.adapter.toolsets_for_source(source))
        self.assertTrue(all(call.args == ("g1", self.module.OUT_OF_SCOPE)
                            for call in self.adapter.client.send_text_chunk.await_args_list))
        for index, (chat, kind) in enumerate((("not-approved", "GROUP"), ("dm-new-a", "PRIVATE"))):
            await self.adapter._dispatch(self.update(f"denied-chat-{index}", user="new-a", chat=chat, kind=kind,
                                                     text="Giải thích Python là gì?"))
        self.handler.assert_not_awaited()
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
        self.handler.assert_not_awaited()
        source = self.sources[-1]
        self.assertEqual((user, chat), (source.user_id, source.chat_id))
        self.assertEqual([], self.adapter.toolsets_for_source(source))
        self.assertEqual(self.module.OUT_OF_SCOPE, self.adapter.client.send_text_chunk.await_args.args[1])
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
        self.handler.assert_not_awaited()
        await self.adapter._dispatch(self.update('reg-status', user=user, chat=chat, kind='PRIVATE', text='/trangthaidangky'))
        self.assertEqual(sent + 1, self.adapter.client.send_text_chunk.await_count)
        self.assertNotIn(person.phone, self.adapter.client.send_text_chunk.await_args.args[1])
        self.assertNotIn(person.name, self.adapter.client.send_text_chunk.await_args.args[1])
        sheets._request.assert_not_awaited()


    async def test_native_gateway_and_legacy_chat_cannot_reopen_general_generation(self):
        """Neither a direct gateway event nor an unhandled work turn calls AI."""
        import socket
        legacy = SimpleNamespace(reply=AsyncMock(side_effect=AssertionError('Legacy model forbidden')))
        self.adapter.managed_chat = legacy
        async def attempt_gateway(message):
            event = self.adapter._current_event
            self.assertFalse(event.allow_gateway_control)
            event._gateway_accepted = True
            await self.adapter.handle_message(event)
            self.assertFalse(event._gateway_accepted)
            return None
        self.work_handler.side_effect = attempt_gateway
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('External network forbidden')):
            await self.adapter._dispatch(self.update('closed-fallback', text='Viết truyện vui đi'))
            resumed = SimpleNamespace(internal=True, _gateway_accepted=True)
            await self.adapter.handle_message(resumed)
        self.assertFalse(resumed._gateway_accepted)
        self.assertEqual(self.module.OUT_OF_SCOPE, self.adapter.client.send_text_chunk.await_args.args[1])
        self.assertFalse(self.adapter._session_tasks)
        legacy.reply.assert_not_awaited()
        self.handler.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
