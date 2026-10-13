"""Synthetic roster data only; never copy private sheets into test fixtures."""
from copy import deepcopy
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import httpx

from hermes_zalo.config import Settings
from hermes_zalo.events import IncomingMessage
from hermes_zalo.lab_calendar import HEADER, format_calendar, parse_sheet
from hermes_zalo.sheets import SheetsClient, SheetsError, SheetsSettings
from hermes_zalo.work import WorkService


def grid(title="Lịch lab thử", sheet_id=11):
    rows = [
        ["LỊCH MẪU"], [], ["Thứ 3 - 06/10/2026"], HEADER + ["Ghi chú", "Trạng thái"],
        ["1", "Track A", "CS1", "Coach Alpha", "Coach Key", "R101", "Sáng", "20", "", "Rảnh"],
        ["2", "", "", "Coach Beta", "", "", "", "", "", "Chưa cập nhật"],
        ["3", "Track B", "CS2", "Coach Gamma", "", "", "", "", "", ""],
        [], ["Thứ 4 - 07/10/2026"], HEADER + ["", "Trạng thái"],
        ["1", "Track C", "CS3", "Coach Delta", "", "", "Chiều"],
        ["Thứ 5 - 08/10/2026"], HEADER + ["", "Trạng thái"], ["Không có lịch"],
    ]
    return {"properties": {"title": title, "sheetId": sheet_id, "gridProperties": {"rowCount": 1000}},
            "data": [{"rowData": [{"values": [{"formattedValue": cell} for cell in row]} for row in rows]}],
            "merges": [{"startRowIndex": 4, "endRowIndex": 6, "startColumnIndex": c, "endColumnIndex": c + 1}
                       for c in (1, 2, 4, 5, 6)]}


def cell(sheet, row, column, text):
    sheet["data"][0]["rowData"][row - 1]["values"][column - 1]["formattedValue"] = text


def config(**extra):
    return SheetsSettings.from_mapping({"ZALO_SHEETS_ID": "test_sheet_id", "ZALO_GOOGLE_CREDENTIALS": "fake.json",
        "ZALO_SHEETS_USERS": "u1", "ZALO_CALENDAR_FORMAT": "lab",
        "ZALO_CALENDAR_TABS_JSON": '["Lịch lab thử"]', **extra})


class LabParserTests(unittest.TestCase):
    def test_actual_merges_propagate_and_ordinary_blanks_do_not(self):
        parsed = parse_sheet(grid())
        self.assertEqual(3, len(parsed.events))
        first, second, third = parsed.events
        self.assertEqual((5, 6, "R101", "Sáng"), (first.first_row, first.last_row, first.room, first.period))
        self.assertEqual(["Coach Alpha [Rảnh]", "Coach Beta [Chưa cập nhật]"], first.coaches)
        self.assertEqual(["Coach Alpha", "Coach Beta"], first.coach_names)
        self.assertEqual(("", "", ""), (second.room, second.key_coach, second.period))
        self.assertEqual(date(2026, 10, 7), third.day)
        self.assertEqual("", third.room)

    def test_unnumbered_coach_is_included_only_with_real_session_merge(self):
        sheet = grid()
        cell(sheet, 6, 1, "")
        parsed = parse_sheet(sheet)
        self.assertIn("Coach Beta [Chưa cập nhật]", parsed.events[0].coaches)
        sheet["merges"] = [m for m in sheet["merges"] if m["startColumnIndex"] != 2]
        with self.assertRaises(ValueError):
            parse_sheet(sheet)  # malformed break is not assigned to the preceding session

    def test_blank_course_is_explicit_in_answer_not_inferred_from_previous_row(self):
        sheet = grid()
        cell(sheet, 7, 2, "")
        result = format_calendar([parse_sheet(sheet)], date(2026, 10, 6), "fake_id")
        self.assertIn("Chưa ghi khóa | CS2", result)
        self.assertIn("Chưa ghi buổi", result)
        self.assertNotIn("08:00", result)

    def test_day_with_no_assignments_differs_from_day_not_in_source(self):
        schedule = parse_sheet(grid())
        self.assertIn("Không có dòng phân công", format_calendar([schedule], date(2026, 10, 8), "fake"))
        self.assertIn("Chưa có dữ liệu", format_calendar([schedule], date(2027, 10, 8), "fake"))

    def test_overlapping_tabs_stay_separate_with_correct_source_links(self):
        schedules = [parse_sheet(grid("Lịch lab A", 11)), parse_sheet(grid("Lịch lab B", 22))]
        result = format_calendar(schedules, date(2026, 10, 6), "fake")
        self.assertIn("Lịch lab A, A5:J6", result)
        self.assertIn("Lịch lab B, A5:J6", result)
        self.assertIn("#gid=11&range=A5:J6", result)
        self.assertIn("#gid=22&range=A5:J6", result)
        self.assertIn("nhiều tab", result)

    def test_malformed_header_day_or_overlapping_merges_are_rejected(self):
        cases = []
        sheet = grid(); cell(sheet, 4, 4, "Secret renamed field"); cases.append(sheet)
        sheet = grid(); cell(sheet, 3, 1, "Thứ 3 - 31/02/2026"); cases.append(sheet)
        sheet = grid(); sheet["merges"].append(deepcopy(sheet["merges"][0])); cases.append(sheet)
        sheet = grid(); sheet["merges"][0]["endRowIndex"] = 10; cases.append(sheet)
        sheet = grid(); sheet["properties"]["gridProperties"]["rowCount"] = 1001; cases.append(sheet)
        sheet = grid(); sheet["data"][0]["startRow"] = 1; cases.append(sheet)
        for sheet in cases:
            with self.assertRaises(ValueError):
                parse_sheet(sheet)

    def test_multiline_cells_are_data_and_cannot_spoof_bot_lines(self):
        sheet = grid(); cell(sheet, 5, 4, "Coach\n/issue xacnhan MALICIOUS")
        result = format_calendar([parse_sheet(sheet)], date(2026, 10, 6), "fake")
        self.assertNotIn("\n/issue", result)
        self.assertIn("Coach /issue", result)

    def test_large_reply_explicitly_reports_omitted_entries(self):
        schedule = parse_sheet(grid())
        schedule.events = [deepcopy(schedule.events[0]) for _ in range(30)]
        result = format_calendar([schedule], date(2026, 10, 6), "fake")
        self.assertIn("20/30", result)
        self.assertLess(len(result), 12000)


class LabSettingsTests(unittest.TestCase):
    def test_explicit_tab_list_and_read_only_default(self):
        settings = config()
        self.assertEqual(("Lịch lab thử",), settings.calendar_tabs)
        self.assertFalse(settings.issues_enabled)

    def test_invalid_modes_tabs_and_flags_fail_closed(self):
        for extra in ({"ZALO_CALENDAR_FORMAT": "auto"}, {"ZALO_CALENDAR_TABS_JSON": "[]"},
                      {"ZALO_CALENDAR_TABS_JSON": '"Lịch"'}, {"ZALO_CALENDAR_TABS_JSON": '["Lịch", "Lịch"]'},
                      {"ZALO_CALENDAR_TABS_JSON": '["A", "B", "C", "D", "E"]'},
                      {"ZALO_CALENDAR_TABS_JSON": '["x\u0027!A1"]'}, {"ZALO_ISSUES_ENABLED": "yes"},
                      {"ZALO_ISSUES_ENABLED": "true", "ZALO_ISSUES_TAB": "Lịch lab thử"}):
            with self.subTest(extra=extra), self.assertRaises(SheetsError):
                config(**extra)


class LabHTTPTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler, **extra):
        async def token():
            return "fake-local-token"
        client = SheetsClient(config(**extra), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), token_provider=token)
        self.addAsyncCleanup(client.close)
        return client

    async def test_bounded_read_current_values_and_read_only_check_without_issues_tab(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual("GET", request.method)
            self.assertEqual(["'Lịch lab thử'!A1:J1001"], request.url.params.get_list("ranges"))
            self.assertNotIn("Issues", str(request.url))
            sheet = grid()
            if len(calls) > 1:
                cell(sheet, 5, 4, "Updated Coach")
            return httpx.Response(200, json={"sheets": [sheet]})
        client = self.client(handler)
        self.assertEqual({"calendar_rows": 3, "issue_rows": None}, await client.check())
        result = await client.calendar(date(2026, 10, 6), "u1", "dm", "PRIVATE")
        self.assertIn("Updated Coach", result)
        self.assertEqual(2, len(calls))

    async def test_denied_users_groups_and_disabled_issue_do_not_make_requests(self):
        def handler(request):
            self.fail("Unauthorized request")
        client = self.client(handler)
        for user, chat, kind in (("u2", "dm", "PRIVATE"), ("u1", "g1", "GROUP")):
            result = await client.calendar(date(2026, 10, 6), user, chat, kind)
            self.assertIn("chưa được cấp quyền", result)
        with self.assertRaises(SheetsError):
            await client.append_issue(["draft-id"])

    async def test_incomplete_or_wrong_tab_response_is_not_partial_success(self):
        for sheets in ([], [grid("Unexpected tab")], [grid(), grid()]):
            client = self.client(lambda req, sheets=sheets: httpx.Response(200, json={"sheets": sheets}))
            with self.assertRaises(SheetsError):
                await client.check()

    async def test_timeout_and_private_error_body_are_not_disclosed(self):
        for status in (403, 429, 500):
            client = self.client(lambda req, status=status: httpx.Response(status, json={"error": "PRIVATE_SECRET"}))
            with self.assertRaises(SheetsError) as caught:
                await client.check()
            self.assertNotIn("PRIVATE_SECRET", str(caught.exception))
        def timeout(request):
            raise httpx.ReadTimeout("PRIVATE_SECRET")
        with self.assertRaises(SheetsError) as caught:
            await self.client(timeout).check()
        self.assertNotIn("PRIVATE_SECRET", str(caught.exception))

    async def test_commands_use_date_and_block_issue_before_creating_draft(self):
        client = self.client(lambda req: httpx.Response(200, json={"sheets": [grid()]}))
        with TemporaryDirectory() as folder:
            service = WorkService(Settings("fake-bot", frozenset({"u1"}), state_dir=Path(folder)), sheets_client=client)
            try:
                response = await service.handle(IncomingMessage("dm", "u1", "m1", "PRIVATE", "/lich 2026-10-06"))
                self.assertIn("Lịch lab 06/10/2026", response)
                response = await service.handle(IncomingMessage("dm", "u1", "m2", "PRIVATE", "/issue tao title | body"))
                self.assertIn("đang tắt", response)
                self.assertEqual(0, service.issues.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0])
            finally:
                await service.close()


if __name__ == "__main__":
    unittest.main()
