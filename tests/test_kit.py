"""Stdlib smoke tests. Run from the kit root: python3 -m unittest discover -s tests

Each test class works on a throwaway copy of the kit, so state/ and drop/ in
the real tree are never touched.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
IGNORE = shutil.ignore_patterns(".git", "state", "drop", "__pycache__", "*.pyc")

VALID = """# RETURN v1
source: web-chat-anthropic
model: test-model
date: 2026-10-02
ask: T1
audience: bot
sections: SUMMARY, FINDINGS
budget: 40

## [SUMMARY] one line
All good.

## [FINDINGS] details
~~~yaml
- a: 1
## [NOT_A_SECTION] inside a fence
~~~
- b: 2
"""


def last_json(text: str) -> dict:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return json.loads(lines[-1])


class KitCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "kit"
        shutil.copytree(KIT, cls.root, ignore=IGNORE)
        (cls.root / "state").mkdir()
        (cls.root / "drop" / "returns").mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def kit(self, *args: str) -> dict:
        p = subprocess.run([sys.executable, "grokkit.py", *args], cwd=self.root,
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        return last_json(p.stdout)

    def worker(self, name: str, *args: str) -> dict:
        p = subprocess.run([sys.executable, str(self.root / "lib" / name), *args], cwd=self.root,
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        return last_json(p.stdout)


class TestCore(KitCase):
    def test_manifests_and_stamps(self) -> None:
        current = json.loads((self.root / "sources.json").read_text())["current_grok_bot"]
        self.assertEqual(current, "0.58.0")
        files = sorted((self.root / "lib").glob("*.py"))
        self.assertEqual(len(files), 14)
        for f in files:
            with self.subTest(worker=f.name):
                man = self.worker(f.name, "--manifest")
                self.assertTrue(man.get("ok"))
                self.assertEqual(man.get("verified_grok_bot"), current)
                for key in ("id", "title", "source", "priority", "keywords", "default_action", "actions"):
                    self.assertIn(key, man)

    def test_no_flags_does_no_work(self) -> None:
        for f in sorted((self.root / "lib").glob("*.py")):
            with self.subTest(worker=f.name):
                self.assertFalse(self.worker(f.name).get("ok"))

    def test_list_matches_tasks(self) -> None:
        ids = {t["id"] for t in json.loads((self.root / "tasks.json").read_text())["tasks"]}
        listed = {t["id"] for t in self.kit("list")["tasks"]}
        self.assertEqual(ids, listed)
        self.assertIn("ret-lint", ids)

    def test_inbox_quiet(self) -> None:
        self.assertTrue(self.kit("inbox")["quiet"])

    def test_meta_workers_clean(self) -> None:
        lint = self.kit("action", "worker-lint", "collect")
        self.assertTrue(lint["ok"], lint)
        self.assertEqual(lint["data"]["counts"]["issues"], 0, lint)
        for tid, act in (("version-gate", "check"), ("schema-guard", "collect"), ("audit-harness", "validate"),
                         ("secrets-scan", "collect"), ("worker-index", "collect")):
            with self.subTest(task=tid):
                res = self.kit("action", tid, act)
                self.assertTrue(res["ok"], res)
                self.assertFalse(res["alert"], res)

    def test_route_hint_uses_running_interpreter(self) -> None:
        res = self.kit("route", "lint returns section extract")
        self.assertEqual(res["matched"], "ret-lint")
        self.assertEqual(res["run"][0], sys.executable)


class TestReturns(KitCase):
    def write(self, name: str, text: str) -> str:
        path = self.root / "drop" / "returns" / name
        path.write_text(text, encoding="utf-8")
        return "drop/returns/" + name

    def test_round_trip(self) -> None:
        rel = self.write("ok.md", VALID)
        lint = self.kit("returns", "lint", rel)
        self.assertTrue(lint["ok"], lint)
        self.assertEqual([s["id"] for s in lint["data"]["sections"]], ["SUMMARY", "FINDINGS"])
        ing = self.kit("returns", "ingest", rel)
        self.assertTrue(ing["ok"], ing)
        self.assertFalse(ing["duplicate"])
        self.assertEqual((ing["source"], ing["model"], ing["date"]), ("web-chat-anthropic", "test-model", "2026-10-02"))
        self.assertTrue((self.root / ing["archive"]).exists())
        again = self.kit("returns", "ingest", rel)
        self.assertTrue(again["duplicate"])
        listed = self.kit("returns", "list")
        self.assertIn(ing["ret_id"], [r["ret_id"] for r in listed["returns"]])
        ex = self.kit("returns", "extract", ing["ret_id"], "FINDINGS")
        self.assertTrue(ex["ok"], ex)
        self.assertIn("NOT_A_SECTION", ex["data"]["body"])
        self.assertEqual(ex["provenance"]["source"], "web-chat-anthropic")
        miss = self.kit("returns", "extract", ing["ret_id"], "NOPE")
        self.assertFalse(miss["ok"])

    def test_cli_flags_and_outer_fence(self) -> None:
        body = "```\n" + VALID.replace("source: web-chat-anthropic\nmodel: test-model\n", "") + "```\n"
        rel = self.write("fenced.md", body)
        no_prov = self.kit("returns", "ingest", rel)
        self.assertEqual(no_prov["reason"], "missing_provenance")
        ing = self.kit("returns", "ingest", rel, "--source", "web-chat-openai", "--model", "m2")
        self.assertTrue(ing["ok"], ing)
        bad_src = self.kit("returns", "ingest", self.write("other.md", VALID.replace("All good.", "x")),
                           "--source", "nobody")
        self.assertEqual(bad_src["reason"], "unknown_source")

    def test_invalid_returns(self) -> None:
        cases = {
            "nomagic.md": VALID.replace("# RETURN v1\n", ""),
            "missing.md": VALID.replace("SUMMARY, FINDINGS", "SUMMARY, FINDINGS, NEXT"),
            "budget.md": VALID.replace("budget: 40", "budget: 5"),
            "dupe.md": VALID + "\n## [SUMMARY] again\nx\n",
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                res = self.kit("returns", "ingest", self.write(name, text))
                self.assertFalse(res["ok"], res)
                self.assertEqual(res["reason"], "return_lint_failed")
        col = self.kit("action", "ret-lint", "collect")
        self.assertTrue(col["ok"])
        self.assertTrue(col["alert"])
        self.assertGreaterEqual(col["data"]["counts"]["invalid"], 4)


if __name__ == "__main__":
    unittest.main()
