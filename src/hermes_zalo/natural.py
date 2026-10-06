"""Vietnamese work intent grammar. No generated actions or arbitrary tools."""
import re
import unicodedata

from .commands import normalized, route


def interpret(text):
    text = unicodedata.normalize("NFC", text).strip()
    folded = normalized(text)
    if text.startswith("/"):
        command, argument = route(text)
        if command == "/lich" and normalized(argument).startswith(("cua toi", "cua minh", "cua em")):
            argument = re.sub(r"^cua (?:toi|minh|em)\s*", "", normalized(argument), count=1)
            return "/lichcuatoi", argument
        return command, argument
    registration = re.match(r"^(?:toi la|minh la|em la|dang ky(?: ten)?)\s+", folded)
    if registration:
        return "/dangky", text[registration.end():].strip()
    if folded in ("ho so cua toi", "ten cua toi", "toi da dang ky ten gi", "toi da dang ky ten gi?"):
        return "/hoso", ""
    if folded in ("xin chao", "chao bot", "bot oi", "hello"):
        return "", text  # group fallback shows menu; DMs retain normal chat
    # Keep instructions to write/cancel explicit. Free-form requests never
    # become issue confirmation or bulk deletes.
    if re.search(r"\b(?:tao issue|tao ticket|bao loi)\b", folded):
        return "/trogiup", "issue"
    if re.match(r"^(?:nhac minh|/nhac)(?:\s|$)", folded):
        return "", text  # existing strict reminder parser, including deadlines
    if re.search(r"\b(?:quy dinh|quy trinh|noi quy|nghi phep|xin nghi|di muon|cham cong|doi ca|chuan bi)\b", folded):
        return "/hoi", text
    if re.search(r"\b(?:xoa|huy|sua|doi|dat|tao)\s+(?:lich|ca)\b", folded):
        return "/lich", "help"
    explicit_calendar = bool(re.search(r"\blich\b", folded)) and not re.search(r"\blich su\b", folded)
    work_word = bool(re.search(r"\b(?:ca|lớp|lop|dạy|day)\b", text.casefold()))
    work_context = bool(re.search(r"\b(?:hom nay|ngay mai|mai|tuan|nay|co|truc|hoc|sang|chieu|toi)\b|\d{1,2}/\d{1,2}", folded))
    calendar = explicit_calendar or (work_word and work_context and not re.search(r"\bca nhac\b", folded))
    if not calendar:
        return route(text)
    # Already structured read commands and filter values keep original text.
    command, argument = route(text)
    if command == "/lich" and "|" in argument:
        return command, argument
    personal = bool(re.search(r"\b(?:cua\s+(?:toi|minh|em)|(?:toi|minh|em)\s+(?:co|day|truc|lam|hoc)|lich\s+(?:toi|minh|em))\b", folded))
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
