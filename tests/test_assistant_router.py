"""Per-step model routing: settings, policies, the middleware and what telemetry keeps."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from assistant_core import bench, reporting, router
from assistant_core.hermes_plugin import AssistantCore
from assistant_core.router import CHEAP, STRONG, Decision, Router
from assistant_core.settings import CoreSettings, RouterSettings, SettingsError
from assistant_core.telemetry import TelemetryStore


ENV = {"ASSISTANT_ROUTER_POLICY": "rule", "ASSISTANT_ROUTER_STRONG_MODEL": "big-1",
       "ASSISTANT_ROUTER_CHEAP_MODEL": "small-1"}


def settings(policy="rule", **extra):
    return RouterSettings.from_mapping(dict(ENV, ASSISTANT_ROUTER_POLICY=policy, **extra))


def request(model="big-1", messages=3):
    return {"model": model, "messages": [{"role": "user", "content": "private text"}] * messages,
            "reasoning": {"effort": "high"}}


def step(target, number, *, session="s1", turn="t1", **extra):
    """Call the middleware for model call ``number`` of a turn, the way Hermes does."""
    context = dict(session_id=session, turn_id=turn, api_request_id=f"{turn}:api:{number}",
                   api_call_count=number, platform="api_server", provider="openai-codex", model="big-1")
    context.update(extra)
    return target.llm_request(request=context.pop("request", request()), **context)


class SettingsTests(unittest.TestCase):
    def test_off_by_default_and_needs_nothing_else(self):
        self.assertFalse(RouterSettings.from_mapping({}).enabled)
        self.assertFalse(RouterSettings.from_mapping({"ASSISTANT_ROUTER_POLICY": " OFF "}).enabled)
        core = CoreSettings.from_mapping({}, default_dir=Path("/tmp/x"))
        self.assertEqual("off", core.router.policy)

    def test_full_settings(self):
        parsed = settings("random", ASSISTANT_ROUTER_PROVIDER="openai-codex", ASSISTANT_ROUTER_STRONG_SHARE="0.25",
                          ASSISTANT_ROUTER_SEED="7", ASSISTANT_ROUTER_STRONG_FIRST_STEPS="2",
                          ASSISTANT_ROUTER_PLATFORMS="api_server, telegram",
                          ASSISTANT_ROUTER_CHEAP_REQUEST='{"reasoning": {"effort": "low"}, "service_tier": null}')
        self.assertEqual(("random", "big-1", "small-1", "openai-codex", 0.25, 7, 2),
                         (parsed.policy, parsed.strong_model, parsed.cheap_model, parsed.provider,
                          parsed.strong_share, parsed.seed, parsed.strong_first_steps))
        self.assertEqual(frozenset({"api_server", "telegram"}), parsed.platforms)
        self.assertEqual((("reasoning", {"effort": "low"}), ("service_tier", None)), parsed.cheap_request)
        described = parsed.describe()
        self.assertEqual(["reasoning", "service_tier"], described["cheap_request_keys"])
        json.dumps(described)

    def test_invalid_values_fail_closed(self):
        bad = [
            {"ASSISTANT_ROUTER_POLICY": "rule"},                                   # models missing
            dict(ENV, ASSISTANT_ROUTER_CHEAP_MODEL="has space"),
            dict(ENV, ASSISTANT_ROUTER_POLICY="Bad Policy!"),
            dict(ENV, ASSISTANT_ROUTER_STRONG_SHARE="1.5"),
            dict(ENV, ASSISTANT_ROUTER_STRONG_SHARE="nan"),
            dict(ENV, ASSISTANT_ROUTER_SEED="-1"),
            dict(ENV, ASSISTANT_ROUTER_STRONG_FIRST_STEPS="many"),
            dict(ENV, ASSISTANT_ROUTER_CHEAP_REQUEST="not json"),
            dict(ENV, ASSISTANT_ROUTER_CHEAP_REQUEST='["list"]'),
            dict(ENV, ASSISTANT_ROUTER_CHEAP_REQUEST='{"model": "other"}'),
            dict(ENV, ASSISTANT_ROUTER_CHEAP_REQUEST='{"messages": []}'),
        ]
        for values in bad:
            with self.assertRaises(SettingsError, msg=str(values)):
                RouterSettings.from_mapping(values)
        with self.assertRaises(SettingsError):  # a well-formed name that is not a policy
            Router(settings("smart-v9"))


class PolicyTests(unittest.TestCase):
    def test_fixed_policies_rewrite_only_when_the_model_differs(self):
        strong = Router(settings("always-strong"))
        self.assertIsNone(step(strong, 1))                     # already the strong model
        self.assertEqual(STRONG, strong.decision_for(session_id="s1", api_request_id="t1:api:1")["choice"])
        cheap = Router(settings("always-cheap"))
        result = step(cheap, 1)
        self.assertEqual("small-1", result["request"]["model"])
        self.assertEqual(("assistant-core-router", "always-cheap", "cheap: policy always-cheap"),
                         (result["source"], result["name"], result["reason"]))
        self.assertEqual(3, len(result["request"]["messages"]))  # the conversation is passed through

    def test_rule_uses_strong_first_and_after_a_tool_error(self):
        target = Router(settings("rule"))
        self.assertIsNone(step(target, 1))                               # first step: strong (the default)
        target.tool_result(session_id="s1", tool_name="read_file", status="ok")
        self.assertEqual("small-1", step(target, 2)["request"]["model"])  # routine step
        target.tool_result(session_id="s1", tool_name="execute_code", status="error")
        self.assertIsNone(step(target, 3))                               # after the failure: strong
        record = target.decision_for(session_id="s1", api_request_id="t1:api:3")
        self.assertEqual((STRONG, "tool error in the previous step", True),
                         (record["choice"], record["reason"], record["switched"]))
        self.assertEqual(dict(step=3, session_step=3, prev_choice=CHEAP, prev_tools=1, prev_tool_errors=1,
                              last_tool="execute_code", error_streak=1, messages=3),
                         {k: v for k, v in record["features"].items() if k != "chars"})
        self.assertGreater(record["features"]["chars"], 0)
        target.tool_result(session_id="s1", tool_name="execute_code", status="ok")
        self.assertEqual("small-1", step(target, 4)["request"]["model"])  # the error is consumed
        # A new turn starts again from the strong model; the session count keeps going.
        self.assertIsNone(step(target, 1, turn="t2"))
        record = target.decision_for(session_id="s1", api_request_id="t2:api:1")
        self.assertEqual((1, 5), (record["features"]["step"], record["features"]["session_step"]))

    def test_rule_first_steps_can_be_zero_or_more(self):
        self.assertEqual("small-1", step(Router(settings(ASSISTANT_ROUTER_STRONG_FIRST_STEPS="0")), 1)
                         ["request"]["model"])
        two = Router(settings(ASSISTANT_ROUTER_STRONG_FIRST_STEPS="2"))
        self.assertEqual([None, None, "small-1"],
                         [(step(two, n) or {}).get("request", {}).get("model") for n in (1, 2, 3)])

    def test_blocked_tool_counts_as_an_error(self):
        target = Router(settings("rule"))
        step(target, 1)
        target.tool_result(session_id="s1", tool_name="memory", blocked=True)
        self.assertIsNone(step(target, 2))

    def test_random_is_reproducible_and_follows_the_share(self):
        def choices(seed, share="0.3", count=400):
            target = Router(settings("random", ASSISTANT_ROUTER_SEED=seed, ASSISTANT_ROUTER_STRONG_SHARE=share))
            out = []
            for number in range(1, count + 1):
                step(target, number)
                out.append(target.decision_for(session_id="s1", api_request_id=f"t1:api:{number}")["choice"])
            return out

        first = choices("1")
        self.assertEqual(first, choices("1"))
        self.assertNotEqual(first, choices("2"))
        self.assertTrue(0.22 < first.count(STRONG) / len(first) < 0.38, first.count(STRONG))
        self.assertEqual({CHEAP}, set(choices("1", share="0", count=20)))
        self.assertEqual({STRONG}, set(choices("1", share="1", count=20)))

    def test_a_retry_keeps_the_decision_and_does_not_advance_the_state(self):
        target = Router(settings("random", ASSISTANT_ROUTER_SEED="3"))
        step(target, 1)
        first = target.decision_for(session_id="s1", api_request_id="t1:api:1")
        target.tool_result(session_id="s1", tool_name="execute_code", status="error")
        step(target, 1)                                             # same step id: a retry
        self.assertIs(first, target.decision_for(session_id="s1", api_request_id="t1:api:1"))
        step(target, 2)
        second = target.decision_for(session_id="s1", api_request_id="t1:api:2")
        self.assertEqual((2, 1), (second["features"]["step"], second["features"]["prev_tool_errors"]))
        self.assertIsNone(target.decision_for(session_id="s1", api_request_id="t1:api:1"))  # only the latest step

    def test_sessions_do_not_share_state(self):
        target = Router(settings("rule"))
        step(target, 1, session="a")
        target.tool_result(session_id="a", tool_name="execute_code", status="error")
        step(target, 1, session="b")
        self.assertEqual("small-1", step(target, 2, session="b")["request"]["model"])
        self.assertIsNone(step(target, 2, session="a"))


class MiddlewareTests(unittest.TestCase):
    def test_tier_request_fields_are_set_and_removed(self):
        target = Router(settings("always-cheap",
                                 ASSISTANT_ROUTER_CHEAP_REQUEST='{"reasoning": {"effort": "low"}, "top_p": null}'))
        original = dict(request(), top_p=0.9)
        result = step(target, 1, request=original)["request"]
        self.assertEqual(("small-1", {"effort": "low"}), (result["model"], result["reasoning"]))
        self.assertNotIn("top_p", result)
        self.assertEqual(("big-1", 0.9), (original["model"], original["top_p"]))  # the input is not mutated
        # The strong tier can carry fields too, even when the model name is already right.
        strong = Router(settings("always-strong", ASSISTANT_ROUTER_STRONG_REQUEST='{"service_tier": "priority"}'))
        self.assertEqual("priority", step(strong, 1)["request"]["service_tier"])

    def test_requests_outside_the_scope_are_left_alone(self):
        target = Router(settings("always-cheap", ASSISTANT_ROUTER_PROVIDER="openai-codex",
                                 ASSISTANT_ROUTER_PLATFORMS="api_server"))
        self.assertIsNone(step(target, 1, platform="telegram"))
        self.assertIsNone(step(target, 1, provider="anthropic"))
        self.assertIsNone(step(target, 1, request={"messages": []}))        # no model field
        self.assertIsNone(step(target, 1, request="not a mapping"))
        self.assertIsNone(target.decision_for(session_id="s1", api_request_id="t1:api:1"))
        self.assertEqual("small-1", step(target, 1)["request"]["model"])

    def test_responses_style_requests_are_sized_from_input(self):
        target = Router(settings("always-cheap"))
        step(target, 1, request={"model": "big-1", "instructions": "x", "input": [{"type": "message"}] * 5})
        record = target.decision_for(session_id="s1", api_request_id="t1:api:1")
        self.assertEqual(5, record["features"]["messages"])

    def test_a_failing_policy_leaves_the_request_untouched(self):
        self.addCleanup(router.POLICIES.pop, "broken", None)
        self.addCleanup(router.POLICIES.pop, "sloppy", None)
        router.register_policy("broken", lambda features, config: 1 / 0)
        router.register_policy("sloppy", lambda features, config: "cheap")
        with self.assertLogs("assistant_core.router", "WARNING"):
            self.assertIsNone(step(Router(settings("broken")), 1))
            self.assertIsNone(step(Router(settings("sloppy")), 1))

    def test_a_new_policy_plugs_in(self):
        self.addCleanup(router.POLICIES.pop, "by-size", None)
        router.register_policy("by-size", lambda features, config: Decision(
            CHEAP if features["messages"] < 10 else STRONG, "context size", features["messages"] / 10))
        target = Router(settings("by-size"))
        self.assertEqual("small-1", step(target, 1)["request"]["model"])
        self.assertIsNone(step(target, 2, request=request(messages=12)))
        record = target.decision_for(session_id="s1", api_request_id="t1:api:2")
        self.assertEqual((STRONG, 1.2, "by-size"), (record["choice"], record["score"], record["policy"]))


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def core(self, values=ENV):
        core = AssistantCore(CoreSettings.from_mapping(dict(values, ASSISTANT_DATA_DIR=str(self.dir)),
                                                       default_dir=self.dir), config={})
        self.addCleanup(core.quota.close)
        self.addCleanup(core.telemetry.close)
        return core

    def run_task(self, core, session, outcomes):
        """One turn: model call, then a tool call with each outcome, ending with a final model call."""
        ids = dict(session_id=session, turn_id="t1", platform="api_server", provider="openai-codex", model="big-1")
        for number, outcome in enumerate([*outcomes, None], 1):
            call = dict(ids, api_request_id=f"{session}:t1:api:{number}", api_call_count=number)
            core.llm_request(request=request(), **call)
            core.pre_api_request(**call)
            core.post_api_request(**call, api_duration=1.0, usage={"input_tokens": 100, "output_tokens": 10},
                                  response_model=None)
            if outcome:
                core.post_tool_call(tool_name="execute_code", status=outcome, args={"code": "SECRET"},
                                    result="SECRET", **{k: ids[k] for k in ("session_id", "turn_id")})

    def test_rows_hold_the_routed_model_and_the_decision(self):
        core = self.core()
        self.run_task(core, "s1", ["ok", "error"])
        db = core.telemetry.db
        rows = db.execute("SELECT model, default_model, route_policy, route_choice, route_reason, route_switched "
                          "FROM api_calls ORDER BY id").fetchall()
        self.assertEqual([("big-1", "big-1", "rule", "strong", "first step of the turn", 0),
                          ("small-1", "big-1", "rule", "cheap", "routine step", 1),
                          ("big-1", "big-1", "rule", "strong", "tool error in the previous step", 1)], rows)
        features = json.loads(db.execute("SELECT route_features FROM api_calls ORDER BY id DESC").fetchone()[0])
        self.assertEqual(("execute_code", 1), (features["last_tool"], features["prev_tool_errors"]))
        self.assertNotIn("step_id", features)
        policy, stored = db.execute("SELECT policy, settings_json FROM router_runs").fetchone()
        self.assertEqual(("rule", "small-1"), (policy, json.loads(stored)["cheap_model"]))
        dump = "\n".join(db.iterdump())
        self.assertNotIn("private text", dump)
        self.assertNotIn("SECRET", dump)

    def test_errors_and_retries_carry_the_same_decision(self):
        core = self.core(dict(ENV, ASSISTANT_ROUTER_POLICY="always-cheap"))
        call = dict(session_id="s1", turn_id="t1", api_request_id="t1:api:1", api_call_count=1, model="big-1")
        for attempt in (0, 1):
            self.assertEqual("small-1", core.llm_request(request=request(), **call)["request"]["model"])
            core.pre_api_request(retry_count=attempt, **call)
            if attempt == 0:
                core.api_request_error(**call, error=TimeoutError("x"), status_code=429)
            else:
                core.post_api_request(**call, api_duration=1.0, usage={})
        rows = core.telemetry.db.execute("SELECT model, route_choice, retry_count, error_type FROM api_calls "
                                         "ORDER BY id").fetchall()
        self.assertEqual([("small-1", "cheap", 0, "TimeoutError"), ("small-1", "cheap", 1, None)], rows)

    def test_router_off_changes_nothing(self):
        core = self.core({})
        self.assertIsNone(core.router)
        self.assertIsNone(core.llm_request(request=request(), session_id="s1", api_request_id="a"))
        core.post_api_request(api_request_id="a", session_id="s1", model="big-1", api_duration=1.0, usage={})
        row = core.telemetry.db.execute("SELECT model, default_model, route_policy, route_choice FROM api_calls"
                                        ).fetchone()
        self.assertEqual(("big-1", None, None, None), row)
        self.assertEqual(0, core.telemetry.db.execute("SELECT COUNT(*) FROM router_runs").fetchone()[0])

    def test_an_older_store_gains_the_route_columns(self):
        path = self.dir / "telemetry.sqlite3"
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at REAL NOT NULL, hermes_rev TEXT, config_hash TEXT,
                               label TEXT);
            CREATE TABLE api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL NOT NULL, platform TEXT, session_id TEXT,
                task_id TEXT, turn_id TEXT, api_request_id TEXT, api_call_count INTEGER, retry_count INTEGER,
                provider TEXT, model TEXT, response_model TEXT, duration_s REAL, ttfb_s REAL, finish_reason TEXT,
                error_type TEXT, status_code INTEGER, input_tokens INTEGER, output_tokens INTEGER,
                cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                message_count INTEGER, context_length INTEGER, tool_call_count INTEGER);
            INSERT INTO api_calls (run_id, ts, session_id, model) VALUES ('old', 1.0, 's0', 'big-1');
        """)
        old.close()
        self.assertEqual("big-1", reporting.session_steps(path, "s0")[0]["model"])  # readable before the upgrade
        self.assertNotIn("route_choice", reporting.session_steps(path, "s0")[0])
        for _ in range(2):  # opening twice must not try to add the columns again
            store = TelemetryStore(path, run_id="new")
            store.post_api_request(api_request_id="a", session_id="s1", model="big-1", api_duration=1.0, usage={},
                                   route=dict(policy="rule", choice="cheap", reason="routine step",
                                              model="small-1", default_model="big-1", switched=False,
                                              features={"step": 2}))
            store.close()
        steps = reporting.session_steps(path, "s1")
        self.assertEqual(("small-1", "cheap", {"step": 2}),
                         (steps[0]["model"], steps[0]["route_choice"], steps[0]["route_features"]))
        self.assertIsNone(reporting.session_steps(path, "s0")[0]["route_choice"])

    def test_console_activity_shows_the_routed_model_and_reason(self):
        from hermes_zalo import admin_observe as observe
        from hermes_zalo.console_sections import ConsoleContext

        core = self.core()
        self.run_task(core, "s1", ["ok"])
        ctx = ConsoleContext(home=self.dir / "home", mode="local", assistant_dir=self.dir, environ={})
        turn = observe.activity_snapshot(ctx, {"days": "1"})["items"][0]
        steps = [s for s in observe.activity_snapshot(ctx, {"turn": turn["id"]})["items"] if s["kind"] == "api"]
        self.assertEqual([("big-1", "strong", "first step of the turn"), ("small-1", "cheap", "routine step")],
                         [(s["model"], s["route_choice"], s["route_reason"]) for s in steps])
        self.assertEqual({"rule"}, {s["route_policy"] for s in steps})

    def test_reports_count_cheap_steps_and_switches(self):
        core = self.core()
        self.run_task(core, "s1", ["ok", "error", "ok"])   # strong, cheap, strong, cheap
        self.run_task(core, "s2", ["ok"])                  # strong, cheap
        core.post_api_request(session_id="s3", api_request_id="x", model="big-1", api_duration=1.0, usage={})
        totals = reporting.session_totals(self.dir / "telemetry.sqlite3", ["s1", "s2", "s3"])
        self.assertEqual(dict(steps=4, cheap=2, switches=3), totals["s1"]["routing"])
        self.assertEqual(dict(steps=2, cheap=1, switches=1), totals["s2"]["routing"])
        self.assertNotIn("routing", totals["s3"])
        self.assertEqual({"big-1": 2, "small-1": 2},
                         {model: item["api_calls"] for model, item in totals["s1"]["models"].items()})
        results = [dict(passed=True, error=None, level="L1", session_id=name, checks=[]) for name in ("s1", "s2", "s3")]
        summary = bench.summarize({}, results, sessions=totals)
        self.assertAlmostEqual(3 / 6, summary["cheap_step_share"])
        self.assertAlmostEqual(4 / 2, summary["switches_per_task"])
        self.assertIsNone(bench.summarize({}, results[2:], sessions=totals)["cheap_step_share"])
        matrix = bench.compare([({"id": "a", "suite": "s", "suite_hash": "h", "status": "done"}, summary)] * 2)
        self.assertIn("Cheap-model steps", [row["label"] for row in matrix["rows"]])


if __name__ == "__main__":
    unittest.main()
