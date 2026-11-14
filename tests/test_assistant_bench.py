"""Benchmark suites, runner, store and settings snapshots (assistant_core.bench / telemetry)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from assistant_core import bench, reporting
from assistant_core.hermes_plugin import AssistantCore
from assistant_core.settings import CoreSettings, run_label
from assistant_core.telemetry import TelemetryStore, config_snapshot, flatten_snapshot


SECRET_CONFIG = {
    "model": {"default": "openrouter/fast-model", "provider": "openrouter", "temperature": 0.3,
              "base_url": "https://user:hunter2@llm.example.com:8443/v1?key=QUERYSECRET",
              "api_key": "sk-or-v1-SECRETMODELKEY0123456789"},
    "agent": {"max_turns": 40, "reasoning_effort": "low", "system_prompt": "PRIVATE PROMPT TEXT"},
    "smart_model_routing": {"enabled": True, "max_simple_chars": 160,
                            "cheap_model": {"provider": "openrouter", "model": "cheap/one",
                                            "api_key": "sk-cheapSECRET0123456789abcdef"}},
    "auxiliary": {"vision": {"provider": "openai", "model": "gpt-vision", "api_key": "AUXSECRET",
                             "base_url": "https://aux.example.com/v1/secret-path"},
                  "api_token_cache": {"model": "x"}},
    "compression": {"enabled": True, "threshold": 0.5, "summary_model": "sum/one"},
    "delegation": {"model": "dele/one", "api_key": "DELEGATESECRET"},
    "platform_toolsets": {"telegram": ["web", "vision"], "zalo": ["web"]},
    "terminal": {"backend": "docker", "docker_image": "img:1", "env_passthrough": ["SECRET_ENV"]},
    "memory": {"memory_enabled": False, "user_profile_enabled": False},
    "personalities": {"x": "PERSONALITY PROMPT"},
    "providers": {"openrouter": {"api_key": "PROVIDERSECRET"}},
    "mcp_servers": {"github": {"headers": {"Authorization": "Bearer MCPSECRET"}}},
    "custom_value": "a4f8b2c9d1e3f5a7b9c0d2e4f6a8b0c2d4e6f8a0",
}
LEAKS = ("hunter2", "QUERYSECRET", "SECRETMODELKEY", "PRIVATE PROMPT", "cheapSECRET", "AUXSECRET",
         "secret-path", "DELEGATESECRET", "SECRET_ENV", "PERSONALITY", "PROVIDERSECRET", "MCPSECRET", "a4f8b2c9d1")


class SnapshotTests(unittest.TestCase):
    def test_snapshot_is_allowlisted_and_secret_free(self):
        snapshot = config_snapshot(SECRET_CONFIG)
        dump = json.dumps(snapshot)
        for leak in LEAKS:
            self.assertNotIn(leak, dump)
        self.assertEqual("llm.example.com:8443", snapshot["model"]["base_url_host"])
        self.assertEqual("openrouter/fast-model", snapshot["model"]["default"])
        self.assertEqual(40, snapshot["agent"]["max_turns"])
        self.assertEqual("cheap/one", snapshot["smart_model_routing"]["cheap_model"]["model"])
        self.assertEqual({"provider": "openai", "model": "gpt-vision", "base_url_host": "aux.example.com"},
                         snapshot["auxiliary"]["vision"])
        self.assertNotIn("api_token_cache", snapshot["auxiliary"])
        self.assertEqual(["vision", "web"], snapshot["platform_toolsets"]["telegram"])
        self.assertEqual(1, snapshot["mcp_servers"])
        self.assertEqual("docker", snapshot["terminal"]["backend"])
        flat = flatten_snapshot(snapshot)
        self.assertEqual("vision, web", flat["platform_toolsets.telegram"])

    def test_credential_looking_values_in_allowed_keys_are_redacted(self):
        snapshot = config_snapshot({"model": {"default": "sk-proj-abcdefghijklmnop", "provider": "https://x.example/key"},
                                    "compression": {"summary_model": "user:pw@host"}})
        self.assertEqual("[redacted]", snapshot["model"]["default"])
        self.assertEqual("[redacted]", snapshot["model"]["provider"])
        self.assertEqual("[redacted]", snapshot["compression"]["summary_model"])
        self.assertEqual({"version": 1}, config_snapshot("not a mapping"))

    def test_plugin_stores_one_snapshot_per_config_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "skills" / "a").mkdir(parents=True)
            (home / "skills" / "a" / "SKILL.md").write_text("x")
            settings = CoreSettings(home / "ac", run_label="B1")
            core = AssistantCore(settings, config=SECRET_CONFIG, config_text=b"config-bytes", home=home)
            try:
                row = core.telemetry.db.execute("SELECT r.config_hash, r.label, s.snapshot_json FROM runs r "
                                                "JOIN config_snapshots s USING (config_hash)").fetchone()
            finally:
                core.quota.close()
                core.telemetry.close()
            self.assertEqual("B1", row[1])
            snapshot = json.loads(row[2])
            self.assertEqual(1, snapshot["skills_count"])
            for leak in LEAKS:
                self.assertNotIn(leak, row[2])
            self.assertEqual(bench.config_file_hash(None), "")

    def test_run_label_is_cleaned_not_fatal(self):
        self.assertEqual("B2 routing", run_label(" B2 routing\x1b[31m<x> "[:12]))
        self.assertEqual(60, len(run_label("x" * 200)))
        self.assertEqual("ablation-1", CoreSettings.from_mapping({"ASSISTANT_RUN_LABEL": "ablation-1"},
                                                                 default_dir=Path("/tmp")).run_label)


class ReportingTests(unittest.TestCase):
    def test_runs_metrics_snapshots_and_pricing(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "telemetry.sqlite3"
            for run_id, label, config, model, durations in (("r1", "B1", "h1", "m-cheap", (1.0, 2.0, 3.0)),
                                                            ("r2", "B2", "h2", "m-strong", (4.0, 6.0))):
                store = TelemetryStore(db, run_id=run_id, config_hash=config, label=label)
                store.save_snapshot(config, {"model": {"default": model}})
                for index, duration in enumerate(durations):
                    store.post_api_request(api_request_id=f"{run_id}{index}", session_id=f"s-{run_id}",
                                           turn_id=f"t{index // 2}", model=model, api_duration=duration,
                                           usage={"input_tokens": 1000, "output_tokens": 100})
                store.tool_call(tool_name="web_search", status="error", session_id=f"s-{run_id}")
                store.tool_call(tool_name="web_search", status="ok", session_id=f"s-{run_id}")
                store.close()
            overview = {row["run_id"]: row for row in reporting.runs_overview(db)}
            self.assertEqual((2, 2, 1, True), (overview["r1"]["turns"], overview["r1"]["tool_calls"],
                                               overview["r1"]["tool_failures"], overview["r1"]["has_snapshot"]))
            pricing_path = Path(directory) / "pricing.yaml"
            pricing_path.write_text("m-cheap: {input: 1.0, output: 2.0}\nbad: {input: -1}\n")
            pricing = reporting.load_pricing(pricing_path)
            self.assertEqual({"m-cheap"}, set(pricing))
            metrics = reporting.run_metrics(db, ["r1"], pricing)
            self.assertEqual(3, metrics["api_calls"])
            self.assertAlmostEqual(1.5, metrics["api_calls_per_turn"])
            self.assertEqual(2.0, metrics["p50_latency_s"])
            self.assertEqual(3.0, metrics["p95_latency_s"])
            self.assertAlmostEqual(0.5, metrics["tool_failure_rate"])
            self.assertAlmostEqual(3 * (1000 * 1.0 + 100 * 2.0) / 1e6, metrics["cost_usd"])
            self.assertIsNone(reporting.run_metrics(db, ["r2"], pricing)["cost_usd"])  # unpriced model stays unknown
            self.assertEqual({"h1", "h2"}, set(reporting.config_snapshots(db, ["h1", "h2", "missing"])))
            self.assertEqual(3, reporting.session_totals(db, ["s-r1"])["s-r1"]["api_calls"])
            self.assertAlmostEqual(1.0, reporting.estimate_cost({"vendor/m": {"input": 1, "output": 1, "cache_read": 1}},
                                                                "other/m", 1_000_000, 0))

    def test_older_store_without_snapshot_table_reads_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "telemetry.sqlite3"
            with sqlite3.connect(db) as connection:
                connection.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at REAL NOT NULL, "
                                   "hermes_rev TEXT, config_hash TEXT, label TEXT)")
            self.assertEqual({}, reporting.config_snapshots(db, ["h"]))
            self.assertEqual({}, reporting.load_pricing(Path(directory) / "missing.yaml"))


SUITE = {"name": "unit", "description": "Unit suite", "tasks": [
    {"id": "ok-task", "prompt": "please answer ok", "level": "L1", "category": "math",
     "checks": [{"type": "contains", "value": ["forty-two", "42"]}, {"type": "max_latency_s", "value": 30}]},
    {"id": "fail-task", "prompt": "please fail", "level": "L2"},
    {"id": "long-task", "prompt": "please answer long", "level": "L3",
     "checks": [{"type": "min_length", "value": 10}, {"type": "not_contains", "value": "error"}]},
]}


class SuiteTests(unittest.TestCase):
    def test_parse_valid_suite(self):
        suite = bench.parse_suite(SUITE)
        self.assertEqual(("unit", 3), (suite.name, len(suite.tasks)))
        self.assertEqual({"L1": 1, "L2": 1, "L3": 1}, suite.levels())
        self.assertEqual(("forty-two", "42"), suite.tasks[0].checks[0].value)
        self.assertEqual(suite.digest, bench.parse_suite(json.loads(json.dumps(SUITE))).digest)

    def test_strict_validation(self):
        task = SUITE["tasks"][0]
        bad = [
            dict(SUITE, extra=1), dict(SUITE, name="Bad Name"), dict(SUITE, tasks=[]),
            dict(SUITE, tasks=[task, task]), dict(SUITE, tasks=[dict(task, level="L4")]),
            dict(SUITE, tasks=[dict(task, unknown=1)]), dict(SUITE, tasks=[dict(task, prompt="")]),
            dict(SUITE, tasks=[dict(task, prompt="x" * 8001)]), dict(SUITE, tasks=[dict(task, timeout_s=1)]),
            dict(SUITE, tasks=[dict(task, checks=[{"type": "eval", "value": "x"}])]),
            dict(SUITE, tasks=[dict(task, checks=[{"type": "regex", "value": "("}])]),
            dict(SUITE, tasks=[dict(task, checks=[{"type": "min_length", "value": True}])]),
            dict(SUITE, tasks=[dict(task, checks=[{"type": "contains", "value": ""}])]),
            dict(SUITE, tasks=[dict(task, id=f"t{i}") for i in range(301)]),
            dict(SUITE, tasks=[dict(task, timeout_s=1801)]),
            dict(SUITE, tasks=[dict(task, checks=[{"type": "answers", "value": {}}])]),
            dict(SUITE, tasks=[dict(task, checks=[{"type": "answers", "value": {"mean": 34.65}}])]),
            dict(SUITE, tasks=[dict(task, checks=[{"type": "answers", "value": {"flag": False}}])]),
            dict(SUITE, tasks=[dict(task, checks=[{"type": "answers", "value": {"bad name": "1"}}])]),
            dict(SUITE, tasks=[dict(task, checks=[{"type": "answers", "value": {"a": "x]y"}}])]),
            dict(SUITE, tasks=[dict(task, checks=[{"type": "answers", "value": {f"n{i}": "1" for i in range(13)}}])]),
        ]
        for data in bad:
            with self.assertRaises(bench.BenchError, msg=str(data)[:120]):
                bench.parse_suite(data)

    def test_bundled_suites_are_valid_and_home_suites_load(self):
        suites, problems = bench.load_suites(None)
        self.assertEqual([], problems)
        self.assertIn("assistant-basics", suites)
        self.assertIn("tool-use", suites)
        self.assertTrue(all(suites["assistant-basics"].levels().values()))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "benchmarks").mkdir()
            import yaml
            (home / "benchmarks" / "mine.yaml").write_text(yaml.safe_dump(SUITE))
            (home / "benchmarks" / "dupe.yaml").write_text(yaml.safe_dump(dict(SUITE, name="tool-use")))
            (home / "benchmarks" / "broken.yaml").write_text("name: [")
            (home / "benchmarks" / "link.yaml").symlink_to(home / "benchmarks" / "mine.yaml")
            suites, problems = bench.load_suites(home)
            self.assertEqual("home", suites["unit"].source)
            self.assertEqual("bundled", suites["tool-use"].source)
            self.assertEqual({"broken.yaml", "dupe.yaml", "link.yaml"}, {p["file"] for p in problems})

    def test_checks(self):
        checks = bench.parse_suite({"name": "c", "tasks": [{"id": "a", "prompt": "p", "level": "L1", "checks": [
            {"type": "contains", "value": "HÀ NỘI"}, {"type": "not_contains", "value": "sorry"},
            {"type": "regex", "value": r"\d{4}"}, {"type": "max_latency_s", "value": 2},
            {"type": "min_length", "value": 5}, {"type": "json_valid"}]}]}).tasks[0].checks
        good = bench.evaluate_checks(checks, '```json\n{"city": "Hà Nội", "year": 2026}\n```', 1.0)
        self.assertTrue(all(c["passed"] for c in good), good)
        bad = bench.evaluate_checks(checks, "Sorry", 3.0)
        self.assertEqual([False, False, False, False, True, False], [c["passed"] for c in bad])
        self.assertNotIn("Sorry", json.dumps(bad))  # details describe the check, not the reply

    def test_answers_check_scores_like_the_dabench_script(self):
        def score(expected, reply, **options):
            check = bench.parse_suite({"name": "c", "tasks": [{"id": "a", "prompt": "p", "level": "L1", "checks": [
                dict({"type": "answers", "value": expected}, **options)]}]}).tasks[0].checks
            return bench.evaluate_checks(check, reply, 1.0)[0]

        expected = {"mean_fare": "34.65", "verdict": "No", "empty": ""}
        full = score(expected, "Result:\n@mean_fare[34.650000]\n@verdict[No]\n@empty[]")
        self.assertEqual((True, 3, 3, "3/3 answers match"),
                         (full["passed"], full["matched"], full["total"], full["detail"]))
        # Text is compared exactly, numbers within 1e-6, and a repeated name keeps its last value.
        part = score(expected, "@mean_fare[1] @verdict[no] ... @mean_fare[ 34.65 ]")
        self.assertEqual((False, 1, 3), (part["passed"], part["matched"], part["total"]))
        self.assertTrue(score({"verdict": "No"}, "@verdict[no]", ignore_case=True)["passed"])
        self.assertEqual(0, score(expected, "The mean fare is 34.65.")["matched"])
        self.assertFalse(score({"r": "0.21"}, "@r[0.2100011]")["passed"])
        self.assertNotIn("34.65", json.dumps(full))

    def test_answer_accuracy_follows_the_three_dabench_metrics(self):
        def result(matched, total, **extra):
            return dict(dict(passed=matched == total, error=None, level="L1", checks=[
                dict(type="answers", passed=matched == total, matched=matched, total=total, detail="")]), **extra)

        results = [result(1, 1), result(1, 2), result(0, 3),
                   dict(passed=False, error="timeout", level="L1", checks=[]),
                   dict(passed=True, error=None, level="L1", checks=[dict(type="contains", passed=True, detail="")])]
        answers = bench.summarize({}, results)["answers"]
        self.assertEqual(3, answers["questions"])
        self.assertAlmostEqual(1 / 3, answers["by_question"])
        self.assertAlmostEqual((1 + 0.5 + 0) / 3, answers["proportional"])
        self.assertAlmostEqual(2 / 6, answers["by_subquestion"])
        self.assertIsNone(bench.summarize({}, results[3:])["answers"])


class TargetTests(unittest.TestCase):
    def test_target_rules(self):
        env = {"HERMES_CONSOLE_CHAT_API_URL": "http://127.0.0.1:8642/v1", "API_SERVER_KEY": "k"}
        target = bench.resolve_target(env)
        self.assertEqual(("http://127.0.0.1:8642/v1/chat/completions", "127.0.0.1:8642", "hermes"),
                         (target.endpoint, target.host, target.model))
        self.assertNotIn("'k'", repr(target))
        self.assertEqual("bench.example.com",
                         bench.resolve_target(dict(env, HERMES_BENCH_API_URL="https://bench.example.com")).host)
        for url in ("http://bench.example.com", "https://u:p@x.example.com", "https://x.example.com/v1?q=1",
                    "ftp://x", "https://x.example.com/other"):
            with self.assertRaises(bench.BenchError):
                bench.resolve_target(dict(env, HERMES_BENCH_API_URL=url))
        with self.assertRaises(bench.BenchError):
            bench.resolve_target({"HERMES_BENCH_API_URL": "http://localhost:1"})
        with self.assertRaises(bench.BenchError):
            bench.resolve_target(dict(env, API_SERVER_KEY="a\nb"))


class FakeHermes:
    """Loopback stand-in for the Hermes API server; counts every request."""

    def __init__(self):
        self.requests, self.gate, self.hold = [], threading.Event(), False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(dict(path=self.path, auth=self.headers.get("Authorization"),
                                           agent=self.headers.get("User-Agent"), body=body))
                prompt = body["messages"][-1]["content"]
                if outer.hold:
                    outer.gate.wait(10)
                if "fail" in prompt:
                    return self._send(500, {"error": "boom"})
                if "slow" in prompt:
                    time.sleep(1.5)
                content = "The answer is 42." if "ok" in prompt else (
                    "A long reply sk-abcdefghijklmnop0123456789 " + "x" * 5000)
                self._send(200, {"model": "hermes-agent", "choices": [{"message": {"role": "assistant", "content": content}}],
                                 "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}},
                           session="api-" + str(len(outer.requests)).zfill(4))

            def _send(self, status, payload, session=None):
                data = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                except (BrokenPipeError, ConnectionResetError):
                    return
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if session:
                    self.send_header("X-Hermes-Session-Id", session)
                try:
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the client gave up (timeout test)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.environ = {"HERMES_BENCH_API_URL": self.url, "API_SERVER_KEY": "test-key"}

    def close(self):
        self.gate.set()
        self.server.shutdown()
        self.server.server_close()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.home = self.dir / "home"
        self.home.mkdir()
        (self.home / "config.yaml").write_text("model:\n  default: m-one\n  api_key: sk-HOMESECRET0123456789abc\n")
        self.store = bench.BenchStore(self.dir / "ac" / "bench.sqlite3")
        self.fake = FakeHermes()
        self.target = bench.resolve_target(self.fake.environ)
        self.suite = bench.parse_suite(SUITE)

    def tearDown(self):
        self.fake.close()
        self.store.close()
        self.temp.cleanup()

    def test_run_records_results_once_without_retries(self):
        run = bench.prepare_run(self.store, self.suite, self.target, label="B1", repeats=2, concurrency=2,
                                home=self.home, telemetry_db=self.dir / "ac" / "telemetry.sqlite3")
        self.assertEqual(("queued", 6, 16), (run["status"], run["total"], len(run["config_hash"])))
        self.assertNotIn("HOMESECRET", json.dumps(run["snapshot"]))
        self.assertEqual("m-one", run["snapshot"]["model"]["default"])
        finished = bench.BenchRunner(self.store, run["id"], self.suite, self.target).run()
        self.assertEqual(("done", 6, 4, 2), (finished["status"], finished["completed"], finished["passed"], finished["errors"]))
        self.assertEqual(6, len(self.fake.requests))  # the 500s were not retried
        request = self.fake.requests[0]
        self.assertEqual(("/v1/chat/completions", "Bearer test-key"), (request["path"], request["auth"]))
        self.assertIn(run["id"], request["agent"])
        self.assertEqual(2, len(request["body"]["messages"]))
        system_prompts = {r["body"]["messages"][0]["content"] for r in self.fake.requests}
        self.assertEqual(6, len(system_prompts))  # each task and attempt gets a fresh session
        results = {(r["task_id"], r["attempt"]): r for r in self.store.results(run["id"])}
        ok, fail, long = results[("ok-task", 1)], results[("fail-task", 2)], results[("long-task", 1)]
        self.assertTrue(ok["passed"])
        self.assertEqual((120, 30, 150, "hermes-agent"), (ok["input_tokens"], ok["output_tokens"], ok["total_tokens"],
                                                          ok["response_model"]))
        self.assertTrue(ok["session_id"].startswith("api-"))
        self.assertEqual(("http_500", False), (fail["error"], fail["passed"]))
        self.assertEqual(2000, len(long["response_text"]))
        self.assertGreater(long["response_length"], 5000)
        self.assertNotIn("sk-abcdefghijklmnop", long["response_text"])
        summary = bench.summarize(finished, list(results.values()))
        self.assertAlmostEqual(4 / 6, summary["pass_rate"])
        self.assertEqual(0.0, summary["levels"]["L2"]["pass_rate"])
        self.assertEqual(150, summary["tokens_per_task"])
        dump = "\n".join(self.store.db.iterdump())
        self.assertNotIn("test-key", dump)
        self.assertNotIn("HOMESECRET", dump)

    def test_timeout_is_recorded_not_retried(self):
        task = bench.Task("slow", "please be slow", "L1", timeout_s=0.3)
        import httpx
        with httpx.Client(trust_env=False) as client:
            result = bench.execute_task(client, self.target, "20260101-000000-abcdef", task, 1)
        self.assertEqual(("timeout", False), (result["error"], result["passed"]))
        self.assertEqual(1, len(self.fake.requests))
        # No response header arrived, yet the step telemetry can still be joined to this task.
        sent = self.fake.requests[0]["body"]["messages"]
        self.assertEqual(bench.expected_session_id(sent[0]["content"], sent[1]["content"]), result["session_id"])
        self.assertRegex(result["session_id"], r"^api-[0-9a-f]{16}$")

    def test_unreachable_target_is_a_connection_error(self):
        target = bench.resolve_target({"HERMES_BENCH_API_URL": "http://127.0.0.1:9/v1", "API_SERVER_KEY": "k"})
        import httpx
        with httpx.Client(trust_env=False) as client:
            result = bench.execute_task(client, target, "r", bench.Task("a", "ok", "L1", timeout_s=5), 1)
        self.assertEqual("connection_error", result["error"])

    def test_one_active_run_cancellation_and_delete(self):
        self.fake.hold = True
        run = bench.prepare_run(self.store, self.suite, self.target, repeats=1, concurrency=1)
        with self.assertRaises(bench.BenchError):
            bench.prepare_run(self.store, self.suite, self.target)
        with self.assertRaises(bench.BenchError):
            self.store.delete_run(run["id"])
        thread = bench.start_background(self.store, run, self.suite, self.target)
        deadline = time.time() + 5
        while not self.fake.requests and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(bench.cancel(self.store, run["id"]))
        self.fake.gate.set()
        thread.join(10)
        final = self.store.get_run(run["id"])
        self.assertEqual(("cancelled", 1), (final["status"], final["completed"]))
        self.assertEqual(1, len(self.fake.requests))
        self.assertFalse(bench.cancel(self.store, run["id"]))
        self.assertTrue(self.store.delete_run(run["id"]))
        self.assertEqual([], self.store.results(run["id"]))

    def test_stale_runs_from_dead_processes_fail_on_load(self):
        run = bench.prepare_run(self.store, self.suite, self.target)
        self.store.db.execute("UPDATE bench_runs SET status='running', owner=? WHERE id=?",
                              (f"{bench.socket.gethostname()[:60]}:{os.getpid()}:oldtoken", run["id"]))
        self.assertIsNone(self.store.active_run())
        final = self.store.get_run(run["id"])
        self.assertEqual("failed", final["status"])
        self.assertIn("restarted", final["reason"])
        other = bench.prepare_run(self.store, self.suite, self.target)
        self.store.db.execute("UPDATE bench_runs SET heartbeat_at=? WHERE id=?", (time.time() - 3600, other["id"]))
        self.assertEqual(1, self.store.recover_stale())

    def test_resume_keeps_results_and_sends_only_missing_tasks(self):
        self.fake.hold = True
        run = bench.prepare_run(self.store, self.suite, self.target, label="R1", home=self.home)
        with self.assertRaises(bench.BenchError):
            self.store.reopen(run["id"])  # queued: not resumable
        thread = bench.start_background(self.store, run, self.suite, self.target)
        deadline = time.time() + 5
        while not self.fake.requests and time.time() < deadline:
            time.sleep(0.02)
        bench.cancel(self.store, run["id"])
        self.fake.gate.set()
        thread.join(10)
        stopped = self.store.get_run(run["id"])
        self.assertEqual(("cancelled", 1), (stopped["status"], stopped["completed"]))
        started_at = stopped["started_at"]
        self.fake.hold = False
        reopened = bench.resume_run(self.store, run["id"], self.suite, home=self.home)
        self.assertEqual(("queued", 1, None), (reopened["status"], reopened["completed"], reopened["finished_at"]))
        final = bench.BenchRunner(self.store, run["id"], self.suite, self.target).run()
        self.assertEqual(("done", 3, 2, 1), (final["status"], final["completed"], final["passed"], final["errors"]))
        self.assertEqual(started_at, final["started_at"])
        self.assertEqual(3, len(self.fake.requests))  # the first task was not sent again
        self.assertEqual(["fail-task", "long-task", "ok-task"],
                         sorted(r["task_id"] for r in self.store.results(run["id"])))
        with self.assertRaises(bench.BenchError):
            bench.resume_run(self.store, run["id"], self.suite, home=self.home)  # done: nothing to resume

    def test_resume_retries_listed_errors_and_refuses_changed_inputs(self):
        run = bench.prepare_run(self.store, self.suite, self.target, label="R2", home=self.home)
        bench.BenchRunner(self.store, run["id"], self.suite, self.target).run()
        self.store.finish(run["id"], "done")
        self.store.db.execute("UPDATE bench_runs SET status='failed' WHERE id=?", (run["id"],))
        other = bench.parse_suite(dict(SUITE, tasks=SUITE["tasks"][:2]))
        with self.assertRaises(bench.BenchError):
            bench.resume_run(self.store, run["id"], other, home=self.home)  # suite changed
        (self.home / "config.yaml").write_text("model:\n  default: m-two\n")
        with self.assertRaises(bench.BenchError):
            bench.resume_run(self.store, run["id"], self.suite, home=self.home)  # config changed
        reopened = bench.resume_run(self.store, run["id"], self.suite, home=self.home, force=True,
                                    retry_errors=("http_500",))
        self.assertEqual((2, 0), (reopened["completed"], reopened["errors"]))  # the 500 was dropped
        before = len(self.fake.requests)
        final = bench.BenchRunner(self.store, run["id"], self.suite, self.target).run()
        self.assertEqual((1, "done", 3), (len(self.fake.requests) - before, final["status"], final["completed"]))

    def test_bounds(self):
        for kwargs in (dict(repeats=0), dict(repeats=6), dict(concurrency=5), dict(repeats=1.5), dict(label="x" * 61),
                       dict(label="bad\nlabel")):
            with self.assertRaises(bench.BenchError):
                self.store.create_run(self.suite, **dict(dict(label="", repeats=1, concurrency=1), **kwargs))

    def test_cli(self):
        env = dict(self.fake.environ, HERMES_HOME=str(self.home), ASSISTANT_DATA_DIR=str(self.dir / "ac"))
        import yaml
        (self.home / "benchmarks").mkdir()
        (self.home / "benchmarks" / "unit.yaml").write_text(yaml.safe_dump(SUITE))
        out = io.StringIO()
        self.assertEqual(0, bench.main(["suites"], env, out))
        self.assertIn("unit", out.getvalue())
        out = io.StringIO()
        self.assertEqual(0, bench.main(["run", "--suite", "unit", "--label", "B1", "--concurrency", "2"], env, out))
        out = io.StringIO()
        self.assertEqual(0, bench.main(["run", "--suite", "unit", "--label", "B2"], env, out))
        runs = self.store.list_runs()
        self.assertEqual(2, len(runs))
        out = io.StringIO()
        self.assertEqual(0, bench.main(["list"], env, out))
        self.assertIn("B2", out.getvalue())
        out = io.StringIO()
        self.assertEqual(0, bench.main(["show", runs[0]["id"]], env, out))
        self.assertIn("http_500", out.getvalue())
        out = io.StringIO()
        self.assertEqual(0, bench.main(["compare", runs[0]["id"], runs[1]["id"]], env, out))
        self.assertIn("Pass rate L3", out.getvalue())
        self.assertEqual(2, bench.main(["compare", runs[0]["id"]], env, io.StringIO()))
        self.assertEqual(2, bench.main(["run", "--suite", "nope"], env, io.StringIO()))


if __name__ == "__main__":
    unittest.main()
