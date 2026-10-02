#!/usr/bin/env python3
"""worker-index: index <root>/lib/*.py manifests; optional --also-drop. --root DIR."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any

MANIFEST = {
    "ok": True,
    "id": "worker-index",
    "title": "Sibling Worker Manifest Index",
    "source": "grok-4.7-high",
    "origin": {"grok_build": "1.0.41", "os": "windows-11"},
    "priority": 92,
    "keywords": ["discovery", "index", "cache", "meta"],
    "default_action": "collect",
    "verified_grok_bot": "0.58.0",
    "verified_at": "2026-10-02",
    "actions": [
        {"name": "collect", "use_bot": False, "summary": "Rebuild lib index; counts indexed/broken"},
        {"name": "diff", "use_bot": False, "summary": "Ids added/removed since last cache build"},
        {"name": "pack", "use_bot": True, "summary": "Tiny pack only on broken manifest or same-folder collision"},
    ],
}

CACHE_PREFIX = ".worker-index.cache."
TIMEOUT = 8


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


def cap_summary(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


def parse_root(argv: list[str]) -> str:
    """Match secrets-scan: optional --root DIR, else walked kit root."""
    root = root_dir()
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            root = argv[i + 1]
            i += 2
            continue
        i += 1
    return os.path.abspath(root)


def also_drop(argv: list[str]) -> bool:
    return "--also-drop" in argv


def cache_path(root: str, extra: bool) -> str:
    key = root + ("|drop" if extra else "|lib")
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), CACHE_PREFIX + h + ".json")


def last_json_line(text: str) -> Any | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def folders(root: str, extra: bool) -> list[tuple[str, str]]:
    out = [("lib", os.path.join(root, "lib"))]
    if extra:
        out.append(("drop", os.path.join(root, "drop")))
    return out


def list_candidates(root: str, extra: bool) -> list[tuple[str, str]]:
    """*.py under root/lib and root/lib/<lane>/ (+ optional root/drop)."""
    self_path = os.path.abspath(__file__)
    out: list[tuple[str, str]] = []
    for label, d in folders(root, extra):
        if not os.path.isdir(d):
            continue
        for dirpath, dirnames, filenames in os.walk(d):
            dirnames[:] = [x for x in dirnames if x.lower() != "__pycache__" and not x.startswith(".")]
            rel = os.path.relpath(dirpath, d)
            if rel != "." and os.sep in rel:
                dirnames[:] = []
                continue
            for n in filenames:
                if not n.endswith(".py"):
                    continue
                p = os.path.join(dirpath, n)
                if os.path.abspath(p) == self_path:
                    continue
                out.append((label, p))
    return out


def rel_label(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return os.path.basename(path)


def run_manifest(py_path: str) -> dict[str, Any] | None:
    try:
        proc = subprocess.run(
            [sys.executable, py_path, "--manifest"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=os.path.dirname(py_path) or root_dir(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    obj = last_json_line(proc.stdout or "")
    if not isinstance(obj, dict) or obj.get("ok") is not True:
        return None
    return obj


def build(root: str, extra: bool) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    broken: list[str] = []
    by_folder: dict[str, list[str]] = {}
    for folder_id, path in list_candidates(root, extra):
        label = rel_label(path, root)
        man = run_manifest(path)
        if man is None:
            broken.append(label)
            continue
        wid = man.get("id")
        if not isinstance(wid, str) or not wid:
            wid = os.path.splitext(os.path.basename(path))[0]
        items.append(
            {
                "id": wid,
                "file": label,
                "folder": folder_id,
                "title": man.get("title") if isinstance(man.get("title"), str) else "",
                "priority": man.get("priority"),
                "default_action": man.get("default_action"),
            }
        )
        by_folder.setdefault(folder_id, []).append(wid)

    same_folder: list[str] = []
    for _fid, ids in by_folder.items():
        same_folder.extend(sorted({x for x in ids if ids.count(x) > 1}))
    same_folder = sorted(set(same_folder))

    ids_all = [i["id"] for i in items]
    all_dups = sorted({x for x in ids_all if ids_all.count(x) > 1})
    cross = [i for i in all_dups if i not in same_folder]

    return {
        "items": items,
        "broken": broken,
        "same_folder_collisions": same_folder,
        "cross_folder_duplicates": cross,
    }


def load_cache(root: str, extra: bool) -> dict[str, Any] | None:
    try:
        with open(cache_path(root, extra), encoding="utf-8") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            return obj
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def save_cache(root: str, extra: bool, items: list[dict[str, Any]]) -> None:
    slim = [{"id": i["id"], "file": i["file"]} for i in items]
    try:
        with open(cache_path(root, extra), "w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "items": slim}, f)
    except OSError:
        pass


def action_collect(argv: list[str]) -> dict[str, Any]:
    extra = also_drop(argv)
    root = parse_root(argv)
    b = build(root, extra)
    items, broken = b["items"], b["broken"]
    prev = load_cache(root, extra)
    prev_ids = {i.get("id") for i in (prev or {}).get("items", []) if isinstance(i, dict)}
    cur_ids = {i["id"] for i in items}
    changed = len(prev_ids ^ cur_ids)
    save_cache(root, extra, items)
    sample = [{"id": i["id"], "file": i["file"], "folder": i["folder"]} for i in items[:12]]
    alert = bool(broken or b["same_folder_collisions"])
    return {
        "ok": True,
        "alert": alert,
        "summary": cap_summary(
            "indexed %d workers, %d broken, %d same-folder collision(s); skips self"
            % (len(items), len(broken), len(b["same_folder_collisions"]))
        ),
        "data": {
            "counts": {
                "indexed": len(items),
                "broken": len(broken),
                "same_folder_collisions": len(b["same_folder_collisions"]),
                "cross_folder_duplicates": len(b["cross_folder_duplicates"]),
                "changed": changed,
            },
            "workers": sample,
            "truncated": len(items) > 12,
            "broken": broken[:12],
            "same_folder_collisions": b["same_folder_collisions"][:12],
            "cross_folder_duplicates": b["cross_folder_duplicates"][:12],
            "also_drop": extra,
        },
        "action": "collect",
    }


def action_diff(argv: list[str]) -> dict[str, Any]:
    extra = also_drop(argv)
    root = parse_root(argv)
    b = build(root, extra)
    items = b["items"]
    prev = load_cache(root, extra)
    prev_ids = {i.get("id") for i in (prev or {}).get("items", []) if isinstance(i, dict)}
    cur_ids = {i["id"] for i in items}
    added = sorted(cur_ids - prev_ids)
    removed = sorted(prev_ids - cur_ids)
    return {
        "ok": True,
        "alert": False,
        "summary": cap_summary("%d added, %d removed vs cache" % (len(added), len(removed))),
        "data": {
            "added": added[:12],
            "added_total": len(added),
            "removed": removed[:12],
            "removed_total": len(removed),
            "current_total": len(items),
        },
        "action": "diff",
    }


def action_pack(argv: list[str]) -> dict[str, Any]:
    extra = also_drop(argv)
    b = build(parse_root(argv), extra)
    if not b["broken"] and not b["same_folder_collisions"]:
        return {
            "ok": True,
            "alert": False,
            "summary": cap_summary("no judgment needed"),
            "data": {
                "emit": False,
                "needs_judgment": False,
                "indexed": len(b["items"]),
                "cross_folder_duplicates": b["cross_folder_duplicates"][:12],
            },
            "action": "pack",
        }
    return {
        "ok": True,
        "alert": True,
        "summary": cap_summary(
            "%d broken, %d same-folder collision(s)"
            % (len(b["broken"]), len(b["same_folder_collisions"]))
        ),
        "data": {
            "emit": True,
            "needs_judgment": True,
            "broken": b["broken"][:12],
            "same_folder_collisions": b["same_folder_collisions"][:12],
            "broken_total": len(b["broken"]),
            "same_folder_collision_total": len(b["same_folder_collisions"]),
        },
        "action": "pack",
    }


ACTIONS = {"collect": action_collect, "diff": action_diff, "pack": action_pack}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}, 2)
    primary = None
    i = 1
    while i < len(argv):
        if argv[i] == "--also-drop":
            i += 1
            continue
        if argv[i] == "--root" and i + 1 < len(argv):
            i += 2
            continue
        primary = argv[i]
        break
    if primary is None:
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}, 2)
    if primary == "--manifest":
        return emit(MANIFEST)
    if primary == "--list-actions":
        return emit(
            {"ok": True, "actions": MANIFEST["actions"], "default_action": MANIFEST["default_action"]}
        )
    if primary == "--action":
        name = None
        try:
            idx = argv.index("--action")
        except ValueError:
            idx = -1
        if idx >= 0:
            j = idx + 1
            while j < len(argv):
                if argv[j] == "--also-drop":
                    j += 1
                    continue
                if argv[j] == "--root" and j + 1 < len(argv):
                    j += 2
                    continue
                name = argv[j]
                break
        if not name:
            return emit({"ok": False, "reason": "missing_action", "alert": False}, 2)
        fn = ACTIONS.get(name)
        if fn is None:
            return emit({"ok": False, "reason": "unknown_action", "action": name, "alert": False}, 2)
        return emit(fn(argv))
    if primary == "--also-drop":
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}, 2)
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest", "alert": False}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
