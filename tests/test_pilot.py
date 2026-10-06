"""Offline aggregate-only pilot reporting and privacy checks."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hermes_zalo.pilot import PilotStore, format_report


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.store = PilotStore(self.path / "pilot.sqlite")
        self.addCleanup(lambda: self.store.close())

    def hermes(self, rows):
        path = self.path / "hermes.sqlite"
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE sessions (
                source TEXT, started_at REAL, input_tokens INTEGER, output_tokens INTEGER,
                api_call_count INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL,
                cost_status TEXT, user_id TEXT, system_prompt TEXT);
            CREATE TABLE messages (content TEXT);
            INSERT INTO messages VALUES ('VERY-PRIVATE-CONVERSATION-123');
        """)
        for source, estimate, actual, status in rows:
            db.execute("INSERT INTO sessions VALUES (?,100,10,5,1,?,?,?,'PRIVATE-USER-ID','PRIVATE-SYSTEM-PROMPT')",
                       (source, estimate, actual, status))
        db.commit()
        db.close()
        return path

    def test_metrics_are_persistent_aggregates_without_identity_or_content(self):
        self.store.record("chat", "ok", 100)
        self.store.record("chat", "ok", 200)
        self.store.record("documents", "not_found", 25)
        original = self.store.report()
        self.store.close()
        self.store = PilotStore(self.path / "pilot.sqlite")
        restarted = self.store.report()
        self.assertEqual(original, restarted)
        self.assertEqual({"kind": "chat", "outcome": "ok", "count": 2, "measured_count": 2, "average_ms": 150}, restarted["events"][0])
        self.assertEqual({"kind", "outcome", "count", "measured_count", "average_ms"}, set(restarted["events"][0]))
        with self.assertRaises(ValueError):
            self.store.record("PRIVATE-USER-ID", "ok")
        with self.assertRaises(ValueError):
            self.store.record("chat", "raw private conversation")
        for elapsed in (-1, True, float("inf"), float("nan"), 86400001):
            with self.assertRaises(ValueError):
                self.store.record("chat", "ok", elapsed)

    def test_unknown_durations_do_not_become_zero_or_change_measured_average(self):
        self.store.record("chat", "ok")
        self.store.record("chat", "ok", None)
        report = self.store.report()
        self.assertEqual(2, report["events"][0]["count"])
        self.assertEqual(0, report["events"][0]["measured_count"])
        self.assertIsNone(report["events"][0]["average_ms"])
        self.assertIn("thời gian: chưa đo", format_report(report))
        for measured in (100, 200, 0):
            self.store.record("chat", "ok", measured)
        report = self.store.report()
        self.assertEqual(5, report["events"][0]["count"])
        self.assertEqual(3, report["events"][0]["measured_count"])
        self.assertEqual(100, report["events"][0]["average_ms"])
        self.assertIn("100 ms (3 lượt có đo)", format_report(report))

    def test_legacy_migration_keeps_counts_and_feedback_without_guessing_latency(self):
        path = self.path / "legacy.sqlite"
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE pilot_meta (key TEXT PRIMARY KEY,value REAL NOT NULL);
            INSERT INTO pilot_meta VALUES ('created_at',123);
            CREATE TABLE pilot_metrics (
                kind TEXT NOT NULL,outcome TEXT NOT NULL,count INTEGER NOT NULL,total_ms REAL NOT NULL,
                PRIMARY KEY(kind,outcome));
            INSERT INTO pilot_metrics VALUES ('chat','ok',5,100);
            CREATE TABLE pilot_feedback (id TEXT PRIMARY KEY,created_at REAL NOT NULL,text TEXT NOT NULL);
            INSERT INTO pilot_feedback VALUES ('old-id',123,'Keep private feedback');
        """)
        db.close()
        migrated = PilotStore(path)
        try:
            report = migrated.report()
            self.assertEqual(123, report["since"])
            self.assertEqual(1, report["feedback_count"])
            self.assertEqual(5, report["events"][0]["count"])
            self.assertIsNone(report["events"][0]["average_ms"])
            migrated.record("chat", "ok", 250)
        finally:
            migrated.close()
        migrated = PilotStore(path)
        try:
            row = migrated.report()["events"][0]
            self.assertEqual((6, 1, 250), (row["count"], row["measured_count"], row["average_ms"]))
        finally:
            migrated.close()

    def test_explicit_feedback_is_private_and_not_in_report(self):
        identifier = self.store.feedback("Nội dung góp ý riêng không đưa vào báo cáo")
        self.assertRegex(identifier, "^[A-F0-9]{12}$")
        report = self.store.report()
        self.assertEqual(1, report["feedback_count"])
        self.assertNotIn("Nội dung góp ý", json.dumps(report, ensure_ascii=False))
        self.assertNotIn(identifier, json.dumps(report))
        self.assertNotIn("Nội dung góp ý", format_report(report))
        for body in ("", " ", "x" * 2001, "nul\0text"):
            with self.assertRaises(ValueError):
                self.store.feedback(body)

    def test_feedback_retention_is_bounded_and_keeps_newest(self):
        with self.store.connection:
            self.store.connection.executemany("INSERT INTO pilot_feedback VALUES (?,?,?)",
                ((str(index), float(index), "Older feedback") for index in range(1000)))
        identifier = self.store.feedback("Newest")
        self.assertEqual(1000, self.store.report()["feedback_count"])
        self.assertIsNone(self.store.connection.execute("SELECT id FROM pilot_feedback WHERE id='0'").fetchone())
        self.assertEqual(identifier, self.store.connection.execute("SELECT id FROM pilot_feedback WHERE text='Newest'").fetchone()[0])

    def test_cost_subtotals_do_not_double_count_or_treat_unknown_as_zero(self):
        source = self.hermes([
            ("zalo", 99, 2, "actual"), ("zalo", 99, 0, "actual"),
            ("zalo", 3, None, "estimated"), ("zalo", 0, None, "estimated"),
            ("zalo", 0, None, "included"), ("zalo", 0, 0, "unknown"),
            ("zalo", None, None, "unknown"), ("zalo", 5, None, None),
            ("telegram", 888, 999, "actual"), ("cli", 111, None, "estimated"),
        ])
        report = self.store.report(source)
        usage = report["usage"]
        self.assertTrue(usage["available"])
        self.assertEqual(8, usage["sessions"])
        self.assertEqual((80, 40, 8), (usage["input_tokens"], usage["output_tokens"], usage["api_call_count"]))
        self.assertEqual(2, usage["actual_sessions"])
        self.assertEqual(2, usage["actual_cost_usd"])
        self.assertEqual(3, usage["estimated_sessions"])
        self.assertEqual(3, usage["estimated_cost_usd"])
        self.assertEqual(3, usage["unknown_cost_sessions"])
        self.assertEqual(1, usage["included_sessions"])
        serialized = json.dumps(report)
        self.assertNotIn("PRIVATE", serialized)
        self.assertNotIn("telegram", serialized)
        self.assertNotIn("999", serialized)
        rendered = format_report(report)
        self.assertIn("không phải theo ngày", rendered)
        self.assertIn("toàn bộ 8 phiên Zalo còn lưu", rendered)
        self.assertIn("Phiên chưa xác định chi phí: 3", rendered)

    def test_actual_zero_is_known_but_missing_or_unknown_cost_is_not(self):
        path = self.hermes([("zalo", None, 0, "actual")])
        usage = self.store.report(path)["usage"]
        self.assertEqual(0, usage["actual_cost_usd"])
        self.assertIsNone(usage["estimated_cost_usd"])
        self.assertEqual(0, usage["unknown_cost_sessions"])
        self.assertIn("0.000000 USD", format_report(self.store.report(path)))
        with sqlite3.connect(path) as db:
            db.execute("UPDATE sessions SET actual_cost_usd=NULL,input_tokens=NULL,api_call_count=NULL,cost_status='unknown'")
        db.close()
        report = self.store.report(path)
        self.assertIsNone(report["usage"]["actual_cost_usd"])
        self.assertIsNone(report["usage"]["input_tokens"])
        self.assertIsNone(report["usage"]["api_call_count"])
        self.assertEqual(1, report["usage"]["unknown_cost_sessions"])
        self.assertEqual(1, report["usage"]["unknown_usage_sessions"])
        self.assertIn("chưa xác định", format_report(report))

    def test_missing_or_incompatible_hermes_db_is_unknown_and_never_created(self):
        missing = self.path / "not-created.sqlite"
        for source in (None, missing):
            self.assertFalse(self.store.report(source)["usage"]["available"])
        self.assertFalse(missing.exists())
        incompatible = self.path / "incompatible.sqlite"
        with sqlite3.connect(incompatible) as db:
            db.execute("CREATE TABLE sessions (source TEXT)")
        db.close()
        self.assertFalse(self.store.report(incompatible)["usage"]["available"])

    def test_report_reads_only_approved_columns_and_source_is_unchanged(self):
        source = self.hermes([("zalo", 0.00000001, None, "estimated")])
        previous = source.read_bytes()
        report = self.store.report(source)
        self.assertEqual(previous, source.read_bytes())
        self.assertIn("<0.000001 USD", format_report(report))
        self.assertNotIn("0.000000 USD", format_report(report))


if __name__ == "__main__":
    unittest.main()
