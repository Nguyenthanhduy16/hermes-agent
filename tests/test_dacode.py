"""DA-Code suites, working folders and scoring (the original evaluator is stubbed)."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import yaml

from assistant_core import bench, dacode


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


class FakeDacode:
    """A tiny DA-Code checkout: tasks per group, one visualization task, one without source."""

    IDS = ["data-sa-001", "data-sa-002", "data-sa-003", "di-text-001", "di-text-002", "dm-csv-001", "dm-csv-002",
           "ml-binary-001", "ml-cluster-001", "ml-regression-001", "ml-competition-001", "ml-multi-001"]

    def __init__(self, root: Path):
        self.root = root
        tasks, evals = [], []
        for task_id in self.IDS + ["plot-bar-001", "data-sa-099"]:
            visual = task_id.startswith("plot")
            tasks.append(dict(id=task_id, type="Data Visualization" if visual else "Statistical Analysis",
                              instruction=f"Solve {task_id}.", hardness="Hard",
                              post_process=["plot_process"] if visual else []))
            evals.append(dict(id=task_id, config=dict(task="data visualization" if visual else "statistical analysis",
                                                      type="csv", hardness="Hard"),
                              func=["compare_csv"], result=[{"file": ["result.csv"]}], options=[{}]))
            if task_id != "data-sa-099":
                source = root / "da_code" / "source" / task_id
                source.mkdir(parents=True)
                (source / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
                (source / "result.csv").write_text("template\n", encoding="utf-8")
        write_jsonl(root / "da_code" / "configs" / "task" / "all.jsonl", tasks)
        write_jsonl(root / "da_code" / "configs" / "eval" / "eval_all.jsonl", evals)


class DacodeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.data = self.dir / "da-code"
        FakeDacode(self.data)
        self.home = self.dir / "home"

    def test_tasks_skip_visualization_and_missing_sources(self):
        ids = [t["id"] for t in dacode.load_tasks(self.data)]
        self.assertEqual(sorted(FakeDacode.IDS), ids)

    def test_build_writes_valid_suites_and_a_balanced_reproducible_pilot(self):
        written = dict(dacode.build(self.data, self.home / "benchmarks", container_dir="/dacode", timeout_s=900,
                                    pilot=1, seed=7, force=False))
        pilot = [t["id"] for t in written["dacode-pilot"]]
        groups = [dacode._group(t) for t in pilot]
        self.assertEqual({"sa": 2, "di": 2, "dm": 2, "ml": 4}, {g: groups.count(g) for g in set(groups)})
        self.assertEqual(4, len({t.rsplit("-", 1)[0] for t in pilot if t.startswith("ml-")}))  # 4 ML kinds
        suite = bench.load_suite_file(self.home / "benchmarks" / "dacode-pilot.yaml", source="home")
        task = suite.tasks[0]
        self.assertTrue(task.id.startswith("dc-"))
        self.assertIn(f"/dacode/{task.id.removeprefix('dc-')}", task.prompt)
        self.assertIn("<answer>", task.prompt)
        self.assertEqual("external", task.checks[0].type)
        again = dacode.pick_pilot(dacode.load_tasks(self.data), 7)
        self.assertEqual(pilot, [t["id"] for t in again])
        with self.assertRaises(dacode.DacodeError):
            dacode.build(self.data, self.home / "benchmarks", container_dir="/dacode", timeout_s=900,
                         pilot=1, seed=7, force=False)

    def test_reset_copies_sources_and_changed_files_finds_outputs(self):
        work = self.dir / "work"
        dacode.reset(self.data, work, ["data-sa-001"], dacode.DEFAULT_IMAGE)
        folder = work / "data-sa-001"
        (folder / "result.csv").write_text("p_val,result\n0.1,reject\n", encoding="utf-8")
        (folder / "sub").mkdir()
        (folder / "sub" / "model.txt").write_text("x", encoding="utf-8")
        added, changed = dacode.changed_files(self.data / "da_code" / "source" / "data-sa-001", folder)
        self.assertEqual([Path("sub/model.txt")], added)
        self.assertEqual([Path("result.csv")], changed)
        dacode.reset(self.data, work, ["data-sa-001"], dacode.DEFAULT_IMAGE)  # fresh again
        self.assertEqual("template\n", (folder / "result.csv").read_text(encoding="utf-8"))
        self.assertFalse((folder / "sub").exists())

    def test_the_last_answer_block_wins(self):
        self.assertEqual("result.csv", dacode.extract_answer("x <answer>old</answer> y <ANSWER> result.csv </answer>"))
        self.assertEqual("", dacode.extract_answer("no block"))

    def test_score_builds_result_json_and_stores_scores(self):
        work, data_dir = self.dir / "work", self.home / "assistant-core"
        dacode.build(self.data, self.home / "benchmarks", container_dir="/dacode", timeout_s=900, pilot=1, seed=7,
                     force=False)
        suite = bench.load_suite_file(self.home / "benchmarks" / "dacode-pilot.yaml", source="home")
        ids = [t.id.removeprefix("dc-") for t in suite.tasks]
        dacode.reset(self.data, work, ids, dacode.DEFAULT_IMAGE)
        (work / ids[0] / "result.csv").write_text("answer\n", encoding="utf-8")
        # Hermes' session store has the full final reply; the bench row only a prefix.
        state = sqlite3.connect(self.home / "state.db")
        state.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
        state.execute("INSERT INTO messages (session_id, role, content) VALUES ('s0', 'assistant', ?)",
                      ("long work... <answer>result.csv</answer>",))
        state.commit()
        state.close()
        store = bench.BenchStore(data_dir / "bench.sqlite3")
        run = store.create_run(suite, label="t", repeats=1, concurrency=1)
        for number, task in enumerate(suite.tasks):
            store.add_result(run["id"], dict(task_id=task.id, attempt=1, level=task.level, category=task.category,
                                             passed=False, checks=[], session_id=f"s{number}",
                                             response_text="long work... (cut)" if number == 0 else "",
                                             error=None if number < 2 else "timeout"))
        store.close()
        seen = {}

        def evaluator(data, out, image, timeout_s):
            for task_id in ids:
                seen[task_id] = json.loads((out / task_id / "dabench" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ids), len((out / "eval.jsonl").read_text(encoding="utf-8").splitlines()))
            return {ids[0]: {"id": ids[0], "total_score": 1.0}, ids[1]: {"id": ids[1], "total_score": 0.4}}

        with patch.object(dacode, "run_evaluator", side_effect=evaluator):
            report = dacode.score(run["id"], home=self.home, data_dir=data_dir, data=self.data, work=work,
                                  image=dacode.DEFAULT_IMAGE)
        first = seen[ids[0]]
        self.assertEqual(("result.csv", True), (first["result"], first["finished"]))
        self.assertEqual(1, len(first["result_files"]["changed_files"]))
        self.assertFalse(seen[ids[2]]["finished"])  # request error
        self.assertEqual([1.0, 0.4] + [0.0] * (len(ids) - 2), [value for _, value, _ in report])
        store = bench.BenchStore(data_dir / "bench.sqlite3")
        self.addCleanup(store.close)
        results = store.results(run["id"])
        self.assertEqual([True] + [False] * (len(ids) - 1), [r["passed"] for r in results])
        self.assertEqual(0.4, results[1]["checks"][0]["score"])
        self.assertEqual(1, store.get_run(run["id"])["passed"])
        summary = bench.summarize(store.get_run(run["id"]), results)
        self.assertAlmostEqual(1.4 / len(ids), summary["external_score"])

    def test_repeats_are_refused(self):
        dacode.build(self.data, self.home / "benchmarks", container_dir="/dacode", timeout_s=900, pilot=0, seed=7,
                     force=False)
        suite = bench.load_suite_file(self.home / "benchmarks" / "dacode-sample.yaml", source="home")
        store = bench.BenchStore(self.home / "assistant-core" / "bench.sqlite3")
        run = store.create_run(suite, label="t", repeats=2, concurrency=1)
        store.add_result(run["id"], dict(task_id=suite.tasks[0].id, attempt=2, passed=False, checks=[]))
        store.close()
        with self.assertRaises(dacode.DacodeError):
            dacode.score(run["id"], home=self.home, data_dir=self.home / "assistant-core", data=self.data,
                         work=self.dir / "work", image=dacode.DEFAULT_IMAGE)

    def test_split_is_disjoint_stratified_reproducible_and_keeps_included_tasks_in_dev(self):
        tasks = dacode.load_tasks(self.data)
        split = dacode.pick_split(tasks, 3, 4, 5, dev_include=["ml-binary-001"])
        dev, test = split["dacode-dev"], split["dacode-test"]
        self.assertEqual((4, 5, set()), (len(dev), len(test), set(dev) & set(test)))
        self.assertIn("ml-binary-001", dev)
        self.assertEqual(split, dacode.pick_split(tasks, 3, 4, 5, dev_include=["ml-binary-001"]))
        self.assertEqual(9, sum(r["dev"] + r["test"] for r in split["strata"].values()))
        self.assertEqual({"di/Hard": 2, "dm/Hard": 2, "ml/Hard": 5, "sa/Hard": 3},
                         {k: r["available"] for k, r in split["strata"].items()})
        self.assertEqual(4, split["strata"]["ml/Hard"]["dev"] + split["strata"]["ml/Hard"]["test"])  # 9 * 5/12
        everything = dacode.pick_split(tasks, 3, 5, 7)
        self.assertEqual(sorted(t["id"] for t in tasks), sorted(everything["dacode-dev"] + everything["dacode-test"]))
        for bad in (dict(n_dev=10, n_test=10), dict(n_dev=0, n_test=1, dev_include=["ml-binary-001"]),
                    dict(n_dev=1, n_test=1, dev_include=["nope"])):
            with self.assertRaises(dacode.DacodeError):
                dacode.pick_split(tasks, 3, **bad)
        # tasks listed in a zip count as available before extraction
        self.assertEqual(["data-sa-099"], [t["id"] for t in dacode.load_tasks(self.data, {"data-sa-099"})])

    def test_build_split_writes_dev_and_test_suites_and_keeps_other_manifest_entries(self):
        dacode.build(self.data, self.home / "benchmarks", container_dir="/dacode", timeout_s=900, pilot=1, seed=7,
                     force=False)
        pilot = (self.home / "benchmarks" / "dacode-pilot.yaml").read_text(encoding="utf-8")
        split = dacode.pick_split(dacode.load_tasks(self.data), 3, 4, 5)
        written = dacode.build_split(self.data, self.home / "benchmarks", split, container_dir="/dacode",
                                     timeout_s=1800, force=False)
        self.assertEqual(["dacode-dev", "dacode-test"], [name for name, _ in written])
        dev = bench.load_suite_file(self.home / "benchmarks" / "dacode-dev.yaml", source="home")
        self.assertEqual(sorted("dc-" + i for i in split["dacode-dev"]), sorted(t.id for t in dev.tasks))
        self.assertEqual(pilot, (self.home / "benchmarks" / "dacode-pilot.yaml").read_text(encoding="utf-8"))
        manifest = json.loads((self.home / "benchmarks" / "dacode-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual({"dacode-sample", "dacode-pilot", "dacode-dev", "dacode-test"}, set(manifest["suites"]))
        self.assertEqual(3, manifest["split"]["seed"])
        with self.assertRaises(dacode.DacodeError):  # exists
            dacode.build_split(self.data, self.home / "benchmarks", split, container_dir="/dacode", timeout_s=1800,
                               force=False)
        with self.assertRaises(dacode.DacodeError):  # a task without source
            dacode.build_split(self.data, self.home / "benchmarks", dict(split, **{"dacode-dev": ["data-sa-099"]}),
                               container_dir="/dacode", timeout_s=1800, force=True)

    def test_reset_for_resume_keeps_finished_folders(self):
        dacode.build(self.data, self.home / "benchmarks", container_dir="/dacode", timeout_s=900, pilot=0, seed=7,
                     force=False)
        suite = bench.load_suite_file(self.home / "benchmarks" / "dacode-sample.yaml", source="home")
        data_dir = self.home / "assistant-core"
        store = bench.BenchStore(data_dir / "bench.sqlite3")
        run = store.create_run(suite, label="t", repeats=1, concurrency=1)
        store.add_result(run["id"], dict(task_id="dc-data-sa-001", attempt=1, passed=False, checks=[]))
        store.close()
        work = self.dir / "work"
        dacode.main(["--home", str(self.home), "--data", str(self.data), "--work", str(work),
                     "reset", "--suite", "dacode-sample"])
        for task_id in ("data-sa-001", "data-sa-002"):
            (work / task_id / "result.csv").write_text("answer\n", encoding="utf-8")
        with patch.dict("os.environ", {"ASSISTANT_DATA_DIR": str(data_dir)}):
            self.assertEqual(0, dacode.main(["--home", str(self.home), "--data", str(self.data), "--work", str(work),
                                             "reset", "--suite", "dacode-sample", "--run", run["id"]]))
        self.assertEqual("answer\n", (work / "data-sa-001" / "result.csv").read_text(encoding="utf-8"))  # finished
        self.assertEqual("template\n", (work / "data-sa-002" / "result.csv").read_text(encoding="utf-8"))  # half-done
        ids = [t.id.removeprefix("dc-") for t in suite.tasks]
        self.assertEqual(ids[1:], dacode.pending_ids(data_dir, run["id"], ids))
        with self.assertRaises(dacode.DacodeError):
            dacode.pending_ids(data_dir, "nope", ids)


class ExternalCheckTests(unittest.TestCase):
    def test_external_checks_parse_and_stay_pending(self):
        suite = bench.parse_suite(dict(name="x", tasks=[dict(id="a", level="L1", prompt="p", checks=[
            {"type": "external", "value": "dacode"}])]))
        self.assertEqual("external", suite.tasks[0].checks[0].type)
        out = bench.evaluate_checks(suite.tasks[0].checks, "reply", 1.0)
        self.assertEqual((False, True), (out[0]["passed"], out[0]["pending"]))
        with self.assertRaises(bench.BenchError):
            bench.parse_suite(dict(name="x", tasks=[dict(id="a", level="L1", prompt="p", checks=[
                {"type": "external", "value": "other"}])]))
        self.assertIsNone(bench.external_score([dict(checks=out)]))


if __name__ == "__main__":
    unittest.main()
