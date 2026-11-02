"""CEO Briefing console page: demo data, live derivation from local sources, dismiss/restore."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from unittest import mock
import unittest

from hermes_zalo import admin_briefing as briefing
from hermes_zalo import console_sections
from hermes_zalo.admin_auth import AdminError
from hermes_zalo.console_sections import ConsoleContext

NOW = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)
SECRET = "sk-test-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def demo_ctx(name="demo-a"):
    return ConsoleContext(Path("/nonexistent") / name, "demo", Path("/nonexistent") / name / "ac")


def envelope(action, revision, values):
    return {"action": action, "expected_revision": revision, "confirm": True, "values": values}


def routines(*jobs, available=True):
    return {"section": "routines", "available": available, "items": list(jobs), "message": None if available else "broken"}


def job(id_, name, **over):
    base = {"id": id_, "name": name, "enabled": True, "state": "scheduled", "schedule": "every day at 8:00",
            "next_run_at": None, "last_run_at": None, "last_status": None, "last_error": None,
            "last_delivery_error": None, "failure_streak": None, "outputs": [], "latest_output": None}
    base.update(over)
    return base


class LiveHome(unittest.TestCase):
    """Live mode with every source patched, so only the briefing logic is under test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.ctx = ConsoleContext(self.home, "local", self.home / "assistant-core", environ={})
        self.routines = routines()
        self.connectors = {"available": True, "items": []}
        self.gateway = ({"state": "running", "platforms": []}, {"status": "fresh"})
        self.users = {"total_used_today": None, "total_daily_limit": None}
        self.usage = {"available": False}
        assistant = mock.Mock()
        assistant.snapshot.side_effect = lambda section: self.users if section == "users" else self.usage
        self.patches = [
            mock.patch.object(briefing, "_routines", lambda ctx: self.routines),
            mock.patch.object(briefing, "_connectors", lambda ctx: self.connectors),
            mock.patch.object(briefing, "_gateway", lambda ctx: self.gateway),
            mock.patch.object(briefing, "_assistant", lambda ctx: assistant),
            mock.patch.object(briefing, "_now", lambda: NOW),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def snap(self):
        return briefing.snapshot(self.ctx, {})

    def kinds(self, data):
        return [(item["kind"], item["severity"]) for item in data["attention"]["items"]]


class DemoTests(unittest.TestCase):
    def test_registered_in_console_sections(self):
        self.assertIn("admin_briefing", console_sections.EXTENSION_MODULES)
        section = console_sections.get("briefing")
        self.assertIsNotNone(section)
        self.assertIs(section.snapshot, briefing.snapshot)

    def test_demo_snapshot_is_rich_and_json_safe(self):
        data = briefing.snapshot(demo_ctx("rich"), {})
        json.dumps(data)
        self.assertEqual(data["section"], "briefing")
        self.assertTrue(data["available"])
        self.assertEqual(data["source"], "demo")
        self.assertEqual(data["tone"], "bad")
        self.assertRegex(data["summary"], r"^1 urgent item needs your attention;")
        severities = [item["severity"] for item in data["attention"]["items"]]
        self.assertEqual(severities, sorted(severities, key=briefing.SEVERITY.get))
        self.assertGreaterEqual(len(data["kpis"]["items"]), 5)
        platforms = {tile["platform"] for tile in data["kpis"]["items"]}
        self.assertTrue({"facebook", "zalo", "telegram", "x", "work"} <= platforms)
        for tile in data["kpis"]["items"]:
            self.assertGreaterEqual(len(tile["series"]), 7)
            self.assertTrue(all(isinstance(v, (int, float)) for v in tile["series"]))
        self.assertEqual(len(data["reports"]["items"]), 4)
        saved = [r for r in data["reports"]["items"] if r["status"] == "ok"]
        self.assertEqual(len(saved), 3)
        for report in saved:
            self.assertEqual(report["link"], "#reports")
            self.assertRegex(report["report"]["job"], r"^[A-Za-z0-9_-]{1,64}$")
            self.assertRegex(report["report"]["file"], r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.md$")
        failed = next(r for r in data["reports"]["items"] if r["status"] == "failed")
        self.assertIsNone(failed["report"])
        self.assertEqual(failed["link"], "#routines", "a failed run without output opens Automations")
        times = [briefing._parse_time(u["at"]) for u in data["up_next"]["items"]]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(t > briefing._now() - timedelta(hours=1) for t in times))
        self.assertTrue(data["prompts"] and all(p["prompt"] for p in data["prompts"]))
        for item in data["attention"]["items"]:
            self.assertRegex(item["id"], briefing.ITEM_ID)
            self.assertRegex(item["link"], r"^#[a-z][a-z0-9-]{1,31}$")

    def test_demo_reads_nothing_from_disk(self):
        with mock.patch.object(briefing, "_routines", side_effect=AssertionError("disk")), \
                mock.patch.object(briefing, "_gateway", side_effect=AssertionError("disk")):
            briefing.snapshot(demo_ctx("nodisk"), {})

    def test_dismiss_and_restore_in_demo_memory(self):
        ctx = demo_ctx("dismiss")
        data = briefing.snapshot(ctx, {})
        first = data["attention"]["items"][0]
        dismiss = [a for a in data["actions"] if a["id"] == "dismiss"]
        self.assertEqual({a["target"] for a in dismiss}, {i["id"] for i in data["attention"]["items"]})
        self.assertFalse(any(a["id"] == "restore" for a in data["actions"]))
        result = briefing.action(ctx, envelope("dismiss", data["revision"], {"id": first["id"]}))
        self.assertTrue(result["ok"])
        after = briefing.snapshot(ctx, {})
        self.assertNotIn(first["id"], [i["id"] for i in after["attention"]["items"]])
        self.assertEqual(after["attention"]["dismissed"], 1)
        self.assertNotEqual(after["revision"], data["revision"])
        # The old revision is stale now.
        second = after["attention"]["items"][0]["id"]
        with self.assertRaises(AdminError) as stale:
            briefing.action(ctx, envelope("dismiss", data["revision"], {"id": second}))
        self.assertEqual(stale.exception.status, 409)
        restore = next(a for a in after["actions"] if a["id"] == "restore")
        briefing.action(ctx, envelope("restore", restore["revision"], {}))
        again = briefing.snapshot(ctx, {})
        self.assertIn(first["id"], [i["id"] for i in again["attention"]["items"]])
        self.assertEqual(again["attention"]["dismissed"], 0)
        # Another demo home is unaffected.
        self.assertEqual(briefing.snapshot(demo_ctx("other"), {})["attention"]["dismissed"], 0)

    def test_action_validation(self):
        ctx = demo_ctx("validation")
        data = briefing.snapshot(ctx, {})
        rev, item = data["revision"], data["attention"]["items"][0]["id"]
        bad = [
            {"action": "dismiss", "expected_revision": rev, "values": {"id": item}},  # no confirm
            envelope("delete", rev, {"id": item}),
            envelope("dismiss", rev, {}),
            envelope("dismiss", rev, {"id": item, "extra": 1}),
            envelope("dismiss", rev, {"id": "../../etc/passwd"}),
            envelope("dismiss", rev, {"id": "a-" + "0" * 16}),  # well-formed but not on the briefing
            envelope("dismiss", "not-a-revision", {"id": item}),
            envelope("dismiss", 7, {"id": item}),
            envelope("restore", rev, {"id": item}),
            envelope("dismiss_all", rev, {"id": item}),
            envelope("dismiss_all", "f" * 64, {}),  # stale revision
        ]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(AdminError):
                briefing.action(ctx, payload)
        self.assertEqual(briefing.snapshot(ctx, {})["attention"]["dismissed"], 0)


    def test_dismiss_all_hides_every_visible_item(self):
        ctx = demo_ctx("dismiss-all")
        data = briefing.snapshot(ctx, {})
        action = next(a for a in data["actions"] if a["id"] == "dismiss_all")
        self.assertNotIn("target", action)
        self.assertEqual(action["fields"], [])
        self.assertIn(str(len(data["attention"]["items"])), action["description"])
        result = briefing.action(ctx, envelope("dismiss_all", action["revision"], {}))
        self.assertIn(f"Dismissed {len(data['attention']['items'])} items", result["message"])
        after = briefing.snapshot(ctx, {})
        self.assertEqual(after["attention"]["items"], [])
        self.assertEqual(after["tone"], "good")
        self.assertEqual([a["id"] for a in after["actions"]], ["restore"])
        with self.assertRaises(AdminError) as nothing:
            briefing.action(ctx, envelope("dismiss_all", after["revision"], {}))
        self.assertEqual(nothing.exception.status, 409)

    def test_demo_reports_link_to_matching_saved_reports(self):
        data = briefing.snapshot(demo_ctx("refs"), {})
        refs = [r.get("report") for r in data["reports"]["items"]]
        self.assertTrue(all(ref is None or (briefing.JOB_ID.fullmatch(ref["job"]) and briefing.OUTPUT_FILE.fullmatch(ref["file"]))
                            for ref in refs))
        try:
            from hermes_zalo.admin_integrations import saved_reports
        except ImportError:
            self.skipTest("Reports page not available")
        saved = {(row["job"], row["file"]) for row in saved_reports(demo_ctx("refs"), 60)}
        linked = [ref for ref in refs if ref]
        self.assertGreaterEqual(len(linked), 2)
        self.assertTrue(all((ref["job"], ref["file"]) in saved for ref in linked))
        new_report = next(i for i in data["attention"]["items"] if i["title"].startswith("New report"))
        self.assertIn(new_report["report"], linked)


class LiveTests(LiveHome):
    def test_empty_home_is_calm_and_writes_nothing(self):
        data = self.snap()
        self.assertEqual(data["source"], "hermes-home")
        self.assertEqual(data["tone"], "good")
        self.assertTrue(data["summary"].startswith("All clear"))
        self.assertEqual(data["attention"]["items"], [])
        self.assertEqual(data["reports"]["items"], [])
        self.assertEqual(data["up_next"]["items"], [])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_failed_automation_new_report_and_up_next(self):
        self.routines = routines(
            job("r1", "Report · Daily social summary · Café", last_status="error", last_error="connector disabled",
                last_run_at="2026-11-01T01:00:00+00:00", failure_streak=1,
                next_run_at="2026-11-02T01:00:00+00:00",
                outputs=[{"name": "2026-11-01_01-00-00.md", "at": "2026-11-01T01:00:00"}],
                latest_output={"name": "2026-11-01_01-00-00.md",
                               "excerpt": "# Daily social summary\n\n## Summary\n- **Reach** up 12%\n- Two comments wait"}),
            job("j2", "Backup notes", next_run_at="2026-11-01T05:00:00+00:00", last_status="ok",
                last_delivery_error="telegram: chat not found", last_run_at="2026-10-31T05:00:00+00:00"),
            job("j3", "Paused job", enabled=False, state="paused", next_run_at="2026-11-01T04:00:00+00:00"))
        data = self.snap()
        kinds = self.kinds(data)
        self.assertEqual(kinds[0], ("automation", "critical"))  # report job failure is urgent
        self.assertIn(("automation", "warning"), kinds)  # delivery error
        self.assertIn(("report", "info"), kinds)
        failed = data["attention"]["items"][0]
        self.assertEqual(failed["title"], "Daily social summary · Café failed")
        self.assertEqual(failed["link"], "#reports")
        report = data["reports"]["items"][0]
        self.assertEqual(report["report"], {"job": "r1", "file": "2026-11-01_01-00-00.md"})
        new_report = next(i for i in data["attention"]["items"] if i["kind"] == "report")
        self.assertEqual(new_report["report"], report["report"])
        self.assertEqual(report["title"], "Daily social summary · Café")
        self.assertEqual(report["excerpt"], "Reach up 12% · Two comments wait")
        self.assertEqual(report["status"], "failed")
        self.assertEqual([u["id"] for u in data["up_next"]["items"]], ["j2", "r1"])  # paused job excluded
        self.assertIn("next automation in 2 hours", data["summary"])
        tiles = {t["id"]: t for t in data["kpis"]["items"]}
        self.assertEqual(tiles["automations"]["value"], 1)
        self.assertEqual(tiles["automations"]["unit"], "/ 2")
        self.assertEqual(tiles["reports-saved"]["series"][-1], 1)

    def test_new_failure_resurfaces_after_dismissal(self):
        failing = lambda at: routines(job("j1", "Sync", last_status="error", last_run_at=at))  # noqa: E731
        self.routines = failing("2026-11-01T01:00:00+00:00")
        data = self.snap()
        item = data["attention"]["items"][0]
        briefing.action(self.ctx, envelope("dismiss", data["revision"], {"id": item["id"]}))
        self.assertTrue((self.home / "console" / "briefing.json").is_file())
        self.assertEqual(self.snap()["attention"]["items"], [])
        self.routines = failing("2026-11-01T02:00:00+00:00")
        again = self.snap()["attention"]["items"]
        self.assertEqual(len(again), 1)
        self.assertNotEqual(again[0]["id"], item["id"])

    def test_gateway_scheduler_connectors_and_quota(self):
        self.gateway = ({"state": "stopped", "detail": None, "platforms": []}, {"status": "missing"})
        self.connectors = {"available": True, "items": [
            {"id": "facebook", "name": "Facebook Pages", "status": "needs_credentials", "servers": [{"name": "fb"}],
             "credentials": [{"variable": "FB_TOKEN", "label": "Page token", "configured": False}]},
            {"id": "notion", "name": "Notion", "status": "needs_credentials", "servers": [], "credentials": []},
            {"id": "github", "name": "GitHub", "status": "enabled", "servers": [{"name": "gh"}], "credentials": []}]}
        self.users = {"total_used_today": 450, "total_daily_limit": 500, "day": "2026-11-01"}
        data = self.snap()
        kinds = self.kinds(data)
        self.assertEqual(kinds[0], ("gateway", "critical"))
        self.assertIn(("connector", "warning"), kinds)
        self.assertEqual(sum(1 for k, _ in kinds if k == "connector"), 1)  # not-installed Notion is not nagged
        self.assertIn(("quota", "warning"), kinds)
        quota = next(i for i in data["attention"]["items"] if i["kind"] == "quota")
        self.assertEqual(quota["title"], "Daily AI quota at 90%")
        self.assertEqual(data["connected"], 1)
        self.assertIn("connector configured", data["summary"])
        self.assertNotIn("connector live", data["summary"])
        self.assertEqual(data["tone"], "bad")
        self.users = {"total_used_today": 500, "total_daily_limit": 500, "day": "2026-11-01"}
        self.assertIn(("quota", "critical"), self.kinds(self.snap()))

    def test_running_gateway_with_stale_scheduler_and_platform_issue(self):
        self.routines = routines(job("j1", "Sync"))
        self.gateway = ({"state": "running", "platforms": [
            {"name": "telegram", "state": "connected", "needs_attention": False, "error": None},
            {"name": "zalo", "state": "error", "needs_attention": True, "error": "token_invalid"}]},
            {"status": "stale"})
        data = self.snap()
        self.assertIn(("scheduler", "warning"), self.kinds(data))
        channel = next(i for i in data["attention"]["items"] if i["kind"] == "channel")
        self.assertEqual(channel["link"], "#channels")
        tiles = {t["id"]: t for t in data["kpis"]["items"]}
        self.assertEqual((tiles["channels"]["value"], tiles["channels"]["unit"]), (1, "/ 2"))

    def test_usage_series_and_custom_kpi_file(self):
        self.usage = {"available": True, "daily": [{"day": "2026-11-01", "calls": 10}, {"day": "2026-10-30", "calls": 5},
                                                   {"day": "2026-10-20", "calls": 5}]}
        (self.home / "console").mkdir()
        (self.home / "console" / "kpis.json").write_text(json.dumps({"period": "This week", "items": [
            {"id": "fb-reach", "platform": "facebook", "label": "Page reach", "value": 1200, "previous": 1000,
             "series": [1, 2, "x", 3, float("inf") if False else 4]},
            {"label": "no value"}, "junk"]}))
        data = self.snap()
        tiles = {t["id"]: t for t in data["kpis"]["items"]}
        self.assertEqual(tiles["fb-reach"]["change"], 0.2)
        self.assertEqual(data["kpis"]["period"], "This week")
        self.assertEqual(tiles["fb-reach"]["series"], [1, 2, 3, 4])
        self.assertEqual(len([t for t in data["kpis"]["items"] if t.get("source") == "console/kpis.json"]), 1)
        requests = tiles["assistant-requests"]
        self.assertEqual(requests["value"], 15)
        self.assertEqual(requests["series"], [0, 0, 0, 0, 5, 0, 10])
        self.assertEqual(requests["change"], 2.0)  # 15 vs 5 in the previous 7 days

    def test_broken_sources_degrade_per_block(self):
        def boom(ctx):
            raise RuntimeError("disk on fire " + SECRET)
        with mock.patch.object(briefing, "_routines", boom), mock.patch.object(briefing, "_connectors", boom), \
                mock.patch.object(briefing, "_gateway", boom), mock.patch.object(briefing, "_assistant", boom):
            data = self.snap()
        self.assertTrue(data["available"])
        self.assertFalse(data["reports"]["available"])
        self.assertFalse(data["up_next"]["available"])
        self.assertEqual(data["reports"]["message"], "Automations could not be read.")
        self.assertNotIn(SECRET, json.dumps(data))
        (self.home / "console").mkdir()
        (self.home / "console" / "briefing.json").write_text("{not json")
        self.routines = routines(job("j1", "Sync", last_status="error", last_run_at="2026-11-01T01:00:00+00:00"))
        data = self.snap()
        self.assertFalse(data["attention"]["can_dismiss"])
        self.assertEqual(data["actions"], [])
        self.assertEqual(len(data["attention"]["items"]), 1)

    def test_report_refs_only_for_safe_job_and_file_names(self):
        self.assertIsNone(briefing._report_ref("../etc", "2026-11-01_01-00-00.md"))
        self.assertIsNone(briefing._report_ref("job1", "../../secrets.md"))
        self.assertIsNone(briefing._report_ref("job1", None))
        self.assertEqual(briefing._report_ref("job_1-a", "2026-11-01_01-00-00.md"), {"job": "job_1-a", "file": "2026-11-01_01-00-00.md"})

    def test_untrusted_text_is_bounded_single_line(self):
        self.routines = routines(job("j1", "Sync\x00\nnow " + "x" * 400, last_status="error",
                                     last_error="line1\nline2\t" + "y" * 500, last_run_at="2026-11-01T01:00:00+00:00"))
        item = self.snap()["attention"]["items"][0]
        self.assertLessEqual(len(item["title"]), 120)
        self.assertLessEqual(len(item["detail"]), 240)
        self.assertNotIn("\n", item["title"] + item["detail"])
        self.assertNotIn("\x00", item["title"])


class RealSourcesTest(unittest.TestCase):
    """Unpatched live sources on a temporary home: jobs.json and a saved output are read, nothing is written."""

    def test_reads_jobs_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "cron" / "output" / "job1").mkdir(parents=True)
            (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{
                "id": "job1", "name": "Report · Weekly digest", "prompt": "Summarize", "enabled": True,
                "schedule": {"kind": "cron", "expr": "0 8 * * 1", "display": "0 8 * * 1"},
                "next_run_at": "2099-01-05T08:00:00+00:00", "last_status": "error", "last_error": "boom",
                "last_run_at": "2026-10-31T08:00:00+00:00"}]}))
            (home / "cron" / "output" / "job1" / "2026-10-31_08-00-00.md").write_text("# Digest\n\n- Shipped the menu\n")
            before = sorted(str(p) for p in home.rglob("*"))
            ctx = ConsoleContext(home, "local", home / "assistant-core", environ={})
            data = briefing.snapshot(ctx, {})
            self.assertEqual(sorted(str(p) for p in home.rglob("*")), before)
            self.assertEqual(data["up_next"]["items"][0]["name"], "Weekly digest")
            self.assertEqual(data["reports"]["items"][0]["excerpt"], "Shipped the menu")
            self.assertIn("Weekly digest failed", [i["title"] for i in data["attention"]["items"]])


if __name__ == "__main__":
    unittest.main()
