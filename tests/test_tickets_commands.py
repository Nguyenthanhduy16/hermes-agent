"""Tickets chat admission, deterministic intent routing and work integration."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import importlib.util
from types import SimpleNamespace
import threading
import time
import unicodedata
import unittest
from unittest.mock import Mock, patch

from hermes_zalo.commands import help_text, public_tickets_url, route, ticket_route, TICKETS_COMMANDS
from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.natural import interpret
from hermes_zalo.work import WorkService
from hermes_zalo.nlu import OUT_OF_SCOPE


class TicketLinkTests(unittest.TestCase):
    def test_real_public_https_addresses_keep_path_and_query(self):
        for value in ("https://tickets.company.vn/doi-ca", "https://shift.team.org:8443/requests?view=mine",
                      "https://8.8.8.8/tickets", "https://[2606:4700:4700::1111]/"):
            with self.subTest(value=value):
                self.assertEqual(value, public_tickets_url(value))

    def test_local_private_internal_and_credential_links_are_rejected(self):
        values = ("", "http://tickets.company.vn", "//tickets.company.vn", "https://localhost:8765",
                  "https://sub.localhost", "https://127.0.0.1", "https://127.1", "https://2130706433",
                  "https://10.0.0.2", "https://172.16.0.1", "https://192.168.1.1", "https://169.254.1.1",
                  "https://100.64.0.1", "https://0.0.0.0", "https://[::1]", "https://[fe80::1]",
                  "https://224.0.0.1", "https://[ff00::1]",
                  "https://[::ffff:127.0.0.1]", "https://[fc00::1]", "https://printer", "https://tickets.local",
                  "https://tickets.internal", "https://tickets.home.arpa", "https://portal.lan", "https://tickets.corp", "https://localhost.localdomain",
                  "https://tickets.test", "https://tickets.invalid", "https://tickets.example",
                  "https://name:password@tickets.company.vn", "https://name@tickets.company.vn",
                  "https://tickets.company.vn:bad", "https://tickets.company.vn:99999",
                  "https://tickets.company.vn:0", "https://%31%32%37.0.0.1", "https://tickets..company.vn",
                  "https://tickets.company.vn\\@localhost", "https://tickets.company.vn/\nsecret")
        for value in values:
            with self.subTest(value=value):
                self.assertIsNone(public_tickets_url(value))

    def test_explicit_natural_chat_request_precedes_calendar_or_policy_intent(self):
        for text in ("/tickets", "Tôi muốn đổi ca", "Mở trang đổi ca", "mo trang doi ca", "MỞ TRANG ĐỔI CA?", "trang đổi ca", "mở  trang\nđổi ca"):
            for parser in (route, interpret):
                self.assertEqual(("/tickets", ""), parser(unicodedata.normalize("NFD", text)))
        self.assertEqual("/hoi", interpret("Quy định đổi ca như thế nào?")[0])
        for text in ("Đổi ca ngày mai", "Tôi muốn đổi ca với B"):
            self.assertEqual(("/tickets", ""), interpret(text))
        self.assertIn("Đổi ca: /doica", help_text(tickets=True))
        self.assertNotIn("/doica", help_text(tickets=False))
        self.assertIn("TIN NHẮN RIÊNG", help_text("tickets"))
        self.assertNotIn("lấy link", help_text("tickets"))

    def test_prompt_phrases_with_or_without_accents_are_exact(self):
        cases = (("Tạo yêu cầu đổi ca", "/doica", ""),
                 ("Yêu cầu của tôi", "/yeucau", ""),
                 ("Có yêu cầu nào chờ tôi không?", "/choxacnhan", ""),
                 ("Yêu cầu chờ tôi", "/choxacnhan", ""),
                 ("Xem yêu cầu DC-012", "/yeucau", "DC-012"),
                 ("Lịch sử đổi ca của tôi", "/lichsudoica", ""),
                 ("Các yêu cầu chờ duyệt", "/choduyet", ""))
        from hermes_zalo.commands import normalized
        for text, command, argument in cases:
            for variant in (text, unicodedata.normalize("NFD", text), normalized(text)):
                for parser in (ticket_route, route, interpret):
                    actual = parser(variant)
                    self.assertEqual(command, actual[0], variant)
                    self.assertEqual(argument.casefold(), actual[1].casefold(), variant)

    def test_slash_actions_keep_ticket_ids_and_confirmation_codes(self):
        for command in TICKETS_COMMANDS:
            self.assertEqual((command, "DC-Ab12 Ab9X"), ticket_route(command.upper() + " DC-Ab12 Ab9X"))
        self.assertIsNone(ticket_route("Đồng ý"))
        self.assertIsNone(ticket_route("Từ chối"))
        self.assertIsNone(ticket_route("/dongyy DC-012"))

    def test_existing_read_and_reminder_intents_are_not_hijacked(self):
        for text, expected in (("/hoi quy trình đổi ca", "/hoi"),
                               ("Quy trình đổi ca như thế nào?", "/hoi"),
                               ("Ngày mai mình có lịch gì?", "/lichcuatoi"),
                               ("/dangky Nguyễn Văn An", "/dangky")):
            self.assertEqual(expected, interpret(text)[0])
        text = "nhắc mình sau 10 phút: đổi ca với Bình"
        self.assertEqual(("", text), interpret(text))

    def test_friendly_navigation_phrases_are_accent_case_and_punctuation_tolerant(self):
        examples = {
            '/tickets': ('Mình muốn đổi ca', 'Cho mình đổi ca', 'Em muốn đổi ca', 'Giúp mình đổi ca'),
            '/choxacnhan': ('Mình có yêu cầu đổi ca nào đang chờ không?', 'Ai đang chờ mình xác nhận?',
                            'Xem yêu cầu đang chờ mình xác nhận'),
            '/yeucau': ('Xem yêu cầu đổi ca của mình', 'Xem yêu cầu đổi ca của tôi'),
            '/thongbao': ('Xem thông báo', 'Mình có thông báo gì không?', 'Cho mình xem thông báo'),
            '/trogiup': ('Bot làm được gì?', 'Cho mình xem menu', 'Bot có tính năng gì?'),
        }
        from hermes_zalo.commands import normalized
        for command, phrases in examples.items():
            for phrase in phrases:
                for variant in (phrase, phrase.upper(), normalized(phrase), unicodedata.normalize('NFD', phrase),
                                '  ' + phrase.rstrip('?').replace(' ', '  \n') + ' ?  '):
                    with self.subTest(phrase=variant):
                        for parser in (route, interpret):
                            self.assertEqual((command, ''), parser(variant))
                        self.assertEqual((command, '') if command in TICKETS_COMMANDS else None, ticket_route(variant))

    def test_navigation_aliases_do_not_capture_policy_questions_or_embedded_prose(self):
        policies = ('Quy định đổi ca như thế nào?', 'Mình muốn đổi ca theo quy trình nào?',
                    'Quy định thông báo khi nghỉ phép là gì?', 'Bot làm được gì theo nội quy?',
                    'Cho mình xem quy định đổi ca', 'Xem yêu cầu đổi ca của mình theo quy định nào?')
        for phrase in policies:
            with self.subTest(phrase=phrase):
                self.assertIsNone(ticket_route(phrase))
                self.assertEqual('/hoi', interpret(phrase)[0])
        ordinary = ('Bạn ấy nói rằng mình muốn đổi ca', 'Hôm trước mình đã xem thông báo rồi',
                    'Cho mình xem menu là câu ví dụ', 'Mình có thông báo gì không là câu hỏi mẫu',
                    'Ai đang chờ mình xác nhận là ví dụ', 'Không muốn đổi ca', 'Đừng mở thông báo')
        for phrase in ordinary:
            with self.subTest(phrase=phrase):
                self.assertIsNone(ticket_route(phrase))
                self.assertEqual(('', phrase), route(phrase))

    def test_ordinary_confirmation_words_never_create_write_or_confirmation_commands(self):
        writes = {'/dongy', '/tuchoi', '/huydoica', '/duyet', '/doisoat', '/issue', '/doica'}
        for phrase in ('Đồng ý', 'Đồng ý 1234', 'Tôi đồng ý đổi ca', 'Từ chối 1234',
                       'Hủy yêu cầu DC-012', 'Hãy duyệt yêu cầu cho tôi', 'Gửi yêu cầu đổi ca cho Bình',
                       'Mình muốn đổi ca rồi cập nhật Sheets luôn', 'Xác nhận đổi lịch giúp tôi'):
            with self.subTest(phrase=phrase):
                for parser in (route, interpret):
                    command, argument = parser(phrase)
                    self.assertNotIn(command, writes)
        for command, argument in (('/dongy', '1234'), ('/tuchoi', 'DC-Ab12 4567'),
                                  ('/huydoica', 'DC-Xy9 7890'), ('/hoi', 'Quy định nghỉ phép của nhóm')):
            for parser in (route, interpret):
                self.assertEqual((command, argument), parser(command.upper() + ' ' + argument))


class TicketCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = TemporaryDirectory()
        self.settings = Settings("fake", frozenset({"admin"}), frozenset({"g1"}),
                                 state_dir=Path(self.folder.name), group_access="members")
        self.services = []
        self.domain_stores = []

    async def asyncTearDown(self):
        for service in self.services:
            await service.close()
        for store in self.domain_stores:
            store.close()
        self.folder.cleanup()

    def service(self, values=None, **kwargs):
        service = WorkService(self.settings, environ=values or {}, **kwargs)
        self.services.append(service)
        return service

    async def test_group_reader_gets_dm_guidance_and_never_calls_tickets(self):
        url = "https://tickets.company.vn/doi-ca"
        chat = SimpleNamespace(handle=Mock(return_value="private ticket detail"), close=Mock())
        service = self.service({"TICKETS_PUBLIC_URL": url}, tickets_chat=chat)
        for text in ("mở trang đổi ca", "Mình muốn đổi ca", "Ai đang chờ mình xác nhận?",
                     "Xem yêu cầu đổi ca của mình", "/doica", "/yeucau DC-012", "/dongy DC-012 code", "/help tickets"):
            response = await service.handle(IncomingMessage("g1", "new-member", "1", "GROUP", text))
            self.assertIn("tin nhắn riêng", response)
            self.assertNotIn(url, response)
            self.assertNotIn("DC-012", response)
            self.assertNotIn("private ticket detail", response)
        chat.handle.assert_not_called()
        for chat, kind, user in (("g2", "GROUP", "admin"), ("dm", "PRIVATE", "new-member")):
            response = await service.handle(IncomingMessage(chat, user, "2", kind, "/tickets"))
            self.assertIn("chưa được cấp quyền", response)
            self.assertNotIn(url, response)
        self.assertEqual(0, service.issues.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0])
        self.assertEqual([], service.reminders.list_for("new-member", "g1", "GROUP"))

    async def test_disabled_mode_returns_instructions_without_any_url_or_secret(self):
        for url in ("", "http://localhost:8765", "https://secret:do-not-echo@tickets.company.vn"):
            service = self.service({"TICKETS_PUBLIC_URL": url})
            response = await service.handle(IncomingMessage("dm", "admin", "3", "PRIVATE", "/tickets"))
            self.assertIn("chưa được bật", response)
            self.assertIn("/doica", response)
            self.assertNotIn("do-not-echo", response)
            self.assertNotIn("http://", response)
            self.assertNotIn("https://", response)

    async def test_command_cannot_override_config_or_create_an_issue(self):
        service = self.service({"TICKETS_PUBLIC_URL": "https://tickets.company.vn/"})
        response = await service.handle(IncomingMessage("dm", "admin", "4", "PRIVATE", "/tickets https://attacker.test"))
        self.assertNotIn("attacker", response)
        self.assertIn("/tickets", response)
        help_response = await service.handle(IncomingMessage("dm", "admin", "5", "PRIVATE", "/tickets help"))
        self.assertIn("sau khi đủ các bước xác nhận", help_response)
        self.assertEqual(0, service.issues.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0])

    async def test_private_wizard_receives_raw_message_off_the_event_loop(self):
        main_thread = threading.get_ident()
        threads, messages = [], []
        def handle(message):
            threads.append(threading.get_ident())
            messages.append(message)
            return "Bước tiếp theo" if message.text in ("Tạo yêu cầu đổi ca", "1", "quay lại", "thoát") else None
        chat = SimpleNamespace(handle=handle, close=Mock())
        service = self.service(tickets_chat=chat)
        for index, text in enumerate(("Tạo yêu cầu đổi ca", "1", "quay lại", "thoát")):
            message = IncomingMessage("dm", "admin", str(index), "PRIVATE", text)
            self.assertEqual("Bước tiếp theo", await service.handle(message))
            self.assertIs(message, messages[-1])
            self.assertEqual(message.key, messages[-1].key)
        self.assertTrue(all(thread != main_thread for thread in threads))
        self.assertIn("MENU BOT", await service.handle(IncomingMessage("dm", "admin", "menu", "PRIVATE", "/menu")))
        self.assertEqual(OUT_OF_SCOPE, await service.handle(IncomingMessage("dm", "admin", "chat", "PRIVATE", "Xin chào")))

    async def test_bindings_never_override_bot_allowlist(self):
        chat = SimpleNamespace(handle=Mock(return_value="should not appear"), close=Mock())
        service = self.service(tickets_chat=chat)
        message = IncomingMessage("dm", "bound-but-not-allowed", "1", "PRIVATE", "/doica")
        self.assertIn("chưa được cấp quyền", await service.handle(message))
        chat.handle.assert_not_called()
        self.assertEqual(frozenset({"admin"}), service.settings.allowed_users)

    async def test_natural_navigation_resumes_real_wizard_without_restarting_or_creating_work(self):
        from hermes_zalo.tickets.bindings import IdentityStore
        from hermes_zalo.tickets.chat import TicketsChat
        from hermes_zalo.tickets.service import DemoAssignments, TicketService, TicketStore, digest
        store = TicketStore(Path(self.folder.name) / 'navigation.sqlite3')
        self.domain_stores.append(store)
        now = datetime.fromisoformat('2030-10-01T09:00:00+07:00').timestamp()
        provider = DemoAssignments(store, now=now)
        domain = TicketService(store, provider, require_manager=False, clock=lambda: now)
        identities = IdentityStore(store)
        bot = digest(self.settings.token)
        identities.bind(bot, 'admin', 'dm', 'demo-a')
        identities.bind(bot, 'peer', 'peer-dm', 'demo-b')
        chat = TicketsChat(domain, identities, bot_scope=bot)
        service = self.service(tickets_chat=chat)
        sequence = 0
        async def say(text):
            nonlocal sequence
            sequence += 1
            return await service.handle(IncomingMessage('dm', 'admin', str(sequence), 'PRIVATE', text))
        def dialog():
            row = store.db.execute('SELECT body,expires FROM ticket_chat_dialogs').fetchone()
            return (json.loads(row['body']), row['expires']) if row else None
        await say('/doica')
        await say(dialog()[0]['choices'][0]['code'])
        before = dialog()
        self.assertEqual('person', before[0]['step'])
        for phrase in ('Mình muốn đổi ca', 'Cho mình đổi ca'):
            self.assertIn('Bước 2/4', await say(phrase))
            self.assertEqual(before, dialog())
        for phrase, expected in (('Bot làm được gì?', 'MENU BOT'), ('Cho mình xem menu', 'MENU BOT'),
                                  ('Mình có thông báo gì không?', 'THÔNG BÁO'), ('Xem thông báo', 'THÔNG BÁO')):
            self.assertIn(expected, await say(phrase))
            self.assertEqual(before, dialog())
        self.assertEqual(0, store.db.execute('SELECT COUNT(*) FROM exchange_tickets').fetchone()[0])
        self.assertEqual(0, service.issues.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0])
        self.assertEqual([], service.reminders.list_for('admin', 'dm', 'PRIVATE'))

    async def test_lazy_open_is_once_for_concurrent_messages_and_closes_resource(self):
        chat = SimpleNamespace(handle=Mock(return_value="Tickets ready"), close=Mock())
        def create(*args):
            time.sleep(0.02)
            return chat
        with patch("hermes_zalo.tickets.runtime.open_chat", side_effect=create) as open_chat:
            service = self.service({"TICKETS_CHAT_MODE": "sheets_test"})
            open_chat.assert_not_called()
            replies = await asyncio.gather(*(service.handle(IncomingMessage("dm", "admin", str(i), "PRIVATE", "/tickets")) for i in range(2)))
            self.assertEqual(["Tickets ready", "Tickets ready"], replies)
            open_chat.assert_called_once()
            self.assertEqual(2, chat.handle.call_count)
            await service.close()
            self.services.remove(service)
            chat.close.assert_called_once()

    async def test_invalid_tickets_configuration_does_not_break_other_work(self):
        with patch("hermes_zalo.tickets.runtime.open_chat", side_effect=RuntimeError("secret-url-password")) as open_chat:
            service = self.service({"TICKETS_CHAT_MODE": "sheets_test"})
            response = await service.handle(IncomingMessage("dm", "admin", "1", "PRIVATE", "/doica"))
            self.assertIn("chưa sẵn sàng", response)
            self.assertNotIn("secret-url-password", response)
            self.assertIn("MENU BOT", await service.handle(IncomingMessage("dm", "admin", "2", "PRIVATE", "/menu")))
            response = await service.handle(IncomingMessage("dm", "admin", "3", "PRIVATE", "/nhac sau 10 phut | Báo cáo"))
            self.assertIn("Đã đặt nhắc", response)
            self.assertEqual(OUT_OF_SCOPE, await service.handle(IncomingMessage("dm", "admin", "4", "PRIVATE", "Xin chào")))
            open_chat.assert_called_once()

    async def test_cancelled_lazy_open_releases_resources_without_handling_message(self):
        started, release = threading.Event(), threading.Event()
        chat = SimpleNamespace(handle=Mock(return_value="should not run"), close=Mock())
        def create(*args):
            started.set()
            release.wait(timeout=3)
            return chat
        with patch("hermes_zalo.tickets.runtime.open_chat", side_effect=create):
            service = self.service({"TICKETS_CHAT_MODE": "sheets_test"})
            pending = asyncio.create_task(service.handle(IncomingMessage("dm", "admin", "cancel", "PRIVATE", "/tickets")))
            await asyncio.to_thread(started.wait, 3)
            self.assertTrue(started.is_set())
            pending.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            chat.handle.assert_not_called()
            chat.close.assert_called_once()
            self.assertIsNone(service._tickets_chat)

    async def test_handler_failure_does_not_echo_details_or_fallthrough_to_model(self):
        chat = SimpleNamespace(handle=Mock(side_effect=RuntimeError("private-sheet-body")), close=Mock())
        service = self.service(tickets_chat=chat)
        for text in ("/doica", "1", "Đổi ca ngày mai"):
            response = await service.handle(IncomingMessage("dm", "admin", text, "PRIVATE", text))
            self.assertIn("chưa xác định được kết quả", response)
            self.assertNotIn("private-sheet-body", response)
        self.assertIn("MENU BOT", await service.handle(IncomingMessage("dm", "admin", "menu", "PRIVATE", "/menu")))

    async def test_disabled_or_group_traffic_never_opens_runtime(self):
        with patch("hermes_zalo.tickets.runtime.open_chat", side_effect=AssertionError("must not open")) as open_chat:
            off = self.service()
            enabled = self.service({"TICKETS_CHAT_MODE": "sheets_test"})
            await off.handle(IncomingMessage("dm", "admin", "1", "PRIVATE", "/doica"))
            await enabled.handle(IncomingMessage("g1", "admin", "2", "GROUP", "/doica"))
            open_chat.assert_not_called()


class TicketLaunchTests(unittest.TestCase):
    def test_local_modes_do_not_require_hermes_or_zalo_settings(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("ticket_launch_test", root / "scripts" / "launch.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for mode in ("demo", "sheets_test"):
            with self.subTest(mode=mode), patch.object(module, "load_env"), \
                    patch.object(module.sys, "argv", ["launch.py", "tickets", mode]), \
                    patch.object(module.Settings, "from_env", side_effect=AssertionError("Do not require Zalo credentials")), \
                    patch.object(module.subprocess, "call", return_value=0) as call:
                self.assertEqual(0, module.main())
                self.assertEqual([module.sys.executable, "-m", "hermes_zalo.tickets.cli", "serve",
                                  "--mode", mode, "--port", "8765"], call.call_args.args[0])
                self.assertEqual(root, call.call_args.kwargs["cwd"])


if __name__ == "__main__":
    unittest.main()
