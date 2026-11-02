from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from assistant_core.quota import VIETNAM_TZ, QuotaStore, person_key
from assistant_core.telemetry import TelemetryStore
from hermes_zalo.admin_assistant import (COST_SCOPE, REPO_ROOT, ROW_LIMIT, SECTIONS, TELEMETRY_COLUMNS,
                                         AssistantAdmin, redact)


SECRET = "sk-live-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
COMMON_KEYS = {"section", "available", "source", "message", "items", "updated_at"}


class Work:
    class settings:
        bot_mode = "assistant"


def tree(root: Path):
    """Every path under root with size and mtime: detects created, deleted or modified files."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            stat = path.lstat()
            out[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return out


def write_telemetry(home: Path, calls=3, *, keep_open=False):
    store = TelemetryStore(home / "assistant-core" / "telemetry.sqlite3", run_id="r1", hermes_rev="abc1234def")
    for i in range(calls):
        store.post_api_request(platform="zalo", session_id="s", api_request_id=f"q{i}", provider="openrouter",
                               model="m-small" if i % 2 else "m-large", api_duration=1.5,
                               usage={"input_tokens": 100, "output_tokens": 10})
    store.api_request_error(platform="zalo", api_request_id="err", provider="openrouter", model="m-large",
                            error_type="RateLimitError", status_code=429)
    if keep_open:
        return store
    store.close()
    return None


def write_state(home: Path, *, costs=True):
    db = sqlite3.connect(home / "state.db", isolation_level=None)
    db.execute("PRAGMA journal_mode=WAL")
    cost_cols = ", estimated_cost_usd REAL, actual_cost_usd REAL" if costs else ""
    db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, "
               "system_prompt TEXT, started_at REAL NOT NULL, ended_at REAL" + cost_cols + ")")
    for i in range(3):
        values = [f"s{i}", "zalo", f"user-{i}", "SYSTEM PROMPT TEXT", 1767600000 + i, None if i == 2 else 1767600100]
        if costs:
            values += [0.01, None]
        db.execute(f"INSERT INTO sessions VALUES ({','.join('?' * len(values))})", values)
    db.close()


def write_skill(home: Path, rel: str, frontmatter: str, body="Follow these private instructions."):
    path = home / "skills" / rel
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")


def populate(home: Path):
    (home / "config.yaml").write_text("terminal:\n  backend: docker\nskills:\n  disabled: [notes]\n", encoding="utf-8")
    write_state(home)
    write_telemetry(home)
    write_skill(home, "lab/schedule", "name: schedule\ndescription: Lab duty lookup\nversion: 1.2\ntags: [lab]")
    write_skill(home, "notes", "name: notes\ndescription: Take notes")
    (home / "cron").mkdir()
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{
        "id": "j1", "name": "Morning reminder", "prompt": "PRIVATE PROMPT " + SECRET, "script": "/opt/run.sh",
        "deliver": "zalo:12345", "base_url": "https://api.example.com/v1", "workdir": "/srv/data",
        "schedule": {"kind": "cron", "expr": "0 7 * * *", "display": "0 7 * * *"}, "enabled": True,
        "state": "scheduled", "next_run_at": "2026-01-06T07:00:00+07:00", "last_run_at": "2026-01-05T07:00:00+07:00",
        "last_status": "ok", "last_error": "token=" + SECRET, "failure_streak": 0,
        "repeat": {"times": None, "completed": 4}, "skills": ["schedule"]}]}), encoding="utf-8")
    (home / "memories").mkdir()
    (home / "memories" / "MEMORY.md").write_text("first fact\n§\nsecond fact\n", encoding="utf-8")
    (home / "memories" / "USER.md").write_text("likes tea\n", encoding="utf-8")


class AssistantAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "hermes-home"
        self.home.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def admin(self, **kw):
        return AssistantAdmin(Work(), None, home=self.home, **kw)

    def test_every_section_follows_the_contract(self):
        populate(self.home)
        admin = self.admin()
        for section in SECTIONS:
            data = admin.snapshot(section)
            self.assertLessEqual(COMMON_KEYS, set(data), section)
            self.assertEqual(data["section"], section)
            self.assertIsInstance(data["items"], list)
            self.assertTrue(data["updated_at"].endswith("+00:00"))
            json.dumps(data)  # serialisable for the web layer
        overview = admin.snapshot("overview")
        self.assertEqual(set(overview["runtime"]), {"mode", "bot_mode", "sandbox_backend", "hermes_commit", "uptime_seconds"})
        self.assertEqual(set(overview["counts"]), {"sessions", "memories", "skills", "routines"})
        self.assertEqual(set(overview["usage"]), {"calls", "input_tokens", "output_tokens", "cost_usd", "cost_status",
                                                  "cost_scope"})
        self.assertEqual(overview["daily"], admin.snapshot("usage")["daily"])
        self.assertEqual(overview["daily"][0]["calls"], 4)
        for capability in overview["capabilities"]:
            self.assertEqual(set(capability), {"id", "label", "status", "description"})
        for event in overview["activity"]:
            self.assertEqual(set(event), {"kind", "label", "status", "at"})
        self.assertEqual(overview["counts"], {"sessions": 3, "memories": 3, "skills": 2, "routines": 1})
        self.assertEqual(overview["runtime"]["bot_mode"], "assistant")
        self.assertEqual(overview["runtime"]["sandbox_backend"], "docker")
        runtime = admin.snapshot("runtime")
        self.assertEqual(set(runtime["resources"]), {"cpu_count", "load_1m", "memory_used_bytes", "memory_total_bytes",
                                                     "disk_used_bytes", "disk_total_bytes"})
        self.assertEqual(runtime["controls"], {"restart": False, "terminal": False})

    def test_missing_state_is_unavailable_not_zero(self):
        missing = self.root / "never-created"
        admin = AssistantAdmin(Work(), None, home=missing)
        overview = admin.snapshot("overview")
        self.assertEqual(overview["counts"], {"sessions": None, "memories": None, "skills": None, "routines": None})
        self.assertEqual(overview["usage"], {"calls": None, "input_tokens": None, "output_tokens": None,
                                             "cost_usd": None, "cost_status": "unavailable", "cost_scope": COST_SCOPE})
        self.assertEqual(overview["daily"], [])
        self.assertEqual({c["id"]: c["status"] for c in overview["capabilities"]}["telemetry"], "unavailable")
        for section in ("skills", "routines", "usage"):
            data = admin.snapshot(section)
            self.assertFalse(data["available"], section)
            self.assertEqual(data["items"], [])
            self.assertTrue(data["message"])
            self.assertNotIn(str(self.root), data["message"])
        self.assertIsNone(admin.snapshot("usage")["calls"])
        self.assertFalse(missing.exists())

    def test_snapshots_never_create_or_modify_files(self):
        populate(self.home)
        before = tree(self.root)
        self.assertNotIn("state.db-wal", before)
        admin = self.admin()
        for _ in range(2):
            for section in SECTIONS:
                admin.snapshot(section)
                admin.snapshot(section, "m")
        self.assertEqual(tree(self.root), before)

    def test_reads_while_gateway_holds_the_wal_open(self):
        store = write_telemetry(self.home, calls=2, keep_open=True)
        try:
            usage = self.admin().snapshot("usage")
        finally:
            store.close()
        self.assertTrue(usage["available"])
        self.assertEqual(usage["calls"], 3)

    def test_usage_reads_real_telemetry_schema(self):
        write_state(self.home)
        write_telemetry(self.home, calls=4)
        usage = self.admin().snapshot("usage")
        self.assertTrue(usage["available"])
        self.assertEqual(usage["source"], "assistant-telemetry")
        self.assertEqual((usage["calls"], usage["errors"], usage["input_tokens"], usage["output_tokens"]), (5, 1, 400, 40))
        self.assertEqual(set(usage["items"][0]), {"ts", "provider", "model", "input_tokens", "output_tokens",
                                                  "duration_s", "error_type"})
        self.assertEqual(usage["items"][0]["error_type"], "RateLimitError")
        self.assertIsNone(usage["items"][0]["input_tokens"])  # failed call: unknown, not 0
        self.assertEqual(len(usage["daily"]), 1)
        self.assertEqual(usage["daily"][0]["calls"], 5)
        self.assertEqual((usage["cost_usd"], usage["cost_status"], usage["cost_scope"]),
                         (0.03, "estimated", "all_hermes_sessions"))
        filtered = self.admin().snapshot("usage", "small")
        self.assertEqual({item["model"] for item in filtered["items"]}, {"m-small"})
        self.assertEqual(self.admin().snapshot("overview")["runtime"]["hermes_commit"], "abc1234def")

    def test_usage_without_prices_reports_null_cost(self):
        write_state(self.home, costs=False)
        write_telemetry(self.home, calls=1)
        usage = self.admin().snapshot("usage")
        self.assertIsNone(usage["cost_usd"])
        self.assertEqual(usage["cost_status"], "unavailable")

    def test_invalid_costs_are_unknown_and_overview_still_answers(self):
        write_telemetry(self.home, calls=1)
        for bad in (float("inf"), -1.0, "NaN", float("-inf")):
            with self.subTest(bad=bad):
                (self.home / "state.db").unlink(missing_ok=True)
                write_state(self.home)
                db = sqlite3.connect(self.home / "state.db")
                db.execute("UPDATE sessions SET estimated_cost_usd=? WHERE id='s1'", (bad,))
                db.commit()
                db.close()
                usage = self.admin().snapshot("usage")
                self.assertEqual((usage["cost_usd"], usage["cost_status"]), (None, "unknown"))
                overview = self.admin().snapshot("overview")
                self.assertEqual(overview["usage"]["cost_status"], "unknown")
                self.assertEqual(overview["usage"]["calls"], 2)

    def test_broken_sessions_schema_is_unknown_cost_not_an_error(self):
        write_telemetry(self.home, calls=1)
        db = sqlite3.connect(self.home / "state.db")
        db.execute("CREATE TABLE gone (x)")
        db.execute("CREATE VIEW sessions AS SELECT x AS source, x AS started_at, x AS estimated_cost_usd, "
                   "x AS actual_cost_usd FROM gone")
        db.execute("DROP TABLE gone")
        db.commit()
        db.close()
        usage = self.admin().snapshot("usage")
        self.assertTrue(usage["available"])
        self.assertEqual((usage["cost_usd"], usage["cost_status"]), (None, "unknown"))
        overview = self.admin().snapshot("overview")
        self.assertIsNone(overview["counts"]["sessions"])
        self.assertEqual({c["id"]: c["status"] for c in overview["capabilities"]}["sessions"], "unavailable")

    def test_reader_matches_telemetry_store_schema(self):
        """Contract with assistant_core.telemetry: the DB its writer creates is what the reader expects."""
        store = TelemetryStore(self.home / "assistant-core" / "telemetry.sqlite3", run_id="run-x",
                               hermes_rev="0123456789", config_hash="h")
        store.post_api_request(api_request_id="a", provider="p", model="m", api_duration=0.5,
                               usage={"input_tokens": 7, "output_tokens": 3})
        store.tool_call(tool_name="web_search", status="ok")
        store.close()
        db = sqlite3.connect(self.home / "assistant-core" / "telemetry.sqlite3")
        api_columns = {row[1] for row in db.execute("PRAGMA table_info(api_calls)")}
        run_columns = {row[1] for row in db.execute("PRAGMA table_info(runs)")}
        db.close()
        self.assertLessEqual(TELEMETRY_COLUMNS, api_columns)
        self.assertLessEqual({"hermes_rev", "started_at"}, run_columns)
        usage = self.admin().snapshot("usage")
        self.assertEqual(usage["items"], [{"ts": usage["items"][0]["ts"], "provider": "p", "model": "m",
                                           "input_tokens": 7, "output_tokens": 3, "duration_s": 0.5,
                                           "error_type": None}])
        self.assertEqual((usage["calls"], usage["input_tokens"], usage["output_tokens"]), (1, 7, 3))
        self.assertEqual(self.admin().snapshot("runtime")["hermes_commit"], "0123456789")

    def test_runtime_and_overview_work_without_work_object(self):
        populate(self.home)
        admin = AssistantAdmin(None, None, home=self.home)
        runtime = admin.snapshot("runtime")
        self.assertTrue(runtime["available"])
        self.assertIsNone(runtime["bot_mode"])
        self.assertEqual(runtime["controls"], {"restart": False, "terminal": False})
        self.assertIsNone(admin.snapshot("overview")["runtime"]["bot_mode"])

    def test_sandbox_is_reported_as_configured_not_verified(self):
        populate(self.home)
        sandbox = {c["id"]: c for c in self.admin().snapshot("overview")["capabilities"]}["sandbox"]
        self.assertEqual(sandbox["description"], "Configured backend docker; execution has not been verified.")
        (self.home / "config.yaml").write_text("terminal:\n  backend: local\n", encoding="utf-8")
        sandbox = {c["id"]: c for c in self.admin().snapshot("overview")["capabilities"]}["sandbox"]
        self.assertEqual(sandbox["status"], "warning")
        self.assertIn("execution has not been verified", sandbox["description"])

    def test_usage_rows_are_bounded(self):
        store = TelemetryStore(self.home / "assistant-core" / "telemetry.sqlite3", run_id="r")
        for i in range(ROW_LIMIT + 50):
            store.post_api_request(api_request_id=str(i), model="m", usage={"input_tokens": 1, "output_tokens": 1})
        store.close()
        usage = self.admin().snapshot("usage")
        self.assertEqual(len(usage["items"]), ROW_LIMIT)
        self.assertEqual(usage["calls"], ROW_LIMIT + 50)

    def test_unknown_or_foreign_telemetry_schema_is_unavailable(self):
        path = self.home / "assistant-core" / "telemetry.sqlite3"
        path.parent.mkdir()
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY, ts REAL, tokens INTEGER)")
        db.execute("INSERT INTO api_calls (ts, tokens) VALUES (1, 5)")
        db.commit()
        db.close()
        usage = self.admin().snapshot("usage")
        self.assertFalse(usage["available"])
        self.assertIsNone(usage["calls"])
        path.write_bytes(b"not a database")
        self.assertFalse(self.admin().snapshot("usage")["available"])

    def test_skills_expose_only_redacted_metadata(self):
        write_skill(self.home, "ops/deploy",
                    f"name: deploy\ndescription: Push to https://internal.example.com using {SECRET} "
                    "from /home/duy/.ssh/id_rsa with $DEPLOY_TOKEN, ask ops@example.com\n"
                    "env:\n  API_KEY: hidden-value\nversion: 2",
                    body="SECRET INSTRUCTIONS: run rm -rf /")
        items = self.admin().snapshot("skills")["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(set(items[0]), {"id", "name", "category", "description", "version", "tags", "enabled"})
        text = json.dumps(items)
        for leaked in ("internal.example.com", SECRET, "/home/duy", "id_rsa", "DEPLOY_TOKEN", "ops@example.com",
                       "hidden-value", "SECRET INSTRUCTIONS", "rm -rf"):
            self.assertNotIn(leaked, text)
        self.assertIn("[url]", items[0]["description"])
        self.assertEqual(items[0]["category"], "ops")

    def test_skill_symlinks_are_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text("---\nname: leaked\ndescription: outside file\n---\n", encoding="utf-8")
        write_skill(self.home, "real", "name: real")
        (self.home / "skills" / "linked-dir").symlink_to(outside, target_is_directory=True)
        (self.home / "skills" / "linked-file").mkdir()
        (self.home / "skills" / "linked-file" / "SKILL.md").symlink_to(outside / "SKILL.md")
        skills = self.admin().snapshot("skills")
        self.assertEqual([item["name"] for item in skills["items"]], ["real"])
        self.assertEqual(skills["skipped"], 1)
        self.assertNotIn("leaked", json.dumps(skills))
        self.home.joinpath("skills").rename(self.root / "real-skills")
        (self.home / "skills").symlink_to(self.root / "real-skills", target_is_directory=True)
        self.assertFalse(self.admin().snapshot("skills")["available"])

    def test_symlinked_home_sources_are_not_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        populate(outside)
        for name in ("config.yaml", "state.db", "cron", "memories"):
            (self.home / name).symlink_to(outside / name, target_is_directory=(outside / name).is_dir())
        overview = self.admin().snapshot("overview")
        self.assertIsNone(overview["counts"]["sessions"])
        self.assertIsNone(overview["counts"]["memories"])
        self.assertIsNone(overview["counts"]["routines"])
        self.assertIsNone(overview["runtime"]["sandbox_backend"])
        self.assertFalse(self.admin().snapshot("routines")["available"])
        self.assertNotIn("Morning reminder", json.dumps(overview))
        linked = self.root / "linked-home"
        linked.symlink_to(outside, target_is_directory=True)
        admin = AssistantAdmin(Work(), None, home=linked, assistant_data_dir=self.root / "no-data")
        self.assertFalse(admin.snapshot("skills")["available"])
        self.assertFalse(admin.snapshot("routines")["available"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs are POSIX-only")
    def test_fifo_sources_do_not_block_and_are_unavailable(self):
        (self.home / "cron").mkdir()
        os.mkfifo(self.home / "cron" / "jobs.json")
        os.mkfifo(self.home / "config.yaml")
        self.assertFalse(self.admin().snapshot("routines")["available"])
        self.assertIsNone(self.admin().snapshot("runtime")["sandbox_backend"])

    def test_skill_scan_is_bounded(self):
        for i in range(ROW_LIMIT + 5):
            write_skill(self.home, f"bulk/s{i:03d}", f"name: s{i:03d}")
        deep = self.home / "skills" / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        (deep / "SKILL.md").write_text("---\nname: too-deep\n---\n", encoding="utf-8")
        skills = self.admin().snapshot("skills")
        self.assertEqual(len(skills["items"]), ROW_LIMIT)
        self.assertEqual(skills["total"], ROW_LIMIT + 5)
        self.assertTrue(skills["truncated"])
        self.assertNotIn("too-deep", json.dumps(skills))

    def test_routines_drop_prompts_targets_and_errors(self):
        populate(self.home)
        routines = self.admin().snapshot("routines")
        item = routines["items"][0]
        self.assertEqual(item["name"], "Morning reminder")
        self.assertEqual(item["next_run_at"], "2026-01-06T00:00:00+00:00")
        self.assertTrue(item["has_error"])
        text = json.dumps(routines)
        for leaked in ("PRIVATE PROMPT", SECRET, "/opt/run.sh", "12345", "api.example.com", "/srv/data"):
            self.assertNotIn(leaked, text)
        self.assertEqual(self.admin().snapshot("routines", "nothing-matches")["items"], [])

    def test_overview_activity_hides_people(self):
        populate(self.home)
        text = json.dumps(self.admin().snapshot("overview"))
        for leaked in ("user-0", "SYSTEM PROMPT", "first fact", "likes tea"):
            self.assertNotIn(leaked, text)

    def test_demo_is_explicit_deterministic_and_offline(self):
        populate(self.home)
        demo = self.admin(mode="demo")
        with patch("hermes_zalo.admin_assistant.open", side_effect=AssertionError("demo read a file"), create=True), \
                patch("os.walk", side_effect=AssertionError("demo scanned files")):
            first = [demo.snapshot(section) for section in SECTIONS]
        second = [demo.snapshot(section) for section in SECTIONS]
        self.assertEqual(first, second)
        for data in first:
            self.assertEqual(data["source"], "demo")
            self.assertTrue(data["message"])
        self.assertNotIn("Morning reminder", json.dumps(first))
        overview, skills, routines = first[0], first[2], first[3]
        self.assertTrue(overview["updated_at"].startswith("2026-10-31"))
        self.assertEqual(overview["daily"], first[4]["daily"])
        self.assertEqual({item["category"] for item in skills["items"]}, {"news", "data", "research", "productivity"})
        self.assertNotRegex(json.dumps([skills["items"], routines["items"]], ensure_ascii=False), r"(?i)\blab\b")
        self.assertTrue(all(r["last_run_at"] <= "2026-10-31T23:59:59+00:00" for r in routines["items"]))
        self.assertEqual(self.admin().snapshot("overview")["source"], "hermes-home")

    # ---- users ----------------------------------------------------------

    NOW = datetime(2026, 10, 31, 1, 0, tzinfo=VIETNAM_TZ)  # still 30 Oct in UTC: the day must follow Vietnam time

    def users_admin(self, settings_users=None, **kw):
        class W:
            class settings:
                bot_mode = "assistant"
                allowed_users = frozenset(settings_users or ())
        return AssistantAdmin(W(), None, home=self.home, clock=lambda: self.NOW, **kw)

    def quota(self):
        return QuotaStore(self.home / "assistant-core" / "quota.sqlite3", user_daily_limit=30,
                          total_daily_limit=500, clock=lambda: self.NOW)

    def test_users_read_quota_written_by_quota_store(self):
        store = self.quota()
        for message in ("m1", "m2"):
            store.reserve(person_key("telegram", "111"), message)
        store.reserve(person_key("zalo", "abc"), "m3")
        store.reserve(person_key("telegram", "999"), "m4")  # used the bot but not on any allowlist
        expected_used = store.usage(person_key("telegram", "111"))
        store.close()
        old = QuotaStore(self.home / "assistant-core" / "quota.sqlite3", user_daily_limit=30, total_daily_limit=500,
                         clock=lambda: datetime(2026, 10, 30, 12, tzinfo=VIETNAM_TZ))
        old.reserve(person_key("telegram", "111"), "yesterday")
        old.close()
        env = {"TELEGRAM_ALLOWED_USERS": " 111, 222,,*, bad id,../x", "ZALO_ALLOWED_USERS": "abc",
               "TELEGRAM_BOT_TOKEN": "123:" + SECRET, "ZALO_BOT_TOKEN": SECRET}
        with patch.dict(os.environ, env, clear=True):
            users = self.users_admin({"zz9", "abc"}).snapshot("users")
        self.assertTrue(users["available"])
        self.assertEqual(users["source"], "allowlist")
        self.assertEqual(users["coverage"], "explicit_allowlist_only")
        self.assertEqual(users["day"], "2026-10-31")
        self.assertEqual({(i["platform"], i["user_id"]): i["used_today"] for i in users["items"]},
                         {("telegram", "111"): expected_used[0], ("telegram", "222"): 0,
                          ("zalo", "abc"): 1, ("zalo", "zz9"): 0})
        self.assertEqual(expected_used, (2, 4))
        self.assertEqual(users["total_used_today"], 4)
        self.assertEqual((users["user_daily_limit"], users["total_daily_limit"]), (30, 500))
        self.assertEqual(users["rejected"], 3)
        for item in users["items"]:
            self.assertEqual(set(item), {"user_id", "platform", "status", "used_today", "user_daily_limit"})
            self.assertEqual(item["status"], "configured")
        self.assertIn("group", users["message"])
        text = json.dumps(users)
        for leaked in (SECRET, "bad id", "../x", "999", "TOKEN"):
            self.assertNotIn(leaked, text)
        self.assertNotRegex(text, r"[0-9a-f]{64}")  # no person hashes

    def test_users_without_quota_store_report_null_and_create_nothing(self):
        before = tree(self.root)
        with patch.dict(os.environ, {"ZALO_ALLOWED_USERS": "abc"}, clear=True):
            users = self.users_admin().snapshot("users")
        self.assertEqual(tree(self.root), before)
        self.assertFalse((self.home / "assistant-core").exists())
        self.assertEqual(users["items"][0]["used_today"], None)
        self.assertIsNone(users["total_used_today"])
        self.assertFalse(users["quota_available"])
        self.assertEqual(users["total_daily_limit"], 500)  # configuration is still known

    def test_users_reading_existing_quota_store_writes_nothing(self):
        self.quota().close()
        before = tree(self.root)
        with patch.dict(os.environ, {"ZALO_ALLOWED_USERS": "abc"}, clear=True):
            self.assertEqual(self.users_admin().snapshot("users")["items"][0]["used_today"], 0)
        self.assertEqual(tree(self.root), before)

    def test_users_with_foreign_quota_schema_report_null(self):
        path = self.home / "assistant-core" / "quota.sqlite3"
        path.parent.mkdir()
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE turns (day TEXT, who TEXT)")
        db.commit()
        db.close()
        with patch.dict(os.environ, {"ZALO_ALLOWED_USERS": "abc"}, clear=True):
            users = self.users_admin().snapshot("users")
        self.assertIsNone(users["items"][0]["used_today"])
        self.assertIsNone(users["total_used_today"])

    def test_users_limits_come_from_config_with_defaults(self):
        env = {"ZALO_ALLOWED_USERS": "abc", "ASSISTANT_USER_DAILY_LIMIT": "5", "ASSISTANT_TOTAL_DAILY_LIMIT": "50"}
        with patch.dict(os.environ, env, clear=True):
            users = self.users_admin().snapshot("users")
        self.assertEqual((users["user_daily_limit"], users["total_daily_limit"]), (5, 50))
        self.assertEqual(users["items"][0]["user_daily_limit"], 5)
        self.assertEqual(users["limits_source"], "config")
        with patch.dict(os.environ, {"ZALO_ALLOWED_USERS": "abc", "ASSISTANT_USER_DAILY_LIMIT": "0"}, clear=True):
            users = self.users_admin().snapshot("users")
        self.assertEqual((users["user_daily_limit"], users["total_daily_limit"]), (None, None))
        self.assertIn("invalid", users["message"])

    def test_users_limits_without_assistant_core_package(self):
        """Sealed plugin layout: assistant_core is another plugin and cannot be imported here."""
        blocked = {"assistant_core": None, "assistant_core.settings": None}
        cases = [({}, (30, 500), False),
                 ({"ASSISTANT_USER_DAILY_LIMIT": " 7 ", "ASSISTANT_TOTAL_DAILY_LIMIT": "1000000"}, (7, 1000000), False),
                 ({"ASSISTANT_USER_DAILY_LIMIT": "", "ASSISTANT_TOTAL_DAILY_LIMIT": "  "}, (30, 500), False),
                 ({"ASSISTANT_USER_DAILY_LIMIT": "10001"}, (None, None), True),
                 ({"ASSISTANT_TOTAL_DAILY_LIMIT": "1000001"}, (None, None), True),
                 ({"ASSISTANT_USER_DAILY_LIMIT": "0"}, (None, None), True),
                 ({"ASSISTANT_USER_DAILY_LIMIT": "abc"}, (None, None), True),
                 ({"ASSISTANT_TOTAL_DAILY_LIMIT": "2.5"}, (None, None), True)]
        for env, expected, invalid in cases:
            with self.subTest(env=env):
                with patch.dict(os.environ, {"ZALO_ALLOWED_USERS": "abc", **env}, clear=True):
                    with patch.dict(sys.modules, blocked):
                        with self.assertRaises(ImportError):
                            import assistant_core.settings  # noqa: F401 - proves the package is unavailable
                        fallback = self.users_admin().snapshot("users")
                    core = self.users_admin().snapshot("users")
                self.assertTrue(fallback["available"])
                self.assertEqual((fallback["user_daily_limit"], fallback["total_daily_limit"]), expected)
                self.assertEqual(fallback["items"][0]["user_daily_limit"], expected[0])
                self.assertEqual("invalid" in fallback["message"], invalid)
                # The fallback must agree with assistant_core's own parser.
                self.assertEqual((core["user_daily_limit"], core["total_daily_limit"]), expected)

    def test_users_allowlist_is_validated_and_bounded(self):
        ids = ",".join(f"u{i:03d}" for i in range(ROW_LIMIT + 20)) + "," + "a" * 121
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": ids}, clear=True):
            users = self.users_admin().snapshot("users")
        self.assertEqual(len(users["items"]), ROW_LIMIT)
        self.assertEqual(users["total"], ROW_LIMIT + 20)
        self.assertTrue(users["truncated"])
        self.assertEqual(users["rejected"], 1)
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=True):
            users = self.users_admin().snapshot("users")
        self.assertFalse(users["available"])
        self.assertEqual((users["items"], users["rejected"]), ([], 1))
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "111,222"}, clear=True):
            self.assertEqual([i["user_id"] for i in self.users_admin().snapshot("users", "22")["items"]], ["222"])

    def test_users_demo_is_synthetic_telegram_and_zalo(self):
        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "real-user-1"}, clear=True):
            users = self.users_admin(mode="demo").snapshot("users")
        self.assertEqual(users["source"], "demo")
        self.assertEqual(users["day"], "2026-10-31")
        self.assertEqual({item["platform"] for item in users["items"]}, {"telegram", "zalo"})
        self.assertNotIn("real-user-1", json.dumps(users))
        self.assertEqual(users["total_used_today"], sum(item["used_today"] for item in users["items"]))

    def test_never_defaults_to_personal_hermes_home(self):
        with patch.dict(os.environ, {}, clear=True):
            admin = AssistantAdmin(Work(), None)
        self.assertEqual(admin.home, REPO_ROOT / ".runtime" / "hermes-home")
        self.assertNotEqual(admin.home, Path.home() / ".hermes")
        with patch.dict(os.environ, {"HERMES_HOME": str(self.home)}, clear=True):
            self.assertEqual(AssistantAdmin(Work(), None).home, self.home)

    def test_rejects_unknown_section_and_bad_query(self):
        admin = self.admin()
        with self.assertRaises(ValueError):
            admin.snapshot("conversations")
        with self.assertRaises(ValueError):
            admin.snapshot("skills", "x" * 101)
        with self.assertRaises(ValueError):
            admin.snapshot("skills", {"$ne": 1})

    def test_redact(self):
        self.assertEqual(redact("see /etc/passwd and ~/.env"), "see [path] and [path]")
        self.assertEqual(redact("and/or 24/7"), "and/or 24/7")
        self.assertEqual(redact("password=hunter2 ok"), "[secret] ok")
        self.assertIsNone(redact({"a": 1}))
        self.assertEqual(len(redact("x " * 500, 50)), 50)


if __name__ == "__main__":
    unittest.main()
