"""Launch the isolated Hermes instance or local tickets preview; hide secrets."""
import os
from pathlib import Path
import subprocess
import sys

from hermes_zalo.config import ConfigError, Settings, load_env

ROOT = Path(__file__).resolve().parent.parent


def main():
    load_env(ROOT / ".env")
    if sys.argv[1:] in (["tickets", "demo"], ["tickets", "sheets_test"]):
        return subprocess.call([sys.executable, "-m", "hermes_zalo.tickets.cli", "serve",
                                "--mode", sys.argv[2], "--port", "8765"], cwd=ROOT)
    hermes_home = ROOT / ".runtime" / "hermes-home"
    # A UTF-8 .cmd wrapper loses accented paths on legacy Windows code pages.
    # Native launchers pass Unicode paths directly to Windows process creation.
    launchers = [hermes_home / "bin" / "hermes.exe",
                 ROOT / ".runtime" / "hermes-agent" / ".hermes" / "bin" / "hermes.exe"]
    launcher = next((path for path in launchers if path.is_file()), launchers[0])
    if not launcher.is_file():
        print("Thiếu launcher Hermes native (hermes.exe). Chạy scripts/install-hermes.ps1 để hoàn tất hoặc sửa cài đặt.")
        return 2
    if sys.argv[1:] not in (["model"], ["gateway", "run"]):
        print("Chỉ hỗ trợ model, gateway run hoặc tickets demo|sheets_test.")
        return 2
    if sys.argv[1] == "gateway":
        Settings.from_env()
    child_env = os.environ.copy()
    child_env["HERMES_HOME"] = str(hermes_home)
    child_env["ZALO_STATE_DIR"] = str(hermes_home / "data" / "zalo")
    child_env["GATEWAY_MULTIPLEX_PROFILES"] = "false"
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["TICKETS_PROJECT_ROOT"] = str(ROOT)
    # Keep this bot on its dedicated home and project working directory.
    return subprocess.call([str(launcher), *sys.argv[1:]], cwd=ROOT, env=child_env)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigError as exc:
        print(str(exc))
        raise SystemExit(2)
    except KeyboardInterrupt:
        raise SystemExit(130)
