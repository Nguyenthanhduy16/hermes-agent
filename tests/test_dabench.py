"""InfiAgent-DABench converter (assistant_core.dabench) on a small made-up dataset."""
import io
import json
from pathlib import Path
import tempfile
import unittest

import yaml

from assistant_core import bench, dabench


LEVELS = ("easy", "medium", "hard")


def make_dataset(root: Path, files=12, per_file=4) -> Path:
    data = root / "data"
    (data / "da-dev-tables").mkdir(parents=True)
    questions, labels = [], []
    for f in range(files):
        name = f"table_{f:02d}.csv"
        (data / "da-dev-tables" / name).write_text("a,b\n1,2\n")
        for q in range(per_file):
            qid = f * 10 + q
            questions.append(dict(id=qid, question=f"What is the mean of a in file {f}?", concepts=["Summary Statistics"],
                                  constraints="Round to two decimals.", format="@mean_a[value] with two decimals.",
                                  file_name=name, level=LEVELS[(f + q) % 3]))
            labels.append(dict(id=qid, common_answers=[["mean_a", f"{qid}.50"], ["verdict", "no"]]))
    (data / "da-dev-questions.jsonl").write_text("\n".join(json.dumps(row) for row in questions) + "\n")
    (data / "da-dev-labels.jsonl").write_text("\n".join(json.dumps(row) for row in labels) + "\n")
    return data


class DabenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = make_dataset(self.root)

    def test_split_keeps_files_apart_and_is_reproducible(self):
        first = dabench.build(self.data, seed=7)
        again = dabench.build(self.data, seed=7)
        other = dabench.build(self.data, seed=8)
        self.assertEqual(first, again)
        self.assertNotEqual(first["manifest"]["splits"]["dev"]["ids"], other["manifest"]["splits"]["dev"]["ids"])
        splits = first["manifest"]["splits"]
        dev, test = set(splits["dev"]["files"]), set(splits["test"]["files"])
        self.assertFalse(dev & test)
        self.assertEqual(12, len(dev | test))
        self.assertEqual(48, splits["dev"]["questions"] + splits["test"]["questions"])
        for level in ("L1", "L2", "L3"):  # 16 questions per level, 40% on dev
            self.assertTrue(4 <= splits["dev"]["levels"][level] <= 9, splits["dev"]["levels"])
        self.assertTrue(set(splits["pilot"]["ids"]) <= set(splits["dev"]["ids"]))
        self.assertEqual({"L1": 3, "L2": 3, "L3": 3}, splits["pilot"]["levels"])

    def test_tasks_are_valid_suites_scored_by_the_answers_check(self):
        result = dabench.build(self.data, container_dir="/mnt/tables", timeout_s=300)
        suite = bench.parse_suite(result["suites"]["dabench-test"])
        task = suite.tasks[0]
        qid = int(task.id.split("-")[1])
        self.assertIn(f"/mnt/tables/table_{qid // 10:02d}.csv", task.prompt)
        self.assertIn("@mean_a[value]", task.prompt)
        self.assertEqual(("Summary Statistics", 300.0), (task.category, task.timeout_s))
        good = bench.evaluate_checks(task.checks, f"@mean_a[{qid}.5]\n@verdict[no]", 1.0)[0]
        bad = bench.evaluate_checks(task.checks, f"@mean_a[{qid}.5]\n@verdict[yes]", 1.0)[0]
        self.assertEqual((True, 2, False, 1), (good["passed"], good["total"], bad["passed"], bad["matched"]))

    def test_per_level_sampling_and_no_pilot(self):
        result = dabench.build(self.data, per_level=2, pilot=0)
        splits = result["manifest"]["splits"]
        self.assertNotIn("pilot", splits)
        self.assertNotIn("dabench-pilot", result["suites"])
        for name in ("dev", "test"):
            self.assertTrue(all(count <= 2 for count in splits[name]["levels"].values()))

    def test_unusable_rows_are_skipped_with_a_reason(self):
        questions = self.data / "da-dev-questions.jsonl"
        rows = [json.loads(line) for line in questions.read_text().splitlines()]
        rows[0]["level"] = "extreme"
        rows[1]["file_name"] = "../secret.csv"
        rows[2]["file_name"] = "missing.csv"
        rows.append(dict(rows[3]))                                   # repeated id
        rows.append(dict(rows[3], id=999))                           # no label
        questions.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        labels = self.data / "da-dev-labels.jsonl"
        label_rows = [json.loads(line) for line in labels.read_text().splitlines()]
        label_rows[4]["common_answers"] = [["mean_a", "x" * 501]]   # too long to score
        labels.write_text("\n".join(json.dumps(row) for row in label_rows) + "\n")
        manifest = dabench.build(self.data)["manifest"]
        self.assertEqual(48 - 4, manifest["usable_questions"])
        reasons = {item["id"]: item["reason"] for item in manifest["skipped"]}
        self.assertEqual("unknown level", reasons[rows[0]["id"]])
        self.assertEqual("unusable file name", reasons[rows[1]["id"]])
        self.assertEqual("table file not found", reasons[rows[2]["id"]])
        self.assertEqual("no usable label", reasons[999])
        self.assertIn("answers needs", reasons[rows[4]["id"]])
        self.assertEqual(6, len(manifest["skipped"]))

    def test_bad_options_and_missing_data(self):
        for options in (dict(dev_fraction=0.05), dict(container_dir="relative/dir"), dict(per_level=0),
                        dict(pilot=31)):
            with self.assertRaises(dabench.DabenchError, msg=str(options)):
                dabench.build(self.data, **options)
        with self.assertRaises(dabench.DabenchError):
            dabench.build(self.root / "nowhere")

    def test_cli_writes_loadable_suites_and_does_not_overwrite(self):
        home = self.root / "home"
        env = {"HERMES_HOME": str(home)}
        out = io.StringIO()
        self.assertEqual(0, dabench.main(["build", "--data", str(self.data), "--seed", "3"], env, out))
        self.assertIn("dabench-test:", out.getvalue())
        suites, problems = bench.load_suites(home)
        self.assertEqual([], problems)
        self.assertTrue({"dabench-dev", "dabench-test", "dabench-pilot"} <= set(suites))
        self.assertEqual("home", suites["dabench-dev"].source)
        manifest = json.loads((home / "benchmarks" / "dabench-manifest.json").read_text())
        self.assertEqual(3, manifest["seed"])
        # Answers stay text in the YAML: "0.50" must not come back as the number 0.5.
        task = yaml.safe_load((home / "benchmarks" / "dabench-dev.yaml").read_text())["tasks"][0]
        self.assertTrue(all(isinstance(value, str) for value in task["checks"][0]["value"].values()))
        before = (home / "benchmarks" / "dabench-dev.yaml").read_text()
        out = io.StringIO()
        self.assertEqual(2, dabench.main(["build", "--data", str(self.data), "--seed", "4"], env, out))
        self.assertIn("--force", out.getvalue())
        self.assertEqual(before, (home / "benchmarks" / "dabench-dev.yaml").read_text())
        self.assertEqual(0, dabench.main(["build", "--data", str(self.data), "--seed", "4", "--force"], env,
                                         io.StringIO()))
        self.assertEqual(2, dabench.main(["build", "--data", str(self.data), "--timeout", "1", "--force"], env,
                                         io.StringIO()))


if __name__ == "__main__":
    unittest.main()
