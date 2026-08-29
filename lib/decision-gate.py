#!/usr/bin/env python3
"""decision-gate -- deterministic threshold/escalation gate for Grok Bot.

Offline, read-only, stdlib-only. Answers the pre-flight question "should the
bot look at this at all?" by scoring cheap local signals against a weighted
threshold and emitting a single verdict. Purely deterministic (use_bot:false).

Usage:
  python decision-gate.py --manifest
  python decision-gate.py --list-actions
  python decision-gate.py --action gate [--dir PATH] [--config PATH]

Optional config (default: <dir>/gate.json):
{
  "threshold": 50,
  "gates": [
    {"name": "blocked", "kind": "marker_glob", "glob": "*BLOCK*,*STUCK*", "weight": 90},
    {"name": "stale",   "kind": "max_age_days", "pattern": ".", "days": 30, "weight": 30},
    {"name": "todos",   "kind": "regex_count", "pattern": "**/*.py,**/*.md",
     "regex": "TODO|FIXME", "min_count": 5, "weight": 10}
  ]
}
When no config exists, conservative built-in gates are used. Lists in data are
capped at 5 entries plus a total count.
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
SOURCE = "local"
KEYWORDS = ["gate", "threshold", "escalation", "prefilter", "priority"]
DEFAULT_ACTION = "gate"
USE_BOT = False
PRIORITY = 85
SCAN_CAP = 4000

ACTIONS = {
    "gate": "Evaluate all configured signals; return a single verdict (default).",
    "scores": "Per-signal weight/trigger/value breakdown.",
    "signals": "Document the supported signal kinds used in a config.",
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
        "pattern": ".",
        "days": 30,
        "weight": 30,
    },
]
BUILTIN_THRESHOLD = 50.0

SIGNAL_KINDS = [
    {"kind": "marker_glob", "keys": "glob, weight",
     "desc": "triggers when any path matches a comma-separated glob"},
    {"kind": "max_age_days", "keys": "pattern, days, weight",
     "desc": "triggers when the newest file under pattern is older than days"},
    {"kind": "count_glob", "keys": "glob, min_count, weight",
     "desc": "triggers when the number of matching files reaches min_count"},
    {"kind": "regex_count", "keys": "pattern, regex, min_count, weight",
     "desc": "triggers when matching lines across files reach min_count"},
]


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _abort(_summary: str, action: str = "abort") -> dict:
    return {"ok": False, "alert": True, "summary": _summary,
            "data": {"error": _summary}, "action": action}


def _manifest() -> dict:
    return {
        "ok": True,
        "id": ID,
        "title": TITLE,
        "source": SOURCE,
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
        "actions": [{"name": n, "use_bot": USE_BOT, "summary": ACTIONS[n]}
                    for n in sorted(ACTIONS)],
    }


def _find(base: Path, pattern: str, cap: int) -> list[Path]:
    """Files under base matching pattern (pathlib glob semantics)."""
    if not base.is_dir():
        return []
    pat = pattern.strip()
    if pat in (".", "./"):
        pat = "**"
    elif pat.startswith("./"):
        pat = pat[2:]
    if ".." in pat:
        return []
    try:
        iterator = base.glob(pat)
    except (re.error, ValueError):
        return []
    out: list[Path] = []
    for p in iterator:
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
    res = {"name": name, "kind": kind, "weight": weight,
           "triggered": False, "value": 0, "note": ""}

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
    return res


def _gates(config: dict) -> list[dict]:
    gates = config.get("gates") if isinstance(config, dict) else None
    return gates if isinstance(gates, list) and gates else BUILTIN_GATES


def run_gate(base: Path, config: dict) -> dict:
    gates = _gates(config)
    threshold = float(config.get("threshold", BUILTIN_THRESHOLD))
    results = [_signal(base, s) for s in gates]
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
    summary = f"score {score:.0f}/{threshold:.0f} -> {verdict}"
    if names:
        summary += f" ({', '.join(names[:3])})"
    return {"ok": True, "alert": verdict == "escalate", "summary": summary,
            "data": data, "action": "gate"}


def run_scores(base: Path, config: dict) -> dict:
    gates = _gates(config)
    threshold = float(config.get("threshold", BUILTIN_THRESHOLD))
    results = [_signal(base, s) for s in gates]
    rows = [
        {"name": r["name"], "kind": r["kind"], "weight": r["weight"],
         "triggered": r["triggered"], "value": r["value"]}
        for r in results
    ]
    trigg = sum(1 for r in rows if r["triggered"])
    data = {"threshold": threshold, "type": "signal", "total": len(rows),
            "triggered_total": trigg, "rows": rows[:5], "truncated": len(rows) > 5}
    return {"ok": True, "alert": bool(trigg), "summary": f"{trigg} of {len(rows)} signals triggered",
            "data": data, "action": "scores"}


def run_signals() -> dict:
    data = {"type": "signal-kind", "total": len(SIGNAL_KINDS), "kinds": SIGNAL_KINDS}
    return {"ok": True, "alert": False, "summary": f"signal kinds: {len(SIGNAL_KINDS)}",
            "data": data, "action": "signals"}


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
    ap = argparse.ArgumentParser(prog=ID, description=TITLE)
    ap.add_argument("--manifest", action="store_true", help="Print the worker manifest (last-line JSON).")
    ap.add_argument("--list-actions", action="store_true", help="Print available actions.")
    ap.add_argument("--action", choices=sorted(ACTIONS), default=None,
                    help=f"Run an action (default: {DEFAULT_ACTION}).")
    ap.add_argument("--dir", default=".", help="Base directory to inspect (read-only).")
    ap.add_argument("--config", default=None, help="Path to gate.json config; defaults to <dir>/gate.json.")
    args = ap.parse_args(argv)

    action = args.action or DEFAULT_ACTION
    base = Path(args.dir)
    if not base.is_dir():
        _emit(_abort(f"not a directory: {args.dir}", action))
        return 2

    if args.manifest:
        _emit(_manifest())
    elif args.list_actions:
        _emit(_list_actions())
    else:
        config, warn = _load_config(base, args.config)
        if warn:
            _emit(_abort(warn, action))
            return 1
        if action == "gate":
            _emit(run_gate(base, config))
        elif action == "scores":
            _emit(run_scores(base, config))
        elif action == "signals":
            _emit(run_signals())
    return 0


if __name__ == "__main__":
    sys.exit(main())
