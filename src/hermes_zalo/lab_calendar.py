"""Read the existing Lab Coach roster without guessing values in blank cells.

Only vertical merges reported by Google propagate values. Day banners and
repeated headers delimit sections; no model or sheet cell can select tools.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import re


MAX_ROWS = 1000
DAY = re.compile(r"(?:Thứ\s+[2-7]|Chủ nhật)\s*[-–]\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
HEADER = ["STT", "Khóa", "Session", "Lab Coach", "Key Coach", "Phòng", "Buổi", "Sức chứa phòng"]


def clean(value):
    if not isinstance(value, str) or len(value) > 4000:
        raise ValueError("Invalid cell.")
    return " ".join(value.split())


@dataclass
class LabEvent:
    day: date
    course: str
    session: str
    key_coach: str
    room: str
    period: str
    first_row: int
    last_row: int
    coaches: list[str] = field(default_factory=list)
    coach_names: list[str] = field(default_factory=list)


@dataclass
class LabSchedule:
    tab: str
    sheet_id: int
    days: set[date]
    events: list[LabEvent]


def parse_sheet(sheet):
    """Parse bounded spreadsheets.get grid data, including actual merge ranges."""
    props = sheet["properties"]
    if not 1 <= props["gridProperties"]["rowCount"] <= MAX_ROWS:
        raise ValueError("Unsupported grid size.")
    sheet_id = props["sheetId"]
    if type(sheet_id) is not int or sheet_id < 0:
        raise ValueError("Invalid sheet id.")
    data = sheet["data"]
    if len(data) != 1 or data[0].get("startRow", 0) != 0 or data[0].get("startColumn", 0) != 0:
        raise ValueError("Unexpected data range.")
    raw = []
    for row in data[0].get("rowData", []):
        cells = row.get("values", [])
        if len(cells) > 10:
            raise ValueError("Unexpected columns.")
        raw.append([clean(cell.get("formattedValue", "")) for cell in cells] + [""] * (10 - len(cells)))
    if len(raw) > MAX_ROWS:
        raise ValueError("Too many rows.")
    # Google may omit empty trailing rows. Merges may extend into those rows.
    raw += [[""] * 10 for _ in range(MAX_ROWS - len(raw))]
    expanded = [row.copy() for row in raw]
    occupied = set()
    for merge in sheet.get("merges", []):
        a, b = merge.get("startRowIndex", 0), merge["endRowIndex"]
        c, d = merge.get("startColumnIndex", 0), merge["endColumnIndex"]
        if not all(type(v) is int for v in (a, b, c, d)) or not (0 <= a < b and 0 <= c < d):
            raise ValueError("Invalid merge.")
        if c >= 10 or a >= MAX_ROWS:
            continue
        if b > MAX_ROWS:
            raise ValueError("Merge outside supported rows.")
        if d - c != 1:
            # Horizontal banners are read solely from their top-left cell.
            if any(re.fullmatch(r"[1-9]\d*", raw[r][0]) for r in range(a, b)):
                raise ValueError("Merged data columns are ambiguous.")
            continue
        if c == 0:
            raise ValueError("Cannot merge row identifiers.")
        for r in range(a, b):
            if (r, c) in occupied or (r != a and raw[r][c]):
                raise ValueError("Overlapping merge.")
            if r != a and (DAY.fullmatch(raw[r][0]) or raw[r][0] == "STT"):
                raise ValueError("Merge crosses day or header.")
            occupied.add((r, c))
            expanded[r][c] = raw[a][c]

    current_day = None
    ready = False
    days = set()
    events = []
    previous = None
    header_count = 0
    for index, row in enumerate(expanded):
        first = raw[index][0]
        match = DAY.fullmatch(first)
        if match:
            current_day = datetime.strptime(match[1], "%d/%m/%Y").date()
            days.add(current_day)
            ready = False
            previous = None
            continue
        if first.startswith(("Thứ ", "Chủ nhật")):
            raise ValueError("Invalid day banner.")
        if first == "STT":
            if not current_day or row[:8] != HEADER or row[9] != "Trạng thái":
                raise ValueError("Unexpected header.")
            ready = True
            previous = None
            header_count += 1
            continue
        numbered = bool(re.fullmatch(r"[1-9]\d*", first))
        # Some manually inserted coach rows have no STT. Accept them only when
        # Google's real session merge anchors them to a schedule section.
        continuation = not first and bool(raw[index][3]) and (index, 2) in occupied and bool(row[2])
        if not numbered and not continuation:
            previous = None
            # Titles, blank separators and no-class notices end data sections.
            if any(raw[index]):
                ready = False
            continue
        if not ready or not current_day:
            raise ValueError(f"Row {index + 1} has no day or header.")
        key = (current_day, row[1], row[2], row[4], row[5], row[6])
        if previous != key:
            events.append(LabEvent(*key, index + 1, index + 1))
        event = events[-1]
        event.last_row = index + 1
        # Keep raw roster status beside each named person; do not interpret it
        # as attendance, confirmation, or availability for another day.
        if row[3] and row[3] != "-":
            coach = row[3] + (f" [{row[9]}]" if row[9] else "")
            if coach not in event.coaches:
                event.coaches.append(coach)
                # Preserve the name cell separately for concise display,
                # without stripping brackets that may belong to a real name.
                event.coach_names.append(row[3])
        previous = key
    if not days or not header_count:
        raise ValueError("No recognized calendar sections.")
    return LabSchedule(clean(props["title"]), sheet_id, days, events)


def format_calendar(schedules, day, spreadsheet_id):
    selected = [(schedule, event) for schedule in schedules for event in schedule.events if event.day == day]
    covered = [s for s in schedules if day in s.days]
    if not covered:
        return (f"Chưa có dữ liệu ngày {day:%d/%m/%Y} trong các tab Lịch lab đã cấu hình.\n"
                "Các tab đã kiểm tra: " + ", ".join(s.tab for s in schedules) + ".")
    if not selected:
        return (f"Không có dòng phân công ngày {day:%d/%m/%Y} trong các tab đã kiểm tra.\n"
                "Nguồn: " + ", ".join(s.tab for s in covered) + ".")
    lines = [f"Lịch lab {day:%d/%m/%Y} (theo bảng phân công):"]
    shown = 0
    for schedule, event in selected:
        people = "; ".join(event.coaches) or "Chưa ghi tên"
        block = (f"{event.period or 'Chưa ghi buổi'} | {event.course or 'Chưa ghi khóa'} | {event.session or 'Chưa ghi session'}\n"
                 f"Phòng: {event.room or 'Chưa ghi'} · Key Coach: {event.key_coach or 'Chưa ghi'}\n"
                 f"Lab Coach: {people}\n"
                 f"Nguồn: {schedule.tab}, A{event.first_row}:J{event.last_row}\n"
                 f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
                 f"#gid={schedule.sheet_id}&range=A{event.first_row}:J{event.last_row}")
        # Bound the final reply below the adapter's limit, without cutting names
        # or citations mid-block; explicitly report omitted entries.
        if shown == 20 or sum(map(len, lines)) + len(block) > 10000:
            break
        lines.append(block)
        shown += 1
    if not shown:
        lines.append("Mục lịch quá dài để gửi trọn qua bot; hãy mở bảng nguồn.")
    if shown < len(selected):
        lines.append(f"Đang hiển thị {shown}/{len(selected)} mục; mở bảng nguồn để xem đầy đủ.")
    lines.append("Buổi và trạng thái giữ nguyên theo bảng; chưa có giờ bắt đầu/kết thúc cụ thể.")
    if len({s.tab for s, _ in selected}) > 1:
        lines.append("Ngày này có trong nhiều tab; các mục được giữ riêng theo nguồn.")
    return "\n\n".join(lines)


def format_query(schedules, query, spreadsheet_id, *, exact_coach=False):
    """Read-only date/filter/page selection; every page reapplies the same ACL."""
    from .commands import normalized

    def page_command(page):
        if not exact_coach:
            return query.command(page)
        from dataclasses import replace
        personal = replace(query, filters=tuple((k, v) for k, v in query.filters if k != 'coach'))
        return personal.command(page).replace('/lich ', '/lichcuatoi ', 1)

    def contains(value, needle):
        # Match complete words: room H20 does not match H201, An not Thanh.
        value, needle = normalized(value), normalized(needle)
        return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", value) is not None

    def matches(event):
        for key, value in query.filters:
            if key == "coach":
                people = [event.key_coach] + [person.split(" [", 1)[0] for person in event.coaches]
                if not any(normalized(person) == normalized(value) if exact_coach else contains(person, value)
                           for person in people):
                    return False
            elif not contains({"khoa": event.course, "phong": event.room, "buoi": event.period}[key], value):
                return False
        return True

    label = f"{query.start:%d/%m/%Y}" if query.start == query.end else f"{query.start:%d/%m/%Y}–{query.end:%d/%m/%Y}"
    days = {query.start + timedelta(days=i) for i in range((query.end - query.start).days + 1)}
    covered = set().union(*(s.days for s in schedules)) & days
    missing = ("Chưa có dữ liệu cho: " + ", ".join(f"{d:%d/%m/%Y}" for d in sorted(days - covered)) + ".") if days - covered else ""
    selected = [(s, e) for s in schedules for e in s.events if query.start <= e.day <= query.end and matches(e)]
    selected.sort(key=lambda pair: (pair[1].day, {"sang": 0, "chieu": 1, "toi": 2}.get(normalized(pair[1].period), 3),
                                    pair[0].tab, pair[1].first_row))
    filters = " · ".join(f"{key}={value}" for key, value in query.filters)
    if not covered:
        return f"Chưa có dữ liệu lịch ngày {label}.\n/help lich — xem cách tra lịch"
    if not selected:
        message = "Không tìm thấy lịch khớp bộ lọc" if query.filters else "Không có dòng phân công"
        return (f"{message} ngày {label}." + (f"\nBộ lọc: {filters}" if filters else "") +
                (f"\n{missing}" if missing else "") +
                f"\nThử bỏ bộ lọc: /lich {query.start.isoformat()}\n/help lich — xem ví dụ")
    page_count = (len(selected) + 4) // 5
    if query.page > page_count:
        return f"Kết quả chỉ có {page_count} trang. Về trang đầu:\n{page_command(1)}"
    lines = [f"Lịch lab {label} · {len(selected)} mục" +
             (f" · Trang {query.page}/{page_count}" if page_count > 1 else "")]
    if filters:
        lines.append("Bộ lọc: " + filters)
    start = (query.page - 1) * 5
    for number, (schedule, event) in enumerate(selected[start:start + 5], start + 1):
        # Keep whole names and explicitly report omissions in oversized cells.
        names = event.coach_names or event.coaches
        people_lines = []
        used = 0
        for index, name in enumerate(names):
            if used + len(name) + 3 > 800:
                people_lines.append(f"• … còn {len(names) - index} tên chưa hiển thị.")
                break
            people_lines.append(f"• {name}")
            used += len(name) + 3
        people = "\n".join(people_lines) or "Chưa ghi tên"
        short = lambda value: value[:150] + ("…" if len(value) > 150 else "")
        day_label = f"{event.day:%d/%m} · " if query.start != query.end else ""
        lines.append(
            f"{number}. {day_label}{short(event.period) or 'Chưa ghi buổi'} · {short(event.course) or 'Chưa ghi khóa'} · {short(event.session) or 'Chưa ghi session'}\n"
            f"Phòng: {short(event.room) or 'Chưa ghi'} | Key Coach: {short(event.key_coach) or 'Chưa ghi'}\n"
            f"Lab Coach:\n{people}")
    if missing:
        lines.append(missing)
    if any(sum(e.day == day for s in schedules for e in s.events) and sum(day in s.days for s in schedules) > 1 for day in days):
        lines.append("Một số ngày có nhiều bảng lịch; các mục được liệt kê riêng.")
    if query.page < page_count:
        lines.append("Xem tiếp:\n" + page_command(query.page + 1))
    return "\n\n".join(lines)
