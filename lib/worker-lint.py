#!/usr/bin/env python3
"""Worker Contract Linter — local-first Grok Bot meta-worker (stdlib only).

Validates candidate *.py workers in a folder against the local-first contract:
- the last line of --manifest is JSON with exactly the required manifest keys
- every declared --action emits JSON with exactly {ok,alert,summary,data,action}
- no non-stdlib imports (the offline/read-only requirement)

This lints *worker contract outputs* and complements schema-guard (which lints
JSON/JSONL *data* files); it does not duplicate either. Executing dropped code
is required to inspect its emitted JSON; run only in a sandbox you trust.

NOTE: running arbitrary dropped .py executes untrusted code. Use a sandbox.
"""
import argparse, json, os, sys, subprocess, glob, ast, signal

DEFAULT_ROOT = os.getcwd()
MANIFEST_KEYS = ["ok", "id", "title", "source", "priority",
                 "keywords", "default_action", "actions"]
ACTION_KEYS = ["ok", "alert", "summary", "data", "action"]
TIMEOUT = 20


def _emit(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.exit(0)


def _err(m):
    _emit({"ok": False, "alert": True, "summary": m, "data": {}, "action": "error"})


def _cap(lst, n=5):
    return lst[:n], len(lst)


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


def _run(path, *a):
    # Returns: parsed dict, the string "TIMEOUT", or None (no JSON / error).
    # start_new_session lets us kill the whole process group so a candidate that
    # spawns its own workers cannot leave orphaned children behind on timeout.
    try:
        p = subprocess.Popen([sys.executable, path, *a],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, cwd=os.path.dirname(path),
                             start_new_session=True)
    except Exception:
        return None
    try:
        out, _ = p.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            p.kill()
        except Exception:
            pass
        return "TIMEOUT"
    except Exception:
        return None
    for ln in reversed(out.splitlines()):
        ln = ln.strip()
        if ln:
            try:
                return json.loads(ln)
            except Exception:
                continue
    return None


def _non_stdlib_imports(path):
    try:
        with open(path, "r", errors="ignore") as f:
            tree = ast.parse(f.read())
    except Exception:
        return ["<unparseable>"]
    std = getattr(sys, "stdlib_module_names", set())
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                top = n.name.split(".")[0]
                if top not in std and top != "__future__":
                    bad.append(top)
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod and mod not in std and mod != "__future__":
                bad.append(mod)
    return bad


def _scan(root):
    workers = []
    for p in _siblings(root):
        rel = os.path.relpath(p, root)
        issues = []
        imps = _non_stdlib_imports(p)
        if imps:
            issues.append({"kind": "non_stdlib_import", "detail": imps})
        man = _run(p, "--manifest")
        if man == "TIMEOUT":
            issues.append({"kind": "manifest_timeout",
                           "detail": "manifest exceeded %ds" % TIMEOUT})
        elif not isinstance(man, dict) or not man.get("ok"):
            issues.append({"kind": "manifest_invalid",
                           "detail": "not ok or unparseable"})
        else:
            # Contract: manifest needs AT LEAST the required keys (extras allowed);
            # only the --action output must be EXACTLY the 5 keys.
            missing = [k for k in MANIFEST_KEYS if k not in man]
            if missing:
                issues.append({"kind": "manifest_keys",
                               "detail": {"missing": missing}})
            acts = man.get("actions")
            if not isinstance(acts, list) or not acts:
                issues.append({"kind": "no_actions", "detail": ""})
            else:
                for a in acts:
                    an = a.get("name") if isinstance(a, dict) else None
                    if not an:
                        issues.append({"kind": "action_no_name", "detail": ""})
                        continue
                    res = _run(p, "--action", an)
                    if res == "TIMEOUT":
                        issues.append({"kind": "action_timeout",
                                       "detail": {"action": an, "limit_s": TIMEOUT}})
                        continue
                    if not isinstance(res, dict):
                        issues.append({"kind": "action_nonjson",
                                       "detail": {"action": an}})
                        continue
                    ak = set(res.keys())
                    if ak != set(ACTION_KEYS):
                        issues.append({"kind": "action_keys",
                                       "detail": {"action": an,
                                                  "missing": [k for k in ACTION_KEYS if k not in ak],
                                                  "extra": [k for k in res if k not in ACTION_KEYS]}})
                    elif res.get("action") != an:
                        # Remixed from audit-harness: the output must echo the
                        # requested action name, else the worker is mis-wired.
                        issues.append({"kind": "action_echo_mismatch",
                                       "detail": {"action": an, "echoed": res.get("action")}})
        workers.append({"file": rel, "issues": issues})
    return workers


def manifest():
    return {
        "ok": True,
        "id": "worker-lint",
        "title": "Worker Contract Linter",
        "source": "local",
        "priority": 88,
        "keywords": ["contract", "lint", "validation", "meta"],
        "default_action": "collect",
        "actions": [
            {"name": "collect", "use_bot": False, "summary": "Count workers and contract issues"},
            {"name": "violations", "use_bot": False, "summary": "First contract violations per file"},
            {"name": "pack", "use_bot": True, "summary": "Escalate contract drift needing judgment"},
        ],
    }


def do_action(args, name):
    root = os.path.abspath(args.root or DEFAULT_ROOT)
    workers = _scan(root)
    total = len(workers)
    ok = sum(1 for w in workers if not w["issues"])
    issue_count = sum(len(w["issues"]) for w in workers)

    if name == "collect":
        _emit({"ok": True, "alert": bool(issue_count),
               "summary": f"{total} worker(s); {ok} clean, {issue_count} contract issue(s)",
               "data": {"counts": {"workers": total, "clean": ok,
                                   "with_issues": total - ok, "issues": issue_count}},
               "action": "collect"})

    elif name == "violations":
        flat = []
        for w in workers:
            for iss in w["issues"]:
                flat.append({"file": w["file"], "kind": iss["kind"], "detail": iss["detail"]})
        v, vt = _cap(flat)
        _emit({"ok": True, "alert": bool(flat),
               "summary": f"{len(flat)} contract violation(s) across {total - ok} file(s)",
               "data": {"violations": v, "violations_total": vt},
               "action": "violations"})

    elif name == "pack":
        flat = []
        for w in workers:
            for iss in w["issues"]:
                flat.append({"file": w["file"], "kind": iss["kind"], "detail": iss["detail"]})
        v, vt = _cap(flat)
        _emit({"ok": True, "alert": bool(flat),
               "summary": (f"{len(flat)} contract violation(s) for review"
                           if flat else "all workers contract-clean"),
               "data": {"violations": v, "violations_total": vt,
                        "needs_review": bool(flat)},
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
