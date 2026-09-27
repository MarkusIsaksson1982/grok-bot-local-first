#!/usr/bin/env python3
"""schema-guard: light shape checks for tasks.json / sources.json / project.json."""
from __future__ import annotations

import json
import os
import sys
from typing import Any

MANIFEST = {
    "ok": True,
    "id": "schema-guard",
    "title": "Local Schema / Contract Guard",
    "source": "grok-4.7-high",
    "origin": {"grok_build": "1.0.41", "os": "windows-11"},
    "priority": 90,
    "keywords": ["validation", "schema", "contract", "quality"],
    "default_action": "collect",
    "verified_grok_bot": "0.61.0",
    "verified_at": "2026-09-27",
    "actions": [
        {"name": "collect", "use_bot": False, "summary": "Light-check known registry JSON shapes"},
        {"name": "violations", "use_bot": False, "summary": "First shape violations with file + issue"},
        {"name": "pack", "use_bot": True, "summary": "Tiny pack only on invalid known registry files"},
    ],
}

KNOWN_FILES = ("tasks.json", "sources.json", "project.json")


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


def parse_root(argv: list[str]) -> str:
    root = root_dir()
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            root = argv[i + 1]
            i += 2
            continue
        i += 1
    return os.path.abspath(root)


def strip_root(argv: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def cap_summary(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


def load_json(path: str) -> tuple[Any, str | None]:
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        return None, "unreadable"
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, "invalid json"


def check_tasks(obj: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(obj, dict):
        return ["not a JSON object"]
    tasks = obj.get("tasks")
    if tasks is None:
        issues.append("missing tasks")
    elif not isinstance(tasks, list):
        issues.append("tasks not list")
    else:
        for i, row in enumerate(tasks[:40]):
            if not isinstance(row, dict):
                issues.append("tasks[%d] not object" % i)
                continue
            if not isinstance(row.get("id"), str) or not row.get("id"):
                issues.append("tasks[%d] missing id" % i)
            local = row.get("local")
            if local is not None:
                if not isinstance(local, dict):
                    issues.append("tasks[%d].local not object" % i)
                elif local.get("script") is not None and not isinstance(local.get("script"), str):
                    issues.append("tasks[%d].local.script not string" % i)
    policy = obj.get("policy")
    if policy is not None and not isinstance(policy, dict):
        issues.append("policy not object")
    return issues[:12]


def check_sources(obj: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(obj, dict):
        return ["not a JSON object"]
    bot = obj.get("current_grok_bot")
    if bot is not None and not isinstance(bot, str):
        issues.append("current_grok_bot not string")
    sources = obj.get("sources")
    if sources is None:
        issues.append("missing sources")
    elif not isinstance(sources, list):
        issues.append("sources not list")
    else:
        for i, row in enumerate(sources[:40]):
            if not isinstance(row, dict):
                issues.append("sources[%d] not object" % i)
                continue
            if not isinstance(row.get("id"), str) or not row.get("id"):
                issues.append("sources[%d] missing id" % i)
    return issues[:12]


def check_project(obj: Any) -> tuple[list[str], bool]:
    """Return (issues, unverified). unverified is not a fail."""
    if not isinstance(obj, dict):
        return ["not a JSON object"], False
    knownish = any(k in obj for k in ("id", "name", "project", "artifacts", "budget", "context_budget", "watch"))
    if not knownish:
        return [], True
    issues: list[str] = []
    for key in ("id", "name"):
        if key in obj and obj[key] is not None and not isinstance(obj[key], str):
            issues.append("%s not string" % key)
    return issues[:12], False


def scan(root: str | None = None) -> dict[str, Any]:
    root = root or root_dir()
    valid = 0
    invalid = 0
    unverified = 0
    missing_required = 0
    violations: list[dict[str, Any]] = []

    for name in KNOWN_FILES:
        path = os.path.join(root, name)
        required = name in ("tasks.json", "sources.json")
        if not os.path.isfile(path):
            if required:
                missing_required += 1
                invalid += 1
                violations.append({"file": name, "issue": "missing"})
            else:
                unverified += 1
            continue
        obj, err = load_json(path)
        if err:
            invalid += 1
            violations.append({"file": name, "issue": err})
            continue
        if name == "tasks.json":
            issues = check_tasks(obj)
            if issues:
                invalid += 1
                for iss in issues[:6]:
                    violations.append({"file": name, "issue": iss})
            else:
                valid += 1
        elif name == "sources.json":
            issues = check_sources(obj)
            if issues:
                invalid += 1
                for iss in issues[:6]:
                    violations.append({"file": name, "issue": iss})
            else:
                valid += 1
        else:
            issues, uv = check_project(obj)
            if uv:
                unverified += 1
            elif issues:
                invalid += 1
                for iss in issues[:6]:
                    violations.append({"file": name, "issue": iss})
            else:
                valid += 1

    return {
        "valid": valid,
        "invalid": invalid,
        "unverified": unverified,
        "missing_required": missing_required,
        "violations": violations[:12],
        "violations_total": len(violations),
        "files_scanned": 3,
    }


def action_collect(root: str | None = None) -> dict[str, Any]:
    c = scan(root)
    alert = c["invalid"] > 0
    return {
        "ok": True,
        "alert": alert,
        "summary": cap_summary(
            "%d file(s): %d valid, %d invalid, %d unverified"
            % (c["files_scanned"], c["valid"], c["invalid"], c["unverified"])
        ),
        "data": {
            "counts": {
                "valid": c["valid"],
                "invalid": c["invalid"],
                "unverified": c["unverified"],
                "missing_required": c["missing_required"],
                "files_scanned": c["files_scanned"],
            }
        },
        "action": "collect",
    }


def action_violations(root: str | None = None) -> dict[str, Any]:
    c = scan(root)
    return {
        "ok": True,
        "alert": c["invalid"] > 0,
        "summary": cap_summary("%d schema violation(s)" % c["violations_total"]),
        "data": {
            "violations": c["violations"],
            "violations_total": c["violations_total"],
            "unverified": c["unverified"],
        },
        "action": "violations",
    }


def action_pack(root: str | None = None) -> dict[str, Any]:
    c = scan(root)
    if c["invalid"] <= 0:
        return {
            "ok": True,
            "alert": False,
            "summary": cap_summary("no judgment needed"),
            "data": {
                "emit": False,
                "needs_judgment": False,
                "unverified": c["unverified"],
            },
            "action": "pack",
        }
    return {
        "ok": True,
        "alert": True,
        "summary": cap_summary("%d violation(s) in registry JSON" % c["violations_total"]),
        "data": {
            "emit": True,
            "needs_judgment": True,
            "violations": c["violations"][:8],
            "violations_total": c["violations_total"],
        },
        "action": "pack",
    }


ACTIONS = {"collect": action_collect, "violations": action_violations, "pack": action_pack}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}, 2)
    root = parse_root(argv[1:])
    flags = strip_root(argv[1:])
    if not flags:
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}, 2)
    if flags[0] == "--manifest":
        return emit(MANIFEST)
    if flags[0] == "--list-actions":
        return emit(
            {"ok": True, "actions": MANIFEST["actions"], "default_action": MANIFEST["default_action"]}
        )
    if flags[0] == "--action":
        if len(flags) < 2:
            return emit({"ok": False, "reason": "missing_action", "alert": False}, 2)
        name = flags[1]
        fn = ACTIONS.get(name)
        if fn is None:
            return emit({"ok": False, "reason": "unknown_action", "action": name, "alert": False}, 2)
        return emit(fn(root))
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest", "alert": False}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
