"""Offline deployment checks. No real credentials, APIs or bot data are used."""
from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import yaml

from hermes_zalo import container_runtime as runtime


ROOT = Path(__file__).resolve().parent.parent


class ContainerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.values = {"BOT_DATA_DIR": str(self.root), "ZALO_BOT_TOKEN": "fake-local-token",
                       "ZALO_ALLOWED_USERS": "admin", "HERMES_PROVIDER": "openrouter",
                       "HERMES_MODEL": "synthetic-model", "OPENROUTER_API_KEY": "fake-key-only"}

    def prepare(self):
        return runtime.prepare(self.values, ROOT)

    def test_restart_preserves_database_and_restores_restricted_config(self):
        _, config = self.prepare()
        database = Path(self.values["ZALO_STATE_DIR"]) / "test.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE approvals (member TEXT)")
            connection.execute("INSERT INTO approvals VALUES ('synthetic-member')")
            connection.commit()
        home = Path(self.values["HERMES_HOME"])
        config["mcp_servers"] = {"wrong": {"command": "unused"}}
        config["platform_toolsets"]["zalo"] = ["terminal"]
        config["gateway"]["platforms"]["discord"] = {"enabled": True}
        (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        (home / "plugins/zalo-platform/hermes_zalo/stale.py").write_text("# old code")
        _, result = self.prepare()
        self.assertEqual({}, result["mcp_servers"])
        self.assertEqual({"zalo": [], "cli": []}, result["platform_toolsets"])
        self.assertEqual({"zalo": {"enabled": True}}, result["gateway"]["platforms"])
        self.assertFalse(result["memory"]["memory_enabled"])
        self.assertTrue(result["group_sessions_per_user"])
        self.assertFalse(result["security"]["allow_lazy_installs"])
        self.assertEqual("synthetic-model", result["model"]["default"])
        self.assertFalse((home / "plugins/zalo-platform/hermes_zalo/stale.py").exists())
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(("synthetic-member",), connection.execute("SELECT member FROM approvals").fetchone())

    def test_model_kept_without_override_and_explicit_change_applied(self):
        self.prepare()
        del self.values["HERMES_MODEL"]
        _, config = self.prepare()
        self.assertEqual("synthetic-model", config["model"]["default"])
        self.values["HERMES_MODEL"] = "new-synthetic-model"
        self.assertEqual("new-synthetic-model", self.prepare()[1]["model"]["default"])

    def test_railway_refuses_nonpersistent_or_wrong_mount(self):
        for mount in (None, str(self.root / "elsewhere")):
            values = dict(self.values, RAILWAY_SERVICE_ID="fake-service")
            if mount:
                values["RAILWAY_VOLUME_MOUNT_PATH"] = mount
            with self.assertRaises(runtime.ContainerError):
                runtime.paths(values)
        values["RAILWAY_VOLUME_MOUNT_PATH"] = str(self.root)
        self.assertEqual(self.root, runtime.paths(values))

    def test_provider_change_clears_previous_provider_routing_and_auth(self):
        _, config = self.prepare()
        config["model"].update(base_url="https://previous.invalid", api_key="old-private-key", api_mode="old")
        home = Path(self.values["HERMES_HOME"])
        (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        self.values.update(HERMES_PROVIDER="openai", HERMES_MODEL="synthetic-new-model")
        _, config = self.prepare()
        for key in ("base_url", "api_key", "api_mode"):
            self.assertNotIn(key, config["model"])
        self.values["HERMES_BASE_URL"] = "https://new.invalid"
        self.assertEqual("https://new.invalid", self.prepare()[1]["model"]["base_url"])

    def test_plugin_ancestor_link_is_rejected_before_writing(self):
        home = self.root / "hermes-home"
        home.mkdir()
        destination = self.root / "elsewhere"
        destination.mkdir()
        try:
            (home / "plugins").symlink_to(destination, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks requires OS privileges")
        with self.assertRaises(runtime.ContainerError):
            self.prepare()
        self.assertEqual([], list(destination.iterdir()))

    def test_windows_relative_and_escaping_paths_fail(self):
        for value in (".runtime/tickets", "C:\\private\\tickets", str(self.root.parent / "outside")):
            with self.subTest(value=value), self.assertRaises(runtime.ContainerError):
                runtime.paths(dict(self.values, TICKETS_DATA_DIR=value))

    def test_old_home_env_refused_without_deleting_it(self):
        home = self.root / "hermes-home"
        home.mkdir()
        old = home / ".env"
        old.write_text("OPENROUTER_API_KEY=do-not-expose\n")
        with self.assertRaises(runtime.ContainerError):
            self.prepare()
        self.assertTrue(old.exists())

    def test_google_json_materialized_privately_not_put_in_config(self):
        info = {"type": "service_account", "client_email": "synthetic@example.invalid",
                "project_id": "synthetic", "private_key": "synthetic-secret-key",
                "token_uri": "https://oauth2.googleapis.com/token"}
        self.values["ZALO_GOOGLE_CREDENTIALS_SECRET_JSON"] = json.dumps(info)
        _, config = self.prepare()
        target = Path(self.values["ZALO_GOOGLE_CREDENTIALS"])
        self.assertEqual(info, json.loads(target.read_text()))
        self.assertNotIn("synthetic-secret-key", yaml.safe_dump(config))
        if os.name != "nt":
            self.assertEqual(0o600, target.stat().st_mode & 0o777)

    def test_bad_json_does_not_leak_or_overwrite_existing_key(self):
        runtime.paths(self.values)
        target = Path(self.values["ZALO_GOOGLE_CREDENTIALS"])
        target.parent.mkdir()
        target.write_text("existing private key")
        self.values["ZALO_GOOGLE_CREDENTIALS_SECRET_JSON"] = "private-value-not-json"
        with self.assertRaises(runtime.ContainerError) as caught:
            runtime.credentials(self.values)
        self.assertNotIn("private-value-not-json", str(caught.exception))
        self.assertEqual("existing private key", target.read_text())

    def test_token_endpoint_in_json_must_be_google(self):
        self.values["ZALO_GOOGLE_CREDENTIALS_SECRET_JSON"] = json.dumps({
            "type": "service_account", "client_email": "fake", "private_key": "fake",
            "project_id": "fake", "token_uri": "https://example.invalid/token"})
        with self.assertRaises(runtime.ContainerError):
            self.prepare()

    def test_offline_validation_rejects_missing_provider_key(self):
        _, config = self.prepare()
        del self.values["OPENROUTER_API_KEY"]
        with self.assertRaisesRegex(runtime.ContainerError, "OPENROUTER_API_KEY"):
            runtime.validate(self.values, config)

    def test_registration_admin_must_be_allowed(self):
        _, config = self.prepare()
        self.values.update(ZALO_SHEETS_ID="synthetic-sheet-id", REGISTRATION_ENABLED="true",
                           REGISTRATION_ADMIN_USERS="stranger", REGISTRATION_WORKLOAD_TAB="Workload",
                           REGISTRATION_WORKLOAD_NAME_COLUMN="D", REGISTRATION_WORKLOAD_PHONE_COLUMN="C",
                           REGISTRATION_WORKLOAD_HEADER_ROW="5", REGISTRATION_WORKLOAD_NAME_HEADER="Name",
                           REGISTRATION_WORKLOAD_PHONE_HEADER="Phone")
        with self.assertRaisesRegex(runtime.ContainerError, "REGISTRATION_ADMIN_USERS"):
            runtime.validate(self.values, config)

    def test_standby_never_prepares_or_launches_gateway(self):
        with patch.dict(os.environ, {"BOT_DATA_DIR": str(self.root)}, clear=True), \
                patch.object(runtime, "standby", return_value=0) as wait, \
                patch.object(runtime, "prepare") as prepare, patch.object(os, "execve") as launch:
            self.assertEqual(0, runtime.main(["gateway", "run"]))
            wait.assert_called_once()
            prepare.assert_not_called()
            launch.assert_not_called()

    def test_check_does_not_launch_or_call_network(self):
        prepare = runtime.prepare
        with patch.dict(os.environ, self.values, clear=True), \
                patch.object(runtime, "prepare", side_effect=lambda v: prepare(v, ROOT)), \
                patch.object(os, "execve") as launch, patch("httpx.Client.request") as network, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(0, runtime.main(["check"]))
            launch.assert_not_called()
            network.assert_not_called()

    def test_active_exec_preserves_signal_path_and_environment(self):
        prepare = runtime.prepare
        launcher = self.root / "fake-launcher"
        launcher.touch()
        self.values.update(BOT_START_ENABLED="true", HERMES_EXECUTABLE=str(launcher))
        with patch.dict(os.environ, self.values, clear=True), \
                patch.object(runtime, "prepare", side_effect=lambda v: prepare(v, ROOT)), \
                patch.object(os, "execve") as launch, redirect_stdout(io.StringIO()):
            self.assertEqual(0, runtime.main(["gateway", "run"]))
            executable, args, child = launch.call_args.args
            self.assertEqual(str(launcher), executable)
            self.assertEqual([str(launcher), "gateway", "run"], args)
            self.assertEqual(str(self.root / "tickets"), child["TICKETS_DATA_DIR"])
            self.assertEqual("false", child["API_SERVER_ENABLED"])
            self.assertEqual("false", child["GATEWAY_MULTIPLEX_PROFILES"])
            self.assertEqual("1", child["HERMES_GATEWAY_EXTERNAL_SUPERVISOR"])

    def test_malformed_yaml_error_is_redacted(self):
        home = self.root / "hermes-home"
        home.mkdir()
        (home / "config.yaml").write_text("model: [VERY_PRIVATE_SECRET\n")
        prepare = runtime.prepare
        output = io.StringIO()
        with patch.dict(os.environ, self.values, clear=True), \
                patch.object(runtime, "prepare", side_effect=lambda v: prepare(v, ROOT)), redirect_stderr(output):
            self.assertEqual(2, runtime.main(["check"]))
        self.assertNotIn("VERY_PRIVATE_SECRET", output.getvalue())


if __name__ == "__main__":
    unittest.main()
