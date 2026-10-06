"""Remove a leading mention of this bot, retaining the user's original payload."""

import re
import unicodedata


_SPACE = r"[\s\u200b\ufeff]"


def strip_bot_mention(text: str, names: tuple[str, ...]) -> str:
    """Match only names confirmed by getMe, never arbitrary @names or mid-text.

    Zalo's account_name can be an internal handle while display_name is the
    visible group mention. Separators and Unicode composition may differ in
    copied text. Normalization is used for matching only, not for the payload.
    """
    normalized = unicodedata.normalize("NFC", text)
    candidates = {
        unicodedata.normalize("NFC", name).strip()
        for name in names if isinstance(name, str) and name.strip()
    }
    for name in sorted(candidates, key=len, reverse=True):
        words = re.split(_SPACE + "+", name)
        pattern = (
            r"^" + _SPACE + "*@"
            + (_SPACE + "+").join(re.escape(word) for word in words)
            + r"(?=" + _SPACE + r"|[:,]|$)[\s\u200b\ufeff:,]*"
        )
        match = re.match(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        # Map the normalized prefix back to the original string so accents,
        # newlines and spacing inside the command/question stay untouched.
        remaining = len(unicodedata.normalize("NFD", normalized[:match.end()]))
        for index, char in enumerate(text):
            remaining -= len(unicodedata.normalize("NFD", char))
            if remaining == 0:
                return text[index + 1:] or "/menu"
    return text
