"""Group admission, natural reads and self-declared profiles, entirely offline."""
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from hermes_zalo.config import Settings, ConfigError
from hermes_zalo.events import IncomingMessage, is_allowed
from hermes_zalo.natural import interpret
from hermes_zalo.commands import parse_calendar
from hermes_zalo.profiles import ProfileStore
from hermes_zalo.sheets import SheetsClient, SheetsError
from hermes_zalo.work import WorkService
from test_lab_calendar import grid, config
import test_adapter as contract


def incoming(text="", user="new-user", chat="g1", kind="GROUP", mid="m1"):
    return IncomingMessage(chat, user, mid, kind, text)


class NaturalTests(unittest.TestCase):
    def test_personal_calendar_varied_wording_and_dates(self):
        for text, day in (("Ngày mai mình có lịch gì?", "ngay mai"),
                          ("Tuần này tôi dạy ca nào?", "tuan nay"),
                          ("Lịch của tôi ngày 06/10/2026", "06/10/2026"),
                          ("/lich của tôi mai", "mai"), ("/lichcuatoi homnay", "homnay")):
            command, argument = interpret(text)
            self.assertEqual("/lichcuatoi", command)
            self.assertEqual(parse_calendar(day), parse_calendar(argument))

    def test_common_schedule_filters_and_evening_not_confused_with_personal_pronoun(self):
        for text in ("Hôm nay phòng H201 có lớp nào?", "Cho mình xem lịch hôm nay phòng H201"):
            command, argument = interpret(text)
            self.assertEqual("/lich", command)
            self.assertEqual((("phong", "H201"),), parse_calendar(argument).filters)
        for text in ("Tối nay có lớp nào?", "Lich buoi toi hom nay"):
            command, argument = interpret(text)
            self.assertEqual("/lich", command)
            self.assertIn(("buoi", "toi"), parse_calendar(argument).filters)

    def test_policy_and_write_requests_never_become_calendar_or_issue_actions(self):
        for text in ("Quy định xếp lịch thế nào?", "Xin nghỉ phép cần báo ai?", "Đi muộn xử lý sao?", "Lab Coach cần chuẩn bị gì?"):
            self.assertEqual(("/hoi", text), interpret(text))
        self.assertEqual(("/trogiup", "issue"), interpret("Tạo issue lỗi nút lưu"))
        for text in ("Xóa lịch hôm nay", "Đổi ca ngày mai", "Lịch thứ hai", "Lịch 06/10 và 07/10", "Lịch 2026-1-1", "Lịch 06/10/26"):
            command, argument = interpret(text)
            self.assertNotIn(command, ("/issue", "/nhac"))
            if command == "/lich":
                self.assertEqual("help", argument)

    def test_registration_is_explicit_and_not_derived_from_display_name(self):
        self.assertEqual(("/dangky", "Coach Alpha"), interpret("Tôi là Coach Alpha"))
        self.assertEqual(("/dangky", "Coach Beta"), interpret("đăng ký tên Coach Beta"))
        self.assertNotEqual("/dangky", interpret("Bạn biết Coach Alpha không?")[0])
        for text in ("Bạn đang làm gì ở đây?", "Hôm nay có cá ngon không?", "Tôi thích ca nhạc", "Lịch sử Việt Nam"):
            self.assertNotIn(interpret(text)[0], ("/lich", "/lichcuatoi"))


class GroupAccessTests(unittest.TestCase):
    def test_member_mode_never_opens_other_groups_or_private_chats(self):
        users, groups = frozenset({"admin"}), frozenset({"g1"})
        self.assertTrue(is_allowed(incoming(), users, groups, group_members=True))
        self.assertFalse(is_allowed(incoming(), users, groups))
        self.assertFalse(is_allowed(incoming(chat="g2"), users, groups, group_members=True))
        self.assertFalse(is_allowed(incoming(kind="PRIVATE"), users, groups, group_members=True))
        with self.assertRaises(ConfigError):
            Settings.from_mapping({"ZALO_BOT_TOKEN": "fake", "ZALO_ALLOWED_USERS": "admin", "ZALO_GROUP_ACCESS": "everyone"})

    def test_sheet_member_access_only_applies_to_calendar_in_named_group(self):
        settings = config(ZALO_GROUP_ACCESS="members", ZALO_SHEETS_GROUPS="g1", ZALO_ISSUES_ENABLED="true")
        self.assertTrue(settings.allows_calendar("new-user", "g1", "GROUP"))
        self.assertFalse(settings.allows("new-user", "g1", "GROUP"))
        self.assertFalse(settings.allows_calendar("new-user", "g2", "GROUP"))
        self.assertFalse(settings.allows_calendar("new-user", "g1", "PRIVATE"))

    def test_profile_persists_but_cannot_cross_sender_chat_or_chat_type(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "profiles.sqlite3"
            store = ProfileStore(path)
            store.set(incoming(), "Coach Alpha")
            store.close()
            store = ProfileStore(path)
            try:
                self.assertEqual("Coach Alpha", store.get(incoming()))
                for other in (incoming(user="other"), incoming(chat="g2"), incoming(kind="PRIVATE")):
                    self.assertIsNone(store.get(other))
                    store.remove(other)
                self.assertEqual("Coach Alpha", store.get(incoming()))
                store.remove(incoming())
                self.assertIsNone(store.get(incoming()))
            finally:
                store.close()


class GroupWorkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = TemporaryDirectory()
        root = Path(self.folder.name)
        kb = root / "knowledge"; kb.mkdir()
        (kb / "shared.md").write_text("# Quy định xin nghỉ phép\nXin nghỉ phép: báo quản lý trước 2 ngày.\n", encoding="utf-8")
        (kb / "private.md").write_text("# Quy định thưởng riêng\nQuy định thưởng riêng: PRIVATE-BONUS-SECRET.\n", encoding="utf-8")
        (kb / "manifest.json").write_text(json.dumps({"documents": [
            {"id": "shared", "title": "Quy định chung", "path": "shared.md", "users": [], "groups": ["g1"]},
            {"id": "private", "title": "Quy định thưởng riêng", "path": "private.md", "users": ["u1"], "groups": []},
        ]}), encoding="utf-8")
        self.calls = []
        def handler(request):
            self.calls.append(request)
            return httpx.Response(200, json={"sheets": [grid()]})
        self.client = SheetsClient(config(ZALO_GROUP_ACCESS="members", ZALO_SHEETS_GROUPS="g1"),
                                   client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                                   token_provider=AsyncMock(return_value="fake"))
        settings = Settings("fake", frozenset({"u1"}), frozenset({"g1"}), state_dir=root,
                            knowledge_dir=kb, group_access="members")
        self.service = WorkService(settings, sheets_client=self.client)

    async def asyncTearDown(self):
        await self.service.close()
        self.folder.cleanup()

    async def test_member_reads_public_calendar_registers_then_queries_own_and_deletes(self):
        response = await self.service.handle(incoming("Hôm nay phòng H201 có lớp nào?"))
        self.assertIsNotNone(response)
        self.assertEqual(1, len(self.calls))
        response = await self.service.handle(incoming("Ngày mai mình có lịch gì?"))
        self.assertIn("/dangky", response)
        self.assertEqual(1, len(self.calls))  # unknown identity never guesses a roster
        self.assertIn("Đã lưu", await self.service.handle(incoming("Tôi là Coach Alpha")))
        response = await self.service.handle(incoming("Lịch của tôi ngày 06/10/2026"))
        self.assertIn("Coach Alpha", response)
        self.assertNotIn("Coach Gamma", response)
        self.assertIn("/dangky", await self.service.handle(incoming("/lichcuatoi 06/10/2026", user="other")))
        await self.service.handle(incoming("/hoso xoa"))
        self.assertIsNone(self.service.profiles.get(incoming()))

    async def test_failed_registration_does_not_replace_existing_profile(self):
        await self.service.handle(incoming("/dangky Coach Alpha"))
        response = await self.service.handle(incoming("/dangky Nobody"))
        self.assertIn("Chưa tìm thấy", response)
        self.assertEqual("Coach Alpha", self.service.profiles.get(incoming()))
        self.assertIn("chưa được cấp quyền", await self.service.handle(incoming("/dangky Coach Alpha", chat="g2")))
        self.assertIsNone(await self.service.handle(incoming("Tôi là Coach Alpha", user="u1", kind="PRIVATE", chat="dm")))

    async def test_natural_policy_is_grounded_and_private_document_never_leaks(self):
        response = await self.service.handle(incoming("Xin nghỉ phép cần báo ai?"))
        self.assertIn("2 ngày", response)
        self.assertIn("shared.md", response)
        for user in ("new-user", "u1"):
            response = await self.service.handle(incoming("Quy định thưởng riêng thế nào?", user=user))
            self.assertNotIn("PRIVATE-BONUS-SECRET", response)
            self.assertNotIn("private.md", response)
        response = await self.service.handle(incoming("Chính sách mua máy bay có mức nào?"))
        self.assertIn("Chưa tìm thấy", response)
        self.assertIsNotNone(response)  # no hallucinated policy from model fallback

    async def test_new_member_cannot_create_reminder_or_issue_through_either_syntax(self):
        for text in ("/nhac sau 1 phut | test", "nhắc mình sau 1 phút: test", "/issue tao T | B", "/issue xacnhan abc"):
            self.assertIn("cấp quyền riêng", await self.service.handle(incoming(text)))
        self.assertFalse(self.calls)
        self.assertEqual([], self.service.reminders.list_for("new-user", "g1", "GROUP"))
        self.assertEqual(0, self.service.issues.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0])

    async def test_group_members_do_not_override_per_document_user_restrictions(self):
        path = self.service.knowledge.root / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data['documents'][0]['users'] = ['u1']
        path.write_text(json.dumps(data), encoding="utf-8")
        response = await self.service.handle(incoming("Xin nghỉ phép cần báo ai?"))
        self.assertNotIn("2 ngày", response)
        self.assertNotIn("shared.md", response)


class GroupAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await contract.AdapterTests.asyncSetUp(self)
        self.adapter.settings = replace(self.adapter.settings, group_access="members")
        self.adapter.work = WorkService(self.adapter.settings, environ={})
        self.adapter._bot_names = ("Bot LabCoach", "bot.synthetic-handle")

    async def asyncTearDown(self):
        await contract.AdapterTests.asyncTearDown(self)

    async def test_new_members_admitted_only_in_approved_group_and_replies_stay_there(self):
        for user in ("new-a", "new-b"):
            await self.adapter._dispatch(contract.update(user, user=user, chat="g1", kind="GROUP", text="@Bot LabCoach /menu"))
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)
        self.assertTrue(all(call.args[0] == "g1" for call in self.adapter.client.send_text_chunk.await_args_list))
        self.assertTrue(all("MENU BOT" in call.args[1] for call in self.adapter.client.send_text_chunk.await_args_list))
        self.handler.assert_not_awaited()
        for chat, kind in (("g2", "GROUP"), ("dm1", "PRIVATE")):
            await self.adapter._dispatch(contract.update(chat, user="new-a", chat=chat, kind=kind, text="@Bot LabCoach /menu"))
        self.assertEqual(2, self.adapter.client.send_text_chunk.await_count)

    async def test_connect_uses_display_name_and_account_handle_for_group_menu(self):
        await self.adapter.disconnect()
        client = SimpleNamespace(
            get_me=AsyncMock(return_value={"id": "synthetic-id", "can_join_groups": True,
                                          "account_name": "bot.synthetic-handle", "display_name": "Bot LabCoach"}),
            get_webhook_info=AsyncMock(return_value={"url": ""}), close=AsyncMock(),
            send_text_chunk=AsyncMock(return_value={"message_id": "reply"}),
        )
        service = WorkService(self.adapter.settings, environ={})
        with patch.object(contract.adapter_module, "ZaloClient", return_value=client), \
                patch.object(contract.adapter_module, "WorkService", return_value=service), \
                patch.object(self.adapter, "_poll_loop", new=AsyncMock()), \
                patch.object(self.adapter, "_reminder_loop", new=AsyncMock()):
            self.assertTrue(await self.adapter.connect())
            for index, name in enumerate(("Bot LabCoach", "bot.synthetic-handle")):
                await self.adapter._dispatch(contract.update(str(index), user="new-a", chat="g1", kind="GROUP",
                                                             text=f"@{name} /menu"))
        self.assertEqual(2, client.send_text_chunk.await_count)
        self.assertTrue(all("MENU BOT" in call.args[1] for call in client.send_text_chunk.await_args_list))
        self.handler.assert_not_awaited()

    async def test_private_chat_keeps_mention_in_model_message(self):
        text = "@Bot LabCoach /menu"
        await self.adapter._dispatch(contract.update("private", user="u1", chat="dm1", kind="PRIVATE", text=text))
        self.handler.assert_awaited_once()
        self.assertEqual(text, self.handler.await_args.args[0].text)
        self.assertNotIn("MENU BOT", self.adapter.client.send_text_chunk.await_args.args[1])

    async def test_display_mention_natural_body_reaches_work_without_name_prefix(self):
        text = "Ngày mai mình có lịch gì?"
        with patch.object(self.adapter.work, "handle", wraps=self.adapter.work.handle) as handle:
            await self.adapter._dispatch(contract.update("natural", user="new-a", chat="g1", kind="GROUP",
                                                         text=f"@Bot LabCoach {text}"))
            self.assertEqual(text, handle.await_args.args[0].text)
        self.handler.assert_not_awaited()

    async def test_other_and_middle_mentions_do_not_become_menu_commands(self):
        for index, text in enumerate(("@Other /menu", "@Bot LabCoachElse /menu", "Hãy đọc @Bot LabCoach /menu")):
            await self.adapter._dispatch(contract.update(str(index), user="new-a", chat="g1", kind="GROUP", text=text))
        self.assertEqual(3, self.adapter.client.send_text_chunk.await_count)
        self.assertTrue(all("MENU BOT" not in call.args[1] for call in self.adapter.client.send_text_chunk.await_args_list))
        # Other mentions remain ordinary text, never a privileged menu command.
        self.assertEqual(3, self.handler.await_count)

    async def test_mentioned_ordinary_chat_reaches_hermes_once_for_allowed_group_member(self):
        update = contract.update('ordinary', user='new-a', chat='g1', kind='GROUP',
                                 text='@Bot LabCoach Giải thích Python là gì?')
        await self.adapter._dispatch(update)
        await self.adapter._dispatch(update)
        self.handler.assert_awaited_once()
        event = self.handler.await_args.args[0]
        self.assertEqual('Giải thích Python là gì?', event.text)
        self.assertEqual(('g1', 'new-a', 'group'),
                         (event.source.chat_id, event.source.user_id, event.source.chat_type))
        self.assertFalse(event.allow_gateway_control)
        self.assertEqual([], self.adapter.toolsets_for_source(event.source))
        self.adapter.client.send_text_chunk.assert_awaited_once_with('g1', 'Xin chào bạn')
        self.assertFalse(self.adapter._session_tasks)

    async def test_group_member_conversation_sessions_stay_separate_by_group_and_user(self):
        self.adapter.settings = replace(self.adapter.settings, allowed_groups=frozenset({'g1', 'g2'}))
        for index, (user, group) in enumerate((('new-a', 'g1'), ('new-b', 'g1'), ('new-a', 'g2'))):
            # A plain text event models a reply delivered by the official API.
            await self.adapter._dispatch(contract.update(str(index), user=user, chat=group, kind='GROUP',
                                                         text='Viết giúp mình lời chúc sinh nhật'))
        self.assertEqual(3, self.handler.await_count)
        events = [call.args[0] for call in self.handler.await_args_list]
        self.assertEqual(3, len({self.adapter._event_session_key(event) for event in events}))
        self.assertEqual(['g1', 'g1', 'g2'], [call.args[0] for call in self.adapter.client.send_text_chunk.await_args_list])
        for event in events:
            self.assertEqual([], self.adapter.toolsets_for_source(event.source))
            self.assertFalse(event.allow_gateway_control)

    async def test_general_chat_does_not_admit_member_into_unapproved_group_or_private_chat(self):
        for index, (group, kind) in enumerate((('g2', 'GROUP'), ('dm-new', 'PRIVATE'))):
            await self.adapter._dispatch(contract.update(str(index), user='new-a', chat=group, kind=kind,
                                                         text='@Bot LabCoach Giải thích Python là gì?'))
        self.handler.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_not_awaited()

    async def test_member_conversation_fails_closed_when_work_boundary_is_missing(self):
        await self.adapter.work.close()
        self.adapter.work = None
        await self.adapter._dispatch(contract.update('missing-work', user='new-a', chat='g1', kind='GROUP',
                                                     text='@Bot LabCoach Giải thích Python là gì?'))
        self.handler.assert_not_awaited()
        self.adapter.client.send_text_chunk.assert_not_awaited()
        self.assertEqual('not_admitted', self.adapter.store.connection.execute('SELECT status FROM messages').fetchone()[0])

    async def test_group_work_reads_and_unknown_slash_commands_stay_in_application(self):
        for index, text in enumerate(('Quy định xin nghỉ phép thế nào?', 'Ngày mai mình có lịch gì?',
                                      'Ai phụ trách phòng lab?', '/unknown-action', '/dongy 1234',
                                      '/nhac sau 1 phut | Nội dung thử', '/issue tao Tiêu đề | Mô tả')):
            await self.adapter._dispatch(contract.update(str(index), user='new-a', chat='g1', kind='GROUP',
                                                         text='@Bot LabCoach ' + text))
        self.handler.assert_not_awaited()
        self.assertEqual(7, self.adapter.client.send_text_chunk.await_count)
        self.assertEqual([], self.adapter.work.reminders.list_for('new-a', 'g1', 'GROUP'))
        self.assertEqual(0, self.adapter.work.issues.db.execute('SELECT COUNT(*) FROM drafts').fetchone()[0])

    async def test_group_model_reply_cannot_send_to_a_private_or_other_group_destination(self):
        denied = []
        async def handler(event):
            for chat in ('dm-new', 'g2'):
                denied.append(await self.adapter.send(chat, 'Synthetic wrong destination', reply_to=event.message_id))
            return 'Câu trả lời chung'
        self.handler.side_effect = handler
        await self.adapter._dispatch(contract.update('route', user='new-a', chat='g1', kind='GROUP',
                                                     text='@Bot LabCoach Giải thích Python là gì?'))
        self.assertEqual(2, len(denied))
        self.assertTrue(all(not result.success for result in denied))
        self.adapter.client.send_text_chunk.assert_awaited_once_with('g1', 'Câu trả lời chung')

    async def test_repeated_registration_event_and_unknown_question_do_not_call_model(self):
        update = contract.update(user="new", chat="g1", kind="GROUP", text="Ngày mai mình có lịch gì?")
        await self.adapter._dispatch(update)
        await self.adapter._dispatch(update)
        self.assertEqual(1, self.adapter.client.send_text_chunk.await_count)
        await self.adapter._dispatch(contract.update("other", user="new", chat="g1", kind="GROUP", text="Nội bộ làm việc thế nào?"))
        self.handler.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
