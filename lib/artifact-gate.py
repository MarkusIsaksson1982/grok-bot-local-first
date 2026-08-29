#!/usr/bin/env python3
"""artifact-gate -- expected-deliverable existence and property checks.

Offline, read-only, stdlib-only. Verifies that expected deliverables exist and
satisfy basic properties (size, non-empty, freshness, header marker) so the bot
is not invoked to review work that was never produced. Deterministic
(use_bot:false). Never creates, modifies, or deletes anything.

Usage:
  python artifact-gate.py --manifest
  python artifact-gate.py --list-actions
  python artifact-gate.py --action verify [--dir PATH] [--config PATH]

Optional config (default: <dir>/artifacts.json):
{
  "artifacts": [
    {"path": "dist/app.zip", "min_bytes": 1024, "needle": "PK\\u0003\\u0004"},
    {"path": "docs/**/*.md", "min_files": 2},
    {"path": "coverage.txt", "nonempty": true, "max_age_days": 2}
  ]
}
A "path" containing *, ? or [ is treated as a glob. "needle" is searched in the
first 64 KiB of an exact single-file artifact only. Lists in data are capped at
5 entries plus a total count.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ID = "artifact-gate"
TITLE = "Expected-deliverable existence and property checks"
VERSION = "1.0.0"
SOURCE = "local"
KEYWORDS = ["artifact", "deliverable", "existence", "verification", "build"]
DEFAULT_ACTION = "verify"
USE_BOT = False
PRIORITY = 60
SCAN_CAP = 4000
HEAD_BYTES = 65536

ACTIONS = {
    "verify": "Check every configured artifact; report failing items (default).",
    "inspect": "List the configured artifacts and the checks applied to them.",
}


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


def _find_glob(base: Path, pattern: str) -> list[Path]:
    pat = pattern.strip()
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
        if len(out) >= SCAN_CAP:
            break
    return out


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _inspect_artifact(base: Path, spec: dict) -> dict:
    path = str(spec.get("path") or "")
    is_glob = any(ch in path for ch in "*?[")

    if is_glob:
        matches = _find_glob(base, path)
        min_files = int(spec.get("min_files", 1))
        if len(matches) < min_files:
            return {"path": path, "status": "missing",
                    "reason": f"{len(matches)}/{min_files} matches"}
        total = sum(_size(f) for f in matches)
        if spec.get("nonempty") and total == 0:
            return {"path": path, "status": "empty", "reason": "all matches are empty"}
        if int(spec.get("min_bytes", 0)) > total:
            return {"path": path, "status": "too-small", "reason": f"{total}<{spec['min_bytes']}b"}
        return {"path": path, "status": "ok", "matches": len(matches), "bytes": total}

    p = Path(base) / path
    if not p.exists():
        return {"path": path, "status": "missing", "reason": "not found"}
    files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
    if not files:
        return {"path": path, "status": "missing", "reason": "directory has no files"}

    total = sum(_size(f) for f in files)
    if spec.get("nonempty") and total == 0:
        return {"path": path, "status": "empty", "reason": "artifact is empty"}
    if int(spec.get("min_bytes", 0)) > total:
        return {"path": path, "status": "too-small", "reason": f"{total}<{spec['min_bytes']}b"}
    if "max_age_days" in spec:
        newest = max(f.stat().st_mtime for f in files)
        age_days = (time.time() - newest) / 86400.0
        if age_days > float(spec["max_age_days"]):
            return {"path": path, "status": "stale",
                    "reason": f"{age_days:.1f}d>{float(spec['max_age_days']):g}d"}
    if "needle" in spec and p.is_file():
        try:
            with open(p, "rb") as fh:
                head = fh.read(HEAD_BYTES)
        except OSError as exc:
            return {"path": path, "status": "unreadable", "reason": str(exc)}
        if spec["needle"].encode("utf-8") not in head:
            return {"path": path, "status": "needle-missing",
                    "reason": f"head lacks marker {spec['needle']!r}"}
    return {"path": path, "status": "ok", "matches": len(files), "bytes": total}


def run_verify(base: Path, config: dict) -> dict:
    specs = config.get("artifacts") if isinstance(config, dict) else None
    if not isinstance(specs, list) or not specs:
        return _abort("no artifacts configured (set --config or add artifacts.json)", "verify")
    results = [_inspect_artifact(base, s) for s in specs]
    failing = [r for r in results if r["status"] != "ok"]
    ok_n = len(results) - len(failing)
    severity = ("high" if any(r["status"] == "missing" for r in failing)
                else "warn") if failing else "ok"
    data = {
        "checked": len(results),
        "failing_total": len(failing),
        "severity": severity,
        "verdict": "pass" if not failing else "block",
        "failing": [{"path": r["path"], "status": r["status"],
                     "reason": r.get("reason", "")} for r in failing][:5],
        "truncated": len(failing) > 5,
    }
    summary = f"{ok_n}/{len(results)} artifacts ok, {len(failing)} failing"
    return {"ok": True, "alert": bool(failing), "summary": summary, "data": data,
            "action": "verify"}


def run_inspect(config: dict) -> dict:
    specs = config.get("artifacts") if isinstance(config, dict) else []
    if not isinstance(specs, list):
        specs = []
    keys = ("path", "min_bytes", "nonempty", "max_age_days", "min_files", "needle")
    rows = [{k: v for k, v in s.items() if k in keys} for s in specs]
    data = {"type": "artifact-config", "total": len(rows),
            "rows": rows[:5], "truncated": len(rows) > 5}
    return {"ok": True, "alert": False, "summary": f"{len(rows)} artifacts configured",
            "data": data, "action": "inspect"}


def _load_config(base_dir: Path, config_arg: str | None) -> tuple[dict, str]:
    p = Path(config_arg) if config_arg else Path(base_dir) / "artifacts.json"
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
    ap.add_argument("--config", default=None,
                    help="Path to artifacts.json config; defaults to <dir>/artifacts.json.")
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
        if action == "inspect":
            config, warn = _load_config(base, args.config)
            if warn:
                _emit(_abort(warn, action))
                return 1
            _emit(run_inspect(config))
        else:
            config, warn = _load_config(base, args.config)
            if warn:
                _emit(_abort(warn, action))
                return 1
            _emit(run_verify(base, config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
