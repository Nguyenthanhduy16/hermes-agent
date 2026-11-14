"""DA-Code (Huang et al., EMNLP 2024) as benchmark suites, scored by its own evaluator.

DA-Code tasks give a folder of files (data, README.md, templates) and an instruction; the agent
writes result files (``result.csv``, a filled template, predictions) or answers in text. The
original evaluator compares ``<output>/<id>/`` with ``gold/<id>/``. Here:

1. ``build``  writes suites whose prompts point to ``<container dir>/<id>`` (default
   ``/dacode/<id>``), the task's working folder in the Hermes sandbox. Mount the host work folder
   there read-write (``terminal.docker_volumes``). Visualization tasks are left out: the
   original re-runs the agent's plotting script with an instrumented matplotlib.
2. ``reset``  gives every task of a suite a fresh copy of its source files. Run it before each
   benchmark run; a run uses ``--repeats 1`` because repeats of a task would share its folder.
   Before ``bench resume``, ``reset --run RUN`` resets only the tasks still without a result, so the
   folders of finished tasks survive until ``score``. No other run may use the folders in between.
3. ``score``  collects each task's new and changed files and the agent's final answer (from
   Hermes' session store, so long replies are not cut), writes ``dabench/result.json`` the way
   DA-Agent does, runs the original evaluator in an offline container of the sandbox image and
   stores each score (0..1) on the benchmark result.

    python -m assistant_core.dacode build --data ~/datasets/da-code --work ~/datasets/dacode-work
    python -m assistant_core.dacode reset --suite dacode-pilot
    python -m assistant_core.bench run --suite dacode-pilot --label S
    python -m assistant_core.dacode score <RUN_ID>
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time

import yaml

from .bench import BenchStore


GROUPS = (("sa", "data-sa-"), ("di", "di-"), ("dm", "dm-"), ("ml", "ml-"))
LEVELS = {"Easy": "L1", "Medium": "L2", "Hard": "L3"}
PILOT_SHARE = {"sa": 2, "di": 2, "dm": 2, "ml": 4}
DEFAULT_IMAGE = "hermes-bench-sandbox:2"
ANSWER = re.compile(r"<answer>(.*?)</answer>", re.S | re.I)
WORK_ENV = "DACODE_WORK_DIR"
DATA_ENV = "DACODE_DATA_DIR"

PROMPT = """Your working directory is {dir}. It contains the files of this task (data, README.md, and any templates).

Task: {instruction}

Work on the files with code in the sandbox: Python with pandas, NumPy, SciPy, scikit-learn, statsmodels, XGBoost and DuckDB is available; there is no internet access. Save every output file the task asks for in {dir}, with exactly the file names the task gives, and fill a provided template file in place when the task says so.

When you are done, end your reply with the final answer between <answer> and </answer>: for a question, the answer itself in the requested format; for file outputs, the name(s) of the file(s) you wrote, for example <answer>result.csv</answer>."""


class DacodeError(Exception):
    pass


def _group(task_id: str):
    return next((name for name, prefix in GROUPS if task_id.startswith(prefix)), None)


def _jsonl(path: Path) -> list:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_tasks(data: Path) -> list:
    """DA-Code tasks that have source files and an evaluation config, minus visualization."""
    root = Path(data).expanduser() / "da_code"
    if not (root / "configs" / "task" / "all.jsonl").is_file():
        raise DacodeError(f"{data} is not a DA-Code checkout (da_code/configs/task/all.jsonl missing).")
    evals = {item["id"]: item for item in _jsonl(root / "configs" / "eval" / "eval_all.jsonl")}
    tasks = []
    for task in _jsonl(root / "configs" / "task" / "all.jsonl"):
        task_id = task["id"]
        if not (root / "source" / task_id).is_dir() or task_id not in evals or _group(task_id) is None:
            continue
        if task.get("post_process") or evals[task_id].get("config", {}).get("task") == "data visualization":
            continue
        tasks.append(dict(task, eval=evals[task_id]))
    return sorted(tasks, key=lambda t: t["id"])


def bench_task(task: dict, container_dir: str, timeout_s: float) -> dict:
    folder = f"{container_dir.rstrip('/')}/{task['id']}"
    return dict(id=f"dc-{task['id']}", level=LEVELS.get(task.get("hardness"), "L2"),
                category=re.sub(r"[^A-Za-z0-9 _-]", "", f"{_group(task['id'])} {task.get('type', '')}")[:40].strip(), timeout_s=timeout_s,
                prompt=PROMPT.format(dir=folder, instruction=task["instruction"].strip()),
                checks=[{"type": "external", "value": "dacode"}])


def pick_pilot(tasks: list, seed: int) -> list:
    rng = random.Random(seed)
    chosen = []
    for name, count in PILOT_SHARE.items():
        pool = [t for t in tasks if _group(t["id"]) == name]
        if name == "ml":  # one per ML kind first, so binary/cluster/regression/competition all appear
            kinds = sorted({t["id"].rsplit("-", 1)[0] for t in pool})
            rng.shuffle(kinds)
            firsts = [rng.choice([t for t in pool if t["id"].startswith(kind + "-")]) for kind in kinds]
            rest = [t for t in pool if t not in firsts]
            rng.shuffle(rest)
            chosen += (firsts + rest)[:count]
        else:
            chosen += rng.sample(pool, min(count, len(pool)))
    return sorted(chosen, key=lambda t: t["id"])


def build(data: Path, out_dir: Path, *, container_dir: str, timeout_s: float, pilot: int, seed: int,
          force: bool) -> list:
    tasks = load_tasks(data)
    pilot_tasks = pick_pilot(tasks, seed) if pilot else []
    suites = {"dacode-sample": ("All DA-Code sample tasks with source files, without visualization", tasks)}
    if pilot_tasks:
        suites["dacode-pilot"] = (f"DA-Code pilot: {len(pilot_tasks)} tasks by group, seed {seed}", pilot_tasks)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (description, chosen) in suites.items():
        path = out_dir / f"{name}.yaml"
        if path.exists() and not force:
            raise DacodeError(f"{path} exists; runs may refer to it. Use --force to rewrite it.")
        body = dict(name=name, description=description,
                    tasks=[bench_task(t, container_dir, timeout_s) for t in chosen])
        path.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        written.append((name, chosen))
    manifest = dict(seed=seed, container_dir=container_dir, data=str(Path(data).expanduser()),
                    created_at=time.time(), suites={name: [t["id"] for t in chosen] for name, chosen in written})
    (out_dir / "dacode-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return written


# ---- working folders ---------------------------------------------------------

def _suite_ids(home: Path, suite: str) -> list:
    path = home / "benchmarks" / f"{suite}.yaml"
    if not path.is_file():
        raise DacodeError(f"No suite {path}.")
    raw = [t["id"] for t in yaml.safe_load(path.read_text(encoding="utf-8"))["tasks"]]
    if not raw or not all(task_id.startswith("dc-") for task_id in raw):
        raise DacodeError(f"{suite} is not a DA-Code suite (task ids start with dc-).")
    return [task_id.removeprefix("dc-") for task_id in raw]


def _remove(folder: Path, image: str):
    """Delete a working folder; files the sandbox (root) wrote are removed through Docker."""
    if not folder.exists():
        return
    try:
        shutil.rmtree(folder)
    except PermissionError:
        subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", f"{folder.parent}:/w", image,
                        "rm", "-rf", f"/w/{folder.name}"], check=True, capture_output=True, timeout=300)


def pending_ids(data_dir: Path, run_id: str, ids: list) -> list:
    """Task ids (without ``dc-``) of ``ids`` that have no result yet in ``run_id``."""
    store = BenchStore(Path(data_dir) / "bench.sqlite3")
    try:
        if store.get_run(run_id) is None:
            raise DacodeError(f"No run {run_id}.")
        done = {r["task_id"].removeprefix("dc-") for r in store.results(run_id)}
    finally:
        store.close()
    return [task_id for task_id in ids if task_id not in done]


def reset(data: Path, work: Path, ids: list, image: str) -> None:
    source = Path(data).expanduser() / "da_code" / "source"
    work.mkdir(parents=True, exist_ok=True)
    for task_id in ids:
        target = work / task_id
        _remove(target, image)
        shutil.copytree(source / task_id, target)
        os.chmod(target, 0o777)  # the sandbox writes as root; keep it writable either way


# ---- scoring -----------------------------------------------------------------

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def changed_files(source: Path, folder: Path):
    """(added, changed) paths relative to ``folder``, compared with the task's source files."""
    added, changed = [], []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder)
        original = source / rel
        if not original.is_file():
            added.append(rel)
        elif original.stat().st_size != path.stat().st_size or _sha256(original) != _sha256(path):
            changed.append(rel)
    return added, changed


def final_reply(state_db: Path, session_id: str | None) -> str | None:
    """The agent's last assistant message for a session, from Hermes' session store."""
    if not session_id or not state_db.is_file():
        return None
    db = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=10)
    try:
        row = db.execute("SELECT content FROM messages WHERE session_id=? AND role='assistant' "
                         "AND content IS NOT NULL AND content != '' ORDER BY id DESC LIMIT 1",
                         (session_id,)).fetchone()
    finally:
        db.close()
    return row[0] if row and isinstance(row[0], str) else None


def extract_answer(reply: str) -> str:
    found = ANSWER.findall(reply or "")
    return found[-1].strip() if found else ""


def _steps(telemetry_db: Path, session_id: str | None) -> int:
    if not session_id or not telemetry_db.is_file():
        return 0
    db = sqlite3.connect(f"file:{telemetry_db}?mode=ro", uri=True, timeout=10)
    try:
        return db.execute("SELECT COUNT(DISTINCT api_request_id) FROM api_calls WHERE session_id=?",
                          (session_id,)).fetchone()[0]
    finally:
        db.close()


EVAL_SCRIPT = """
import json, sys
sys.path.insert(0, "/repo")
from da_agent.evaluators.evaluation import Evaluator
results = Evaluator(output_dir="/out", gold_dir="/repo/da_code/gold", timeout_seconds=%d).evaluate(env_config="/out/eval.jsonl")
keep = ("id", "total_score", "finished", "task", "hardness", "result_type")
print("@@RESULTS@@" + json.dumps([{k: r.get(k) for k in keep} for r in results or []]))
"""


def run_evaluator(data: Path, out: Path, image: str, timeout_s: int) -> dict:
    command = ["docker", "run", "--rm", "--network", "none", "-v", f"{Path(data).expanduser()}:/repo:ro",
               "-v", f"{out}:/out", "-w", "/repo", image, "python3", "-c", EVAL_SCRIPT % timeout_s]
    done = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    marker = next((line for line in done.stdout.splitlines() if line.startswith("@@RESULTS@@")), None)
    if done.returncode != 0 or marker is None:
        raise DacodeError("DA-Code evaluator failed:\n" + (done.stderr or done.stdout)[-3000:])
    (out / "evaluator.log").write_text(done.stdout + "\n" + done.stderr, encoding="utf-8")
    return {item["id"]: item for item in json.loads(marker[len("@@RESULTS@@"):])}


def score(run_id: str, *, home: Path, data_dir: Path, data: Path, work: Path, image: str,
          eval_timeout: int = 120) -> list:
    store = BenchStore(data_dir / "bench.sqlite3")
    try:
        run = store.get_run(run_id)
        if run is None:
            raise DacodeError(f"No run {run_id}.")
        results = [r for r in store.results(run_id) if r["task_id"].startswith("dc-")]
        if not results:
            raise DacodeError(f"Run {run_id} has no DA-Code results.")
        if any(r["attempt"] != 1 for r in results):
            raise DacodeError("DA-Code runs must use --repeats 1 (repeats would share a working folder).")
        evals = {t["id"]: t["eval"] for t in load_tasks(data)}
        source = Path(data).expanduser() / "da_code" / "source"
        out = data_dir / "dacode" / run_id
        _remove(out, image)
        out.mkdir(parents=True)
        configs, notes = [], {}
        for r in results:
            task_id = r["task_id"].removeprefix("dc-")
            folder, target = work / task_id, out / task_id
            (target / "dabench").mkdir(parents=True)
            added, changed = changed_files(source / task_id, folder) if folder.is_dir() else ([], [])
            for rel in added + changed:
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(folder / rel, target / rel)
            reply = final_reply(home / "state.db", r.get("session_id")) or r.get("response_text") or ""
            answer = extract_answer(reply)
            finished = bool(reply.strip()) and not r.get("error")
            (target / "dabench" / "result.json").write_text(json.dumps(dict(
                finished=finished, steps=_steps(data_dir / "telemetry.sqlite3", r.get("session_id")), result=answer,
                result_files=dict(added_files=[str(target / p) for p in added],
                                  changed_files=[str(target / p) for p in changed], post_process_files=[], error=[]),
                trajectory=[]), indent=2), encoding="utf-8")
            configs.append(evals[task_id])
            notes[task_id] = dict(files=len(added) + len(changed), answered=bool(answer), finished=finished)
        with open(out / "eval.jsonl", "w", encoding="utf-8") as handle:
            for config in configs:
                handle.write(json.dumps(config) + "\n")
        os.chmod(out, 0o777)
        scores = run_evaluator(data, out, image, eval_timeout)
        updates, report = [], []
        for r in results:
            task_id = r["task_id"].removeprefix("dc-")
            value = scores.get(task_id, {}).get("total_score")
            value = float(value) if isinstance(value, (int, float)) else 0.0
            note = notes[task_id]
            detail = f"DA-Code score {value:.2f} ({note['files']} files, {'answer' if note['answered'] else 'no <answer>'})"
            checks = [dict(type="external", scorer="dacode", passed=value >= 1.0, score=round(value, 4), detail=detail,
                           files=note["files"], answered=note["answered"])]
            updates.append((r["task_id"], r["attempt"], value >= 1.0, checks))
            report.append((task_id, value, note))
        store.set_external_scores(run_id, updates)
        return report
    finally:
        store.close()


# ---- CLI ---------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m assistant_core.dacode", description=__doc__.split("\n\n")[0])
    parser.add_argument("--home", default=os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes"))
    parser.add_argument("--data", default=os.environ.get(DATA_ENV) or str(Path.home() / "datasets" / "da-code"))
    parser.add_argument("--work", default=os.environ.get(WORK_ENV) or str(Path.home() / "datasets" / "dacode-work"))
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="write dacode-sample and dacode-pilot suites")
    b.add_argument("--container-dir", default="/dacode")
    b.add_argument("--timeout", type=float, default=1800.0)
    b.add_argument("--pilot", type=int, default=1, help="0 to skip the pilot suite")
    b.add_argument("--seed", type=int, default=2026)
    b.add_argument("--force", action="store_true")
    r = sub.add_parser("reset", help="fresh working folders for every task of a suite")
    r.add_argument("--suite", required=True)
    r.add_argument("--run", help="resume: reset only the tasks without a result in this run (their folders may hold "
                                 "a half-done attempt); folders of finished tasks are kept for scoring")
    s = sub.add_parser("score", help="score a finished run with the DA-Code evaluator")
    s.add_argument("run_id")
    s.add_argument("--eval-timeout", type=int, default=120)
    args = parser.parse_args(argv)
    home, data, work = Path(args.home).expanduser(), Path(args.data).expanduser(), Path(args.work).expanduser()
    data_dir = Path(os.environ.get("ASSISTANT_DATA_DIR") or home / "assistant-core")
    try:
        if args.command == "build":
            for name, chosen in build(data, home / "benchmarks", container_dir=args.container_dir,
                                      timeout_s=args.timeout, pilot=args.pilot, seed=args.seed, force=args.force):
                counts = {}
                for t in chosen:
                    counts[_group(t["id"])] = counts.get(_group(t["id"]), 0) + 1
                print(f"{name}: {len(chosen)} tasks  " + "  ".join(f"{k} {v}" for k, v in counts.items()))
            print(f"Mount {work} read-write at the container dir (terminal.docker_volumes), then restart the gateway.")
        elif args.command == "reset":
            ids = _suite_ids(home, args.suite)
            if args.run:
                ids = pending_ids(data_dir, args.run, ids)
            reset(data, work, ids, args.image)
            print(f"{len(ids)} working folders reset under {work}")
        else:
            report = score(args.run_id, home=home, data_dir=data_dir, data=data, work=work, image=args.image,
                           eval_timeout=args.eval_timeout)
            for task_id, value, note in report:
                print(f"  {task_id:24s} {value:5.2f}  files {note['files']:2d}  "
                      f"{'answer' if note['answered'] else 'NO ANSWER'}{'' if note['finished'] else '  unfinished'}")
            print(f"mean score {sum(v for _, v, _ in report) / len(report):.3f} over {len(report)} tasks")
    except DacodeError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
