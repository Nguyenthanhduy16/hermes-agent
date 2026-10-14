"""Natural navigation and group chat boundaries, using only synthetic data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import unicodedata
from unittest.mock import patch

from hermes_zalo.commands import parse_calendar
from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.knowledge import NO_SOURCES
from hermes_zalo.natural import interpret, is_work_question
from hermes_zalo.nlu import OUT_OF_SCOPE
from hermes_zalo.work import WorkService


class NaturalConversationTests(unittest.TestCase):
    def test_personal_availability_uses_registered_schedule_without_guessing_identity(self):
        for question in ("Mai mình có ca nào?", "Ngày mai mình rảnh không?",
                         "Ngày mai tôi trực không?", "Mình có bận ngày mai không?",
                         "Ngay mai minh ranh khong?"):
            for text in (question, unicodedata.normalize("NFD", question)):
                command, argument = interpret(text)
                self.assertEqual("/lichcuatoi", command, text)
                self.assertEqual(parse_calendar("mai"), parse_calendar(argument))
        self.assertEqual(("/lich", "help"), interpret("Lịch thứ hai"))

    def test_general_learning_or_friend_questions_do_not_become_schedule_reads(self):
        for question in ("Lớp học Python là gì?", "Tôi muốn tìm một lớp học Python",
                         "Tôi có bạn ngày mai đến chơi", "Giải thích lịch sử Python",
                         "Viết giúp mình lời chúc sinh nhật", "Xin chào!"):
            self.assertEqual("", interpret(question)[0], question)
            self.assertFalse(is_work_question(question), question)

    def test_calendar_evening_does_not_imply_first_person(self):
        self.assertEqual(('/lich', 'mai | buoi=toi'), interpret('Lịch tối mai'))
        self.assertEqual(('/lichcuatoi', 'ngay mai'), interpret('Lịch tôi ngày mai'))

    def test_reminder_navigation_precedes_calendar_and_preserves_explicit_content(self):
        for text, expected in (("nhắc lịch giúp mình", ("/trogiup", "nhac")),
                               ("Xem các nhắc lịch của tôi", ("/nhac", "xem")),
                               ("Xem lịch sử nhắc việc", ("/nhac", "lichsu")),
                               ("Mình có thông báo gì không?", ("/thongbao", ""))):
            self.assertEqual(expected, interpret(text))
        text = "nhắc mình sau 10 phút: Báo lỗi lịch ngày mai"
        self.assertEqual(("", text), interpret(text))

    def test_internal_work_questions_have_a_grounded_fallback(self):
        for question in ("Ai phụ trách phòng lab?", "Nhóm mình nghỉ Tết khi nào?",
                         "Tài liệu nội bộ nằm ở đâu?", "Lương tháng này bao nhiêu?",
                         "Có thay đổi phân công nào không?"):
            self.assertTrue(is_work_question(question), question)


class NaturalConversationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.work = WorkService(Settings("test-bot", frozenset({"u1"}), frozenset({"g1"}),
                                        group_access="members", state_dir=Path(self.temp.name)), environ={})

    async def asyncTearDown(self):
        await self.work.close()
        self.temp.cleanup()

    async def say(self, text, user="member"):
        return await self.work.handle(IncomingMessage("g1", user, text, "GROUP", text))

    async def test_general_chat_returns_scope_guide_and_internal_questions_stay_grounded(self):
        with patch.object(self.work.knowledge, "answer", return_value=NO_SOURCES) as knowledge:
            for text in ("Xin chào", "Giải thích Python là gì?", "Viết giúp mình lời chúc sinh nhật"):
                reply = await self.say(text)
                self.assertEqual(OUT_OF_SCOPE, reply)
                self.assertFalse(reply.context_eligible)
            knowledge.assert_not_called()
            for text in ("Ai phụ trách phòng lab?", "Nhóm mình nghỉ Tết khi nào?", "Quy định xin nghỉ phép?"):
                self.assertIn(NO_SOURCES, await self.say(text))
            self.assertEqual(3, knowledge.call_count)
        self.assertIn("Google Sheets", await self.say("Ngày mai mình rảnh không?"))

    async def test_natural_feature_requests_do_not_create_or_confirm_work(self):
        for text in ("Nhắc lịch giúp mình", "Mình muốn tạo issue", "Mình muốn đổi ca",
                     "Xem thông báo", "/lệnhkhôngcó"):
            self.assertIsNotNone(await self.say(text, user="u1"))
        self.assertIn("cấp quyền riêng", await self.say("Xem các nhắc lịch của tôi"))
        self.assertIn("cấp quyền riêng", await self.say("nhắc mình sau 10 phút: Họp nhóm"))
        self.assertEqual([], self.work.reminders.list_for("u1", "g1", "GROUP"))
        self.assertEqual(0, self.work.issues.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
