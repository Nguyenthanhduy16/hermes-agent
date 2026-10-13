"""Small deterministic command vocabulary; prose never grants tool access."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from ipaddress import ip_address
import re
import unicodedata
from urllib.parse import urlsplit

from .reminders import VIETNAM_TZ


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
                   if not unicodedata.combining(c))


TICKETS_COMMANDS = frozenset((
    "/tickets", "/doica", "/yeucau", "/choxacnhan", "/lichsudoica", "/choduyet",
    "/dongy", "/tuchoi", "/huydoica", "/duyet", "/doisoat",
))
TICKETS_TOPICS = frozenset(("tickets", "doica", "doi ca", "yeucau", "yeu cau", "choxacnhan",
                          "lichsudoica", "choduyet", "dongy", "tuchoi", "huydoica", "duyet", "doisoat"))
TICKETS_DM_GUIDE = ("Đổi ca được xử lý trong tin nhắn riêng với bot. "
                    "Hãy mở cuộc trò chuyện riêng và gửi /doica để chọn ca. "
                    "Cần đăng ký hoặc liên kết tài khoản của bạn trước khi dùng; "
                    "đăng ký tên tra lịch không cấp quyền đổi ca.")


def ticket_route(text):
    """Recognize chat tickets deterministically, without inferring an action.

    A vague swap request opens instructions; only canonical commands or the
    explicit phrases below can start a workflow or select an existing ticket.
    Arguments retain their original case, accents and confirmation code.
    """
    text = unicodedata.normalize("NFC", text).strip()
    pieces = text.split(maxsplit=1)
    head, argument = (pieces + [""])[:2] if pieces else ("", "")
    if head.startswith("/"):
        command = normalized(head)
        return (command, argument) if command in TICKETS_COMMANDS else None
    folded = normalized(text)
    phrase = " ".join(folded.split()).rstrip(".!?").rstrip()
    exact = {
        "toi muon doi ca": "/tickets", "tickets": "/tickets", "doi ca": "/tickets",
        "minh muon doi ca": "/tickets", "em muon doi ca": "/tickets",
        "cho minh doi ca": "/tickets", "cho toi doi ca": "/tickets", "giup minh doi ca": "/tickets",
        "mo trang doi ca": "/tickets", "mo trang tickets": "/tickets", "trang doi ca": "/tickets",
        "tao yeu cau doi ca": "/doica",
        "yeu cau cua toi": "/yeucau",
        "xem yeu cau doi ca cua minh": "/yeucau", "xem yeu cau doi ca cua toi": "/yeucau",
        "yeu cau doi ca cua minh": "/yeucau", "yeu cau doi ca cua toi": "/yeucau",
        "co yeu cau nao cho toi khong": "/choxacnhan", "yeu cau cho toi": "/choxacnhan",
        "minh co yeu cau doi ca nao dang cho khong": "/choxacnhan",
        "toi co yeu cau doi ca nao dang cho khong": "/choxacnhan",
        "ai dang cho minh xac nhan": "/choxacnhan", "ai dang cho toi xac nhan": "/choxacnhan",
        "xem yeu cau dang cho minh xac nhan": "/choxacnhan",
        "lich su doi ca cua toi": "/lichsudoica",
        "cac yeu cau cho duyet": "/choduyet",
    }
    if phrase in exact:
        return exact[phrase], ""
    detail = re.fullmatch(r"xem\s+yeu\s+cau\s+(dc-[a-z0-9-]+)[.!?]?", folded)
    if detail:
        return "/yeucau", text[detail.start(1):detail.end(1)]
    # Policy questions remain document reads. Unstructured action requests
    # receive a safe menu instead of being interpreted as a write by the model.
    vague_swap = re.match(
        r"^(?:(?:(?:toi|minh|em)\s+(?:(?:muon|can|xin)\s+)?)|(?:(?:cho|giup)\s+(?:toi|minh|em)\s+))?doi\s+ca\b"
        r"|^(?:tao|gui|xin)\s+yeu\s+cau\s+doi\s+ca\b", folded)
    if vague_swap and not re.search(r"\b(?:quy dinh|quy trinh|noi quy)\b", folded):
        return "/tickets", ""
    return None


def public_tickets_url(value):
    """Validate an administrator-supplied public link without fetching it.

    A valid URL does not prove the site is deployed or authenticate its users.
    Never expose a local preview, a credential-bearing URL or malformed host.
    """
    value = value.strip()
    if not value or len(value) > 2048 or "\\" in value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme != "https" or not host or parsed.username is not None or
                parsed.password is not None or parsed.port == 0 or "%" in host):
            return None
        try:
            address = ip_address(host)
        except ValueError:
            host = host.rstrip(".").encode("idna").decode("ascii").lower()
            labels = host.split(".")
            if (len(labels) < 2 or len(host) > 253 or
                    any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels) or
                    not re.fullmatch(r"[a-z]{2,63}|xn--[a-z0-9-]+", labels[-1]) or
                    labels[-1] in {"localhost", "local", "localdomain", "localnet", "internal", "intranet", "intra", "corp", "lan", "home", "private", "test", "invalid", "example", "onion"} or
                    host == "home.arpa" or host.endswith(".home.arpa")):
                return None
        else:
            if (not address.is_global or address.is_multicast or address.is_reserved or
                    (address.version == 6 and address.ipv4_mapped is not None and not address.ipv4_mapped.is_global)):
                return None
        return value
    except (ValueError, UnicodeError):
        return None


def route(text):
    """Return a canonical command and original arguments, or ordinary prose."""
    text = unicodedata.normalize("NFC", text).strip()
    ticket = ticket_route(text)
    if ticket:
        return ticket
    pieces = text.split(maxsplit=1)
    head, argument = (pieces + [""])[:2] if pieces else ("", "")
    command = normalized(head)
    aliases = {"/menu": "/trogiup", "/help": "/trogiup", "/lenh": "/trogiup", "/gopy": "/phanhoi"}
    if command.startswith("/"):
        return aliases.get(command, command), argument
    folded = normalized(text)
    # Exact read/navigation phrases only. Longer policy questions or ordinary
    # prose containing these words retain their normal interpretation.
    phrase = " ".join(folded.split()).rstrip(".!?").rstrip()
    navigation = {
        "thong bao": "/thongbao", "xem thong bao": "/thongbao",
        "minh co thong bao gi khong": "/thongbao", "toi co thong bao gi khong": "/thongbao",
        "cho minh xem thong bao": "/thongbao",
        "bot lam duoc gi": "/trogiup", "cho minh xem menu": "/trogiup",
        "cho toi xem menu": "/trogiup", "xem menu": "/trogiup", "bot co tinh nang gi": "/trogiup",
    }
    if phrase in navigation:
        return navigation[phrase], ""
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


def help_text(topic="", *, sheets_state="not_configured", lab=False, issues=False,
              issue_access=False, reminders=False, tickets=False, private_chat=True):
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
    if topic in ("thongbao", "thong bao"):
        lines = ["THÔNG BÁO", ""]
        if reminders:
            lines += ["Nhắc lịch của bạn tại chat này:",
                      "/nhac xem — các nhắc đang chờ",
                      "/nhac lichsu — nhắc gần đây và kết quả gửi", ""]
        if tickets:
            lines += ["Yêu cầu đổi ca:",
                      "/choxacnhan — yêu cầu đang chờ bạn đồng ý" +
                      (" (nhắn riêng cho bot)" if not private_chat else ""),
                      "Bot gửi thông báo riêng khi có yêu cầu mới cho tài khoản đã liên kết.", ""]
        if not reminders and not tickets:
            lines += ["Chưa có mục thông báo được mở cho bạn tại đây.", ""]
        lines.append("/menu — trở về menu")
        return "\n".join(lines)
    if topic in ("nhac", "nhac viec", "nhac lich"):
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
                "Máy chạy bot cần bật và có mạng.\n"
                "Bạn tự đặt từng nhắc; bot chưa tự tạo nhắc từ lịch Sheets.")
    if topic in ("hoi", "tailieu", "tai lieu"):
        return ("HỎI TÀI LIỆU\n/tailieu — xem tài liệu bạn được phép đọc\n"
                "/hoi Giờ làm việc của nhóm là khi nào?\n"
                "Hoặc: hỏi tài liệu Giờ làm việc của nhóm là khi nào?\n"
                "Bot tìm trích đoạn và chỉ rõ nguồn; nếu chưa tìm thấy sẽ báo rõ. "
                "Tra lịch Google Sheets dùng /lich.\n/menu — trở về hướng dẫn chung")
    if topic == "issue":
        if not issues:
            if issue_access:
                return ("TẠO ISSUE — hiện chưa bật.\n"
                        "Nhờ người quản lý bật tính năng và chuẩn bị tab nhận issue trên Sheets.\n"
                        "Khi bật, bot sẽ đưa bản nháp để bạn xác nhận trước khi tạo.\n"
                        "/menu — trở về menu")
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
    if topic in TICKETS_TOPICS:
        return ("ĐỔI CA QUA TIN NHẮN RIÊNG\n"
                "1. Người tạo nhắn /doica, chọn hai ca tương lai và kiểm tra nội dung.\n"
                "2. Gửi lệnh /doica gui kèm mã bot cấp. Chờ thông báo Đã gửi yêu cầu.\n"
                "3. Bot gửi thông báo riêng cho người đổi cùng đã được liên kết.\n"
                "4. Đọc hai ca rồi gửi /dongy hoặc /tuchoi kèm mã 4 số bot cấp.\n\n"
                "Không nhận được tin hoặc mã hết hạn: /choxacnhan\n"
                "Danh sách dài: /doica trang 2; quay về: /doica trang 1.\n"
                "Bản xem trước và mã xác nhận có hiệu lực tối đa 15 phút.\n"
                "Bot chỉ đổi lịch sau khi đủ các bước xác nhận; có bước quản lý nếu được cấu hình.\n\n"
                "/tickets — menu hoặc tiếp tục bước đang nhập\n"
                "/yeucau — xem hoặc hủy yêu cầu của tôi\n"
                "/lichsudoica — lịch sử\n"
                "/choduyet — dành cho quản lý\n\n"
                "Cần quản trị liên kết tài khoản trước khi đổi ca; /dangky chỉ lưu tên tra lịch.")
    if topic in ("dangky", "hoso", "nhom"):
        return ("DÙNG BOT TRONG NHÓM\n"
                "Trả lời một tin của bot hoặc gọi @bot, rồi hỏi lịch/quy định.\n"
                "Đăng ký tên: /dangky Họ tên đầy đủ đúng trong bảng\n"
                "Lịch cá nhân: /lichcuatoi homnay hoặc Ngày mai mình có lịch gì?\n"
                "Xem tên đã đăng ký: /hoso · Xóa tên: /hoso xoa\n"
                "Tên chỉ dùng để lọc lịch tại chat này, không xác minh danh tính hay cấp quyền.\n"
                "Chỉ đọc lịch và tài liệu được quản trị chia sẻ với nhóm. Câu trả lời hiển thị cho cả nhóm.")
    if topic:
        return "Chưa có mục hướng dẫn này. Gửi /menu để xem các mục đang dùng."
    # Menu entries reflect this user's access. Disabled issue creation stays
    # visible to eligible users with an explicit status, never enabling writes.
    entries = []
    if sheets_state == "ready":
        entries.append("Tra lịch: /lich homnay")
        if lab:
            entries.append("Lịch của tôi: /lichcuatoi homnay")
    if tickets:
        entries.append("Đổi ca: /doica" + (" (nhắn riêng cho bot)" if not private_chat else ""))
    if reminders:
        entries.append("Nhắc lịch: /nhac")
    if reminders or tickets:
        entries.append("Thông báo: /thongbao")
    if issues or issue_access:
        entries.append("Tạo issue: /issue" + ("" if issues else " (chưa bật)"))
    lines = ["MENU BOT", ""] + [f"{index}. {entry}" for index, entry in enumerate(entries, 1)]
    if sheets_state == "ready" and lab:
        lines += ["", "Chưa đăng ký tên? /dangky Họ tên đầy đủ"]
    if sheets_state == "ready":
        lines += ["", "Có thể hỏi: Mai mình có ca nào?" if lab else "Có thể hỏi: Mai có lịch gì?"]
    if tickets:
        lines += ["Đổi ca tự nhiên: Mình muốn đổi ca"]
    if not entries:
        lines += ["Bạn có thể nhắn trực tiếp để trò chuyện." if private_chat else
                  "Chưa có mục nào được mở tại nhóm này. Nhờ quản trị kiểm tra quyền."]
    if not private_chat:
        lines += ["", "Trong nhóm: @bot hoặc trả lời tin của bot để hỏi hay trò chuyện."]
    return "\n".join(lines)


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
