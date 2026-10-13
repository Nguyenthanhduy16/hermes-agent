"""Linux container bootstrap. No network calls until the operator activates it."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import threading

import yaml

from .config import ConfigError, Settings, id_set
from .sheets import SheetsError, SheetsSettings
from .tickets.providers import TicketsSheetsSettings, TicketsSourceError
from .tickets.lab_provider import LabSheetsSettings
from .workload import WorkloadError, WorkloadSettings


class ContainerError(ValueError):
    """Messages contain setting names, never their values or credential text."""


def flag(values, name, default="false"):
    value = (values.get(name) or default).strip().lower()
    if value not in ("true", "false"):
        raise ContainerError(f"{name} cần là true hoặc false.")
    return value == "true"


def paths(values):
    root = Path(values.get("BOT_DATA_DIR") or "/data")
    if not root.is_absolute():
        raise ContainerError("BOT_DATA_DIR cần là đường dẫn tuyệt đối của volume.")
    root = root.resolve()
    if values.get("RAILWAY_ENVIRONMENT_ID") or values.get("RAILWAY_SERVICE_ID"):
        mount = values.get("RAILWAY_VOLUME_MOUNT_PATH")
        if not mount or Path(mount).resolve() != root:
            raise ContainerError("Railway cần gắn Volume tại BOT_DATA_DIR trước khi chạy bot.")
    defaults = {
        "HERMES_HOME": root / "hermes-home",
        "ZALO_STATE_DIR": root / "hermes-home" / "data" / "zalo",
        "TICKETS_DATA_DIR": root / "tickets",
        "ZALO_KNOWLEDGE_DIR": root / "knowledge",
        "ZALO_GOOGLE_CREDENTIALS": root / "secrets" / "google-service-account.json",
        "TICKETS_TEST_GOOGLE_CREDENTIALS": root / "secrets" / "tickets-test-service-account.json",
    }
    for key, default in defaults.items():
        path = Path(values.get(key) or default)
        if not path.is_absolute() or path.resolve() == root or not path.resolve().is_relative_to(root):
            raise ContainerError(f"{key} phải là đường dẫn tuyệt đối nằm trong BOT_DATA_DIR; không dùng đường dẫn Windows.")
        values[key] = str(path.resolve())
    values["BOT_DATA_DIR"] = str(root)
    return root


def atomic_private(path, content):
    """Create private files before writing their contents and replace atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ContainerError("Không ghi cấu hình hoặc khóa vào liên kết tượng trưng.")
    fd, temporary = tempfile.mkstemp(prefix=".bootstrap-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def require_plain_path(path, root):
    """Reject links at every plugin component before copying image-owned code."""
    if not path.resolve().is_relative_to(root.resolve()):
        raise ContainerError("Thư mục plugin không hợp lệ.")
    for item in (path, *path.parents):
        if item == root:
            break
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ContainerError("Thư mục plugin không được là liên kết.")


def credentials(values):
    for variable, destination in (
        ("ZALO_GOOGLE_CREDENTIALS_SECRET_JSON", "ZALO_GOOGLE_CREDENTIALS"),
        ("TICKETS_TEST_GOOGLE_CREDENTIALS_SECRET_JSON", "TICKETS_TEST_GOOGLE_CREDENTIALS"),
    ):
        raw = values.get(variable)
        if not raw:
            continue
        try:
            if len(raw.encode("utf-8")) > 32768:
                raise ValueError()
            info = json.loads(raw)
            if (not isinstance(info, dict) or info.get("type") != "service_account"
                    or info.get("token_uri") != "https://oauth2.googleapis.com/token"
                    or any(not isinstance(info.get(k), str) or not info[k].strip()
                           for k in ("client_email", "private_key", "project_id"))):
                raise ValueError()
        except (ValueError, TypeError):
            raise ContainerError(f"{variable} cần là nội dung JSON service account hợp lệ.") from None
        atomic_private(Path(values[destination]), json.dumps(info, ensure_ascii=False))


def prepare(values, app_root=None):
    """Install only image-owned code; preserve persisted user data and model choice."""
    root = paths(values)
    app = Path(app_root or "/app").resolve()
    home = Path(values["HERMES_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    if (home / ".env").exists():
        raise ContainerError("Không dùng .env cũ trong HERMES_HOME; chuyển cấu hình và API key sang Variables của container.")
    config_path = home / "config.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        safety = yaml.safe_load((app / "config" / "hermes.config.example.yaml").read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeError):
        raise ContainerError("Cấu hình Hermes không phải YAML hợp lệ.") from None
    config = config or {}
    if not isinstance(config, dict):
        raise ContainerError("Cấu hình Hermes cần là một mapping.")
    for key, value in safety.items():
        if isinstance(value, dict):
            previous = config.get(key)
            config[key] = {**(previous if isinstance(previous, dict) else {}), **value}
        else:
            config[key] = value
    config.update(mcp_servers={}, group_sessions_per_user=True, thread_sessions_per_user=True,
                  multiplex_profiles=False, profile_routes=[])
    config["gateway"].update(profile_routes=[], platforms={"zalo": {"enabled": True}})
    config["plugins"]["enabled"] = ["zalo-platform"]
    config["platform_toolsets"] = {"zalo": [], "cli": []}
    config["security"] = {**(config.get("security") if isinstance(config.get("security"), dict) else {}),
                          "allow_lazy_installs": False}
    model = config.get("model")
    model = dict(model) if isinstance(model, dict) else {}
    provider = values.get("HERMES_PROVIDER", "").strip()
    if provider and provider != model.get("provider"):
        # Provider-specific routing and credentials must not cross providers.
        for key in ("base_url", "api_key", "api_mode"):
            model.pop(key, None)
    for variable, key in (("HERMES_PROVIDER", "provider"), ("HERMES_MODEL", "default"),
                          ("HERMES_BASE_URL", "base_url")):
        if values.get(variable, "").strip():
            model[key] = values[variable].strip()
    if not all(isinstance(model.get(k), str) and model[k].strip() for k in ("provider", "default")):
        raise ContainerError("Cần cấu hình HERMES_PROVIDER và HERMES_MODEL cho lần chạy đầu.")
    config["model"] = model
    credentials(values)
    plugin = home / "plugins" / "zalo-platform"
    require_plain_path(plugin, home)
    for name in ("__init__.py", "plugin.yaml", "hermes_zalo"):
        require_plain_path(plugin / name, home)
    plugin.mkdir(parents=True, exist_ok=True)
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copy2(app / "plugin" / name, plugin / name)
    package = plugin / "hermes_zalo"
    # Only replace the dedicated code subtree. User state is outside plugins.
    if package.exists():
        shutil.rmtree(package)
    shutil.copytree(Path(__file__).resolve().parent, package,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    atomic_private(config_path, yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    for key in ("ZALO_STATE_DIR", "TICKETS_DATA_DIR", "ZALO_KNOWLEDGE_DIR"):
        Path(values[key]).mkdir(parents=True, exist_ok=True)
    values.update(TICKETS_PROJECT_ROOT=str(app), GATEWAY_MULTIPLEX_PROFILES="false",
                  HERMES_GATEWAY_NO_SUPERVISE="1", HERMES_GATEWAY_EXTERNAL_SUPERVISOR="1",
                  HERMES_DASHBOARD="0", API_SERVER_ENABLED="false",
                  PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return root, config


def validate(values, config):
    """Validate local configuration only; no Zalo, Google, model or DB writes."""
    from .nlu import options
    from .admin_config import AdminConfig
    try:
        options(values)
        AdminConfig.from_mapping(values)
    except ValueError as error:
        raise ContainerError(str(error)) from None
    settings = Settings.from_mapping(values)
    sheets = SheetsSettings.from_mapping(values)
    workload = WorkloadSettings.from_mapping(values, sheets)
    if workload is not None:
        flag(values, "REGISTRATION_AUTO_APPROVE")
        admins = id_set(values.get("REGISTRATION_ADMIN_USERS", ""))
        if not admins or not admins <= settings.allowed_users:
            raise ContainerError("REGISTRATION_ADMIN_USERS cần thuộc ZALO_ALLOWED_USERS.")
    keys = [sheets.credentials_file] if sheets else []
    mode = (values.get("TICKETS_CHAT_MODE") or "off").strip().lower()
    if mode not in ("off", "sheets_test", "lab_live", "lab_live_test"):
        raise ContainerError("TICKETS_CHAT_MODE chưa hợp lệ.")
    if mode != "off":
        if mode.startswith("lab_live"):
            source = LabSheetsSettings.from_mapping(values, test_mode=mode == "lab_live_test")
        else:
            source = TicketsSheetsSettings.from_mapping(values)
        if source is None:
            raise ContainerError("Thiếu cấu hình nguồn lịch cho Tickets.")
        keys.append(source.credentials_file)
        manager = flag(values, "TICKETS_REQUIRE_MANAGER", "false" if mode.startswith("lab_live") else "true")
        if mode == "lab_live_test" and manager:
            raise ContainerError("lab_live_test yêu cầu TICKETS_REQUIRE_MANAGER=false.")
    if any(not path.is_file() for path in keys):
        raise ContainerError("Thiếu file khóa Google trên volume hoặc Variables JSON tương ứng.")
    key_name = {"openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY"}.get(config["model"]["provider"])
    if key_name and not values.get(key_name, "").strip():
        raise ContainerError(f"Thiếu {key_name} trong Variables của container.")


def standby():
    stopped = threading.Event()
    previous = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, lambda *_: stopped.set())
    print("STANDBY: chưa nhận tin Zalo. Chuyển dữ liệu, dừng bot Windows rồi đặt BOT_START_ENABLED=true.", flush=True)
    try:
        stopped.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args not in (["gateway", "run"], ["check"], ["demo"], []):
        print("Chỉ hỗ trợ gateway run, check hoặc demo.", file=sys.stderr)
        return 2
    if args == ["demo"]:
        from .cli import demo
        demo()
        return 0
    try:
        values = os.environ.copy()
        paths(values)
        if args in ([], ["gateway", "run"]) and not flag(values, "BOT_START_ENABLED"):
            return standby()
        root, config = prepare(values)
        validate(values, config)
        if args == ["check"]:
            print("Đạt kiểm tra cấu hình container cục bộ; chưa gọi Zalo, Google hoặc model.")
            return 0
        launcher = values.get("HERMES_EXECUTABLE") or "/usr/local/bin/hermes"
        if not Path(launcher).is_file():
            raise ContainerError("Không tìm thấy launcher Hermes trong image.")
        print("Bắt đầu Zalo gateway; dữ liệu được lưu trên volume.", flush=True)
        # Hermes receives SIGTERM directly and closes polling, timers and DBs.
        os.execve(launcher, [launcher, "gateway", "run"], values)
        return 0
    except (ContainerError, ConfigError, SheetsError, WorkloadError, TicketsSourceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError:
        print("Không đọc/ghi hoặc khởi chạy được container; kiểm tra volume và quyền truy cập.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
