"""Conservative, opt-in Lab Coach (column D) swaps in an existing roster.

No metadata is provisioned. A global structure fingerprint makes any non-D
edit invalidate assignment IDs instead of guessing where a shifted row went.
Atomic Sheets batches do not replace an exclusive-writer operating agreement.
"""
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from ..lab_calendar import parse_sheet, MAX_ROWS
from .providers import (GoogleSheetsAssignments, TicketsAccessDenied, TicketsSourceError,
                        TicketsWriteUncertain, require_ticket_access)


VN = timezone(timedelta(hours=7))
KEYS = ('id', 'member_id', 'member_name', 'start', 'end', 'course', 'room', 'period')
EXPLICIT_PERIOD = re.compile(r'(Sáng|Chiều|Tối) ((?:[01]\d|2[0-3]):[0-5]\d)\s*[–-]\s*'
                             r'((?:[01]\d|2[0-3]):[0-5]\d)')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _name(value):
    if value in ('', '-'):
        return None
    if (not isinstance(value, str) or len(value) > 160 or value != unicodedata.normalize('NFC', value)
            or value != ' '.join(value.split()) or ' và ' in value.casefold()
            or any(not (unicodedata.category(c)[0] in ('L', 'M') or c in " '-’") for c in value)
            or not any(unicodedata.category(c)[0] == 'L' for c in value)):
        raise ValueError('Ambiguous person cell.')
    return value


def _member(name):
    # Exact NFC spelling only: no accent/case/space folding or partial matches.
    return 'lab-' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:24]


@dataclass(frozen=True)
class LabSheetsSettings:
    spreadsheet_id: str
    credentials_file: Path = field(repr=False)
    tabs: tuple[str, ...]
    periods: dict = field(default_factory=dict)
    write_enabled: bool = False
    exclusive_writer: bool = False
    test_mode: bool = False
    test_members: tuple[str, ...] = ()

    def __post_init__(self):
        try:
            if type(self.test_mode) is not bool:
                raise ValueError()
            if self.test_mode and (not isinstance(self.test_members, tuple) or len(self.test_members) != 2
                                   or len(set(self.test_members)) != 2
                                   or any(_name(name) != name or name in ('', '-') for name in self.test_members)):
                raise ValueError()
            if not self.test_mode and self.test_members:
                raise ValueError()
        except (ValueError, TypeError):
            raise TicketsSourceError('Chế độ thử lịch thật cần đúng hai tên thành viên NFC rõ ràng, khác nhau.') from None

    @classmethod
    def from_mapping(cls, values, *, test_mode=False):
        try:
            sid = str(values.get('ZALO_SHEETS_ID', '')).strip()
            key = str(values.get('ZALO_GOOGLE_CREDENTIALS', '')).strip()
            tabs = json.loads(values.get('ZALO_CALENDAR_TABS_JSON') or '[]')
            periods = {} if test_mode else json.loads(values.get('TICKETS_LAB_PERIODS_JSON') or '{}')
            test_members = json.loads(values.get('TICKETS_LAB_TEST_MEMBERS_JSON') or '[]') if test_mode else []
            if not isinstance(test_members, list):
                raise ValueError()
            prefix = 'TICKETS_LAB_TEST_' if test_mode else 'TICKETS_LAB_'
            flags = [str(values.get(prefix + suffix) or 'false').strip().lower()
                     for suffix in ('WRITE_ENABLED', 'EXCLUSIVE_WRITER')]
            if (values.get('ZALO_CALENDAR_FORMAT') != 'lab' or not re.fullmatch(r'[A-Za-z0-9_-]{10,160}', sid)
                    or not key or not isinstance(tabs, list) or not 1 <= len(tabs) <= 4
                    or any(not isinstance(tab, str) or not re.fullmatch(r'[\w -]{1,80}', tab) for tab in tabs)
                    or len(set(tabs)) != len(tabs) or any(flag not in ('true', 'false') for flag in flags)):
                raise ValueError()
            if (not isinstance(periods, dict)
                    or not set(periods).issubset({'Sáng', 'Chiều', 'Tối'})):
                raise ValueError()
            for times in periods.values():
                if not isinstance(times, dict) or set(times) != {'start', 'end'}:
                    raise ValueError()
                if any(not isinstance(times[k], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', times[k])
                       for k in ('start', 'end')) or times['end'] <= times['start']:
                    raise ValueError()
            if flags[0] == 'true' and flags[1] != 'true':
                raise ValueError()
            return cls(sid, Path(key).resolve(), tuple(tabs), periods, flags[0] == 'true', flags[1] == 'true',
                       test_mode, tuple(test_members))
        except (TypeError, ValueError):
            raise TicketsSourceError('Cấu hình lịch lab chưa hợp lệ: cần bảng/tab lab rõ ràng, khóa Google, '
                                     'giờ buổi dạng start/end HH:MM và xác nhận ghi độc quyền khi bật ghi.') from None


class GoogleLabAssignments(GoogleSheetsAssignments):
    def __init__(self, settings, *, client=None, token_provider=None, clock=None):
        super().__init__(settings, client=client, token_provider=token_provider)
        self._clock = clock or (lambda: datetime.now(VN))

    @property
    def source_key(self):
        return f'lab:{self.settings.spreadsheet_id}:' + _hash(
            {'tabs': list(self.settings.tabs), 'periods': self.settings.periods,
             'test_mode': self.settings.test_mode, 'test_members': self.settings.test_members})

    def _request(self, method, **kwargs):
        try:
            return super()._request(method, **kwargs)
        except TicketsAccessDenied:
            raise
        except TicketsWriteUncertain:
            raise TicketsWriteUncertain('Chưa rõ lịch lab đã được cập nhật; cần đối soát, không gửi ghi lại.') from None
        except TicketsSourceError:
            raise TicketsSourceError('Chưa truy cập được lịch lab; kiểm tra kết nối, quyền Google và cấu hình trên máy.') from None

    def _read_lab(self):
        data = self._request('GET', params=[
            *[('ranges', f"'{tab}'!A1:J1001") for tab in self.settings.tabs],
            ('fields', 'spreadsheetId,sheets(properties(sheetId,title,gridProperties(rowCount)),merges,'
                       'data(startRow,startColumn,rowData(values(formattedValue,userEnteredValue,effectiveValue))))'),
        ])
        try:
            if data.get('spreadsheetId') != self.settings.spreadsheet_id:
                raise ValueError()
            sheets = data['sheets']
            by_title = {sheet['properties']['title']: sheet for sheet in sheets}
            if len(by_title) != len(sheets) or set(by_title) != set(self.settings.tabs):
                raise ValueError()
            if len({sheet['properties']['sheetId'] for sheet in sheets}) != len(sheets):
                raise ValueError()
            structure, records, key_records, names = [], [], [], set()
            for title in self.settings.tabs:
                sheet = by_title[title]
                schedule = parse_sheet(sheet)  # Actual merges + each day's exact D header.
                row_data = sheet['data'][0].get('rowData', [])
                matrix = []
                for row in row_data:
                    raw = row.get('values', [])
                    matrix.append([{'formattedValue': cell.get('formattedValue', ''),
                                    'userEnteredValue': cell.get('userEnteredValue', {}),
                                    'effectiveValue': cell.get('effectiveValue', {})} for cell in raw]
                                  + [{} for _ in range(10 - len(raw))])
                matrix += [[{} for _ in range(10)] for _ in range(MAX_ROWS - len(matrix))]
                merges = sheet.get('merges', [])
                candidates = set()
                for event in schedule.events:
                    if not event.course or not event.session or not event.room or not event.period:
                        # Hours are intentionally not needed just to inspect names.
                        # Incomplete locations/classes cannot be safely selected.
                        incomplete = True
                    else:
                        incomplete = False
                    for row in range(event.first_row - 1, event.last_row):
                        if row in candidates:
                            raise ValueError()
                        candidates.add(row)
                        for merge in merges:
                            if (merge.get('startColumnIndex', 0) <= 3 < merge.get('endColumnIndex', 10)
                                    and merge.get('startRowIndex', 0) <= row < merge['endRowIndex']):
                                raise ValueError('D is merged.')
                        cell = matrix[row][3]
                        entered = cell.get('userEnteredValue', {})
                        effective = cell.get('effectiveValue', {})
                        formatted = cell.get('formattedValue', '')
                        if (set(entered) - {'stringValue'} or set(effective) - {'stringValue'}
                                or entered.get('stringValue', '') != formatted
                                or effective.get('stringValue', '') != formatted):
                            raise ValueError('D is not a literal name.')
                        name = _name(formatted)
                        if name:
                            names.add(name)
                            records.append({'sheet_id': schedule.sheet_id, 'tab': title, 'row': row,
                                            'event': event, 'name': name, 'incomplete': incomplete})
                            matrix[row][3] = {'lab_coach': 'single-name'}
                    # Read Key Coach from its real merged anchor to avoid the
                    # display parser normalizing away multiple-person separators.
                    key_row = event.first_row - 1
                    for merge in merges:
                        if (merge.get('startColumnIndex', 0) == 4 and merge.get('endColumnIndex') == 5
                                and merge.get('startRowIndex', 0) <= key_row < merge['endRowIndex']):
                            key_row = merge.get('startRowIndex', 0)
                            break
                    key_raw = matrix[key_row][4].get('formattedValue', '')
                    if key_raw not in ('', '-'):
                        key_records.append({'raw_name': key_raw, 'event': event})
                # An unnumbered, unmerged name row must not silently disappear
                # from the conflict roster merely because the display parser
                # treated it as a separator/title.
                for row, cells in enumerate(matrix):
                    if row in candidates:
                        continue
                    for column, label in ((3, 'Lab Coach'), (4, 'Key Coach')):
                        text = cells[column].get('formattedValue', '')
                        if text not in ('', '-') and not (
                                cells[0].get('formattedValue') == 'STT' and text == label):
                            raise ValueError('Unmapped coach name row.')
                structure.append({'title': title, 'sheet_id': schedule.sheet_id,
                                  'grid_rows': sheet['properties']['gridProperties']['rowCount'],
                                  'merges': sorted(merges, key=lambda m: json.dumps(m, sort_keys=True)), 'matrix': matrix})
            fingerprint = _hash(structure)[:24]
            return fingerprint, records, key_records, sorted(names)
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise TicketsSourceError('Lịch lab có cấu trúc hoặc ô người phụ trách chưa rõ: kiểm tra tiêu đề từng ngày, '
                                     'ô D không gộp/công thức, mỗi ô một tên NFC rõ ràng và giới hạn 1.000 hàng.') from None

    def inspect_members(self):
        with self._lock:
            return [{'member_id': _member(name), 'member_name': name} for name in self._read_lab()[3]
                    if not self.settings.test_mode or name in self.settings.test_members]

    def _times(self, event):
        match = EXPLICIT_PERIOD.fullmatch(event.period)
        if self.settings.test_mode and not match:
            # Missing hours remain unknown. This internal date interval also
            # closes selection at midnight; the UI must never assert these
            # placeholder values as actual shift times.
            start = datetime.combine(event.day, time.min, VN)
            return start.isoformat(), (start + timedelta(days=1)).isoformat()
        hours = {'start': match[2], 'end': match[3]} if match else self.settings.periods.get(event.period)
        if not hours:
            raise TicketsSourceError('Chưa có giờ bắt đầu/kết thúc đã xác nhận cho tất cả buổi trong lịch lab; '
                                     'cần cấu hình TICKETS_LAB_PERIODS_JSON trước khi chọn hoặc đổi ca.')
        start = datetime.combine(event.day, time.fromisoformat(hours['start']), VN)
        end = datetime.combine(event.day, time.fromisoformat(hours['end']), VN)
        if end <= start:
            raise TicketsSourceError('Giờ kết thúc buổi lab phải sau giờ bắt đầu trong cùng ngày.')
        return start.isoformat(), end.isoformat()

    def _assignments(self):
        fingerprint, records, key_records, _ = self._read_lab()
        # Each current/future Lab and Key Coach event must resolve its own
        # confirmed hours. Explicit hours need no default mapping; a mapping
        # may omit unused periods, but _times still rejects any missing period.
        assignments, positions, key_shifts = {}, {}, []
        today = self._clock().astimezone(VN).date()
        for record in records:
            if self.settings.test_mode and record['name'] not in self.settings.test_members:
                continue
            event = record['event']
            if record['incomplete']:
                if self.settings.test_mode or event.day < today:
                    continue  # Retain full fingerprint; never guess old blanks.
                raise TicketsSourceError('Có ca lab thiếu khóa, session, phòng hoặc buổi; cần làm rõ dữ liệu trước khi đổi ca.')
            try:
                start, end = self._times(event)
            except TicketsSourceError:
                if event.day < today:
                    continue
                raise
            identity = f"LAB-{record['sheet_id']}-{record['row'] + 1}-{fingerprint}"
            course = f"{event.course} · {event.session} · {record['tab']} D{record['row'] + 1}"
            if len(course) > 180:
                raise TicketsSourceError('Nội dung ca quá dài để hiển thị rõ; cần quản trị chuẩn hóa tên khóa/session/tab.')
            name = record['name']
            assignments[identity] = dict(id=identity, member_id=_member(name), member_name=name,
                                         start=start, end=end, course=course, room=event.room, period=event.period)
            positions[identity] = record
        for record in key_records:
            if self.settings.test_mode:
                break  # Explicit two-person real-write test bypasses time conflicts.
            try:
                name = _name(record['raw_name'])
                start, end = self._times(record['event'])
            except (ValueError, TicketsSourceError):
                if record['event'].day < today:
                    continue
                raise TicketsSourceError('Có phân công Key Coach hôm nay/tương lai chưa rõ tên hoặc giờ; '
                                         'cần làm rõ trước khi đổi Lab Coach.') from None
            key_shifts.append(dict(member_id=_member(name), start=start, end=end))
        return assignments, positions, key_shifts

    def list_assignments(self):
        with self._lock:
            return list(self._assignments()[0].values())

    def _check(self, a, b, current, key_shifts):
        try:
            if (a['id'] == b['id'] or a['member_id'] == b['member_id']
                    or any(current[item['id']] != {key: item[key] for key in KEYS} for item in (a, b))):
                raise ValueError()
        except (TypeError, ValueError, KeyError):
            raise TicketsSourceError('Lịch hoặc vị trí dòng đã thay đổi; cần xem lại và xác nhận yêu cầu mới.') from None
        for destination in (a, b):
            if datetime.fromisoformat(destination['start']) <= self._clock():
                raise TicketsSourceError('Ca lab đã bắt đầu, đã qua hoặc hôm nay chưa rõ giờ; không thể đổi.')
        if not self.settings.test_mode:
            expected = [dict(a, member_id=b['member_id']), dict(b, member_id=a['member_id'])]
            self._occupancy(expected, current, key_shifts)

    @staticmethod
    def _occupancy(expected, current, key_shifts):
        excluded = {item['id'] for item in expected}
        for destination in expected:
            start, end = [datetime.fromisoformat(destination[k]) for k in ('start', 'end')]
            for other in [*current.values(), *key_shifts]:
                if (other.get('id') not in excluded and other['member_id'] == destination['member_id']
                        and datetime.fromisoformat(other['start']) < end and datetime.fromisoformat(other['end']) > start):
                    raise TicketsSourceError('Sau khi đổi sẽ trùng giờ với phân công Lab Coach hoặc Key Coach khác.')

    def validate_pair(self, a, b):
        with self._lock:
            current, _, key_shifts = self._assignments()
            self._check(a, b, current, key_shifts)

    def validate_result(self, a, b):
        """Read-only post-write check using original snapshots, even after midnight."""
        with self._lock:
            a, b = self.reconciliation_snapshot(a), self.reconciliation_snapshot(b)
            current, _, key_shifts = self._assignments()
            expected = [dict(a, member_id=b['member_id'], member_name=b['member_name']),
                        dict(b, member_id=a['member_id'], member_name=a['member_name'])]
            if not all(current.get(item['id']) == item for item in expected):
                raise TicketsSourceError('Chưa đối soát được đúng hai ô Lab Coach theo nội dung đã xác nhận.')
            if not self.settings.test_mode:
                self._occupancy(expected, current, key_shifts)

    def reconciliation_snapshot(self, item):
        """Read-only compatibility for old test-mode date placeholders.

        Older test snapshots stored an entire calendar day even when the exact
        period hours were present. Only that exact representation is converted;
        source identity, day, period, owners and every other field stay intact.
        Never use this for preview, acceptance validation or a write.
        """
        result = dict(item)
        if not self.settings.test_mode:
            return result
        match = EXPLICIT_PERIOD.fullmatch(item['period'])
        if not match:
            return result
        try:
            original = datetime.fromisoformat(item['start'])
            midnight = datetime.combine(original.astimezone(VN).date(), time.min, VN)
            if (item['start'] != midnight.isoformat()
                    or item['end'] != (midnight + timedelta(days=1)).isoformat()):
                return result
            start = datetime.combine(midnight.date(), time.fromisoformat(match[2]), VN)
            end = datetime.combine(midnight.date(), time.fromisoformat(match[3]), VN)
            if end <= start:
                return result
        except (TypeError, ValueError):
            raise TicketsSourceError('Thời gian trong bản đối soát cũ chưa hợp lệ.') from None
        return dict(result, start=start.isoformat(), end=end.isoformat())

    def swap(self, a, b, ticket_id):
        require_ticket_access()
        if not self.settings.write_enabled or not self.settings.exclusive_writer:
            raise TicketsSourceError('Ghi đổi ca trên lịch lab đang tắt; cần quyền ghi và xác nhận chỉ một bên sửa lịch.')
        if not isinstance(ticket_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', ticket_id):
            raise TicketsSourceError('Mã yêu cầu đổi ca không hợp lệ.')
        with self._lock:
            current, positions, key_shifts = self._assignments()
            self._check(a, b, current, key_shifts)
            requests = []
            for destination, owner in ((a, b), (b, a)):
                record = positions[destination['id']]
                requests.append({'updateCells': {
                    'range': {'sheetId': record['sheet_id'], 'startRowIndex': record['row'],
                              'endRowIndex': record['row'] + 1, 'startColumnIndex': 3, 'endColumnIndex': 4},
                    'rows': [{'values': [{'userEnteredValue': {'stringValue': owner['member_name']}}]}],
                    'fields': 'userEnteredValue',
                }})
            self._request('POST', json={'requests': requests, 'includeSpreadsheetInResponse': False})
