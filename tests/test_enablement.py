"""enablement-kit worker + core tests (stdlib). Run: python3 -m unittest discover -s tests"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

L = Path(__file__).resolve().parent.parent / "lib" / "enablement"
W = L / "enablement-kit.py"
_s = importlib.util.spec_from_file_location("enablement_core", L / "core" / "enablement_core.py")
core = importlib.util.module_from_spec(_s); _s.loader.exec_module(core)


def run(*args):
    p = subprocess.run([sys.executable, str(W), *args], capture_output=True, text=True, timeout=60)
    return json.loads(p.stdout.strip().splitlines()[-1])


EV = [
    {"week": "W1", "user": "a", "function": "Eng", "tool": "x", "sessions": 12, "cost_usd": 1.0},
    {"week": "W1", "user": "a", "function": "Eng", "tool": "y", "sessions": 3, "cost_usd": 1.0},
    {"week": "W1", "user": "b", "function": "Eng", "tool": "x", "sessions": 2, "cost_usd": 1.0},
    {"week": "W1", "user": "c", "function": "HR", "tool": "x", "sessions": 1, "cost_usd": 7.0},
    {"week": "W1", "user": "d", "function": "HR", "tool": "x", "sessions": 0, "cost_usd": 0.0},
    {"week": "W0", "user": "e", "function": "Fin", "tool": "x", "sessions": 5, "cost_usd": 1.0},
]
HC = {"Eng": 2, "HR": 4, "Fin": 3, "Ops": 0}


class Core(unittest.TestCase):
    def test_frontmatter(self):
        m, body = core.parse_frontmatter("---\nname: a-b\ndescription: >-\n  Does x.\n  Use when y.\ntags: [p, q]\n---\n# T\n")
        self.assertEqual(m["name"], "a-b"); self.assertEqual(m["description"], "Does x. Use when y."); self.assertEqual(m["tags"], ["p", "q"])
        self.assertEqual(core.parse_frontmatter("# no fm")[0], None)

    def test_lint_clean_and_dirty(self):
        ok = "---\nname: tidy\ndescription: Tidies notes. Use when notes are messy.\n---\nSee [t](t.md).\n"
        self.assertEqual(core.lint_skill("tidy", ok, {"t.md"}), [])
        bad = "---\nname: Bad_Name\ndescription: x\n---\nRead /home/anna/x and [m](missing.md). key sk-" + "a" * 24 + "\n"
        codes = {i["code"] for i in core.lint_skill("Bad_Name", bad)}
        self.assertTrue({"name_format", "description_no_trigger", "broken_ref", "absolute_path", "secret_like"} <= codes)
        self.assertIn("no_frontmatter", {i["code"] for i in core.lint_skill("x", "# hi")})
        self.assertIn("name_dir_mismatch", {i["code"] for i in core.lint_skill("other", ok, {"t.md"})})

    def test_adoption(self):
        self.assertEqual(core.weekly_active(EV, "W1"), {"Eng": 2, "HR": 1})
        self.assertEqual(core.adoption_rate(EV, HC, "W1"), {"Eng": 1.0, "Fin": 0.0, "HR": 0.25, "Ops": 0.0})
        self.assertEqual(core.power_share(EV, "W1"), {"Eng": 0.5, "HR": 0.0})
        self.assertEqual([core.maturity(r, p) for r, p in [(0, 0), (0.1, 1), (0.3, 1), (0.9, 0.1), (0.9, 0.5)]], [0, 1, 2, 3, 4])
        self.assertEqual(core.spend_flags(EV, "W1"), ["HR"])
        self.assertEqual(core.spend_flags([], "W1"), [])
        r = core.adoption_report(EV, HC, "W1")
        self.assertEqual(r["below_target"], ["Fin", "HR", "Ops"]); self.assertEqual(r["overall_rate"], 0.333)

    def test_intake(self):
        base = {"data_class": "internal", "scopes": ["read"], "vendor_region": "EU", "human_review": True}
        self.assertEqual(core.screen({"id": 1, **base})["decision"], "fast-track")
        self.assertEqual(core.screen({"id": 2, **base, "data_class": "restricted", "vendor_region": "US"})["decision"], "reject")
        r = core.screen({"id": 3, **base, "personal_data": True, "scopes": ["write"]})
        self.assertEqual((r["decision"], r["reviews"]), ("review", ["legal", "tech"]))
        r = core.screen({"id": 4, **base, "type": "mcp_connector"})
        self.assertIn("MCP connector without a tool allowlist", r["reasons"])
        g = core.agenda([{"id": "a", **base}, {"id": "b", **base, "external_output": True, "human_review": False},
                         {"id": "c", **base, "personal_data": True}])
        self.assertEqual([x["id"] for x in g["agenda"]], ["c", "b"]); self.assertEqual(g["fast_track"], ["a"])


class Worker(unittest.TestCase):
    def test_manifest_and_actions(self):
        m = run("--manifest"); self.assertEqual(m["id"], "enablement-kit")
        self.assertEqual({a["name"] for a in m["actions"]}, {"skill-lint", "adoption", "intake", "pack"})
        self.assertEqual(run("--action", "nope")["reason"], "unknown_action")

    def test_fixtures(self):
        s = run("--action", "skill-lint")
        self.assertEqual((s["data"]["skills"], s["data"]["with_errors"]), (3, ["Test_Ideas"]))
        a = run("--action", "adoption"); self.assertEqual(a["data"]["week"], "2026-W42"); self.assertIn("Marketing", a["data"]["spend_flags"])
        i = run("--action", "intake"); self.assertEqual((i["data"]["fast_track"], i["data"]["rejected"]), (["P-101"], ["P-104"]))
        p = run("--action", "pack"); self.assertTrue(p["data"]["needs_judgment"])

    def test_bad_input(self):
        with tempfile.TemporaryDirectory() as t:
            r = run("--action", "adoption", "--events", str(Path(t) / "missing.csv"))
            self.assertFalse(r["ok"]); self.assertEqual(r["reason"], "bad_input")


if __name__ == "__main__":
    unittest.main()
