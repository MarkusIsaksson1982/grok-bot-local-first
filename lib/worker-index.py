#!/usr/bin/env python3
"""Sibling Worker Manifest Index — local-first Grok Bot worker (stdlib only, read-only)."""
import argparse, json, os, sys, subprocess, glob, time, tempfile, hashlib

CACHE_PREFIX = ".worker-index.cache."
DEFAULT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _cache_path(root):
    h = hashlib.sha1(root.encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), CACHE_PREFIX + h + ".json")


def _emit(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.exit(0)


def _err(m):
    _emit({"ok": False, "alert": True, "summary": m, "data": {}, "action": "error"})


def _self():
    return os.path.abspath(sys.argv[0])


def _siblings(root):
    selfp = _self()
    out = []
    for p in sorted(glob.glob(os.path.join(root, "*.py"))):
        ap = os.path.abspath(p)
        bn = os.path.basename(p)
        if ap == selfp or bn.startswith("_") or bn.startswith("__"):
            continue
        out.append(p)
    return out


def _run_manifest(p):
    try:
        r = subprocess.run([sys.executable, p, "--manifest"],
                           capture_output=True, text=True, timeout=30)
        for ln in reversed(r.stdout.splitlines()):
            ln = ln.strip()
            if ln:
                return json.loads(ln)
    except Exception:
        return None
    return None


def _build(root):
    items, broken = [], []
    for p in _siblings(root):
        m = _run_manifest(p)
        if not isinstance(m, dict) or not m.get("ok"):
            broken.append(os.path.basename(p))
            continue
        items.append({"id": m.get("id"), "title": m.get("title"),
                      "priority": m.get("priority"),
                      "default_action": m.get("default_action"),
                      "keywords": m.get("keywords", [])})
    ids = [i["id"] for i in items]
    collisions = sorted({x for x in ids if ids.count(x) > 1})
    return items, broken, collisions


def _load_cache(root):
    try:
        with open(_cache_path(root)) as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(root, items):
    try:
        with open(_cache_path(root), "w") as f:
            json.dump({"ts": int(time.time()), "items": items}, f)
    except Exception:
        pass


def _cap(lst, n=5):
    return lst[:n], len(lst)


def manifest():
    return {
        "ok": True,
        "id": "worker-index",
        "title": "Sibling Worker Manifest Index",
        "source": "local",
        "priority": 92,
        "keywords": ["discovery", "index", "cache", "meta"],
        "default_action": "collect",
        "actions": [
            {"name": "collect", "use_bot": False,
             "summary": "Rebuild index; return counts indexed/changed/broken"},
            {"name": "diff", "use_bot": False,
             "summary": "Which worker ids added/removed/changed since last build"},
            {"name": "pack", "use_bot": True,
             "summary": "Review pack only on parse failure or id collision"},
        ],
    }


def do_action(args, name):
    root = os.path.abspath(args.root or DEFAULT_ROOT)
    items, broken, collisions = _build(root)
    prev = _load_cache(root)
    prev_ids = {i.get("id") for i in (prev or {}).get("items", [])}
    cur_ids = {i["id"] for i in items}
    changed = len(prev_ids ^ cur_ids)

    if name == "collect":
        _save_cache(root, items)
        sample, total = _cap([{"id": i["id"], "title": i["title"],
                               "priority": i["priority"]} for i in items])
        _emit({"ok": True, "alert": bool(broken or collisions),
               "summary": f"indexed {len(items)} workers, {len(broken)} broken, {changed} changed",
               "data": {"counts": {"indexed": len(items), "broken": len(broken),
                                   "changed": changed},
                        "workers": sample, "workers_total": total},
               "action": "collect"})

    elif name == "diff":
        added = sorted(cur_ids - prev_ids)
        removed = sorted(prev_ids - cur_ids)
        a, at = _cap(added)
        r, rt = _cap(removed)
        _emit({"ok": True, "alert": bool(added or removed),
               "summary": f"{len(added)} added, {len(removed)} removed vs cache",
               "data": {"added": a, "added_total": at, "removed": r,
                        "removed_total": rt, "current_total": len(items)},
               "action": "diff"})

    elif name == "pack":
        issues = [{"file": b, "issue": "manifest parse failed"} for b in broken]
        for c in collisions:
            issues.append({"id": c, "issue": "id collision"})
        iss, ist = _cap(issues)
        _emit({"ok": True, "alert": bool(issues),
               "summary": (f"{len(issues)} index issue(s) for review"
                           if issues else "index clean"),
               "data": {"issues": iss, "issues_total": ist,
                        "needs_review": bool(issues)},
               "action": "pack"})

    else:
        _err(f"unknown action: {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--list-actions", action="store_true")
    ap.add_argument("--action")
    ap.add_argument("--root", default=None)
    args = ap.parse_args()
    if args.manifest:
        _emit(manifest())
    if args.list_actions:
        print(" ".join(a["name"] for a in manifest()["actions"]))
        return
    if args.action:
        do_action(args, args.action)


if __name__ == "__main__":
    main()
