#!/usr/bin/env python3
import json, subprocess, sys, os, glob

LIB = os.path.dirname(os.path.abspath(__file__))
workers = [w for w in sorted(glob.glob(os.path.join(LIB, "*.py")))
            if not os.path.basename(w).startswith("_")]
MAN_KEYS = {"ok", "id", "title", "source", "priority", "keywords", "default_action", "actions"}
ACT_KEYS = {"ok", "alert", "summary", "data", "action"}
fails = []


def last_json(path, args):
    r = subprocess.run([sys.executable, path, *args], capture_output=True, text=True, timeout=60)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if not lines:
        return None, r.stderr
    return json.loads(lines[-1]), r.stderr


for w in workers:
    name = os.path.basename(w)
    man, err = last_json(w, ["--manifest"])
    if man is None:
        fails.append(f"{name}: manifest parse fail {err}")
        continue
    if set(man.keys()) != MAN_KEYS:
        fails.append(f"{name}: manifest keys {set(man.keys())} != {MAN_KEYS}")
    if man.get("source") != "local":
        fails.append(f"{name}: source != local")
    acts = man.get("actions", [])
    if not isinstance(acts, list) or not acts:
        fails.append(f"{name}: no actions")
    for a in acts:
        if not all(k in a for k in ("name", "use_bot", "summary")):
            fails.append(f"{name}: action {a} missing keys")
    # list-actions
    r = subprocess.run([sys.executable, w, "--list-actions"], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        fails.append(f"{name}: list-actions rc={r.returncode}")
    # each action
    for a in acts:
        an = a["name"]
        res, e = last_json(w, ["--action", an])
        if res is None:
            fails.append(f"{name}:{an} parse fail {e}")
            continue
        if set(res.keys()) != ACT_KEYS:
            fails.append(f"{name}:{an} action keys {set(res.keys())} != {ACT_KEYS}")
        if not isinstance(res.get("data"), dict):
            fails.append(f"{name}:{an} data not dict")

def main():
    print("WORKERS:", len(workers))
    if fails:
        print("FAILURES:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("ALL CONTRACT CHECKS PASSED")


if __name__ == "__main__":
    main()
