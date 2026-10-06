"""Small, explicit Google Sheets API boundary; no arbitrary ranges from chat."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, time as daytime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import quote

import httpx

VIETNAM_TZ = timezone(timedelta(hours=7))
CALENDAR_HEADER = ["id", "start", "end", "title", "users", "groups", "location"]
ISSUE_HEADER = ["id", "created_at", "title", "description", "status", "user_id", "chat_id", "chat_type"]


class SheetsError(Exception):
    """Only fixed messages cross this boundary; never a credential/HTTP body."""


class SheetsUncertain(SheetsError):
    pass


@dataclass(frozen=True)
class SheetsSettings:
    spreadsheet_id: str
    credentials_file: Path = field(repr=False)
    calendar_tab: str = "Lich"
    issues_tab: str = "Issues"
    users: frozenset[str] = frozenset()
    groups: frozenset[str] = frozenset()
    calendar_format: str = "standard"
    calendar_tabs: tuple[str, ...] = ()
    issues_enabled: bool = True
    group_members: bool = False

    @classmethod
    def from_mapping(cls, values):
        sid = values.get("ZALO_SHEETS_ID", "").strip()
        if not sid:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,160}", sid):
            raise SheetsError("ZALO_SHEETS_ID cần là ID bảng tính, không phải URL.")
        key = values.get("ZALO_GOOGLE_CREDENTIALS", "").strip()
        if not key:
            raise SheetsError("Chưa đặt đường dẫn ZALO_GOOGLE_CREDENTIALS.")
        tabs = [values.get("ZALO_CALENDAR_TAB") or "Lich", values.get("ZALO_ISSUES_TAB") or "Issues"]
        if any(not re.fullmatch(r"[\w -]{1,80}", tab) for tab in tabs) or tabs[0] == tabs[1]:
            raise SheetsError("Tên hai tab cần khác nhau, chỉ gồm chữ, số, khoảng trắng, - hoặc _.")
        acl = []
        for name in ("ZALO_SHEETS_USERS", "ZALO_SHEETS_GROUPS"):
            ids = frozenset(x.strip() for x in values.get(name, "").split(",") if x.strip())
            if "*" in ids:
                raise SheetsError("Quyền Sheets không chấp nhận dấu *.")
            acl.append(ids)
        layout = values.get("ZALO_CALENDAR_FORMAT") or "standard"
        if layout not in ("standard", "lab"):
            raise SheetsError("ZALO_CALENDAR_FORMAT cần là standard hoặc lab.")
        try:
            lab_tabs = json.loads(values.get("ZALO_CALENDAR_TABS_JSON") or "[]")
        except (ValueError, TypeError):
            raise SheetsError("ZALO_CALENDAR_TABS_JSON cần là danh sách JSON tên tab.") from None
        if (not isinstance(lab_tabs, list) or len(lab_tabs) > 4 or
                any(not isinstance(tab, str) or not re.fullmatch(r"[\w -]{1,80}", tab) for tab in lab_tabs) or
                len(set(lab_tabs)) != len(lab_tabs) or (layout == "lab" and not lab_tabs)):
            raise SheetsError("Chế độ lab cần 1–4 tên tab khác nhau trong ZALO_CALENDAR_TABS_JSON.")
        enabled = values.get("ZALO_ISSUES_ENABLED") or ("false" if layout == "lab" else "true")
        if enabled.lower() not in ("true", "false"):
            raise SheetsError("ZALO_ISSUES_ENABLED cần là true hoặc false.")
        if enabled.lower() == "true" and tabs[1] in lab_tabs:
            raise SheetsError("Tab issue phải khác các tab lịch.")
        return cls(sid, Path(key).resolve(), *tabs, *acl, layout, tuple(lab_tabs), enabled.lower() == "true",
                   values.get("ZALO_GROUP_ACCESS") == "members")

    def allows(self, user_id, chat_id, chat_type):
        return user_id in self.users and (chat_type == "PRIVATE" or
               (chat_type == "GROUP" and chat_id in self.groups))

    def allows_calendar(self, user_id, chat_id, chat_type):
        return self.allows(user_id, chat_id, chat_type) or (
            self.group_members and chat_type == "GROUP" and chat_id in self.groups)


class GoogleTokenProvider:
    def __init__(self, settings):
        self.settings = settings
        self.credentials = None
        self.lock = asyncio.Lock()

    def _refresh(self):
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
            if self.credentials is None:
                path = self.settings.credentials_file
                if path.stat().st_size > 32768:
                    raise ValueError()
                info = json.loads(path.read_text(encoding="utf-8-sig"))
                if info.get("type") != "service_account" or info.get("token_uri") != "https://oauth2.googleapis.com/token":
                    raise ValueError()
                scope = "https://www.googleapis.com/auth/spreadsheets"
                if not self.settings.issues_enabled:
                    scope += ".readonly"
                self.credentials = service_account.Credentials.from_service_account_info(info, scopes=[scope])
            if not self.credentials.valid:
                request = Request()
                def bounded_request(url, method="GET", body=None, headers=None, **kwargs):
                    if url != "https://oauth2.googleapis.com/token":
                        raise ValueError()
                    return request(url=url, method=method, body=body, headers=headers, timeout=20, allow_redirects=False)
                self.credentials.refresh(bounded_request)
            return self.credentials.token
        except Exception:
            raise SheetsError("Chưa xác thực được Google Sheets; kiểm tra file khóa và quyền chia sẻ trên máy.") from None

    async def __call__(self):
        async with self.lock:
            return await asyncio.to_thread(self._refresh)


class SheetsClient:
    def __init__(self, settings, *, client=None, token_provider=None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        self.token_provider = token_provider or GoogleTokenProvider(settings)

    async def close(self):
        await self.client.aclose()

    def _url(self, tab, span):
        cell_range = quote(f"'{tab}'!{span}", safe="")
        return f"https://sheets.googleapis.com/v4/spreadsheets/{self.settings.spreadsheet_id}/values/{cell_range}"

    async def _request(self, method, url, **kwargs):
        token = await self.token_provider()
        try:
            response = await self.client.request(method, url, headers={"Authorization": f"Bearer {token}"}, follow_redirects=False, **kwargs)
            if (response.status_code >= 500 or response.status_code == 408) and method == "POST":
                raise SheetsUncertain("Chưa rõ Google đã ghi issue hay chưa. Không tự ghi lại.")
            if response.status_code != 200:
                raise SheetsError("Google Sheets từ chối yêu cầu; kiểm tra quyền, cấu hình tab hoặc hạn mức.")
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except SheetsError:
            raise
        except (httpx.HTTPError, ValueError):
            if method == "POST":
                raise SheetsUncertain("Chưa rõ Google đã ghi issue hay chưa. Không tự ghi lại.") from None
            raise SheetsError("Chưa đọc được Google Sheets; hãy thử lại sau.") from None

    async def _rows(self, tab, header):
        data = await self._request("GET", self._url(tab, f"A1:{chr(64 + len(header))}1002"),
                                   params={"valueRenderOption": "UNFORMATTED_VALUE"})
        rows = data.get("values", [])
        if not isinstance(rows, list) or not rows or rows[0] != header:
            raise SheetsError("Tab Google Sheets chưa đúng dòng tiêu đề của mẫu.")
        if len(rows) > 1001:
            raise SheetsError("Bảng thử nghiệm vượt 1.000 dòng; cần thu gọn hoặc nâng cấp bộ đọc.")
        if any(not isinstance(row, list) or len(row) > len(header) for row in rows):
            raise SheetsError("Dữ liệu Google Sheets không đúng mẫu.")
        return [row + [""] * (len(header) - len(row)) for row in rows[1:] if any(row)]

    async def calendar(self, day, user_id, chat_id, chat_type):
        if not self.settings.allows_calendar(user_id, chat_id, chat_type):
            return "Bạn hoặc cuộc trò chuyện này chưa được cấp quyền tra lịch Sheets."
        if self.settings.calendar_format == "lab":
            from .lab_calendar import format_calendar
            schedules = await self._lab_schedules()
            return format_calendar(schedules, day, self.settings.spreadsheet_id)
        rows = await self._rows(self.settings.calendar_tab, CALENDAR_HEADER)
        start = datetime.combine(day, daytime.min, VIETNAM_TZ)
        end = start + timedelta(days=1)
        found = []
        for row in rows:
            users = {x.strip() for x in str(row[4]).split(",") if x.strip()}
            groups = {x.strip() for x in str(row[5]).split(",") if x.strip()}
            # DM: explicit user. Group: explicit group, optionally narrower user list.
            if chat_type == "PRIVATE":
                allowed = user_id in users
            else:
                allowed = chat_id in groups and (not users or user_id in users)
            if not allowed:
                continue
            try:
                a, b = [datetime.fromisoformat(str(x)) for x in row[1:3]]
                if a.tzinfo is None or b.tzinfo is None or b <= a:
                    raise ValueError()
            except (ValueError, TypeError):
                raise SheetsError("Có dòng lịch được cấp quyền sai ngày giờ; dùng định dạng ISO có +07:00.") from None
            if a < end and b > start:
                title, place = [" ".join(str(cell).split())[:limit] for cell, limit in ((row[3], 180), (row[6], 120))]
                found.append((a, f"{a.astimezone(VIETNAM_TZ):%d/%m %H:%M}–{b.astimezone(VIETNAM_TZ):%d/%m %H:%M}: {title}" +
                              (f" — {place}" if place else "")))
        if not found:
            return f"Không có lịch được cấp quyền hiển thị ngày {day:%d/%m/%Y}."
        lines = [text for _, text in sorted(found)[:20]]
        suffix = "\nChỉ hiển thị 20 lịch đầu." if len(found) > 20 else ""
        return f"Lịch {day:%d/%m/%Y} (giờ Việt Nam):\n" + "\n".join(lines) + suffix + f"\nNguồn: Google Sheets, tab {self.settings.calendar_tab}."

    async def _lab_schedules(self):
        from .lab_calendar import parse_sheet
        # Explicit allowlist; no discovery or access to unrelated workbook tabs.
        params = [("ranges", f"'{tab}'!A1:J1001") for tab in self.settings.calendar_tabs]
        params.append(("fields", "sheets(properties(sheetId,title,gridProperties(rowCount)),merges,"
                       "data(startRow,startColumn,rowData(values(formattedValue))))"))
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.settings.spreadsheet_id}"
        data = await self._request("GET", url, params=params)
        try:
            sheets = data["sheets"]
            by_title = {sheet["properties"]["title"]: sheet for sheet in sheets}
            if len(by_title) != len(sheets) or set(by_title) != set(self.settings.calendar_tabs):
                raise ValueError()
            return [parse_sheet(by_title[tab]) for tab in self.settings.calendar_tabs]
        except (KeyError, TypeError, ValueError, IndexError):
            raise SheetsError("Tab Lịch lab sai cấu trúc hoặc vượt 1.000 dòng; kiểm tra tiêu đề, ngày và ô gộp.") from None

    async def lab_calendar(self, query, user_id, chat_id, chat_type):
        if not self.settings.allows_calendar(user_id, chat_id, chat_type):
            return "Bạn hoặc cuộc trò chuyện này chưa được cấp quyền tra lịch Sheets."
        from .lab_calendar import format_query
        return format_query(await self._lab_schedules(), query, self.settings.spreadsheet_id)

    async def coach_name(self, name, user_id, chat_id, chat_type):
        if not self.settings.allows_calendar(user_id, chat_id, chat_type):
            raise SheetsError("Bạn/cuộc trò chuyện này chưa được cấp quyền tra lịch.")
        if self.settings.calendar_format != "lab":
            raise SheetsError("Đăng ký tên Coach hiện dành cho bảng Lịch lab.")
        from .commands import normalized
        names = set()
        for schedule in await self._lab_schedules():
            for event in schedule.events:
                names.update([event.key_coach] + [person.split(" [", 1)[0] for person in event.coaches])
        matches = {item.casefold(): item for item in sorted(names) if normalized(item) == normalized(name)}
        if len(matches) != 1:
            raise SheetsError("Chưa tìm thấy một tên Coach duy nhất khớp tên bạn nhập. Hãy dùng đầy đủ họ tên đúng trong bảng.")
        return next(iter(matches.values()))

    async def check(self):
        if self.settings.calendar_format == "lab":
            schedules = await self._lab_schedules()
            count = sum(len(schedule.events) for schedule in schedules)
        else:
            count = len(await self._rows(self.settings.calendar_tab, CALENDAR_HEADER))
        issues = await self._rows(self.settings.issues_tab, ISSUE_HEADER) if self.settings.issues_enabled else None
        return {"calendar_rows": count, "issue_rows": len(issues) if issues is not None else None}

    async def append_issue(self, row):
        if not self.settings.issues_enabled:
            raise SheetsError("Tạo issue đang tắt; bảng này hiện chỉ dùng để đọc lịch.")
        existing = await self._rows(self.settings.issues_tab, ISSUE_HEADER)
        for item in existing:
            if str(item[0]) == row[0]:
                if item[2:] != row[2:]:
                    raise SheetsUncertain("Có issue cùng ID nhưng nội dung khác; cần kiểm tra trên Sheets, không tự ghi lại.")
                return  # A confirmed draft ID already present must not be appended again.
        if len(existing) >= 1000:
            raise SheetsError("Tab Issues đã đạt giới hạn 1.000 dòng thử nghiệm.")
        result = await self._request("POST", self._url(self.settings.issues_tab, "A:H") + ":append",
                                     params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                                     json={"majorDimension": "ROWS", "values": [row]})
        updates = result.get("updates")
        if not isinstance(updates, dict) or updates.get("updatedRows") != 1:
            raise SheetsUncertain("Chưa xác nhận được dòng issue đã ghi; kiểm tra Sheets trước khi thử lại.")
