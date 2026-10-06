"""Small deterministic command vocabulary; prose never grants tool access."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
import unicodedata

from .reminders import VIETNAM_TZ


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
                   if not unicodedata.combining(c))


def route(text):
    """Return a canonical command and original arguments, or ordinary prose."""
    text = unicodedata.normalize("NFC", text).strip()
    pieces = text.split(maxsplit=1)
    head, argument = (pieces + [""])[:2] if pieces else ("", "")
    command = normalized(head)
    aliases = {"/menu": "/trogiup", "/help": "/trogiup", "/lenh": "/trogiup", "/gopy": "/phanhoi"}
    if command.startswith("/"):
        return aliases.get(command, command), argument
    folded = normalized(text)
    for phrase, command in (("xem lich", "/lich"), ("lich", "/lich"),
                            ("hoi tai lieu", "/hoi"), ("tro giup", "/trogiup"),
                            ("huong dan", "/trogiup"), ("menu", "/trogiup")):
        match = re.match(r"^" + r"\s+".join(phrase.split()) + r"(?=\s|$)", folded)
        if match:
            if phrase == "lich" and folded[match.end():].lstrip().startswith("su"):
                return "", text  # "lịch sử ..." is ordinary conversation
            return command, text[match.end():].strip()
    if folded in ("tai lieu", "xem tai lieu"):
        return "/tailieu", ""
    if folded in ("xem nhac viec", "danh sach nhac viec"):
        return "/nhac", "xem"
    # Existing explicit natural reminder syntax is handled by its own parser.
    return "", text


def help_topic(argument):
    return normalized(" ".join(argument.split())).lstrip("/") in ("help", "trogiup", "tro giup", "huong dan", "?")


def help_text(topic="", *, sheets_state="not_configured", lab=False, issues=False):
    topic = normalized(" ".join(topic.split())).lstrip("/")
    tomorrow = (datetime.now(VIETNAM_TZ) + timedelta(days=1)).strftime("%d/%m/%Y")
    if topic in ("lich", "lichcuatoi", "calendar"):
        result = ("TRA LỊCH\n"
                  "/lich homnay — hôm nay\n/lich mai — ngày mai\n"
                  "/lich 06/10/2026 — ngày cụ thể\n"
                  "/lich 06/10 — ngày/tháng trong năm hiện tại\n"
                  "Có thể nhắn: lịch hôm nay hoặc xem lịch ngày mai.\n"
                  "Ngày được tính theo giờ Việt Nam.")
        if lab:
            result += ("\n\nLọc lịch Lab (thêm sau dấu |):\n"
                       "/lich homnay | coach=Nguyễn Văn An\n"
                       "/lich homnay | khoa=Track 1 | buoi=sáng\n"
                       "/lich homnay | phong=H201\n"
                       "/lich tuan nay — thứ Hai đến Chủ nhật\n"
                       "/lich 06/10/2026 | trang=2 — trang tiếp theo\n"
                       "Mỗi trang tối đa 5 mục, có dòng và link nguồn. Coach tìm cả Lab Coach và Key Coach.\n"
                       "Lịch của bạn: /dangky Họ tên đầy đủ, rồi /lichcuatoi homnay.\n"
                       "Có thể hỏi: Ngày mai mình có lịch gì? hoặc Tuần này tôi dạy ca nào?")
        result += "\n\n" + {"ready": "Lịch đã cấu hình; kết nối sẽ được kiểm tra khi tra.",
            "denied": "Bạn/cuộc trò chuyện này chưa được cấp quyền tra lịch. Nhờ người quản lý bot cấp quyền.",
            "invalid": "Cấu hình lịch cần người quản lý bot kiểm tra.",
            "not_configured": "Lịch chưa được kết nối."}[sheets_state]
        return result
    if topic in ("nhac", "nhac viec"):
        return ("NHẮC VIỆC\n"
                "1. Tạo nhắc:\n/nhac sau 10 phut | Gửi báo cáo\n"
                "/nhac mai 09:00 | Họp nhóm\n"
                f"/nhac {tomorrow} 09:00 | Kiểm tra lịch\n"
                "Hoặc: nhắc mình sau 10 phút: Gửi báo cáo\n"
                "2. Xem các nhắc đang chờ: /nhac xem\n"
                "3. Xem các nhắc gần đây: /nhac lichsu\n"
                "4. Hủy: /nhac huy MÃ_NHẮC\n"
                "Thay MÃ_NHẮC bằng mã bot gửi khi tạo hoặc trong danh sách.\n"
                "Dấu | ngăn thời gian và nội dung. Giờ Việt Nam; bot nhắc tại chính chat tạo nhắc. "
                "Máy chạy bot cần bật và có mạng.")
    if topic in ("hoi", "tailieu", "tai lieu"):
        return ("HỎI TÀI LIỆU\n/tailieu — xem tài liệu bạn được phép đọc\n"
                "/hoi Giờ làm việc của nhóm là khi nào?\n"
                "Hoặc: hỏi tài liệu Giờ làm việc của nhóm là khi nào?\n"
                "Bot tìm trích đoạn và chỉ rõ nguồn; nếu chưa tìm thấy sẽ báo rõ. "
                "Tra lịch Google Sheets dùng /lich.\n/menu — trở về hướng dẫn chung")
    if topic == "issue":
        if not issues:
            return "TẠO ISSUE — hiện chưa bật hoặc bạn chưa được cấp quyền.\nDùng /menu để xem tính năng có thể dùng."
        return ("TẠO ISSUE\n1. Gửi nội dung:\n/issue tao Lỗi nút lưu | Bấm Lưu nhưng dữ liệu chưa cập nhật\n"
                "2. Đọc bản nháp bot gửi; lúc này chưa tạo issue.\n"
                "3. Nếu đúng: /issue xacnhan MÃ_BẢN_NHÁP\n"
                "Nếu không muốn tạo: /issue huy MÃ_BẢN_NHÁP\n"
                "Chỉ người tạo được xác nhận tại chat ban đầu, trong 10 phút. "
                "Câu trả lời “đồng ý” không thay cho lệnh xác nhận.")
    if topic in ("phanhoi", "gopy", "gop y"):
        return ("GỬI GÓP Ý\n/phanhoi Lịch ngày 06/10 thiếu phòng\n"
                "Ghi rõ lệnh bạn dùng, kết quả nhận được và điều bạn mong muốn. "
                "Góp ý được lưu trên máy chạy bot để người quản lý tổng hợp.")
    if topic in ("dangky", "hoso", "nhom"):
        return ("DÙNG BOT TRONG NHÓM\n"
                "Trả lời một tin của bot hoặc gọi @bot, rồi hỏi lịch/quy định.\n"
                "Đăng ký tên: /dangky Họ tên đầy đủ đúng trong bảng\n"
                "Lịch cá nhân: /lichcuatoi homnay hoặc Ngày mai mình có lịch gì?\n"
                "Xem tên đã đăng ký: /hoso · Xóa tên: /hoso xoa\n"
                "Tên chỉ dùng để lọc lịch tại chat này, không xác minh danh tính hay cấp quyền.\n"
                "Chỉ đọc lịch và tài liệu được quản trị chia sẻ với nhóm. Câu trả lời hiển thị cho cả nhóm.")
    if topic:
        return "Chưa có mục hướng dẫn này. Dùng /menu hoặc /help lich, /help nhac, /help tailieu, /help issue, /help phanhoi."
    state = {"ready": "đã cấu hình", "denied": "chưa có quyền ở chat này", "invalid": "cần kiểm tra cấu hình",
             "not_configured": "chưa kết nối"}[sheets_state]
    return ("MENU BOT\n"
            f"1. Lịch ({state}): /lich homnay\n"
            "2. Nhắc việc: /nhac sau 10 phut | Gửi báo cáo\n"
            "3. Tài liệu: /tailieu hoặc /hoi Câu hỏi\n"
            "4. Góp ý: /phanhoi Nội dung\n"
            f"5. Issue: {'/help issue' if issues else 'hiện chưa bật hoặc chưa có quyền'}\n\n"
            "Lịch của bạn: /dangky Họ tên đầy đủ → /lichcuatoi homnay\n"
            "Xem chi tiết: /help nhom · /help lich · /help nhac · /help tailieu · /help phanhoi\n"
            "Có thể viết có dấu hoặc không dấu. Ví dụ: lịch hôm nay, xem nhắc việc.\n"
            "Trong nhóm: trả lời một tin của bot rồi nhập lệnh. Nhắn /menu bất cứ lúc nào để xem lại.")


@dataclass(frozen=True)
class CalendarQuery:
    start: date
    end: date
    filters: tuple[tuple[str, str], ...] = ()
    page: int = 1

    def command(self, page):
        day = self.start.isoformat() if self.start == self.end else f"{self.start.isoformat()}..{self.end.isoformat()}"
        return "/lich " + day + "".join(f" | {k}={v}" for k, v in self.filters) + f" | trang={page}"


def calendar_day(text, today):
    value = normalized(" ".join(text.split()))
    relative = {"": 0, "homnay": 0, "hom nay": 0, "nay": 0, "mai": 1, "ngay mai": 1,
                "homqua": -1, "hom qua": -1, "ngay kia": 2}
    if value in relative:
        return today + timedelta(days=relative[value])
    if value.startswith("ngay "):
        value = value[5:]
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if re.fullmatch(r"\d{1,2}/\d{1,2}(?:/\d{4})?", value):
            parts = [int(x) for x in value.split("/")]
            return date(parts[2] if len(parts) == 3 else today.year, parts[1], parts[0])
    except ValueError:
        pass
    raise ValueError("Ngày chưa hợp lệ. Ví dụ: /lich homnay, /lich mai hoặc /lich 06/10/2026.\nXem thêm: /help lich")


def parse_calendar(argument, *, today=None):
    today = today or datetime.now(VIETNAM_TZ).date()
    parts = argument.split("|")
    period = normalized(" ".join(parts[0].split()))
    if period in ("tuan nay", "tuan sau"):
        start = today - timedelta(days=today.weekday()) + timedelta(days=7 if period == "tuan sau" else 0)
        end = start + timedelta(days=6)
    elif ".." in period:
        bounds = period.split("..")
        if len(bounds) != 2:
            raise ValueError("Khoảng ngày chưa hợp lệ. Dùng /lich tuan nay hoặc /help lich.")
        start, end = [calendar_day(x, today) for x in bounds]
        if not 0 <= (end - start).days <= 6:
            raise ValueError("Mỗi lần tra tối đa 7 ngày, ngày cuối cần bằng hoặc sau ngày đầu.")
    else:
        start = end = calendar_day(parts[0], today)
    filters = {}
    page = 1
    seen = set()
    for part in parts[1:]:
        key, separator, value = part.partition("=")
        key = normalized(key.strip())
        value = " ".join(value.split())
        if not separator or key not in ("coach", "khoa", "phong", "buoi", "trang") or key in seen or not value or len(value) > 100:
            raise ValueError("Bộ lọc chưa hợp lệ. Ví dụ: /lich homnay | coach=Nguyễn Văn An | phong=H201\nXem thêm: /help lich")
        seen.add(key)
        if key == "trang":
            if not re.fullmatch(r"[1-9]\d{0,2}", value):
                raise ValueError("Số trang cần từ 1 đến 999. Ví dụ: /lich homnay | trang=2")
            page = int(value)
        else:
            if key == "buoi" and normalized(value) not in ("sang", "chieu", "toi"):
                raise ValueError("Buổi cần là sáng, chiều hoặc tối. Ví dụ: /lich homnay | buoi=sáng")
            filters[key] = value
    return CalendarQuery(start, end, tuple(filters.items()), page)
