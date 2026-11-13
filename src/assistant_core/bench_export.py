"""Export benchmark runs to CSV for analysis and router training.

    python -m assistant_core.bench export RUN [RUN ...] --out DIR

writes two files (docs/thiet-ke-thuc-nghiem.md, section 8, tool 1):

    tasks.csv   one row per task x attempt: outcome, score, steps, routing, tokens, cost
    steps.csv   one row per model call (step): the router's features (``f_*``), its
                choice, the model that ran, tokens, cost, the tool calls that step made,
                and the outcome of the task it belongs to (the training label)

Steps are joined to tasks through the Hermes session id; a retried step (several
rows with one ``api_request_id``) is one step. Tool calls belong to the step whose
model call requested them. Nothing here reads message text.

Cost uses pricing.yaml (off-peak list prices). DeepSeek bills twice that in its peak
hours (Mon-Fri 01-04 and 06-10 UTC, per Hermes agent/usage_pricing.py), so
``cost_usd_billed`` applies that factor per call; the two are equal for every other
provider.
"""

import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3

from . import reporting

TASK_COLUMNS = [
    "run_id", "label", "suite", "suite_hash", "config_hash", "policy", "task_id", "attempt", "level", "category",
    "passed", "score", "error", "latency_s", "session_id", "steps", "strong_steps", "cheap_steps", "switches",
    "input_tokens", "output_tokens", "cache_read_tokens", "strong_output_tokens", "cost_usd", "cost_usd_billed",
    "tool_calls", "tool_errors", "tool_timeouts", "first_ts", "last_ts"]
STEP_COLUMNS = [
    "run_id", "label", "task_id", "attempt", "session_id", "step_index", "api_request_id", "api_call_count",
    "ts", "provider", "model", "policy", "choice", "reason", "score", "switched", "retries",
    "input_tokens", "output_tokens", "cache_read_tokens", "reasoning_tokens", "duration_s", "error_type",
    "cost_usd", "cost_usd_billed", "tool_calls", "tool_errors", "tool_timeouts", "tool_max_s",
    "task_passed", "task_score"]
PEAK_PROVIDERS = ("deepseek",)
PEAK_HOURS_UTC = ((1, 4), (6, 10))   # [start, end) hours, Monday to Friday


def peak_factor(provider, ts) -> float:
    """2 for a DeepSeek call in its peak hours, else 1."""
    if not provider or str(provider).lower() not in PEAK_PROVIDERS or ts is None:
        return 1.0
    moment = datetime.fromtimestamp(float(ts), timezone.utc)
    if moment.weekday() >= 5:
        return 1.0
    return 2.0 if any(start <= moment.hour < end for start, end in PEAK_HOURS_UTC) else 1.0


def _score(result) -> float:
    """The task's score: the external evaluator's 0-1 score when there is one, else pass/fail."""
    for check in result.get("checks") or []:
        if check.get("type") == "external" and isinstance(check.get("score"), (int, float)):
            return float(check["score"])
    return 1.0 if result.get("passed") else 0.0


def _rows(db, sql, params):
    db.row_factory = sqlite3.Row
    return [dict(row) for row in db.execute(sql, params)]


def _steps(db, session_id, pricing):
    """The steps of one session in order, retries merged, with their tool calls."""
    calls = _rows(db, "SELECT * FROM api_calls WHERE session_id = ? ORDER BY ts, id", (session_id,))
    tools = _rows(db, "SELECT api_request_id, tool_name, status, duration_ms, blocked FROM tool_calls "
                      "WHERE session_id = ? ORDER BY ts, id", (session_id,))
    by_step: dict = {}
    for call in calls:
        key = call["api_request_id"] or f"row-{call['id']}"
        step = by_step.get(key)
        model = call["response_model"] or call["model"]
        cost = reporting.estimate_cost(pricing, model, call["input_tokens"], call["output_tokens"],
                                       call["cache_read_tokens"])
        billed = None if cost is None else cost * peak_factor(call["provider"], call["ts"])
        if step is None:
            features = {}
            try:
                features = json.loads(call["route_features"]) if call["route_features"] else {}
            except ValueError:
                features = {}
            by_step[key] = step = dict(
                api_request_id=call["api_request_id"], api_call_count=call["api_call_count"], ts=call["ts"],
                provider=call["provider"], model=model, policy=call["route_policy"], choice=call["route_choice"],
                reason=call["route_reason"], score=call["route_score"], switched=call["route_switched"], retries=0,
                input_tokens=0, output_tokens=0, cache_read_tokens=0, reasoning_tokens=0, duration_s=0.0,
                error_type=None, cost_usd=0.0, cost_usd_billed=0.0, priced=True,
                tool_calls=0, tool_errors=0, tool_timeouts=0, tool_max_s=0.0, features=features)
        else:
            step["retries"] += 1
        for name in ("input_tokens", "output_tokens", "cache_read_tokens", "reasoning_tokens"):
            step[name] += call[name] or 0
        step["duration_s"] += call["duration_s"] or 0.0
        step["error_type"] = call["error_type"] or step["error_type"]
        if cost is None:
            step["priced"] = False
        else:
            step["cost_usd"] += cost
            step["cost_usd_billed"] += billed
    for tool in tools:
        step = by_step.get(tool["api_request_id"])
        if step is None:
            continue
        step["tool_calls"] += 1
        status = str(tool["status"] or "").lower()
        if status == "timeout":
            step["tool_timeouts"] += 1
        elif tool["blocked"] or status not in ("", "ok", "success"):
            step["tool_errors"] += 1
        seconds = (tool["duration_ms"] or 0) / 1000.0
        if math.isfinite(seconds):
            step["tool_max_s"] = max(step["tool_max_s"], seconds)
    out = list(by_step.values())
    for step in out:
        if not step.pop("priced"):
            step["cost_usd"] = step["cost_usd_billed"] = None
    return out


def export(store, data_dir: Path, run_ids, out_dir: Path, pricing=None, prompts=None) -> dict:
    """Write tasks.csv and steps.csv for the given runs; returns the row counts and unjoined tasks.

    ``prompts`` ({suite name: {task id: prompt}}) recovers the session of a result stored
    without one (a timed-out task from before the bench recorded the expected id)."""
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pricing = reporting.load_pricing(data_dir / "pricing.yaml") if pricing is None else pricing
    telemetry = data_dir / "telemetry.sqlite3"
    db = sqlite3.connect(f"file:{telemetry}?mode=ro", uri=True) if telemetry.exists() else None
    task_rows, step_rows, unjoined = [], [], []
    feature_names: list = []
    try:
        for run_id in run_ids:
            run = store.get_run(run_id)
            if run is None:
                raise ValueError(f"unknown run {run_id}")
            for result in store.results(run["id"]):
                session_id = result.get("session_id") or ""
                prompt = ((prompts or {}).get(run["suite"]) or {}).get(result["task_id"])
                if not session_id and prompt:
                    from .bench import expected_session_id
                    session_id = expected_session_id(
                        f"Benchmark run {run['id']}, task {result['task_id']}, attempt {result['attempt']}.", prompt)
                steps = _steps(db, session_id, pricing) if db is not None and session_id else []
                if not steps:
                    unjoined.append(f"{run['id']}:{result['task_id']}#{result['attempt']}")
                score = _score(result)
                for index, step in enumerate(steps, 1):
                    features = step.pop("features")
                    for name in features:
                        if name not in feature_names:
                            feature_names.append(name)
                    step_rows.append(dict(step, run_id=run["id"], label=run["label"], task_id=result["task_id"],
                                          attempt=result["attempt"], session_id=session_id, step_index=index,
                                          task_passed=int(bool(result["passed"])), task_score=score,
                                          **{f"f_{k}": v for k, v in features.items()}))
                choices = [s["choice"] for s in steps if s["choice"]]
                costs = [s["cost_usd"] for s in steps]
                billed = [s["cost_usd_billed"] for s in steps]
                policies = [s["policy"] for s in steps if s["policy"]]
                task_rows.append(dict(
                    run_id=run["id"], label=run["label"], suite=run["suite"], suite_hash=run["suite_hash"],
                    config_hash=run["config_hash"], policy=max(set(policies), key=policies.count) if policies else None,
                    task_id=result["task_id"], attempt=result["attempt"], level=result["level"],
                    category=result["category"], passed=int(bool(result["passed"])), score=score,
                    error=result["error"], latency_s=result["latency_s"], session_id=session_id,
                    steps=len(steps), strong_steps=choices.count("strong"), cheap_steps=choices.count("cheap"),
                    switches=sum(a != b for a, b in zip(choices, choices[1:])),
                    input_tokens=sum(s["input_tokens"] for s in steps),
                    output_tokens=sum(s["output_tokens"] for s in steps),
                    cache_read_tokens=sum(s["cache_read_tokens"] for s in steps),
                    strong_output_tokens=sum(s["output_tokens"] for s in steps if s["choice"] == "strong"),
                    cost_usd=None if not steps or None in costs else round(sum(costs), 6),
                    cost_usd_billed=None if not steps or None in billed else round(sum(billed), 6),
                    tool_calls=sum(s["tool_calls"] for s in steps), tool_errors=sum(s["tool_errors"] for s in steps),
                    tool_timeouts=sum(s["tool_timeouts"] for s in steps),
                    first_ts=steps[0]["ts"] if steps else None, last_ts=steps[-1]["ts"] if steps else None))
    finally:
        if db is not None:
            db.close()
    _write(out_dir / "tasks.csv", TASK_COLUMNS, task_rows)
    _write(out_dir / "steps.csv", STEP_COLUMNS + [f"f_{name}" for name in feature_names], step_rows)
    return dict(tasks=len(task_rows), steps=len(step_rows), unjoined=unjoined,
                files=[str(out_dir / "tasks.csv"), str(out_dir / "steps.csv")])


def _write(path: Path, columns, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key)) for key in columns})


def _cell(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value
