#!/usr/bin/env python3
"""Structured Log / JSONL Lens — local-first Grok Bot worker (stdlib only, read-only)."""
import argparse, json, os, sys, glob, re

DEFAULT_ROOT = os.getcwd()
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
LOG_EXT = {".jsonl", ".log"}
LEVEL_RE = re.compile(r"\b(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|FATAL|CRITICAL)\b", re.I)


def _emit(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.exit(0)


def _err(m):
    _emit({"ok": False, "alert": True, "summary": m, "data": {}, "action": "error"})


def _cap(lst, n=5):
    return lst[:n], len(lst)


def _norm(msg):
    s = re.sub(r"0x[0-9a-fA-F]+", "0xN", msg)
    s = re.sub(r"\d+", "#", s)
    s = re.sub(r"\"[^\"]*\"", "\"\"", s)
    return s.strip()[:120]


def _scan(root):
    levels, errors = {}, {}
    files = total = 0
    for dirpath, dirs, fnames in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in fnames:
            if os.path.splitext(fn)[1].lower() not in LOG_EXT:
                continue
            fp = os.path.join(dirpath, fn)
            files += 1
            try:
                with open(fp, "r", errors="ignore") as f:
                    lines = f.readlines()
            except Exception:
                continue
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                total += 1
                lvl = None
                obj = None
                if ln.startswith("{"):
                    try:
                        obj = json.loads(ln)
                        if isinstance(obj, dict):
                            lvl = (obj.get("level") or obj.get("severity") or "").upper()
                    except Exception:
                        lvl = None
                if not lvl:
                    m = LEVEL_RE.search(ln)
                    if m:
                        lvl = m.group(1).upper()
                if not lvl:
                    lvl = "UNKNOWN"
                levels[lvl] = levels.get(lvl, 0) + 1
                if lvl in ("ERROR", "FATAL", "CRITICAL", "WARNING", "WARN"):
                    msg = obj.get("message") if isinstance(obj, dict) else ln
                    if not isinstance(msg, str):
                        msg = ln
                    sig = _norm(msg)
                    e = errors.get(sig)
                    if e is None:
                        e = {"count": 0, "latest": "", "sample": msg[:160]}
                        errors[sig] = e
                    e["count"] += 1
                    if isinstance(obj, dict):
                        e["latest"] = (obj.get("ts") or obj.get("time")
                                       or obj.get("timestamp") or e["latest"])
    return levels, errors, files, total


def manifest():
    return {
        "ok": True,
        "id": "log-lens",
        "title": "Structured Log / JSONL Lens",
        "source": "local",
        "priority": 85,
        "keywords": ["logs", "errors", "jsonl", "dedupe"],
        "default_action": "collect",
        "actions": [
            {"name": "collect", "use_bot": False, "summary": "Recent volume + level counts"},
            {"name": "errors", "use_bot": False, "summary": "Deduplicated error signatures + latest"},
            {"name": "pack", "use_bot": True, "summary": "Escalate repeated or high-severity patterns"},
        ],
    }


def do_action(args, name):
    root = os.path.abspath(args.root or DEFAULT_ROOT)
    levels, errors, files, total = _scan(root)
    err_count = sum(levels.get(k, 0) for k in ("ERROR", "FATAL", "CRITICAL"))
    warn_count = sum(levels.get(k, 0) for k in ("WARNING", "WARN"))

    if name == "collect":
        _emit({"ok": True, "alert": bool(err_count),
               "summary": f"{total} line(s) in {files} file(s); {err_count} errors, {warn_count} warnings",
               "data": {"counts": {"lines": total, "files": files,
                                   "errors": err_count, "warnings": warn_count},
                        "levels": levels},
               "action": "collect"})

    elif name == "errors":
        sigs = sorted(errors.items(), key=lambda kv: -kv[1]["count"])
        out = [{"signature": k, "count": v["count"], "latest": v["latest"],
                "sample": v["sample"]} for k, v in sigs]
        o, ot = _cap(out)
        _emit({"ok": True, "alert": bool(out),
               "summary": f"{len(out)} deduplicated error signature(s)",
               "data": {"errors": o, "errors_total": ot},
               "action": "errors"})

    elif name == "pack":
        sigs = sorted(errors.items(), key=lambda kv: -kv[1]["count"])
        repeat = [k for k, v in sigs if v["count"] >= 5]
        o, ot = _cap([{"signature": k, "count": errors[k]["count"]} for k in repeat])
        needs = bool(repeat) or err_count > 0
        _emit({"ok": True, "alert": needs,
               "summary": (f"{len(repeat)} repeated + {err_count} error(s) for review"
                           if needs else "no error patterns"),
               "data": {"repeated": o, "repeated_total": ot,
                        "errors_total": err_count, "needs_review": needs},
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
