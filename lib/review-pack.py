#!/usr/bin/env python3
"""Progressive Evidence Pack Builder — local-first Grok Bot worker (stdlib only, read-only)."""
import argparse, json, os, sys, glob, subprocess

DEFAULT_ROOT = os.path.dirname(os.path.abspath(__file__))


def _emit(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.exit(0)


def _err(m):
    _emit({"ok": False, "alert": True, "summary": m, "data": {}, "action": "error"})


def _self():
    return os.path.abspath(sys.argv[0])


def _cap(lst, n=5):
    return lst[:n], len(lst)


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


def _run(path, *a):
    try:
        r = subprocess.run([sys.executable, path, *a],
                           capture_output=True, text=True, timeout=40)
        for ln in reversed(r.stdout.splitlines()):
            ln = ln.strip()
            if ln:
                return json.loads(ln)
    except Exception:
        return None
    return None


def _sibling_manifests(root):
    out = []
    for p in _siblings(root):
        m = _run(p, "--manifest")
        if isinstance(m, dict) and m.get("ok"):
            out.append({"path": p, "id": m.get("id"), "title": m.get("title"),
                        "default_action": m.get("default_action"),
                        "priority": m.get("priority")})
    return out


def manifest():
    return {
        "ok": True,
        "id": "review-pack",
        "title": "Progressive Evidence Pack Builder",
        "source": "local",
        "priority": 93,
        "keywords": ["progressive", "evidence", "review", "pack"],
        "default_action": "d1",
        "actions": [
            {"name": "d1", "use_bot": False, "summary": "Minimal index of relevant evidence"},
            {"name": "d2", "use_bot": False, "summary": "Compact summaries of selected evidence"},
            {"name": "pack", "use_bot": True, "summary": "Mark assembled evidence ready for judgment"},
        ],
    }


def do_action(args, name):
    root = os.path.abspath(args.root or DEFAULT_ROOT)
    manifests = _sibling_manifests(root)

    if name == "d1":
        idx, tot = _cap([{"id": m["id"], "title": m["title"],
                          "default_action": m["default_action"]} for m in manifests])
        _emit({"ok": True, "alert": False,
               "summary": f"{len(manifests)} sibling worker(s) available for evidence",
               "data": {"evidence": idx, "evidence_total": tot},
               "action": "d1"})

    elif name == "d2":
        summaries = []
        for m in manifests:
            act = m.get("default_action") or "collect"
            res = _run(m["path"], "--action", act)
            if isinstance(res, dict):
                summaries.append({"id": m["id"], "action": act,
                                  "summary": res.get("summary", ""),
                                  "alert": bool(res.get("alert"))})
        s, st = _cap(summaries)
        _emit({"ok": True, "alert": any(x["alert"] for x in summaries),
               "summary": f"{len(summaries)} sibling summary(ies) assembled",
               "data": {"summaries": s, "summaries_total": st},
               "action": "d2"})

    elif name == "pack":
        summaries = []
        for m in manifests:
            act = m.get("default_action") or "collect"
            res = _run(m["path"], "--action", act)
            if isinstance(res, dict):
                summaries.append({"id": m["id"], "action": act,
                                  "summary": res.get("summary", ""),
                                  "alert": bool(res.get("alert"))})
        flagged = [x["id"] for x in summaries if x["alert"]]
        s, st = _cap(summaries)
        needs = bool(flagged)
        _emit({"ok": True, "alert": needs,
               "summary": (f"{len(flagged)} sibling(s) flagged for judgment"
                           if flagged else "evidence assembled; nothing flagged"),
               "data": {"summaries": s, "summaries_total": st,
                        "flagged": flagged, "flagged_total": len(flagged),
                        "needs_review": needs},
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
