"""ops-pack worker tests (stdlib). Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

W = Path(__file__).resolve().parent.parent / "lib" / "ops" / "ops-pack.py"


def run(*args, db):
    p = subprocess.run([sys.executable, str(W), *args, "--db", db] if args[0] == "--action" else [sys.executable, str(W), *args],
                       capture_output=True, text=True, timeout=60)
    return json.loads(p.stdout.strip().splitlines()[-1])


class OpsPackWorker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.db = str(Path(self.tmp.name) / "ops.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_manifest(self):
        m = run("--manifest", db=self.db)
        self.assertEqual(m["id"], "ops-pack"); self.assertIn("pack", [a["name"] for a in m["actions"]])

    def test_full_flow(self):
        i = run("--action", "intake", db=self.db)
        self.assertEqual(i["data"]["ingest"]["near_duplicates"], 1)
        self.assertEqual(i["data"]["route"]["rule"], 3)
        self.assertEqual(i["data"]["pending_classification"], 3)
        p = run("--action", "pack", db=self.db)
        self.assertTrue(p["data"]["needs_judgment"]); ids = [x["id"] for x in p["data"]["items"]]
        answers = [{"id": ids[0], "team": "bizops", "confidence": 0.8, "reason": "meeting logistics"},
                   {"id": ids[1], "team": "finance", "confidence": 0.9, "reason": "double charge"},
                   {"id": ids[2], "team": "marketing", "confidence": 0.9, "reason": "invalid team"}]
        f = Path(self.tmp.name) / "a.json"; f.write_text(json.dumps(answers))
        c = run("--action", "classify-apply", "--answers", str(f), db=self.db)
        self.assertEqual(c["summary"], "applied 2, rejected 1")
        s = run("--action", "status", db=self.db)
        open_ids = [x["id"] for x in s["data"]["open_approvals"]]
        self.assertIn("email:m8", open_ids)                      # money still needs a human after the bot
        self.assertFalse(run("--action", "approve", "--id", "chat:c1", "--approver", "kim@company.example", db=self.db)["ok"])
        self.assertTrue(run("--action", "approve", "--id", "chat:c1", "--approver", "sam@company.example", db=self.db)["ok"])
        d = run("--action", "dq", "--at", "2026-10-15T09:00:00", db=self.db)
        self.assertEqual(d["data"]["duplicate_vendors"], 1)

    def test_runbook_and_errors(self):
        self.assertIn("Access rules", run("--action", "runbook", db=self.db)["data"]["text"])
        self.assertFalse(run("--action", "classify-apply", db=self.db)["ok"])
        self.assertEqual(run("--action", "nope", db=self.db)["reason"], "unknown_action")


if __name__ == "__main__":
    unittest.main()
