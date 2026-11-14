"""bench export: tasks.csv / steps.csv joined through the Hermes session id."""

import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from assistant_core import bench, bench_export
from assistant_core.telemetry import TelemetryStore

PRICING = {"big-1": {"input": 2.0, "output": 10.0, "cache_read": 0.1},
           "deepseek-flash": {"input": 0.15, "output": 0.6, "cache_read": 0.003}}
PROMPT = "Analyse the data."


def ts(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.store = bench.BenchStore(self.dir / "bench.sqlite3")
        self.addCleanup(self.store.close)
        suite = bench.parse_suite({"name": "mini", "tasks": [
            {"id": "a", "prompt": PROMPT, "level": "L1", "checks": [{"type": "external", "value": "dacode"}]},
            {"id": "b", "prompt": "Other.", "level": "L2", "checks": [{"type": "contains", "value": "x"}]}]})
        self.run = self.store.create_run(suite, label="R50", repeats=1, concurrency=1)
        self.run_id = self.run["id"]
        telemetry = TelemetryStore(self.dir / "telemetry.sqlite3", run_id="gw")
        db = telemetry.db

        def call(session, n, choice, provider, model, when, features, out=100, retry=False):
            db.execute("INSERT INTO api_calls (run_id, ts, session_id, api_request_id, api_call_count, provider, "
                       "model, input_tokens, output_tokens, cache_read_tokens, route_policy, route_choice, "
                       "route_features, error_type) VALUES ('gw', ?, ?, ?, ?, ?, ?, 1000, ?, 0, 'random', ?, ?, ?)",
                       (ts(when), session, f"{session}:api:{n}", n, provider, model, out, choice,
                        json.dumps(features), "timeout" if retry else None))

        def tool(session, n, status, ms):
            db.execute("INSERT INTO tool_calls (run_id, ts, session_id, api_request_id, tool_name, status, "
                       "duration_ms) VALUES ('gw', 0, ?, ?, 'execute_code', ?, ?)",
                       (session, f"{session}:api:{n}", status, ms))

        # Task a (timed out, stored without a session id): 3 steps, one retried, DeepSeek in peak hours.
        session_a = bench.expected_session_id(f"Benchmark run {self.run_id}, task a, attempt 1.", PROMPT)
        call(session_a, 1, "strong", "openai-codex", "big-1", "2026-11-12T11:00:00", {"step": 1}, retry=True)
        call(session_a, 1, "strong", "openai-codex", "big-1", "2026-11-12T11:00:05", {"step": 1})
        call(session_a, 2, "cheap", "deepseek", "deepseek-flash", "2026-11-13T07:00:00",   # Friday, peak
             {"step": 2, "prev_tool_timeout": 1})
        call(session_a, 3, "cheap", "deepseek", "deepseek-flash", "2026-11-14T07:00:00", {"step": 3})  # Saturday
        tool(session_a, 1, "timeout", 420000)
        tool(session_a, 2, "error", 500)
        tool(session_a, 2, "ok", 2000)
        self.store.add_result(self.run_id, dict(task_id="a", attempt=1, level="L1", passed=False, error="timeout",
                                                checks=[{"type": "external", "score": 0.56, "passed": False}],
                                                session_id=None))
        # Task b: no telemetry at all.
        self.store.add_result(self.run_id, dict(task_id="b", attempt=1, level="L2", passed=True, checks=[],
                                                session_id="api-0000000000000000"))
        telemetry.db.close()

    def export(self, prompts=None):
        out = self.dir / "out"
        done = bench_export.export(self.store, self.dir, [self.run_id], out, PRICING, prompts)
        with open(out / "tasks.csv", encoding="utf-8") as handle:
            tasks = list(csv.DictReader(handle))
        with open(out / "steps.csv", encoding="utf-8") as handle:
            steps = list(csv.DictReader(handle))
        return done, tasks, steps

    def test_tasks_and_steps_are_joined_and_priced(self):
        done, tasks, steps = self.export({"mini": {"a": PROMPT, "b": "Other."}})
        self.assertEqual((2, 3), (done["tasks"], done["steps"]))
        self.assertEqual([f"{self.run_id}:b#1"], done["unjoined"])
        a = next(t for t in tasks if t["task_id"] == "a")
        self.assertEqual(("3", "1", "2", "1", "0.56", "R50", "random"),
                         (a["steps"], a["strong_steps"], a["cheap_steps"], a["switches"], a["score"],
                          a["label"], a["policy"]))
        self.assertEqual(("1", "1"), (a["tool_timeouts"], a["tool_errors"]))
        first, second, third = steps
        self.assertEqual(("1", "1", "2000", "200"), (first["step_index"], first["retries"], first["input_tokens"],
                                                     first["output_tokens"]))   # the retry is merged
        self.assertEqual(("1", "420.0"), (first["tool_timeouts"], first["tool_max_s"]))
        self.assertEqual(("1", "0", "2.0", "1"), (second["tool_errors"], second["tool_timeouts"],
                                                  second["tool_max_s"], second["f_prev_tool_timeout"]))
        self.assertEqual(("0", "0.56"), (third["task_passed"], third["task_score"]))
        cheap = (1000 * 0.15 + 100 * 0.6) / 1e6
        self.assertAlmostEqual(cheap, float(second["cost_usd"]), places=6)
        self.assertAlmostEqual(2 * cheap, float(second["cost_usd_billed"]), places=6)   # peak hours
        self.assertAlmostEqual(cheap, float(third["cost_usd_billed"]), places=6)        # weekend
        strong = 2 * (1000 * 2.0 + 100 * 10.0) / 1e6
        self.assertAlmostEqual(strong + 3 * cheap, float(a["cost_usd_billed"]), places=6)
        self.assertEqual("", next(t for t in tasks if t["task_id"] == "b")["cost_usd"])

    def test_without_prompts_a_result_without_session_stays_unjoined(self):
        done, tasks, steps = self.export()
        self.assertEqual(0, done["steps"])
        self.assertEqual(2, len(done["unjoined"]))

    def test_peak_factor(self):
        self.assertEqual(2.0, bench_export.peak_factor("deepseek", ts("2026-11-12T09:59:00")))  # Thursday
        self.assertEqual(1.0, bench_export.peak_factor("deepseek", ts("2026-11-12T12:00:00")))
        self.assertEqual(1.0, bench_export.peak_factor("deepseek", ts("2026-11-12T04:00:00")))
        self.assertEqual(1.0, bench_export.peak_factor("openai-codex", ts("2026-11-12T09:00:00")))
        self.assertEqual(1.0, bench_export.peak_factor(None, None))


if __name__ == "__main__":
    unittest.main()
