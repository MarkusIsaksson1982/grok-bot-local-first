#!/usr/bin/env python3
"""context-budget: token estimate + subset select for candidate paths."""
from __future__ import annotations

import json
import os
import sys
from typing import Any

MANIFEST = {
    "ok": True,
    "id": "context-budget",
    "title": "Context Budget Estimator",
    "source": "grok-4.7-high",
    "origin": {"grok_build": "1.0.41", "os": "windows-11"},
    "priority": 85,
    "keywords": ["context", "budget", "triage", "tokens"],
    "default_action": "collect",
    "verified_grok_bot": "0.61.0",
    "verified_at": "2026-09-27",
    "actions": [
        {"name": "collect", "use_bot": False, "summary": "Total size + rough token estimate of candidates"},
        {"name": "select", "use_bot": False, "summary": "High-value subset that fits under budget"},
        {"name": "pack", "use_bot": True, "summary": "Tiny pack only if over budget and nothing fits"},
    ],
}

DEFAULT_BUDGET = 20000
SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "dist",
    "build",
    ".idea",
    ".vscode",
    "target",
    "findings",
}
EXT_PRIORITY = {
    ".md": 3,
    ".py": 3,
    ".txt": 2,
    ".json": 2,
    ".yaml": 2,
    ".yml": 2,
    ".toml": 1,
    ".cfg": 1,
    ".ini": 1,
    ".log": 1,
    ".csv": 1,
    ".xml": 1,
    ".html": 1,
    ".css": 1,
}
MAX_FILE = 5_000_000
CANDIDATE_DIRS = ("lib", "drop", "prompts", ".")


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


def load_budget() -> int:
    env = os.environ.get("GROKKIT_CONTEXT_BUDGET") or os.environ.get("CONTEXT_BUDGET")
    if env:
        try:
            n = int(env.strip())
            if n > 0:
                return max(1000, n)
        except ValueError:
            pass
    root = root_dir()
    for name in ("context-budget.json", "project.json"):
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        raw = obj.get("context_budget", obj.get("budget"))
        if isinstance(raw, dict):
            raw = raw.get("tokens") or raw.get("budget")
        try:
            n = int(raw)
            if n > 0:
                return max(1000, n)
        except (TypeError, ValueError):
            continue
    return DEFAULT_BUDGET


def list_files(root: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    seen: set[str] = set()

    def consider(dirpath: str, fn: str) -> None:
        fp = os.path.join(dirpath, fn)
        try:
            if not os.path.isfile(fp):
                return
            sz = os.path.getsize(fp)
        except OSError:
            return
        if sz > MAX_FILE:
            return
        try:
            rel = os.path.relpath(fp, root).replace("\\", "/")
        except ValueError:
            rel = fn
        if rel in seen:
            return
        seen.add(rel)
        ext = os.path.splitext(fn)[1].lower()
        out.append((rel, sz, EXT_PRIORITY.get(ext, 1)))

    for folder in CANDIDATE_DIRS:
        if folder == ".":
            try:
                for fn in os.listdir(root):
                    if fn.startswith("."):
                        continue
                    consider(root, fn)
            except OSError:
                continue
            continue
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            continue
        for dirpath, dirs, fnames in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in fnames:
                if fn.startswith("."):
                    continue
                consider(dirpath, fn)
    return out


def select(files: list[tuple[str, int, int]], budget: int) -> tuple[list[str], int]:
    ordered = sorted(files, key=lambda x: (-x[2], x[1]))
    sel: list[str] = []
    used = 0
    for path, sz, _pr in ordered:
        t = max(1, sz // 4) if sz else 0
        if t <= 0:
            continue
        if used + t > budget:
            continue
        sel.append(path)
        used += t
        if used >= budget:
            break
    return sel, used


def classify(root: str | None = None, budget: int | None = None) -> dict[str, Any]:
    root = root or root_dir()
    if budget is None:
        budget = load_budget()
    files = list_files(root)
    total_bytes = sum(s for _, s, _ in files)
    est_tokens = total_bytes // 4
    sel, used = select(files, budget)
    over = est_tokens > budget
    cannot_fit = over and len(files) > 0 and used == 0
    top = sorted(files, key=lambda x: -x[1])[:8]
    return {
        "budget": budget,
        "files": len(files),
        "total_bytes": total_bytes,
        "est_tokens": est_tokens,
        "selected": sel,
        "used_tokens": used,
        "over": over,
        "cannot_fit": cannot_fit,
        "top": [{"path": p, "bytes": s, "est_tokens": s // 4} for p, s, _ in top],
    }


def action_collect(root: str | None = None, budget: int | None = None) -> dict[str, Any]:
    c = classify(root, budget)
    alert = bool(c["cannot_fit"])
    return {
        "ok": True,
        "alert": alert,
        "summary": cap_summary(
            "%d file(s); ~%d tokens (budget %d); selected ~%d"
            % (c["files"], c["est_tokens"], c["budget"], c["used_tokens"])
        ),
        "data": {
            "counts": {
                "files": c["files"],
                "total_bytes": c["total_bytes"],
                "est_tokens": c["est_tokens"],
                "budget": c["budget"],
                "used_tokens": c["used_tokens"],
                "selected_count": len(c["selected"]),
            },
            "top_files": c["top"][:5],
            "cannot_fit": c["cannot_fit"],
        },
        "action": "collect",
    }


def action_select(root: str | None = None, budget: int | None = None) -> dict[str, Any]:
    c = classify(root, budget)
    alert = bool(c["cannot_fit"])
    sample = c["selected"][:12]
    return {
        "ok": True,
        "alert": alert,
        "summary": cap_summary(
            "selected %d path(s) ~%d tokens under budget %d"
            % (len(c["selected"]), c["used_tokens"], c["budget"])
        ),
        "data": {
            "selected": sample,
            "selected_total": len(c["selected"]),
            "used_tokens": c["used_tokens"],
            "budget": c["budget"],
            "est_tokens": c["est_tokens"],
            "cannot_fit": c["cannot_fit"],
            "truncated": len(c["selected"]) > 12,
        },
        "action": "select",
    }


def action_pack(root: str | None = None, budget: int | None = None) -> dict[str, Any]:
    c = classify(root, budget)
    if not c["cannot_fit"]:
        return {
            "ok": True,
            "alert": False,
            "summary": cap_summary("no judgment needed"),
            "data": {
                "emit": False,
                "needs_judgment": False,
                "est_tokens": c["est_tokens"],
                "budget": c["budget"],
            },
            "action": "pack",
        }
    return {
        "ok": True,
        "alert": True,
        "summary": cap_summary(
            "evidence ~%d tokens exceeds budget %d and selection cannot fit" % (c["est_tokens"], c["budget"])
        ),
        "data": {
            "emit": True,
            "needs_judgment": True,
            "est_tokens": c["est_tokens"],
            "budget": c["budget"],
            "files": c["files"],
            "cannot_fit": True,
        },
        "action": "pack",
    }


ACTIONS = {"collect": action_collect, "select": action_select, "pack": action_pack}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}, 2)
    mode: str | None = None
    action: str | None = None
    root: str | None = None
    budget: int | None = None
    i = 1
    while i < len(argv):
        flag = argv[i]
        if flag == "--manifest":
            mode = "manifest"
        elif flag == "--list-actions":
            mode = "list"
        elif flag == "--action":
            if i + 1 >= len(argv):
                return emit({"ok": False, "reason": "missing_action", "alert": False}, 2)
            mode = "action"
            action = argv[i + 1]
            i += 1
        elif flag == "--root":
            if i + 1 >= len(argv):
                return emit({"ok": False, "reason": "missing_root", "alert": False}, 2)
            root = argv[i + 1]
            i += 1
        elif flag == "--budget":
            if i + 1 >= len(argv):
                return emit({"ok": False, "reason": "missing_budget", "alert": False}, 2)
            try:
                budget = int(argv[i + 1])
            except ValueError:
                return emit({"ok": False, "reason": "bad_budget", "alert": False}, 2)
            if budget <= 0:
                return emit({"ok": False, "reason": "bad_budget", "alert": False}, 2)
            i += 1
        else:
            return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest", "alert": False}, 2)
        i += 1
    if mode == "manifest":
        return emit(MANIFEST)
    if mode == "list":
        return emit(
            {"ok": True, "actions": MANIFEST["actions"], "default_action": MANIFEST["default_action"]}
        )
    if mode == "action":
        fn = ACTIONS.get(action or "")
        if fn is None:
            return emit({"ok": False, "reason": "unknown_action", "action": action, "alert": False}, 2)
        if root is not None and not os.path.isdir(root):
            return emit({"ok": False, "reason": "bad_root", "alert": False}, 2)
        return emit(fn(root, budget))
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest", "alert": False}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
