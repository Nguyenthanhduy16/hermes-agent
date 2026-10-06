"""Normalize only the documented Zalo event shape, never Telegram offsets."""

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class IncomingMessage:
    chat_id: str
    user_id: str
    message_id: str
    chat_type: str
    text: str

    @property
    def key(self) -> str:
        return sha256(f"{self.chat_type}\0{self.chat_id}\0{self.message_id}".encode()).hexdigest()

    @property
    def session_key(self) -> str:
        return f"zalo:{self.chat_type}:{self.chat_id}:{self.user_id}"


def _id(value) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value).strip()
    return value if value and not any(c in value for c in (":", "\0", "\r", "\n")) else None


def parse_update(update: dict) -> IncomingMessage | None:
    if not isinstance(update, dict):
        return None
    if update.get("event_name") != "message.text.received":
        return None
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    sender, chat = message.get("from"), message.get("chat")
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return None
    if sender.get("is_bot") is not False:
        return None
    chat_type = chat.get("chat_type")
    if chat_type not in ("PRIVATE", "GROUP"):
        return None
    chat_id, user_id, message_id = _id(chat.get("id")), _id(sender.get("id")), _id(message.get("message_id"))
    text = message.get("text")
    if not all((chat_id, user_id, message_id)) or not isinstance(text, str) or not text.strip():
        return None
    return IncomingMessage(chat_id, user_id, message_id, chat_type, text)


def is_allowed(message: IncomingMessage, users: frozenset[str], groups: frozenset[str], *, group_members=False) -> bool:
    if message.chat_type == "GROUP" and group_members and message.chat_id in groups:
        return True
    return message.user_id in users and (
        message.chat_type == "PRIVATE" or message.chat_id in groups
    )


def split_text(text: str, limit: int = 2000) -> list[str]:
    """Stay within both Unicode code point and UTF-16 unit limits (emoji safe)."""
    if limit < 2:
        raise ValueError("limit must be >= 2")
    chunks, current, size = [], [], 0
    for char in text:
        units = 2 if ord(char) > 0xFFFF else 1
        if size + units > limit:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += units
    if current:
        chunks.append("".join(current))
    return chunks
