"""Read a bounded, explicitly configured Workload identity directory.

This reader never writes Sheets or grants permissions. A matching phone proves
only that a roster row exists; enrollment must separately verify its owner.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
import unicodedata


MAX_RECORDS = 1000
CONFIG_ERROR = 'Chưa cấu hình đúng tab, cột và tiêu đề của danh sách Workload.'
READ_ERROR = 'Chưa đọc được danh sách Workload; hãy thử lại sau.'
SCHEMA_ERROR = 'Danh sách Workload chưa đúng cấu trúc hoặc vượt giới hạn 1.000 dòng dữ liệu.'
PHONE_ERROR = 'Nhập số di động Việt Nam gồm 10 số hoặc dạng +84 tương ứng.'
LOOKUP_ERROR = 'Chưa đối chiếu được một người duy nhất với số điện thoại trong Workload; nhờ quản trị kiểm tra.'
NOT_FOUND_ERROR = 'Không tìm thấy số điện thoại này trong Workload. Kiểm tra lại số hoặc nhờ quản trị cập nhật danh sách.'
DUPLICATE_PHONE_ERROR = 'Số điện thoại bị trùng trong Workload. Nhờ quản trị kiểm tra và giữ đúng một người trước khi đăng ký.'
NAME_ERROR = 'Tên trong Workload chưa xác định được duy nhất một người có số điện thoại hợp lệ.'
# Explicit copy/paste formatting only; do not strip arbitrary Unicode controls
# or infer digits that Sheets has dropped. Apply to both input and roster rows.
PHONE_COPY_FORMAT = str.maketrans({'\u200b': '', '\ufeff': '', '\u00a0': ' ', '\u202f': ' '})


class WorkloadError(Exception):
    """Fixed, non-personal error text suitable for an enrollment response."""


def normalize_phone(value):
    """Accept complete VN mobile numbers; never infer a missing leading zero."""
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        raise WorkloadError(PHONE_ERROR)
    value = value.translate(PHONE_COPY_FORMAT)
    if not re.fullmatch(r'[+0-9 .-]+', value):
        raise WorkloadError(PHONE_ERROR)
    phone = value.replace(' ', '').replace('.', '').replace('-', '')
    if re.fullmatch(r'\+84[35789][0-9]{8}', phone):
        return '0' + phone[3:]
    if re.fullmatch(r'0[35789][0-9]{8}', phone):
        return phone
    raise WorkloadError(PHONE_ERROR)


def _column(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z]{1,2}', value):
        raise ValueError()
    number = 0
    for character in value:
        number = number * 26 + ord(character) - ord('A') + 1
    return number - 1


def _visible(value, limit):
    return (isinstance(value, str) and 1 <= len(value) <= limit and value == value.strip()
            and value == unicodedata.normalize('NFC', value)
            and not any(unicodedata.category(character).startswith('C') for character in value))


def _name_key(name):
    folded = unicodedata.normalize('NFD', name.casefold().replace('đ', 'd'))
    return ' '.join(''.join(c for c in folded if not unicodedata.combining(c)).split())


@dataclass(frozen=True)
class WorkloadSettings:
    spreadsheet_id: str
    tab: str
    name_column: str
    phone_column: str
    header_row: int
    name_header: str
    phone_header: str

    def __post_init__(self):
        try:
            if (not isinstance(self.spreadsheet_id, str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{10,160}', self.spreadsheet_id)
                    or not _visible(self.tab, 100) or any(c in self.tab for c in '[]:*?/\\')
                    or _column(self.name_column) == _column(self.phone_column)
                    or type(self.header_row) is not int or not 1 <= self.header_row <= 1000
                    or not _visible(self.name_header, 160) or not _visible(self.phone_header, 160)
                    or self.name_header == self.phone_header):
                raise ValueError()
        except (ValueError, TypeError):
            raise WorkloadError(CONFIG_ERROR) from None

    @property
    def source_key(self):
        return 'workload:' + json.dumps([self.spreadsheet_id, self.tab], ensure_ascii=False, separators=(',', ':'))

    @classmethod
    def from_mapping(cls, values, sheets_settings):
        enabled = str(values.get('REGISTRATION_ENABLED') or 'false').strip().lower()
        if enabled == 'false':
            return None
        if enabled != 'true' or sheets_settings is None:
            raise WorkloadError(CONFIG_ERROR)
        try:
            fields = {name: values['REGISTRATION_WORKLOAD_' + name].strip()
                      for name in ('TAB', 'NAME_COLUMN', 'PHONE_COLUMN', 'HEADER_ROW', 'NAME_HEADER', 'PHONE_HEADER')}
            if not re.fullmatch(r'[0-9]{1,4}', fields['HEADER_ROW']):
                raise ValueError()
            return cls(sheets_settings.spreadsheet_id, fields['TAB'], fields['NAME_COLUMN'].upper(),
                       fields['PHONE_COLUMN'].upper(), int(fields['HEADER_ROW']), fields['NAME_HEADER'], fields['PHONE_HEADER'])
        except (KeyError, TypeError, ValueError, AttributeError):
            raise WorkloadError(CONFIG_ERROR) from None


@dataclass(frozen=True)
class WorkloadPerson:
    name: str
    phone: str = field(repr=False)
    source_key: str
    fingerprint: str
    name_unique: bool


class WorkloadDirectory:
    """Use a caller-owned SheetsClient and its authentication boundary, GET only.

    No directory or credentials are cached or persisted. Grid allocation is
    bounded too: an oversized source is refused rather than skipping rows that
    might contain another occurrence of the requested phone or name.
    """
    def __init__(self, settings, sheets_client):
        if (not isinstance(settings, WorkloadSettings)
                or getattr(getattr(sheets_client, 'settings', None), 'spreadsheet_id', None) != settings.spreadsheet_id):
            raise WorkloadError(CONFIG_ERROR)
        self.settings, self.client = settings, sheets_client

    @property
    def source_key(self):
        return self.settings.source_key

    @staticmethod
    def _text(row):
        if not isinstance(row, dict):
            raise ValueError()
        values = row.get('values', [])
        if not isinstance(values, list) or len(values) > 1:
            raise ValueError()
        cell = values[0] if values else {}
        if not isinstance(cell, dict):
            raise ValueError()
        entered, effective = cell.get('userEnteredValue', {}), cell.get('effectiveValue', {})
        if (not isinstance(entered, dict) or not isinstance(effective, dict)
                or 'formulaValue' in entered or 'errorValue' in effective):
            raise ValueError()
        value = cell.get('formattedValue', '')
        if not isinstance(value, str):
            raise ValueError()
        # A custom number format must not conceal a name/phone in an area
        # accepted as blank (for example, a merged header continuation).
        if value == '' and any(key in raw and raw[key] != ''
                               for raw in (entered, effective)
                               for key in ('stringValue', 'numberValue', 'boolValue')):
            raise ValueError()
        return value

    @staticmethod
    def _data_start(parsed, indices, merges, header_start, row_count, column_count):
        """Allow literal column headers and blank non-anchor footer cells only."""
        if not isinstance(merges, list):
            raise ValueError()
        relevant = []
        header_end = header_start + 1
        for merge in merges:
            start_row, end_row = merge.get('startRowIndex', 0), merge['endRowIndex']
            start_col, end_col = merge.get('startColumnIndex', 0), merge['endColumnIndex']
            if (any(type(value) is not int for value in (start_row, end_row, start_col, end_col))
                    or not 0 <= start_row < end_row <= row_count or not 0 <= start_col < end_col <= column_count):
                raise ValueError()
            if end_row <= header_start or not any(start_col <= index < end_col for index in indices):
                continue
            rectangle = (start_row, end_row, start_col, end_col)
            if any(start_row < old_end_row and old_start_row < end_row
                   and start_col < old_end_col and old_start_col < end_col
                   for old_start_row, old_end_row, old_start_col, old_end_col in relevant):
                raise ValueError()
            relevant.append(rectangle)
            if start_row == header_start and start_col in indices and end_col == start_col + 1:
                header_end = max(header_end, end_row)

        def blank(start, end):
            return all(not parsed[index][row - header_start]
                       for index in indices
                       for row in range(start, min(end, header_start + len(parsed[index]))))

        # Different header heights are safe only if neither configured column
        # contains data anywhere in the combined continuation area.
        if not blank(header_start + 1, header_end):
            raise ValueError()
        for start_row, end_row, start_col, end_col in relevant:
            if start_row == header_start and start_col in indices and end_col == start_col + 1:
                continue
            # A wide footer may carry text in A/B, outside the two selected
            # columns. Never infer a person or phone from that merged anchor.
            if (start_row < header_end or start_col in indices
                    or not all(start_col <= index < end_col for index in indices)
                    or not blank(start_row, end_row)):
                raise ValueError()
        return header_end - header_start

    async def _read(self):
        cfg = self.settings
        # Escape a literal apostrophe inside a quoted sheet title. No range or
        # sheet selection is accepted from the registering user.
        title = cfg.tab.replace("'", "''")
        columns = (cfg.name_column, cfg.phone_column)
        ranges = [f"'{title}'!{column}{cfg.header_row}:{column}{cfg.header_row + MAX_RECORDS}" for column in columns]
        try:
            data = await self.client._request('GET',
                'https://sheets.googleapis.com/v4/spreadsheets/' + cfg.spreadsheet_id,
                params={'ranges': ranges,
                        'fields': 'spreadsheetId,sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)),'
                                  'merges,data(startRow,startColumn,rowData(values(formattedValue,userEnteredValue,effectiveValue))))'})
        except Exception:
            raise WorkloadError(READ_ERROR) from None
        try:
            if not isinstance(data, dict) or data.get('spreadsheetId') != cfg.spreadsheet_id:
                raise ValueError()
            sheets = data['sheets']
            if not isinstance(sheets, list) or len(sheets) != 1:
                raise ValueError()
            sheet = sheets[0]
            props = sheet['properties']
            grid = props['gridProperties']
            row_count, column_count = grid['rowCount'], grid['columnCount']
            indices = (_column(cfg.name_column), _column(cfg.phone_column))
            if (props['title'] != cfg.tab or type(props['sheetId']) is not int or props['sheetId'] < 0
                    or type(row_count) is not int or not cfg.header_row <= row_count <= cfg.header_row + MAX_RECORDS
                    or type(column_count) is not int or not max(indices) < column_count <= 18278):
                raise ValueError()
            blocks = sheet['data']
            if not isinstance(blocks, list) or len(blocks) != 2:
                raise ValueError()
            parsed = {}
            for block in blocks:
                column = block.get('startColumn', 0)
                start_row = block.get('startRow', 0)
                rows = block.get('rowData', [])
                if (type(column) is not int or column not in indices or column in parsed
                        or type(start_row) is not int or start_row != cfg.header_row - 1
                        or not isinstance(rows, list) or not 1 <= len(rows) <= row_count - start_row):
                    raise ValueError()
                parsed[column] = [self._text(row) for row in rows]
            names, phones = [parsed[index] for index in indices]
            if names[0] != cfg.name_header or phones[0] != cfg.phone_header:
                raise ValueError()
            data_start = self._data_start(parsed, indices, sheet.get('merges', []),
                                          cfg.header_row - 1, row_count, column_count)
            rows = []
            for index in range(data_start, max(len(names), len(phones))):
                name = names[index] if index < len(names) else ''
                raw_phone = phones[index] if index < len(phones) else ''
                if not name and not raw_phone:
                    continue
                try:
                    phone = normalize_phone(raw_phone)
                except WorkloadError:
                    phone = None
                # Keep invalid-phone and blank-name rows for collision checks.
                rows.append((name, phone))
            return rows
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            raise WorkloadError(SCHEMA_ERROR) from None

    def _person(self, record, rows, *, require_unique_name=False):
        name, phone = record
        if (not _visible(name, 160) or not any(unicodedata.category(c).startswith('L') for c in name)
                or phone is None or sum(other_phone == phone for _, other_phone in rows) != 1):
            raise WorkloadError(NAME_ERROR if require_unique_name else LOOKUP_ERROR)
        name_unique = sum(_name_key(other_name) == _name_key(name) for other_name, _ in rows) == 1
        if require_unique_name and not name_unique:
            raise WorkloadError(NAME_ERROR)
        fingerprint = hashlib.sha256(json.dumps([self.source_key, name, phone], ensure_ascii=False,
                                                separators=(',', ':')).encode('utf-8')).hexdigest()
        return WorkloadPerson(name, phone, self.source_key, fingerprint, name_unique)

    async def lookup(self, phone):
        normalized = normalize_phone(phone)
        rows = await self._read()
        found = [row for row in rows if row[1] == normalized]
        if not found:
            raise WorkloadError(NOT_FOUND_ERROR)
        if len(found) > 1:
            raise WorkloadError(DUPLICATE_PHONE_ERROR)
        return self._person(found[0], rows)

    async def lookup_many(self, phones):
        """One fresh roster snapshot for trusted startup reconciliation.

        Invalid or missing individual roster entries are omitted; a source
        failure still raises. This never grants rights or returns the roster
        to chat, and duplicate names remain marked for the caller to reject.
        """
        requested = {normalize_phone(phone) for phone in phones}
        if not requested:
            return {}
        rows = await self._read()
        indexed = {}
        for row in rows:
            if row[1] in requested:
                indexed.setdefault(row[1], []).append(row)
        result = {}
        for phone, matches in indexed.items():
            if len(matches) != 1:
                continue
            try:
                result[phone] = self._person(matches[0], rows)
            except WorkloadError:
                continue
        return result

    async def resolve_name(self, name):
        """Certify one exact roster spelling without accent-fold collisions."""
        if not isinstance(name, str) or not _visible(name, 160):
            raise WorkloadError(NAME_ERROR)
        rows = await self._read()
        found = [row for row in rows if row[0] == name]
        if len(found) != 1:
            raise WorkloadError(NAME_ERROR)
        return self._person(found[0], rows, require_unique_name=True)
