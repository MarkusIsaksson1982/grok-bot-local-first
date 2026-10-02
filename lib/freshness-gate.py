#!/usr/bin/env python3
"""freshness-gate -- timestamp / staleness detector for Grok Bot.

Offline, read-only, stdlib-only. Monitors files, directories, or globs and
flags anything that went stale (older than its max age). Lets the bot skip work
backed by data that is still current and only look at genuinely stale inputs.
Deterministic (use_bot:false).

Policy stubs emit JSON proposals only. They do not delete,
move, or schedule. Trigger priority, highest first:
  grok_bot_update > grok_build_harness > external_model (only_if_indicated)
  > foundational_grok_version (example 4.6->4.7; immediate high priority when
  a bump is observed within ~24h).
Archive rules apply only to an explicit designation: a path under
drop/_archive in the kit root. Other paths are not classified as archives.

Paths accept / and \\. Missing or unknown locations return a compact schema hint
so watched items can be expanded without opening this file.

Usage:
  python freshness-gate.py --manifest
  python freshness-gate.py --list-actions
  python freshness-gate.py --action check [--dir PATH] [--config PATH]
  python freshness-gate.py --action archive-age-check --dir <drop/_archive/...>
  python freshness-gate.py --action lib-scan-stub
  python freshness-gate.py --action checksum-dupe-stub
  python freshness-gate.py --action cadence-stub
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ID = "freshness-gate"
TITLE = "Timestamp / staleness detector for watched inputs"
VERSION = "1.0.0"
SOURCE = "grok-4.7-high"
KEYWORDS = ["freshness", "staleness", "timestamp", "age", "mtime", "archive"]
DEFAULT_ACTION = "check"
USE_BOT = False
PRIORITY = 55
SCAN_CAP = 10000
DEFAULT_DAYS = 7.0
ARCHIVE_REMOVE_DAYS = 7.0
FULL_SCAN_GATE_HOURS = 24.0
CADENCE_STUB_DAYS = 3
SKIP_WALK = frozenset({"__pycache__", ".git", "node_modules"})

# Standing trigger order. foundational_grok_version stays 4th, and a bump
# observed within ~24h (example 4.6->4.7) is immediate high priority.
TRIGGER_PRIORITY = [
    {
        "id": "grok_bot_update",
        "priority": 1,
        "when": "highest",
    },
    {
        "id": "grok_build_harness",
        "priority": 2,
        "when": "secondary",
    },
    {
        "id": "external_model",
        "priority": 3,
        "when": "only_if_indicated",
    },
    {
        "id": "foundational_grok_version",
        "priority": 4,
        "when": "immediate_high_priority_if_bump_within_24h",
        "example": "4.6->4.7",
    },
]

CADENCE_STUB = {
    "every_days": CADENCE_STUB_DAYS,
    "enabled": False,
    "scope": "deferred",
    "note": "stub only; decided scope for the every-3-days freshness cadence is deferred",
}

ACTIONS = {
    "check": "Report per-item freshness and a single verdict (default).",
    "ages": "Reference age per watched item, for dashboards and graphing.",
    "schema": "Config and path-pattern hint for expanding watched items.",
    "archive-age-check": "7-day remove expectation for an explicit drop\\_archive tree; proposal only.",
    "lib-scan-stub": "Full-lib scan cadence gate; suggest-skip when the last comparable scan is under 24h.",
    "checksum-dupe-stub": "Identical-checksum suggestions (delete or structural move); same 24h gate; authorized false.",
    "cadence-stub": "Note the every-3-days freshness cadence; scope deferred.",
}

HINT_TREE = {
    "config_file": "freshness.json (object with mode, max_age_days, items: list)",
    "item_forms": ["bare path string", "object with path plus optional keys"],
    "item_keys": ["path", "max_age_days", "mode", "missing_ok"],
    "modes": {
        "item": "any = newest file; all = oldest file",
        "top": "any = stale if any item stale; all = stale only if every item stale",
    },
    "path_forms": [
        "relative file: data\\last.json",
        "relative dir: snapshots",
        "glob: logs\\**\\*.log",
        "tree root: .",
    ],
    "suggested_roots": [
        ".",
        "drop",
        "lib",
        "out",
        "logs",
        "snapshots",
        "data",
        "Desktop",
        "Documents",
        "Downloads",
    ],
    "notes": (
        "paths are relative to --dir. Globs accept / or \\. "
        "'..' segments are rejected. missing_ok treats absence as fresh."
    ),
    "archive_designation": (
        "archive rules apply only under drop/_archive in the kit root. "
        "A folder name or nearby label is not a designation."
    ),
    "scan_state": "freshness-scan-state.json beside this script, or --state",
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


def _norm_pat(path: str) -> str:
    return path.strip().replace("\\", "/")


def _mtimes(base: Path, path: str) -> list[float] | None:
    path = _norm_pat(path)
    if ".." in path.split("/"):
        return None
    if any(ch in path for ch in "*?["):
        try:
            iterator = base.glob(path)
        except (re.error, ValueError, OSError):
            return None
        deadline = time.monotonic() + 1.0
        out: list[float] = []
        for p in iterator:
            if time.monotonic() >= deadline:
                break
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
    if p.is_file():
        files = [p]
    else:
        # Cap the recursive walk so a directory item stays inside the lint action cap.
        deadline = time.monotonic() + 1.0
        files = []
        try:
            iterator = p.rglob("*")
        except (OSError, ValueError):
            return None
        for f in iterator:
            if len(files) >= SCAN_CAP or time.monotonic() >= deadline:
                break
            if f.is_file():
                files.append(f)
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
        path = _norm_pat(str(item.get("path", "")))
        days = float(item.get("max_age_days", default_days))
        mode = str(item.get("mode", "any"))
        missing_ok = bool(item.get("missing_ok", False))
    else:
        path = _norm_pat(str(item))
        days = default_days
        mode = "any"
        missing_ok = False

    mts = _mtimes(base, path)
    if mts is None:
        if missing_ok:
            return {
                "path": path,
                "status": "fresh",
                "note": "missing_ok",
                "age_days": None,
                "limit_days": days,
            }
        return {
            "path": path,
            "status": "missing",
            "note": "nothing to watch",
            "age_days": None,
            "limit_days": days,
            "hint": HINT_TREE,
        }

    ref = max(mts) if mode == "any" else min(mts)
    age_days = round((time.time() - ref) / 86400.0, 2)
    status = "stale" if age_days > days else "fresh"
    return {
        "path": path,
        "status": status,
        "age_days": age_days,
        "limit_days": days,
        "mode": mode,
    }


def _items_list(config: dict) -> list:
    items = config.get("items") if isinstance(config, dict) else None
    # No config: top-level names only. Recursive "." exceeds worker-lint's action cap.
    return items if isinstance(items, list) and items else ["*"]


def run_check(base: Path, config: dict) -> dict:
    items = _items_list(config)
    default_days = float(config.get("max_age_days", DEFAULT_DAYS))
    overall_mode = str(config.get("mode", "any"))
    rows = [_item(base, it, default_days) for it in items]
    stale = [r for r in rows if r["status"] == "stale"]
    missing = [r for r in rows if r["status"] == "missing"]
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
        "missing_total": len(missing),
        "severity": severity if verdict_stale else ("warn" if missing else "ok"),
        "verdict": "ok" if not verdict_stale else "refresh",
        "stale": [r["path"] for r in stale][:5],
        "truncated": len(stale) > 5,
        "newest_age_days": min(ages) if ages else None,
        "oldest_age_days": max(ages) if ages else None,
    }
    if missing:
        data["hint"] = HINT_TREE
        data["missing"] = [r["path"] for r in missing][:5]
    summary = f"{len(rows) - len(stale)}/{len(rows)} fresh ({len(stale)} stale)"
    return {
        "ok": True,
        "alert": verdict_stale,
        "summary": summary,
        "data": data,
        "action": "check",
    }


def run_ages(base: Path, config: dict) -> dict:
    items = _items_list(config)
    default_days = float(config.get("max_age_days", DEFAULT_DAYS))
    rows = [_item(base, it, default_days) for it in items]
    table = [
        {
            "path": r["path"],
            "status": r["status"],
            "age_days": r["age_days"],
            "limit_days": r["limit_days"],
        }
        for r in rows
    ]
    stale = sum(1 for r in rows if r["status"] == "stale")
    data = {
        "type": "freshness",
        "total": len(rows),
        "stale_total": stale,
        "rows": table[:5],
        "truncated": len(rows) > 5,
        "hint": HINT_TREE,
    }
    return {
        "ok": True,
        "alert": bool(stale),
        "summary": f"ages for {len(rows)} watched items",
        "data": data,
        "action": "ages",
    }


def run_schema() -> dict:
    return {
        "ok": True,
        "alert": False,
        "summary": "freshness-gate schema / expansion hint",
        "data": {
            "type": "freshness-schema",
            "hint": HINT_TREE,
            "cadence_stub": CADENCE_STUB,
            "triggers": TRIGGER_PRIORITY,
        },
        "action": "schema",
    }


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


def _grokkit_root() -> Path:
    here = Path(__file__).resolve()
    script_dir = here.parent
    if script_dir.name.lower() == "lib":
        return script_dir.parent
    if script_dir.parent.name.lower() == "lib":
        return script_dir.parent.parent
    env = os.environ.get("GROKKIT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return script_dir


def _lib_dir() -> Path:
    return _grokkit_root() / "lib"


def _archive_root() -> Path:
    return _grokkit_root() / "drop" / "_archive"


def _under(child: Path, parent: Path) -> bool:
    try:
        c = os.path.normcase(os.path.abspath(child))
        p = os.path.normcase(os.path.abspath(parent))
        return os.path.commonpath([c, p]) == p
    except ValueError:
        return False


def _proposal(kind: str, target: str, note: str) -> dict:
    return {
        "kind": kind,
        "target": target,
        "authorized": False,
        "needs_judgment": True,
        "note": note,
    }


def _policy_fields() -> dict:
    return {
        "authorized": False,
        "cadence_stub": CADENCE_STUB,
        "triggers": TRIGGER_PRIORITY,
        "foundational_bump_note": (
            "A foundational model bump seen within about 24h is immediate high priority"
        ),
        "designation_rule": "only paths under drop/_archive in the kit root; no name-based guessing",
    }


def _age_days(mtime: float) -> float:
    return round((time.time() - mtime) / 86400.0, 2)


def _explicit_archive(path: Path) -> tuple[bool, str]:
    """True only when path is drop/_archive or a child of it."""
    root = _archive_root()
    if not root.is_dir():
        return False, "archive_root_missing"
    if _under(path, _lib_dir()) or os.path.abspath(path) == os.path.abspath(_grokkit_root()):
        return False, "refused_target"
    if _under(path, root):
        return True, "under_drop_xarchive"
    return False, "not_explicit_archive"


def _content_span(base: Path) -> dict:
    newest = None
    oldest = None
    count = 0
    truncated = False
    deadline = time.monotonic() + 1.0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_WALK and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            if count >= SCAN_CAP or time.monotonic() >= deadline:
                truncated = True
                break
            fp = Path(dirpath) / name
            try:
                mtime = fp.stat().st_mtime
            except OSError:
                continue
            count += 1
            newest = mtime if newest is None else max(newest, mtime)
            oldest = mtime if oldest is None else min(oldest, mtime)
        if truncated:
            break
    return {
        "file_count": count,
        "content_newest_age_days": None if newest is None else _age_days(newest),
        "content_oldest_age_days": None if oldest is None else _age_days(oldest),
        "truncated": truncated,
    }


def run_archive_age(target: str | None) -> tuple[dict, int]:
    action = "archive-age-check"
    if not target:
        return (
            {
                "ok": False,
                "alert": True,
                "summary": "archive-age-check needs --dir or --archive",
                "data": {
                    **_policy_fields(),
                    "reason": "missing_archive",
                    "proposal": _proposal("none", "", "no path was designated"),
                },
                "action": action,
            },
            2,
        )
    path = Path(target)
    if not path.is_dir():
        return (
            {
                "ok": False,
                "alert": True,
                "summary": "archive-age-check target is not a directory",
                "data": {
                    **_policy_fields(),
                    "reason": "not_a_directory",
                    "target": os.path.abspath(target),
                    "explicit_archive": False,
                    "proposal": _proposal("none", os.path.abspath(target), "not classified as archive"),
                },
                "action": action,
            },
            2,
        )
    explicit, why = _explicit_archive(path)
    abspath = os.path.abspath(path)
    if not explicit:
        return (
            {
                "ok": False,
                "alert": True,
                "summary": "archive-age-check refuses a path that is not an explicit archive",
                "data": {
                    **_policy_fields(),
                    "reason": why,
                    "target": abspath,
                    "explicit_archive": False,
                    "archive_root": str(_archive_root()),
                    "proposal": _proposal(
                        "none",
                        abspath,
                        "not classified as archive; no age remove expectation applied",
                    ),
                },
                "action": action,
            },
            2,
        )
    try:
        tree_age = _age_days(path.stat().st_mtime)
    except OSError:
        return _abort(f"cannot stat archive: {abspath}", action), 2
    span = _content_span(path)
    remove_expected = tree_age > ARCHIVE_REMOVE_DAYS
    gate = _scan_gate(_state_path(None))
    kind = "remove_expectation" if remove_expected else "none"
    summary = "archive-age-check remove_expected=%s age_days=%s threshold=%s" % (
        str(remove_expected).lower(),
        tree_age,
        int(ARCHIVE_REMOVE_DAYS) if ARCHIVE_REMOVE_DAYS == int(ARCHIVE_REMOVE_DAYS) else ARCHIVE_REMOVE_DAYS,
    )
    data = {
        **_policy_fields(),
        "reason": why,
        "target": abspath,
        "explicit_archive": True,
        "archive_root": str(_archive_root()),
        "age_days": tree_age,
        "age_source": "designated_directory_mtime",
        "remove_expectation_days": ARCHIVE_REMOVE_DAYS,
        "remove_expected": remove_expected,
        "content_oldest_age_days": span["content_oldest_age_days"],
        "content_newest_age_days": span["content_newest_age_days"],
        "file_count": span["file_count"],
        "truncated": span["truncated"],
        "full_lib_scan_due": gate["due"],
        "full_lib_scan_gate_hours": FULL_SCAN_GATE_HOURS,
        "proposal": _proposal(
            kind,
            abspath,
            "data-only; archive-age-check does not delete or move files",
        ),
        "compose": {
            "version_gate_action": "archive-diff",
            "freshness_action": "check",
            "combined_rule": (
                "freshness data.verdict refresh AND data.alert true "
                "AND version-gate remove_candidate true "
                "AND archive remove_expected true; none of these delete"
            ),
        },
    }
    return (
        {
            "ok": True,
            "alert": remove_expected,
            "summary": summary,
            "data": data,
            "action": action,
        },
        0,
    )


def run_cadence_stub() -> dict:
    return {
        "ok": True,
        "alert": False,
        "summary": "cadence stub every 3 days; scope deferred; not enabled",
        "data": {
            **_policy_fields(),
            "enabled": False,
            "scope": "deferred",
        },
        "action": "cadence-stub",
    }


def _state_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(__file__).resolve().parent / "freshness-scan-state.json"


def _state_refused(path: Path) -> str | None:
    if _under(path, _lib_dir()) or _under(path, _archive_root()):
        return "state_path_refused"
    if os.path.abspath(path) == os.path.abspath(_grokkit_root()):
        return "state_path_refused"
    return None


def _load_state(path: Path) -> tuple[dict, str]:
    if not path.is_file():
        return {}, ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}, "state_unreadable"
    if not isinstance(raw, dict):
        return {}, "state_unreadable"
    return raw, ""


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours_since(value: object) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    dt = _parse_iso(value.strip())
    if dt is None:
        return None
    hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    if hours < 0:
        return 0.0
    return hours


def _scan_gate(path: Path) -> dict:
    state, warn = _load_state(path)
    last = state.get("last_full_lib_scan_at") if isinstance(state, dict) else None
    hours = _hours_since(last)
    due = hours is None or hours >= FULL_SCAN_GATE_HOURS
    return {
        "due": due,
        "suggest_skip": not due,
        "hours_since": None if hours is None else round(hours, 2),
        "last_full_lib_scan_at": last if isinstance(last, str) else None,
        "last_full_lib_scan_action": state.get("last_full_lib_scan_action")
        if isinstance(state.get("last_full_lib_scan_action"), str)
        else None,
        "state_path": str(path),
        "state_warning": warn or None,
        "gate_hours": FULL_SCAN_GATE_HOURS,
    }


def _write_state(path: Path, action: str) -> str | None:
    refused = _state_refused(path)
    if refused:
        return refused
    payload = {
        "version": 1,
        "last_full_lib_scan_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "last_full_lib_scan_action": action,
        "gate_hours": FULL_SCAN_GATE_HOURS,
        "note": "cadence memory for lib-scan-stub and checksum-dupe-stub; not a scheduler",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        return "state_write_failed:%s" % exc.__class__.__name__
    return None


def _sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _lib_py_files() -> list[Path]:
    lib = _lib_dir()
    found: list[Path] = []
    try:
        names = sorted(os.listdir(lib))
    except OSError:
        return []
    for name in names:
        if not name.lower().endswith(".py") or name.lower() == "grokkit.py":
            continue
        path = lib / name
        if path.is_file():
            found.append(path)
    return found


def _same_slot(path: Path, lib: Path) -> bool:
    slot = lib / path.name
    try:
        return os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(slot))
    except OSError:
        return False


def _dupe_suggestions(files: list[Path]) -> tuple[list[dict], int]:
    """Group identical checksums. Suggest delete, or a structural move when a
    duplicate's basename has no lib/<basename>.py slot yet. Never authorized.
    """
    grouped: dict[str, list[Path]] = {}
    hashed = 0
    for path in files:
        if hashed >= SCAN_CAP:
            break
        digest = _sha256(path)
        hashed += 1
        if digest is None:
            continue
        grouped.setdefault(digest, []).append(path)
    lib = _lib_dir()
    suggestions: list[dict] = []
    for digest, paths in sorted(grouped.items(), key=lambda kv: kv[1][0].name.lower()):
        if len(paths) < 2:
            continue
        occupants = sorted((p for p in paths if _same_slot(p, lib)), key=lambda p: p.name.lower())
        others = sorted((p for p in paths if not _same_slot(p, lib)), key=lambda p: str(p).lower())
        # Identical bytes already sitting on more than one lib/<name>.py do not
        # fill a missing function. Keep the first name; suggest delete for the rest.
        deletes: list[str] = [str(p) for p in occupants[1:]]
        moves: list[dict] = []
        claimed = {p.name.lower() for p in occupants[:1]}
        for path in others:
            slot = lib / path.name
            name = path.name.lower()
            if slot.is_file() or name in claimed:
                deletes.append(str(path))
                continue
            moves.append(
                {
                    "from": str(path),
                    "to": str(slot),
                    "because": "fills_missing_lib_function",
                }
            )
            claimed.add(name)
        if moves and deletes:
            suggestion = "structural_move"
            why = (
                "some copies would fill a missing lib/<basename>.py function; "
                "other identical copies are delete suggestions; neither is authorized"
            )
        elif moves:
            suggestion = "structural_move"
            why = "duplicate fills a missing lib/<basename>.py function; move is a suggestion only"
        else:
            suggestion = "delete"
            why = "identical bytes; extra copies do not fill a missing lib function"
        suggestions.append(
            {
                "checksum": digest[:12],
                "count": len(paths),
                "paths": [str(p) for p in paths],
                "suggestion": suggestion,
                "delete_paths": deletes[:10],
                "structural_moves": moves[:10],
                "authorized": False,
                "needs_judgment": True,
                "note": why,
            }
        )
        if len(suggestions) >= 20:
            break
    return suggestions, hashed


def run_lib_scan_stub(state_arg: str | None) -> tuple[dict, int]:
    action = "lib-scan-stub"
    path = _state_path(state_arg)
    refused = _state_refused(path)
    if refused:
        return (
            {
                "ok": False,
                "alert": True,
                "summary": "lib-scan-stub refuses a state file under lib or drop\\_archive",
                "data": {
                    **_policy_fields(),
                    "reason": refused,
                    "state_path": str(path),
                    "proposal": _proposal("none", str(_lib_dir()), "state path refused; no scan recorded"),
                },
                "action": action,
            },
            2,
        )
    gate = _scan_gate(path)
    if gate["suggest_skip"]:
        return (
            {
                "ok": True,
                "alert": False,
                "summary": "lib-scan-stub suggest_skip=true last comparable scan <24h",
                "data": {
                    **_policy_fields(),
                    **gate,
                    "scanned": False,
                    "proposal": _proposal(
                        "suggest_skip",
                        str(_lib_dir()),
                        "full-lib scan is inside the 24h gate; nothing deleted",
                    ),
                },
                "action": action,
            },
            0,
        )
    files = _lib_py_files()
    write_error = _write_state(path, action)
    summary = "lib-scan-stub suggest_skip=false py=%d" % len(files)
    return (
        {
            "ok": write_error is None,
            "alert": write_error is not None,
            "summary": summary if write_error is None else "lib-scan-stub state write failed",
            "data": {
                **_policy_fields(),
                **gate,
                "suggest_skip": False,
                "due": True,
                "scanned": True,
                "lib": str(_lib_dir()),
                "py_count": len(files),
                "files": [p.name for p in files[:40]],
                "truncated": len(files) > 40,
                "state_write": "ok" if write_error is None else write_error,
                "proposal": _proposal(
                    "full_lib_scan",
                    str(_lib_dir()),
                    "stub enumeration only; not a scheduler; does not delete or move",
                ),
            },
            "action": action,
        },
        0 if write_error is None else 1,
    )


def run_checksum_dupe_stub(state_arg: str | None) -> tuple[dict, int]:
    action = "checksum-dupe-stub"
    path = _state_path(state_arg)
    refused = _state_refused(path)
    if refused:
        return (
            {
                "ok": False,
                "alert": True,
                "summary": "checksum-dupe-stub refuses a state file under lib or drop\\_archive",
                "data": {
                    **_policy_fields(),
                    "reason": refused,
                    "state_path": str(path),
                    "suggestions": [],
                    "proposal": _proposal("none", str(_lib_dir()), "state path refused; no scan recorded"),
                },
                "action": action,
            },
            2,
        )
    gate = _scan_gate(path)
    if gate["suggest_skip"]:
        return (
            {
                "ok": True,
                "alert": False,
                "summary": "checksum-dupe-stub suggest_skip=true last comparable scan <24h",
                "data": {
                    **_policy_fields(),
                    **gate,
                    "scanned": False,
                    "suggestions": [],
                    "proposal": _proposal(
                        "suggest_skip",
                        str(_lib_dir()),
                        "identical-checksum scan shares the 24h full-lib gate; nothing deleted",
                    ),
                },
                "action": action,
            },
            0,
        )
    files = _lib_py_files()
    suggestions, hashed = _dupe_suggestions(files)
    write_error = _write_state(path, action)
    summary = "checksum-dupe-stub suggest_skip=false suggestions=%d hashed=%d" % (
        len(suggestions),
        hashed,
    )
    return (
        {
            "ok": write_error is None,
            "alert": bool(suggestions) or write_error is not None,
            "summary": summary if write_error is None else "checksum-dupe-stub state write failed",
            "data": {
                **_policy_fields(),
                **gate,
                "suggest_skip": False,
                "due": True,
                "scanned": True,
                "lib": str(_lib_dir()),
                "hashed": hashed,
                "suggestion_count": len(suggestions),
                "suggestions": suggestions,
                "state_write": "ok" if write_error is None else write_error,
                "proposal": _proposal(
                    "checksum_duplicates" if suggestions else "none",
                    str(_lib_dir()),
                    "suggest delete or structural_move only; authorized false; no auto-delete",
                ),
            },
            "action": action,
        },
        0 if write_error is None else 1,
    )


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
    ap.add_argument(
        "--config",
        default=None,
        help="Path to freshness.json; defaults to <dir>/freshness.json.",
    )
    ap.add_argument(
        "--archive",
        default=None,
        help="Explicit archive path for archive-age-check. Still must be under drop\\_archive.",
    )
    ap.add_argument(
        "--state",
        default=None,
        help="Scan-cadence state JSON. Default: freshness-scan-state.json beside this script.",
    )
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
    if action == "schema":
        _emit(run_schema())
        return 0
    if action == "cadence-stub":
        _emit(run_cadence_stub())
        return 0
    if action == "archive-age-check":
        payload, code = run_archive_age(args.archive or args.dir)
        _emit(payload)
        return code
    if action == "lib-scan-stub":
        payload, code = run_lib_scan_stub(args.state)
        _emit(payload)
        return code
    if action == "checksum-dupe-stub":
        payload, code = run_checksum_dupe_stub(args.state)
        _emit(payload)
        return code

    base = Path(args.dir)
    if not base.is_dir():
        _emit(_abort(f"not a directory: {args.dir}", action))
        return 2

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
