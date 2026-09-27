#!/usr/bin/env python3
"""Drop Folder Triage Meta-Worker — intake over the kit drop/ folder (stdlib only).

Progressive disclosure L0-L3 and a temp snapshot cache. Discovers new/changed
files under drop/ (never lib/). Compose reads sibling workers via --manifest
only. Does not write sibling sources, does not stamp, and does not run
faucet or transfer.

Ingest and stamp proposals, when shown, live under data.proposed with
authorized false. Stamp runs only after a promote signature. Ingest is not
how a source id is refreshed; ingest forces enabled true.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any

CURRENT_FALLBACK = "0.61.0"
LIST_CAP = 5
ARCHIVE_LIST_CAP = 2
SKIP_DIRS = frozenset({
    "__pycache__",
    ".git",
    "node_modules",
    # Campaign cores + secrets live outside drop intake (see state/DROP-ROUTING.md).
    "projects",
    "workspaces",
    # Manual/cold trees — not automatic drop triage.
    "_cold_archive",
    "_backups",
})
LEVELS = ("L0", "L1", "L2", "L3")

_ASSET_NAME = re.compile(
    r"(?i)(?:wallet-create|wallets\.jsonl|faucet-|wallet|faucet|transfer|\bcdp\b)"
)
_INLINE_SECRET = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
    r"|\bCDP_(?:API_KEY_ID|API_KEY_SECRET|API_KEY_PRIVATE_KEY|WALLET_SECRET)\s*[:=]\s*"
    r"(?!['\"]?(?:REDACTED|YOUR_|placeholder|<|\$\{))",
    re.I,
)
_JSON_SECRET = re.compile(
    r'"(?:privateKey|walletSecret|seedPhrase|cdpApiKeySecret)"\s*:\s*"'
    r'(?!REDACTED|YOUR_|placeholder|<)[^"]{8,}"'
)

MANIFEST = {
    "ok": True,
    "id": "drop-triage",
    "title": "Drop Folder Triage Meta-Worker",
    "source": "grok-4.7-high",
    "origin": {"grok_build": "1.0.41", "os": "windows-11"},
    "priority": 95,
    "keywords": [
        "meta",
        "drop",
        "intake",
        "triage",
        "compose",
        "orchestration",
        "ingest",
        "version-gate",
        "disclosure",
        "asset",
    ],
    "default_action": "d1",
    "asset_meta": {
        "asset_class": "none",
        "capabilities": [],
        "allowed_networks": [],
        "max_transfer_eth": 0,
        "secrets": "env_path_only",
    },
    "verified_grok_bot": "0.61.0",
    "verified_at": "2026-09-27",
    "actions": [
        {
            "name": "d1",
            "use_bot": False,
            "summary": "L0 default: drop snapshot counts+alert. Prefer d1; climb d2 only if new|changed. --level L1 for path lists",
        },
        {
            "name": "d2",
            "use_bot": False,
            "summary": "Compose via sibling --manifest; L2 adds per-row asset_flags; climb pack only if needs_judgment or asset alert",
        },
        {
            "name": "pack",
            "use_bot": True,
            "summary": "L3 integrate/rework/discard pack when judgment or asset alert is needed",
        },
        {
            "name": "review-pack",
            "use_bot": True,
            "summary": "L3 separate review pack for integrate/rework/discard judgment",
        },
    ],
}


def emit(obj: object, code: int = 0) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n")
    return code


def cap_summary(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


def _cap(lst: list[Any], n: int = LIST_CAP) -> tuple[list[Any], int]:
    return lst[:n], len(lst)


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


def parse_kv_flags(argv: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--drop", "--root", "--lib", "--level", "--scope") and i + 1 < len(argv):
            out[a[2:]] = argv[i + 1]
            i += 2
            continue
        if a.startswith("--drop="):
            out["drop"] = a.split("=", 1)[1]
            i += 1
            continue
        if a.startswith("--root="):
            out["root"] = a.split("=", 1)[1]
            i += 1
            continue
        if a.startswith("--level="):
            out["level"] = a.split("=", 1)[1]
            i += 1
            continue
        if a.startswith("--scope="):
            out["scope"] = a.split("=", 1)[1]
            i += 1
            continue
        i += 1
    return out


def _script_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def root_dir(flags: dict[str, str] | None = None) -> str:
    flags = flags or {}
    if flags.get("root"):
        return os.path.abspath(flags["root"])
    here = _script_dir()
    base = os.path.basename(here).lower()
    parent = os.path.dirname(here)
    # drop\\adapt\\drop-triage.py or drop\\drop-triage.py
    if base == "adapt" and os.path.basename(parent).lower() == "drop":
        return os.path.dirname(parent)
    if base == "drop":
        return parent
    if os.path.isdir(os.path.join(here, "drop")):
        return here
    if os.path.isdir(os.path.join(parent, "drop")):
        return parent
    # lib/<worker>.py or lib/<lane>/<worker>.py, whether or not drop/ exists yet
    if base == "lib":
        return parent
    if os.path.basename(parent).lower() == "lib":
        return os.path.dirname(parent)
    env = os.environ.get("GROKKIT_ROOT")
    if env:
        return os.path.abspath(env)
    return here


def drop_dir(flags: dict[str, str] | None = None) -> str:
    flags = flags or {}
    env = os.environ.get("DROP_DIR")
    if flags.get("drop"):
        return os.path.abspath(flags["drop"])
    if env:
        return os.path.abspath(env)
    return os.path.join(root_dir(flags), "drop")


def sources_path(flags: dict[str, str] | None = None) -> str:
    return os.path.join(root_dir(flags), "sources.json")


def current_bot(flags: dict[str, str] | None = None) -> str:
    path = sources_path(flags)
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            v = obj.get("current_grok_bot")
            if isinstance(v, str) and v.strip():
                return v.strip()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return CURRENT_FALLBACK


def win_drop_rel(rel: str) -> str:
    """Ingest path using forward slashes. Windows and Python accept them."""
    rel = rel.replace("\\", "/").lstrip("/")
    return "drop/" + rel


def _posix(rel: str) -> str:
    return rel.replace("\\", "/")


def _scope_of(flags: dict[str, str] | None) -> str:
    s = ((flags or {}).get("scope") or "all").strip().lower()
    if s in ("findings", "archive", "all"):
        return s
    return "all"


def _scope_ok(rel: str, scope: str) -> bool:
    if not scope or scope == "all":
        p = _posix(rel).lower()
        # Belt: never treat campaign/secret trees as intake even if a walk leaks them.
        if p.startswith("projects/") or p.startswith("workspaces/"):
            return False
        if p.startswith("_cold_archive/") or p.startswith("_backups/") or p.startswith("_archive/"):
            return False
        return True
    p = _posix(rel).lower()
    if scope == "findings":
        return p.startswith("findings/")
    if scope == "archive":
        return p.startswith("archive/") or p.startswith("_archive/")
    return True


def _zone(rel: str) -> int:
    p = _posix(rel).lower()
    if p.startswith("findings/"):
        return 0
    if p.startswith("archive/") or p.startswith("_archive/"):
        return 2
    return 1


def requested_level(flags: dict[str, str] | None, default: str) -> str:
    raw = ((flags or {}).get("level") or default).strip().upper()
    if raw in LEVELS:
        return raw
    return default


def disclosure(
    level: str,
    next_action: str | None,
    truncated: bool,
    totals: dict[str, Any],
) -> dict[str, Any]:
    return {
        "level": level,
        "next": next_action,
        "truncated": bool(truncated),
        "totals": totals,
    }


def _snapshot(drop: str, scope: str = "all") -> dict[str, list[int]]:
    snap: dict[str, list[int]] = {}
    if not os.path.isdir(drop):
        return snap
    for dp, dirs, files in os.walk(drop):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(".bak") or fn.startswith(".drop-triage.cache."):
                continue
            fp = os.path.join(dp, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            rel = os.path.relpath(fp, drop)
            if rel.startswith(".."):
                continue
            key = _posix(rel)
            if not _scope_ok(key, scope):
                continue
            snap[key] = [int(st.st_size), int(st.st_mtime)]
    return snap


def _cache_path(drop: str) -> str:
    h = hashlib.sha1(os.path.abspath(drop).encode("utf-8")).hexdigest()[:16]
    # Distinct from lib/drop-triage.py cache so skip-dirs cannot poison live snapshots.
    return os.path.join(tempfile.gettempdir(), ".drop-triage.pd-iterate.cache." + h + ".json")


def _load_cache(drop: str) -> dict[str, list[int]] | None:
    """None means first-seen (no prior snapshot)."""
    try:
        with open(_cache_path(drop), encoding="utf-8") as f:
            obj = json.load(f)
        snap = obj.get("snapshot")
        if isinstance(snap, dict):
            return snap
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def _save_cache(drop: str, snap: dict[str, list[int]]) -> None:
    try:
        with open(_cache_path(drop), "w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "snapshot": snap}, f)
    except OSError:
        pass


def _diff(
    prev: dict[str, list[int]], cur: dict[str, list[int]]
) -> tuple[list[str], list[str], list[str]]:
    added = sorted(k for k in cur if k not in prev)
    removed = sorted(k for k in prev if k not in cur)
    changed = sorted(k for k in cur if k in prev and cur[k] != prev[k])
    return added, changed, removed


def last_json_line(text: str) -> Any | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def call_manifest(py_path: str) -> dict[str, Any] | None:
    """Subprocess --manifest only. Never open the worker source body."""
    try:
        proc = subprocess.run(
            [sys.executable, py_path, "--manifest"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    obj = last_json_line(proc.stdout or "")
    if not isinstance(obj, dict) or obj.get("ok") is not True:
        return None
    return obj


def list_drop_py(drop: str, scope: str = "all") -> list[tuple[str, str]]:
    """Return (rel posix, abs path) for .py files under drop, not lib."""
    out: list[tuple[str, str]] = []
    if not os.path.isdir(drop):
        return out
    self_abs = os.path.abspath(__file__)
    for dp, dirs, files in os.walk(drop):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        # never walk into a lib folder if one is nested under drop
        dirs[:] = [d for d in dirs if d.lower() != "lib"]
        for fn in files:
            if not fn.lower().endswith(".py"):
                continue
            if fn.lower() == "grokkit.py":
                continue
            fp = os.path.abspath(os.path.join(dp, fn))
            if fp == self_abs:
                continue
            rel = os.path.relpath(fp, drop).replace("\\", "/")
            if rel.startswith(".."):
                continue
            if not _scope_ok(rel, scope):
                continue
            out.append((rel, fp))
    out.sort(key=lambda x: (_zone(x[0]), x[0].lower()))
    return out


def _name_blob(rel: str) -> str:
    return _posix(rel).lower()


def _path_asset_touch(rel: str) -> bool:
    blob = _name_blob(rel)
    base = os.path.basename(blob)
    if _ASSET_NAME.search(blob):
        return True
    if base.startswith("faucet-") or base.startswith("wallet-create"):
        return True
    return False


def _package_root(drop: str, rel: str) -> str:
    """Directory that holds the dropped item, not a nested scripts/ or node_modules/."""
    posix = _posix(rel)
    cur = os.path.join(drop, os.path.dirname(posix).replace("/", os.sep))
    if os.path.isfile(os.path.join(drop, posix.replace("/", os.sep))):
        pass
    elif os.path.isdir(os.path.join(drop, posix.replace("/", os.sep))):
        cur = os.path.join(drop, posix.replace("/", os.sep))
    drop_abs = os.path.abspath(drop)
    last = cur if os.path.isdir(cur) else os.path.dirname(cur)
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    while True:
        name = os.path.basename(os.path.abspath(cur)).lower()
        if os.path.abspath(cur) == drop_abs:
            return last
        if name in ("findings", "archive", "inbox", "packages"):
            return last
        if date_re.match(name):
            return last
        last = cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return last
        cur = parent


def _load_asset_meta(dirpath: str, man: dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(man, dict):
        am = man.get("asset_meta")
        if isinstance(am, dict):
            return am
    cur = dirpath
    for _ in range(8):
        p = os.path.join(cur, "asset_meta.json")
        try:
            with open(p, encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                return obj
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def _collect_nearby_names(root: str, max_files: int = 80) -> list[str]:
    hits: list[str] = []
    if not os.path.isdir(root):
        return hits
    n = 0
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d.lower() != "lib"]
        depth = os.path.relpath(dp, root).replace("\\", "/").count("/")
        if os.path.relpath(dp, root) not in (".",) and depth > 3:
            dirs[:] = []
        for fn in files:
            n += 1
            if n > max_files:
                return hits
            rel = os.path.relpath(os.path.join(dp, fn), root).replace("\\", "/")
            hits.append(rel)
    return hits


def _is_env_secret_path(rel: str) -> bool:
    p = _posix(rel).lower()
    base = os.path.basename(p)
    if base in (".env", ".env.example") or base.endswith(".env"):
        return True
    if "/secrets/" in ("/" + p) or p.startswith("secrets/"):
        return True
    return False


def _cheap_secret_leak(root: str, max_files: int = 20, max_bytes: int = 65536) -> bool:
    """Peek small nearby files only. Never execute. Skip node_modules/.git/__pycache__.

    Env-path files (.env, a secrets/ directory) are not inline leaks.
    """
    if not os.path.isdir(root):
        return False
    n = 0
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d.lower() != "lib"]
        for fn in files:
            if fn.endswith((".map", ".png", ".jpg", ".woff", ".dll", ".so")):
                continue
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            if _is_env_secret_path(rel):
                continue
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            if sz > max_bytes or sz <= 0:
                continue
            n += 1
            if n > max_files:
                return False
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read(max_bytes)
            except OSError:
                continue
            if _INLINE_SECRET.search(text) or _JSON_SECRET.search(text):
                return True
    return False


def _looks_like_test_network(name: str) -> bool:
    """True when a network name says test and does not say main."""
    text = name.lower()
    if "main" in text:
        return False
    return "test" in text


def compute_asset_flags(
    drop: str,
    rel: str,
    man: dict[str, Any] | None = None,
    read_bodies: bool = False,
) -> dict[str, bool]:
    posix = _posix(rel)
    wdir = _package_root(drop, posix)
    names = [posix]
    if os.path.isdir(wdir):
        names.extend(_collect_nearby_names(wdir))
    blob = " ".join(_posix(n).lower() for n in names)
    touch = bool(_ASSET_NAME.search(blob))
    meta = _load_asset_meta(wdir, man)
    needs = bool(touch and meta is None)
    testnet = False
    if touch and isinstance(meta, dict):
        nets = meta.get("allowed_networks") or []
        if isinstance(nets, list) and nets:
            testnet = all(_looks_like_test_network(str(x)) for x in nets)
    elif touch:
        if "testnet" in blob and "mainnet" not in blob:
            testnet = True
    env_only = False
    if touch:
        env_path = any(_is_env_secret_path(n) for n in names)
        leak = _cheap_secret_leak(wdir) if read_bodies else False
        env_only = bool(env_path and not leak)
    return {
        "asset_touch": bool(touch),
        "testnet_only": bool(testnet),
        "needs_asset_meta": bool(needs),
        "secrets_env_only": bool(env_only),
    }


def empty_flags() -> dict[str, bool]:
    return {
        "asset_touch": False,
        "testnet_only": False,
        "needs_asset_meta": False,
        "secrets_env_only": False,
    }


def asset_alert_of(flags: dict[str, bool]) -> bool:
    if flags.get("needs_asset_meta"):
        return True
    if flags.get("asset_touch") and not flags.get("secrets_env_only"):
        return True
    return False


def _cap_pref_rows(rows: list[dict[str, Any]], n: int = LIST_CAP) -> list[dict[str, Any]]:
    def key(r: dict[str, Any]) -> tuple[int, int, str]:
        rel = r.get("file") or ""
        flags = r.get("asset_flags") or {}
        touch = 0 if flags.get("asset_touch") else 1
        return (touch, _zone(rel), _posix(rel).lower())

    ordered = sorted(rows, key=key)
    out: list[dict[str, Any]] = []
    arch = 0
    for r in ordered:
        is_arch = _zone(r.get("file") or "") == 2
        if is_arch and arch >= ARCHIVE_LIST_CAP:
            continue
        out.append(r)
        if is_arch:
            arch += 1
        if len(out) >= n:
            break
    return out


def _cap_pref_paths(paths: list[str], n: int = LIST_CAP) -> tuple[list[str], int]:
    rows = [{"file": p, "asset_flags": {"asset_touch": _path_asset_touch(p)}} for p in paths]
    capped = _cap_pref_rows(rows, n)
    return [r["file"] for r in capped], len(paths)


def classify_drop_workers(
    drop: str, flags: dict[str, str] | None = None
) -> dict[str, Any]:
    # source, verified_grok_bot, and origin say where a worker was last iterated.
    # They are not instructions for where that worker must run.
    current = current_bot(flags)
    scope = _scope_of(flags)
    rows: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    for rel, path in list_drop_py(drop, scope):
        man = call_manifest(path)
        af = compute_asset_flags(drop, rel, man=man, read_bodies=True)
        if man is None:
            broken.append({"file": rel, "hint": "manifest_failed", "asset_flags": af})
            continue
        wid = man.get("id")
        if not isinstance(wid, str) or not wid.strip():
            wid = os.path.splitext(os.path.basename(rel))[0]
        src = man.get("source")
        src_s = src.strip() if isinstance(src, str) and src.strip() else "other"
        stamp = man.get("verified_grok_bot")
        stamp_s = stamp.strip() if isinstance(stamp, str) and stamp.strip() else None
        rows.append(
            {
                "id": wid,
                "file": rel,
                "source": src_s,
                "verified_grok_bot": stamp_s,
                "verified_at": man.get("verified_at")
                if isinstance(man.get("verified_at"), str)
                else None,
                "stamp_lag": is_outdated(stamp_s, current),
                "asset_flags": af,
            }
        )
    ingest_ids = [r["id"] for r in rows]
    stamp_ids = [r["id"] for r in rows if r["stamp_lag"]]
    any_alert = any(asset_alert_of(r["asset_flags"]) for r in rows) or any(
        asset_alert_of(b["asset_flags"]) for b in broken
    )
    any_touch = any(r["asset_flags"]["asset_touch"] for r in rows) or any(
        b["asset_flags"]["asset_touch"] for b in broken
    )
    return {
        "current_grok_bot": current,
        "workers": rows,
        "broken": broken,
        "ingest_ids": ingest_ids,
        "stamp_ids": stamp_ids,
        "asset_alert": any_alert,
        "asset_touch": any_touch,
    }


PROPOSED_TEXT = (
    "Stamp only after a promote signature. "
    "Ingest is not how a source id is refreshed; ingest forces enabled: true. "
    "Never stamp drop copies. Never run faucet or transfer. "
    "An asset-name hit means stop."
)


def proposed_writes(cls: dict[str, Any], cap: int = 12) -> dict[str, Any]:
    """Data-only proposals. Not executed here. authorized stays false."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(kind: str, argv: list[str], note: str) -> None:
        key = kind + "\0" + "\0".join(argv)
        if key in seen:
            return
        seen.add(key)
        items.append(
            {"kind": kind, "argv": argv, "note": note, "authorized": False}
        )

    preferred = _cap_pref_rows(list(cls.get("workers") or []), LIST_CAP)
    for r in preferred:
        flags = r.get("asset_flags") or empty_flags()
        if flags.get("needs_asset_meta") or asset_alert_of(flags):
            add(
                "stop",
                ["python", "grokkit.py", "action", "drop-triage", "pack"],
                "asset-name hit means stop; integrate only after flags quiet",
            )
            continue
        rel = win_drop_rel(r["file"])
        src = r.get("source") or "other"
        add(
            "ingest",
            ["python", "grokkit.py", "ingest", rel, "--source", src],
            "not a source-id refresh; ingest forces enabled: true",
        )
    add(
        "validate",
        ["python", "grokkit.py", "action", "audit-harness", "validate"],
        "gate only; this JSON does not run it",
    )
    ids = cls.get("stamp_ids") or cls.get("ingest_ids") or []
    if ids:
        add(
            "stamp",
            [
                "python",
                "grokkit.py",
                "action",
                "version-gate",
                "stamp",
                "--ids",
                ",".join(ids[:8]),
            ],
            "stamp only after a promote signature; version-gate owns stamp",
        )
    add(
        "check",
        ["python", "grokkit.py", "action", "version-gate", "check"],
        "read-only check; stamp only after a promote signature",
    )
    return {
        "authorized": False,
        "text": PROPOSED_TEXT,
        "count": len(items),
        "items": items[:cap],
    }


def empty_proposed() -> dict[str, Any]:
    return {"authorized": False, "text": PROPOSED_TEXT, "count": 0, "items": []}


def snapshot_state(drop: str, scope: str = "all") -> dict[str, Any]:
    missing = not os.path.isdir(drop)
    cur = _snapshot(drop, scope)
    prev = _load_cache(drop)
    first_seen = prev is None
    if prev is None:
        prev = {}
    added, changed, removed = _diff(prev, cur)
    _save_cache(drop, cur)

    # d1 never escalates on mere discovery. verdict is discover-only: new|changed|unchanged.
    if first_seen or (added and not changed and not removed):
        verdict = "new"
    elif changed or removed:
        verdict = "changed"
    else:
        verdict = "unchanged"

    a, at = _cap_pref_paths(added)
    c, ct = _cap_pref_paths(changed)
    r, rt = _cap_pref_paths(removed)
    return {
        "drop": drop,
        "missing": missing,
        "first_seen": first_seen,
        "verdict": verdict,
        "added": a,
        "added_total": at,
        "changed": c,
        "changed_total": ct,
        "removed": r,
        "removed_total": rt,
        "total": len(cur),
        "delta": {"added": at, "changed": ct, "removed": rt},
        "added_all": added,
        "changed_all": changed,
    }


def _d1_path_flags(drop: str, paths: list[str]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for p in paths:
        if not _path_asset_touch(p):
            continue
        hits.append(
            {
                "path": p,
                "asset_flags": compute_asset_flags(drop, p, man=None, read_bodies=False),
            }
        )
        if len(hits) >= LIST_CAP:
            break
    return hits


def action_d1(flags: dict[str, str]) -> dict[str, Any]:
    drop = drop_dir(flags)
    scope = _scope_of(flags)
    st = snapshot_state(drop, scope)
    level = requested_level(flags, "L0")
    if level == "L3":
        level = "L2"
    paths_for_flags = st.get("added_all") or st["added"]
    paths_for_flags = list(paths_for_flags) + list(st.get("changed_all") or st["changed"])
    asset_hits = _d1_path_flags(drop, paths_for_flags)
    agg = empty_flags()
    for h in asset_hits:
        f = h["asset_flags"]
        for k in agg:
            agg[k] = bool(agg[k] or f.get(k))
    a_alert = any(asset_alert_of(h["asset_flags"]) for h in asset_hits)
    next_action: str | None = None
    if st["verdict"] in ("new", "changed") or a_alert:
        next_action = "d2"
    truncated = (st["added_total"] + st["changed_total"] + st["removed_total"]) > LIST_CAP
    if level == "L0":
        truncated = truncated or (
            st["added_total"] + st["changed_total"] + st["removed_total"] > 0
        )
    totals = {
        "total": st["total"],
        "added": st["added_total"],
        "changed": st["changed_total"],
        "removed": st["removed_total"],
        "asset_hits": len(asset_hits),
    }
    summary = "drop %s: +%d ~%d -%d (total %d); next=%s" % (
        st["verdict"],
        st["added_total"],
        st["changed_total"],
        st["removed_total"],
        st["total"],
        next_action or "null",
    )
    if a_alert:
        summary += " asset_alert"
    data: dict[str, Any] = {
        "drop_dir": drop,
        "drop_missing": st["missing"],
        "first_seen": st["first_seen"],
        "verdict": st["verdict"],
        "counts": {
            "total": st["total"],
            "added": st["added_total"],
            "changed": st["changed_total"],
            "removed": st["removed_total"],
        },
        "added_total": st["added_total"],
        "changed_total": st["changed_total"],
        "removed_total": st["removed_total"],
        "scope": scope,
        "climb": "prefer d1; d2 only if new|changed or asset_alert; pack only if needs_judgment",
        "proposed": empty_proposed(),
    }
    if level in ("L1", "L2"):
        data["added"] = st["added"]
        data["changed"] = st["changed"]
        data["removed"] = st["removed"]
    if level == "L2" and asset_hits:
        data["asset_hits"] = asset_hits
        data["asset_flags"] = agg
    data["disclosure"] = disclosure(level, next_action, truncated, totals)
    return {
        "ok": True,
        "alert": bool(a_alert),
        "summary": cap_summary(summary),
        "data": data,
        "action": "d1",
    }


def _slim_worker(r: dict[str, Any], with_flags: bool) -> dict[str, Any]:
    row = {
        "id": r["id"],
        "file": r["file"],
        "source": r["source"],
        "verified_grok_bot": r["verified_grok_bot"] or "missing",
        "stamp_lag": r["stamp_lag"],
    }
    if with_flags:
        row["asset_flags"] = r.get("asset_flags") or empty_flags()
    return row


def _slim_broken(b: dict[str, Any], with_flags: bool) -> dict[str, Any]:
    row = {"file": b["file"], "hint": b.get("hint") or "manifest_failed"}
    if with_flags:
        row["asset_flags"] = b.get("asset_flags") or empty_flags()
    return row


def action_d2(flags: dict[str, str]) -> dict[str, Any]:
    drop = drop_dir(flags)
    scope = _scope_of(flags)
    st = snapshot_state(drop, scope)
    cls = classify_drop_workers(drop, flags)
    proposed = proposed_writes(cls)
    with_flags_rows = _cap_pref_rows(cls["workers"], LIST_CAP)
    with_flags_broken = _cap_pref_rows(cls["broken"], LIST_CAP)
    pending = len(cls["workers"]) + len(cls["broken"])
    needs = pending > 0 or bool(cls.get("asset_alert"))
    if st["missing"]:
        verdict = "missing"
        needs = False
    elif pending == 0 and not cls.get("asset_alert"):
        verdict = "pass"
    elif cls["broken"] or cls.get("asset_alert"):
        verdict = "rework" if cls["broken"] else "ingest"
        if cls.get("asset_alert"):
            verdict = "rework"
    else:
        verdict = "ingest"
    level = requested_level(flags, "L1")
    if cls.get("asset_touch") and level in ("L0", "L1"):
        level = "L2"
    if level == "L3":
        level = "L2"
    next_action: str | None = "pack" if needs else None
    truncated = len(cls["workers"]) > len(with_flags_rows) or len(cls["broken"]) > len(
        with_flags_broken
    )
    if level == "L0":
        truncated = truncated or pending > 0
    totals = {
        "workers": len(cls["workers"]),
        "broken": len(cls["broken"]),
        "stamp_lag": len(cls["stamp_ids"]),
        "asset_touch": int(bool(cls.get("asset_touch"))),
        "asset_alert": int(bool(cls.get("asset_alert"))),
    }
    summary = "compose %s workers=%d broken=%d proposals=%d; next=%s" % (
        verdict,
        len(cls["workers"]),
        len(cls["broken"]),
        proposed["count"],
        next_action or "null",
    )
    data: dict[str, Any] = {
        "drop_dir": drop,
        "drop_missing": st["missing"],
        "verdict": verdict,
        "needs_judgment": needs,
        "asset_alert": bool(cls.get("asset_alert")),
        "current_grok_bot": cls["current_grok_bot"],
        "counts": {
            "workers": len(cls["workers"]),
            "broken": len(cls["broken"]),
            "stamp_lag": len(cls["stamp_ids"]),
        },
        "broken_total": len(cls["broken"]),
        "stamp_owner": "version-gate",
        "drop_delta": st["delta"],
        "scope": scope,
        "climb": "prefer d1; pack only if needs_judgment or asset_alert; never stamp drop; never faucet/transfer",
        "proposed": {
            "authorized": False,
            "text": PROPOSED_TEXT,
            "count": proposed["count"],
            "items": [],
        },
    }
    if level in ("L1", "L2"):
        show_flags = level == "L2"
        data["workers"] = [_slim_worker(r, show_flags) for r in with_flags_rows]
        data["broken"] = [_slim_broken(b, show_flags) for b in with_flags_broken]
        data["proposed"] = proposed
    data["disclosure"] = disclosure(level, next_action, truncated, totals)
    return {
        "ok": True,
        "alert": bool(needs),
        "summary": cap_summary(summary),
        "data": data,
        "action": "d2",
    }


def _judgment_pack(flags: dict[str, str], action_name: str) -> dict[str, Any]:
    drop = drop_dir(flags)
    cls = classify_drop_workers(drop, flags)
    proposed = proposed_writes(cls, 6)
    n_ok = len(cls["workers"])
    n_bad = len(cls["broken"])
    needs = (n_ok + n_bad) > 0 or bool(cls.get("asset_alert"))
    totals = {
        "workers": n_ok,
        "broken": n_bad,
        "asset_alert": int(bool(cls.get("asset_alert"))),
    }
    if not needs:
        return {
            "ok": True,
            "alert": False,
            "summary": cap_summary("drop clean; no judgment needed; next=null"),
            "data": {
                "needs_judgment": False,
                "verdict": "quiet",
                "choices": ["integrate", "rework", "discard"],
                "climb": "prefer d1; pack is L3 only",
                "proposed": empty_proposed(),
                "disclosure": disclosure("L3", None, False, totals),
            },
            "action": action_name,
        }
    verdict = "rework" if (n_bad and not n_ok) or cls.get("asset_alert") else "integrate"
    preferred = _cap_pref_rows(cls["workers"], LIST_CAP)
    broken_p = _cap_pref_rows(cls["broken"], LIST_CAP)
    ids = [r["id"] for r in preferred]
    files = [r["file"] for r in preferred]
    files += [b["file"] for b in broken_p]
    files = files[:LIST_CAP]
    slim = [
        {
            "id": r["id"],
            "file": r["file"],
            "source": r["source"],
            "stamp": r["verified_grok_bot"] or "missing",
            "asset_flags": r.get("asset_flags") or empty_flags(),
        }
        for r in preferred
    ]
    truncated = n_ok > len(preferred) or n_bad > len(broken_p)
    summary = "review pack ready: verdict=%s ids=%d broken=%d; next=null" % (
        verdict,
        n_ok,
        n_bad,
    )
    notes = (
        "Stamp only after a promote signature. "
        "Ingest is not how a source id is refreshed; ingest forces enabled: true. "
        "Never stamp drop copies. Never faucet or transfer. Climb was d1 then d2 then pack."
    )
    if cls.get("asset_alert"):
        notes += "; asset_alert: needs_asset_meta or inline-secret path"
    return {
        "ok": True,
        "alert": True,
        "summary": cap_summary(summary),
        "data": {
            "needs_judgment": True,
            "verdict": verdict,
            "choices": ["integrate", "rework", "discard"],
            "current_grok_bot": cls["current_grok_bot"],
            "ids": ids,
            "files": files,
            "candidates": slim,
            "broken_total": n_bad,
            "asset_alert": bool(cls.get("asset_alert")),
            "proposed": proposed,
            "stamp_owner": "version-gate",
            "notes": notes,
            "disclosure": disclosure("L3", None, truncated, totals),
        },
        "action": action_name,
    }


def action_pack(flags: dict[str, str]) -> dict[str, Any]:
    return _judgment_pack(flags, "pack")


def action_review_pack(flags: dict[str, str]) -> dict[str, Any]:
    return _judgment_pack(flags, "review-pack")


ACTIONS = {
    "d1": action_d1,
    "d2": action_d2,
    "pack": action_pack,
    "review-pack": action_review_pack,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit(
            {
                "ok": False,
                "reason": "no_flags",
                "hint": "--manifest",
                "alert": False,
            },
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
            return emit(
                {"ok": False, "reason": "missing_action", "alert": False},
                2,
            )
        name = argv[2]
        fn = ACTIONS.get(name)
        if fn is None:
            return emit(
                {
                    "ok": False,
                    "reason": "unknown_action",
                    "action": name,
                    "alert": False,
                },
                2,
            )
        flags = parse_kv_flags(argv[3:])
        return emit(fn(flags))
    return emit(
        {
            "ok": False,
            "reason": "unknown_flag",
            "hint": "--manifest",
            "alert": False,
        },
        2,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
