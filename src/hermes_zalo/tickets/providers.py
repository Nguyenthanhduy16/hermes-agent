"""Bounded Google Sheets test provider; never infer writable ranges from chat.

The caller owns durable ticket state, authorization, locking and read-back.
Google's atomic batch write is not compare-and-swap against manual edits.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from threading import RLock

import httpx

from ..sheets import GoogleTokenProvider, SheetsSettings


ASSIGNMENT_HEADER = [
    "assignment_id", "member_id", "member_name", "start", "end", "course", "room", "period",
]
ASSIGNMENT_KEYS = ["id", *ASSIGNMENT_HEADER[1:]]
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
_SPREADSHEET_ID = re.compile(r"[A-Za-z0-9_-]{10,160}")
_TAB = re.compile(r"[\w -]{1,80}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})")


class TicketsSourceError(Exception):
    """Only fixed, public-safe messages leave this provider."""


class TicketsWriteUncertain(TicketsSourceError):
    """A write may have reached Google; reconcile before any new write."""


class TicketsAccessDenied(TicketsSourceError):
    """A local permission changed before the external write began."""


_ticket_access_guard = ContextVar('tickets_operation_access_guard', default=None)


@contextmanager
def ticket_access_scope(guard):
    """Bind a trusted runtime permission snapshot to this operation only."""
    if guard is not None and not callable(guard):
        raise TicketsAccessDenied('Quyền đổi ca đã thay đổi; hãy xem lại yêu cầu trước khi xác nhận.')
    token = _ticket_access_guard.set(guard)
    try:
        yield
    finally:
        _ticket_access_guard.reset(token)


def require_ticket_access():
    guard = _ticket_access_guard.get()
    if guard is None:
        return  # Legacy runtimes retain their existing explicit authorization.
    try:
        allowed = guard() is True
    except Exception:
        allowed = False
    if not allowed:
        raise TicketsAccessDenied('Quyền đổi ca đã thay đổi; hãy xem lại yêu cầu trước khi xác nhận.')


def _flag(values, name):
    value = str(values.get(name, "false")).strip().lower()
    if value not in ("true", "false"):
        raise TicketsSourceError("Cấu hình bật/tắt Tickets cần là true hoặc false.")
    return value == "true"


@dataclass(frozen=True)
class TicketsSheetsSettings:
    spreadsheet_id: str
    credentials_file: Path = field(repr=False)
    tab: str = "TicketsTest"
    write_enabled: bool = False
    exclusive_writer: bool = False
    production_spreadsheet_id: str = ""

    def __post_init__(self):
        if (not _SPREADSHEET_ID.fullmatch(self.spreadsheet_id)
                or not _SPREADSHEET_ID.fullmatch(self.production_spreadsheet_id)
                or self.spreadsheet_id == self.production_spreadsheet_id):
            raise TicketsSourceError("Tickets cần ID bảng thử riêng, khác ZALO_SHEETS_ID của lịch hiện tại.")
        if not _TAB.fullmatch(self.tab):
            raise TicketsSourceError("Tên tab thử chỉ gồm chữ, số, khoảng trắng, - hoặc _, tối đa 80 ký tự.")
        if self.write_enabled and not self.exclusive_writer:
            raise TicketsSourceError("Chỉ bật ghi khi đã xác nhận riêng dịch vụ Tickets được sửa bảng thử.")

    @classmethod
    def from_mapping(cls, values):
        sid = str(values.get("TICKETS_TEST_SHEETS_ID", "")).strip()
        enabled = _flag(values, "TICKETS_TEST_WRITE_ENABLED")
        exclusive = _flag(values, "TICKETS_TEST_EXCLUSIVE_WRITER")
        if not sid:
            if enabled:
                raise TicketsSourceError("Chưa đặt TICKETS_TEST_SHEETS_ID cho chế độ ghi thử.")
            return None
        key = str(values.get("TICKETS_TEST_GOOGLE_CREDENTIALS", "")).strip()
        tab = str(values.get("TICKETS_TEST_SHEETS_TAB", "TicketsTest")).strip() or "TicketsTest"
        if not key or not tab:
            raise TicketsSourceError("Cần cấu hình riêng tên tab thử và đường dẫn khóa Google cho Tickets.")
        return cls(sid, Path(key).resolve(), tab, enabled, exclusive,
                   str(values.get("ZALO_SHEETS_ID", "")).strip())


class GoogleSheetsAssignments:
    """Synchronous provider for one explicitly configured normalized test tab.

    ``swap`` compares the eight fields of both approved snapshots after a fresh
    read, resolving their current rows by stable ID. It writes only B:C. Keep a
    single service process and prohibit other writers for this test mode; Google
    Sheets offers no conditional transaction on the values read here.
    """

    def __init__(self, settings, *, client=None, token_provider=None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self._lock = RLock()
        if token_provider is None:
            auth = GoogleTokenProvider(SheetsSettings(
                settings.spreadsheet_id, settings.credentials_file,
                issues_enabled=settings.write_enabled))
            token_provider = auth._refresh
        self._token_provider = token_provider

    def close(self):
        self.client.close()

    @property
    def source_key(self):
        return f"sheets:{self.settings.spreadsheet_id}:{self.settings.tab}"

    @property
    def _url(self):
        return "https://sheets.googleapis.com/v4/spreadsheets/" + self.settings.spreadsheet_id

    def _request(self, method, **kwargs):
        try:
            token = self._token_provider()
            if not isinstance(token, str) or not token or "\n" in token or "\r" in token:
                raise ValueError()
        except Exception:
            raise TicketsSourceError("Chưa xác thực được bảng thử; kiểm tra khóa Google và quyền chia sẻ trên máy.") from None
        if method == "POST":
            # Credential refresh can block; recheck the captured local grant
            # after it returns and immediately before any external mutation.
            require_ticket_access()
        try:
            response = self.client.request(
                method, self._url + (":batchUpdate" if method == "POST" else ""),
                headers={"Authorization": "Bearer " + token}, follow_redirects=False, **kwargs)
            if method == "POST" and (response.status_code >= 500 or response.status_code == 408
                    or 200 < response.status_code < 300):
                raise TicketsWriteUncertain("Chưa rõ bảng thử đã được cập nhật; cần đối soát, không gửi ghi lại.")
            if response.status_code != 200:
                raise TicketsSourceError("Google từ chối yêu cầu; kiểm tra quyền bảng thử, cấu hình và hạn mức.")
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError()
            if method == "POST" and (payload.get("spreadsheetId") != self.settings.spreadsheet_id
                    or not isinstance(payload.get("replies"), list) or len(payload["replies"]) != 2
                    or any(not isinstance(reply, dict) for reply in payload["replies"])):
                raise ValueError()
            return payload
        except TicketsSourceError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            if method == "POST":
                raise TicketsWriteUncertain("Chưa rõ bảng thử đã được cập nhật; cần đối soát, không gửi ghi lại.") from None
            raise TicketsSourceError("Chưa đọc được bảng thử; hãy thử lại sau.") from None

    def _read(self):
        payload = self._request("GET", params={
            "ranges": f"'{self.settings.tab}'!A1:H1002",
            "fields": "spreadsheetId,sheets(properties(sheetId,title,gridProperties(rowCount)),merges,"
                      "data(startRow,startColumn,rowData(values(userEnteredValue,effectiveValue))))",
        })
        try:
            if payload["spreadsheetId"] != self.settings.spreadsheet_id:
                raise ValueError()
            sheets = payload["sheets"]
            if not isinstance(sheets, list) or len(sheets) != 1:
                raise ValueError()
            sheet = sheets[0]
            props = sheet["properties"]
            sheet_id = props["sheetId"]
            row_count = props["gridProperties"]["rowCount"]
            if (props["title"] != self.settings.tab or type(sheet_id) is not int or sheet_id < 0
                    or type(row_count) is not int or not 1 <= row_count <= 1001):
                raise ValueError()
            # Reject any merged region touching A:H, including blank cells.
            for merge in sheet.get("merges", []):
                if merge.get("startColumnIndex", 0) < 8 and merge.get("endColumnIndex", 8) > 0:
                    raise ValueError()
            grid = sheet["data"]
            if (not isinstance(grid, list) or len(grid) != 1
                    or grid[0].get("startRow", 0) != 0 or grid[0].get("startColumn", 0) != 0):
                raise ValueError()
            row_data = grid[0]["rowData"]
            if not isinstance(row_data, list) or not 1 <= len(row_data) <= row_count:
                raise ValueError()
            parsed = [self._cells(row) for row in row_data]
            if parsed[0] != ASSIGNMENT_HEADER:
                raise ValueError()
            assignments, row_indices, names = {}, {}, {}
            for row_index, cells in enumerate(parsed[1:], start=1):
                if not any(cells):
                    continue
                item = self._assignment(cells)
                if item["id"] in assignments:
                    raise ValueError()
                if names.setdefault(item["member_id"], item["member_name"]) != item["member_name"]:
                    raise ValueError()
                assignments[item["id"]] = item
                row_indices[item["id"]] = row_index
            return sheet_id, assignments, row_indices
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            raise TicketsSourceError(
                "Bảng thử sai mẫu: kiểm tra tiêu đề, mã duy nhất, ngày giờ có múi giờ, ô gộp/công thức và giới hạn 1.000 ca.") from None

    @staticmethod
    def _cells(row):
        cells = row.get("values", [])
        if not isinstance(cells, list) or len(cells) > 8:
            raise ValueError()
        result = []
        for cell in cells:
            entered, effective = cell.get("userEnteredValue", {}), cell.get("effectiveValue", {})
            # No formula output, numeric date serials, booleans or hidden errors.
            if (set(entered) - {"stringValue"} or set(effective) - {"stringValue"}
                    or entered.get("stringValue", "") != effective.get("stringValue", "")):
                raise ValueError()
            value = entered.get("stringValue", "")
            if not isinstance(value, str):
                raise ValueError()
            result.append(value)
        return result + [""] * (8 - len(result))

    @staticmethod
    def _assignment(cells):
        limits = [80, 80, 160, 40, 40, 180, 120, 80]
        if any(not value or value != value.strip() or len(value) > limit
               or any(ord(char) < 32 or ord(char) == 127 for char in value)
               for value, limit in zip(cells, limits)):
            raise ValueError()
        if not _ID.fullmatch(cells[0]) or not _ID.fullmatch(cells[1]):
            raise ValueError()
        if not _DATE.fullmatch(cells[3]) or not _DATE.fullmatch(cells[4]):
            raise ValueError()
        start, end = map(datetime.fromisoformat, cells[3:5])
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError()
        return dict(zip(ASSIGNMENT_KEYS, cells))

    def list_assignments(self):
        with self._lock:
            return list(self._read()[1].values())

    def swap(self, a, b, ticket_id):
        require_ticket_access()
        if not self.settings.write_enabled or not self.settings.exclusive_writer:
            raise TicketsSourceError("Ghi đổi ca trên bảng thử đang tắt.")
        if not isinstance(ticket_id, str) or not _ID.fullmatch(ticket_id):
            raise TicketsSourceError("Mã yêu cầu đổi ca không hợp lệ.")
        with self._lock:
            sheet_id, current, rows = self._read()
            try:
                if (a["id"] == b["id"] or a["member_id"] == b["member_id"]
                        or any(current[item["id"]] != {key: item[key] for key in ASSIGNMENT_KEYS}
                               for item in (a, b))):
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise TicketsSourceError("Ca đã thay đổi hoặc không hợp lệ; cần xem lại và xác nhận yêu cầu mới.") from None
            # Recheck other shifts from this same fresh read, not an earlier UI
            # snapshot. A new third shift can conflict even if A and B did not move.
            for destination, owner in ((a, b), (b, a)):
                start = datetime.fromisoformat(destination["start"])
                end = datetime.fromisoformat(destination["end"])
                for other in current.values():
                    if (other["id"] not in (a["id"], b["id"])
                            and other["member_id"] == owner["member_id"]
                            and datetime.fromisoformat(other["start"]) < end
                            and datetime.fromisoformat(other["end"]) > start):
                        raise TicketsSourceError("Lịch mới bị trùng với ca khác; cần xem lại trước khi đổi.")
            requests = []
            for destination, owner in ((a, b), (b, a)):
                row = rows[destination["id"]]
                requests.append({"updateCells": {
                    "range": {"sheetId": sheet_id, "startRowIndex": row, "endRowIndex": row + 1,
                              "startColumnIndex": 1, "endColumnIndex": 3},
                    "rows": [{"values": [{"userEnteredValue": {"stringValue": owner[key]}}
                                          for key in ("member_id", "member_name")]}],
                    "fields": "userEnteredValue",
                }})
            # One request, no retries. A response still requires caller read-back.
            self._request("POST", json={"requests": requests, "includeSpreadsheetInResponse": False})
