"""Install this repo's plugin into its dedicated project-local Hermes home."""
from pathlib import Path
import shutil
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent


def env_value(env_file: Path, name: str) -> str:
    """A variable from the process environment, else its literal .env value."""
    import os
    value = os.environ.get(name, "")
    if not value and env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, sep, raw = line.strip().partition("=")
            if sep and key.strip() == name:
                value = raw.strip().strip("\"'")
    return value.strip()


def bot_mode(env_file: Path) -> str:
    return env_value(env_file, "ZALO_BOT_MODE").lower() or "lab"


def install_plugin(hermes_home: Path, name: str, wrapper: Path, package: str) -> None:
    """Copy a plugin wrapper and its source package into the dedicated home."""
    plugin_dir = hermes_home / "plugins" / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for source in wrapper.iterdir():
        if source.is_file():
            shutil.copy2(source, plugin_dir / source.name)
    shutil.copytree(ROOT / "src" / package, plugin_dir / package,
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def configure() -> None:
    hermes_home = ROOT / ".runtime" / "hermes-home"
    install_plugin(hermes_home, "zalo-platform", ROOT / "plugin", "hermes_zalo")
    install_plugin(hermes_home, "assistant-core", ROOT / "plugins" / "assistant-core", "assistant_core")
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copy2(ROOT / ".env.example", env_file)
    # A channel is enabled when its token is present; Zalo stays the default
    # when nothing is configured yet, so the first Setup keeps working.
    telegram = bool(env_value(env_file, "TELEGRAM_BOT_TOKEN"))
    zalo = bool(env_value(env_file, "ZALO_BOT_TOKEN")) or not telegram
    assistant = telegram or bot_mode(env_file) == "assistant"
    config_file = hermes_home / "config.yaml"
    config = yaml.safe_load(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
    config = config or {}
    if not isinstance(config, dict):
        raise ValueError("Hermes config must be a mapping.")
    if config_file.exists():
        # Back up inside ignored runtime, preserving model credentials if any.
        shutil.copy2(config_file, hermes_home / "config.before-zalo.yaml")
    overlays = ["hermes.config.example.yaml"]
    if assistant:
        overlays.append("hermes.assistant.example.yaml")
    for name in overlays:
        safety = yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))
        for key, value in safety.items():
            if isinstance(value, dict):
                if not isinstance(config.get(key), dict):
                    config[key] = {}
                config[key].update(value)
            else:
                config[key] = value
    # This is a dedicated bot instance: no cross-profile routing or inherited
    # MCP servers. Work commands run in adapter code, not model-selected tools.
    config["mcp_servers"] = {}
    config["group_sessions_per_user"] = True
    config["thread_sessions_per_user"] = True
    config["multiplex_profiles"] = False
    config["profile_routes"] = []
    config["gateway"]["profile_routes"] = []
    config["gateway"]["platforms"] = {name: {"enabled": True}
                                      for name, on in (("zalo", zalo), ("telegram", telegram)) if on}
    # assistant-core governs every channel that reaches the agent loop.
    config["plugins"]["enabled"] = (["zalo-platform"] if zalo else []) + (["assistant-core"] if assistant else [])
    temporary = config_file.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(config_file)
    channels = ", ".join(name for name, on in (("Zalo " + bot_mode(env_file), zalo), ("Telegram", telegram)) if on)
    print(f"Đã cập nhật .runtime/hermes-home: kênh {channels}; assistant-core {'bật' if assistant else 'tắt'}.")
    print("Điền token trong .env (ZALO_BOT_TOKEN / TELEGRAM_BOT_TOKEN); API key nhập qua Model.")
    print("Khởi động lại gateway sau mỗi lần Setup/Update để nhận code mới.")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    configure()
