"""Core settings from environment variables; invalid values fail closed."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re


# Unsafe while Hermes stores them per home instead of per person:
# session_search reads every user's sessions (it filters by source only) and
# skill_manage writes the shared skill library without an owner-bound review.
DEFAULT_BLOCKED_TOOLS = ("session_search", "skill_manage", "memory")


RUN_LABEL_DROP = re.compile(r"[^\w .:+-]")
ROUTER_POLICIES = ("off", "always-strong", "always-cheap", "random", "rule")
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}")
ROUTER_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
REQUEST_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")
# Request fields a tier may never set: they pick the model, carry the conversation or change the wire shape.
REQUEST_KEYS_FIXED = frozenset({"model", "messages", "input", "instructions", "system", "tools", "stream"})


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


def _fraction(values, name, default):
    raw = values.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(str(raw).strip())
    except ValueError:
        value = math.nan
    if not 0.0 <= value <= 1.0:  # also false for NaN
        raise SettingsError(f"{name} cần là số từ 0 đến 1.")
    return value


def _request_overrides(values, name) -> tuple:
    """A tier's extra request fields: a small JSON object, e.g. {"reasoning": {"effort": "low"}}.

    ``null`` removes a field Hermes prepared for the default model.
    """
    raw = (values.get(name) or "").strip()
    if not raw:
        return ()
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if (not isinstance(data, dict) or len(data) > 8 or len(raw) > 2000
            or not all(isinstance(key, str) and REQUEST_KEY.fullmatch(key) and key not in REQUEST_KEYS_FIXED
                       for key in data)):
        raise SettingsError(f"{name} cần là một JSON object nhỏ (tối đa 8 trường, 2000 ký tự) và không được đặt "
                            f"{', '.join(sorted(REQUEST_KEYS_FIXED))}.")
    return tuple(sorted(data.items()))


@dataclass(frozen=True)
class RouterSettings:
    """Per-step model routing (assistant_core.router). ``off`` leaves every request untouched."""
    policy: str = "off"
    strong_model: str = ""
    cheap_model: str = ""
    provider: str = ""             # when set, only requests of this Hermes provider are routed
    cheap_provider: str = ""       # cheap tier on another Hermes provider: switch the agent's runtime per step
    platforms: frozenset = frozenset()  # empty = every platform
    strong_share: float = 0.5      # random: probability of the strong model at each step
    seed: int = 0                  # random: same seed and step ids give the same draws
    strong_first_steps: int = 1    # rule: this many first steps of a turn use the strong model
    strong_request: tuple = ()     # extra request fields per tier, as sorted (key, value) pairs
    cheap_request: tuple = ()

    @property
    def enabled(self) -> bool:
        return self.policy != "off"

    def describe(self) -> dict:
        """Secret-free summary stored with the run."""
        return dict(policy=self.policy, strong_model=self.strong_model, cheap_model=self.cheap_model,
                    provider=self.provider, cheap_provider=self.cheap_provider, platforms=sorted(self.platforms), strong_share=self.strong_share,
                    seed=self.seed, strong_first_steps=self.strong_first_steps,
                    strong_request_keys=[key for key, _ in self.strong_request],
                    cheap_request_keys=[key for key, _ in self.cheap_request])

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "RouterSettings":
        policy = (values.get("ASSISTANT_ROUTER_POLICY") or "off").strip().lower()
        if not ROUTER_NAME.fullmatch(policy):
            raise SettingsError("ASSISTANT_ROUTER_POLICY cần là một trong: " + ", ".join(ROUTER_POLICIES) + ".")
        if policy == "off":
            return cls()
        models = {}
        for tier in ("STRONG", "CHEAP"):
            name = (values.get(f"ASSISTANT_ROUTER_{tier}_MODEL") or "").strip()
            if not MODEL_NAME.fullmatch(name):
                raise SettingsError(f"ASSISTANT_ROUTER_{tier}_MODEL cần là tên mô hình khi bật định tuyến.")
            models[tier] = name
        provider = (values.get("ASSISTANT_ROUTER_PROVIDER") or "").strip()
        if provider and not MODEL_NAME.fullmatch(provider):
            raise SettingsError("ASSISTANT_ROUTER_PROVIDER không hợp lệ.")
        cheap_provider = (values.get("ASSISTANT_ROUTER_CHEAP_PROVIDER") or "").strip().lower()
        if cheap_provider and not MODEL_NAME.fullmatch(cheap_provider):
            raise SettingsError("ASSISTANT_ROUTER_CHEAP_PROVIDER không hợp lệ.")
        seed_raw = (values.get("ASSISTANT_ROUTER_SEED") or "0").strip()
        if not re.fullmatch(r"[0-9]{1,9}", seed_raw):
            raise SettingsError("ASSISTANT_ROUTER_SEED cần là số nguyên không âm.")
        first_raw = (values.get("ASSISTANT_ROUTER_STRONG_FIRST_STEPS") or "1").strip()
        if not re.fullmatch(r"[0-9]{1,2}", first_raw):
            raise SettingsError("ASSISTANT_ROUTER_STRONG_FIRST_STEPS cần là số nguyên từ 0 đến 99.")
        return cls(policy=policy, strong_model=models["STRONG"], cheap_model=models["CHEAP"], provider=provider,
                   cheap_provider=cheap_provider,
                   platforms=_names(values.get("ASSISTANT_ROUTER_PLATFORMS"), ()),
                   strong_share=_fraction(values, "ASSISTANT_ROUTER_STRONG_SHARE", 0.5),
                   seed=int(seed_raw), strong_first_steps=int(first_raw),
                   strong_request=_request_overrides(values, "ASSISTANT_ROUTER_STRONG_REQUEST"),
                   cheap_request=_request_overrides(values, "ASSISTANT_ROUTER_CHEAP_REQUEST"))


@dataclass(frozen=True)
class CoreSettings:
    data_dir: Path
    user_daily_limit: int = 30
    total_daily_limit: int = 500
    blocked_tools: frozenset = frozenset(DEFAULT_BLOCKED_TOOLS)
    telemetry: bool = True
    run_label: str = ""
    router: RouterSettings = field(default_factory=RouterSettings)

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
            router=RouterSettings.from_mapping(values),
        )
