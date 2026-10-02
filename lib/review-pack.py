#!/usr/bin/env python3
"""review-pack: progressive d1/d2/pack over <root>/lib and drop manifests. --root DIR."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

MANIFEST = {
    "ok": True,
    "id": "review-pack",
    "title": "Progressive Evidence Pack Builder",
    "source": "grok-4.7-high",
    "origin": {"grok_build": "1.0.41", "os": "windows-11"},
    "priority": 93,
    "keywords": ["progressive", "evidence", "review", "pack"],
    "default_action": "d1",
    "verified_grok_bot": "0.58.0",
    "verified_at": "2026-10-02",
    "actions": [
        {"name": "d1", "use_bot": False, "summary": "Minimal index of sibling worker manifests"},
        {"name": "d2", "use_bot": False, "summary": "Compact default-action summaries (skip meta executors)"},
        {"name": "pack", "use_bot": True, "summary": "Tiny pack only when a sibling flags alert"},
    ],
}

SCAN_DIRS = ("lib", "drop")
TIMEOUT = 15
# Mutual with worker-lint SKIP_ACTION_IDS. d1 still lists these ids.
# d2/pack must not --action-run them (lint/index/review-pack executor storm).
SKIP_RUN_IDS = {"review-pack", "worker-lint", "worker-index"}


def emit(obj: object, code: int = 0) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    return code



def _lane_aware_root(script_dir: str) -> str | None:
    """If worker lives in lib/ or lib/<lane>/, return the kit root."""
    base = os.path.basename(script_dir).lower()
    parent = os.path.dirname(script_dir)
    if base == "lib":
        return parent
    if os.path.basename(parent).lower() == "lib":
        return os.path.dirname(parent)
    return None

def root_dir() -> str:
    here = os.path.abspath(__file__)
    script_dir = os.path.dirname(here)
    lane_root = _lane_aware_root(script_dir)
    if lane_root:
        return lane_root
    cur = script_dir
    for _ in range(6):
        if os.path.isfile(os.path.join(cur, "tasks.json")) or os.path.isfile(
            os.path.join(cur, "sources.json")
        ):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    env = os.environ.get("GROKKIT_ROOT")
    if env:
        return os.path.abspath(env)
    return script_dir


def cap_summary(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


def last_json_line(text: str) -> Any | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def parse_root(argv: list[str]) -> str:
    """Match secrets-scan: optional --root DIR, else walked kit root."""
    root = root_dir()
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            root = argv[i + 1]
            i += 2
            continue
        i += 1
    return os.path.abspath(root)


def list_candidates(root: str) -> list[str]:
    selfp = os.path.abspath(__file__)
    out: list[str] = []
    for folder in SCAN_DIRS:
        d = os.path.join(root, folder)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in sorted(names):
            if not name.lower().endswith(".py"):
                continue
            if name.startswith("_") or name.startswith("."):
                continue
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            if os.path.abspath(path) == selfp:
                continue
            out.append(path)
    return out


def run_worker(py_path: str, *args: str) -> dict[str, Any] | None:
    try:
        proc = subprocess.run(
            [sys.executable, py_path, *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=os.path.dirname(py_path) or root_dir(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    obj = last_json_line(proc.stdout or "")
    if not isinstance(obj, dict):
        return None
    return obj


def sibling_manifests(root: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in list_candidates(root):
        man = run_worker(path, "--manifest")
        if not isinstance(man, dict) or man.get("ok") is not True:
            continue
        wid = man.get("id")
        if not isinstance(wid, str) or not wid:
            wid = os.path.splitext(os.path.basename(path))[0]
        out.append(
            {
                "path": path,
                "id": wid,
                "title": man.get("title") if isinstance(man.get("title"), str) else "",
                "default_action": man.get("default_action") if isinstance(man.get("default_action"), str) else "collect",
                "priority": man.get("priority"),
            }
        )
    return out


def action_d1(argv: list[str]) -> dict[str, Any]:
    manifests = sibling_manifests(parse_root(argv))
    idx = [{"id": m["id"], "title": m["title"], "default_action": m["default_action"]} for m in manifests[:12]]
    return {
        "ok": True,
        "alert": False,
        "summary": cap_summary("%d sibling worker(s) available for evidence" % len(manifests)),
        "data": {"evidence": idx, "evidence_total": len(manifests), "truncated": len(manifests) > 12},
        "action": "d1",
    }


def gather_summaries(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for m in manifests:
        wid = m["id"]
        act = m.get("default_action") or "collect"
        if wid in SKIP_RUN_IDS:
            summaries.append({"id": wid, "action": act, "summary": "skipped meta executor", "alert": False})
            continue
        res = run_worker(m["path"], "--action", act)
        if isinstance(res, dict):
            raw = res.get("summary", "")
            summaries.append(
                {
                    "id": wid,
                    "action": act,
                    "summary": cap_summary(raw if isinstance(raw, str) else "", 80),
                    "alert": bool(res.get("alert")),
                }
            )
        else:
            summaries.append({"id": wid, "action": act, "summary": "no json", "alert": False})
    return summaries


def action_d2(argv: list[str]) -> dict[str, Any]:
    manifests = sibling_manifests(parse_root(argv))
    summaries = gather_summaries(manifests)
    flagged = [x["id"] for x in summaries if x["alert"]]
    return {
        "ok": True,
        "alert": False,
        "summary": cap_summary("%d sibling summary(ies) assembled" % len(summaries)),
        "data": {
            "summaries": summaries[:12],
            "summaries_total": len(summaries),
            "flagged_total": len(flagged),
            "truncated": len(summaries) > 12,
        },
        "action": "d2",
    }


def action_pack(argv: list[str]) -> dict[str, Any]:
    manifests = sibling_manifests(parse_root(argv))
    summaries = gather_summaries(manifests)
    flagged = [x["id"] for x in summaries if x["alert"]]
    if not flagged:
        return {
            "ok": True,
            "alert": False,
            "summary": cap_summary("no judgment needed"),
            "data": {"emit": False, "needs_judgment": False, "siblings": len(summaries)},
            "action": "pack",
        }
    return {
        "ok": True,
        "alert": True,
        "summary": cap_summary("%d sibling(s) flagged for judgment" % len(flagged)),
        "data": {
            "emit": True,
            "needs_judgment": True,
            "flagged": flagged[:12],
            "flagged_total": len(flagged),
        },
        "action": "pack",
    }


ACTIONS = {"d1": action_d1, "d2": action_d2, "pack": action_pack}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}, 2)
    primary = None
    i = 1
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            i += 2
            continue
        primary = argv[i]
        break
    if primary is None:
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}, 2)
    if primary == "--manifest":
        return emit(MANIFEST)
    if primary == "--list-actions":
        return emit(
            {"ok": True, "actions": MANIFEST["actions"], "default_action": MANIFEST["default_action"]}
        )
    if primary == "--action":
        name = None
        try:
            idx = argv.index("--action")
        except ValueError:
            idx = -1
        if idx >= 0:
            j = idx + 1
            while j < len(argv):
                if argv[j] == "--root" and j + 1 < len(argv):
                    j += 2
                    continue
                name = argv[j]
                break
        if not name:
            return emit({"ok": False, "reason": "missing_action", "alert": False}, 2)
        fn = ACTIONS.get(name)
        if fn is None:
            return emit({"ok": False, "reason": "unknown_action", "action": name, "alert": False}, 2)
        return emit(fn(argv))
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest", "alert": False}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
