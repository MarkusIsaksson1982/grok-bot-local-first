#!/usr/bin/env python3
"""Context Budget Estimator — local-first Grok Bot worker (stdlib only, read-only)."""
import argparse, json, os, sys, glob

DEFAULT_ROOT = os.getcwd()
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist",
             "build", ".idea", ".vscode", "target"}
EXT_PRIORITY = {".md": 3, ".py": 3, ".txt": 2, ".json": 2, ".yaml": 2,
                ".yml": 2, ".toml": 1, ".cfg": 1, ".ini": 1, ".log": 1,
                ".csv": 1, ".xml": 1, ".html": 1, ".css": 1}
MAX_FILE = 5_000_000


def _emit(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.exit(0)


def _err(m):
    _emit({"ok": False, "alert": True, "summary": m, "data": {}, "action": "error"})


def _cap(lst, n=5):
    return lst[:n], len(lst)


def _files(root):
    out = []
    for dirpath, dirs, fnames in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in fnames:
            fp = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(fp)
            except Exception:
                continue
            if sz > MAX_FILE:
                continue
            ext = os.path.splitext(fn)[1].lower()
            out.append((os.path.relpath(fp, root), sz, EXT_PRIORITY.get(ext, 1)))
    return out


def _scan(root):
    files = _files(root)
    total_bytes = sum(s for _, s, _ in files)
    est_tokens = total_bytes // 4
    top = sorted(files, key=lambda x: -x[1])[:50]
    return files, total_bytes, est_tokens, top


def _select(files, budget):
    ordered = sorted(files, key=lambda x: (-x[2], x[1]))
    sel, used = [], 0
    for path, sz, _pr in ordered:
        t = sz // 4
        if used + t > budget:
            continue
        sel.append(path)
        used += t
        if used >= budget:
            break
    return sel, used


def manifest():
    return {
        "ok": True,
        "id": "context-budget",
        "title": "Context Budget Estimator",
        "source": "local",
        "priority": 85,
        "keywords": ["context", "budget", "triage", "tokens"],
        "default_action": "collect",
        "actions": [
            {"name": "collect", "use_bot": False, "summary": "Total size + rough token estimate of candidates"},
            {"name": "select", "use_bot": False, "summary": "Smallest high-value subset under budget"},
            {"name": "pack", "use_bot": True, "summary": "Escalate only when evidence exceeds safe budget"},
        ],
    }


def do_action(args, name):
    root = os.path.abspath(args.root or DEFAULT_ROOT)
    budget = max(1000, int(getattr(args, "budget", 0) or 20000))
    files, total_bytes, est_tokens, top = _scan(root)

    if name == "collect":
        top_out = [{"path": p, "bytes": s, "est_tokens": s // 4} for p, s, _ in top[:5]]
        _emit({"ok": True, "alert": est_tokens > budget,
               "summary": f"{len(files)} file(s); ~{est_tokens} tokens (budget {budget})",
               "data": {"counts": {"files": len(files),
                                   "total_bytes": total_bytes,
                                   "est_tokens": est_tokens},
                        "top_files": top_out, "top_files_total": len(top)},
               "action": "collect"})

    elif name == "select":
        sel, used = _select(files, budget)
        s, st = _cap(sel)
        _emit({"ok": True, "alert": est_tokens > budget,
               "summary": f"selected {len(sel)} path(s) ~{used} tokens under budget {budget}",
               "data": {"selected": s, "selected_total": st,
                        "used_tokens": used, "budget": budget},
               "action": "select"})

    elif name == "pack":
        over = est_tokens > budget
        _emit({"ok": True, "alert": over,
               "summary": (f"evidence ~{est_tokens} tokens exceeds budget {budget}"
                           if over else "evidence within budget"),
               "data": {"est_tokens": est_tokens, "budget": budget,
                        "files": len(files), "needs_review": over},
               "action": "pack"})

    else:
        _err(f"unknown action: {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--list-actions", action="store_true")
    ap.add_argument("--action")
    ap.add_argument("--root", default=None)
    ap.add_argument("--budget", type=int, default=20000)
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
