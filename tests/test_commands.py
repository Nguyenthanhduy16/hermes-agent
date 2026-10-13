"""User-facing commands tested without credentials, real chat or model calls."""
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import unicodedata
from unittest.mock import AsyncMock, patch

import httpx

from hermes_zalo.commands import route, parse_calendar, help_text
from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.lab_calendar import parse_sheet, format_query
from hermes_zalo.reminders import parse_reminder_command
from hermes_zalo.sheets import SheetsClient
from hermes_zalo.work import WorkService
from test_lab_calendar import grid, config


TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 16, 30, tzinfo=timezone.utc).timestamp()  # 23:30 VN


class CommandParsingTests(unittest.TestCase):
    def test_aliases_unicode_and_whitespace_preserve_content(self):
        for text, expected in [("/MENU", ("/trogiup", "")), ("/help\nnhắc", ("/trogiup", "nhắc")),
                ("xem lịch ngày mai", ("/lich", "ngày mai")), ("LỊCH HÔM NAY", ("/lich", "HÔM NAY")),
                ("hỏi tài liệu Giờ làm việc?", ("/hoi", "Giờ làm việc?")),
                ("xem nhắc việc", ("/nhac", "xem")), ("/GÓPÝ\tNội dung", ("/phanhoi", "Nội dung"))]:
            self.assertEqual(expected, route(unicodedata.normalize("NFD", text)))
        for text in ("Xin chào", "Lịch sử Việt Nam", "Đồng ý", "Hãy giải thích lệnh /nhac", "Tôi muốn học cách tạo issue"):
            self.assertEqual("", route(text)[0])

    def test_dates_and_year_are_explicit_with_vietnam_day_reference(self):
        for text, expected in [("homnay", TODAY), ("ngày mai", date(2026, 10, 7)),
                ("hôm qua", date(2026, 10, 5)), ("6/10", TODAY), ("06/10/2026", TODAY), ("2026-10-06", TODAY)]:
            query = parse_calendar(text, today=TODAY)
            self.assertEqual((expected, expected), (query.start, query.end))
        self.assertEqual(date(2027, 1, 1), parse_calendar("mai", today=date(2026, 12, 31)).start)

    def test_week_and_copyable_next_page_preserve_dates_and_filters(self):
        query = parse_calendar("tuần này | khóa=Track 1 | coach=Nguyễn Văn An", today=TODAY)
        self.assertEqual((date(2026, 10, 5), date(2026, 10, 11)), (query.start, query.end))
        later = parse_calendar(query.command(2).removeprefix("/lich "), today=date(2030, 1, 1))
        self.assertEqual((query.start, query.end, query.filters, 2), (later.start, later.end, later.filters, later.page))

    def test_bad_dates_filters_or_pages_do_not_fall_back_to_today(self):
        for text in ("31/02/2026", "02/03/26", "thứ hai", "homnay | phogn=H201", "homnay | coach=",
                "homnay | coach=An | coach=Bình", "homnay | buoi=trua", "homnay | trang=-1", "homnay | trang=0",
                "2026-10-06..2026-10-15", "2026-10-06..2026-10-05", "homnay | x=secret"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_calendar(text, today=TODAY)

    def test_relative_and_local_reminder_dates_do_not_roll_past_times_to_tomorrow(self):
        for text in ("/nhac mai 09:00 | Gửi báo cáo", "/nhac 07/10/2026 09:00 | Gửi báo cáo"):
            parsed = parse_reminder_command(text, now=NOW)
            self.assertEqual(datetime(2026, 10, 7, 2, 0, tzinfo=timezone.utc).timestamp(), parsed.due_at)
            self.assertEqual("Gửi báo cáo", parsed.text)
        for text in ("/nhac homnay 09:00 | Họp", "/nhac 07/10 09:00 | Họp", "/nhac mai | Họp"):
            with self.assertRaises(ValueError):
                parse_reminder_command(text, now=NOW)
        self.assertEqual("history", parse_reminder_command("/nhac lịch sử", now=NOW).action)

    def test_menu_is_honest_about_disabled_and_unconfigured_features(self):
        for state in ("not_configured", "denied", "invalid"):
            menu = help_text(sheets_state=state)
            self.assertNotIn("/lich", menu)
            self.assertNotIn("/doica", menu)
        menu = help_text(sheets_state="ready", lab=True, tickets=True, issues=True, reminders=True)
        for command in ("/lich homnay", "/lichcuatoi homnay", "/doica", "/dangky", "/nhac", "/thongbao", "/issue"):
            self.assertIn(command, menu)
        for hidden in ("/tailieu", "/hoi", "/phanhoi"):
            self.assertNotIn(hidden, menu)
        self.assertLess(len(menu), 500)
        self.assertIn("chưa được cấp quyền", help_text("lich", sheets_state="denied"))
        self.assertIn("chưa bật", help_text("issue"))
        self.assertNotIn("/issue tao", help_text("issue"))
        self.assertIn("chưa tạo issue", help_text("issue", issues=True))
        self.assertIn("chưa được cấp quyền", help_text("lich", sheets_state="denied", lab=True))


class CalendarQueryTests(unittest.TestCase):
    def test_coach_filter_ignores_accents_but_does_not_match_status_or_word_fragment(self):
        schedule = parse_sheet(grid())
        event = schedule.events[0]
        event.coaches = ["Nguyễn Văn An [Rảnh]", "Nguyễn Văn Thanh [Rảnh]"]
        event.coach_names = ["Nguyễn Văn An", "Nguyễn Văn Thanh"]
        for name, matched in (("nguyen van an", True), ("An", True), ("Rảnh", False), ("Ngu", False), ("Coach Key", True)):
            answer = format_query([schedule], parse_calendar("06/10/2026 | coach=" + name), "fake")
            self.assertEqual(matched, "Lab Coach:" in answer)

    def test_combined_filters_intersect_and_never_guess_missing_rooms(self):
        schedule = parse_sheet(grid())
        answer = format_query([schedule], parse_calendar("06/10/2026 | khoa=Track A | phong=R101 | buoi=sáng"), "fake")
        self.assertIn("Phòng: R101", answer)
        self.assertIn("Lab Coach:\n• Coach Alpha\n• Coach Beta", answer)
        self.assertNotIn("[Rảnh]", answer)
        self.assertNotIn("[Chưa cập nhật]", answer)
        self.assertNotIn("Nguồn", answer)
        self.assertNotIn("docs.google.com", answer)
        self.assertNotIn("Coach Gamma", answer)
        for arg in ("06/10/2026 | phong=R10", "06/10/2026 | khoa=Track B | phong=R101"):
            self.assertIn("Không tìm thấy", format_query([schedule], parse_calendar(arg), "fake"))

    def test_pagination_no_omissions_and_out_of_range_has_recovery_command(self):
        schedule = parse_sheet(grid())
        schedule.events = [deepcopy(schedule.events[0]) for _ in range(7)]
        for i, event in enumerate(schedule.events):
            event.session = f"Session-{i+1}"
        first = format_query([schedule], parse_calendar("06/10/2026"), "fake")
        second = format_query([schedule], parse_calendar("06/10/2026 | trang=2"), "fake")
        self.assertIn("Trang 1/2", first)
        self.assertIn("/lich 2026-10-06 | trang=2", first)
        self.assertNotIn("Session-6", first)
        self.assertIn("Session-6", second)
        self.assertIn("Session-7", second)
        self.assertNotIn("Session-1", second)
        self.assertIn("Về trang đầu", format_query([schedule], parse_calendar("06/10/2026 | trang=3"), "fake"))

    def test_partial_week_and_no_result_do_not_claim_missing_days_are_empty(self):
        schedule = parse_sheet(grid())
        for arg in ("tuan nay", "tuan nay | coach=Nobody"):
            answer = format_query([schedule], parse_calendar(arg, today=TODAY), "fake")
            self.assertIn("Chưa có dữ liệu cho:", answer)
            self.assertIn("05/10/2026", answer)

    def test_large_entries_report_omitted_names_and_stay_below_transport_cap(self):
        schedule = parse_sheet(grid())
        schedule.events = [deepcopy(schedule.events[0]) for _ in range(5)]
        for event in schedule.events:
            event.coaches = ["A" * 1000 for _ in range(20)]
            event.coach_names = list(event.coaches)
            event.course = event.room = event.key_coach = event.period = event.session = "B" * 4000
        answer = format_query([schedule], parse_calendar("06/10/2026"), "fake")
        self.assertLess(len(answer), 12000)
        self.assertEqual(5, answer.count("Lab Coach:"))
        self.assertNotIn("#gid=", answer)
        self.assertIn("còn 20 tên chưa hiển thị", answer)


class CommandServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = TemporaryDirectory()
        self.calls = []
        def handler(req):
            self.calls.append(req)
            return httpx.Response(200, json={"sheets": [grid()]})
        self.client = SheetsClient(config(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), token_provider=AsyncMock(return_value="fake"))
        self.service = WorkService(Settings("fake-token", frozenset({"u1"}), state_dir=Path(self.folder.name)), sheets_client=self.client)

    async def asyncTearDown(self):
        await self.service.close()
        self.folder.cleanup()

    async def send(self, text, *, user="u1", chat="dm", kind="PRIVATE", mid="1"):
        return await self.service.handle(IncomingMessage(chat, user, mid, kind, text))

    async def test_menu_empty_commands_and_unknown_command_never_contact_google(self):
        for text, expected in (("/menu", "MENU"), ("/nhac", "NHẮC VIỆC"), ("/hoi", "HỎI TÀI LIỆU"),
                               ("/thongbao", "THÔNG BÁO"), ("/issue", "chưa bật"),
                               ("/phanhoi", "GỬI GÓP Ý"), ("/lichh", "chưa nhận ra")):
            self.assertIn(expected, await self.send(text))
        self.assertFalse(self.calls)
        menu = await self.send("/menu")
        self.assertIn("/lich homnay", menu)
        self.assertIn("/lichcuatoi homnay", menu)
        self.assertIn("Nhắc lịch: /nhac", menu)
        self.assertIn("Thông báo: /thongbao", menu)
        self.assertIn("Tạo issue: /issue (chưa bật)", menu)
        self.assertNotIn("/doica", menu)  # Tickets is off in this fixture.
        self.assertNotIn("/lich", await self.send("/menu", user="u2"))
        self.assertFalse(self.calls)
        self.assertEqual([], self.service.reminders.list_for("u1", "dm", "PRIVATE"))
        self.assertIsNone(await self.send("Xin chào"))

    async def test_menu_and_notifications_do_not_open_tickets_or_create_work(self):
        self.service._tickets_values["TICKETS_CHAT_MODE"] = "lab_live_test"
        with patch("hermes_zalo.tickets.runtime.open_chat", side_effect=AssertionError("must not open")):
            for command in ("/menu", "/thongbao", "/help thongbao"):
                response = await self.send(command)
                self.assertIn("/nhac", response)
                self.assertIn("/doica" if command == "/menu" else "/choxacnhan", response)
        self.assertFalse(self.calls)
        self.assertFalse(self.service._tickets_initialized)
        self.assertEqual([], self.service.reminders.list_for("u1", "dm", "PRIVATE"))
        self.assertEqual(0, self.service.issues.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0])

    async def test_group_readers_do_not_gain_write_menu_or_notification_details(self):
        self.service.settings = replace(self.service.settings, allowed_groups=frozenset({"g1"}), group_access="members")
        self.client.settings = replace(self.client.settings, groups=frozenset({"g1"}), group_members=True, issues_enabled=True)
        self.service._tickets_values["TICKETS_CHAT_MODE"] = "lab_live_test"
        menu = await self.send("/menu", user="reader", chat="g1", kind="GROUP")
        self.assertIn("/lich homnay", menu)
        for command in ("/nhac", "/thongbao", "/issue", "/doica"):
            self.assertNotIn(command, menu)
        notice = await self.send("/thongbao", user="reader", chat="g1", kind="GROUP")
        for command in ("/nhac xem", "/nhac lichsu", "/choxacnhan"):
            self.assertNotIn(command, notice)
        self.assertIn("Chưa có mục thông báo", notice)
        for chat, kind in (("g2", "GROUP"), ("dm", "PRIVATE")):
            menu = await self.send("/menu", user="reader", chat=chat, kind=kind)
            self.assertNotIn("/issue", menu)
            self.assertNotIn("/nhac", menu)
        writer = await self.send("/thongbao", chat="g1", kind="GROUP")
        self.assertIn("/nhac xem", writer)
        self.assertIn("/choxacnhan", writer)
        self.assertIn("nhắn riêng cho bot", writer)
        self.assertFalse(self.calls)

    async def test_issue_entry_tracks_sheet_permission_and_enabled_state(self):
        self.client.settings = replace(self.client.settings, issues_enabled=True)
        menu = await self.send("/menu")
        self.assertIn("Tạo issue: /issue", menu)
        self.assertNotIn("chưa bật", menu)
        self.assertIn("/issue xacnhan", await self.send("/issue"))
        self.client.settings = replace(self.client.settings, users=frozenset())
        self.assertNotIn("/issue", await self.send("/menu"))
        self.assertNotIn("/issue tao", await self.send("/issue"))
        self.assertIn("/nhac", await self.send("/menu"))
        self.assertFalse(self.calls)
        self.assertEqual(0, self.service.issues.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0])

    async def test_read_alias_and_filters_route_to_same_calendar_with_permissions(self):
        reply = await self.send("xem lịch 06/10/2026 | phòng=R101")
        self.assertIn("Coach Alpha", reply)
        self.assertNotIn("Coach Gamma", reply)
        self.assertEqual(1, len(self.calls))
        for user, chat, kind in (("u2", "dm", "PRIVATE"), ("u1", "g1", "GROUP")):
            reply = await self.send("lịch 06/10/2026 | trang=2", user=user, chat=chat, kind=kind)
            self.assertIn("chưa được cấp quyền", reply)
        self.assertEqual(1, len(self.calls))

    async def test_ambiguous_reminder_creates_nothing_and_history_keeps_cancelled(self):
        reply = await self.send("/nhac mai | Họp nhóm")
        self.assertIn("/help nhac", reply)
        self.assertEqual([], self.service.reminders.list_for("u1", "dm", "PRIVATE"))
        await self.send("/nhac sau 10 phut | Họp nhóm", mid="2")
        item = self.service.reminders.list_for("u1", "dm", "PRIVATE")[0]
        await self.send("/nhac huy " + item.id, mid="3")
        self.assertNotIn(item.id, await self.send("/nhac xem", mid="4"))
        self.assertIn(item.id, await self.send("/nhac lichsu", mid="5"))
        self.assertNotIn(item.id, await self.send("/nhac lichsu", user="u2", mid="6"))


if __name__ == "__main__":
    unittest.main()
