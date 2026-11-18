"""Placement and runtime dispatch. Stdlib only; does not need addons/."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent


def load_grokkit():
    spec = importlib.util.spec_from_file_location("grokkit_under_test", KIT / "grokkit.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def last_json(text: str) -> dict:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return json.loads(lines[-1])


class TestHostIdentity(unittest.TestCase):
    def setUp(self) -> None:
        self.gk = load_grokkit()
        self._old = os.environ.get("GROKKIT_HOST")
        os.environ.pop("GROKKIT_HOST", None)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("GROKKIT_HOST", None)
        else:
            os.environ["GROKKIT_HOST"] = self._old

    def test_env_label_is_user_defined(self) -> None:
        os.environ["GROKKIT_HOST"] = "Studio"
        self.assertEqual(self.gk.current_host(), "studio")

    def test_hosts_json_maps_hostname(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            root = Path(tmp.name)
            payload = {self.gk.platform.node(): "laptop", "_comment": "ignore me"}
            (root / "hosts.json").write_text(json.dumps(payload), encoding="utf-8")
            self.gk.ROOT = root
            self.assertEqual(self.gk.current_host(), "laptop")
        finally:
            tmp.cleanup()

    def test_fallback_is_os_family(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            self.gk.ROOT = Path(tmp.name)
            self.assertEqual(self.gk.current_host(), self.gk.os_family())
            self.assertIn(self.gk.os_family(), {"linux", "darwin", "windows", "other"})
        finally:
            tmp.cleanup()


class TestPlacementSkip(unittest.TestCase):
    def setUp(self) -> None:
        self.gk = load_grokkit()
        self._old = os.environ.get("GROKKIT_HOST")
        os.environ["GROKKIT_HOST"] = "studio"

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("GROKKIT_HOST", None)
        else:
            os.environ["GROKKIT_HOST"] = self._old

    def test_class_any_does_not_skip(self) -> None:
        self.assertIsNone(self.gk.placement_skip({"id": "t", "local": {"hosts": {"class": "any"}}}))

    def test_matching_label_does_not_skip(self) -> None:
        self.assertIsNone(self.gk.placement_skip({"id": "t", "local": {"hosts": {"class": "studio"}}}))

    def test_other_label_skips_without_a_bot_turn(self) -> None:
        res = self.gk.placement_skip({"id": "t", "local": {"hosts": {"class": "laptop"}}})
        self.assertIsNotNone(res)
        self.assertEqual(res["reason"], "host_mismatch")
        self.assertTrue(res["ok"])
        self.assertFalse(res["alert"])
        self.assertFalse(res["use_bot"])
        self.assertEqual(res["host"], "studio")

    def test_os_family_mismatch_skips(self) -> None:
        # This suite runs on Linux in CI and on the Grok Bot box; skip the
        # assertion's direction if we are somehow on Windows.
        if self.gk.os_family() == "windows":
            res = self.gk.placement_skip({"id": "t", "local": {"hosts": {"class": "linux"}}})
        else:
            res = self.gk.placement_skip({"id": "t", "local": {"hosts": {"class": "windows"}}})
        self.assertEqual(res["reason"], "host_mismatch")
        self.assertIn("class", res["data"]["reasons"])

    def test_os_family_match_ignores_label(self) -> None:
        if self.gk.os_family() != "linux":
            self.skipTest("this assertion is for a Linux host")
        self.assertIsNone(self.gk.placement_skip({"id": "t", "local": {"hosts": {"class": "linux"}}}))

    def test_prefer_never_skips(self) -> None:
        self.assertIsNone(
            self.gk.placement_skip({"id": "t", "local": {"hosts": {"class": "any", "prefer": "other-host"}}})
        )


class TestRuntimeDispatch(unittest.TestCase):
    def setUp(self) -> None:
        self.gk = load_grokkit()

    def test_infer_python_default_and_suffixes(self) -> None:
        self.assertEqual(self.gk.infer_runtime(Path("lib/secrets-scan.py")), "python")
        self.assertEqual(self.gk.infer_runtime(Path("addons/bash/host-echo.sh")), "bash")
        self.assertEqual(self.gk.infer_runtime(Path("addons/node/hello.mjs")), "node")
        self.assertEqual(self.gk.infer_runtime(Path("addons/go/hello.go")), "go")
        self.assertEqual(self.gk.infer_runtime(Path("x.bin"), task={"local": {"runtime": "exec"}}), "exec")

    def test_unknown_runtime_does_not_start(self) -> None:
        res = self.gk.launch_command(Path("x"), [], "cobol")
        self.assertEqual(res["reason"], "unknown_runtime")
        self.assertFalse(res["ok"])
        self.assertFalse(res["use_bot"])

    def test_python_launch_uses_current_interpreter(self) -> None:
        argv = self.gk.launch_command(Path("lib/secrets-scan.py"), ["--manifest"], "python")
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[-1], "--manifest")


class TestActionSkipViaCli(unittest.TestCase):
    """End-to-end skip uses a shipped Python worker, not an add-on."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "kit"
        shutil.copytree(KIT, cls.root, ignore=shutil.ignore_patterns(".git", "state", "drop", "__pycache__", "*.pyc", "addons"))
        (cls.root / "state").mkdir()
        (cls.root / "drop" / "returns").mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def kit(self, *args: str, env: dict | None = None) -> dict:
        e = os.environ.copy()
        if env:
            e.update(env)
        p = subprocess.run(
            [sys.executable, "grokkit.py", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            env=e,
        )
        return last_json(p.stdout)

    def test_core_list_has_no_addon_workers(self) -> None:
        ids = {t["id"] for t in self.kit("list")["tasks"]}
        self.assertIn("decision-gate", ids)
        self.assertNotIn("host-echo", ids)
        self.assertNotIn("node-hello", ids)
        self.assertNotIn("go-hello", ids)
        self.assertFalse((self.root / "addons").exists())

    def test_python_worker_skips_on_label_mismatch(self) -> None:
        path = self.root / "tasks.json"
        original = path.read_text(encoding="utf-8")
        cfg = json.loads(original)
        for task in cfg["tasks"]:
            if task["id"] == "decision-gate":
                task.setdefault("local", {})["hosts"] = {"class": "laptop"}
        path.write_text(json.dumps(cfg), encoding="utf-8")
        try:
            res = self.kit("action", "decision-gate", "pack", env={"GROKKIT_HOST": "studio"})
            self.assertEqual(res.get("reason"), "host_mismatch", res)
            self.assertTrue(res.get("ok"))
            self.assertFalse(res.get("alert"))
            self.assertFalse(res.get("use_bot"))
            self.assertEqual(res.get("host"), "studio")
        finally:
            path.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
