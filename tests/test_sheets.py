from datetime import date
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

import httpx

from hermes_zalo.sheets import (
    CALENDAR_HEADER, ISSUE_HEADER, SheetsClient, SheetsError, SheetsSettings, SheetsUncertain,
)


async def fake_token():
    return "test-token-never-real"


def sheet_settings(**changes):
    values = dict(spreadsheet_id="test_spreadsheet_id", credentials_file=Path("fake-google-key.json"),
                  users=frozenset({"u1", "u2"}), groups=frozenset({"g1"}))
    values.update(changes)
    return SheetsSettings(**values)


def calendar_row(identity="event1", title="Họp thử nghiệm", users="u1", groups="g1",
                 start="2026-10-05T09:00:00+07:00", end="2026-10-05T10:00:00+07:00", location="Phòng A"):
    return [identity, start, end, title, users, groups, location]


class SheetsSettingsTests(unittest.TestCase):
    def test_id_tabs_and_wildcards_are_validated_before_any_network(self):
        base = {"ZALO_SHEETS_ID": "test_spreadsheet_id", "ZALO_GOOGLE_CREDENTIALS": "fake.json",
                "ZALO_SHEETS_USERS": "u1,u2", "ZALO_SHEETS_GROUPS": "g1"}
        for changes in ({"ZALO_SHEETS_ID": "https://docs.google.com/spreadsheets/d/example"},
                        {"ZALO_CALENDAR_TAB": "Lich'!A1:H100"}, {"ZALO_ISSUES_TAB": "Lich"},
                        {"ZALO_SHEETS_USERS": "*"}, {"ZALO_SHEETS_GROUPS": "g1,*"},
                        {"ZALO_GOOGLE_CREDENTIALS": ""}):
            with self.subTest(changes=changes), self.assertRaises(SheetsError):
                SheetsSettings.from_mapping(base | changes)
        settings = SheetsSettings.from_mapping(base)
        self.assertTrue(settings.allows("u1", "private", "PRIVATE"))
        self.assertTrue(settings.allows("u1", "g1", "GROUP"))
        for scope in (("u3", "g1", "GROUP"), ("u1", "g2", "GROUP"), ("u1", "g1", "CHANNEL")):
            self.assertFalse(settings.allows(*scope))
        self.assertIsNone(SheetsSettings.from_mapping({}))


class SheetsHTTPTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler, **settings_changes):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        client = SheetsClient(sheet_settings(**settings_changes), client=http, token_provider=fake_token)
        self.addAsyncCleanup(client.close)
        return client

    async def test_global_acl_prevents_http_entirely(self):
        def no_request(request):
            self.fail("An unauthorized scope must never fetch a spreadsheet")
        client = self.client(no_request)
        result = await client.calendar(date(2026, 10, 5), "u3", "g1", "GROUP")
        self.assertIn("chưa được cấp quyền", result)

    async def test_dynamic_member_calendar_grant_preserves_row_acl_and_static_permissions(self):
        rows = [CALENDAR_HEADER, calendar_row(users='new-user', title='Member meeting'),
                calendar_row(identity='hidden', users='other-user', title='Hidden confidential', start='invalid-hidden')]
        client = self.client(lambda request: httpx.Response(200, json={'values': rows}), users=frozenset())
        client.calendar_authorizer = AsyncMock(side_effect=lambda user, chat, kind:
                                                (user, chat, kind) == ('new-user', 'dm-new', 'PRIVATE'))
        reply = await client.calendar(date(2026, 10, 5), 'new-user', 'dm-new', 'PRIVATE')
        self.assertIn('Member meeting', reply)
        self.assertNotIn('Hidden confidential', reply)
        self.assertEqual(frozenset(), client.settings.users)
        self.assertFalse(client.settings.allows('new-user', 'dm-new', 'PRIVATE'))
        self.assertIn('chưa được cấp quyền', await client.calendar(date(2026, 10, 5), 'new-user', 'g1', 'GROUP'))

    async def test_local_revocation_during_calendar_fetch_suppresses_data(self):
        active = True
        def handler(request):
            nonlocal active
            active = False
            return httpx.Response(200, json={'values': [CALENDAR_HEADER, calendar_row(title='Suppressed calendar')]})
        client = self.client(handler)
        client.calendar_access_guard = lambda *args: active
        reply = await client.calendar(date(2026, 10, 5), 'u1', 'dm-u1', 'PRIVATE')
        self.assertNotIn('Suppressed calendar', reply)
        self.assertIn('chưa được cấp quyền', reply)

    async def test_calendar_row_acl_filters_before_parsing_private_information(self):
        rows = [CALENDAR_HEADER,
                calendar_row(title="Allowed meeting"),
                calendar_row(identity="hidden", title="Acquisition confidential", users="u3", groups="g9", start="invalid-private-date")]
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"values": rows})
        client = self.client(handler)
        result = await client.calendar(date(2026, 10, 5), "u1", "private", "PRIVATE")
        self.assertIn("Allowed meeting", result)
        self.assertNotIn("Acquisition", result)
        self.assertEqual(1, len(calls))
        self.assertEqual("GET", calls[0].method)
        self.assertIn("'Lich'!A1:G1002", calls[0].url.path)
        self.assertEqual("UNFORMATTED_VALUE", calls[0].url.params["valueRenderOption"])
        self.assertEqual("Bearer test-token-never-real", calls[0].headers["Authorization"])

    async def test_group_row_acl_requires_named_group_and_intersects_users(self):
        rows = [CALENDAR_HEADER,
                calendar_row(identity="shared", title="Shared to g1", users="", groups="g1"),
                calendar_row(identity="narrow", title="Only u1 in g1", users="u1", groups="g1"),
                calendar_row(identity="private", title="Private only", users="u2", groups=""),
                calendar_row(identity="other", title="Other group", users="u2", groups="g2")]
        client = self.client(lambda request: httpx.Response(200, json={"values": rows}))
        group = await client.calendar(date(2026, 10, 5), "u2", "g1", "GROUP")
        self.assertIn("Shared to g1", group)
        for hidden in ("Only u1 in g1", "Private only", "Other group"):
            self.assertNotIn(hidden, group)
        private = await client.calendar(date(2026, 10, 5), "u2", "p2", "PRIVATE")
        self.assertIn("Private only", private)
        self.assertNotIn("Shared to g1", private)

    async def test_timezone_conversion_and_overlapping_midnight_event(self):
        rows = [CALENDAR_HEADER,
                calendar_row(title="Cross midnight", start="2026-10-04T23:30:00+07:00", end="2026-10-05T00:30:00+07:00"),
                calendar_row(identity="utc", title="UTC appointment", start="2026-10-05T03:00:00Z", end="2026-10-05T04:00:00Z"),
                calendar_row(identity="boundary", title="Already ended", start="2026-10-04T23:00:00+07:00", end="2026-10-05T00:00:00+07:00")]
        client = self.client(lambda request: httpx.Response(200, json={"values": rows}))
        result = await client.calendar(date(2026, 10, 5), "u1", "p1", "PRIVATE")
        self.assertIn("Cross midnight", result)
        self.assertIn("05/10 10:00–05/10 11:00: UTC appointment", result)
        self.assertNotIn("Already ended", result)

    async def test_malformed_headers_rows_and_over_limit_are_rejected(self):
        payloads = [{"values": []}, {"values": [CALENDAR_HEADER[::-1]]},
                    {"values": [CALENDAR_HEADER, "bad"]},
                    {"values": [CALENDAR_HEADER, calendar_row() + ["extra"]]},
                    {"values": [CALENDAR_HEADER] + [calendar_row()] * 1001}]
        for payload in payloads:
            with self.subTest(payload_type=str(type(payload["values"]))), self.assertRaises(SheetsError):
                client = self.client(lambda request, payload=payload: httpx.Response(200, json=payload))
                await client.calendar(date(2026, 10, 5), "u1", "p1", "PRIVATE")

    async def test_invalid_or_naive_authorized_dates_are_rejected_without_cell_text(self):
        for start, end in (("bad-secret-date", "2026-10-05T10:00:00+07:00"),
                           ("2026-10-05T09:00:00", "2026-10-05T10:00:00"),
                           ("2026-10-05T11:00:00+07:00", "2026-10-05T10:00:00+07:00")):
            rows = [CALENDAR_HEADER, calendar_row(start=start, end=end)]
            client = self.client(lambda request, rows=rows: httpx.Response(200, json={"values": rows}))
            with self.assertRaises(SheetsError) as caught:
                await client.calendar(date(2026, 10, 5), "u1", "p1", "PRIVATE")
            self.assertNotIn("bad-secret", str(caught.exception))

    async def test_read_transport_and_api_errors_are_sanitized_without_retries(self):
        for failure in (httpx.ReadTimeout("Bearer PRIVATE_TOKEN"), 401, 429, 503):
            calls = []
            def handler(request, failure=failure):
                calls.append(request)
                if isinstance(failure, Exception):
                    raise failure
                return httpx.Response(failure, json={"error": "PRIVATE_TOKEN secret body"})
            client = self.client(handler)
            with self.assertRaises(SheetsError) as caught:
                await client.calendar(date(2026, 10, 5), "u1", "p1", "PRIVATE")
            self.assertNotIn("PRIVATE_TOKEN", str(caught.exception))
            self.assertEqual(1, len(calls))

    async def test_append_is_raw_and_checks_header_before_single_post(self):
        calls = []
        row = ["draft-1", "2026-10-05T00:00:00Z", "=IMPORTXML(unsafe)", "@body", "open", "u1", "p1", "PRIVATE"]
        def handler(request):
            calls.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"values": [ISSUE_HEADER]})
            self.assertEqual("RAW", request.url.params["valueInputOption"])
            self.assertEqual("INSERT_ROWS", request.url.params["insertDataOption"])
            self.assertIn("'Issues'!A:H:append", request.url.path)
            self.assertEqual([row], json.loads(request.content)["values"])
            return httpx.Response(200, json={"updates": {"updatedRows": 1}})
        client = self.client(handler)
        await client.append_issue(row)
        self.assertEqual(["GET", "POST"], [request.method for request in calls])

    async def test_issue_access_guard_denial_prevents_credentials_and_http(self):
        row = ['member-draft', 'now', 'title', 'body', 'open', 'member', 'member-dm', 'PRIVATE']
        def no_request(request):
            self.fail('Denied issue must not contact Sheets')
        client = self.client(no_request)
        client.token_provider = AsyncMock(return_value='synthetic-token')
        with self.assertRaises(SheetsError):
            await client.append_issue(row, access_guard=lambda: False)
        client.token_provider.assert_not_awaited()

    async def test_issue_revocation_during_header_read_prevents_post(self):
        active = True
        methods = []
        row = ['member-draft', 'now', 'title', 'body', 'open', 'member', 'member-dm', 'PRIVATE']
        def handler(request):
            nonlocal active
            methods.append(request.method)
            self.assertEqual('GET', request.method)
            active = False
            return httpx.Response(200, json={'values': [ISSUE_HEADER]})
        client = self.client(handler)
        with self.assertRaises(SheetsError):
            await client.append_issue(row, access_guard=lambda: active)
        self.assertEqual(['GET'], methods)

    async def test_issue_revocation_during_write_credential_refresh_prevents_post(self):
        active = True
        methods, refreshes = [], []
        row = ['member-draft', 'now', 'title', 'body', 'open', 'member', 'member-dm', 'PRIVATE']
        def handler(request):
            methods.append(request.method)
            self.assertEqual('GET', request.method)
            return httpx.Response(200, json={'values': [ISSUE_HEADER]})
        async def refresh_token():
            nonlocal active
            refreshes.append(True)
            if len(refreshes) == 2:
                active = False
            return 'synthetic-token'
        client = self.client(handler)
        client.token_provider = refresh_token
        with self.assertRaises(SheetsError):
            await client.append_issue(row, access_guard=lambda: active)
        self.assertEqual(2, len(refreshes))
        self.assertEqual(['GET'], methods)

    async def test_issue_dynamic_guard_allows_single_write_without_changing_static_lists(self):
        methods = []
        row = ['member-draft', 'now', 'title', 'body', 'open', 'member', 'member-dm', 'PRIVATE']
        def handler(request):
            methods.append(request.method)
            if request.method == 'GET':
                return httpx.Response(200, json={'values': [ISSUE_HEADER]})
            self.assertEqual([row], json.loads(request.content)['values'])
            return httpx.Response(200, json={'updates': {'updatedRows': 1}})
        client = self.client(handler, users=frozenset())
        await client.append_issue(row, access_guard=lambda: True)
        self.assertEqual(['GET', 'POST'], methods)
        self.assertEqual(frozenset(), client.settings.users)
        self.assertFalse(client.settings.allows('member', 'member-dm', 'PRIVATE'))

    async def test_existing_id_and_invalid_header_never_append(self):
        row = ["draft-1", "now", "title", "body", "open", "u1", "p1", "PRIVATE"]
        for rows in ([ISSUE_HEADER, row], [["wrong", "header"]]):
            calls = []
            def handler(request, rows=rows):
                calls.append(request.method)
                self.assertEqual("GET", request.method)
                return httpx.Response(200, json={"values": rows})
            client = self.client(handler)
            if rows[0] == ISSUE_HEADER:
                await client.append_issue(row)
            else:
                with self.assertRaises(SheetsError):
                    await client.append_issue(row)
            self.assertEqual(["GET"], calls)

    async def test_existing_issue_id_with_different_content_is_not_reported_as_created(self):
        desired = ["draft-1", "new-time", "Confirmed title", "Confirmed body", "open", "u1", "p1", "PRIVATE"]
        existing = ["draft-1", "old-time", "Edited title", "Other body", "open", "u2", "g2", "GROUP"]
        calls = []
        def handler(request):
            calls.append(request.method)
            self.assertEqual("GET", request.method)
            return httpx.Response(200, json={"values": [ISSUE_HEADER, existing]})
        client = self.client(handler)
        with self.assertRaises(SheetsError):
            await client.append_issue(desired)
        self.assertEqual(["GET"], calls)

    async def test_multiline_calendar_cells_cannot_spoof_extra_bot_lines(self):
        rows = [CALENDAR_HEADER, calendar_row(title="Cuộc họp\r\nNguồn: giả mạo", location="Phòng A\n/issue xacnhan MALICIOUS")]
        client = self.client(lambda request: httpx.Response(200, json={"values": rows}))
        result = await client.calendar(date(2026, 10, 5), "u1", "p1", "PRIVATE")
        self.assertNotIn("\nNguồn: giả mạo", result)
        self.assertNotIn("\n/issue", result)
        self.assertEqual(3, len(result.splitlines()))

    async def test_ambiguous_write_outcomes_never_retry_or_leak_upstream_details(self):
        failures = [httpx.ReadTimeout("PRIVATE_TOKEN"), 408, 500, 503, "invalid-json", "missing-receipt"]
        row = ["draft-1", "now", "title", "body", "open", "u1", "p1", "PRIVATE"]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                calls = []
                def handler(request, failure=failure):
                    calls.append(request.method)
                    if request.method == "GET":
                        return httpx.Response(200, json={"values": [ISSUE_HEADER]})
                    if isinstance(failure, Exception):
                        raise failure
                    if isinstance(failure, int):
                        return httpx.Response(failure, json={"error": "PRIVATE_TOKEN"})
                    if failure == "invalid-json":
                        return httpx.Response(200, text="PRIVATE_TOKEN")
                    return httpx.Response(200, json={"result": "PRIVATE_TOKEN"})
                client = self.client(handler)
                with self.assertRaises(SheetsUncertain) as caught:
                    await client.append_issue(row)
                self.assertNotIn("PRIVATE_TOKEN", str(caught.exception))
                self.assertEqual(["GET", "POST"], calls)


if __name__ == "__main__":
    unittest.main()
