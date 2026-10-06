"""Small, offline, extractive document search with an explicit per-file ACL.

No document is sent to a model, and document text never becomes instructions.
The manifest and content are reopened for every request, including ACL changes.
Only explicit manifest entries are read; there is no recursive folder discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import unicodedata


UNAVAILABLE = "Chưa thể đọc kho tài liệu. Người quản trị cần kiểm tra cấu hình và quyền truy cập trên máy."
NO_SOURCES = "Chưa có tài liệu được cấp quyền cho bạn trong cuộc trò chuyện này."
NO_MATCH = "Chưa tìm thấy thông tin trả lời câu hỏi này trong tài liệu bạn được phép xem. Hãy thử nêu rõ tên nội dung cần tra cứu."
MAX_FILES = 100
MAX_MANIFEST_BYTES = 128 * 1024
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_QUESTION_CHARS = 2000
MAX_EXCERPT_CHARS = 1200
MAX_RESULTS = 3
MAX_PASSAGES = 4000

# Remove conversational/question words, not domain vocabulary. Vietnamese words
# are split into syllables, so the high coverage gate below deliberately prefers
# an honest no-match over a weak, generic keyword overlap.
_STOP_WORDS = frozenset("""
ai anh ban bao ben biet bot cac cai can cau chi cho chung co cua da dang day de den
do duoc gi giup hay hoi khi khong la lai ma minh moi mot nao nay neu nhieu
nhe nho nhu nhung noi o oi phai rang roi sao se tai theo thi toi trong tu va vay ve
voi xin the
""".split())
_FORBIDDEN_COMPONENTS = frozenset({
    "runtime", "logs", "log", "secrets", "secret", "credentials", "credential",
    "tokens", "token", "passwords", "password", "node_modules", "__pycache__",
})
_SECRET_NAME = re.compile(r"(?:^|[._-])(?:secrets?|credentials?|tokens?|passwords?|api[_-]?keys?)(?:[._-]|$)", re.I)


class _InvalidKnowledge(ValueError):
    """Internal error. Never include its details in a user-visible response."""


@dataclass(frozen=True)
class _Document:
    identifier: str
    title: str
    path: str
    users: frozenset[str]
    groups: frozenset[str]

    def allowed(self, user_id: str, chat_id: str, chat_type: str) -> bool:
        if chat_type == "PRIVATE":
            return user_id in self.users
        if chat_type == "GROUP":
            return chat_id in self.groups and (not self.users or user_id in self.users)
        return False


@dataclass(frozen=True)
class _Passage:
    document: _Document
    heading: str
    first_line: int
    last_line: int
    text: str


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    return "".join(char for char in text if not unicodedata.combining(char))


def _tokens(text: str) -> frozenset[str]:
    return frozenset(token for token in re.findall(r"[^\W_]+", _normalize(text), re.UNICODE)
                     if token not in _STOP_WORDS and len(token) > 1)


def _plain_string(value: object, maximum: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _InvalidKnowledge()
    if value != value.strip() or any(unicodedata.category(c).startswith("C") for c in value):
        raise _InvalidKnowledge()
    return value


def _identifiers(value: object) -> frozenset[str]:
    if not isinstance(value, list) or len(value) > 1000:
        raise _InvalidKnowledge()
    identifiers = [_plain_string(item) for item in value]
    if any("*" in item for item in identifiers) or len(set(identifiers)) != len(identifiers):
        raise _InvalidKnowledge()
    return frozenset(identifiers)


def _relative_path(value: object) -> str:
    raw = _plain_string(value, 240)
    # Explicit POSIX-style relative paths also exclude drive letters, UNC, ADS,
    # Windows normalization tricks and accidental environment/config references.
    if "\\" in raw or ":" in raw or raw.startswith("/"):
        raise _InvalidKnowledge()
    parts = raw.split("/")
    for part in parts:
        if (not part or part in {".", ".."} or part.startswith(".")
                or part.endswith((" ", ".")) or part.casefold() in _FORBIDDEN_COMPONENTS
                or _SECRET_NAME.search(part) or any(c in part for c in '<>"|?*')):
            raise _InvalidKnowledge()
    path = PurePosixPath(raw)
    if path.is_absolute() or path.suffix.casefold() not in {".md", ".txt"}:
        raise _InvalidKnowledge()
    return raw


def _reject_link(path: Path) -> os.stat_result:
    info = path.lstat()
    # is_symlink alone does not reject all Windows directory junctions.
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise _InvalidKnowledge()
    return info


def _json_pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise _InvalidKnowledge()
        value[key] = item
    return value


class KnowledgeBase:
    """Search an administrator-managed UTF-8 .md/.txt knowledge folder.

    manifest.json shape: {"documents": [{"id": "...", "title": "...",
    "path": "handbook.md", "users": ["user-id"], "groups": ["group-id"]}]}.
    PRIVATE requires membership in users. GROUP requires membership in groups;
    a nonempty users list additionally restricts who can ask in that group. An
    empty users list permits globally approved members of the named groups only.
    The caller MUST enforce its global user/group allowlist before calling here.
    """

    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(root))

    def _root(self) -> Path:
        # Reject a linked root or a linked ancestor as well as linked documents.
        for part in reversed((self.root, *self.root.parents)):
            _reject_link(part)
        if not self.root.is_dir():
            raise _InvalidKnowledge()
        return self.root.resolve(strict=True)

    @staticmethod
    def _read(root: Path, relative: str, limit: int) -> bytes:
        candidate = root.joinpath(*relative.split("/"))
        current = root
        for component in relative.split("/"):
            current = current / component
            _reject_link(current)
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise _InvalidKnowledge()
        before = _reject_link(candidate)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise _InvalidKnowledge()
        with candidate.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise _InvalidKnowledge()
            data = handle.read(limit + 1)
        # Check again before returning any bytes in case the file was replaced.
        after = _reject_link(candidate)
        if len(data) > limit or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise _InvalidKnowledge()
        for parent in candidate.parents:
            _reject_link(parent)
            if parent == root:
                break
        return data

    def _manifest(self) -> tuple[Path, list[_Document]]:
        root = self._root()
        payload = json.loads(self._read(root, "manifest.json", MAX_MANIFEST_BYTES).decode("utf-8-sig"),
                             object_pairs_hook=_json_pairs)
        if not isinstance(payload, dict) or set(payload) != {"documents"}:
            raise _InvalidKnowledge()
        entries = payload["documents"]
        if not isinstance(entries, list) or len(entries) > MAX_FILES:
            raise _InvalidKnowledge()
        documents = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"id", "title", "path", "users", "groups"}:
                raise _InvalidKnowledge()
            document = _Document(_plain_string(entry["id"]), _plain_string(entry["title"]),
                                 _relative_path(entry["path"]), _identifiers(entry["users"]),
                                 _identifiers(entry["groups"]))
            if not document.users and not document.groups:
                raise _InvalidKnowledge()
            documents.append(document)
        if (len({doc.identifier for doc in documents}) != len(documents)
                or len({doc.path.casefold() for doc in documents}) != len(documents)):
            raise _InvalidKnowledge()
        return root, documents

    @staticmethod
    def _authorized(documents: list[_Document], user_id: str, chat_id: str, chat_type: str) -> list[_Document]:
        if (not isinstance(user_id, str) or not user_id.strip()
                or not isinstance(chat_id, str) or not chat_id.strip()):
            return []
        return [doc for doc in documents if doc.allowed(user_id, chat_id, chat_type)]

    def list_sources(self, user_id: str, chat_id: str, chat_type: str) -> str:
        try:
            _, documents = self._manifest()
            documents = self._authorized(documents, user_id, chat_id, chat_type)
            if not documents:
                return NO_SOURCES
            return "Tài liệu bạn được phép tra cứu trong cuộc trò chuyện này:\n" + "\n".join(
                f"- {doc.title} ({doc.path})" for doc in documents)
        except (OSError, ValueError, TypeError, RecursionError):
            return UNAVAILABLE

    def answer(self, question: str, user_id: str, chat_id: str, chat_type: str) -> str:
        if not isinstance(question, str) or not question.strip():
            return "Bạn hãy nhập câu hỏi về tài liệu sau lệnh /hoi."
        if len(question) > MAX_QUESTION_CHARS:
            return f"Câu hỏi quá dài. Bạn hãy rút gọn còn tối đa {MAX_QUESTION_CHARS} ký tự."
        try:
            root, documents = self._manifest()
            documents = self._authorized(documents, user_id, chat_id, chat_type)
            if not documents:
                return NO_SOURCES
            query = _tokens(question)
            if len(query) < 2:
                return NO_MATCH
            passages = []
            total = 0
            for document in documents:
                data = self._read(root, document.path, MAX_FILE_BYTES)
                total += len(data)
                if total > MAX_TOTAL_BYTES:
                    raise _InvalidKnowledge()
                content = data.decode("utf-8-sig")
                if any(unicodedata.category(c) in {"Cc", "Cf"} and c not in "\r\n\t" for c in content):
                    raise _InvalidKnowledge()
                passages.extend(_passages(document, content))
                if len(passages) > MAX_PASSAGES:
                    raise _InvalidKnowledge()
            results = _retrieve(query, passages)
            if not results:
                return NO_MATCH
            rendered = ["Trích đoạn liên quan (nguyên văn tài liệu, chưa diễn giải bằng AI):"]
            for passage in results:
                source = f"Nguồn: {passage.document.title} — {passage.document.path}, dòng {passage.first_line}–{passage.last_line}"
                rendered.append("\n".join("> " + line for line in passage.text.splitlines()) + "\n" + source)
            return "\n\n".join(rendered)
        except (OSError, ValueError, TypeError, RecursionError):
            return UNAVAILABLE


def _passages(document: _Document, content: str) -> list[_Passage]:
    """Paragraphs retain exact text and original line numbers; headings aid rank."""
    result = []
    heading = ""
    lines: list[str] = []
    first = 0

    def flush():
        nonlocal lines
        if lines:
            result.append(_Passage(document, heading, first, first + len(lines) - 1, "\n".join(lines)))
            if len(result) > MAX_PASSAGES:
                raise _InvalidKnowledge()
            lines = []

    for number, line in enumerate(content.splitlines(), start=1):
        if re.match(r"^#{1,6}\s", line):
            flush()
            heading = re.sub(r"^#{1,6}\s+", "", line).strip()[:240]
        elif not line.strip():
            flush()
        elif len(line) > MAX_EXCERPT_CHARS:
            # Avoid silently truncating a sentence into a misleading statement.
            flush()
        else:
            if lines and sum(map(len, lines)) + len(lines) + len(line) > MAX_EXCERPT_CHARS:
                flush()
            if not lines:
                first = number
            lines.append(line)
    flush()
    return result


def _retrieve(query: frozenset[str], passages: list[_Passage]) -> list[_Passage]:
    if not passages:
        return []
    vocabulary = [_tokens(passage.heading + " " + passage.text) for passage in passages]
    frequency = {token: sum(token in words for words in vocabulary) for token in query}
    weights = {token: 1.0 + math.log((len(passages) + 1) / (frequency[token] + 1)) for token in query}
    maximum = sum(weights.values())
    ranked = []
    for index, (passage, words) in enumerate(zip(passages, vocabulary)):
        common = query & words
        # Missing query-specific words weigh more than ubiquitous document words.
        coverage = sum(weights[token] for token in common) / maximum
        if len(common) < 2 or coverage < 0.72 or len(common) / len(query) < 0.65:
            continue
        heading_overlap = len(query & _tokens(passage.heading)) / len(query)
        score = coverage + heading_overlap * 0.25
        ranked.append((score, index, passage))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if not ranked:
        return []
    # The score margin avoids filling the response with weaker unrelated matches.
    return [passage for score, _, passage in ranked if score >= ranked[0][0] - 0.08][:MAX_RESULTS]
