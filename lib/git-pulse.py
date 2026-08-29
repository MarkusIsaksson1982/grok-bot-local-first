#!/usr/bin/env python3
"""Local Git State Digest — local-first Grok Bot worker (stdlib only, read-only)."""
import argparse, json, os, sys, subprocess

DEFAULT_ROOT = os.getcwd()


def _emit(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.exit(0)


def _err(m):
    _emit({"ok": False, "alert": True, "summary": m, "data": {}, "action": "error"})


def _cap(lst, n=5):
    return lst[:n], len(lst)


def _git(root, *a):
    try:
        r = subprocess.run(["git", "-C", root, *a],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


def _scan(root):
    branch = (_git(root, "rev-parse", "--abbrev-ref", "HEAD") or "unknown").strip()
    ahead = behind = 0
    ab = _git(root, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if ab:
        parts = ab.strip().split("\t")
        if len(parts) == 2:
            ahead, behind = int(parts[0] or 0), int(parts[1] or 0)
    status = _git(root, "status", "--porcelain") or ""
    modified = staged = untracked = conflicts = 0
    by_status = {"modified": [], "staged": [], "untracked": [], "conflict": []}
    for ln in status.splitlines():
        if not ln:
            continue
        code = ln[:2]
        path = ln[3:].strip()
        if code == "??":
            untracked += 1
            by_status["untracked"].append(path)
        elif code in ("DD", "AU", "UA", "UU", "AA"):
            conflicts += 1
            by_status["conflict"].append(path)
        else:
            if code[0] in ("M", "A", "D", "R", "C"):
                staged += 1
                by_status["staged"].append(path)
            if code[1] in ("M", "D", "R"):
                modified += 1
                by_status["modified"].append(path)
    return branch, ahead, behind, modified, staged, untracked, conflicts, by_status


def manifest():
    return {
        "ok": True,
        "id": "git-pulse",
        "title": "Local Git State Digest",
        "source": "local",
        "priority": 80,
        "keywords": ["git", "vcs", "state", "diff"],
        "default_action": "collect",
        "actions": [
            {"name": "collect", "use_bot": False, "summary": "Branch + ahead/behind + modified/staged/untracked counts"},
            {"name": "files", "use_bot": False, "summary": "Filenames grouped by status"},
            {"name": "pack", "use_bot": True, "summary": "Only on conflicts or diverged history"},
        ],
    }


def do_action(args, name):
    root = os.path.abspath(args.root or DEFAULT_ROOT)
    out = _git(root, "rev-parse", "--is-inside-work-tree")
    if out is None:
        _emit({"ok": True, "alert": False,
               "summary": "not a git repository", "action": "collect",
               "data": {"branch": None, "ahead": 0, "behind": 0, "modified": 0,
                        "staged": 0, "untracked": 0, "conflicts": 0}})
    branch, ahead, behind, modified, staged, untracked, conflicts, by_status = _scan(root)
    diverged = ahead > 0 and behind > 0

    if name == "collect":
        _emit({"ok": True, "alert": bool(conflicts or diverged),
               "summary": f"branch {branch}; +{ahead}/-{behind}; M{modified} S{staged} U{untracked} C{conflicts}",
               "data": {"branch": branch, "ahead": ahead, "behind": behind,
                        "modified": modified, "staged": staged,
                        "untracked": untracked, "conflicts": conflicts},
               "action": "collect"})

    elif name == "files":
        groups = {}
        for k in ("modified", "staged", "untracked", "conflict"):
            lst, tot = _cap(by_status[k])
            groups[k] = lst
            groups[k + "_total"] = tot
        _emit({"ok": True, "alert": bool(conflicts),
               "summary": f"{modified + staged + untracked + conflicts} changed path(s)",
               "data": groups, "action": "files"})

    elif name == "pack":
        needs = bool(conflicts or diverged)
        items = []
        if conflicts:
            items.append(f"{conflicts} conflict(s)")
        if diverged:
            items.append(f"diverged history +{ahead}/-{behind}")
        _emit({"ok": True, "alert": needs,
               "summary": ("; ".join(items) if items else "git state clean"),
               "data": {"conflicts": conflicts, "ahead": ahead, "behind": behind,
                        "diverged": diverged, "needs_review": needs},
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
