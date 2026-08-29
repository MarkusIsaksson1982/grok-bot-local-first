#!/usr/bin/env python3
"""freshness-gate -- timestamp / staleness detector for Grok Bot.

Offline, read-only, stdlib-only. Monitors files, directories, or globs and
flags anything that went stale (older than its max age). Lets the bot skip work
backed by data that is still current and only look at genuinely stale inputs.
Deterministic (use_bot:false).

Usage:
  python freshness-gate.py --manifest
  python freshness-gate.py --list-actions
  python freshness-gate.py --action check [--dir PATH] [--config PATH]

Optional config (default: <dir>/freshness.json):
{
  "mode": "any",
  "max_age_days": 7,
  "items": [
    "data/last.json",
    {"path": "snapshots", "max_age_days": 3, "mode": "any"},
    {"path": "logs/**/*.log", "max_age_days": 30}
  ]
}
Per item: a bare path string or an object. "mode": "any" judges by the newest
file, "all" by the oldest. Top-level "mode": "any" means stale if any item is
stale; "all" means stale only if every item is stale. Lists in data are capped
at 5 entries plus a total count.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ID = "freshness-gate"
TITLE = "Timestamp / staleness detector for watched inputs"
VERSION = "1.0.0"
SOURCE = "local"
KEYWORDS = ["freshness", "staleness", "timestamp", "age", "mtime"]
DEFAULT_ACTION = "check"
USE_BOT = False
PRIORITY = 55
SCAN_CAP = 10000
DEFAULT_DAYS = 7.0

ACTIONS = {
    "check": "Report per-item freshness and a single verdict (default).",
    "ages": "Reference age per watched item, for dashboards and graphing.",
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


def _mtimes(base: Path, path: str) -> list[float] | None:
    """Return mtimes of watched files, or None when nothing is watched."""
    if any(ch in path for ch in "*?["):
        try:
            iterator = base.glob(path.strip())
        except (re.error, ValueError):
            return None
        out: list[float] = []
        for p in iterator:
            if not p.is_file():
                continue
            try:
                out.append(p.stat().st_mtime)
            except OSError:
                continue
            if len(out) >= SCAN_CAP:
                break
        return out or None
    p = Path(base) / path
    if not p.exists():
        return None
    files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
    if not files:
        return None
    out = []
    for f in files:
        try:
            out.append(f.stat().st_mtime)
        except OSError:
            continue
    return out or None


def _item(base: Path, item, default_days: float) -> dict:
    if isinstance(item, dict):
        path = str(item.get("path", ""))
        days = float(item.get("max_age_days", default_days))
        mode = str(item.get("mode", "any"))
        missing_ok = bool(item.get("missing_ok", False))
    else:
        path = str(item)
        days = default_days
        mode = "any"
        missing_ok = False

    mts = _mtimes(base, path)
    if mts is None:
        if missing_ok:
            return {"path": path, "status": "fresh", "note": "missing_ok",
                    "age_days": None, "limit_days": days}
        return {"path": path, "status": "missing", "note": "nothing to watch",
                "age_days": None, "limit_days": days}

    ref = max(mts) if mode == "any" else min(mts)
    age_days = round((time.time() - ref) / 86400.0, 2)
    status = "stale" if age_days > days else "fresh"
    return {"path": path, "status": status, "age_days": age_days,
            "limit_days": days, "mode": mode}


def _items_list(config: dict) -> list:
    items = config.get("items") if isinstance(config, dict) else None
    return items if isinstance(items, list) and items else ["."]


def run_check(base: Path, config: dict) -> dict:
    items = _items_list(config)
    default_days = float(config.get("max_age_days", DEFAULT_DAYS))
    overall_mode = str(config.get("mode", "any"))
    rows = [_item(base, it, default_days) for it in items]
    stale = [r for r in rows if r["status"] == "stale"]
    ages = [r["age_days"] for r in rows if r["age_days"] is not None]

    if overall_mode == "all":
        verdict_stale = len(stale) == len(rows) and bool(rows)
    else:
        verdict_stale = bool(stale)

    severity = "high" if any(r.get("age_days") is None for r in stale) else "warn"
    data = {
        "mode": overall_mode,
        "default_max_age_days": default_days,
        "watched_total": len(rows),
        "stale_total": len(stale),
        "severity": severity if verdict_stale else "ok",
        "verdict": "ok" if not verdict_stale else "refresh",
        "stale": [r["path"] for r in stale][:5],
        "truncated": len(stale) > 5,
        "newest_age_days": min(ages) if ages else None,
        "oldest_age_days": max(ages) if ages else None,
    }
    summary = f"{len(rows) - len(stale)}/{len(rows)} fresh ({len(stale)} stale)"
    return {"ok": True, "alert": verdict_stale, "summary": summary, "data": data,
            "action": "check"}


def run_ages(base: Path, config: dict) -> dict:
    items = _items_list(config)
    default_days = float(config.get("max_age_days", DEFAULT_DAYS))
    rows = [_item(base, it, default_days) for it in items]
    table = [{"path": r["path"], "status": r["status"], "age_days": r["age_days"],
              "limit_days": r["limit_days"]} for r in rows]
    stale = sum(1 for r in rows if r["status"] == "stale")
    data = {"type": "freshness", "total": len(rows), "stale_total": stale,
            "rows": table[:5], "truncated": len(rows) > 5}
    return {"ok": True, "alert": bool(stale), "summary": f"ages for {len(rows)} watched items",
            "data": data, "action": "ages"}


def _load_config(base_dir: Path, config_arg: str | None) -> tuple[dict, str]:
    p = Path(config_arg) if config_arg else Path(base_dir) / "freshness.json"
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
                    help="Path to freshness.json config; defaults to <dir>/freshness.json.")
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
        if action == "check":
            _emit(run_check(base, config))
        elif action == "ages":
            _emit(run_ages(base, config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
