"""All requests here use MockTransport; these are not live Sheets tests."""
import json
from pathlib import Path
import unittest

import httpx

from hermes_zalo.tickets.providers import (
    ASSIGNMENT_HEADER, ASSIGNMENT_KEYS, GoogleSheetsAssignments, TicketsSheetsSettings,
    TicketsSourceError, TicketsWriteUncertain,
)


SID = "tickets_test_spreadsheet"
PROD = "existing_calendar_sheet"


def assignment(identity="ca-A", member="A", name="An", day=10):
    return [identity, member, name, f"2035-10-{day:02d}T08:00:00+07:00",
            f"2035-10-{day:02d}T10:00:00+07:00", "Lab 1", "P101", "Sáng"]


def grid(rows=None, **overrides):
    if rows is None:
        rows = [assignment(), assignment("ca-B", "B", "Bình", 11)]
    sheet = {
        "properties": {"sheetId": 7, "title": "TicketsTest", "gridProperties": {"rowCount": 1000}},
        "data": [{"rowData": [{"values": [{"userEnteredValue": {"stringValue": value},
                                             "effectiveValue": {"stringValue": value}}
                                            for value in row]} for row in [ASSIGNMENT_HEADER, *rows]]}],
    }
    sheet.update(overrides)
    return {"spreadsheetId": SID, "sheets": [sheet]}


def settings(**changes):
    return TicketsSheetsSettings(**(dict(spreadsheet_id=SID, credentials_file=Path("fake-key.json"),
                                          production_spreadsheet_id=PROD, write_enabled=True,
                                          exclusive_writer=True) | changes))


class TicketsSettingsTests(unittest.TestCase):
    def mapping(self, **changes):
        return dict(TICKETS_TEST_SHEETS_ID=SID, TICKETS_TEST_SHEETS_TAB="TicketsTest",
                    TICKETS_TEST_GOOGLE_CREDENTIALS="secret-local-path.json", ZALO_SHEETS_ID=PROD) | changes

    def test_explicit_test_config_and_no_fallback(self):
        self.assertIsNone(TicketsSheetsSettings.from_mapping({"ZALO_SHEETS_ID": PROD}))
        s = TicketsSheetsSettings.from_mapping(self.mapping())
        self.assertFalse(s.write_enabled)
        self.assertNotIn("secret-local-path", repr(s))
        without_tab = self.mapping()
        del without_tab["TICKETS_TEST_SHEETS_TAB"]
        self.assertEqual("TicketsTest", TicketsSheetsSettings.from_mapping(without_tab).tab)
        for changes in ({"TICKETS_TEST_SHEETS_ID": PROD}, {"ZALO_SHEETS_ID": ""},
                        {"TICKETS_TEST_SHEETS_ID": "https://docs.google.com/test"},
                        {"TICKETS_TEST_SHEETS_TAB": "test'!A1"},
                        {"TICKETS_TEST_GOOGLE_CREDENTIALS": ""},
                        {"TICKETS_TEST_WRITE_ENABLED": "true"},
                        {"TICKETS_TEST_EXCLUSIVE_WRITER": "yes"},
                        {"TICKETS_TEST_SHEETS_ID": "", "TICKETS_TEST_WRITE_ENABLED": "true"}):
            with self.subTest(changes=changes), self.assertRaises(TicketsSourceError):
                TicketsSheetsSettings.from_mapping(self.mapping(**changes))


class TicketsSheetsTests(unittest.TestCase):
    def provider(self, handler, **changes):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = GoogleSheetsAssignments(settings(**changes), client=client,
                                           token_provider=lambda: "fake-test-token")
        self.addCleanup(provider.close)
        return provider

    def test_reads_only_selected_tab_with_literal_aware_fields(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=grid())
        items = self.provider(handler).list_assignments()
        self.assertEqual(dict(zip(ASSIGNMENT_KEYS, assignment())), items[0])
        self.assertEqual("'TicketsTest'!A1:H1002", calls[0].url.params["ranges"])
        self.assertIn("userEnteredValue", calls[0].url.params["fields"])
        self.assertIn("merges", calls[0].url.params["fields"])
        self.assertEqual("sheets.googleapis.com", calls[0].url.host)
        self.assertNotIn(PROD, str(calls[0].url))

    def test_rejects_duplicate_ids_and_inconsistent_member_names(self):
        cases = [[assignment(), assignment()],
                 [assignment(), assignment("ca-X", "A", "Someone else")]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(TicketsSourceError):
                self.provider(lambda req: httpx.Response(200, json=grid(rows))).list_assignments()

    def test_refuses_unknown_structure_instead_of_guessing(self):
        cases = []
        merged = grid(merges=[{"startColumnIndex": 1, "endColumnIndex": 3,
                              "startRowIndex": 1, "endRowIndex": 2}])
        cases.append(merged)
        for value in ({"formulaValue": "=SECRET()"}, {"numberValue": 123}, {"boolValue": True}):
            data = grid()
            data["sheets"][0]["data"][0]["rowData"][1]["values"][0]["userEnteredValue"] = value
            cases.append(data)
        wrong_header = grid()
        wrong_header["sheets"][0]["data"][0]["rowData"][0]["values"].reverse()
        cases.append(wrong_header)
        wrong_source = grid()
        wrong_source["spreadsheetId"] = PROD
        cases.append(wrong_source)
        wrong_tab = grid()
        wrong_tab["sheets"][0]["properties"]["title"] = "Live lab"
        cases.append(wrong_tab)
        too_many = grid()
        too_many["sheets"][0]["properties"]["gridProperties"]["rowCount"] = 1002
        cases.append(too_many)
        for data in cases:
            with self.subTest(case=cases.index(data)), self.assertRaises(TicketsSourceError) as cm:
                self.provider(lambda req: httpx.Response(200, json=data)).list_assignments()
            self.assertNotIn("SECRET", str(cm.exception))

    def test_invalid_dates_missing_values_and_controls_fail_closed(self):
        bad_rows = []
        for index, value in ((3, "2035-10-10T08:00:00"), (4, "2035-10-09T08:00:00+07:00"),
                             (6, ""), (2, "Name\nsecret"), (0, "ca A")):
            row = assignment()
            row[index] = value
            bad_rows.append(row)
        for row in bad_rows:
            with self.subTest(row=row), self.assertRaises(TicketsSourceError):
                self.provider(lambda req: httpx.Response(200, json=grid([row]))).list_assignments()

    def test_reread_resolves_inserted_rows_and_swaps_only_owners_in_one_batch(self):
        original = grid()
        moved = grid([assignment("new-row", "C", "Chi", 12), [],
                      assignment("ca-B", "B", "Bình", 11), assignment()])
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=original if len(calls) == 1 else moved)
            return httpx.Response(200, json={"spreadsheetId": SID, "replies": [{}, {}]})
        provider = self.provider(handler)
        a, b = provider.list_assignments()
        provider.swap(a, b, "T-123")
        self.assertEqual(["GET", "GET", "POST"], [call.method for call in calls])
        body = json.loads(calls[-1].content)
        self.assertEqual(2, len(body["requests"]))
        for operation, index, owner in zip(body["requests"], [4, 3], [b, a]):
            update = operation["updateCells"]
            self.assertEqual({"sheetId": 7, "startRowIndex": index, "endRowIndex": index + 1,
                              "startColumnIndex": 1, "endColumnIndex": 3}, update["range"])
            self.assertEqual("userEnteredValue", update["fields"])
            self.assertEqual([{"userEnteredValue": {"stringValue": owner[key]}}
                              for key in ("member_id", "member_name")], update["rows"][0]["values"])

    def test_changed_or_deleted_assignment_prevents_any_write(self):
        snapshots = [dict(zip(ASSIGNMENT_KEYS, assignment())),
                     dict(zip(ASSIGNMENT_KEYS, assignment("ca-B", "B", "Bình", 11)))]
        for data in (grid([assignment()]), grid([assignment(day=12), assignment("ca-B", "B", "Bình", 11)])):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(200, json=data)
            with self.assertRaises(TicketsSourceError):
                self.provider(handler).swap(*snapshots, "T-456")
            self.assertEqual(["GET"], [call.method for call in calls])

    def test_disabled_write_does_not_even_authenticate(self):
        def no_request(request):
            self.fail("Disabled writes must not fetch or write any source")
        with self.assertRaises(TicketsSourceError):
            self.provider(no_request, write_enabled=False).swap({}, {}, "T-test")

    def test_new_third_shift_conflict_is_detected_on_final_read(self):
        a = dict(zip(ASSIGNMENT_KEYS, assignment()))
        b = dict(zip(ASSIGNMENT_KEYS, assignment("ca-B", "B", "Bình", 11)))
        data = grid([assignment(), assignment("ca-B", "B", "Bình", 11),
                     assignment("new-shift-B", "B", "Bình", 10)])
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=data)
        with self.assertRaises(TicketsSourceError) as cm:
            self.provider(handler).swap(a, b, "T-conflict")
        self.assertIn("trùng", str(cm.exception))
        self.assertEqual(["GET"], [request.method for request in calls])

    def test_post_timeout_is_uncertain_and_never_retried(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=grid())
            raise httpx.ReadTimeout("secret credentials should not leak", request=request)
        provider = self.provider(handler)
        a, b = provider.list_assignments()
        with self.assertRaises(TicketsWriteUncertain) as cm:
            provider.swap(a, b, "T-timeout")
        self.assertNotIn("secret", str(cm.exception))
        self.assertEqual(1, sum(call.method == "POST" for call in calls))

    def test_ambiguous_success_and_server_failure_are_uncertain(self):
        for response in (httpx.Response(503, json={"secret": "hidden"}), httpx.Response(204),
                         httpx.Response(408), httpx.Response(200, text="not-json-secret"),
                         httpx.Response(200, json={}), httpx.Response(200, json={"spreadsheetId": PROD}),
                         httpx.Response(200, json={"spreadsheetId": SID, "replies": [{}]})):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(200, json=grid()) if request.method == "GET" else response
            provider = self.provider(handler)
            a, b = provider.list_assignments()
            with self.assertRaises(TicketsWriteUncertain) as cm:
                provider.swap(a, b, "T-ambiguous")
            self.assertNotIn("secret", str(cm.exception))
            self.assertEqual(1, sum(call.method == "POST" for call in calls))

    def test_rate_limit_and_redirect_are_not_followed_or_retried(self):
        for status in (429, 302, 403):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, headers={"Location": "https://example.com/leak"},
                                      json={"error": "secret response"})
            with self.assertRaises(TicketsSourceError) as cm:
                self.provider(handler).list_assignments()
            self.assertNotIn("secret", str(cm.exception))
            self.assertEqual(1, len(calls))

    def test_repeat_old_snapshot_cannot_toggle_owners_back(self):
        state = grid()
        posts = []
        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, json=state)
            posts.append(request)
            rows = state["sheets"][0]["data"][0]["rowData"]
            rows[1]["values"][1:3], rows[2]["values"][1:3] = rows[2]["values"][1:3], rows[1]["values"][1:3]
            return httpx.Response(200, json={"spreadsheetId": SID, "replies": [{}, {}]})
        provider = self.provider(handler)
        a, b = provider.list_assignments()
        provider.swap(a, b, "T-repeat")
        with self.assertRaises(TicketsSourceError):
            provider.swap(a, b, "T-repeat")
        self.assertEqual(1, len(posts))


if __name__ == "__main__":
    unittest.main()
