"""Unit tests for decision-gate: hard escalate, integer scores, unknown kinds."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
WORKER = KIT / "lib" / "decision-gate.py"


def run(tmpdir: Path, action: str, config: dict | None = None, extra: list[str] | None = None) -> dict:
    args = [sys.executable, str(WORKER), "--action", action, "--dir", str(tmpdir)]
    if config is not None:
        cfg = tmpdir / "gate.json"
        cfg.write_text(json.dumps(config), encoding="utf-8")
        args.extend(["--config", str(cfg)])
    if extra:
        args.extend(extra)
    p = subprocess.run(args, cwd=str(KIT), capture_output=True, text=True, encoding="utf-8", timeout=30)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1])


class TestDecisionGate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_hard_blocked_marker_escalates_even_with_low_weight(self) -> None:
        (self.dir / "TASK.blocked").write_text("stuck", encoding="utf-8")
        res = run(self.dir, "gate", {
            "threshold": 50,
            "gates": [
                {"name": "blocked-marker", "kind": "marker_glob", "glob": "*BLOCK*,*.blocked", "weight": 1},
            ],
        })
        self.assertTrue(res["ok"])
        self.assertTrue(res["alert"])
        self.assertEqual(res["data"]["verdict"], "escalate")
        self.assertEqual(res["data"]["hard"], ["blocked-marker"])
        self.assertEqual(res["data"]["score"], 1)  # not capped, and 1 < 50 so only hard rule escalates

    def test_score_is_not_capped_at_threshold(self) -> None:
        (self.dir / "a.txt").write_text("x", encoding="utf-8")
        (self.dir / "b.txt").write_text("y", encoding="utf-8")
        res = run(self.dir, "gate", {
            "threshold": 50,
            "gates": [
                {"name": "many", "kind": "count_glob", "glob": "*.txt", "min_count": 1, "weight": 80, "hard": False},
                {"name": "also", "kind": "count_glob", "glob": "*.txt", "min_count": 1, "weight": 40, "hard": False},
            ],
        })
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["score"], 120)
        self.assertEqual(res["data"]["threshold"], 50)
        self.assertEqual(res["data"]["verdict"], "escalate")

    def test_weights_alone_hold_below_threshold(self) -> None:
        (self.dir / "a.txt").write_text("x", encoding="utf-8")
        res = run(self.dir, "gate", {
            "threshold": 50,
            "gates": [
                {"name": "few", "kind": "count_glob", "glob": "*.txt", "min_count": 1, "weight": 10, "hard": False},
            ],
        })
        self.assertTrue(res["ok"])
        self.assertFalse(res["alert"])
        self.assertEqual(res["data"]["verdict"], "hold")
        self.assertEqual(res["data"]["score"], 10)
        self.assertEqual(res["data"]["hard"], [])

    def test_unknown_kind_fails_the_gate(self) -> None:
        res = run(self.dir, "gate", {
            "threshold": 50,
            "gates": [
                {"name": "weird", "kind": "not-a-kind", "weight": 99},
            ],
        })
        self.assertFalse(res["ok"])
        self.assertTrue(res["alert"])
        self.assertEqual(res["data"]["error"], "unsupported_signal_kind")
        self.assertIn("not-a-kind", res["data"]["kinds"])

    def test_explicit_hard_false_on_blocked_uses_weights(self) -> None:
        (self.dir / "TASK.blocked").write_text("stuck", encoding="utf-8")
        res = run(self.dir, "gate", {
            "threshold": 50,
            "gates": [
                {"name": "blocked-marker", "kind": "marker_glob", "glob": "*.blocked", "weight": 1, "hard": False},
            ],
        })
        self.assertTrue(res["ok"])
        self.assertEqual(res["data"]["verdict"], "hold")
        self.assertEqual(res["data"]["hard"], [])
        self.assertEqual(res["data"]["score"], 1)


if __name__ == "__main__":
    unittest.main()
