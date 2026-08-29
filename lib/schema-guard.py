#!/usr/bin/env python3
"""Local Schema / Contract Guard — local-first Grok Bot worker (stdlib only, read-only)."""
import argparse, json, os, sys, glob

DEFAULT_ROOT = os.getcwd()
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
MANIFEST_KEYS = ["ok", "id", "title", "source", "priority",
                 "keywords", "default_action", "actions"]
ACTION_KEYS = ["ok", "alert", "summary", "data", "action"]
SCAN_EXT = {".json", ".jsonl"}


def _emit(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.exit(0)


def _err(m):
    _emit({"ok": False, "alert": True, "summary": m, "data": {}, "action": "error"})


def _cap(lst, n=5):
    return lst[:n], len(lst)


def _validate_obj(o):
    if not isinstance(o, dict):
        return "not a JSON object"
    if set(o.keys()) >= set(MANIFEST_KEYS):
        if not isinstance(o.get("actions"), list):
            return "manifest.actions not list"
        for a in o["actions"]:
            if not isinstance(a, dict) or not all(k in a for k in ("name", "use_bot", "summary")):
                return "manifest action missing keys"
        return None
    if set(o.keys()) == set(ACTION_KEYS):
        if not isinstance(o.get("data"), dict):
            return "action.data not object"
        return None
    return "unknown-shape"


def _scan(root):
    valid = invalid = unverified = files_scanned = 0
    violations = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if os.path.splitext(fn)[1].lower() not in SCAN_EXT:
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root)
            files_scanned += 1
            try:
                with open(fp, "r", errors="ignore") as f:
                    text = f.read()
            except Exception:
                invalid += 1
                if len(violations) < 20:
                    violations.append({"file": rel, "issue": "unreadable"})
                continue
            if fp.lower().endswith(".jsonl"):
                bad = 0
                for li, ln in enumerate(text.splitlines(), 1):
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        json.loads(ln)
                    except Exception:
                        bad += 1
                        if len(violations) < 20:
                            violations.append({"file": rel, "line": li,
                                               "issue": "invalid jsonl line"})
                if bad:
                    invalid += 1
                else:
                    valid += 1
            else:
                try:
                    o = json.loads(text)
                except Exception:
                    invalid += 1
                    if len(violations) < 20:
                        violations.append({"file": rel, "issue": "invalid json"})
                    continue
                issue = _validate_obj(o)
                if issue is None:
                    valid += 1
                elif issue == "unknown-shape":
                    unverified += 1
                else:
                    invalid += 1
                    if len(violations) < 20:
                        violations.append({"file": rel, "issue": issue})
    return valid, invalid, unverified, files_scanned, violations


def manifest():
    return {
        "ok": True,
        "id": "schema-guard",
        "title": "Local Schema / Contract Guard",
        "source": "local",
        "priority": 90,
        "keywords": ["validation", "schema", "contract", "quality"],
        "default_action": "collect",
        "actions": [
            {"name": "collect", "use_bot": False, "summary": "Check known files for basic schema validity"},
            {"name": "violations", "use_bot": False, "summary": "First few violations with file + key"},
            {"name": "pack", "use_bot": True, "summary": "Escalate schema drift needing judgment"},
        ],
    }


def do_action(args, name):
    root = os.path.abspath(args.root or DEFAULT_ROOT)
    valid, invalid, unverified, scanned, violations = _scan(root)

    if name == "collect":
        _emit({"ok": True, "alert": bool(invalid),
               "summary": f"{scanned} file(s): {valid} valid, {invalid} invalid, {unverified} unverified",
               "data": {"counts": {"valid": valid, "invalid": invalid,
                                   "unverified": unverified,
                                   "files_scanned": scanned}},
               "action": "collect"})

    elif name == "violations":
        v, vt = _cap(violations)
        _emit({"ok": True, "alert": bool(violations),
               "summary": f"{len(violations)} schema violation(s)",
               "data": {"violations": v, "violations_total": vt},
               "action": "violations"})

    elif name == "pack":
        v, vt = _cap(violations)
        _emit({"ok": True, "alert": bool(violations),
               "summary": (f"{len(violations)} violation(s) for judgment"
                           if violations else "schemas consistent"),
               "data": {"violations": v, "violations_total": vt,
                        "needs_review": bool(violations)},
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
