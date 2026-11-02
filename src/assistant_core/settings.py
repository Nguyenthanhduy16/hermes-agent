"""Core settings from environment variables; invalid values fail closed."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re


# Unsafe while Hermes stores them per home instead of per person:
# session_search reads every user's sessions (it filters by source only) and
# skill_manage writes the shared skill library without an owner-bound review.
DEFAULT_BLOCKED_TOOLS = ("session_search", "skill_manage", "memory")


RUN_LABEL_DROP = re.compile(r"[^\w .:+-]")


class SettingsError(ValueError):
    pass


def _int(values, name, default, maximum):
    raw = values.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise SettingsError(f"{name} cần là số nguyên từ 1 đến {maximum}.") from None
    if not 1 <= value <= maximum:
        raise SettingsError(f"{name} cần là số nguyên từ 1 đến {maximum}.")
    return value


def _names(raw, default):
    if raw is None:
        return frozenset(default)
    return frozenset(item.strip() for item in str(raw).split(",") if item.strip())


def run_label(raw) -> str:
    """ASSISTANT_RUN_LABEL names an experiment (B1, ablation-no-compress...).

    A label is metadata, so a bad value is cleaned (unsupported characters
    dropped, 60 characters max) instead of disabling quota and tool policy.
    """
    return RUN_LABEL_DROP.sub("", str(raw or "")).strip()[:60].strip()


@dataclass(frozen=True)
class CoreSettings:
    data_dir: Path
    user_daily_limit: int = 30
    total_daily_limit: int = 500
    blocked_tools: frozenset = frozenset(DEFAULT_BLOCKED_TOOLS)
    telemetry: bool = True
    run_label: str = ""

    @classmethod
    def from_mapping(cls, values: Mapping[str, str], *, default_dir: Path) -> "CoreSettings":
        data_dir = values.get("ASSISTANT_DATA_DIR") or ""
        telemetry = (values.get("ASSISTANT_TELEMETRY") or "true").strip().lower()
        if telemetry not in ("true", "false"):
            raise SettingsError("ASSISTANT_TELEMETRY cần là true hoặc false.")
        return cls(
            data_dir=Path(data_dir).resolve() if data_dir else default_dir,
            user_daily_limit=_int(values, "ASSISTANT_USER_DAILY_LIMIT", 30, 10000),
            total_daily_limit=_int(values, "ASSISTANT_TOTAL_DAILY_LIMIT", 500, 1000000),
            blocked_tools=_names(values.get("ASSISTANT_BLOCKED_TOOLS"), DEFAULT_BLOCKED_TOOLS),
            telemetry=telemetry == "true",
            run_label=run_label(values.get("ASSISTANT_RUN_LABEL")),
        )
