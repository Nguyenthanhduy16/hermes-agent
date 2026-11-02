import json
import os
from pathlib import Path
import tempfile
import unittest

import yaml

from hermes_zalo.admin_auth import AdminError
from hermes_zalo.admin_connections import SECTIONS, ConnectionsAdmin, _mcp_token_var
from hermes_zalo.admin_control_store import ControlStore


SECRET = "sk-live-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
FIELD_TYPES = {"text", "textarea", "password", "number", "checkbox", "select", "hidden"}

CONFIG = {
    "agent": {"api_max_retries": 3, "reasoning_effort": "medium"},
    "model": {"streaming": True, "provider": "custom", "default": "vendor/main",
              "base_url": "https://llm.example/v1?key=" + SECRET, "api_key": SECRET},
    "auxiliary": {"vision": {"provider": "auto", "model": "", "timeout": 99}},
    "platform_toolsets": {"zalo": ["web", "vision"], "telegram": ["web", "vision"], "cli": []},
    "gateway": {"multiplex_profiles": False, "platforms": {"telegram": {"extra": {"rich_messages": True}}}},
    "memory": {"memory_enabled": False, "user_profile_enabled": False},
    "plugins": {"enabled": ["zalo-platform", "assistant-core"]},
    "terminal": {"backend": "docker", "timeout": 180, "docker_image": "private/image"},
    "mcp_servers": {"local-tool": {"command": "/private/bin/tool", "args": [SECRET], "env": {"K": SECRET},
                                   "enabled": False}},
    "unrelated": {"keep": ["me", 1]},
}
ENV = "# operator comment\nOPENROUTER_API_KEY=" + SECRET + "\nUNRELATED_SETTING=keep me\n"


class ConnectionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "bot-home"
        self.home.mkdir()
        self.write_config(CONFIG)
        (self.home / ".env").write_text(ENV, encoding="utf-8")
        self.environ = {}
        self.os_environ = dict(os.environ)

    def tearDown(self):
        self.assertEqual(self.os_environ, dict(os.environ), "process environment must never change")

    def write_config(self, value):
        (self.home / "config.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")

    def config(self):
        return yaml.safe_load((self.home / "config.yaml").read_text(encoding="utf-8"))

    def admin(self, **kw):
        return ConnectionsAdmin(ControlStore(self.home), environ=self.environ, **kw)

    def descriptor(self, section, action, target=None):
        for item in self.admin().snapshot(section)["actions"]:
            if item["id"] == action and item.get("target") == target:
                return item
        self.fail(f"no {action} descriptor for {target}")

    def run_action(self, section, action, values, target=None, authorize=None, revision=None):
        descriptor = self.descriptor(section, action, target)
        return self.admin().action(section, {"action": action, "confirm": True, "values": values,
                                             "expected_revision": revision or descriptor["revision"]},
                                   authorize=authorize)

    # ---- contract -----------------------------------------------------------

    def test_snapshots_follow_schema_and_never_leak_credentials(self):
        self.environ = {"TELEGRAM_BOT_TOKEN": SECRET + "x"}
        before = {p.name: p.read_bytes() for p in self.home.iterdir()}
        for section in SECTIONS:
            with self.subTest(section=section):
                data = self.admin().snapshot(section)
                self.assertLessEqual({"section", "available", "source", "message", "updated_at", "items", "actions"},
                                     set(data))
                self.assertTrue(data["available"])
                text = json.dumps(data, allow_nan=False)
                for leaked in (SECRET, "/private/bin/tool", "private/image", "llm.example/v1?key"):
                    self.assertNotIn(leaked, text)
                for action in data["actions"]:
                    self.assertLessEqual({"id", "label", "revision", "fields"}, set(action))
                    for field in action["fields"]:
                        self.assertIn(field["type"], FIELD_TYPES)
                        if field["type"] == "password":
                            self.assertNotIn("value", field)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.home.iterdir()})

    def test_action_request_shape_is_strict(self):
        descriptor = self.descriptor("models", "save_main", "main")
        good = {"action": "save_main", "expected_revision": descriptor["revision"], "confirm": True,
                "values": {"model": "vendor/next", "provider": "openrouter"}}
        for bad in ({**good, "extra": 1}, {**good, "confirm": False}, {**good, "action": "drop_tables"},
                    {**good, "values": {**good["values"], "api_key": "x"}}, {**good, "expected_revision": "abc"},
                    {**good, "values": {"model": {"nested": 1}, "provider": "auto"}}):
            with self.subTest(bad=bad), self.assertRaises(AdminError):
                self.admin().action("models", bad)
        with self.assertRaises(AdminError):
            self.admin().action("skills", good)

    def test_stale_revision_is_a_conflict_and_writes_nothing(self):
        descriptor = self.descriptor("models", "save_main", "main")
        self.run_action("models", "save_main", {"model": "vendor/a", "provider": "openrouter"}, "main")
        before = (self.home / "config.yaml").read_bytes()
        with self.assertRaises(AdminError) as caught:
            self.admin().action("models", {"action": "save_main", "expected_revision": descriptor["revision"],
                                           "confirm": True, "values": {"model": "vendor/b", "provider": "auto"}})
        self.assertEqual(409, caught.exception.status)
        self.assertEqual(before, (self.home / "config.yaml").read_bytes())

    def test_authorize_runs_and_can_stop_a_write(self):
        calls = []
        self.run_action("models", "save_main", {"model": "vendor/a", "provider": "openrouter"}, "main",
                        authorize=lambda: calls.append(1))
        self.assertTrue(calls)
        before = (self.home / "config.yaml").read_bytes()

        def deny():
            raise AdminError("Your permissions changed.", 403)
        with self.assertRaises(AdminError):
            self.run_action("models", "save_main", {"model": "vendor/b", "provider": "openrouter"}, "main",
                            authorize=deny)
        self.assertEqual(before, (self.home / "config.yaml").read_bytes())

    # ---- models -------------------------------------------------------------

    def test_main_model_preserves_unrelated_settings_and_hidden_base_url(self):
        result = self.run_action("models", "save_main", {"model": "anthropic/claude-x", "provider": "custom",
                                                         "base_url": ""}, "main")
        self.assertTrue(result["ok"])
        self.assertNotIn(SECRET, json.dumps(result))
        config = self.config()
        self.assertEqual("anthropic/claude-x", config["model"]["default"])
        self.assertEqual(SECRET, config["model"]["api_key"])
        self.assertIn(SECRET, config["model"]["base_url"])  # an empty field keeps the hidden value
        self.assertTrue(config["model"]["streaming"])
        for key in ("agent", "platform_toolsets", "gateway", "mcp_servers", "unrelated", "memory"):
            self.assertEqual(CONFIG[key], config[key])
        with self.assertRaises(AdminError):
            self.run_action("models", "save_main", {"model": "x", "provider": "custom", "clear_base_url": True}, "main")
        for values in ({"model": "bad model", "provider": "auto"}, {"model": "ok", "provider": "evil"},
                       {"model": "ok", "provider": "openrouter", "base_url": "http://remote.example/v1"},
                       {"model": "ok", "provider": "openrouter", "base_url": "https://u:p@host.example"}):
            with self.subTest(values=values), self.assertRaises(AdminError):
                self.run_action("models", "save_main", values, "main")

    def test_string_model_becomes_mapping(self):
        self.write_config({"model": "vendor/plain"})
        self.run_action("models", "save_main", {"model": "vendor/next", "provider": "openrouter"}, "main")
        self.assertEqual({"default": "vendor/next", "provider": "openrouter"}, self.config()["model"])

    def test_auxiliary_model_assignment(self):
        self.run_action("models", "save_auxiliary", {"role": "vision", "provider": "openrouter",
                                                     "model": "google/gemini-flash"}, "vision")
        self.run_action("models", "save_auxiliary", {"role": "compression", "provider": "auto", "model": ""},
                        "compression")
        aux = self.config()["auxiliary"]
        self.assertEqual({"provider": "openrouter", "model": "google/gemini-flash", "timeout": 99}, aux["vision"])
        self.assertEqual({"provider": "auto", "model": ""}, aux["compression"])
        rows = {row["id"]: row for row in self.admin().snapshot("models")["items"]}
        self.assertEqual("google/gemini-flash", rows["vision"]["model"])
        self.assertTrue(rows["compression"]["uses_main"])
        with self.assertRaises(AdminError):
            self.run_action("models", "save_auxiliary", {"role": "unknown", "provider": "auto"}, "vision")

    # ---- config -------------------------------------------------------------

    def test_allowlisted_settings_validate_and_preserve(self):
        self.run_action("config", "save_setting", {"setting": "terminal_timeout", "value": "300"}, "terminal_timeout")
        self.run_action("config", "save_setting", {"setting": "approvals_mode", "value": "manual"}, "approvals_mode")
        self.run_action("config", "save_setting", {"setting": "timezone", "value": "Asia/Ho_Chi_Minh"}, "timezone")
        self.run_action("config", "save_setting", {"setting": "reasoning_effort", "value": "high"}, "reasoning_effort")
        self.run_action("config", "save_setting", {"setting": "compression_enabled", "value": False},
                        "compression_enabled")
        config = self.config()
        self.assertEqual({"backend": "docker", "timeout": 300, "docker_image": "private/image"}, config["terminal"])
        self.assertEqual("manual", config["approvals"]["mode"])
        self.assertEqual("Asia/Ho_Chi_Minh", config["timezone"])
        self.assertEqual({"api_max_retries": 3, "reasoning_effort": "high"}, config["agent"])
        self.assertIs(False, config["compression"]["enabled"])
        for values in ({"setting": "terminal_timeout", "value": "5"}, {"setting": "approvals_mode", "value": "off"},
                       {"setting": "timezone", "value": "Mars/Base"}, {"setting": "timezone", "value": "../../etc"},
                       {"setting": "max_turns", "value": "100000"}):
            with self.subTest(values=values), self.assertRaises(AdminError):
                self.run_action("config", "save_setting", values, values["setting"])

    def test_isolation_settings_are_locked(self):
        data = self.admin().snapshot("config")
        locked = {row["id"]: row for row in data["items"] if not row["editable"]}
        self.assertEqual({"memory.memory_enabled", "memory.user_profile_enabled", "terminal.backend",
                          "platform_toolsets"}, set(locked))
        self.assertFalse({a["target"] for a in data["actions"]} & {"memory_enabled", "user_profile_enabled",
                                                                    "terminal_backend", "platform_toolsets"})
        revision = data["actions"][0]["revision"]
        for setting, value in (("memory_enabled", True), ("user_profile_enabled", True), ("terminal_backend", "local"),
                               ("platform_toolsets", "terminal")):
            with self.subTest(setting=setting), self.assertRaises(AdminError):
                self.admin().action("config", {"action": "save_setting", "expected_revision": revision,
                                               "confirm": True, "values": {"setting": setting, "value": value}})
        self.assertEqual(CONFIG["memory"], self.config()["memory"])

    # ---- keys ---------------------------------------------------------------

    def test_keys_write_home_env_only_and_never_echo(self):
        new = "sk-new-" + "Z" * 40
        result = self.run_action("keys", "set_key", {"variable": "ANTHROPIC_API_KEY", "value": new},
                                 "ANTHROPIC_API_KEY")
        self.assertNotIn(new, json.dumps(result))
        env = (self.home / ".env").read_text(encoding="utf-8")
        self.assertIn("# operator comment", env)
        self.assertIn("UNRELATED_SETTING=keep me", env)
        self.assertIn("ANTHROPIC_API_KEY='" + new + "'", env)
        self.assertNotIn(new, json.dumps(self.admin().snapshot("keys")))
        self.run_action("keys", "remove_key", {"variable": "OPENROUTER_API_KEY"}, "OPENROUTER_API_KEY")
        env = (self.home / ".env").read_text(encoding="utf-8")
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertIn("UNRELATED_SETTING=keep me", env)
        for values in ({"variable": "ZALO_BOT_TOKEN", "value": "x" * 20}, {"variable": "PATH", "value": "/tmp"},
                       {"variable": "OPENAI_API_KEY", "value": "has space"},
                       {"variable": "OPENAI_API_KEY", "value": "line\nBREAK=1"}):
            with self.subTest(values=values), self.assertRaises(AdminError):
                self.run_action("keys", "set_key", values, "OPENAI_API_KEY")

    def test_keys_report_source_and_override_notice(self):
        self.environ = {"OPENAI_API_KEY": "from-shell", "OPENROUTER_API_KEY": "different", "ZALO_BOT_TOKEN": "z"}
        rows = {row["id"]: row for row in self.admin().snapshot("keys")["items"]}
        self.assertEqual("process environment", rows["OPENAI_API_KEY"]["source"])
        self.assertIn("process environment", rows["OPENAI_API_KEY"]["notice"])
        self.assertEqual("home .env", rows["OPENROUTER_API_KEY"]["source"])
        self.assertIn("different value", rows["OPENROUTER_API_KEY"]["notice"])
        self.assertFalse(rows["ZALO_BOT_TOKEN"]["editable"])
        self.assertIsNone(rows["ANTHROPIC_API_KEY"]["source"])
        self.assertNotIn("from-shell", json.dumps(rows))

    # ---- channels -----------------------------------------------------------

    def test_channel_settings_target_effective_block(self):
        self.environ = {"TELEGRAM_BOT_TOKEN": SECRET}
        row = next(r for r in self.admin().snapshot("channels")["items"] if r["id"] == "telegram")
        self.assertEqual(("auto", True), (row["mode"], row["enabled"]))
        self.run_action("channels", "save_channel", {"channel": "telegram", "mode": "off", "reply_to_mode": "all",
                                                     "typing_indicator": False}, "telegram")
        block = self.config()["gateway"]["platforms"]["telegram"]
        self.assertEqual({"extra": {"rich_messages": True}, "enabled": False, "reply_to_mode": "all",
                          "typing_indicator": False}, block)
        self.assertNotIn("platforms", self.config())
        row = next(r for r in self.admin().snapshot("channels")["items"] if r["id"] == "telegram")
        self.assertEqual(("off", False), (row["mode"], row["enabled"]))
        self.run_action("channels", "save_channel", {"channel": "telegram", "mode": "auto"}, "telegram")
        self.assertNotIn("enabled", self.config()["gateway"]["platforms"]["telegram"])
        self.run_action("channels", "save_channel", {"channel": "discord", "mode": "on"}, "discord")
        self.assertEqual({"enabled": True}, self.config()["platforms"]["discord"])
        zalo = next(r for r in self.admin().snapshot("channels")["items"] if r["id"] == "zalo")
        self.assertEqual("plugin", zalo["mode"])
        revision = self.descriptor("channels", "save_channel", "telegram")["revision"]
        config = self.config()
        config["platforms"]["api_server"] = {"enabled": False}
        self.write_config(config)
        row = next(r for r in self.admin().snapshot("channels")["items"] if r["id"] == "api_server")
        self.assertFalse(row["editable"])
        revision = self.descriptor("channels", "save_channel", "telegram")["revision"]
        for name in ("zalo", "../x", "unknown", "api_server", "webhook"):
            with self.subTest(name=name), self.assertRaises(AdminError):
                self.admin().action("channels", {"action": "save_channel", "expected_revision": revision,
                                                 "confirm": True, "values": {"channel": name, "mode": "on"}})

    # ---- MCP ----------------------------------------------------------------

    def test_mcp_refuses_exposure_to_chat_channels(self):
        values = {"name": "docs", "url": "https://mcp.example/mcp", "enabled": True}
        with self.assertRaises(AdminError) as caught:
            self.run_action("mcp", "add_http_server", values)
        self.assertEqual(409, caught.exception.status)
        self.assertIn("zalo", str(caught.exception))
        self.assertNotIn("docs", self.config()["mcp_servers"])
        self.run_action("mcp", "add_http_server", {**values, "enabled": False})
        self.assertEqual({"url": "https://mcp.example/mcp", "timeout": 120, "enabled": False},
                         self.config()["mcp_servers"]["docs"])
        with self.assertRaises(AdminError):
            self.run_action("mcp", "toggle_server", {"name": "docs", "enabled": True}, "docs")
        config = self.config()
        for platform in ("zalo", "telegram"):
            config["platform_toolsets"][platform].append("no_mcp")
        self.write_config(config)
        self.run_action("mcp", "toggle_server", {"name": "docs", "enabled": True}, "docs")
        self.assertTrue(self.config()["mcp_servers"]["docs"]["enabled"])
        row = next(r for r in self.admin().snapshot("mcp")["items"] if r["id"] == "docs")
        self.assertEqual([], row["exposed_to"])

    def test_mcp_token_lives_in_env_and_remove_cleans_it(self):
        token = "mcp-token-" + "Q" * 30
        result = self.run_action("mcp", "add_http_server", {"name": "docs", "url": "https://mcp.example/mcp",
                                                            "token": token, "timeout": "60", "enabled": False})
        self.assertNotIn(token, json.dumps(result))
        variable = _mcp_token_var("docs")
        server = self.config()["mcp_servers"]["docs"]
        self.assertEqual({"Authorization": "Bearer ${" + variable + "}"}, server["headers"])
        self.assertNotIn(token, (self.home / "config.yaml").read_text(encoding="utf-8"))
        self.assertIn(variable + "='" + token + "'", (self.home / ".env").read_text(encoding="utf-8"))
        snapshot = json.dumps(self.admin().snapshot("mcp"))
        self.assertNotIn(token, snapshot)
        self.assertNotIn("mcp.example/mcp", snapshot)
        self.run_action("mcp", "remove_server", {"name": "docs"}, "docs")
        self.assertNotIn("docs", self.config()["mcp_servers"])
        env = (self.home / ".env").read_text(encoding="utf-8")
        self.assertNotIn(variable, env)
        self.assertIn("UNRELATED_SETTING=keep me", env)
        self.assertEqual(CONFIG["mcp_servers"]["local-tool"], self.config()["mcp_servers"]["local-tool"])

    def test_mcp_validation(self):
        for values in ({"name": "Bad Name", "url": "https://x.example"}, {"name": "ok", "url": "ftp://x.example"},
                       {"name": "ok", "url": "http://remote.example/mcp"}, {"name": "ok", "url": "https://a:b@x.example"},
                       {"name": "ok", "url": "https://x.example", "timeout": "9999"},
                       {"name": "local-tool", "url": "https://x.example", "enabled": False},
                       {"name": "ok", "url": "https://x.example", "command": "bash"}):
            with self.subTest(values=values), self.assertRaises(AdminError):
                self.run_action("mcp", "add_http_server", {"enabled": False, **values})
        self.run_action("mcp", "add_http_server", {"name": "local-ok", "url": "http://127.0.0.1:9000/mcp",
                                                   "enabled": False})
        self.assertIn("local-ok", self.config()["mcp_servers"])
        self.assertNotIn(_mcp_token_var("local-ok"), (self.home / ".env").read_text(encoding="utf-8"))

    # ---- availability and demo --------------------------------------------------

    def test_missing_home_is_not_created_by_snapshots(self):
        missing = Path(self.temp.name) / "never-created"
        admin = ConnectionsAdmin(ControlStore(missing), environ={})
        for section in SECTIONS:
            self.assertTrue(admin.snapshot(section)["available"])
        self.assertFalse(missing.exists())

    def test_broken_config_is_unavailable_without_actions(self):
        (self.home / "config.yaml").write_text("[broken", encoding="utf-8")
        for section in SECTIONS:
            data = self.admin().snapshot(section)
            self.assertFalse(data["available"])
            self.assertEqual([], data["actions"])

    def test_demo_is_deterministic_in_memory_and_ignores_real_environment(self):
        missing = Path(self.temp.name) / "demo-home"
        first = ConnectionsAdmin(ControlStore(missing, mode="demo"), environ={"OPENAI_API_KEY": SECRET})
        second = ConnectionsAdmin(ControlStore(missing, mode="demo"))
        for section in SECTIONS:
            a, b = first.snapshot(section), second.snapshot(section)
            self.assertEqual(a, b)
            self.assertEqual("demo", a["source"])
            self.assertNotIn(SECRET, json.dumps(a))
        keys = {row["id"]: row for row in first.snapshot("keys")["items"]}
        self.assertFalse(keys["OPENAI_API_KEY"]["configured"])
        descriptor = next(a for a in first.snapshot("mcp")["actions"] if a["id"] == "toggle_server")
        result = first.action("mcp", {"action": "toggle_server", "expected_revision": descriptor["revision"],
                                      "confirm": True, "values": {"name": "demo-docs", "enabled": False}})
        self.assertIn("demo only", result["message"])
        self.assertFalse(first.snapshot("mcp")["items"][0]["enabled"])
        self.assertTrue(second.snapshot("mcp")["items"][0]["enabled"])
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
