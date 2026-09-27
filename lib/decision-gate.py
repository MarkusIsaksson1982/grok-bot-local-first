#!/usr/bin/env python3
"""decision-gate -- deterministic threshold/escalation gate for Grok Bot.

Offline, read-only, stdlib-only. Answers the pre-flight question "should the
bot look at this at all?" by scoring cheap local signals against a weighted
threshold and emitting a single verdict. Purely deterministic (use_bot:false).

Paths accept / and \\. Unknown signal kinds and empty matches return a compact
schema hint so the gate list can be expanded without opening this file.

Usage:
  python decision-gate.py --manifest
  python decision-gate.py --list-actions
  python decision-gate.py --action gate [--dir PATH] [--config PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ID = "decision-gate"
TITLE = "Threshold/escalation gate: whether the bot should engage at all"
VERSION = "1.0.0"
SOURCE = "grok-4.7-high"
KEYWORDS = ["gate", "threshold", "escalation", "prefilter", "priority"]
DEFAULT_ACTION = "gate"
USE_BOT = False
PRIORITY = 85
SCAN_CAP = 4000

ACTIONS = {
    "gate": "Evaluate all configured signals; return a single verdict (default).",
    "scores": "Per-signal weight/trigger/value breakdown.",
    "signals": "Document the supported signal kinds used in a config.",
    "schema": "Config and path-pattern hint for expanding gates.",
}

BUILTIN_GATES = [
    {
        "name": "blocked-marker",
        "kind": "marker_glob",
        "glob": "*BLOCK*,*STUCK*,*FAIL*,*.blocked",
        "weight": 90,
    },
    {
        "name": "stale-tree",
        "kind": "max_age_days",
        # Top-level names only. Recursive "." exceeds worker-lint's action cap.
        "pattern": "*",
        "days": 30,
        "weight": 30,
    },
]
BUILTIN_THRESHOLD = 50.0

SIGNAL_KINDS = [
    {
        "kind": "marker_glob",
        "keys": "glob, weight",
        "desc": "triggers when any path matches a comma-separated glob",
    },
    {
        "kind": "max_age_days",
        "keys": "pattern, days, weight",
        "desc": "triggers when the newest file under pattern is older than days",
    },
    {
        "kind": "count_glob",
        "keys": "glob, min_count, weight",
        "desc": "triggers when the number of matching files reaches min_count",
    },
    {
        "kind": "regex_count",
        "keys": "pattern, regex, min_count, weight",
        "desc": "triggers when matching lines across files reach min_count",
    },
]

HINT_TREE = {
    "config_file": "gate.json (object with threshold and gates: list)",
    "gate_keys": ["name", "kind", "weight", "glob", "pattern", "days", "regex", "min_count"],
    "kinds": [k["kind"] for k in SIGNAL_KINDS],
    "path_forms": [
        "relative file or dir",
        "glob: **\\*.py",
        "comma-separated globs: *.blocked,*.FAIL",
        "tree root: .",
    ],
    "suggested_roots": [
        ".",
        "drop",
        "lib",
        "out",
        "logs",
        "Desktop",
        "Documents",
        "Downloads",
    ],
    "notes": (
        "paths are relative to --dir. Globs accept / or \\. "
        "'..' segments are rejected. Unknown kind is a non-trigger with a hint."
    ),
}


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _abort(_summary: str, action: str = "abort") -> dict:
    return {
        "ok": False,
        "alert": True,
        "summary": _summary,
        "data": {"error": _summary, "hint": HINT_TREE},
        "action": action,
    }


def _bare() -> dict:
    return {"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}


def _manifest() -> dict:
    return {
        "verified_at": "2026-09-27",
        "verified_grok_bot": "0.61.0",
        "ok": True,
        "id": ID,
        "title": TITLE,
        "source": SOURCE,
        "origin": {"grok_build": "1.0.41", "os": "windows-11"},
        "priority": PRIORITY,
        "keywords": KEYWORDS,
        "default_action": DEFAULT_ACTION,
        "actions": [
            {"name": n, "use_bot": USE_BOT, "summary": ACTIONS[n]}
            for n in sorted(ACTIONS)
        ],
    }


def _list_actions() -> dict:
    return {
        "ok": True,
        "id": ID,
        "default_action": DEFAULT_ACTION,
        "actions": [
            {"name": n, "use_bot": USE_BOT, "summary": ACTIONS[n]}
            for n in sorted(ACTIONS)
        ],
    }


def _norm_pat(pattern: str) -> str:
    return pattern.strip().replace("\\", "/")


def _find(base: Path, pattern: str, cap: int) -> list[Path]:
    if not base.is_dir():
        return []
    pat = _norm_pat(pattern)
    if pat in (".", "./"):
        pat = "**/*"
    elif pat.startswith("./"):
        pat = pat[2:]
    if ".." in pat.split("/"):
        return []
    try:
        iterator = base.glob(pat)
    except (re.error, ValueError, OSError):
        return []
    # Stop early so a recursive pattern cannot burn worker-lint's 3s action cap.
    deadline = time.monotonic() + 1.0
    out: list[Path] = []
    for p in iterator:
        if time.monotonic() >= deadline:
            break
        if not p.is_file():
            continue
        out.append(p)
        if len(out) >= cap:
            break
    return out


def _signal(base: Path, sig: dict) -> dict:
    kind = sig.get("kind")
    name = str(sig.get("name") or kind)
    weight = int(sig.get("weight", 10))
    res = {
        "name": name,
        "kind": kind,
        "weight": weight,
        "triggered": False,
        "value": 0,
        "note": "",
    }

    if kind == "marker_glob":
        value = 0
        hits: list[str] = []
        for pat in str(sig.get("glob", "*")).split(","):
            found = _find(base, pat, SCAN_CAP)
            if found:
                hits = [p.relative_to(base).as_posix() for p in found[:3]]
                value = len(found)
                break
        res["value"] = value
        res["triggered"] = value >= 1
        res["note"] = ";".join(hits)
    elif kind == "max_age_days":
        days = float(sig.get("days", 7))
        files = _find(base, str(sig.get("pattern", ".")), SCAN_CAP)
        if files:
            age_days = (time.time() - max(f.stat().st_mtime for f in files)) / 86400.0
            res["value"] = round(age_days, 1)
            res["triggered"] = age_days > days
            res["note"] = f"newest is {age_days:.1f}d old (limit {days:g}d)"
        else:
            res["note"] = "no files matched"
            res["hint"] = HINT_TREE
    elif kind == "count_glob":
        min_count = int(sig.get("min_count", 1))
        res["value"] = len(_find(base, str(sig.get("glob", "*")), SCAN_CAP))
        res["triggered"] = res["value"] >= min_count
    elif kind == "regex_count":
        min_count = int(sig.get("min_count", 1))
        try:
            rx = re.compile(str(sig.get("regex", "")))
        except re.error as exc:
            res["note"] = f"bad regex: {exc}"
            res["hint"] = HINT_TREE
            return res
        total = 0
        for pat in str(sig.get("pattern", "**/*.txt")).split(","):
            for p in _find(base, pat, SCAN_CAP):
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                total += sum(1 for line in text.splitlines() if rx.search(line))
        res["value"] = total
        res["triggered"] = total >= min_count
    else:
        res["note"] = f"unsupported signal kind: {kind!r}"
        res["hint"] = HINT_TREE
    return res


def _gates(config: dict) -> list[dict]:
    gates = config.get("gates") if isinstance(config, dict) else None
    return gates if isinstance(gates, list) and gates else BUILTIN_GATES


def run_gate(base: Path, config: dict) -> dict:
    gates = _gates(config)
    threshold = float(config.get("threshold", BUILTIN_THRESHOLD))
    results = [_signal(base, s) if isinstance(s, dict) else
               _signal(base, {"kind": "marker_glob", "glob": str(s)}) for s in gates]
    triggered = [r for r in results if r["triggered"]]
    score = min(threshold, sum(r["weight"] for r in triggered))
    verdict = "escalate" if score >= threshold else "hold"
    names = [r["name"] for r in triggered]
    data = {
        "score": score,
        "threshold": threshold,
        "severity": "high" if verdict == "escalate" else "ok",
        "verdict": verdict,
        "gates": len(results),
        "triggered_total": len(names),
        "triggered": names[:5],
        "truncated": len(names) > 5,
    }
    if any("hint" in r for r in results):
        data["hint"] = HINT_TREE
    summary = f"score {score:.0f}/{threshold:.0f} -> {verdict}"
    if names:
        summary += f" ({', '.join(names[:3])})"
    return {
        "ok": True,
        "alert": verdict == "escalate",
        "summary": summary,
        "data": data,
        "action": "gate",
    }


def run_scores(base: Path, config: dict) -> dict:
    gates = _gates(config)
    threshold = float(config.get("threshold", BUILTIN_THRESHOLD))
    results = [_signal(base, s) if isinstance(s, dict) else
               _signal(base, {"kind": "marker_glob", "glob": str(s)}) for s in gates]
    rows = [
        {
            "name": r["name"],
            "kind": r["kind"],
            "weight": r["weight"],
            "triggered": r["triggered"],
            "value": r["value"],
        }
        for r in results
    ]
    trigg = sum(1 for r in rows if r["triggered"])
    data = {
        "threshold": threshold,
        "type": "signal",
        "total": len(rows),
        "triggered_total": trigg,
        "rows": rows[:5],
        "truncated": len(rows) > 5,
        "hint": HINT_TREE,
    }
    return {
        "ok": True,
        "alert": bool(trigg),
        "summary": f"{trigg} of {len(rows)} signals triggered",
        "data": data,
        "action": "scores",
    }


def run_signals() -> dict:
    data = {
        "type": "signal-kind",
        "total": len(SIGNAL_KINDS),
        "kinds": SIGNAL_KINDS,
        "hint": HINT_TREE,
    }
    return {
        "ok": True,
        "alert": False,
        "summary": f"signal kinds: {len(SIGNAL_KINDS)}",
        "data": data,
        "action": "signals",
    }


def run_schema() -> dict:
    return {
        "ok": True,
        "alert": False,
        "summary": "decision-gate schema / expansion hint",
        "data": {"type": "decision-schema", "hint": HINT_TREE, "kinds": SIGNAL_KINDS},
        "action": "schema",
    }


def _load_config(base_dir: Path, config_arg: str | None) -> tuple[dict, str]:
    p = Path(config_arg) if config_arg else Path(base_dir) / "gate.json"
    if not p.is_file():
        return {}, ""
    text = p.read_text(encoding="utf-8-sig")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, f"config {p} is not valid JSON: {exc}"
    if not isinstance(raw, dict):
        return {}, f"config {p} must be a JSON object"
    return raw, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=ID, description=TITLE, add_help=True)
    ap.add_argument("--manifest", action="store_true", help="Print the worker manifest (last-line JSON).")
    ap.add_argument("--list-actions", action="store_true", help="Print available actions.")
    ap.add_argument(
        "--action",
        choices=sorted(ACTIONS),
        default=None,
        help=f"Run an action (explicit --action required; preferred: {DEFAULT_ACTION}).",
    )
    ap.add_argument("--dir", default=".", help="Base directory to inspect (read-only).")
    ap.add_argument("--config", default=None, help="Path to gate.json; defaults to <dir>/gate.json.")
    args_in = sys.argv[1:] if argv is None else argv
    if not args_in:
        _emit(_bare())
        return 2
    args = ap.parse_args(args_in)

    if not (args.manifest or args.list_actions or args.action):
        _emit(_abort("pass --manifest", "abort"))
        return 2

    if args.manifest:
        _emit(_manifest())
        return 0
    if args.list_actions:
        _emit(_list_actions())
        return 0

    action = args.action
    if action == "signals":
        _emit(run_signals())
        return 0
    if action == "schema":
        _emit(run_schema())
        return 0

    base = Path(args.dir)
    if not base.is_dir():
        _emit(_abort(f"not a directory: {args.dir}", action))
        return 2

    config, warn = _load_config(base, args.config)
    if warn:
        _emit(_abort(warn, action))
        return 1
    if action == "gate":
        _emit(run_gate(base, config))
    elif action == "scores":
        _emit(run_scores(base, config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
