#!/usr/bin/env python3
"""version-gate: stamp vs current_grok_bot for lib workers.

archive-diff is read-only. It compares an archive tree to lib counterparts
and may describe a delete proposal. It does not delete or move files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

CURRENT_FALLBACK = "0.58.0"
MANIFEST = {
    "ok": True,
    "id": "version-gate",
    "title": "Compare lib worker verified_grok_bot stamps to current",
    "source": "grok-4.7-high",
    "origin": {"grok_build": "1.0.41", "os": "windows-11"},
    "priority": 50,
    "keywords": ["version", "stamp", "verified_grok_bot", "iterate", "freshness", "archive"],
    "default_action": "check",
    "verified_grok_bot": "0.58.0",
    "verified_at": "2026-10-02",
    "actions": [
        {"name": "scan", "use_bot": False, "summary": "List lib workers and stamps"},
        {"name": "check", "use_bot": False, "summary": "Flag missing/outdated stamps"},
        {"name": "stamp", "use_bot": False, "summary": "Stamp missing/outdated lib workers; --ids limits"},
        {"name": "pack", "use_bot": True, "summary": "Tiny iterate pack if stamps lag"},
        {
            "name": "archive-diff",
            "use_bot": False,
            "summary": "Read-only archive vs lib diff; delete proposal stays authorized false",
        },
    ],
}


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
    parent = os.path.dirname(script_dir)
    lane_root = _lane_aware_root(script_dir)
    if lane_root:
        return lane_root
    env = os.environ.get("GROKKIT_ROOT")
    if env:
        return os.path.abspath(env)
    return script_dir


def lib_dir() -> str:
    return os.path.join(root_dir(), "lib")


def sources_path() -> str:
    return os.path.join(root_dir(), "sources.json")


def current_bot() -> str:
    path = sources_path()
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        obj = json.loads(raw)
        if isinstance(obj, dict):
            v = obj.get("current_grok_bot")
            if isinstance(v, str) and v.strip():
                return v.strip()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return CURRENT_FALLBACK


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_ver(s: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in s.strip().split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts) if parts else (0,)


def is_outdated(stamp: str | None, current: str) -> bool:
    if not stamp:
        return True
    try:
        return parse_ver(stamp) < parse_ver(current)
    except (TypeError, ValueError):
        return True


def last_json_line(text: str) -> Any | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def run_manifest(py_path: str) -> dict[str, Any] | None:
    try:
        proc = subprocess.run(
            [sys.executable, py_path, "--manifest"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=root_dir(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    obj = last_json_line(proc.stdout or "")
    if not isinstance(obj, dict) or obj.get("ok") is not True:
        return None
    return obj


def list_lib_py() -> list[str]:
    """All *.py under lib/ and lib/<lane>/ (one level). Skip __pycache__."""
    d = lib_dir()
    out: list[str] = []
    if not os.path.isdir(d):
        return out
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x.lower() != "__pycache__" and not x.startswith(".")]
        # only lib/ and lib/<one-lane>/
        rel = os.path.relpath(root, d)
        if rel != "." and os.path.dirname(rel) not in ("", "."):
            dirs[:] = []
            continue
        if rel.count(os.sep) > 0:
            # deeper than lib/lane — do not descend further
            dirs[:] = []
        for name in files:
            if not name.lower().endswith(".py"):
                continue
            if name.lower() == "grokkit.py":
                continue
            out.append(os.path.join(root, name))
    out.sort()
    return out



def collect_workers() -> list[dict[str, Any]]:
    # origin records where a worker was last iterated. It is not a runtime target.
    rows: list[dict[str, Any]] = []
    for path in list_lib_py():
        name = os.path.basename(path)
        man = run_manifest(path)
        if man is None:
            continue
        wid = man.get("id")
        if not isinstance(wid, str) or not wid:
            wid = os.path.splitext(name)[0]
        stamp = man.get("verified_grok_bot")
        stamp_s = stamp.strip() if isinstance(stamp, str) and stamp.strip() else None
        rows.append(
            {
                "id": wid,
                "file": name,
                "path": path,
                "verified_grok_bot": stamp_s,
                "verified_at": man.get("verified_at") if isinstance(man.get("verified_at"), str) else None,
            }
        )
    return rows


def cap_summary(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


def action_scan() -> dict[str, Any]:
    rows = collect_workers()
    slim = [
        {"id": r["id"], "file": r["file"], "verified_grok_bot": r["verified_grok_bot"] or "missing"}
        for r in rows[:20]
    ]
    return {
        "ok": True,
        "alert": False,
        "summary": cap_summary("scanned %d lib workers" % len(rows)),
        "data": {"count": len(rows), "workers": slim, "truncated": len(rows) > 20},
        "action": "scan",
    }


def classify() -> dict[str, Any]:
    current = current_bot()
    rows = collect_workers()
    missing: list[str] = []
    outdated: list[str] = []
    ok_ids: list[str] = []
    for r in rows:
        label = r["id"]
        stamp = r["verified_grok_bot"]
        if stamp is None:
            missing.append(label)
        elif is_outdated(stamp, current):
            outdated.append(label)
        else:
            ok_ids.append(label)
    bad = missing + outdated
    return {
        "current_grok_bot": current,
        "total": len(rows),
        "ok_count": len(ok_ids),
        "missing_count": len(missing),
        "outdated_count": len(outdated),
        "outdated_or_missing": len(bad),
        "missing": missing[:20],
        "outdated": outdated[:20],
        "lib_empty": len(rows) == 0,
    }


def action_check() -> dict[str, Any]:
    c = classify()
    nbad = int(c["outdated_or_missing"])
    alert = nbad > 0
    if c["lib_empty"]:
        summary = "lib empty; no stamps to check"
    elif alert:
        summary = "stamps lag: %d outdated_or_missing vs %s" % (nbad, c["current_grok_bot"])
    else:
        summary = "all %d workers stamped current (%s)" % (c["total"], c["current_grok_bot"])
    data = {
        "current_grok_bot": c["current_grok_bot"],
        "total": c["total"],
        "outdated_or_missing": nbad,
        "missing_count": c["missing_count"],
        "outdated_count": c["outdated_count"],
        "missing": c["missing"],
        "outdated": c["outdated"],
    }
    return {
        "ok": True,
        "alert": alert,
        "summary": cap_summary(summary),
        "data": data,
        "action": "check",
    }


def action_pack() -> dict[str, Any]:
    c = classify()
    nbad = int(c["outdated_or_missing"])
    if nbad <= 0:
        return {
            "ok": True,
            "alert": False,
            "summary": cap_summary("no judgment needed"),
            "data": {"emit": False, "needs_judgment": False},
            "action": "pack",
        }
    ids = (c["missing"] + c["outdated"])[:20]
    return {
        "ok": True,
        "alert": True,
        "summary": cap_summary("iterate stamps: %d lag vs %s" % (nbad, c["current_grok_bot"])),
        "data": {
            "emit": True,
            "needs_judgment": True,
            "current_grok_bot": c["current_grok_bot"],
            "outdated_or_missing": nbad,
            "ids": ids,
        },
        "action": "pack",
    }


def parse_stamp_flags(argv: list[str]) -> tuple[list[str] | None, bool]:
    ids: list[str] | None = None
    all_missing = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--all-missing":
            all_missing = True
            i += 1
            continue
        if a == "--ids" and i + 1 < len(argv):
            raw = argv[i + 1]
            ids = [p.strip() for p in raw.split(",") if p.strip()]
            i += 2
            continue
        if a.startswith("--ids="):
            raw = a.split("=", 1)[1]
            ids = [p.strip() for p in raw.split(",") if p.strip()]
            i += 1
            continue
        i += 1
    return ids, all_missing


def allowed_lib_path(path: str) -> bool:
    lib = os.path.abspath(lib_dir())
    ap = os.path.abspath(path)
    try:
        common = os.path.commonpath([lib, ap])
    except ValueError:
        return False
    if common != lib:
        return False
    base = os.path.basename(ap).lower()
    if base == "grokkit.py":
        return False
    if not base.endswith(".py"):
        return False
    parent = os.path.dirname(ap)
    if parent != lib and os.path.dirname(parent) != lib:
        return False
    return os.path.isfile(ap)


def _replace_or_insert_key(block: str, key: str, value: str) -> tuple[str, bool]:
    pat = re.compile(
        r'(["\']' + re.escape(key) + r'["\']\s*:\s*)(["\'])([^"\']*)(\2)',
        re.M,
    )
    if pat.search(block):
        return pat.sub(r"\1\2" + value + r"\4", block, count=1), True
    # Insert after first opening brace of the block.
    ins = '\n    "%s": "%s",' % (key, value)
    if "{" in block:
        idx = block.find("{")
        return block[: idx + 1] + ins + block[idx + 1 :], True
    return block, False


def patch_source(text: str, bot: str, when: str) -> tuple[str, str]:
    """Return (new_text, mode) where mode is manifest_dict|json_string|none."""
    man = re.search(r"\bMANIFEST\s*=\s*\{", text)
    if man:
        start = man.start()
        i = man.end() - 1
        depth = 0
        end = None
        while i < len(text):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            i += 1
        if end is not None:
            block = text[start:end]
            block, _ = _replace_or_insert_key(block, "verified_grok_bot", bot)
            block, _ = _replace_or_insert_key(block, "verified_at", when)
            return text[:start] + block + text[end:], "manifest_dict"

    def repl_json_obj(m: re.Match[str]) -> str:
        chunk = m.group(0)
        chunk, _ = _replace_or_insert_key(chunk, "verified_grok_bot", bot)
        chunk, _ = _replace_or_insert_key(chunk, "verified_at", when)
        return chunk

    new, n = re.subn(
        r'\{\s*"ok"\s*:\s*True[\s\S]{0,2500}?\}',
        repl_json_obj,
        text,
        count=1,
    )
    if n:
        return new, "json_string"
    new, n = re.subn(
        r'\{\s*"ok"\s*:\s*true[\s\S]{0,2500}?\}',
        repl_json_obj,
        text,
        count=1,
    )
    if n:
        return new, "json_string"
    return text, "none"


def write_with_bak(path: str, new_text: str) -> bool:
    bak = path + ".bak"
    try:
        if not os.path.isfile(bak):
            with open(path, encoding="utf-8", errors="replace") as f:
                old = f.read()
            with open(bak, "w", encoding="utf-8", newline="") as f:
                f.write(old)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
    except OSError:
        return False
    return True


def action_stamp() -> dict[str, Any]:
    current = current_bot()
    when = today_utc()
    ids, all_missing = parse_stamp_flags(sys.argv)
    if not ids and not all_missing:
        all_missing = True

    rows = collect_workers()
    by_id = {r["id"]: r for r in rows}
    targets: list[dict[str, Any]] = []
    skipped: list[str] = []

    if ids:
        for wid in ids[:40]:
            if wid not in by_id:
                skipped.append(wid + ":not_found")
                continue
            targets.append(by_id[wid])
    if all_missing:
        for r in rows:
            if r["id"] in {t["id"] for t in targets}:
                continue
            if r["verified_grok_bot"] is None or is_outdated(r["verified_grok_bot"], current):
                targets.append(r)

    stamped: list[str] = []
    for r in targets[:40]:
        path = r["path"]
        if not allowed_lib_path(path):
            skipped.append(r["id"] + ":refused")
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            skipped.append(r["id"] + ":unreadable")
            continue
        new_text, mode = patch_source(text, current, when)
        if mode == "none" or new_text == text:
            skipped.append(r["id"] + ":no_manifest_edit")
            continue
        if not write_with_bak(path, new_text):
            skipped.append(r["id"] + ":write_failed")
            continue
        stamped.append(r["id"])

    return {
        "ok": True,
        "alert": False,
        "summary": cap_summary("stamped %d, skipped %d vs %s" % (len(stamped), len(skipped), current)),
        "data": {
            "stamped": stamped[:20],
            "skipped": skipped[:20],
            "current_grok_bot": current,
        },
        "action": "stamp",
    }


ROW_CAP = 40
SKIP_WALK = frozenset({"__pycache__", ".git", "node_modules"})
FIELD_KEYS = ("source", "verified_grok_bot", "verified_at")


def parse_named_flags(argv: list[str], names: tuple[str, ...]) -> dict[str, str]:
    wanted = set(names)
    out: dict[str, str] = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        key = None
        val = None
        if a.startswith("--") and "=" in a:
            raw, val = a.split("=", 1)
            key = raw[2:]
            i += 1
        elif a.startswith("--") and a[2:] in wanted and i + 1 < len(argv):
            key = a[2:]
            val = argv[i + 1]
            i += 2
        else:
            i += 1
            continue
        if key in wanted and val is not None:
            out[key] = val
    return out


def file_sha256(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def read_text_head(path: str, n: int = 20000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(n)
    except OSError:
        return ""


def manifest_fields(path: str) -> dict[str, str | None]:
    """Literal manifest strings, or a top-level NAME = \"...\" alias. No exec."""
    text = read_text_head(path)
    found: dict[str, str | None] = {k: None for k in FIELD_KEYS}
    for key in FIELD_KEYS:
        literal = re.search(
            r'["\']' + re.escape(key) + r'["\']\s*:\s*(["\'])([^"\']*)\1',
            text,
        )
        if literal and literal.group(2).strip():
            found[key] = literal.group(2).strip()
            continue
        alias = re.search(
            r'["\']' + re.escape(key) + r'["\']\s*:\s*([A-Za-z_][A-Za-z0-9_]*)',
            text,
        )
        if not alias:
            continue
        assigned = re.search(
            r'(?m)^' + re.escape(alias.group(1)) + r'\s*=\s*(["\'])([^"\']*)\1',
            text,
        )
        if assigned and assigned.group(2).strip():
            found[key] = assigned.group(2).strip()
    return found


def refuse_archive_target(path: str) -> str | None:
    ap = os.path.abspath(path)
    lib = os.path.abspath(lib_dir())
    root = os.path.abspath(root_dir())
    if ap == lib or ap == root:
        return "refused_target"
    try:
        if os.path.commonpath([lib, ap]) == lib:
            return "refused_target"
    except ValueError:
        return "refused_target"
    return None


def iter_tree_files(base: str) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_WALK and not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            found.append(os.path.join(dirpath, name))
    found.sort()
    return found


def counterpart_for(name: str) -> tuple[str, str]:
    """lib worker by basename. grokkit.py is the root runner, not a lib worker."""
    if name.lower() == "grokkit.py":
        return "root", os.path.join(root_dir(), "grokkit.py")
        # prefer flat then lane match by basename
    flat = os.path.join(lib_dir(), name)
    if os.path.isfile(flat):
        return "lib", flat
    for lane in sorted(os.listdir(lib_dir())) if os.path.isdir(lib_dir()) else []:
        cand = os.path.join(lib_dir(), lane, name)
        if os.path.isfile(cand):
            return "lib", cand
    return "lib", flat


def classify_pair(
    arch_fields: dict[str, str | None],
    other_fields: dict[str, str | None],
    hash_differ: bool | None,
    present: bool,
) -> str:
    if not present:
        return "absent"
    if hash_differ is False:
        return "identical"
    if hash_differ is None:
        return "superseded"
    a_bot = arch_fields.get("verified_grok_bot")
    o_bot = other_fields.get("verified_grok_bot")
    if a_bot and o_bot and parse_ver(a_bot) > parse_ver(o_bot):
        return "archive_newer"
    a_at = arch_fields.get("verified_at")
    o_at = other_fields.get("verified_at")
    bot_not_older = (not a_bot) or (not o_bot) or parse_ver(a_bot) >= parse_ver(o_bot)
    if a_at and o_at and a_at > o_at and bot_not_older:
        return "archive_newer"
    return "superseded"


def _file_row(base: str, path: str, kind: str) -> dict[str, Any]:
    rel = os.path.relpath(path, base).replace("/", "\\")
    name = os.path.basename(path)
    where, other = counterpart_for(name)
    try:
        arch_bytes = os.path.getsize(path)
    except OSError:
        arch_bytes = None
    arch_hash = file_sha256(path)
    present = os.path.isfile(other)
    other_bytes = os.path.getsize(other) if present else None
    other_hash = file_sha256(other) if present else None
    hash_differ = None if arch_hash is None or other_hash is None else arch_hash != other_hash
    size_differ = None if arch_bytes is None or other_bytes is None else arch_bytes != other_bytes
    if kind == "py":
        arch_fields = manifest_fields(path)
        other_fields = manifest_fields(other) if present else {k: None for k in FIELD_KEYS}
        relation = classify_pair(arch_fields, other_fields, hash_differ, present)
    else:
        arch_fields = {k: None for k in FIELD_KEYS}
        other_fields = {k: None for k in FIELD_KEYS}
        if not present:
            relation = "noted"
        elif hash_differ:
            relation = "differs"
        else:
            relation = "identical"
    lib_present = present if where == "lib" else os.path.isfile(os.path.join(lib_dir(), name))
    return {
        "rel": rel,
        "name": name,
        "kind": kind,
        "counterpart": where,
        "archive_present": True,
        "lib_present": lib_present,
        "counterpart_present": present,
        "size_differ": size_differ,
        "hash_differ": hash_differ,
        "archive_bytes": arch_bytes,
        "counterpart_bytes": other_bytes,
        "archive_hash": arch_hash[:12] if arch_hash else None,
        "counterpart_hash": other_hash[:12] if other_hash else None,
        "archive_source": arch_fields.get("source"),
        "counterpart_source": other_fields.get("source"),
        "archive_verified_grok_bot": arch_fields.get("verified_grok_bot"),
        "counterpart_verified_grok_bot": other_fields.get("verified_grok_bot"),
        "archive_verified_at": arch_fields.get("verified_at"),
        "counterpart_verified_at": other_fields.get("verified_at"),
        "relation": relation,
    }


def action_archive_diff() -> dict[str, Any]:
    flags = parse_named_flags(sys.argv, ("dir", "archive"))
    target = flags.get("archive") or flags.get("dir")
    if not target:
        return {
            "ok": False,
            "alert": False,
            "summary": cap_summary("archive-diff needs --archive or --dir"),
            "data": {"reason": "missing_archive", "hint": "--archive or --dir"},
            "action": "archive-diff",
        }
    if not os.path.isdir(target):
        return {
            "ok": False,
            "alert": False,
            "summary": cap_summary("archive-diff target is not a directory"),
            "data": {"reason": "not_a_directory", "target": os.path.abspath(target)},
            "action": "archive-diff",
        }
    refused = refuse_archive_target(target)
    if refused:
        return {
            "ok": False,
            "alert": True,
            "summary": cap_summary("archive-diff refuses lib and the kit root"),
            "data": {
                "reason": refused,
                "target": os.path.abspath(target),
                "proposal": {
                    "kind": "none",
                    "authorized": False,
                    "note": "data-only; archive-diff does not delete",
                },
            },
            "action": "archive-diff",
        }

    base = os.path.abspath(target)
    py_rows: list[dict[str, Any]] = []
    siblings: list[dict[str, Any]] = []
    for path in iter_tree_files(base):
        if path.lower().endswith(".py"):
            py_rows.append(_file_row(base, path, "py"))
        else:
            siblings.append(_file_row(base, path, "sibling"))

    absent = [r for r in py_rows if r["relation"] == "absent"]
    newer = [r for r in py_rows if r["relation"] == "archive_newer"]
    superseded = [r for r in py_rows if r["relation"] == "superseded"]
    identical = [r for r in py_rows if r["relation"] == "identical"]
    larger = [
        r
        for r in superseded
        if isinstance(r["archive_bytes"], int)
        and isinstance(r["counterpart_bytes"], int)
        and r["archive_bytes"] > r["counterpart_bytes"]
    ]
    blockers = ["absent_in_lib:%s" % r["rel"] for r in absent]
    blockers += ["archive_newer:%s" % r["rel"] for r in newer]
    remove_candidate = bool(py_rows) and not blockers

    reasons: list[str] = []
    if not py_rows:
        reasons.append("no_py")
    elif remove_candidate:
        reasons.append("py counterparts present and none stamp-newer than lib or root")
    else:
        reasons.extend(blockers[:12])
    if larger:
        reasons.append("larger_than_lib:%d" % len(larger))
    if siblings:
        shown = ",".join(r["rel"] for r in siblings[:5])
        reasons.append("noted_sibling:%s" % shown)
    reasons.append(
        "age is freshness-gate --action check --dir <archive> --config freshness.json; "
        "combined dogfood is verdict refresh AND remove_candidate; this action does not delete"
    )

    rank = {"absent": 0, "archive_newer": 1, "superseded": 2, "identical": 3}
    py_rows.sort(key=lambda r: (rank.get(str(r["relation"]), 9), str(r["rel"]).lower()))
    proposal_kind = "delete" if remove_candidate else "none"
    summary = "archive-diff remove_candidate=%s py=%d absent=%d newer=%d" % (
        str(remove_candidate).lower(),
        len(py_rows),
        len(absent),
        len(newer),
    )
    return {
        "ok": True,
        "alert": not remove_candidate,
        "summary": cap_summary(summary),
        "data": {
            "archive": base,
            "lib": os.path.abspath(lib_dir()),
            "remove_candidate": remove_candidate,
            "reasons": reasons[:16],
            "py_total": len(py_rows),
            "identical_count": len(identical),
            "superseded_count": len(superseded),
            "absent_count": len(absent),
            "archive_newer_count": len(newer),
            "hash_differ_count": sum(1 for r in py_rows if r["hash_differ"]),
            "larger_than_lib_count": len(larger),
            "sibling_total": len(siblings),
            "files": py_rows[:ROW_CAP],
            "siblings": siblings[:20],
            "truncated": len(py_rows) > ROW_CAP or len(siblings) > 20,
            "proposal": {
                "kind": proposal_kind,
                "target": base,
                "authorized": False,
                "needs_judgment": True,
                "note": "data-only; archive-diff does not delete or move files",
            },
            "compose": {
                "age_owner": "freshness-gate",
                "age_action": "check",
                "combined_rule": "freshness data.verdict refresh AND data.alert true AND remove_candidate true",
            },
        },
        "action": "archive-diff",
    }


ACTIONS = {
    "scan": action_scan,
    "check": action_check,
    "stamp": action_stamp,
    "pack": action_pack,
    "archive-diff": action_archive_diff,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit(
            {"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False},
            2,
        )
    if argv[1] == "--manifest":
        return emit(MANIFEST)
    if argv[1] == "--list-actions":
        return emit(
            {
                "ok": True,
                "actions": MANIFEST["actions"],
                "default_action": MANIFEST["default_action"],
            }
        )
    if argv[1] == "--action":
        if len(argv) < 3:
            return emit({"ok": False, "reason": "missing_action", "alert": False}, 2)
        name = argv[2]
        fn = ACTIONS.get(name)
        if fn is None:
            return emit({"ok": False, "reason": "unknown_action", "action": name, "alert": False}, 2)
        obj = fn()
        code = 0
        if name == "archive-diff" and isinstance(obj, dict) and obj.get("ok") is not True:
            code = 2
        return emit(obj, code)
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest", "alert": False}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
