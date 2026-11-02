"""Configuration is fail-closed; secrets never appear in repr/errors."""

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
import os


class ConfigError(ValueError):
    pass


def load_env(path: Path) -> None:
    """Load literal KEY=value pairs without shell evaluation or interpolation."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.replace("_", "").isalnum():
            raise ConfigError("Cau hinh .env khong hop le (can KEY=value).")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def id_set(value: str) -> frozenset[str]:
    result = frozenset(x.strip() for x in value.split(",") if x.strip())
    if "*" in result:
        raise ConfigError("Khong cho phep wildcard * trong danh sach cap quyen.")
    return result


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    allowed_users: frozenset[str]
    allowed_groups: frozenset[str] = frozenset()
    state_dir: Path = Path("data")
    poll_timeout: int = 30
    max_inbound_chars: int = 8000
    max_reply_chars: int = 12000
    knowledge_dir: Path = field(default_factory=lambda: Path("data/knowledge").resolve())
    group_access: str = "allowlist"
    # lab: work commands only (phase 1). assistant: commands stay local and
    # every other admitted message runs the full Hermes agent loop.
    bot_mode: str = "lab"

    @property
    def assistant(self) -> bool:
        return self.bot_mode == "assistant"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "Settings":
        token = values.get("ZALO_BOT_TOKEN", "").strip()
        if not token or token.startswith("YOUR_"):
            raise ConfigError("Chua dien ZALO_BOT_TOKEN trong .env.")
        if any(c.isspace() for c in token) or any(c in token for c in "/?#\\"):
            raise ConfigError("ZALO_BOT_TOKEN sai dinh dang.")
        users = id_set(values.get("ZALO_ALLOWED_USERS", ""))
        if not users:
            raise ConfigError("Can it nhat mot ID trong ZALO_ALLOWED_USERS.")
        group_access = values.get("ZALO_GROUP_ACCESS") or "allowlist"
        if group_access not in ("allowlist", "members"):
            raise ConfigError("ZALO_GROUP_ACCESS can la allowlist hoac members.")
        bot_mode = (values.get("ZALO_BOT_MODE") or "lab").strip().lower()
        if bot_mode not in ("lab", "assistant"):
            raise ConfigError("ZALO_BOT_MODE can la lab hoac assistant.")
        return cls(
            token=token,
            allowed_users=users,
            allowed_groups=id_set(values.get("ZALO_ALLOWED_GROUPS", "")),
            state_dir=Path(values.get("ZALO_STATE_DIR") or "data").resolve(),
            knowledge_dir=Path(values.get("ZALO_KNOWLEDGE_DIR") or "data/knowledge").resolve(),
            group_access=group_access,
            bot_mode=bot_mode,
        )
