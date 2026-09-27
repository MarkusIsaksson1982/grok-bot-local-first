#!/usr/bin/env python3
"""audit-harness: check lib workers against the JSON contract.

Lists *.py workers under lib/, runs --manifest and one sample --action on each,
and emits a tiny pass/fail digest. Stdlib only. Does not mutate files.

origin on a manifest records where that worker was last iterated. It is not a
required or recommended place to run the worker. An externally sourced worker
may name another tool there, for example grok_build "opencode".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

SOURCE = "grok-4.7-high"
ID = "audit-harness"

MANIFEST = {
    "verified_at": "2026-09-27",
    "verified_grok_bot": "0.61.0",
    "ok": True,
    "id": ID,
    "title": "Harness auditor",
    "source": SOURCE,
    "origin": {"grok_build": "1.0.41", "os": "windows-11"},
    "priority": 65,
    "keywords": ["audit", "contract", "validate"],
    "default_action": "validate",
    "actions": [
        {"name": "scan", "use_bot": False,
         "summary": "List workers and collect --manifest results"},
        {"name": "validate", "use_bot": False,
         "summary": "Run contract checks; pass/fail + short issues"},
        {"name": "pack", "use_bot": True,
         "summary": "Tiny review pack only when failures exist"},
    ],
}

ACTION_KEYS = {"ok", "alert", "summary", "data", "action"}


def emit(obj: object, code: int = 0) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n")
    return code


def _find_lib_dir() -> str:
    env = os.environ.get("LIB_DIR")
    if env and os.path.isdir(env):
        return env
    here = os.getcwd()
    cand = os.path.join(here, "lib")
    if os.path.isdir(cand):
        return cand
    cur = here
    for _ in range(6):
        cur = os.path.dirname(cur)
        if not cur:
            break
        lc = os.path.join(cur, "lib")
        if os.path.isdir(lc):
            return lc
    return cand


def _run(args: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run([sys.executable, *args], capture_output=True,
                           text=True, timeout=20, cwd=os.path.dirname(args[0]))
        return p.returncode, p.stdout
    except Exception as exc:  # noqa: BLE001
        return -1, f"error:{exc}"


def _last_json(stdout: str):
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _candidates(lib_dir: str) -> list[str]:
    out: list[str] = []
    if not os.path.isdir(lib_dir):
        return out
    for dirpath, dirnames, filenames in os.walk(lib_dir):
        dirnames[:] = [x for x in dirnames if x.lower() != "__pycache__" and not x.startswith(".")]
        rel = os.path.relpath(dirpath, lib_dir)
        if rel != "." and os.sep in rel:
            dirnames[:] = []
            continue
        for n in filenames:
            if n.endswith(".py") and os.path.isfile(os.path.join(dirpath, n)):
                out.append(os.path.join(dirpath, n))
    return out


def _check_one(path: str) -> dict:
    rec = {"file": os.path.basename(path), "manifest_ok": False,
           "action_ok": False, "issues": []}

    code, out = _run([path, "--manifest"])
    m = _last_json(out)
    if not isinstance(m, dict) or m.get("ok") is not True:
        rec["issues"].append("manifest: bad/no JSON")
        return rec
    rec["manifest_ok"] = True

    names = []
    acts = m.get("actions")
    if isinstance(acts, list):
        for a in acts:
            if isinstance(a, dict) and a.get("name"):
                names.append(a["name"])
    if not names:
        rec["issues"].append("manifest: no actions")
        return rec

    sample = names[0]
    code, out = _run([path, "--action", sample])
    a = _last_json(out)
    if not isinstance(a, dict):
        rec["issues"].append("action: last line not JSON")
        return rec
    miss = ACTION_KEYS - set(a.keys())
    extra = set(a.keys()) - ACTION_KEYS
    if miss:
        rec["issues"].append("action: missing " + ",".join(sorted(miss)))
    if extra:
        rec["issues"].append("action: extra " + ",".join(sorted(extra)))
    if a.get("action") != sample:
        rec["issues"].append("action: echo mismatch")
    if not miss and not extra and a.get("action") == sample:
        rec["action_ok"] = True
    return rec


def _build() -> dict:
    lib_dir = _find_lib_dir()
    records = [_check_one(p) for p in _candidates(lib_dir)]
    issues = []
    for r in records:
        for i in r["issues"]:
            issues.append(r["file"] + ": " + i)
            if len(issues) >= 10:
                break
        if len(issues) >= 10:
            break
    fails = [r for r in records if r["issues"]]
    return {
        "lib_dir": lib_dir,
        "scanned": len(records),
        "passed": len(records) - len(fails),
        "failed": len(fails),
        "records": records,
        "issues": issues,
        "needs_review": bool(fails),
    }


def action_scan() -> dict:
    lib_dir = _find_lib_dir()
    rows = []
    for p in _candidates(lib_dir):
        code, out = _run([p, "--manifest"])
        m = _last_json(out)
        if isinstance(m, dict) and m.get("ok") is True:
            rows.append({"file": os.path.basename(p),
                         "id": m.get("id"), "title": m.get("title")})
        else:
            rows.append({"file": os.path.basename(p), "id": None,
                         "title": None, "bad": True})
    return {
        "ok": True, "alert": False,
        "summary": "scan %d workers in %s" % (len(rows), lib_dir),
        "data": {"workers": rows},
        "action": "scan",
    }


def action_validate() -> dict:
    d = _build()
    return {
        "ok": True, "alert": bool(d["failed"]),
        "summary": "validate %d scanned, %d failed" % (d["scanned"], d["failed"]),
        "data": d,
        "action": "validate",
    }


def action_pack() -> dict:
    d = _build()
    return {
        "ok": True, "alert": bool(d["failed"]),
        "summary": "review pack: %d failure(s)" % d["failed"],
        "data": {"needs_review": bool(d["failed"]),
                 "failed": d["failed"], "issues": d["issues"]},
        "action": "pack",
    }


ACTIONS = {"scan": action_scan, "validate": action_validate, "pack": action_pack}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit({"ok": False, "alert": False, "reason": "no_flags", "hint": "--manifest"}, 2)
    if argv[1] == "--manifest":
        return emit(MANIFEST)
    if argv[1] == "--list-actions":
        return emit({"ok": True, "actions": MANIFEST["actions"],
                     "default_action": MANIFEST["default_action"]})
    if argv[1] == "--action":
        if len(argv) < 3:
            return emit({"ok": False, "reason": "missing_action"}, 2)
        fn = ACTIONS.get(argv[2])
        if fn is None:
            return emit({"ok": False, "reason": "unknown_action",
                         "action": argv[2]}, 2)
        return emit(fn())
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest"}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
