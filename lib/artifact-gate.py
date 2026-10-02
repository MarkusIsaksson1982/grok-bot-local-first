#!/usr/bin/env python3
"""artifact-gate -- expected-deliverable existence and property checks.

Offline, read-only, stdlib-only. Verifies that expected deliverables exist and
satisfy basic properties (size, non-empty, freshness, header marker) so the bot
is not invoked to review work that was never produced. Deterministic
(use_bot:false). Never creates, modifies, or deletes anything.

Paths accept / and \\. Unknown or unsupported
locations do not crash: they report status and a compact schema hint so the
data structure can be expanded without reading this file.

Usage:
  python artifact-gate.py --manifest
  python artifact-gate.py --list-actions
  python artifact-gate.py --action verify [--dir PATH] [--config PATH]
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
SOURCE = "grok-4.7-high"
KEYWORDS = ["artifact", "deliverable", "existence", "verification", "build"]
DEFAULT_ACTION = "verify"
USE_BOT = False
PRIORITY = 60
SCAN_CAP = 4000
HEAD_BYTES = 65536

ACTIONS = {
    "verify": "Check every configured artifact; report failing items (default).",
    "inspect": "List the configured artifacts and the checks applied to them.",
    "schema": "Config and path-pattern hint for expanding watched deliverables.",
    "pack": "Tiny judgment pack; only filled when verify found real failures.",
}
BOT_ACTIONS = {"pack"}

# Compact expansion map for local folders. Not scanned unless listed
# in artifacts.json. Exposed by --action schema and on missing/unknown paths.
HINT_TREE = {
    "config_file": "artifacts.json (object with key artifacts: list)",
    "artifact_keys": [
        "path", "min_bytes", "nonempty", "max_age_days", "min_files", "needle",
        "missing_ok",
    ],
    "path_forms": [
        "relative file: out/build.zip",
        "relative dir: out",
        "glob: docs/**/*.md",
        "multi-segment glob: **/*.log",
    ],
    "suggested_roots": [
        ".",
        "out",
        "dist",
        "build",
        "docs",
        "drop",
        "lib",
        "logs",
        "Desktop",
        "Documents",
        "Downloads",
    ],
    "notes": (
        "path is relative to --dir (default .). Globs use * ? [ ]. "
        "'..' segments are rejected. needle is first-64KiB of a single file. "
        "Use missing_ok:true to treat an absent path as a non-failure."
    ),
}


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _abort(_summary: str, action: str = "abort", alert: bool = True,
           hint: bool = False) -> dict:
    data = {"error": _summary}
    if hint:
        data["hint"] = HINT_TREE
    return {
        "ok": False,
        "alert": alert,
        "summary": _summary,
        "data": data,
        "action": action,
    }


def _bare() -> dict:
    return {"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}


def _manifest() -> dict:
    return {
        "verified_at": "2026-10-02",
        "verified_grok_bot": "0.58.0",
        "ok": True,
        "id": ID,
        "title": TITLE,
        "source": SOURCE,
        "origin": {"grok_build": "1.0.41", "os": "windows-11"},
        "priority": PRIORITY,
        "keywords": KEYWORDS,
        "default_action": DEFAULT_ACTION,
        "actions": [
            {"name": n, "use_bot": n in BOT_ACTIONS, "summary": ACTIONS[n]}
            for n in sorted(ACTIONS)
        ],
    }


def _list_actions() -> dict:
    return {
        "ok": True,
        "id": ID,
        "default_action": DEFAULT_ACTION,
        "actions": [
            {"name": n, "use_bot": n in BOT_ACTIONS, "summary": ACTIONS[n]}
            for n in sorted(ACTIONS)
        ],
    }


def _norm_pat(pattern: str) -> str:
    return pattern.strip().replace("\\", "/")


def _find_glob(base: Path, pattern: str) -> list[Path]:
    pat = _norm_pat(pattern)
    if ".." in pat.split("/"):
        return []
    try:
        iterator = base.glob(pat)
    except (re.error, ValueError, OSError):
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
    path = _norm_pat(str(spec.get("path") or ""))
    is_glob = any(ch in path for ch in "*?[")
    missing_ok = bool(spec.get("missing_ok", False))

    if is_glob:
        matches = _find_glob(base, path)
        min_files = int(spec.get("min_files", 1))
        if len(matches) < min_files:
            status = "ok" if missing_ok and len(matches) == 0 else "missing"
            row = {
                "path": path,
                "status": status,
                "reason": f"{len(matches)}/{min_files} matches",
            }
            if status != "ok":
                row["hint"] = HINT_TREE
            return row
        total = sum(_size(f) for f in matches)
        if spec.get("nonempty") and total == 0:
            return {"path": path, "status": "empty", "reason": "all matches are empty"}
        if int(spec.get("min_bytes", 0)) > total:
            return {
                "path": path,
                "status": "too-small",
                "reason": f"{total}<{spec['min_bytes']}b",
            }
        return {"path": path, "status": "ok", "matches": len(matches), "bytes": total}

    p = Path(base) / path
    if not p.exists():
        if missing_ok:
            return {"path": path, "status": "ok", "reason": "missing_ok"}
        return {
            "path": path,
            "status": "missing",
            "reason": "not found",
            "hint": HINT_TREE,
        }
    files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
    if not files:
        if missing_ok:
            return {"path": path, "status": "ok", "reason": "missing_ok empty dir"}
        return {
            "path": path,
            "status": "missing",
            "reason": "directory has no files",
            "hint": HINT_TREE,
        }

    total = sum(_size(f) for f in files)
    if spec.get("nonempty") and total == 0:
        return {"path": path, "status": "empty", "reason": "artifact is empty"}
    if int(spec.get("min_bytes", 0)) > total:
        return {
            "path": path,
            "status": "too-small",
            "reason": f"{total}<{spec['min_bytes']}b",
        }
    if "max_age_days" in spec:
        newest = max(f.stat().st_mtime for f in files)
        age_days = (time.time() - newest) / 86400.0
        if age_days > float(spec["max_age_days"]):
            return {
                "path": path,
                "status": "stale",
                "reason": f"{age_days:.1f}d>{float(spec['max_age_days']):g}d",
            }
    if "needle" in spec and p.is_file():
        try:
            with open(p, "rb") as fh:
                head = fh.read(HEAD_BYTES)
        except OSError as exc:
            return {"path": path, "status": "unreadable", "reason": str(exc)}
        marker = spec["needle"]
        raw = marker.encode("utf-8") if isinstance(marker, str) else bytes(marker)
        if raw not in head:
            return {
                "path": path,
                "status": "needle-missing",
                "reason": f"head lacks marker {marker!r}",
            }
    return {"path": path, "status": "ok", "matches": len(files), "bytes": total}


def run_verify(base: Path, config: dict) -> dict:
    specs = config.get("artifacts") if isinstance(config, dict) else None
    if not isinstance(specs, list) or not specs:
        return {
            "ok": True,
            "alert": False,
            "summary": "no artifacts configured",
            "data": {
                "error": "no artifacts configured (set --config or add artifacts.json)",
                "verdict": "skip",
                "failing_total": 0,
            },
            "action": "verify",
        }
    results = [_inspect_artifact(base, s) if isinstance(s, dict) else
               _inspect_artifact(base, {"path": str(s)}) for s in specs]
    failing = [r for r in results if r["status"] != "ok"]
    ok_n = len(results) - len(failing)
    severity = (
        ("high" if any(r["status"] == "missing" for r in failing) else "warn")
        if failing else "ok"
    )
    failing_rows = [
        {"path": r["path"], "status": r["status"], "reason": r.get("reason", "")}
        for r in failing
    ][:5]
    data = {
        "checked": len(results),
        "failing_total": len(failing),
        "severity": severity,
        "verdict": "pass" if not failing else "block",
        "failing": failing_rows,
        "truncated": len(failing) > 5,
    }
    if failing:
        data["hint"] = HINT_TREE
    summary = f"{ok_n}/{len(results)} artifacts ok, {len(failing)} failing"
    return {
        "ok": True,
        "alert": bool(failing),
        "summary": summary,
        "data": data,
        "action": "verify",
    }


def run_inspect(config: dict) -> dict:
    specs = config.get("artifacts") if isinstance(config, dict) else []
    if not isinstance(specs, list):
        specs = []
    keys = (
        "path", "min_bytes", "nonempty", "max_age_days",
        "min_files", "needle", "missing_ok",
    )
    rows = []
    for s in specs:
        if isinstance(s, dict):
            rows.append({k: v for k, v in s.items() if k in keys})
        else:
            rows.append({"path": str(s)})
    data = {
        "type": "artifact-config",
        "total": len(rows),
        "rows": rows[:5],
        "truncated": len(rows) > 5,
        "hint": HINT_TREE,
    }
    return {
        "ok": True,
        "alert": False,
        "summary": f"{len(rows)} artifacts configured",
        "data": data,
        "action": "inspect",
    }


def run_schema() -> dict:
    data = {"type": "artifact-schema", "hint": HINT_TREE}
    return {
        "ok": True,
        "alert": False,
        "summary": "artifact-gate schema / expansion hint",
        "data": data,
        "action": "schema",
    }


def run_pack(base: Path, config: dict) -> dict:
    """Judgment pack for the bot. Empty unless verify found real failures."""
    verified = run_verify(base, config)
    failing = (verified.get("data") or {}).get("failing") or []
    real = [r for r in failing if r.get("status") not in ("ok", None)]
    if not real:
        return {
            "ok": True,
            "alert": False,
            "summary": "no pack; nothing configured or artifacts ok",
            "data": {"emit": False, "verdict": "skip", "items": []},
            "action": "pack",
        }
    items = [
        {"path": r.get("path"), "status": r.get("status"), "reason": r.get("reason", "")}
        for r in real[:5]
    ]
    return {
        "ok": True,
        "alert": True,
        "summary": f"pack {len(real)} failing artifacts",
        "data": {
            "emit": True,
            "verdict": "block",
            "failing_total": len(real),
            "items": items,
            "truncated": len(real) > 5,
        },
        "action": "pack",
    }


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
    ap = argparse.ArgumentParser(prog=ID, description=TITLE, add_help=True)
    ap.add_argument("--manifest", action="store_true", help="Print the worker manifest (last-line JSON).")
    ap.add_argument("--list-actions", action="store_true", help="Print available actions.")
    ap.add_argument(
        "--action",
        choices=sorted(ACTIONS),
        default=None,
        help=f"Run an action (default only after an explicit --action; preferred: {DEFAULT_ACTION}).",
    )
    ap.add_argument("--dir", default=".", help="Base directory to inspect (read-only).")
    ap.add_argument(
        "--config",
        default=None,
        help="Path to artifacts.json; defaults to <dir>/artifacts.json.",
    )
    args_in = sys.argv[1:] if argv is None else argv
    if not args_in:
        _emit(_bare())
        return 2
    args = ap.parse_args(args_in)

    if not (args.manifest or args.list_actions or args.action):
        _emit(_abort("pass --manifest", action="abort", alert=False, hint=False))
        return 2

    if args.manifest:
        _emit(_manifest())
        return 0
    if args.list_actions:
        _emit(_list_actions())
        return 0

    action = args.action
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
    if action == "inspect":
        _emit(run_inspect(config))
    elif action == "pack":
        _emit(run_pack(base, config))
    else:
        _emit(run_verify(base, config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
