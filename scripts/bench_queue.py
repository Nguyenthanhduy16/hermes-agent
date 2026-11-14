#!/usr/bin/env python3
"""Run a plan of benchmark runs that can be stopped and continued at any point.

A plan is a text file, one run per line (``#`` starts a comment):

    # label        suite          policy         [VAR=value ...]  (gateway environment)
    dev-S          dacode-dev     always-strong
    dev-R50        dacode-dev     random         ASSISTANT_ROUTER_STRONG_SHARE=0.5 ASSISTANT_ROUTER_SEED=31

``run`` goes through the plan in order and looks each label up in the bench store:

- no run with that label: start the bench gateway with the policy, reset the DA-Code folders,
  ``bench run``, then ``dacode score``;
- a cancelled or failed run (stopped, Ctrl-C, machine off): reset only the folders of tasks without
  a result, ``bench resume`` it (finished tasks are kept), then score;
- done but with tasks that never reached the model provider (``connection_error``,
  ``provider_unavailable``: network or provider down): reset those folders and resume, which runs them again;
- done but not scored yet: score; done and scored: skip.

When the provider is down (a run stops itself after three provider_unavailable tasks in a row, or
ends with such tasks), the queue waits ``RETRY_WAIT_S`` and tries the label again, up to
``RETRY_PASSES`` times, before it gives up.

So the same command continues where the last one stopped. A label is never started while an
earlier one in the plan is unfinished, because DA-Code runs share their working folders.

Stopping:
    stop         graceful: no new task starts, tasks in flight finish (up to their timeout)
    stop --now   at once: kills the queue; the task in flight is lost and runs again on resume

    python scripts/bench_queue.py run PLAN [--concurrency N]   (in a herdr pane, tmux or nohup)
    python scripts/bench_queue.py status PLAN
    python scripts/bench_queue.py stop [--now]
"""

import argparse
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".runtime" / "venv" / "bin" / "python")
GATEWAY = str(ROOT / "scripts" / "bench-gateway.sh")
CURRENT = []  # the child process running now, so SIGTERM can stop it too
RETRYABLE = ("connection_error", "provider_unavailable")  # same as assistant_core.bench.RETRYABLE_ERRORS
RETRY_WAIT_S = int(os.environ.get("BENCH_QUEUE_RETRY_WAIT_S") or 300)
RETRY_PASSES = 6


def profile_dir() -> Path:
    return Path(os.environ.get("BENCH_PROFILE") or ROOT / ".runtime" / "hermes-home" / "profiles" / "bench")


def state_dir() -> Path:
    path = profile_dir() / "bench-queue"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_env(path: Path) -> dict:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                values[key.strip()] = value.strip()
    return values


def child_env() -> dict:
    profile = profile_dir()
    dotenv = read_env(profile / ".env")
    port = dotenv.get("API_SERVER_PORT") or "8650"
    env = dict(os.environ, HERMES_HOME=str(profile), BENCH_PROFILE=str(profile), PYTHONPATH=str(ROOT / "src"),
               PYTHONUNBUFFERED="1",  # live progress lines through the pipe
               HERMES_BENCH_API_URL=f"http://127.0.0.1:{port}/v1")
    if dotenv.get("API_SERVER_KEY"):
        env["API_SERVER_KEY"] = dotenv["API_SERVER_KEY"]
    return env


def read_plan(path: Path) -> list:
    entries, seen = [], set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        words = line.split("#", 1)[0].split()
        if not words:
            continue
        if len(words) < 3 or any("=" not in w for w in words[3:]):
            sys.exit(f"{path}:{number}: expected 'label suite policy [VAR=value ...]'")
        if words[0] in seen:
            sys.exit(f"{path}:{number}: label {words[0]} appears twice")
        seen.add(words[0])
        entries.append(dict(label=words[0], suite=words[1], policy=words[2], env=words[3:]))
    if not entries:
        sys.exit(f"{path}: no runs")
    return entries


def recover_stale(db_path: Path):
    """Mark runs whose runner died (stop --now, machine off) as failed, so they can be resumed."""
    sys.path.insert(0, str(ROOT / "src"))
    from assistant_core.bench import BenchStore
    store = BenchStore(db_path)
    try:
        store.recover_stale()
    finally:
        store.close()


def latest_run(label: str):
    db_path = profile_dir() / "assistant-core" / "bench.sqlite3"
    if not db_path.is_file():
        return None
    recover_stale(db_path)
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        row = db.execute("SELECT id, status, completed, total, reason FROM bench_runs WHERE label=? "
                         "ORDER BY created_at DESC LIMIT 1", (label,)).fetchone()
        if row is None:
            return None
        checks = [json.loads(c or "[]") for (c,) in db.execute(
            "SELECT checks_json FROM bench_results WHERE run_id=? AND task_id LIKE 'dc-%'", (row[0],))]
        scores = [c.get("score") for cs in checks for c in cs if c.get("type") == "external"]
        pending = any(c.get("pending") for cs in checks for c in cs)
        retry = db.execute(f"SELECT COUNT(*) FROM bench_results WHERE run_id=? AND error IN "
                           f"({', '.join('?' * len(RETRYABLE))})", (row[0], *RETRYABLE)).fetchone()[0]
    finally:
        db.close()
    return dict(id=row[0], status=row[1], completed=row[2], total=row[3], reason=row[4] or "", retry=retry,
                dacode=bool(checks), pending=pending, score=(sum(s or 0 for s in scores) / len(scores)) if scores and not pending else None)


def stop_requested() -> bool:
    return (state_dir() / "stop").exists()


def provider_down(run) -> bool:
    return bool(run) and (run["retry"] > 0 or run["reason"].startswith("provider_unavailable"))


def wait_or_stop(seconds: int) -> bool:
    """Sleep ``seconds``; False if a stop was requested meanwhile."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop_requested():
            return False
        time.sleep(min(5, end - time.monotonic()))
    return not stop_requested()


def call(args: list, env: dict, log) -> int:
    log.write(f"$ {' '.join(args)}\n")
    log.flush()
    with subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
        CURRENT[:] = [proc]
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
        code = proc.wait()
        CURRENT.clear()
        return code


def on_term(signum, frame):
    """stop --now: end the child (a killed bench run is marked failed, then resumed) and exit via finally."""
    for proc in CURRENT:
        proc.terminate()
    raise SystemExit(143)


def say(text: str):
    print(f"[queue {time.strftime('%H:%M:%S')}] {text}", flush=True)


def do_run(args) -> int:
    plan = read_plan(Path(args.plan))
    state = state_dir()
    pid_file = state / "queue.pid"
    if pid_file.exists():
        try:
            other = int(pid_file.read_text())
            os.kill(other, 0)
            sys.exit(f"Another queue is running (pid {other}). Stop it first: bench_queue.py stop")
        except (ValueError, ProcessLookupError):
            pass
        except PermissionError:
            sys.exit("Another queue is running.")
    pid_file.write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, on_term)
    (state / "stop").unlink(missing_ok=True)
    env = child_env()
    logs = state / "logs"
    logs.mkdir(exist_ok=True)
    gateway_up = False
    try:
        for entry in plan:
            label = entry["label"]
            if stop_requested():
                say("stop requested; the plan continues on the next 'run'")
                return 0
            run = latest_run(label)
            if run and run["status"] in ("queued", "running"):
                say(f"{label}: run {run['id']} is active in another process; stop it first")
                return 1
            if run and run["status"] == "done" and not run["pending"] and not run["retry"]:
                say(f"{label}: done ({run['completed']}/{run['total']}"
                    + (f", score {run['score']:.3f})" if run["score"] is not None else ")"))
                continue
            with open(logs / f"{label}.log", "a", encoding="utf-8") as log:
                log.write(f"\n==== {time.strftime('%F %T')} queue run\n")
                passes = 0
                while not run or run["status"] != "done" or run["retry"]:
                    passes += 1
                    resume = run is not None
                    if not resume:
                        action = "start"
                    elif run["status"] == "done":
                        action = f"run again the {run['retry']} tasks that did not reach the provider in {run['id']}"
                    else:
                        action = f"resume {run['id']} ({run['completed']}/{run['total']} kept)"
                    say(f"{label}: {action} {entry['suite']} policy={entry['policy']} {' '.join(entry['env'])}".rstrip())
                    if passes == 1:  # this label's policy; later passes keep the same gateway
                        if call([GATEWAY, "start", entry["policy"], label, *entry["env"]], env, log) != 0:
                            say(f"{label}: the bench gateway did not start; see the log above")
                            return 1
                        gateway_up = True
                    reset = [PY, "-m", "assistant_core.dacode", "reset", "--suite", entry["suite"]]
                    if resume:
                        reset += ["--run", run["id"]]
                    if call(reset, env, log) != 0:
                        say(f"{label}: resetting the DA-Code folders failed")
                        return 1
                    if resume:
                        bench = [PY, "-m", "assistant_core.bench", "resume", run["id"]]
                    else:
                        bench = [PY, "-m", "assistant_core.bench", "run", "--suite", entry["suite"], "--label", label,
                                 "--repeats", "1", "--concurrency", str(args.concurrency)]
                    code = call(bench, env, log)
                    run = latest_run(label)
                    status = run["status"] if run else "missing"
                    if status == "done" and not run["retry"]:
                        break
                    if run and (stop_requested() or status == "cancelled"):
                        say(f"{label}: stopped ({run['completed']}/{run['total']} kept); 'run' again to continue")
                        return 0
                    if not provider_down(run):
                        say(f"{label}: run ended as {status} (exit {code}); fix the cause, then 'run' again")
                        return 1
                    if passes >= RETRY_PASSES:
                        say(f"{label}: the model provider is still unreachable after {passes} passes; "
                            "check the network, then 'run' again")
                        return 1
                    say(f"{label}: the model provider was unreachable ({run['retry']} tasks); "
                        f"trying again in {RETRY_WAIT_S // 60} min")
                    if not wait_or_stop(RETRY_WAIT_S):
                        say(f"{label}: stop requested; 'run' again to continue")
                        return 0
                if run["dacode"] and run["pending"]:
                    if call([PY, "-m", "assistant_core.dacode", "score", run["id"]], env, log) != 0:
                        say(f"{label}: scoring failed; 'run' again retries it")
                        return 1
                run = latest_run(label)
                say(f"{label}: done" + (f", score {run['score']:.3f}" if run and run["score"] is not None else ""))
        say("plan finished")
        return 0
    finally:
        if gateway_up:
            with open(os.devnull, "w") as quiet:
                call([GATEWAY, "stop"], env, quiet)
        pid_file.unlink(missing_ok=True)


def do_status(args) -> int:
    rows = []
    for entry in read_plan(Path(args.plan)):
        run = latest_run(entry["label"])
        if run is None:
            rows.append((entry["label"], "not started", "", "", ""))
            continue
        status = "scoring" if run["status"] == "done" and run["pending"] else run["status"]
        rows.append((entry["label"], status, f"{run['completed']}/{run['total']}",
                     "" if run["score"] is None else f"{run['score']:.3f}", run["id"]))
    widths = [max(len(str(r[i])) for r in rows + [("label", "status", "tasks", "score", "run")]) for i in range(5)]
    for row in [("label", "status", "tasks", "score", "run")] + rows:
        print("  ".join(str(c).ljust(w) for c, w in zip(row, widths)).rstrip())
    pid_file = state_dir() / "queue.pid"
    print(f"\nqueue: {'running (pid ' + pid_file.read_text().strip() + ')' if pid_file.exists() else 'not running'}"
          + ("; stop requested" if stop_requested() else ""))
    return 0


def do_stop(args) -> int:
    state = state_dir()
    (state / "stop").write_text(time.strftime("%F %T"))
    env = child_env()
    db_path = profile_dir() / "assistant-core" / "bench.sqlite3"
    active = None
    if db_path.is_file():
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        try:
            active = db.execute("SELECT id FROM bench_runs WHERE status IN ('queued', 'running') "
                                "ORDER BY created_at DESC LIMIT 1").fetchone()
        finally:
            db.close()
    pid_file = state / "queue.pid"
    if args.now:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except (ValueError, ProcessLookupError):
                pass
            for _ in range(60):  # the queue stops its child and the gateway on the way out
                if not pid_file.exists():
                    break
                time.sleep(1)
        print("Stopped now. The task in flight runs again on the next 'run'.")
        return 0
    if active:
        subprocess.run([PY, "-m", "assistant_core.bench", "cancel", active[0]], cwd=ROOT, env=env, check=False)
        print(f"Run {active[0]} finishes the tasks in flight, then the queue exits. 'run' continues later.")
    else:
        print("No active run; the queue exits before its next step.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the plan, continuing where it stopped")
    run.add_argument("plan")
    run.add_argument("--concurrency", type=int, default=int(os.environ.get("BENCH_CONCURRENCY") or 1),
                     help="for runs started now (resumed runs keep theirs); default BENCH_CONCURRENCY or 1")
    status = sub.add_parser("status", help="state of every run in the plan")
    status.add_argument("plan")
    stop = sub.add_parser("stop", help="stop after the tasks in flight (or --now)")
    stop.add_argument("--now", action="store_true")
    args = parser.parse_args(argv)
    return {"run": do_run, "status": do_status, "stop": do_stop}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
