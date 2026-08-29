#!/usr/bin/env python3
"""state-compact -- read-only last.json and log.jsonl reconciliation and dedup.

Offline, read-only, stdlib-only. Discovers local last.json / log.jsonl
pair (via relative globs, no hardcoded absolute paths), checks that
last.json equals the last log entry, and reports deduplication stats
so Grok Bot can ingest a compact state without the full history.
Deterministic except pack (genuine judgment on drift/dupes).

Usage:
  python state-compact.py --manifest
  python state-compact.py --list-actions
  python state-compact.py --action verify [--dir PATH]
  python state-compact.py --action collect [--dir PATH]
  python state-compact.py --action pack [--dir PATH]

Lists in data are capped at 5 entries with totals.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ID = "state-compact"
TITLE = "State reconciliation and compaction for last.json and log.jsonl"
SOURCE = "local"
KEYWORDS = ["state", "reconciliation", "compact", "dedup", "log"]
DEFAULT_ACTION = "verify"
PRIORITY = 87
SCAN_CAP = 4000
MAX_LAST_BYTES = 5_000_000
MAX_LOG_BYTES = 20_000_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".idea", ".vscode", "target"}

ACTIONS = {
    "verify": "Reconcile last.json against last log.jsonl entry + dedup (default).",
    "collect": "Discover state files and count entries.",
    "pack": "Bounded review pack when drift or duplicates need judgment.",
}


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _abort(summary: str, action: str = "abort") -> dict:
    return {"ok": False, "alert": True, "summary": summary, "data": {"error": summary}, "action": action}


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
            {"name": n, "use_bot": (n == "pack"), "summary": ACTIONS[n]}
            for n in sorted(ACTIONS)
        ],
    }


def _list_actions() -> dict:
    return {
        "ok": True,
        "id": ID,
        "default_action": DEFAULT_ACTION,
        "actions": [{"name": n, "use_bot": (n == "pack"), "summary": ACTIONS[n]} for n in sorted(ACTIONS)],
    }


def _cap(lst: list, n: int = 5):
    return lst[:n], len(lst)


def _is_skipped(p: Path, base: Path) -> bool:
    try:
        rel = p.relative_to(base)
    except ValueError:
        return True
    for part in rel.parts:
        if part in SKIP_DIRS:
            return True
    return False


def _discover(base: Path):
    # gather candidate last and log files via relative walk
    last_cands: list[Path] = []
    log_cands: list[Path] = []
    seen = 0
    # explicit relative literals first (no hardcoded absolutes)
    literal_last = ["last.json", "state/last.json", "data/last.json", ".state/last.json", "state.json", "data/state.json"]
    literal_log = ["log.jsonl", "state/log.jsonl", "data/log.jsonl", ".state/log.jsonl", "events.jsonl", "state/events.jsonl", "data/events.jsonl", "history.jsonl", "state/history.jsonl"]
    for pat in literal_last:
        p = base / pat
        if p.is_file() and not _is_skipped(p, base):
            try:
                if p.stat().st_size <= MAX_LAST_BYTES:
                    last_cands.append(p)
            except OSError:
                continue
    for pat in literal_log:
        p = base / pat
        if p.is_file() and not _is_skipped(p, base):
            try:
                if p.stat().st_size <= MAX_LOG_BYTES:
                    log_cands.append(p)
            except OSError:
                continue
    # deep glob to catch alternate locations (capped)
    for p in base.rglob("*.json"):
        if len(last_cands) + len(log_cands) >= 20:
            break
        if seen >= SCAN_CAP:
            break
        seen += 1
        if not p.is_file() or _is_skipped(p, base):
            continue
        n = p.name.lower()
        if n in ("last.json", "state.json") and p not in last_cands:
            try:
                if p.stat().st_size <= MAX_LAST_BYTES:
                    last_cands.append(p)
            except OSError:
                continue
    for p in base.rglob("*.jsonl"):
        if len(log_cands) >= 10:
            break
        if not p.is_file() or _is_skipped(p, base):
            continue
        n = p.name.lower()
        # prioritize log/history/events names, but accept any jsonl as potential log
        if p not in log_cands:
            try:
                if p.stat().st_size <= MAX_LOG_BYTES:
                    log_cands.append(p)
            except OSError:
                continue
    # keep at most 5 each, newest first
    def _newest(paths: list[Path]):
        def _mtime(x):
            try:
                return x.stat().st_mtime
            except OSError:
                return 0
        return sorted(paths, key=_mtime, reverse=True)[:5]
    last_cands = _newest(last_cands)
    log_cands = _newest(log_cands)
    # also sort log by relevance: name contains log/history/events first
    def _log_rank(p: Path):
        n = p.name.lower()
        if "log" in n:
            return 0
        if "event" in n:
            return 1
        if "history" in n:
            return 1
        return 2
    log_cands.sort(key=lambda p: (_log_rank(p), -p.stat().st_mtime if p.exists() else 0))
    return last_cands, log_cands


def _load_last(path: Path | None):
    if path is None or not path.is_file():
        return None, False, "missing"
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return None, False, str(e)[:80]
    if not txt.strip():
        return None, False, "empty"
    try:
        obj = json.loads(txt)
        return obj, True, ""
    except Exception as e:
        return None, False, f"invalid json: {e}"[:80]


def _scan_log(path: Path | None):
    if path is None or not path.is_file():
        return {"total": 0, "valid": 0, "invalid": 0, "unique": 0, "duplicates": 0, "last": None, "dup_examples": [], "dup_total": 0}
    total = valid = invalid = 0
    last_obj = None
    counts: dict[str, int] = {}
    # also track count by id if present for better dedup signal
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                if total > 20000:  # cap processing for huge logs
                    # still count total lines but stop parsing? continue counting without parsing?
                    # we already counted, break dedup but continue? for simplicity break
                    break
                try:
                    obj = json.loads(line)
                except Exception:
                    invalid += 1
                    continue
                valid += 1
                last_obj = obj
                try:
                    canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                except Exception:
                    canon = line[:200]
                counts[canon] = counts.get(canon, 0) + 1
                if valid > 20000:
                    break
    except OSError:
        return {"total": 0, "valid": 0, "invalid": 0, "unique": 0, "duplicates": 0, "last": None, "dup_examples": [], "dup_total": 0}
    unique = len(counts)
    duplicates = valid - unique if valid >= unique else 0
    dup_items = [(k, c) for k, c in counts.items() if c > 1]
    dup_items.sort(key=lambda x: -x[1])
    examples = []
    for canon, c in dup_items[:5]:
        try:
            obj = json.loads(canon)
            # keep tiny sample: up to 80 chars of canonical or id field
            sample = ""
            if isinstance(obj, dict):
                for k in ("id", "key", "ts", "timestamp", "seq"):
                    if k in obj:
                        sample = f"{k}={obj[k]}"
                        break
            if not sample:
                sample = canon[:80]
        except Exception:
            sample = canon[:80]
        examples.append({"sample": sample, "count": c})
    return {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "unique": unique,
        "duplicates": duplicates,
        "last": last_obj,
        "dup_examples": examples,
        "dup_total": len(dup_items),
    }


def _drift_note(a, b) -> str:
    if a is None or b is None:
        return "missing side"
    if a == b:
        return "exact match"
    if isinstance(a, dict) and isinstance(b, dict):
        ak = set(a.keys())
        bk = set(b.keys())
        only_a = sorted(ak - bk)[:3]
        only_b = sorted(bk - ak)[:3]
        changed = []
        for k in sorted(ak & bk)[:5]:
            if a[k] != b[k]:
                changed.append(k)
                if len(changed) >= 3:
                    break
        parts = []
        if only_a:
            parts.append(f"only in last: {','.join(only_a)}")
        if only_b:
            parts.append(f"only in log: {','.join(only_b)}")
        if changed:
            parts.append(f"diff keys: {','.join(changed)}")
        return "; ".join(parts)[:120] if parts else "value mismatch"
    return "value mismatch"[:120]


def _rel(p: Path | None, base: Path) -> str | None:
    if p is None:
        return None
    try:
        return p.relative_to(base).as_posix()
    except Exception:
        return p.name


def run_collect(base: Path) -> dict:
    last_cands, log_cands = _discover(base)
    last_path = last_cands[0] if last_cands else None
    log_path = log_cands[0] if log_cands else None
    last_obj, last_valid, _ = _load_last(last_path)
    log_info = _scan_log(log_path)
    summary = f"discovered {len(last_cands)} last, {len(log_cands)} log; log {log_info['valid']}/{log_info['total']} valid"
    data = {
        "discovered": {"last_total": len(last_cands), "log_total": len(log_cands)},
        "last": {"path": _rel(last_path, base), "exists": last_path is not None, "valid": last_valid},
        "log": {"path": _rel(log_path, base), "exists": log_path is not None, "total": log_info["total"], "valid": log_info["valid"], "invalid": log_info["invalid"]},
        "dedup": {"unique": log_info["unique"], "duplicates": log_info["duplicates"]},
        "truncated": (len(last_cands) > 5 or len(log_cands) > 5),
    }
    # provide capped discovered lists
    if last_cands:
        lst, _ = _cap([_rel(p, base) for p in last_cands])
        data["last_candidates"] = lst
        data["last_candidates_total"] = len(last_cands)
    if log_cands:
        lst, _ = _cap([_rel(p, base) for p in log_cands])
        data["log_candidates"] = lst
        data["log_candidates_total"] = len(log_cands)
    return {"ok": True, "alert": bool(log_info["invalid"] > 0), "summary": summary, "data": data, "action": "collect"}


def run_verify(base: Path) -> dict:
    last_cands, log_cands = _discover(base)
    last_path = last_cands[0] if last_cands else None
    log_path = log_cands[0] if log_cands else None
    last_obj, last_valid, last_err = _load_last(last_path)
    log_info = _scan_log(log_path)
    match = False
    drift = "no comparison (missing)"
    if last_valid and log_info["last"] is not None:
        match = (last_obj == log_info["last"])
        drift = _drift_note(last_obj, log_info["last"]) if not match else "exact match"
    elif not last_valid and last_path is not None:
        drift = last_err[:80]
    elif log_info["total"] == 0 and last_path is None:
        drift = "no state files"
    needs = (not match and last_valid and log_info["last"] is not None) or log_info["duplicates"] > 0 or log_info["invalid"] > 0
    # dedup examples capped
    ex, ex_total = _cap(log_info["dup_examples"])
    summary = f"verify: {'match' if match else 'drift'}; dup {log_info['duplicates']}, invalid {log_info['invalid']}"
    if last_path is None and log_path is None:
        summary = "no state files found"
        needs = False
    data = {
        "last": {"path": _rel(last_path, base), "exists": last_path is not None, "valid": last_valid},
        "log": {"path": _rel(log_path, base), "exists": log_path is not None, "total": log_info["total"], "valid": log_info["valid"], "invalid": log_info["invalid"]},
        "reconciliation": {"match": match, "drift": drift, "needs_review": needs},
        "dedup": {"unique": log_info["unique"], "duplicates": log_info["duplicates"], "examples": ex, "examples_total": ex_total, "truncated": ex_total > 5},
        "counts": {"last_candidates": len(last_cands), "log_candidates": len(log_cands)},
    }
    return {"ok": True, "alert": bool(needs), "summary": summary, "data": data, "action": "verify"}


def run_pack(base: Path) -> dict:
    last_cands, log_cands = _discover(base)
    last_path = last_cands[0] if last_cands else None
    log_path = log_cands[0] if log_cands else None
    last_obj, last_valid, _ = _load_last(last_path)
    log_info = _scan_log(log_path)
    match = False
    drift = "no comparison"
    if last_valid and log_info["last"] is not None:
        match = (last_obj == log_info["last"])
        drift = _drift_note(last_obj, log_info["last"]) if not match else "exact match"
    needs = (not match and last_valid and log_info["last"] is not None) or log_info["duplicates"] > 0 or log_info["invalid"] > 0 or log_info["total"] > 10000
    ex, ex_total = _cap(log_info["dup_examples"])
    # tiny last sample (truncate)
    last_sample = None
    if last_valid and isinstance(last_obj, dict):
        # keep only 2 keys sample
        keys = list(last_obj.keys())[:2]
        last_sample = {k: str(last_obj[k])[:40] for k in keys}
    elif last_valid:
        last_sample = str(last_obj)[:80]
    summary = f"pack: {'needs review' if needs else 'compact clean'}; drift={'match' if match else 'yes' if last_valid and log_info['last'] else 'n/a'}"
    data = {
        "needs_review": needs,
        "reconciliation": {"match": match, "drift": drift},
        "dedup": {"duplicates": log_info["duplicates"], "examples": ex, "examples_total": ex_total},
        "log": {"path": _rel(log_path, base), "total": log_info["total"], "invalid": log_info["invalid"]},
        "last": {"path": _rel(last_path, base), "valid": last_valid, "sample": last_sample},
        "truncated": ex_total > 5,
    }
    return {"ok": True, "alert": bool(needs), "summary": summary, "data": data, "action": "pack"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=ID, description=TITLE)
    ap.add_argument("--manifest", action="store_true", help="Print manifest (last line JSON).")
    ap.add_argument("--list-actions", action="store_true", help="List actions.")
    ap.add_argument("--action", choices=sorted(ACTIONS), default=None, help="Run action.")
    ap.add_argument("--dir", default=".", help="Base directory to inspect (read-only).")
    ap.add_argument("--root", default=None, help="Alias for --dir.")
    args = ap.parse_args(argv)

    base_arg = args.root if args.root is not None else args.dir
    base = Path(base_arg)
    if not base.is_dir():
        _emit(_abort(f"not a directory: {base_arg}", args.action or DEFAULT_ACTION))
        return 2

    if args.manifest:
        _emit(_manifest())
        return 0
    if args.list_actions:
        _emit(_list_actions())
        return 0

    action = args.action or DEFAULT_ACTION
    if action == "collect":
        _emit(run_collect(base))
    elif action == "verify":
        _emit(run_verify(base))
    elif action == "pack":
        _emit(run_pack(base))
    else:
        _emit(_abort(f"unknown action: {action}", action))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
