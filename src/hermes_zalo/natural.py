"""Vietnamese work intent grammar. No generated actions or arbitrary tools."""
import re
import unicodedata

from .commands import normalized, route, ticket_route


def is_work_question(text):
    """Keep unrecognized internal-work questions grounded in approved data.

    This is a routing guard, not an authorization check. General conversation
    still runs without tools or access to internal documents and Sheets.
    """
    folded = normalized(" ".join(text.split()))
    return bool(re.search(
        r"\b(?:lab|coach|track|noi bo|quy dinh|noi quy|quy trinh|chinh sach|"
        r"phan cong|cham cong|nghi phep|xin phep|nghi tet|nghi le|di muon|"
        r"luong|hoc phi|tai lieu|nhac viec|nhac lich|doi ca|yeu cau doi|issue|ticket)\b"
        r"|\blich\b(?!\s+su\b)"
        r"|\b(?:nhom|cong ty|trung tam)\s+(?:minh|toi|em|chung ta)\b"
        r"|\b(?:ca|phong)\s+(?:truc|day|hoc|lab)\b", folded))


def interpret(text):
    text = unicodedata.normalize("NFC", text).strip()
    folded = normalized(text)
    ticket = ticket_route(text)
    if ticket:
        return ticket
    if text.startswith("/"):
        command, argument = route(text)
        if command == "/lich" and normalized(argument).startswith(("cua toi", "cua minh", "cua em")):
            argument = re.sub(r"^cua (?:toi|minh|em)\s*", "", normalized(argument), count=1)
            return "/lichcuatoi", argument
        return command, argument
    # Exact navigation phrases take precedence over words like "lịch" in
    # "nhắc lịch". These aliases never confirm or execute a write.
    navigation = route(text)
    if navigation[0] in ("/trogiup", "/thongbao", "/nhac"):
        return navigation
    phrase = " ".join(folded.split()).rstrip(".!?")
    if phrase in ("nhac lich", "nhac lich giup minh", "minh muon dat nhac lich",
                  "minh muon tao nhac viec", "huong dan nhac lich"):
        return "/trogiup", "nhac"
    if phrase in ("xem cac nhac lich cua toi", "xem nhac lich cua minh",
                  "minh co nhac viec nao", "minh co nhac viec nao khong",
                  "cac nhac viec dang cho", "xem nhac dang cho"):
        return "/nhac", "xem"
    if phrase in ("xem lich su nhac viec", "xem lich su nhac lich"):
        return "/nhac", "lichsu"
    registration = re.match(r"^(?:toi la|minh la|em la|dang ky(?: ten)?)\s+", folded)
    if registration:
        return "/dangky", text[registration.end():].strip()
    if folded in ("ho so cua toi", "ten cua toi", "toi da dang ky ten gi", "toi da dang ky ten gi?"):
        return "/hoso", ""
    if phrase in ("xin chao", "chao bot", "bot oi", "hello"):
        return "", text
    # Keep instructions to write/cancel explicit. Free-form requests never
    # become issue confirmation or bulk deletes.
    if re.match(r"^(?:nhac minh|/nhac)(?:\s|$)", folded):
        return "", text  # existing strict reminder parser, including deadlines
    if re.search(r"\b(?:tao issue|tao ticket|bao loi)\b", folded):
        return "/trogiup", "issue"
    if re.search(r"\b(?:quy dinh|quy trinh|noi quy|nghi phep|xin nghi|di muon|cham cong|doi ca|chuan bi)\b", folded):
        return "/hoi", text
    if re.search(r"\b(?:xoa|huy|sua|doi|dat|tao)\s+(?:lich|ca)\b", folded):
        return "/lich", "help"
    explicit_calendar = bool(re.search(r"\blich\b", folded)) and not re.search(r"\blich su\b", folded)
    work_word = bool(re.search(r"\b(?:ca|lớp|lop|dạy|day|trực|truc)\b", text.casefold()))
    work_context = bool(re.search(r"\b(?:hom nay|homnay|ngay mai|mai|tuan|nay|hom qua|ngay kia|"
                                  r"thu hai|thu ba|thu tu|thu nam|thu sau|thu bay|chu nhat)\b"
                                  r"|\bthu\s+[2-7]\b|\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2}", folded))
    availability = bool(re.search(r"\b(?:mình|minh|tôi|toi|em)\s+(?:(?:có|co)\s+)?"
                                  r"(?:bận|ban|rảnh|ranh|trực|truc)\b", text.casefold()))
    calendar = explicit_calendar or ((work_word or availability) and work_context and
                                     not re.search(r"\bca nhac\b", folded))
    if not calendar:
        return route(text)
    # Already structured read commands and filter values keep original text.
    command, argument = route(text)
    if command == "/lich" and "|" in argument:
        return command, argument
    personal = availability or bool(re.search(r"\b(?:cua\s+(?:toi|minh|em)|(?:toi|minh|em)\s+(?:co|day|truc|lam|hoc)|lich\s+(?:toi|minh|em))\b", folded))
    if re.search(r"\bcua\s+(?!toi\b|minh\b|em\b)", folded):
        return "/lich", "help"  # named people need the explicit coach filter
    date_pattern = r"(?<![\d/-])(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{4})?)(?![\d/-])"
    relative_pattern = r"tuan nay|tuan sau|hom nay|homnay|hom qua|ngay mai|ngay kia|mai"
    dates = re.findall(date_pattern, folded)
    relatives = re.findall(r"\b(?:" + relative_pattern + r")\b", folded)
    if (len(dates) + len(relatives) > 1 or
            (not dates and re.search(r"\d\s*[/\-]\s*\d", folded)) or
            re.search(r"\b(?:thu\s+[2-7]|thu hai|thu ba|thu tu|thu nam|thu sau|thu bay|chu nhat|thang|tuan toi|khi nao|ngay nao|hom kia|cuoi tuan|sap toi)\b", folded)):
        return "/lich", "help"  # do not guess a relative weekday or mixed dates
    day = (dates or relatives or ["homnay"])[0]
    filters = []
    room = re.search(r"\bphong\s+([a-z]+\d[\w-]*)\b", folded)
    if room:
        filters.append("phong=" + text[room.start(1):room.end(1)])
    periods = re.findall(r"\b(?:sang|chieu|toi)\b", folded)
    # 'tôi' is a pronoun, not 'tối'. Match the original accented word for tối,
    # or require an explicit 'buoi toi' for unaccented writing.
    periods = [p for p in periods if p != "toi"]
    if re.search(r"\b(?:tối)\b", text.casefold()) or "buoi toi" in folded:
        periods.append("toi")
    if len(set(periods)) > 1:
        return "/lich", "help"
    if periods:
        filters.append("buoi=" + periods[0])
    course = re.search(r"\btrack\s+(\d+[ab]?)\b", folded)
    if course:
        filters.append("khoa=Track " + course[1])
    argument = day + "".join(" | " + f for f in filters)
    return ("/lichcuatoi" if personal else "/lich"), argument
