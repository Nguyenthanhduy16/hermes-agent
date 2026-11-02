"""Integrations console pages: connector catalog, delegated writes, connection test and reports."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from unittest import mock
import unittest
from urllib.parse import urlsplit

import yaml

from hermes_zalo import admin_integrations as integrations
from hermes_zalo import console_sections, integration_catalog as catalog
from hermes_zalo.admin_auth import AdminError
from hermes_zalo.admin_automation import AutomationAdmin
from hermes_zalo.admin_control_store import ControlStore
from hermes_zalo.console_sections import ConsoleContext

SECRET = "sk-test-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
FIELD_TYPES = {"text", "textarea", "password", "number", "checkbox", "select", "hidden"}


class FakeMCP:
    """A loopback MCP server for the connection test. Records every request."""

    def __init__(self, mode="json", tools=("get_insights", "list_posts")):
        self.mode, self.tools, self.requests = mode, list(tools), []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self, body):
                fake.requests.append({"method": self.command, "path": self.path,
                                      "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})

            def handle(self):
                try:
                    super().handle()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client stops reading at its size bound, as intended

            def do_DELETE(self):
                self._record(None)
                self.send_response(200)
                self.end_headers()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                self._record(body)
                if fake.mode == "redirect":
                    self.send_response(307)
                    self.send_header("Location", "http://other.invalid/mcp")
                    self.end_headers()
                    return
                if fake.mode == "unauthorized" or self.headers.get("Authorization") != "Bearer " + SECRET:
                    self.send_response(401)
                    self.end_headers()
                    return
                if fake.mode == "slow":
                    time.sleep(1.5)
                method = body.get("method")
                if "id" not in body:
                    self.send_response(202)
                    self.end_headers()
                    return
                if method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "fake-social", "version": "1"}}
                elif method == "tools/list":
                    if self.headers.get("Mcp-Session-Id") != "session-1":
                        self.send_response(400)
                        self.end_headers()
                        return
                    if fake.mode == "huge":
                        result = {"tools": [{"name": "t" + str(i), "description": "x" * 1000} for i in range(400)]}
                    elif not body.get("params", {}).get("cursor"):
                        result = {"tools": [{"name": n} for n in fake.tools[:1]], "nextCursor": "page-2"}
                    else:
                        result = {"tools": [{"name": n} for n in fake.tools[1:]]}
                else:
                    result = {}
                payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
                self.send_response(200)
                self.send_header("Mcp-Session-Id", "session-1")
                if fake.mode == "sse":
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b": comment\n\nevent: message\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"note\"}\n\n")
                    self.wfile.write(b"event: message\ndata: " + payload + b"\n\n")
                else:
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Base(unittest.TestCase):
    CONFIG = {
        "platform_toolsets": {"telegram": ["web", "no_mcp"]},
        "mcp_servers": {
            "zalo-oa": {"url": "http://127.0.0.1:8790/mcp", "enabled": False, "timeout": 60},
            "my-tools": {"url": "https://tools.example.com/mcp", "enabled": True,
                         "headers": {"Authorization": "Bearer ${MY_TOOLS_KEY}"}},
            "notion": {"command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"],
                       "env": {"NOTION_TOKEN": "${NOTION_TOKEN}"}, "enabled": False},
        },
        "unrelated": {"keep": True},
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        (self.home / "config.yaml").write_text(yaml.safe_dump(self.CONFIG), encoding="utf-8")
        (self.home / ".env").write_text("OPENROUTER_API_KEY=" + SECRET + "\n", encoding="utf-8")
        self.environ = {}
        self.ctx = ConsoleContext(home=self.home, mode="local", assistant_dir=self.home, environ=self.environ)
        self.os_environ = dict(os.environ)
        integrations._TEST_RESULTS.clear()

    def tearDown(self):
        self.assertEqual(self.os_environ, dict(os.environ), "the process environment must never change")

    def config(self):
        return yaml.safe_load((self.home / "config.yaml").read_text(encoding="utf-8"))

    def env(self):
        return (self.home / ".env").read_text(encoding="utf-8")

    def snapshot(self, section="connectors"):
        snap = (integrations.connectors_snapshot if section == "connectors" else integrations.reports_snapshot)(self.ctx, {})
        for action in snap["actions"]:
            self.assertTrue({"id", "label", "revision", "fields"} <= set(action))
            for field in action["fields"]:
                self.assertIn(field["type"], FIELD_TYPES)
                if field["type"] == "password":
                    self.assertNotIn("value", field, "secrets are never prefilled")
        return snap

    def item(self, snap, item_id):
        return next(i for i in snap["items"] if i["id"] == item_id)

    def descriptor(self, snap, action, target, label=None):
        found = [a for a in snap["actions"] if a["id"] == action and a.get("target") == target
                 and (label is None or label in a["label"])]
        self.assertTrue(found, f"no {action} for {target}")
        return found[0]

    def act(self, action, values, revision=None, section="connectors", target=None, label=None):
        if revision is None:
            revision = self.descriptor(self.snapshot(section), action, target or values.get("template"), label)["revision"]
        handler = integrations.connectors_action if section == "connectors" else integrations.reports_action
        return handler(self.ctx, {"action": action, "expected_revision": revision, "confirm": True, "values": values})


class CatalogTests(unittest.TestCase):
    def test_catalog_is_honest_and_safe(self):
        ids = [t["id"] for t in catalog.CONNECTORS]
        self.assertEqual(len(ids), len(set(ids)))
        for expected in ("facebook-pages", "instagram", "threads", "x-twitter", "tiktok", "youtube", "linkedin",
                         "zalo-oa", "telegram-channel", "google-analytics", "search-console", "gmail",
                         "google-calendar", "google-drive", "notion", "github", "custom"):
            self.assertIn(expected, ids)
        for template in catalog.CONNECTORS:
            for doc in template["docs"]:
                self.assertTrue(doc["url"].startswith("https://"), doc)
            if template["kind"] == "bridge":
                self.assertNotIn("url", template, "a bridge has no invented endpoint")
                self.assertIn("bridge", template["note"].lower())
            if template["kind"] == "local":
                self.assertIn("mcp_servers:", template["snippet"])
                self.assertIn("enabled: false", template["snippet"])
            if template["kind"] == "remote":
                self.assertTrue(template["url"].startswith("https://"))
            for cap in template["capabilities"]:
                self.assertIn(cap["access"], catalog.ACCESS_LABELS)
        zalo = integrations.TEMPLATES["zalo-oa"]
        self.assertEqual("bridge", zalo["kind"])
        self.assertIn("no official MCP server", zalo["note"])
        self.assertTrue(any(c["access"] == "messages" for c in zalo["capabilities"]))

    def test_only_shaped_credential_names_are_writable(self):
        for names in integrations.WRITABLE.values():
            for name in names:
                self.assertTrue(integrations._safe_variable(name))
        for bad in ("PATH", "HERMES_HOME", "LD_PRELOAD", "ZALO_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "HERMES_API_KEY",
                    "PYTHONPATH_KEY", "lower_token", "SOME_SETTING", "", None):
            self.assertFalse(integrations._safe_variable(bad), bad)

    def test_sections_are_registered(self):
        self.assertIsNotNone(console_sections.get("connectors"))
        self.assertIsNotNone(console_sections.get("reports"))


class ConnectorStatusTests(Base):
    def test_status_reflects_config_and_env_presence(self):
        snap = self.snapshot()
        self.assertTrue(snap["available"])
        status = {i["id"]: i["status"] for i in snap["items"]}
        self.assertEqual("available", status["github"])
        self.assertEqual("installed", status["zalo-oa"])
        self.assertEqual("bridge_needed", status["facebook-pages"])
        self.assertEqual("needs_credentials", status["notion"], "installed stdio server whose ${NOTION_TOKEN} is unset")
        self.assertEqual("needs_credentials", status["custom"], "custom server header references a missing key")
        custom = self.item(snap, "custom")
        self.assertEqual(["my-tools"], [s["name"] for s in custom["servers"]])
        self.assertEqual(["MY_TOOLS_KEY"], custom["servers"][0]["missing_credentials"])
        self.environ["MY_TOOLS_KEY"] = SECRET
        (self.home / ".env").write_text("NOTION_TOKEN=" + SECRET + "\n", encoding="utf-8")
        status = {i["id"]: i["status"] for i in self.snapshot()["items"]}
        self.assertEqual("enabled", status["custom"])
        self.assertEqual("installed", status["notion"])
        notion = self.item(self.snapshot(), "notion")
        self.assertEqual("home .env", notion["credentials"][0]["source"])
        dumped = json.dumps(self.snapshot())
        self.assertNotIn(SECRET, dumped, "credential values never leave the server")

    def test_local_template_needs_credentials_before_install(self):
        config = self.config()
        del config["mcp_servers"]["notion"]
        (self.home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        snap = self.snapshot()
        self.assertEqual("needs_credentials", self.item(snap, "notion")["status"])
        self.assertFalse([a for a in snap["actions"] if a["id"] == "install" and a["target"] == "notion"],
                         "local servers are not installed by the console")
        self.assertEqual("needs_credentials", self.item(snap, "google-analytics")["status"])
        self.assertFalse([a for a in snap["actions"] if a["target"] == "google-analytics"],
                         "server file paths are not editable here")

    def test_unreadable_config_is_reported(self):
        (self.home / "config.yaml").write_text("- not a mapping\n", encoding="utf-8")
        snap = integrations.connectors_snapshot(self.ctx, {})
        self.assertFalse(snap["available"])
        self.assertEqual([], snap["actions"])


class ConnectorActionTests(Base):
    def test_install_delegates_to_the_mcp_service(self):
        result = self.act("install", {"template": "github", "name": "github", "url": "https://api.githubcopilot.com/mcp/",
                                      "token": SECRET, "timeout": 90, "enabled": False})
        self.assertTrue(result["ok"])
        self.assertIn("GitHub connector", result["message"])
        server = self.config()["mcp_servers"]["github"]
        self.assertEqual({"url": "https://api.githubcopilot.com/mcp/", "timeout": 90, "enabled": False,
                          "headers": {"Authorization": "Bearer ${MCP_GITHUB_TOKEN}"}}, server)
        self.assertIn("MCP_GITHUB_TOKEN='" + SECRET + "'", self.env())
        self.assertEqual({"keep": True}, self.config()["unrelated"])
        snap = self.snapshot()
        self.assertEqual("installed", self.item(snap, "github")["status"])
        self.assertFalse([a for a in snap["actions"] if a["id"] == "install" and a["target"] == "github"])
        self.assertTrue(self.descriptor(snap, "replace_token", "github"))
        self.assertNotIn(SECRET, json.dumps(snap))

    def test_install_keeps_service_rules(self):
        values = {"template": "custom", "name": "plain", "url": "http://tools.example.com/mcp", "enabled": False}
        with self.assertRaisesRegex(AdminError, "https"):
            self.act("install", values)
        stale = "0" * 64 + "." + "0" * 64
        with self.assertRaises(AdminError) as caught:
            self.act("install", dict(values, url="https://tools.example.com/mcp"), revision=stale)
        self.assertEqual(409, caught.exception.status)
        # A chat channel without no_mcp would receive the tools: enabling is refused, disabled is fine.
        config = self.config()
        config["platform_toolsets"] = {"telegram": ["web"]}
        config["platforms"] = {"telegram": {"enabled": True}}
        (self.home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        snap = self.snapshot()
        install = self.descriptor(snap, "install", "github")
        self.assertIn("telegram", install["description"])
        self.assertFalse(next(f for f in install["fields"] if f["name"] == "enabled")["value"])
        with self.assertRaisesRegex(AdminError, "chat users"):
            self.act("install", {"template": "github", "name": "github", "url": "https://api.githubcopilot.com/mcp/",
                                 "enabled": True})
        self.assertNotIn("github", self.config()["mcp_servers"])
        with self.assertRaisesRegex(AdminError, "operator"):
            self.act("install", {"template": "notion", "name": "notion2", "url": "https://x.example.com/mcp"},
                     revision=install["revision"])
        with self.assertRaisesRegex(AdminError, "Unsupported field"):
            self.act("install", {"template": "github", "name": "g", "url": "https://x.example.com/", "command": "sh"},
                     revision=install["revision"])

    def test_toggle_and_remove_delegate(self):
        self.act("toggle_server", {"template": "zalo-oa", "name": "zalo-oa", "enabled": True})
        self.assertTrue(self.config()["mcp_servers"]["zalo-oa"]["enabled"])
        self.assertEqual("enabled", self.item(self.snapshot(), "zalo-oa")["status"])
        self.act("remove_server", {"template": "zalo-oa", "name": "zalo-oa"})
        self.assertNotIn("zalo-oa", self.config()["mcp_servers"])
        self.assertEqual("bridge_needed", self.item(self.snapshot(), "zalo-oa")["status"])

    def test_credentials_are_allowlisted_and_write_only(self):
        result = self.act("set_credential", {"template": "notion", "variable": "NOTION_TOKEN", "value": SECRET})
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertIn("NOTION_TOKEN='" + SECRET + "'", self.env())
        self.assertIn("OPENROUTER_API_KEY=" + SECRET, self.env(), "unrelated lines are preserved")
        snap = self.snapshot()
        revision = self.descriptor(snap, "set_credential", "notion")["revision"]
        for template, variable in (("notion", "PATH"), ("notion", "OPENROUTER_API_KEY"), ("github", "NOTION_TOKEN"),
                                   ("google-analytics", "GOOGLE_APPLICATION_CREDENTIALS")):
            with self.assertRaisesRegex(AdminError, "cannot be managed"):
                self.act("set_credential", {"template": template, "variable": variable, "value": "x"}, revision=revision)
        with self.assertRaisesRegex(AdminError, "one line"):
            self.act("set_credential", {"template": "notion", "variable": "NOTION_TOKEN", "value": "two words"},
                     revision=revision)
        self.act("remove_credential", {"template": "notion", "variable": "NOTION_TOKEN"})
        self.assertNotIn("NOTION_TOKEN", self.env())

    def test_known_provider_keys_go_through_the_keys_service(self):
        with mock.patch.dict(integrations.WRITABLE, {"github": {"GITHUB_TOKEN"}}):
            with mock.patch.object(integrations.ConnectionsAdmin, "action", autospec=True,
                                   side_effect=lambda self, section, data, **kw: {"ok": True, "message": section}) as act:
                _, env_rev = ControlStore(self.home).read_env()
                result = integrations.connectors_action(self.ctx, {
                    "action": "set_credential", "expected_revision": env_rev, "confirm": True,
                    "values": {"template": "github", "variable": "GITHUB_TOKEN", "value": SECRET}})
        self.assertEqual("keys", result["message"])
        self.assertEqual("set_key", act.call_args.args[2]["action"])

    def test_replace_token_only_for_console_created_tokens(self):
        self.act("install", {"template": "custom", "name": "bridge", "url": "http://127.0.0.1:9/mcp", "token": "first-token",
                             "enabled": False}, label="Add server")
        self.act("replace_token", {"template": "custom", "name": "bridge", "value": SECRET})
        self.assertIn("MCP_BRIDGE_TOKEN='" + SECRET + "'", self.env())
        self.assertNotIn("first-token", self.env())
        _, env_rev = ControlStore(self.home).read_env()
        with self.assertRaisesRegex(AdminError, "not created by the console"):
            self.act("replace_token", {"template": "custom", "name": "my-tools", "value": SECRET}, revision=env_rev)


class ConnectionTestTests(Base):
    def install_fake(self, mode="json"):
        fake = FakeMCP(mode)
        self.addCleanup(fake.close)
        self.act("install", {"template": "custom", "name": "fake", "url": fake.url, "token": SECRET, "enabled": False},
                 label="Add server")
        return fake

    def run_test(self, name="fake"):
        return self.act("test_connection", {"template": "custom", "name": name}, label="Test " + name)

    def test_lists_tools_over_json_and_sends_the_header_only_to_the_server(self):
        fake = self.install_fake()
        result = self.run_test()
        self.assertTrue(result["test"]["ok"], result)
        self.assertEqual(["get_insights", "list_posts"], result["test"]["tools"])
        self.assertEqual("fake-social", result["test"]["server"])
        self.assertIn("2 tools", result["message"])
        methods = [r["body"] and r["body"].get("method") for r in fake.requests]
        self.assertEqual(["initialize", "notifications/initialized", "tools/list", "tools/list", None], methods)
        self.assertEqual("DELETE", fake.requests[-1]["method"])
        for request in fake.requests:
            self.assertEqual("Bearer " + SECRET, request["headers"]["authorization"])
            self.assertEqual("/mcp", request["path"])
        self.assertEqual("2025-06-18", fake.requests[2]["headers"]["mcp-protocol-version"])
        self.assertNotIn(SECRET, json.dumps(result))
        card = next(s for s in self.item(self.snapshot(), "custom")["servers"] if s["name"] == "fake")
        self.assertEqual(2, card["last_test"]["count"])
        self.assertTrue((self.home / "config.yaml").exists())
        self.assertNotIn("last_test", (self.home / "config.yaml").read_text(), "test results stay in memory")

    def test_event_stream_responses(self):
        self.install_fake("sse")
        result = self.run_test()
        self.assertTrue(result["test"]["ok"], result)
        self.assertEqual(2, result["test"]["count"])

    def test_failures_are_reported_without_following_redirects(self):
        fake = self.install_fake("redirect")
        result = self.run_test()
        self.assertFalse(result["test"]["ok"])
        self.assertIn("redirect", result["test"]["error"])
        self.assertEqual(1, len(fake.requests), "redirects are never followed")
        fake.mode = "unauthorized"
        self.assertIn("rejected the credentials", self.run_test()["test"]["error"])
        fake.mode = "huge"
        self.assertIn("larger", self.run_test()["test"]["error"])

    def test_missing_variable_and_stale_revision(self):
        result = self.act("test_connection", {"template": "custom", "name": "my-tools"}, label="Test my-tools")
        self.assertFalse(result["test"]["ok"])
        self.assertIn("MY_TOOLS_KEY", result["test"]["error"])
        with self.assertRaisesRegex(AdminError, "changed"):
            integrations.connectors_action(self.ctx, {"action": "test_connection", "expected_revision": "0" * 64,
                                                      "confirm": True, "values": {"template": "custom", "name": "my-tools"}})
        with self.assertRaisesRegex(AdminError, "Only HTTP"):
            config_rev = ControlStore(self.home).read_yaml()[1]
            integrations.connectors_action(self.ctx, {"action": "test_connection", "expected_revision": config_rev,
                                                      "confirm": True, "values": {"template": "notion", "name": "notion"}})

    def test_probe_timeout_is_short(self):
        fake = FakeMCP("slow")
        self.addCleanup(fake.close)
        started = time.monotonic()
        with self.assertRaisesRegex(integrations.ProbeError, "in time"):
            integrations.probe_mcp(fake.url, {"Authorization": "Bearer " + SECRET}, timeout=0.3)
        self.assertLess(time.monotonic() - started, 3)

    def test_remote_plain_http_is_refused(self):
        config = self.config()
        config["mcp_servers"]["remote"] = {"url": "http://198.51.100.7/mcp", "enabled": False}
        (self.home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        with mock.patch.object(integrations, "probe_mcp", side_effect=AssertionError("no network")):
            result = self.act("test_connection", {"template": "custom", "name": "remote"}, label="Test remote")
        self.assertFalse(result["test"]["ok"])
        self.assertIn("https", result["test"]["error"])


class ConnectorSetupTests(Base):
    def test_setup_descriptors_are_honest_and_default_to_disabled(self):
        snap = self.snapshot()
        github = self.item(snap, "github")
        self.assertEqual("bearer_http", github["connection"]["method"])
        self.assertFalse(github["connection"]["web_oauth_supported"])
        for tid, method in (("google-drive", "bridge_http"), ("notion", "operator_stdio"), ("custom", "custom_http")):
            self.assertEqual(method, self.item(snap, tid)["connection"]["method"])
        for action in snap["actions"]:
            if action["id"] == "install":
                self.assertFalse(next(f for f in action["fields"] if f["name"] == "enabled")["value"])
        self.assertFalse([a for a in snap["actions"] if a["target"] == "google-drive" and a["id"] == "set_credential"],
                         "saving a provider secret must not masquerade as creating an HTTP bridge")
        self.assertEqual("unknown", self.item(snap, "zalo-oa")["runtime_status"])
        self.assertNotIn("restart_required", self.item(snap, "zalo-oa"))
        self.assertNotIn("connected", snap["summary"])

    def test_explicit_authentication_is_validated_and_never_installs_oauth(self):
        values = {"template": "custom", "name": "extra", "url": "https://example.com/mcp", "enabled": False}
        for change in ({"authentication": "oauth"}, {"authentication": "bearer"},
                       {"authentication": "none", "token": SECRET}):
            with self.assertRaises(AdminError):
                self.act("install", dict(values, **change))
        self.assertNotIn("extra", self.config()["mcp_servers"])
        self.act("install", dict(values, authentication="none"))
        self.assertNotIn("headers", self.config()["mcp_servers"]["extra"])
        self.assertFalse(self.config()["mcp_servers"]["extra"]["enabled"])

    def test_yours_uses_actual_config_and_credential_presence_not_missing_requirements(self):
        (self.home / "config.yaml").write_text("mcp_servers: {}\n")
        snap = self.snapshot()
        self.assertEqual(0, snap["summary"]["yours"])
        self.assertTrue(all(not i["configured"] for i in snap["items"]))
        self.assertEqual("needs_credentials", self.item(snap, "notion")["status"])
        with open(self.home / ".env", "a") as handle:
            handle.write("NOTION_TOKEN=" + SECRET + "\n")
        snap = self.snapshot()
        self.assertEqual(1, snap["summary"]["yours"])
        self.assertTrue(self.item(snap, "notion")["configured"])
        self.assertEqual("available", self.item(snap, "notion")["status"], "a credential alone is not an installed server")
        self.assertEqual([], self.item(snap, "notion")["servers"])
        self.assertNotIn(SECRET, json.dumps(snap))

    def test_existing_oauth_servers_require_the_actual_cli_dashboard_flow(self):
        config = self.config(); config["mcp_servers"]["my-tools"] = {"url": "https://example.com/mcp", "auth": "oauth"}
        (self.home / "config.yaml").write_text(yaml.safe_dump(config))
        snap = self.snapshot()
        server = self.item(snap, "custom")["servers"][0]
        self.assertEqual("oauth_operator", server["auth_method"])
        self.assertFalse([a for a in snap["actions"] if a["id"] == "test_connection" and a["target"] == "custom"])
        with mock.patch.object(integrations, "probe_mcp", side_effect=AssertionError("OAuth probe must not run")):
            result = integrations._probe_server(integrations._services(self.ctx), config["mcp_servers"]["my-tools"])
        self.assertFalse(result["ok"])
        self.assertIn("CLI", result["error"])


class ConnectionFreshnessTests(Base):
    def setUp(self):
        super().setUp()
        self.probe = mock.patch.object(integrations, "_probe_server", return_value={
            "ok": True, "count": 1, "tools": ["read_stats"], "server": "synthetic", "error": None})
        self.probe.start(); self.addCleanup(self.probe.stop)
        self.act("install", {"template": "custom", "name": "fresh", "url": "https://example.com/mcp",
                             "token": SECRET, "enabled": False})

    def server(self):
        return next(s for s in self.item(self.snapshot(), "custom")["servers"] if s["name"] == "fresh")

    def _run_probe(self):
        return self.act("test_connection", {"template": "custom", "name": "fresh"}, label="Test fresh")

    def test_save_disabled_test_enable_preserves_connectivity_evidence_without_claiming_runtime(self):
        self.assertFalse(self.server()["enabled"])
        self.assertIsNone(self.server()["last_test"])
        self._run_probe()
        self.assertTrue(self.server()["last_test"]["ok"])
        self.act("toggle_server", {"template": "custom", "name": "fresh", "enabled": True}, label="Enable fresh")
        server = self.server()
        self.assertTrue(server["enabled"])
        self.assertTrue(server["last_test"]["ok"])
        self.assertEqual("unknown", server["runtime_status"])
        dumped = json.dumps(self.snapshot())
        private = integrations._TEST_RESULTS[integrations._test_key(self.ctx, "fresh")]["fingerprint"]
        self.assertNotIn(private, dumped); self.assertNotIn(SECRET, dumped)

    def test_url_header_and_credential_changes_drop_old_evidence(self):
        for field, value in (("url", "https://other.example/mcp"), ("headers", {"Authorization": "Bearer changed-secret"}),
                             ("timeout", 60)):
            self._run_probe()
            config = self.config(); config["mcp_servers"]["fresh"][field] = value
            (self.home / "config.yaml").write_text(yaml.safe_dump(config))
            self.assertIsNone(self.server()["last_test"], field)
        self._run_probe()
        # Replace the saved credential of a server that still references it.
        config = self.config(); config["mcp_servers"]["fresh"]["headers"] = {"Authorization": "Bearer ${MCP_FRESH_TOKEN}"}
        (self.home / "config.yaml").write_text(yaml.safe_dump(config))
        self._run_probe()
        self.act("replace_token", {"template": "custom", "name": "fresh", "value": "replacement-secret"}, label="fresh")
        self.assertIsNone(self.server()["last_test"])

    def test_process_environment_changes_drop_evidence(self):
        config = self.config(); config["mcp_servers"]["fresh"]["headers"] = {"Authorization": "Bearer ${SCOPED_MCP_KEY}"}
        (self.home / "config.yaml").write_text(yaml.safe_dump(config))
        self.environ["SCOPED_MCP_KEY"] = "first-secret"; self._run_probe()
        self.environ["SCOPED_MCP_KEY"] = "changed-secret"
        self.assertIsNone(self.server()["last_test"])

    def test_remove_reinstall_never_reuses_old_test_even_with_identical_connection(self):
        self._run_probe()
        self.act("remove_server", {"template": "custom", "name": "fresh"}, label="fresh")
        self.act("install", {"template": "custom", "name": "fresh", "url": "https://example.com/mcp",
                             "token": SECRET, "enabled": False})
        self.assertIsNone(self.server()["last_test"])


class ReportTests(Base):
    def test_create_report_through_the_scheduler_service(self):
        snap = self.snapshot("reports")
        template = next(t for t in snap["templates"] if t["id"] == "zalo-oa-weekly")
        self.assertEqual("needs_connector", template["readiness"])
        create = self.descriptor(snap, "create_report", "zalo-oa-weekly")
        self.assertTrue(next(f for f in create["fields"] if f["name"] == "paused")["value"])
        result = self.act("create_report", {"template": "zalo-oa-weekly", "account": "Hermes Café OA",
                                            "focus": "", "schedule": "every monday 8am", "language": "vi",
                                            "notes": "Compare with last month.", "paused": False}, section="reports")
        self.assertTrue(result["ok"])
        self.assertIn("Report", result["message"])
        jobs = json.loads((self.home / "cron" / "jobs.json").read_text())["jobs"]
        self.assertEqual(1, len(jobs))
        job = jobs[0]
        self.assertEqual("Report · Zalo OA weekly · Hermes Café OA", job["name"])
        self.assertEqual("local", job["deliver"])
        self.assertTrue(job["prompt"].startswith("Hermes report: zalo-oa-weekly\n"))
        self.assertIn("Vietnamese", job["prompt"])
        self.assertIn("Người quan tâm", job["prompt"])
        self.assertIn("read-only report", job["prompt"])
        self.assertIn("never invent numbers", job["prompt"])
        self.assertIn("Owner notes: Compare with last month.", job["prompt"])
        self.assertEqual({"kind": "cron", "expr": "0 8 * * 1", "display": "every monday 8am"}, job["schedule"])
        # Listed on Reports with its template, and still a normal job on the Schedules page.
        snap = self.snapshot("reports")
        self.assertEqual(1, snap["total"])
        item = snap["items"][0]
        self.assertEqual(("zalo-oa-weekly", "Hermes Café OA"), (item["template"], item["account"]))
        self.assertEqual(1, next(t for t in snap["templates"] if t["id"] == "zalo-oa-weekly")["reports"])
        self.assertTrue(self.descriptor(snap, "pause_routine", item["id"]))
        self.assertTrue(self.descriptor(snap, "delete_routine", item["id"]))
        self.assertFalse([a for a in snap["actions"] if a["id"] == "edit_routine"])
        routines = AutomationAdmin(ControlStore(self.home)).snapshot("routines")
        self.assertEqual([item["id"]], [i["id"] for i in routines["items"]])

    def test_job_actions_are_scoped_to_reports(self):
        self.act("create_report", {"template": "daily-social-summary", "account": "Acme", "schedule": "0 8 * * *",
                                   "language": "en", "paused": False}, section="reports")
        admin = AutomationAdmin(ControlStore(self.home))
        routines = admin.snapshot("routines")
        create = next(a for a in routines["actions"] if a["id"] == "create_routine")
        admin.action("routines", {"action": "create_routine", "expected_revision": create["revision"], "confirm": True,
                                  "values": {"name": "Personal reminder", "schedule": "every 1d", "prompt": "Hi"}})
        snap = self.snapshot("reports")
        self.assertEqual(["Report · Daily social · Acme"], [i["name"] for i in snap["items"]])
        report = snap["items"][0]["id"]
        other = next(i["id"] for i in admin.snapshot("routines")["items"] if i["name"] == "Personal reminder")
        self.assertFalse([a for a in snap["actions"] if a.get("target") == other])
        with self.assertRaisesRegex(AdminError, "no longer exists"):
            integrations.reports_action(self.ctx, {"action": "delete_routine", "expected_revision": snap["revision"],
                                                   "confirm": True, "values": {"id": other}})
        self.act("pause_routine", {"id": report}, section="reports", target=report)
        job = next(j for j in json.loads((self.home / "cron" / "jobs.json").read_text())["jobs"] if j["id"] == report)
        self.assertEqual("paused", job["state"])
        self.act("delete_routine", {"id": report}, section="reports", target=report)
        self.assertEqual(0, self.snapshot("reports")["total"])

    def test_inputs_are_validated(self):
        snap = self.snapshot("reports")
        revision = snap["revision"]
        good = {"template": "competitor-watch", "account": "Rival A, Rival B", "schedule": "every friday 4pm",
                "language": "en"}
        for change, message in (({"language": "fr"}, "Vietnamese or English"), ({"account": ""}, "required"),
                                ({"schedule": "whenever"}, "schedule"), ({"template": "nope"}, "Unknown report"),
                                ({"notes": "x" * 1001}, "1000"), ({"paused": "yes"}, "true or false")):
            with self.assertRaisesRegex(AdminError, "(?i)" + message):
                self.act("create_report", dict(good, **change), revision=revision, section="reports")
        with self.assertRaises(AdminError):
            self.act("create_report", good, revision="0" * 64, section="reports")
        self.assertFalse((self.home / "cron" / "jobs.json").exists())
        ready = next(t for t in snap["templates"] if t["id"] == "competitor-watch")
        self.assertEqual("ready", ready["readiness"], "competitor watch can use web search alone")

    def test_readiness_follows_enabled_connectors(self):
        self.act("toggle_server", {"template": "zalo-oa", "name": "zalo-oa", "enabled": True})
        template = next(t for t in self.snapshot("reports")["templates"] if t["id"] == "zalo-oa-weekly")
        self.assertEqual("ready", template["readiness"])
        self.assertIn("Zalo Official Account", template["readiness_label"])


class ReportFlowTests(Base):
    def enable_zalo(self):
        self.act("toggle_server", {"template": "zalo-oa", "name": "zalo-oa", "enabled": True})

    def create(self, template, revision=None, **values):
        return self.act("create_report", dict({"template": template, "account": "Hermes Café"}, **values),
                        revision=revision, section="reports", target=template)

    def jobs(self):
        return json.loads((self.home / "cron" / "jobs.json").read_text())["jobs"]

    def test_new_report_fields_follow_the_steps_and_offer_only_enabled_connectors(self):
        self.enable_zalo()
        snap = self.snapshot("reports")
        featured = [t["name"] for t in snap["templates"] if t["featured"]]
        self.assertEqual(["Daily social summary", "Weekly work digest", "Mentions watch", "KPI snapshot", "Custom report"],
                         featured)
        create = self.descriptor(snap, "create_report", "mentions-to-reply")
        names = [f["name"] for f in create["fields"]]
        self.assertEqual(["template", "account", "use.zalo-oa", "schedule_preset", "schedule", "delivery", "language",
                          "focus", "notes", "paused"], names)
        preset = next(f for f in create["fields"] if f["name"] == "schedule_preset")
        self.assertEqual(["template", "daily-8", "weekdays-8", "monday-8", "custom"], [o["value"] for o in preset["options"]])
        delivery = next(f for f in create["fields"] if f["name"] == "delivery")
        self.assertEqual(["local"], [o["value"] for o in delivery["options"]])
        self.assertIn("Instagram", create["description"], "connectors that are not enabled are named")
        social = self.descriptor(snap, "create_report", "daily-social-summary")
        self.assertFalse([f for f in social["fields"] if f["name"].startswith("use.")], "nothing social is enabled")

    def test_schedule_presets_and_custom_schedule(self):
        self.enable_zalo()
        for preset, expr in (("daily-8", "0 8 * * *"), ("weekdays-8", "0 8 * * 1-5"), ("monday-8", "0 8 * * 1")):
            self.create("kpi-snapshot", schedule_preset=preset, schedule="ignored for presets")
            self.assertEqual(expr, self.jobs()[-1]["schedule"]["expr"])
        self.create("kpi-snapshot", schedule_preset="template")
        self.assertEqual("0 8 * * *", self.jobs()[-1]["schedule"]["expr"])
        self.create("kpi-snapshot", schedule_preset="custom", schedule="0 18 * * 5")
        self.assertEqual("0 18 * * 5", self.jobs()[-1]["schedule"]["expr"])
        self.assertTrue(all(job["deliver"] == "local" for job in self.jobs()))

    def test_new_report_is_validated_server_side(self):
        self.enable_zalo()
        revision = self.snapshot("reports")["revision"]
        good = {"template": "mentions-to-reply", "account": "Café", "schedule_preset": "daily-8", "delivery": "local",
                "use.zalo-oa": True}
        for change, message in (({"schedule_preset": "hourly"}, "schedule options"),
                                ({"schedule_preset": "custom", "schedule": ""}, "Schedule is required"),
                                ({"schedule_preset": "custom", "schedule": "whenever"}, "schedule"),
                                ({"delivery": "telegram"}, "listed delivery targets"), ({"delivery": 3}, "where to send"),
                                ({"use.instagram": True}, "not enabled"),
                                ({"use.gmail": True}, "not used by the report template"),
                                ({"use.zalo-oa": "yes"}, "true or false"),
                                ({"use.zalo-oa": False}, "at least one enabled connector"),
                                ({"surprise": 1}, "Unsupported field")):
            with self.assertRaisesRegex(AdminError, "(?i)" + message):
                self.act("create_report", dict(good, **change), revision=revision, section="reports")
        self.assertFalse((self.home / "cron" / "jobs.json").exists(), "nothing is written on a refused request")
        with self.assertRaisesRegex(AdminError, "cover"):
            self.act("create_report", {"template": "custom", "account": "Pricing", "schedule_preset": "daily-8"},
                     revision=revision, section="reports")
        # Unticking every connector is allowed for a paused report.
        self.act("create_report", dict(good, **{"use.zalo-oa": False, "paused": True}), revision=revision,
                 section="reports")
        self.assertEqual("paused", self.jobs()[0]["state"])

    def test_prompt_names_only_the_chosen_connectors(self):
        self.enable_zalo()
        self.create("mentions-to-reply", schedule_preset="daily-8", **{"use.zalo-oa": True})
        prompt = self.jobs()[0]["prompt"]
        self.assertIn("the connected tools for Zalo Official Account.", prompt)
        self.assertNotIn("Instagram", prompt)
        self.create("custom", schedule_preset="daily-8", notes="Track our coffee bean prices.")
        prompt = self.jobs()[1]["prompt"]
        self.assertTrue(prompt.startswith("Hermes report: custom\n"))
        self.assertIn("web search and any enabled connected tools", prompt)
        self.assertIn("Owner notes: Track our coffee bean prices.", prompt)

    def test_saved_reports_are_listed_newest_first_and_readable(self):
        self.create("competitor-watch", schedule_preset="template")
        self.create("kpi-snapshot", schedule_preset="daily-8")
        rival, kpi = (job["id"] for job in self.jobs())
        for job_id, name, text in ((rival, "2026-10-30_16-00-00.md", "# Competitor watch · week 44\n\n- **Rival A** ran a sale"),
                                   (kpi, "2026-10-31_08-00-00.md", "# KPI snapshot\n\nKey: OPENROUTER_API_KEY=" + SECRET),
                                   (kpi, "2026-10-29_08-00-00.md", "# Older")):
            folder = self.home / "cron" / "output" / job_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(text, encoding="utf-8")
        (self.home / "cron" / "output" / kpi / "notes.txt").write_text("not a report")
        snap = self.snapshot("reports")
        self.assertEqual(["2026-10-31_08-00-00.md", "2026-10-30_16-00-00.md", "2026-10-29_08-00-00.md"],
                         [row["file"] for row in snap["saved"]])
        self.assertEqual(3, snap["summary"]["saved"])
        self.assertNotIn("files", snap["items"][0], "file lists stay internal")
        reader = snap["reader"]
        self.assertEqual(("KPI snapshot", kpi), (reader["title"], reader["job"]))
        self.assertNotIn(SECRET, reader["text"], "saved output is redacted")
        opened = integrations.reports_snapshot(self.ctx, {"job": rival, "file": "2026-10-30_16-00-00.md"})["reader"]
        self.assertIn("**Rival A**", opened["text"])
        self.assertEqual("Competitor watch · week 44", opened["title"])
        for job, name in ((rival, "../../config.yaml"), (rival, "2026-10-31_08-00-00.md"), ("../x", "2026-10-30_16-00-00.md"),
                          (kpi, "notes.txt"), ("nope", "2026-10-31_08-00-00.md")):
            snap = integrations.reports_snapshot(self.ctx, {"job": job, "file": name})
            self.assertIsNone(snap["reader"])
            self.assertTrue(snap["reader_error"])
        with self.assertRaisesRegex(AdminError, "Unsupported filter"):
            console_sections.clean_query(console_sections.get("reports"), {"path": "/etc/passwd"})

    def test_reader_text_is_bounded(self):
        self.create("kpi-snapshot", schedule_preset="daily-8")
        job = self.jobs()[0]["id"]
        folder = self.home / "cron" / "output" / job
        folder.mkdir(parents=True)
        (folder / "2026-10-31_08-00-00.md").write_text("# Big\n" + "- a line of text\n" * (integrations.READER_MAX_CHARS // 10))
        reader = self.snapshot("reports")["reader"]
        self.assertTrue(reader["truncated"])
        self.assertEqual(integrations.READER_MAX_CHARS, len(reader["text"]))

    def test_reports_can_be_delivered_to_a_configured_home_chat(self):
        create = self.descriptor(self.snapshot("reports"), "create_report", "kpi-snapshot")
        field = next(f for f in create["fields"] if f["name"] == "delivery")
        self.assertEqual((["local"], "local"), ([o["value"] for o in field["options"]], field["value"]))
        with open(self.home / ".env", "a", encoding="utf-8") as env:
            env.write("TELEGRAM_HOME_CHANNEL=55501234\nTELEGRAM_BOT_TOKEN=123456:" + "B" * 35 + "\n")
        snap = self.snapshot("reports")
        field = next(f for f in self.descriptor(snap, "create_report", "kpi-snapshot")["fields"] if f["name"] == "delivery")
        self.assertEqual((["local", "telegram"], "telegram"), ([o["value"] for o in field["options"]], field["value"]))
        self.assertNotIn("55501234", json.dumps(snap))
        self.create("kpi-snapshot", schedule_preset="daily-8", delivery="telegram")
        self.assertEqual("telegram:55501234", self.jobs()[0]["deliver"])
        item = self.snapshot("reports")["items"][0]
        self.assertEqual("Telegram · home chat", item["deliver_label"])
        for bad in ("zalo", "telegram:55501234", "origin"):
            with self.assertRaisesRegex(AdminError, "listed delivery targets"):
                self.create("kpi-snapshot", schedule_preset="daily-8", delivery=bad)

    def test_report_delivery_uses_the_context_environment_without_exposing_it(self):
        self.environ.update(TELEGRAM_HOME_CHANNEL="55506789", TELEGRAM_BOT_TOKEN=SECRET)
        snap = self.snapshot("reports")
        field = next(f for f in self.descriptor(snap, "create_report", "kpi-snapshot")["fields"] if f["name"] == "delivery")
        self.assertEqual(["local", "telegram"], [o["value"] for o in field["options"]])
        self.assertNotIn("55506789", json.dumps(snap))
        self.assertNotIn(SECRET, json.dumps(snap))
        self.create("kpi-snapshot", schedule_preset="daily-8", delivery="telegram", paused=True)
        self.assertEqual("telegram:55506789", self.jobs()[0]["deliver"])

    def test_kpi_snapshot_prompt_writes_the_briefing_file(self):
        self.create("kpi-snapshot", schedule_preset="daily-8")
        prompt = self.jobs()[0]["prompt"]
        self.assertIn("console/kpis.json", prompt)
        self.assertIn('"good_when": "up"', prompt)
        self.assertIn("at most 8 items", prompt)
        self.assertLess(len(prompt), integrations.MAX_PROMPT)
        self.assertTrue(self.create("daily-social-summary", schedule_preset="daily-8", paused=True))
        self.assertNotIn("kpis.json", self.jobs()[1]["prompt"])

    def test_connectors_are_grouped_by_status(self):
        snap = self.snapshot()
        groups = {item["id"]: item["group"] for item in snap["items"]}
        self.assertEqual("needs_setup", groups["zalo-oa"], "installed but disabled")
        self.assertEqual("available", groups["instagram"])
        self.enable_zalo()
        snap = self.snapshot()
        self.assertEqual("configured", self.item(snap, "zalo-oa")["group"])
        summary = snap["summary"]
        self.assertEqual(summary["total"], summary["configured"] + summary["needs_setup"] + summary["available"])
        self.assertLess(len(snap["message"]), 140, "one compact line")


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.ctx = ConsoleContext(home=self.home, mode="demo", assistant_dir=self.home, environ={"NOTION_TOKEN": "real"})
        self.addCleanup(integrations._DEMO_SERVICES.pop, str(self.home), None)

    def test_demo_is_synthetic_and_in_memory(self):
        with mock.patch.object(integrations, "probe_mcp", side_effect=AssertionError("no network in demo")), \
                mock.patch("socket.create_connection", side_effect=AssertionError("no network in demo")):
            snap = integrations.connectors_snapshot(self.ctx, {})
            self.assertEqual("demo", snap["source"])
            status = {i["id"]: i["status"] for i in snap["items"]}
            self.assertEqual(("enabled", "installed", "enabled"), (status["github"], status["zalo-oa"], status["facebook-pages"]))
            test = next(a for a in snap["actions"] if a["id"] == "test_connection" and a["label"] == "Test github")
            result = integrations.connectors_action(self.ctx, {"action": "test_connection", "expected_revision": test["revision"],
                                                               "confirm": True, "values": {"template": "github", "name": "github"}})
            self.assertTrue(result["test"]["ok"])
            self.assertIn("demo only", result["message"])
            install = next(a for a in snap["actions"] if a["id"] == "install" and a["target"] == "threads")
            integrations.connectors_action(self.ctx, {"action": "install", "expected_revision": install["revision"],
                                                      "confirm": True, "values": {"template": "threads", "name": "threads",
                                                                                  "url": "http://127.0.0.1:8792/mcp",
                                                                                  "enabled": True}})
            self.assertEqual("enabled", {i["id"]: i["status"] for i in integrations.connectors_snapshot(self.ctx, {})["items"]}["threads"])
            reports = integrations.reports_snapshot(self.ctx, {})
            self.assertEqual(4, reports["total"])
            daily_job = next(i for i in reports["items"] if i["template"] == "daily-social-summary")
            self.assertEqual("Telegram · Owner chat (demo)", daily_job["deliver_label"])
            self.assertEqual(4, len(reports["saved"]))
            self.assertEqual(sorted((r["file"] for r in reports["saved"]), reverse=True), [r["file"] for r in reports["saved"]])
            self.assertEqual(reports["saved"][0]["file"], reports["reader"]["file"], "the newest report opens by default")
            daily_saved = next(r for r in reports["saved"] if r["file"] == "2026-10-30_08-00-09.md")
            opened = integrations.reports_snapshot(self.ctx, {"job": daily_saved["job"], "file": daily_saved["file"]})
            self.assertIn("30 Oct 2026", opened["reader"]["title"])
            self.assertIn("| Metric | Value | Change |", opened["reader"]["text"])
            self.assertIsNone(opened["reader_error"])
            self.assertTrue(integrations.saved_reports(self.ctx, 2))
            daily = next(i for i in reports["items"] if i["template"] == "daily-social-summary")
            self.assertIn("synthetic", daily["latest_output"]["excerpt"])
            self.assertEqual("ready", next(t for t in reports["templates"] if t["id"] == "daily-social-summary")["readiness"])
            create = next(a for a in reports["actions"] if a["id"] == "create_report")
            integrations.reports_action(self.ctx, {"action": "create_report", "expected_revision": create["revision"],
                                                   "confirm": True, "values": {"template": create["target"], "account": "Demo",
                                                                               "schedule": "every 1d"}})
            self.assertEqual(5, integrations.reports_snapshot(self.ctx, {})["total"])
        self.assertEqual([], list(self.home.iterdir()), "demo never writes to disk")
        notion = next(i for i in integrations.connectors_snapshot(self.ctx, {})["items"] if i["id"] == "notion")
        self.assertEqual("home .env", notion["credentials"][0]["source"], "demo ignores the real process environment")


if __name__ == "__main__":
    unittest.main()
