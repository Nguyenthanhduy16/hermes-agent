"""Install this repo's plugin into its dedicated project-local Hermes home."""
from pathlib import Path
import shutil
import sys

import yaml

ROOT = Path(__file__).resolve().parent.parent


def configure() -> None:
    hermes_home = ROOT / ".runtime" / "hermes-home"
    plugin_dir = hermes_home / "plugins" / "zalo-platform"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for source in (ROOT / "plugin").iterdir():
        if source.is_file():
            shutil.copy2(source, plugin_dir / source.name)
    shutil.copytree(ROOT / "src" / "hermes_zalo", plugin_dir / "hermes_zalo",
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copy2(ROOT / ".env.example", env_file)
    config_file = hermes_home / "config.yaml"
    config = yaml.safe_load(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
    config = config or {}
    if not isinstance(config, dict):
        raise ValueError("Hermes config must be a mapping.")
    if config_file.exists():
        # Back up inside ignored runtime, preserving model credentials if any.
        shutil.copy2(config_file, hermes_home / "config.before-zalo.yaml")
    safety = yaml.safe_load((ROOT / "config" / "hermes.config.example.yaml").read_text(encoding="utf-8"))
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
    config["gateway"]["platforms"] = {"zalo": {"enabled": True}}
    config["plugins"]["enabled"] = ["zalo-platform"]
    temporary = config_file.with_suffix(".tmp")
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(config_file)
    print("Đã cập nhật plugin hội thoại và lệnh công việc trong .runtime/hermes-home.")
    print("Điền token trong .env rồi chạy ZaloCheck và Discover; API key nhập qua Model.")
    print("Khởi động lại gateway sau mỗi lần Setup/Update để nhận code mới.")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    configure()
