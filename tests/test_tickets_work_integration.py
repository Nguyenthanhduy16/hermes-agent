"""WorkService and actual TicketsChat together; only local demo assignments."""
from datetime import datetime
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.tickets.bindings import IdentityStore
from hermes_zalo.tickets.chat import TicketsChat
from hermes_zalo.tickets.service import DemoAssignments, TicketService, TicketStore, digest
from hermes_zalo.work import WorkService


class TicketsWorkIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = datetime.fromisoformat("2030-10-01T09:00:00+07:00").timestamp()
        self.store = TicketStore(self.root / "tickets.sqlite3")
        self.provider = DemoAssignments(self.store, now=self.now)
        self.domain = TicketService(self.store, self.provider, clock=lambda: self.now)
        self.identities = IdentityStore(self.store)
        self.scope = digest("synthetic-work-bot")
        for user in ("a", "b", "c", "manager"):
            self.identities.bind(self.scope, user, "dm-" + user, "demo-" + user)
        self.chat = TicketsChat(self.domain, self.identities, bot_scope=self.scope)
        self.settings = Settings("synthetic-work-bot", frozenset({"a", "b", "manager", "unverified"}),
                                 frozenset({"g1"}), state_dir=self.root / "work", group_access="members",
                                 knowledge_dir=self.root / "knowledge")
        self.work = WorkService(self.settings, environ={}, tickets_chat=self.chat)
        self.seq = 0

    async def asyncTearDown(self):
        await self.work.close()
        self.provider.close()
        self.store.close()
        self.temp.cleanup()

    async def say(self, user, text, *, kind="PRIVATE", chat=None, mid=None):
        self.seq += 1
        return await self.work.handle(IncomingMessage(chat or "dm-" + user, user,
                                                     mid or str(self.seq), kind, text))

    async def choose(self, response, item):
        scope = self.chat._scope(IncomingMessage('dm-a', 'a', 'lookup', 'PRIVATE', ''))
        body = json.loads(self.store.db.execute('SELECT body FROM ticket_chat_dialogs WHERE scope=?', (scope,)).fetchone()[0])
        choice = next(c for c in body['choices'] if c['item']['id'] == item)
        self.assertRegex(response, r'(?m)^' + re.escape(choice['code']) + r'\. ')
        return await self.say("a", choice['code'])

    async def enter_reason_step(self):
        response = await self.say("a", "Tạo yêu cầu đổi ca")
        response = await self.choose(response, "CA-11")
        response = await self.choose(response, "coach-b")
        response = await self.choose(response, "CA-21")
        self.assertIn("Nhập lý do", response)
        return response

    def count_tickets(self):
        return self.store.db.execute("SELECT COUNT(*) FROM exchange_tickets").fetchone()[0]

    async def test_work_commands_do_not_become_reason_in_active_wizard(self):
        await self.enter_reason_step()
        self.assertIn("Google Sheets", await self.say("a", "lịch hôm nay"))
        self.assertIn("MENU BOT", await self.say("a", "/menu"))
        self.assertIsNotNone(await self.say("a", "/hoi quy trình đổi ca"))
        self.assertIn("Đã đặt nhắc", await self.say("a", "nhắc mình sau 10 phút: gửi báo cáo"))
        self.assertIn("TIN NHẮN RIÊNG", await self.say("a", "/help tickets"))
        state = await self.say("a", "/tickets")
        self.assertIn("Nhập lý do", state)
        self.assertNotIn("CHƯA GỬI YÊU CẦU", state)
        self.assertEqual(0, self.count_tickets())
        response = await self.say("a", "Bận việc gia đình")
        self.assertIn("CHƯA GỬI YÊU CẦU", response)
        self.assertIn("Bận việc gia đình", response)

    async def test_verified_private_request_requires_preview_and_explicit_confirmation(self):
        await self.enter_reason_step()
        preview = await self.say("a", "Bận việc gia đình")
        self.assertEqual(0, self.count_tickets())
        command = re.search(r"/doica gui [A-F0-9]+", preview)[0]
        result = await self.say("a", command, mid="submit-once")
        self.assertIn("Đã gửi yêu cầu", result)
        ticket = re.search(r"DC-[A-F0-9]+", result)[0]
        self.assertIn(ticket, await self.say("b", "Có yêu cầu nào chờ tôi không?"))
        self.assertIn("không thực hiện lại", await self.say("a", command, mid="submit-once"))
        self.assertEqual(1, self.count_tickets())
        self.assertEqual("pending_peer", self.domain.detail(self.store.account("demo-a"), ticket)["status"])
        self.assertEqual("coach-a", {row["id"]: row for row in self.provider.list_assignments()}["CA-11"]["member_id"])
        self.assertNotIn("http", result)

    async def test_leave_keywords_are_reason_only_at_reason_step(self):
        with patch.object(self.work.knowledge, "answer", return_value="Kết quả tài liệu") as documents:
            policy = "Quy định nghỉ phép như thế nào?"
            self.assertEqual("Kết quả tài liệu", await self.say("a", policy))
            documents.assert_called_once()
            await self.enter_reason_step()
            for question in ("/hoi Quy định nghỉ phép", "hỏi tài liệu Quy định nghỉ phép"):
                self.assertEqual("Kết quả tài liệu", await self.say("a", question))
            documents.reset_mock()
            reason = "Xin nghỉ phép vì việc gia đình"
            response = await self.say("a", reason)
            self.assertIn("CHƯA GỬI YÊU CẦU", response)
            self.assertIn(reason, response)
            documents.assert_not_called()
            self.assertEqual(0, self.count_tickets())
            await self.say("a", "thoát")
            self.assertEqual("Kết quả tài liệu", await self.say("a", policy))
            documents.assert_called_once()

    async def test_group_binding_and_bot_permissions_never_reveal_private_details(self):
        await self.enter_reason_step()
        preview = await self.say("a", "Lý do riêng")
        command = re.search(r"/doica gui [A-F0-9]+", preview)[0]
        result = await self.say("a", command)
        ticket = re.search(r"DC-[A-F0-9]+", result)[0]
        for user in ("a", "c", "unverified"):
            response = await self.say(user, "/yeucau " + ticket, kind="GROUP", chat="g1")
            self.assertIn("tin nhắn riêng", response)
            for detail in (ticket, "Lý do riêng", "CA-11"):
                self.assertNotIn(detail, response)
        self.assertIn("chưa được cấp quyền", await self.say("c", "/yeucau " + ticket))
        self.assertIn("chưa được quản trị", await self.say("unverified", "/doica"))
        self.assertIn("chưa được quản trị", await self.say("a", "/yeucau " + ticket, chat="other-dm"))
        self.assertEqual(1, self.count_tickets())

    async def test_revoked_binding_stops_plain_wizard_input_and_leaves_normal_work(self):
        await self.enter_reason_step()
        self.identities.revoke(self.scope, "a", "dm-a")
        self.assertIn("chưa được quản trị", await self.say("a", "Bận việc gia đình"))
        self.assertIn("chưa được quản trị", await self.say("a", "/doica"))
        self.assertIn("MENU BOT", await self.say("a", "/menu"))
        self.assertEqual(0, self.count_tickets())

    async def test_expired_or_revoked_reason_with_policy_words_never_becomes_document_query(self):
        for expired in (True, False):
            with self.subTest(expired=expired):
                await self.enter_reason_step()
                if expired:
                    self.now += 901
                else:
                    self.identities.revoke(self.scope, "a", "dm-a")
                with patch.object(self.work.knowledge, "answer") as documents:
                    response = await self.say("a", "Xin nghỉ phép vì việc gia đình")
                    self.assertIn("hết hạn" if expired else "chưa được quản trị", response)
                    documents.assert_not_called()
                self.assertEqual(0, self.count_tickets())


if __name__ == "__main__":
    unittest.main()
