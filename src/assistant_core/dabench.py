"""Turn InfiAgent-DABench into benchmark suites for ``assistant_core.bench``.

InfiAgent-DABench (Hu et al., ICML 2024) is 257 closed-form data-analysis
questions over CSV files, each tagged easy / medium / hard. The dataset is not
shipped with this project: clone https://github.com/InfiAgent/InfiAgent and
point ``--data`` at ``examples/DA-Agent/data`` (``da-dev-questions.jsonl``,
``da-dev-labels.jsonl``, ``da-dev-tables/``).

``build`` writes three suites and a manifest into ``<home>/benchmarks``:

- ``dabench-dev``   questions for building and tuning the router,
- ``dabench-test``  held-out questions, only for the final evaluation,
- ``dabench-pilot`` a few dev questions per level, to estimate cost first,
- ``dabench-manifest.json`` what went where (seed, files, ids, skipped rows).

The split is by CSV file: every question about one file lands in the same
split, so nothing learned about a file on dev can leak into test. Within that
constraint the levels are balanced. The same ``--seed`` gives the same suites.

Differences from the original pipeline, to state when reporting results:

- the answer format is part of the prompt, and the reply is scored as written
  (the original reformats replies with an LLM before scoring);
- the agent reads the CSV from a path in its sandbox instead of an upload;
- a request that fails counts as a wrong answer in ``pass_rate``.

The sandbox must see the tables read-only at ``--container-dir``. With the
Docker backend, in the Hermes ``config.yaml``::

    terminal:
      docker_volumes:
        - "/abs/path/to/da-dev-tables:/data/dabench:ro"

CLI::

    python -m assistant_core.dabench build --data PATH/TO/DA-Agent/data [--out DIR] [--seed 2026]
        [--dev-fraction 0.4] [--per-level N] [--pilot 9] [--container-dir /data/dabench] [--timeout 900] [--force]
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import sys

from . import bench


LEVELS = {"easy": "L1", "medium": "L2", "hard": "L3"}
SUITES = ("dev", "test", "pilot")
FILE_NAME = re.compile(r"[^/\\\0\n]{1,200}")
CONTAINER_DIR = re.compile(r"/[A-Za-z0-9._/-]{1,200}")
PROMPT = """A data file is available in your sandbox at: {path}

Question: {question}

Constraints: {constraints}

Answer format: {format}

Work on the file with code; do not guess values. End your reply with the final answer line(s) written exactly \
in the format above, @answer_name[answer], one per line, with only the answer inside the brackets."""


class DabenchError(Exception):
    """A user-facing problem with the dataset or the options."""


def _read_jsonl(path: Path) -> list:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise DabenchError(f"Cannot read {path.name} in the data directory.") from None
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise DabenchError(f"{path.name} line {number} is not valid JSON.") from None
        if not isinstance(row, dict):
            raise DabenchError(f"{path.name} line {number} is not an object.")
        rows.append(row)
    return rows


def _category(concepts) -> str:
    first = concepts[0] if isinstance(concepts, list) and concepts and isinstance(concepts[0], str) else ""
    name = re.sub(r"[^\w -]", " ", first).strip()[:40]
    return name if bench.CATEGORY.fullmatch(name or "") else "general"


def load_questions(data_dir: Path, *, container_dir: str, timeout_s: float) -> tuple:
    """(tasks, skipped). A task is the suite entry plus the original id, level and file."""
    data_dir = Path(data_dir)
    questions = _read_jsonl(data_dir / "da-dev-questions.jsonl")
    labels = {}
    for row in _read_jsonl(data_dir / "da-dev-labels.jsonl"):
        labels[row.get("id")] = row.get("common_answers")
    tables = data_dir / "da-dev-tables"
    check_files = tables.is_dir()
    tasks, skipped, seen = [], [], set()
    for row in questions:
        qid = row.get("id")

        def skip(reason):
            skipped.append(dict(id=qid, reason=reason))

        if isinstance(qid, bool) or not isinstance(qid, int) or qid < 0 or qid in seen:
            skip("missing or repeated id")
            continue
        seen.add(qid)
        level = LEVELS.get(row.get("level"))
        file_name = row.get("file_name")
        texts = [row.get(key) for key in ("question", "constraints", "format")]
        if level is None:
            skip("unknown level")
            continue
        if not isinstance(file_name, str) or not FILE_NAME.fullmatch(file_name) or file_name in (".", ".."):
            skip("unusable file name")
            continue
        if not all(isinstance(text, str) for text in texts) or not texts[0].strip():
            skip("missing question text")
            continue
        if check_files and not (tables / file_name).is_file():
            skip("table file not found")
            continue
        answers = labels.get(qid)
        try:
            expected = {name: value for name, value in answers}
        except (TypeError, ValueError):
            skip("no usable label")
            continue
        task = dict(id=f"da-{qid:04d}", level=level, category=_category(row.get("concepts")), timeout_s=timeout_s,
                    prompt=PROMPT.format(path=str(PurePosixPath(container_dir) / file_name),
                                         question=texts[0].strip(), constraints=texts[1].strip(),
                                         format=texts[2].strip()),
                    checks=[dict(type="answers", value=expected)])
        try:
            bench.parse_suite(dict(name="probe", tasks=[task]))
        except bench.BenchError as error:
            skip(str(error).replace("probe/", "")[:200])
            continue
        tasks.append(dict(task=task, qid=qid, level=level, file=file_name))
    if not tasks:
        raise DabenchError("No usable questions were found in the data directory.")
    return tasks, skipped


def split_by_file(tasks: list, *, dev_fraction: float, seed: int) -> tuple:
    """(dev, test): whole files per split, each level close to ``dev_fraction`` on dev."""
    rng = random.Random(seed)
    by_file = {}
    for item in tasks:
        by_file.setdefault(item["file"], []).append(item)
    files = sorted(by_file)
    rng.shuffle(files)
    files.sort(key=lambda name: -len(by_file[name]))  # stable: place the big files first
    dev_count = dict.fromkeys(LEVELS.values(), 0)
    seen_count = dict.fromkeys(LEVELS.values(), 0)
    dev_files = set()
    for name in files:
        added = dict.fromkeys(LEVELS.values(), 0)
        for item in by_file[name]:
            added[item["level"]] += 1

        def gap(to_dev):
            return sum((dev_count[level] + (added[level] if to_dev else 0)
                        - dev_fraction * (seen_count[level] + added[level])) ** 2 for level in added)

        with_dev, without = gap(True), gap(False)
        if with_dev < without or (with_dev == without and rng.random() < dev_fraction):
            dev_files.add(name)
            for level, count in added.items():
                dev_count[level] += count
        for level, count in added.items():
            seen_count[level] += count
    dev = [item for item in tasks if item["file"] in dev_files]
    test = [item for item in tasks if item["file"] not in dev_files]
    if not dev or not test:
        raise DabenchError("The split left one side empty; use a dev fraction between 0.1 and 0.9.")
    return dev, test


def sample_per_level(items: list, per_level: int | None, rng: random.Random) -> list:
    """At most ``per_level`` questions of each level, in the original order."""
    if per_level is None:
        return list(items)
    keep = set()
    for level in LEVELS.values():
        pool = [item["qid"] for item in items if item["level"] == level]
        keep.update(rng.sample(pool, min(per_level, len(pool))))
    return [item for item in items if item["qid"] in keep]


def build(data_dir: Path, *, container_dir="/data/dabench", dev_fraction=0.4, seed=2026, per_level=None,
          pilot=9, timeout_s=900.0) -> dict:
    """{"suites": {name: suite mapping}, "manifest": {...}} without touching the disk."""
    if not CONTAINER_DIR.fullmatch(container_dir or ""):
        raise DabenchError("--container-dir must be an absolute path of letters, digits, '.', '_', '-' and '/'.")
    if not 0.1 <= dev_fraction <= 0.9:
        raise DabenchError("--dev-fraction must be between 0.1 and 0.9.")
    if per_level is not None and per_level < 1:
        raise DabenchError("--per-level must be at least 1.")
    if not 0 <= pilot <= 30:
        raise DabenchError("--pilot must be between 0 and 30.")
    tasks, skipped = load_questions(Path(data_dir), container_dir=container_dir, timeout_s=timeout_s)
    dev, test = split_by_file(tasks, dev_fraction=dev_fraction, seed=seed)
    rng = random.Random(f"{seed}:sample")
    chosen = dict(dev=sample_per_level(dev, per_level, rng), test=sample_per_level(test, per_level, rng))
    chosen["pilot"] = sample_per_level(chosen["dev"], -(-pilot // len(LEVELS)), rng)[:pilot] if pilot else []
    described = dict(dev="for building and tuning the router", test="held out for the final evaluation",
                     pilot="a few dev questions to estimate cost")
    suites, splits = {}, {}
    for name in SUITES:
        items = chosen[name]
        if not items:
            continue
        if len(items) > bench.MAX_TASKS:
            raise DabenchError(f"The {name} split has {len(items)} questions; the limit is {bench.MAX_TASKS}. "
                               "Use --per-level to sample.")
        suite = dict(name=f"dabench-{name}",
                     description=f"InfiAgent-DABench {name} split ({described[name]}), seed {seed}.",
                     tasks=[item["task"] for item in items])
        bench.parse_suite(suite)
        suites[suite["name"]] = suite
        splits[name] = dict(
            questions=len(items), files=sorted({item["file"] for item in items}),
            levels={level: sum(item["level"] == level for item in items) for level in LEVELS.values()},
            ids=[item["qid"] for item in items])
    digests = {}
    for file_name in ("da-dev-questions.jsonl", "da-dev-labels.jsonl"):
        digests[file_name] = hashlib.sha256((Path(data_dir) / file_name).read_bytes()).hexdigest()
    manifest = dict(dataset="InfiAgent-DABench", source="https://github.com/InfiAgent/InfiAgent", seed=seed,
                    dev_fraction=dev_fraction, per_level=per_level, container_dir=container_dir,
                    timeout_s=timeout_s, usable_questions=len(tasks), sha256=digests, splits=splits,
                    skipped=skipped)
    return dict(suites=suites, manifest=manifest)


def write(result: dict, out_dir: Path, *, force=False) -> list:
    """Write the suites and the manifest; refuses to replace existing files unless ``force``."""
    import yaml
    out_dir = Path(out_dir)
    targets = {}
    for name, suite in result["suites"].items():
        text = yaml.safe_dump(suite, allow_unicode=True, sort_keys=False, width=1000)
        if len(text.encode()) > bench.MAX_SUITE_BYTES:
            raise DabenchError(f"{name} is larger than {bench.MAX_SUITE_BYTES // 1024} KB; use --per-level to sample.")
        targets[out_dir / f"{name}.yaml"] = text
    targets[out_dir / "dabench-manifest.json"] = json.dumps(result["manifest"], indent=2, ensure_ascii=False) + "\n"
    existing = [path.name for path in targets if path.exists()]
    if existing and not force:
        raise DabenchError(f"Already present in {out_dir}: {', '.join(sorted(existing))}. "
                           "Results of earlier runs refer to these suites; pass --force to replace them.")
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, text in targets.items():
        path.write_text(text, encoding="utf-8")
    return sorted(targets)


def main(argv=None, environ=None, out=None) -> int:
    environ = os.environ if environ is None else environ
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="python -m assistant_core.dabench",
                                     description="Build InfiAgent-DABench suites for the benchmark runner.")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("build", help="write dabench-dev, dabench-test and dabench-pilot suites")
    cmd.add_argument("--data", required=True, help="InfiAgent examples/DA-Agent/data directory")
    cmd.add_argument("--out", help="output directory (default: <HERMES_HOME>/benchmarks)")
    cmd.add_argument("--home", help="Hermes home (default: HERMES_HOME or ~/.hermes)")
    cmd.add_argument("--container-dir", default="/data/dabench", help="where the sandbox sees da-dev-tables")
    cmd.add_argument("--dev-fraction", type=float, default=0.4)
    cmd.add_argument("--seed", type=int, default=2026)
    cmd.add_argument("--per-level", type=int, help="sample at most this many questions per level in each split")
    cmd.add_argument("--pilot", type=int, default=9, help="size of the pilot suite, 0 for none")
    cmd.add_argument("--timeout", type=float, default=900.0, help="seconds allowed per question")
    cmd.add_argument("--force", action="store_true", help="replace suites that already exist")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return int(stop.code or 0)
    try:
        if not 5 <= args.timeout <= bench.MAX_TIMEOUT_S:
            raise DabenchError(f"--timeout must be between 5 and {bench.MAX_TIMEOUT_S}.")
        home = Path(args.home or environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
        out_dir = Path(args.out).expanduser() if args.out else home / "benchmarks"
        result = build(Path(args.data).expanduser(), container_dir=args.container_dir,
                       dev_fraction=args.dev_fraction, seed=args.seed, per_level=args.per_level,
                       pilot=args.pilot, timeout_s=args.timeout)
        paths = write(result, out_dir, force=args.force)
    except (DabenchError, bench.BenchError) as error:
        print(f"error: {error}", file=out)
        return 2
    manifest = result["manifest"]
    for name in SUITES:
        split = manifest["splits"].get(name)
        if split:
            levels = "  ".join(f"{level} {count}" for level, count in split["levels"].items())
            print(f"dabench-{name}: {split['questions']} questions from {len(split['files'])} files  ({levels})", file=out)
    if manifest["skipped"]:
        print(f"Skipped {len(manifest['skipped'])} questions; see dabench-manifest.json.", file=out)
    print(f"Wrote {len(paths)} files to {out_dir}.", file=out)
    print(f"Mount the tables read-only in the sandbox at {args.container_dir} (terminal.docker_volumes), "
          "then restart the gateway.", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
