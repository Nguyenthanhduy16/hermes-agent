import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from hermes_zalo.admin_management import (ManagementAdmin, ROW_LIMIT, SECTIONS, _safe_log)


SECRET = "sk-live-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"


class ManagementAdminTests(unittest.TestCase):
    def test_log_file_component_and_line_limit_are_bounded_and_allowlisted(self):
        self.write('logs/gateway.log', '\n'.join(f'2026-11-01 08:00:00 ERROR cron message {i}' for i in range(550)))
        self.write('logs/agent.log', '2026-11-01 09:00:00 INFO unrelated\n')
        result = self.admin().snapshot('logs', {'file': 'gateway', 'component': 'cron', 'lines': '500', 'level': 'ERROR'})
        self.assertEqual(500, len(result['items']))
        self.assertTrue(result['truncated'])
        self.assertTrue(all(item['file'] == 'gateway.log' for item in result['items']))
        self.assertIn('549', result['items'][0]['line'])
        for query in ({'file': '../config.yaml'}, {'component': 'shell'}, {'lines': '5000'}):
            with self.assertRaises(ValueError):
                self.admin().snapshot('logs', query)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "bot-home"
        self.home.mkdir()

    def admin(self, **kw):
        return ManagementAdmin(self.home, environ={}, **kw)

    def write(self, relative, value):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def config(self):
        self.write("config.yaml", yaml.safe_dump({
            "model": {"default": "vendor/reasoner", "provider": "custom", "api_key": SECRET,
                      "base_url": "https://private.example/v1?key=" + SECRET},
            "auxiliary": {"vision": {"model": "vendor/vision", "provider": "custom", "api_key": SECRET}},
            "terminal": {"backend": "docker", "timeout": 180, "cwd": "/private/workspace",
                         "env": {"PASSWORD": SECRET}},
            "code_execution": {"timeout": 180, "max_tool_calls": 30},
            "memory": {"memory_enabled": False},
            "system_prompt": "PRIVATE PROMPT " + SECRET,
            "platform_toolsets": {"telegram": ["web", "vision", "code_execution"]},
            "platforms": {"telegram": {"enabled": True, "extra": {"token": SECRET}},
                          "webhook": {"enabled": False, "extra": {"secret": SECRET}}},
            "mcp_servers": {"private-docs": {"command": "/private/command", "args": [SECRET],
                "env": {"API_KEY": SECRET}, "enabled": True}, "remote": {"url": "https://private.example/mcp",
                "headers": {"Authorization": "Bearer " + SECRET}}},
            "plugins": {"enabled": ["assistant-core"], "disabled": ["disabled-plugin"]},
        }))

    def populate(self):
        self.config()
        self.write("logs/gateway.log", "2026-10-31 INFO started\nWARNING key=" + SECRET +
                   " endpoint https://private.example/ with Bearer short-token\nERROR api_key=short-secret\n")
        self.write("plugins/assistant-core/plugin.yaml", yaml.safe_dump({
            "name": "assistant-core", "version": "0.1.0", "description": "Policy and telemetry",
            "provides_hooks": ["pre_tool_call"], "optional_env": {"API_KEY": SECRET},
        }))
        self.write("profiles/research/config.yaml", "model:\n  default: vendor/research\n")
        self.write("webhook_subscriptions.json", json.dumps({"updates": {"enabled": True,
            "secret": SECRET, "events": ["update"], "script": "/private/script",
            "prompt": "PRIVATE PROMPT", "deliver": "telegram:private-chat", "created_at": 123}}))
        self.write("platforms/pairing/telegram-pending.json", json.dumps({"SECRET-CODE": {
            "user_id": "private-user", "request_id": "private-request", "created_at": 123}}))
        self.write("platforms/pairing/telegram-approved.json", json.dumps({"private-user": {
            "user_name": "Private Name", "approved_at": 123}}))

    def tree(self):
        return {str(p.relative_to(self.root)): (p.lstat().st_size, p.lstat().st_mtime_ns)
                for p in self.root.rglob("*")}

    def test_every_section_has_common_serializable_contract_and_does_not_write(self):
        self.populate()
        before = self.tree()
        for section in SECTIONS:
            with self.subTest(section=section):
                result = self.admin().snapshot(section)
                self.assertLessEqual({"section", "available", "source", "message", "items", "updated_at"}, result.keys())
                self.assertEqual(section, result["section"])
                self.assertIsInstance(result["available"], bool)
                self.assertIsInstance(result["items"], list)
                self.assertLessEqual(len(result["items"]), ROW_LIMIT)
                json.dumps(result, allow_nan=False)
        self.assertEqual(before, self.tree())

    def test_missing_sources_do_not_create_home_or_invent_configuration(self):
        admin = ManagementAdmin(self.root / "missing", environ={})
        for section in SECTIONS - {"keys", "documentation"}:
            with self.subTest(section=section):
                self.assertFalse(admin.snapshot(section)["available"])
        self.assertFalse((self.root / "missing").exists())
        with self.assertRaises(ValueError):
            ManagementAdmin(None)

    def test_models_and_config_never_return_raw_settings_or_secrets(self):
        self.config()
        models = self.admin().snapshot("models")
        self.assertEqual(models["items"][0], {"role": "Main", "model": "vendor/reasoner", "provider": "custom"})
        self.assertEqual(models["items"][1]["model"], "vendor/vision")
        config = self.admin().snapshot("config")
        values = {(x["section"], x["setting"]): x["value"] for x in config["items"]}
        self.assertEqual("docker", values[("terminal", "backend")])
        self.assertFalse(values[("memory", "memory_enabled")])
        text = json.dumps([models, config])
        for leaked in (SECRET, "private.example", "PRIVATE PROMPT", "/private/workspace", "PASSWORD"):
            self.assertNotIn(leaked, text)

    def test_plugins_are_manifest_only_and_never_imported(self):
        self.populate()
        self.write("plugins/assistant-core/__init__.py", "raise RuntimeError('must never import')")
        data = self.admin().snapshot("plugins")
        self.assertEqual(data["items"], [{"name": "assistant-core", "version": "0.1.0",
            "description": "Policy and telemetry", "status": "enabled", "hooks": 1}])
        self.assertNotIn(SECRET, json.dumps(data))

    def test_mcp_omits_commands_args_headers_and_endpoints(self):
        self.config()
        data = self.admin().snapshot("mcp")
        self.assertEqual(2, len(data["items"]))
        text = json.dumps(data)
        for leaked in (SECRET, "/private/command", "private.example", "Authorization", "Bearer"):
            self.assertNotIn(leaked, text)
        self.assertTrue(all(x["credentials_configured"] for x in data["items"]))

    def test_keys_use_only_known_presence_checks(self):
        admin = ManagementAdmin(self.home, environ={"OPENAI_API_KEY": SECRET,
            "PRIVATE_UNRECOGNIZED_SECRET": "never-show-this"})
        data = admin.snapshot("keys")
        keys = {x["variable"]: x["configured"] for x in data["items"]}
        self.assertTrue(keys["OPENAI_API_KEY"])
        self.assertFalse(keys["ANTHROPIC_API_KEY"])
        self.assertNotIn("PRIVATE_UNRECOGNIZED_SECRET", keys)
        self.assertNotIn(SECRET, json.dumps(data))
        self.assertNotIn("never-show-this", json.dumps(data))

    def test_channels_report_configured_not_connected(self):
        self.config()
        data = ManagementAdmin(self.home, environ={"TELEGRAM_BOT_TOKEN": SECRET}).snapshot("channels")
        channels = {x["channel"]: x for x in data["items"]}
        self.assertTrue(channels["telegram"]["enabled"])
        self.assertTrue(channels["telegram"]["credential_configured"])
        self.assertEqual("not verified", channels["telegram"]["connection"])
        self.assertFalse(channels["webhook"]["enabled"])
        self.assertNotIn(SECRET, json.dumps(data))

    def test_pairing_and_webhooks_do_not_expose_codes_people_or_destinations(self):
        self.populate()
        pairing = self.admin().snapshot("pairing")
        self.assertEqual({x["status"]: x["count"] for x in pairing["items"]}, {"pending": 1, "approved": 1})
        hooks = self.admin().snapshot("webhooks")
        self.assertTrue(hooks["items"][0]["secret_configured"])
        text = json.dumps([pairing, hooks])
        for leaked in (SECRET, "SECRET-CODE", "private-user", "private-request", "Private Name", "PRIVATE PROMPT", "/private/script", "telegram:private-chat"):
            self.assertNotIn(leaked, text)

    def test_profile_reader_stays_inside_selected_home(self):
        self.populate()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "config.yaml").write_text("model: forbidden-outside-model")
        (self.home / "profiles" / "escape").symlink_to(outside, target_is_directory=True)
        items = self.admin().snapshot("profiles")["items"]
        self.assertEqual([x["name"] for x in items], ["research"])
        self.assertEqual("vendor/research", items[0]["model"])
        self.assertNotIn("forbidden-outside", json.dumps(items))

    def test_symlink_files_and_ancestor_directories_are_not_read(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "config.yaml").write_text("model: should-not-leak")
        (outside / "gateway.log").write_text("INFO should-not-leak")
        (self.home / "config.yaml").symlink_to(outside / "config.yaml")
        (self.home / "logs").symlink_to(outside, target_is_directory=True)
        self.assertFalse(self.admin().snapshot("models")["available"])
        self.assertFalse(self.admin().snapshot("logs")["available"])
        (self.home / "plugins").symlink_to(outside, target_is_directory=True)
        self.assertFalse(self.admin().snapshot("plugins")["available"])
        linked = self.root / "linked-home"
        linked.symlink_to(outside, target_is_directory=True)
        self.assertFalse(ManagementAdmin(linked, environ={}).snapshot("models")["available"])

    def test_logs_are_bounded_filtered_and_redacted(self):
        self.populate()
        data = self.admin().snapshot("logs")
        self.assertTrue(data["available"])
        text = json.dumps(data)
        for secret in (SECRET, "short-secret", "short-token", "private.example"):
            self.assertNotIn(secret, text)
        self.assertEqual([x["level"] for x in self.admin().snapshot("logs", {"level": "error"})["items"]], ["ERROR"])
        self.assertEqual(1, len(self.admin().snapshot("logs", {"q": "started"})["items"]))
        self.write("logs/gateway.log", "INFO start\n" + "INFO safe line\n" * 20000)
        self.assertEqual(ROW_LIMIT, len(self.admin().snapshot("logs")["items"]))

    def test_foreign_or_oversize_configuration_is_unavailable(self):
        for value in ("[invalid", "- scalar-list", "x: " + "z" * (1024 * 1024 + 10)):
            with self.subTest(value=value[:20]):
                self.write("config.yaml", value)
                self.assertFalse(self.admin().snapshot("config")["available"])

    def test_scan_results_and_queries_are_bounded(self):
        for i in range(ROW_LIMIT + 10):
            self.write(f"plugins/p{i:03}/plugin.yaml", f"name: p{i:03}\nversion: 1\n")
        data = self.admin().snapshot("plugins")
        self.assertEqual(ROW_LIMIT, len(data["items"]))
        self.assertTrue(data["truncated"])
        for section, query in (("unknown", None), ("config", {"path": "../private"}),
                               ("logs", {"path": "../private"}), ("logs", {"q": "x" * 101}),
                               ("logs", {"level": "not-a-level"}), ("logs", "invalid")):
            with self.assertRaises(ValueError):
                self.admin().snapshot(section, query)

    def test_chat_status_never_calls_endpoint_or_exposes_credential(self):
        env = {"HERMES_CONSOLE_CHAT_API_URL": "https://private.example/v1?key=" + SECRET,
               "HERMES_CONSOLE_CHAT_API_KEY": SECRET}
        with patch("httpx.get", side_effect=AssertionError("must not connect")), \
                patch("httpx.post", side_effect=AssertionError("must not connect")):
            data = ManagementAdmin(self.home, environ=env).snapshot("chat")
        self.assertTrue(data["configured"])
        self.assertNotIn(SECRET, json.dumps(data))
        self.assertNotIn("private.example", json.dumps(data))
        self.assertFalse(self.admin().snapshot("chat")["configured"])

    def test_demo_is_offline_deterministic_and_does_not_read_real_state(self):
        demo = self.admin(mode="demo")
        with patch.object(demo, "_read", side_effect=AssertionError("demo must not read")), \
                patch.object(demo, "_children", side_effect=AssertionError("demo must not scan")):
            first = [demo.snapshot(section) for section in sorted(SECTIONS)]
            second = [demo.snapshot(section) for section in sorted(SECTIONS)]
        self.assertEqual(first, second)
        for data in first:
            self.assertEqual("demo", data["source"])
            self.assertIn("Synthetic", data["message"])
        self.assertFalse(demo.snapshot("chat")["configured"])

    def test_logs_merge_files_newest_first_and_filter_before_bounding(self):
        self.write("logs/gateway.log", "2026-10-31 08:00:01,000 INFO gateway old\n"
                   "2026-10-31 08:00:03,000 ERROR gateway failed\nTraceback line\n")
        self.write("logs/agent.log", "2026-10-31 08:00:02,000 INFO agent middle\n"
                   "2026-10-31 08:00:04,000 INFO agent newest\n")
        lines = [x["line"] for x in self.admin().snapshot("logs")["items"]]
        self.assertEqual(lines[0], "2026-10-31 08:00:04,000 INFO agent newest")
        self.assertEqual(lines[1:3], ["Traceback line", "2026-10-31 08:00:03,000 ERROR gateway failed"])
        self.assertEqual(lines[-1], "2026-10-31 08:00:01,000 INFO gateway old")
        self.write("logs/agent.log", "ERROR early failure\n" + "INFO noise\n" * 500)
        errors = self.admin().snapshot("logs", {"level": "ERROR"})
        self.assertIn("ERROR early failure", [x["line"] for x in errors["items"]])
        self.write("logs/agent.log", "INFO noise\n" * 500)
        self.assertTrue(self.admin().snapshot("logs")["truncated"])

    def test_one_broken_pairing_store_does_not_hide_the_others(self):
        self.populate()
        self.write("platforms/pairing/zalo-pending.json", "{not json")
        pairing = self.admin().snapshot("pairing")
        self.assertTrue(pairing["available"])
        self.assertEqual({(x["channel"], x["status"]) for x in pairing["items"]},
                         {("telegram", "pending"), ("telegram", "approved")})

    def test_unexpected_shapes_are_unavailable_not_errors(self):
        self.write("plugins/odd/plugin.yaml", "name: [not, a, string]\nversion: 2\n")
        self.assertEqual("odd", self.admin().snapshot("plugins")["items"][0]["name"])
        self.write("webhook_subscriptions.json", "[1, 2]")
        self.assertFalse(self.admin().snapshot("webhooks")["available"])
        self.write("config.yaml", "model: &a [*a]\n")
        for section in ("models", "config", "mcp", "channels"):
            json.dumps(self.admin().snapshot(section), allow_nan=False)

    def test_demo_matches_live_schema(self):
        self.populate()
        for section in SECTIONS:
            with self.subTest(section=section):
                live = self.admin().snapshot(section)
                demo = self.admin(mode="demo").snapshot(section)
                self.assertEqual(set(live), set(demo))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs are POSIX-only")
    def test_fifo_sources_do_not_block(self):
        os.mkfifo(self.home / "config.yaml")
        (self.home / "logs").mkdir()
        os.mkfifo(self.home / "logs" / "gateway.log")
        self.assertFalse(self.admin().snapshot("config")["available"])
        self.assertFalse(self.admin().snapshot("logs")["available"])

    def test_log_redactor_handles_quoted_credentials(self):
        for raw in ('INFO {"api_key": "a-short-key", "password":"a-password"}',
                    "Authorization: Bearer small-secret", "cookie='session=private'", "token=123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
            safe = _safe_log(raw)
            for value in ("a-short-key", "a-password", "small-secret", "session=private", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
                self.assertNotIn(value, safe)


if __name__ == "__main__":
    unittest.main()
