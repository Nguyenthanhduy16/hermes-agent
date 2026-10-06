"""A re-run preserves credentials while restoring the isolated chat-only policy."""
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parent.parent


class SetupTests(unittest.TestCase):
    def test_setup_preserves_provider_and_env_and_disables_tools(self):
        spec = importlib.util.spec_from_file_location("project_configure", ROOT / "scripts" / "configure.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            task_root = Path(directory)
            for folder in ("plugin", "config", "src"):
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


if __name__ == "__main__":
    unittest.main()
