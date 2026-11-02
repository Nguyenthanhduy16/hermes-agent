from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
import unittest
from zoneinfo import ZoneInfo

import yaml

from hermes_zalo.admin_auth import AdminError
from hermes_zalo.admin_automation import MISSING_REVISION, AutomationAdmin, parse_schedule, redact_output
from hermes_zalo.admin_control_store import ControlStore


NOW = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)


def skill_md(name, description="Does things."):
    return f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        (self.home / "skills").mkdir(parents=True)
        self.env_before = dict(os.environ)
        self.admin = AutomationAdmin(ControlStore(self.home), environ={}, clock=lambda: NOW)

    def tearDown(self):
        self.assertEqual(dict(os.environ), self.env_before)
        self.tmp.cleanup()

    def write(self, rel, text):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def act(self, section, action, values, *, revision=None, snapshot=None, authorize=None, target=None):
        if revision is None:
            snap = snapshot or self.admin.snapshot(section)
            desc = [a for a in snap["actions"] if a["id"] == action and (target is None or a.get("target") == target)]
            self.assertTrue(desc, f"no descriptor {action} {target}")
            revision = desc[0]["revision"]
        return self.admin.action(section, {"action": action, "expected_revision": revision, "confirm": True,
                                           "values": values}, authorize=authorize)

    def descriptor(self, snap, action, target=None):
        found = [a for a in snap["actions"] if a["id"] == action and (target is None or a.get("target") == target)]
        return found[0] if found else None

    def jobs(self):
        return json.loads((self.home / "cron" / "jobs.json").read_text())["jobs"]


class SkillsTests(Base):
    def setUp(self):
        super().setUp()
        self.write("skills/autonomous-ai-agents/hermes-agent/SKILL.md", skill_md("hermes-agent"))
        self.write("skills/news/briefing/SKILL.md", skill_md("briefing"))
        self.write("skills/news/headlines/SKILL.md", skill_md("headlines"))
        self.write("skills/data/analysis/SKILL.md", skill_md("analysis"))
        self.write("config.yaml", yaml.safe_dump({"model": {"default": "x"}, "skills": {
            "disabled": ["headlines"], "platform_disabled": {"telegram": ["analysis"]}}}))

    def test_snapshot_lists_skills_categories_and_descriptors(self):
        snap = self.admin.snapshot("skills")
        self.assertTrue(snap["available"])
        by_name = {s["name"]: s for s in snap["items"]}
        self.assertEqual(set(by_name), {"hermes-agent", "briefing", "headlines", "analysis"})
        self.assertFalse(by_name["headlines"]["enabled"])
        self.assertTrue(by_name["hermes-agent"]["essential"])
        self.assertEqual(by_name["analysis"]["platform_disabled"], ["telegram"])
        cats = {c["name"]: c for c in snap["categories"]}
        self.assertEqual((cats["news"]["total"], cats["news"]["enabled"]), (2, 1))
        self.assertIsNone(self.descriptor(snap, "disable_skill", "autonomous-ai-agents:hermes-agent"))
        self.assertIsNone(self.descriptor(snap, "disable_category", "category:autonomous-ai-agents"))
        self.assertIsNotNone(self.descriptor(snap, "enable_skill", "news:headlines"))
        self.assertIsNotNone(self.descriptor(snap, "disable_category", "category:news"))
        self.assertIsNotNone(self.descriptor(snap, "enable_category", "category:news"))
        self.assertEqual(self.descriptor(snap, "create_skill")["revision"], MISSING_REVISION)
        self.assertEqual(self.descriptor(snap, "disable_skill", "news:briefing")["revision"], snap["revision"])

    def test_disable_and_enable_preserve_other_settings(self):
        result = self.act("skills", "disable_skill", {"skill": "briefing"}, target="news:briefing")
        self.assertTrue(result["ok"])
        config = yaml.safe_load((self.home / "config.yaml").read_text())
        self.assertEqual(config["model"], {"default": "x"})
        self.assertEqual(config["skills"]["disabled"], ["briefing", "headlines"])
        self.act("skills", "enable_skill", {"skill": "analysis"}, target="data:analysis")
        config = yaml.safe_load((self.home / "config.yaml").read_text())
        self.assertEqual(config["skills"]["platform_disabled"], {"telegram": []})

    def test_essential_and_unknown_skill_refused(self):
        rev = self.admin.snapshot("skills")["revision"]
        with self.assertRaises(AdminError):
            self.act("skills", "disable_skill", {"skill": "hermes-agent"}, revision=rev)
        with self.assertRaises(AdminError) as ctx:
            self.act("skills", "disable_skill", {"skill": "nope"}, revision=rev)
        self.assertEqual(ctx.exception.status, 404)

    def test_category_toggle_never_disables_essential(self):
        self.write("skills/autonomous-ai-agents/helper/SKILL.md", skill_md("helper"))
        self.act("skills", "disable_category", {"category": "autonomous-ai-agents"}, target="category:autonomous-ai-agents")
        disabled = yaml.safe_load((self.home / "config.yaml").read_text())["skills"]["disabled"]
        self.assertIn("helper", disabled)
        self.assertNotIn("hermes-agent", disabled)
        self.act("skills", "enable_category", {"category": "news"}, target="category:news")
        self.assertNotIn("headlines", yaml.safe_load((self.home / "config.yaml").read_text())["skills"]["disabled"])

    def test_stale_revision_conflicts(self):
        rev = self.admin.snapshot("skills")["revision"]
        self.act("skills", "disable_skill", {"skill": "briefing"}, revision=rev)
        with self.assertRaises(AdminError) as ctx:
            self.act("skills", "disable_skill", {"skill": "analysis"}, revision=rev)
        self.assertEqual(ctx.exception.status, 409)

    def test_create_skill_writes_safe_skill_md(self):
        result = self.act("skills", "create_skill", {"name": "trip-planner", "category": "", "new_category": "travel",
                                                       "description": "Plan short trips.",
                                                       "instructions": "## Steps\n1. Ask dates."})
        self.assertTrue(result["ok"])
        text = (self.home / "skills/travel/trip-planner/SKILL.md").read_text()
        self.assertTrue(text.startswith("---\nname: trip-planner\ndescription: Plan short trips.\n"))
        self.assertIn("## Steps\n1. Ask dates.", text)
        self.assertIn("travel:trip-planner", {s["id"] for s in self.admin.snapshot("skills")["items"]})
        with self.assertRaises(AdminError) as ctx:
            self.act("skills", "create_skill", {"name": "trip-planner", "description": "x", "instructions": "y"})
        self.assertEqual(ctx.exception.status, 409)

    def test_create_skill_validation(self):
        bad = [{"name": "../evil", "description": "d", "instructions": "i"},
               {"name": "Upper", "description": "d", "instructions": "i"},
               {"name": "ok", "category": "../x", "description": "d", "instructions": "i"},
               {"name": "ok", "new_category": "a/b", "description": "d", "instructions": "i"},
               {"name": "ok", "description": "x" * 61, "instructions": "i"},
               {"name": "ok", "description": "d", "instructions": ""},
               {"name": "ok", "description": "d\nname: other", "instructions": "i"}]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(AdminError):
                self.act("skills", "create_skill", values, revision=MISSING_REVISION)
        self.assertFalse((self.home / "skills/ok").exists())

    def test_instruction_limit_fits_request_bound(self):
        field = next(f for f in self.descriptor(self.admin.snapshot("skills"), "create_skill")["fields"]
                     if f["name"] == "instructions")
        self.assertEqual(field["max_length"], 7000)
        with self.assertRaises(AdminError):
            self.act("skills", "create_skill", {"name": "big", "description": "d", "instructions": "\U0001F600" * 7001},
                     revision=MISSING_REVISION)
        body = {"action": "create_skill", "expected_revision": MISSING_REVISION, "confirm": True,
                "values": {"name": "b" * 64, "category": "c" * 64, "new_category": "", "description": "\U0001F600" * 60,
                           "instructions": "\U0001F600" * 7000}}
        self.assertLessEqual(len(json.dumps(body, ensure_ascii=False).encode()), 32768)
        self.act("skills", "create_skill", {"name": "big", "description": "d", "instructions": "\U0001F600" * 7000},
                 revision=MISSING_REVISION)

    def test_create_skill_refuses_symlinked_category(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        os.symlink(outside, self.home / "skills" / "linked")
        with self.assertRaises(AdminError):
            self.act("skills", "create_skill", {"name": "x1", "category": "linked", "description": "d",
                                                "instructions": "i"}, revision=MISSING_REVISION)
        self.assertEqual(list(outside.iterdir()), [])

    def test_request_shape_is_exact(self):
        rev = self.admin.snapshot("skills")["revision"]
        base = {"action": "disable_skill", "expected_revision": rev, "confirm": True, "values": {"skill": "briefing"}}
        for data in ({**base, "extra": 1}, {**base, "confirm": False}, {k: v for k, v in base.items() if k != "values"},
                     {**base, "values": {"skill": "briefing", "path": "/etc"}}, {**base, "action": "create_routine"}):
            with self.subTest(data=data), self.assertRaises(AdminError):
                self.admin.action("skills", data)

    def test_authorize_is_checked(self):
        calls = []

        def deny():
            calls.append(1)
            raise AdminError("no", 403)
        with self.assertRaises(AdminError):
            self.act("skills", "disable_skill", {"skill": "briefing"}, authorize=deny)
        self.assertTrue(calls)
        self.assertNotIn("briefing", yaml.safe_load((self.home / "config.yaml").read_text())["skills"]["disabled"])


class FreshHomeSkillsTests(unittest.TestCase):
    def test_absent_home_and_skills_dir_offers_create_without_writing(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "fresh-home"
            admin = AutomationAdmin(ControlStore(home), environ={})
            snap = admin.snapshot("skills")
            self.assertTrue(snap["available"])
            self.assertEqual(snap["items"], [])
            create = next(a for a in snap["actions"] if a["id"] == "create_skill")
            self.assertFalse(home.exists())  # reads never create anything
            admin.action("skills", {"action": "create_skill", "expected_revision": create["revision"], "confirm": True,
                                    "values": {"name": "first", "description": "First skill.", "instructions": "Do it."}})
            self.assertTrue((home / "skills" / "first" / "SKILL.md").is_file())
            self.assertEqual([s["name"] for s in admin.snapshot("skills")["items"]], ["first"])

    def test_existing_home_without_skills_dir_offers_create(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            snap = AutomationAdmin(ControlStore(home)).snapshot("skills")
            self.assertTrue(snap["available"])
            self.assertTrue(any(a["id"] == "create_skill" for a in snap["actions"]))
            self.assertEqual(list(home.iterdir()), [])

    def test_unsafe_skills_path_is_unavailable(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            (Path(tmp) / "elsewhere").mkdir()
            os.symlink(Path(tmp) / "elsewhere", home / "skills")
            snap = AutomationAdmin(ControlStore(home)).snapshot("skills")
            self.assertFalse(snap["available"])
            self.assertFalse(any(a["id"] == "create_skill" for a in snap["actions"]))
            os.unlink(home / "skills")
            (home / "skills").write_text("not a dir")
            self.assertFalse(AutomationAdmin(ControlStore(home)).snapshot("skills")["available"])


class ScheduleTests(unittest.TestCase):
    def test_grammar_matches_upstream_shapes(self):
        tz = ZoneInfo("Asia/Ho_Chi_Minh")
        self.assertEqual(parse_schedule("0 9 * * *", tz), {"kind": "cron", "expr": "0 9 * * *", "display": "0 9 * * *"})
        self.assertEqual(parse_schedule("every 2h", tz), {"kind": "interval", "minutes": 120, "display": "every 120m"})
        self.assertEqual(parse_schedule("30m", tz)["minutes"], 30)
        self.assertEqual(parse_schedule("every monday 9am", tz)["expr"], "0 9 * * 1")
        self.assertEqual(parse_schedule("weekdays at 8:30", tz)["expr"], "30 8 * * 1-5")
        self.assertEqual(parse_schedule("0 9 * * MON-FRI", tz)["kind"], "cron")
        once = parse_schedule("2026-12-01T09:00", tz)
        self.assertEqual((once["kind"], once["run_at"]), ("once", "2026-12-01T09:00:00+07:00"))
        for bad in ("61 * * * *", "* * * *  * *", "every banana", "in 30m", "soon", "0 25 * * *", "*/0 * * * *"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_schedule(bad, tz)


class RoutineTests(Base):
    def setUp(self):
        super().setUp()
        self.write("skills/news/briefing/SKILL.md", skill_md("briefing"))
        self.write("config.yaml", "timezone: Asia/Ho_Chi_Minh\n")

    def create(self, **values):
        values = {"name": "Brief", "schedule": "0 7 * * *", "prompt": "Summarize news.", **values}
        return self.act("routines", "create_routine", values)

    def test_create_uses_upstream_schema_local_delivery_and_lock(self):
        self.assertEqual(self.admin.snapshot("routines")["items"], [])
        result = self.create(skills="briefing", repeat="3")
        job = self.jobs()[0]
        self.assertEqual(job["id"], result["item"]["id"])
        self.assertEqual(job["schedule"], {"kind": "cron", "expr": "0 7 * * *", "display": "0 7 * * *"})
        self.assertEqual((job["deliver"], job["origin"], job["script"], job["workdir"]), ("local", None, None, None))
        self.assertIsNone(job["next_run_at"])
        self.assertEqual((job["enabled"], job["state"], job["repeat"]), (True, "scheduled", {"times": 3, "completed": 0}))
        self.assertEqual((job["skills"], job["skill"]), (["briefing"], "briefing"))
        self.assertEqual(job["created_at"], "2026-11-01T10:00:00+07:00")
        self.assertTrue((self.home / "cron" / ".jobs.lock").exists())
        item = self.admin.snapshot("routines")["items"][0]
        self.assertTrue(item["next_run_pending"])

    def test_create_validation(self):
        for values in ({"schedule": "nonsense"}, {"prompt": "", "skills": ""}, {"skills": "missing"},
                       {"schedule": "2026-10-01T09:00"}, {"repeat": "-1"}, {"repeat": "x"}, {"paused": "yes"},
                       {"script": "rm -rf /"}, {"deliver": "telegram:123"}):
            with self.subTest(values=values), self.assertRaises(AdminError):
                self.create(**values)
        self.assertFalse((self.home / "cron" / "jobs.json").exists())

    def test_one_shot_and_paused_create(self):
        self.create(schedule="2026-11-02T09:00", repeat="5")
        self.create(name="Later", paused=True)
        once, paused = self.jobs()
        self.assertEqual(once["repeat"]["times"], 1)
        self.assertEqual(once["schedule"]["run_at"], "2026-11-02T09:00:00+07:00")
        self.assertEqual((paused["enabled"], paused["state"]), (False, "paused"))
        snap = self.admin.snapshot("routines")
        self.assertIsNotNone(self.descriptor(snap, "resume_routine", paused["id"]))

    def test_preserves_existing_jobs_and_unknown_fields(self):
        self.write("cron/jobs.json", json.dumps({"jobs": [{"id": "abc123", "name": "Old", "prompt": "p",
                    "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"}, "enabled": True,
                    "state": "scheduled", "next_run_at": "2026-11-01T11:00:00+07:00", "deliver": "telegram",
                    "custom_field": {"keep": True}}], "updated_at": "x"}))
        self.create()
        jobs = self.jobs()
        self.assertEqual(jobs[0]["custom_field"], {"keep": True})
        self.assertEqual(len(jobs), 2)

    def test_edit_pause_resume_delete(self):
        job_id = self.create()["item"]["id"]
        jobs = json.loads((self.home / "cron/jobs.json").read_text())
        jobs["jobs"][0].update(next_run_at="2026-11-02T07:00:00+07:00", pending_slot={"x": 1})
        (self.home / "cron/jobs.json").write_text(json.dumps(jobs))
        self.act("routines", "edit_routine", {"id": job_id, "name": "Brief 2", "schedule": "every 1d",
                                               "prompt": "New", "skills": "", "repeat": ""}, target=job_id)
        job = self.jobs()[0]
        self.assertEqual((job["name"], job["prompt"], job["schedule"]["kind"]), ("Brief 2", "New", "interval"))
        self.assertIsNone(job["next_run_at"])
        self.assertNotIn("pending_slot", job)
        self.act("routines", "pause_routine", {"id": job_id}, target=job_id)
        job = self.jobs()[0]
        self.assertEqual((job["enabled"], job["state"]), (False, "paused"))
        self.assertIsNotNone(job["paused_at"])
        # An elapsed recurring slot stays due on resume (upstream resume_job).
        data = json.loads((self.home / "cron/jobs.json").read_text())
        data["jobs"][0]["next_run_at"] = "2026-11-01T08:00:00+07:00"
        (self.home / "cron/jobs.json").write_text(json.dumps(data))
        self.act("routines", "resume_routine", {"id": job_id}, target=job_id)
        job = self.jobs()[0]
        self.assertEqual((job["enabled"], job["state"], job["next_run_at"]),
                         (True, "scheduled", "2026-11-01T08:00:00+07:00"))
        self.act("routines", "pause_routine", {"id": job_id}, target=job_id)
        data = json.loads((self.home / "cron/jobs.json").read_text())
        data["jobs"][0]["next_run_at"] = "2026-11-05T08:00:00+07:00"
        (self.home / "cron/jobs.json").write_text(json.dumps(data))
        self.act("routines", "resume_routine", {"id": job_id}, target=job_id)
        self.assertIsNone(self.jobs()[0]["next_run_at"])
        self.write(f"cron/output/{job_id}/2026-11-01_07-00-00.md", "out")
        self.act("routines", "delete_routine", {"id": job_id}, target=job_id)
        self.assertEqual(self.jobs(), [])
        self.assertFalse((self.home / "cron/output" / job_id).exists())

    def test_stale_revision_and_missing_job(self):
        job_id = self.create()["item"]["id"]
        rev = self.admin.snapshot("routines")["revision"]
        self.act("routines", "pause_routine", {"id": job_id}, revision=rev)
        with self.assertRaises(AdminError) as ctx:
            self.act("routines", "delete_routine", {"id": job_id}, revision=rev)
        self.assertEqual(ctx.exception.status, 409)
        rev = self.admin.snapshot("routines")["revision"]
        with self.assertRaises(AdminError):
            self.act("routines", "pause_routine", {"id": "../x"}, revision=rev)
        with self.assertRaises(AdminError) as ctx:
            self.act("routines", "pause_routine", {"id": "nothere"}, revision=rev)
        self.assertEqual(ctx.exception.status, 404)

    def test_run_now_requires_live_scheduler_and_local_delivery(self):
        job_id = self.create()["item"]["id"]
        snap = self.admin.snapshot("routines")
        self.assertIsNone(self.descriptor(snap, "run_routine"))
        self.assertFalse(snap["scheduler_active"])
        with self.assertRaises(AdminError):
            self.act("routines", "run_routine", {"id": job_id}, revision=snap["revision"])
        self.write("cron/ticker_heartbeat", f"{time.time()} 1")
        self.act("routines", "run_routine", {"id": job_id}, target=job_id)
        job = self.jobs()[0]
        self.assertEqual(job["next_run_at"], job["manual_run_at"])
        data = json.loads((self.home / "cron/jobs.json").read_text())
        data["jobs"][0]["deliver"] = "telegram"
        (self.home / "cron/jobs.json").write_text(json.dumps(data))
        snap = self.admin.snapshot("routines")
        self.assertIsNone(self.descriptor(snap, "run_routine", job_id))
        with self.assertRaises(AdminError):
            self.act("routines", "run_routine", {"id": job_id}, revision=snap["revision"])

    def test_output_redaction_and_no_client_paths(self):
        job_id = self.create()["item"]["id"]
        out = self.home / "cron/output" / job_id
        self.write(f"cron/output/{job_id}/2026-11-01_07-00-00.md",
                   "Report\napi_key=sk-abcdefghijklmnop1234\nBearer abcdefghijklmnopqrstuvwx\n"
                   "https://user:pw@example.com/x\n123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCr4K-ab")
        secret = Path(self.tmp.name) / "secret.md"
        secret.write_text("TOPSECRET")
        os.symlink(secret, out / "2026-11-02_07-00-00.md")
        item = self.admin.snapshot("routines")["items"][0]
        self.assertEqual([f["name"] for f in item["outputs"]], ["2026-11-01_07-00-00.md"])
        excerpt = item["latest_output"]["excerpt"]
        for leaked in ("sk-abcdef", "abcdefghijklmnopqrstuvwx", "pw@", "AAEhBOweik", "TOPSECRET"):
            self.assertNotIn(leaked, excerpt)
        full = self.admin.read_output(job_id)
        self.assertIn("Report", full["output"]["text"])
        for name in ("../../config.yaml", "2026-11-02_07-00-00.md", "/etc/passwd"):
            with self.subTest(name=name), self.assertRaises(AdminError):
                self.admin.read_output(job_id, name)
        with self.assertRaises(AdminError):
            self.admin.read_output("../x")

    def test_run_history_from_executions_db(self):
        job_id = self.create()["item"]["id"]
        db = sqlite3.connect(self.home / "cron" / "executions.db")
        db.execute("CREATE TABLE executions (id TEXT, job_id TEXT, source TEXT, process_id TEXT, pid INTEGER, "
                   "status TEXT, claimed_at TEXT, started_at TEXT, finished_at TEXT, error TEXT)")
        for i in range(7):
            db.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (str(i), job_id, "tick", "p", 1, "completed" if i else "failed",
                        f"2026-10-2{i}T00:00:00+00:00", None, None, "boom token=abc123456" if not i else None))
        db.commit()
        db.close()
        runs = self.admin.snapshot("routines")["items"][0]["runs"]
        self.assertEqual(len(runs), 5)
        self.assertEqual(runs[0]["claimed_at"], "2026-10-26T00:00:00+00:00")
        self.assertFalse(any((self.home / "cron").glob("executions.db-*")))

    def test_routines_rows_and_response_size_are_bounded(self):
        big = "\U0001F600" * 5000
        jobs = [{"id": f"job{i:04d}", "name": f"Job {i}", "prompt": big, "enabled": True, "state": "scheduled",
                 "schedule": {"kind": "cron", "expr": "0 7 * * *", "display": "0 7 * * *"},
                 "schedule_display": "0 7 * * *", "deliver": "local"} for i in range(60)]
        self.write("cron/jobs.json", json.dumps({"jobs": jobs}, ensure_ascii=False))  # store reads <= 2 MiB
        for i in range(60):
            self.write(f"cron/output/job{i:04d}/2026-11-01_07-00-00.md", big)
        snap = self.admin.snapshot("routines")
        self.assertLessEqual(len(snap["items"]), 50)
        self.assertTrue(snap["truncated"])
        self.assertEqual(snap["total"], 60)
        self.assertLess(len(json.dumps(snap, ensure_ascii=True)), 2 * 1024 * 1024)
        self.assertLess(len(json.dumps(snap, ensure_ascii=False).encode()), 2 * 1024 * 1024)
        shown = {i["id"] for i in snap["items"]}
        self.assertTrue(all(a.get("target") in shown for a in snap["actions"] if a.get("target")))
        self.assertIsNotNone(self.descriptor(snap, "create_routine"))

    def test_unsupported_job_file_is_honest(self):
        self.write("cron/jobs.json", json.dumps({"jobs": {"a": 1}}))
        snap = self.admin.snapshot("routines")
        self.assertFalse(snap["available"])
        self.assertEqual(snap["actions"], [])


class DeliveryTests(Base):
    CHAT = "987654321"

    def create(self, **values):
        values = {"name": "Brief", "schedule": "0 7 * * *", "prompt": "Summarize news.", **values}
        return self.act("routines", "create_routine", values)

    def configure(self, telegram=True, zalo=True, tokens=True):
        env = []
        if telegram:
            env.append("TELEGRAM_HOME_CHANNEL=" + self.CHAT)
        if tokens:
            env += ["TELEGRAM_BOT_TOKEN=123456:" + "A" * 35, "ZALO_BOT_TOKEN=zalo-token-value"]
        self.write(".env", "\n".join(env) + "\n")
        config = {"platforms": {"zalo": {"home_channel": {"platform": "zalo", "chat_id": "zalo-owner-42",
                                                           "name": "Owner"}}}} if zalo else {}
        self.write("config.yaml", yaml.safe_dump(config))

    def options(self, snap=None, action="create_routine", target=None):
        desc = self.descriptor(snap or self.admin.snapshot("routines"), action, target)
        return next(f for f in desc["fields"] if f["name"] == "deliver")

    def test_only_local_without_configured_home_chats(self):
        field = self.options()
        self.assertEqual(["local"], [o["value"] for o in field["options"]])
        self.assertEqual("local", field["value"])
        self.assertEqual("New automation", self.descriptor(self.admin.snapshot("routines"), "create_routine")["label"])
        for bad in ("telegram", "zalo", "origin", "telegram:123", "all", "keep", 5, ["local"]):
            with self.subTest(deliver=bad), self.assertRaises(AdminError):
                self.create(deliver=bad)
        self.assertFalse((self.home / "cron" / "jobs.json").exists())

    def test_configured_home_chats_are_offered_without_chat_ids(self):
        self.configure()
        snap = self.admin.snapshot("routines")
        field = self.options(snap)
        self.assertEqual(["local", "telegram", "zalo"], [o["value"] for o in field["options"]])
        self.assertEqual("Zalo · Owner", field["options"][2]["label"])
        text = json.dumps(snap)
        self.assertNotIn(self.CHAT, text)
        self.assertNotIn("zalo-owner-42", text)
        self.create(deliver="zalo")
        self.create(name="Tg", deliver="telegram")
        self.assertEqual(["zalo:zalo-owner-42", "telegram:" + self.CHAT], [job["deliver"] for job in self.jobs()])
        items = self.admin.snapshot("routines")["items"]
        self.assertEqual("Telegram · home chat", items[1]["deliver_label"])
        with self.assertRaises(AdminError):
            self.create(deliver="telegram:" + self.CHAT)

    def test_home_chat_without_bot_token_is_not_offered(self):
        self.configure(tokens=False)
        self.assertEqual(["local"], [o["value"] for o in self.options()["options"]])

    def test_delivery_uses_effective_platform_config_without_exposing_ids_or_tokens(self):
        self.write('config.yaml', yaml.safe_dump({'gateway': {'platforms': {'telegram': {
            'token': 'private-bot-token', 'home_channel': {'chat_id': '10001'}}}, 'telegram': {
            'home_channel': {'chat_id': '10002'}}}}))
        self.create(deliver='telegram')
        self.assertEqual('telegram:10002', self.jobs()[0]['deliver'])
        text = json.dumps(self.admin.snapshot('routines'))
        for private in ('10001', '10002', 'private-bot-token'):
            self.assertNotIn(private, text)
        for target in ('telegram:10001', 'telegram:10002', 'telegram:99999'):
            with self.assertRaises(AdminError):
                self.create(deliver=target)

    def test_unknown_or_foreign_origin_cannot_be_selected_or_triggered(self):
        self.configure()
        self.write('cron/jobs.json', json.dumps({'jobs': [{'id': 'foreign', 'name': 'Foreign origin',
            'prompt': 'p', 'enabled': True, 'state': 'scheduled', 'deliver': 'origin',
            'schedule': {'kind': 'cron', 'expr': '0 8 * * *', 'display': '0 8 * * *'},
            'origin': {'platform': 'telegram', 'chat_id': '111111111'}}]}))
        self.write('cron/ticker_heartbeat', f'{time.time()} 1')
        snapshot = self.admin.snapshot('routines')
        field = self.options(snapshot, 'edit_routine', 'foreign')
        self.assertNotIn('origin', [item['value'] for item in field['options']])
        self.assertIsNone(self.descriptor(snapshot, 'run_routine', 'foreign'))
        self.assertNotIn('111111111', json.dumps(snapshot))
        with self.assertRaises(AdminError):
            self.act('routines', 'run_routine', {'id': 'foreign'}, revision=snapshot['revision'])

    def test_delivery_is_revalidated_inside_the_write_transaction(self):
        self.configure()
        descriptor = self.descriptor(self.admin.snapshot('routines'), 'create_routine')
        def remove_target():
            self.write('.env', '')
        with self.assertRaises(AdminError):
            self.act('routines', 'create_routine', {'name': 'No longer configured', 'schedule': 'every 2h',
                'prompt': 'Research', 'deliver': 'telegram'}, revision=descriptor['revision'], authorize=remove_target)
        self.assertFalse((self.home / 'cron' / 'jobs.json').exists())

    def test_edit_keeps_unknown_targets_and_offers_origin_only_with_an_origin(self):
        self.configure(zalo=False)
        self.write("cron/jobs.json", json.dumps({"jobs": [
            {"id": "cli1", "name": "CLI job", "prompt": "p", "schedule": {"kind": "cron", "expr": "0 7 * * *",
             "display": "0 7 * * *"}, "enabled": True, "state": "scheduled", "deliver": "all"},
            {"id": "chat1", "name": "From chat", "prompt": "p", "schedule": {"kind": "cron", "expr": "0 7 * * *",
             "display": "0 7 * * *"}, "enabled": True, "state": "scheduled", "deliver": "origin",
             "origin": {"platform": "telegram", "chat_id": self.CHAT}}]}))
        snap = self.admin.snapshot("routines")
        cli = self.options(snap, "edit_routine", "cli1")
        self.assertEqual(("keep", ["keep", "local", "telegram"]), (cli["value"], [o["value"] for o in cli["options"]]))
        chat = self.options(snap, "edit_routine", "chat1")
        self.assertEqual("origin", chat["value"])
        self.assertIn("origin", [o["value"] for o in chat["options"]])
        self.write('cron/ticker_heartbeat', f'{time.time()} 1')
        self.assertIsNotNone(self.descriptor(self.admin.snapshot('routines'), 'run_routine', 'chat1'))
        values = {"name": "CLI job", "schedule": "0 7 * * *", "prompt": "p", "skills": "", "repeat": ""}
        self.act("routines", "edit_routine", dict(values, id="cli1", deliver="keep"), target="cli1")
        self.assertEqual("all", self.jobs()[0]["deliver"])
        with self.assertRaises(AdminError):
            self.act("routines", "edit_routine", dict(values, id="cli1", deliver="origin"), target="cli1")
        self.act("routines", "edit_routine", dict(values, id="cli1", deliver="telegram"), target="cli1")
        self.assertEqual("telegram:" + self.CHAT, self.jobs()[0]["deliver"])

    def test_run_now_follows_the_delivery_allowlist(self):
        self.configure()
        self.write("cron/ticker_heartbeat", f"{time.time()} 1")
        job_id = self.create(deliver="telegram")["item"]["id"]
        snap = self.admin.snapshot("routines")
        self.assertIn("Telegram", self.descriptor(snap, "run_routine", job_id)["description"])
        self.write(".env", "ZALO_BOT_TOKEN=x\n")  # the Telegram home chat is gone
        snap = self.admin.snapshot("routines")
        self.assertIsNone(self.descriptor(snap, "run_routine", job_id))
        with self.assertRaises(AdminError):
            self.act("routines", "run_routine", {"id": job_id}, revision=snap["revision"])

    def test_demo_offers_synthetic_targets(self):
        with TemporaryDirectory() as tmp:
            admin = AutomationAdmin(ControlStore(Path(tmp), mode="demo"), environ={}, clock=lambda: NOW)
            snap = admin.snapshot("routines")
            field = next(f for f in self.descriptor(snap, "create_routine")["fields"] if f["name"] == "deliver")
            self.assertEqual(["local", "telegram", "zalo"], [o["value"] for o in field["options"]])
            morning = next(i for i in snap["items"] if i["name"] == "Morning briefing")
            self.assertEqual("Telegram · Owner chat (demo)", morning["deliver_label"])


class DemoTests(unittest.TestCase):
    def test_demo_is_in_memory_and_deterministic(self):
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            admin = AutomationAdmin(ControlStore(home, mode="demo"))
            routines = admin.snapshot("routines")
            self.assertEqual([i["id"] for i in routines["items"]], ["demo00000001", "demo00000002"])
            self.assertEqual(routines["source"], "demo")
            pause = next(a for a in routines["actions"] if a["id"] == "pause_routine")
            admin.action("routines", {"action": "pause_routine", "expected_revision": pause["revision"],
                                      "confirm": True, "values": {"id": "demo00000001"}})
            self.assertEqual(admin.snapshot("routines")["items"][0]["state"], "paused")
            skills = admin.snapshot("skills")
            self.assertEqual(len(skills["items"]), 5)
            admin.action("skills", {"action": "create_skill", "expected_revision": MISSING_REVISION, "confirm": True,
                                    "values": {"name": "demo-new", "description": "Demo.", "instructions": "Do."}})
            self.assertIn("demo-new", {s["name"] for s in admin.snapshot("skills")["items"]})
            self.assertFalse(home.exists())


class RedactionTests(unittest.TestCase):
    def test_redact_output_keeps_text(self):
        text = redact_output("Hello\npassword: hunter2\nok")
        self.assertEqual(text, "Hello\npassword: [secret]\nok")


if __name__ == "__main__":
    unittest.main()
