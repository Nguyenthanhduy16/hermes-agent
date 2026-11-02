"""A re-run preserves credentials while restoring the isolated chat-only policy."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent


class SetupTests(unittest.TestCase):
    def setUp(self):
        # configure.py prefers process variables over .env. These tests describe
        # the .env alone, so channel/mode variables set elsewhere must not apply.
        isolated = {key: value for key, value in os.environ.items()
                    if not key.startswith(("ZALO_", "TELEGRAM_", "HERMES_"))}
        environment = patch.dict(os.environ, isolated, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_setup_preserves_provider_and_env_and_disables_tools(self):
        spec = importlib.util.spec_from_file_location("project_configure", ROOT / "scripts" / "configure.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            task_root = Path(directory)
            for folder in ("plugin", "plugins", "config", "src"):
                shutil.copytree(ROOT / folder, task_root / folder)
            shutil.copy2(ROOT / ".env.example", task_root / ".env.example")
            (task_root / ".env").write_text("ZALO_BOT_TOKEN=local-placeholder\n", encoding="utf-8")
            hermes_home = task_root / ".runtime" / "hermes-home"
            hermes_home.mkdir(parents=True)
            config_file = hermes_home / "config.yaml"
            config_file.write_text(yaml.safe_dump({
                "model": {"provider": "example", "default": "demo-model"},
                "platform_toolsets": {"zalo": ["terminal"]},
                "memory": {"memory_enabled": True},
                "mcp_servers": {"example": {"command": "unused"}},
            }), encoding="utf-8")
            module.ROOT = task_root
            with contextlib.redirect_stdout(io.StringIO()):
                module.configure()
                module.configure()
            config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            self.assertEqual("demo-model", config["model"]["default"])
            self.assertEqual([], config["platform_toolsets"]["zalo"])
            self.assertEqual({}, config["mcp_servers"])
            self.assertFalse(config["memory"]["memory_enabled"])
            self.assertFalse(config["memory"]["user_profile_enabled"])
            self.assertEqual(["zalo-platform"], config["plugins"]["enabled"])
            self.assertFalse(config["multiplex_profiles"])
            self.assertEqual("ZALO_BOT_TOKEN=local-placeholder\n", (task_root / ".env").read_text(encoding="utf-8"))
            self.assertTrue((hermes_home / "plugins" / "zalo-platform" / "hermes_zalo" / "adapter.py").is_file())
            self.assertTrue((hermes_home / "plugins" / "assistant-core" / "assistant_core" / "quota.py").is_file())

    def test_telegram_token_enables_channel_core_and_assistant_toolsets(self):
        spec = importlib.util.spec_from_file_location("project_configure_tg", ROOT / "scripts" / "configure.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            task_root = Path(directory)
            for folder in ("plugin", "plugins", "config", "src"):
                shutil.copytree(ROOT / folder, task_root / folder)
            shutil.copy2(ROOT / ".env.example", task_root / ".env.example")
            (task_root / ".env").write_text("TELEGRAM_BOT_TOKEN=local-placeholder\n", encoding="utf-8")
            module.ROOT = task_root
            with contextlib.redirect_stdout(io.StringIO()):
                module.configure()
            config = yaml.safe_load((task_root / ".runtime" / "hermes-home" / "config.yaml").read_text(encoding="utf-8"))
            self.assertEqual(["assistant-core"], config["plugins"]["enabled"])
            self.assertEqual({"telegram": {"enabled": True}}, config["gateway"]["platforms"])
            self.assertNotIn("session_search", config["platform_toolsets"]["telegram"])
            self.assertIn("code_execution", config["platform_toolsets"]["telegram"])
            self.assertEqual("docker", config["terminal"]["backend"])
            self.assertFalse(config["memory"]["memory_enabled"])


if __name__ == "__main__":
    unittest.main()
