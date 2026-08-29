#!/usr/bin/env python3
"""Drop Folder Triage Meta-Worker — local-first Grok Bot meta-worker (stdlib only, read-only).

Composes the permanent Tier-1 /lib/ workers (secrets-scan, schema-guard,
log-lens, context-budget, git-pulse) against an external /drop/ folder so a
runner can answer, in one call: safe? within budget? smallest evidence
package? does anything need human/bot judgment? It orchestrates the Tier-1
workers via subprocess dispatch and never reimplements their logic.
"""
import argparse, json, os, sys, subprocess, tempfile, hashlib, time

DEFAULT_LIB = os.path.dirname(os.path.abspath(__file__))


def _emit(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.exit(0)


def _err(m):
    _emit({"ok": False, "alert": True, "summary": m, "data": {}, "action": "error"})


def _cap(lst, n=5):
    return lst[:n], len(lst)


def _drop_dir(args):
    return os.path.abspath(args.drop or os.environ.get("DROP_DIR") or "./drop")


def _lib_dir(args):
    return os.path.abspath(args.lib or os.environ.get("LIB_DIR") or DEFAULT_LIB)


def _call(lib, name, action, drop, extra=None):
    path = os.path.join(lib, name + ".py")
    if not os.path.exists(path):
        return {"ok": False, "alert": True, "_compose_error": True,
                "summary": "worker not found: " + name, "data": {}, "action": action}
    cmd = [sys.executable, path, "--action", action, "--root", drop]
    if extra:
        cmd += extra
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        for ln in reversed(r.stdout.splitlines()):
            ln = ln.strip()
            if ln:
                return json.loads(ln)
    except Exception:
        pass
    return {"ok": False, "alert": True, "_compose_error": True,
            "summary": "compose worker unavailable/timeout: " + name, "data": {}, "action": action}


# ---- discovery / snapshot ----
def _snapshot(drop):
    snap = {}
    if not os.path.isdir(drop):
        return snap
    for dp, dirs, files in os.walk(drop):
        for fn in files:
            fp = os.path.join(dp, fn)
            try:
                st = os.stat(fp)
            except Exception:
                continue
            rel = os.path.relpath(fp, drop)
            snap[rel] = [st.st_size, int(st.st_mtime)]
    return snap


def _cache_path(drop):
    h = hashlib.sha1(drop.encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), ".drop-triage.cache." + h + ".json")


def _load_cache(drop):
    try:
        with open(_cache_path(drop)) as f:
            return json.load(f).get("snapshot", {})
    except Exception:
        return {}


def _save_cache(drop, snap):
    try:
        with open(_cache_path(drop), "w") as f:
            json.dump({"ts": int(time.time()), "snapshot": snap}, f)
    except Exception:
        pass


def _diff(prev, cur):
    added = [k for k in cur if k not in prev]
    removed = [k for k in prev if k not in cur]
    changed = [k for k in cur if k in prev and cur[k] != prev[k]]
    return added, changed, removed


# ---- composition of Tier-1 workers ----
def _compose(drop, lib, budget):
    # Always use the detailed Tier-1 actions so per-file findings are available
    # for both d2 (flagged_files) and pack (findings). Same call count either way.
    asks = [
        ("secrets-scan", "pack", None),
        ("schema-guard", "violations", None),
        ("log-lens", "errors", None),
        ("context-budget", "collect", ["--budget", str(budget)]),
        ("worker-lint", "violations", None),
    ]
    res = {}
    for name, action, extra in asks:
        r = _call(lib, name, action, drop, extra)
        # keep both success and compose-error markers so a failed composer is
        # never silently treated as "clean". (Internal dict; never emitted raw.)
        if isinstance(r, dict):
            res[name] = r
    return res


def _status(res, budget):
    sec = res.get("secrets-scan")
    sch = res.get("schema-guard")
    log = res.get("log-lens")
    bud = res.get("context-budget")
    lint = res.get("worker-lint")

    compose_errors = [n for n, r in res.items()
                      if isinstance(r, dict) and r.get("_compose_error")]
    some_error = bool(compose_errors)

    secrets_sus = 0
    if sec and not sec.get("_compose_error"):
        d = sec.get("data", {})
        secrets_sus = d.get("suspected_total", d.get("counts", {}).get("hits_total", 0))

    schema_inv = 0
    if sch and not sch.get("_compose_error"):
        d = sch.get("data", {})
        schema_inv = d.get("violations_total", d.get("counts", {}).get("invalid", 0))

    log_err = 0
    if log and not log.get("_compose_error"):
        d = log.get("data", {})
        log_err = d.get("errors_total", d.get("counts", {}).get("errors", 0))

    contract = 0
    if lint and not lint.get("_compose_error"):
        d = lint.get("data", {})
        contract = d.get("violations_total", d.get("counts", {}).get("issues", 0))

    est_tokens = 0
    if bud and not bud.get("_compose_error"):
        d = bud.get("data", {})
        est_tokens = d.get("est_tokens") or d.get("used_tokens") or d.get("counts", {}).get("est_tokens", 0)
    over = (est_tokens or 0) > budget

    safe = not (secrets_sus > 0 or schema_inv > 0 or contract > 0)
    clean = log_err == 0
    within_budget = not over
    needs = (some_error
             or bool(sec and sec.get("alert")) or bool(sch and sch.get("alert"))
             or bool(log and log.get("alert")) or bool(bud and bud.get("alert"))
             or bool(lint and lint.get("alert"))
             or (not safe) or (not within_budget))
    verdict = ("block" if not safe
               else "review" if not clean
               else "triage" if not within_budget
               else "pass")
    return {
        "safe": safe, "clean": clean, "within_budget": within_budget,
        "needs_judgment": needs, "verdict": verdict,
        "compose_errors": compose_errors,
        "secrets_suspected": secrets_sus, "schema_invalid": schema_inv,
        "contract_invalid": contract, "log_errors": log_err,
        "est_tokens": est_tokens, "budget": budget,
    }


def _rank_evidence(res, st, drop):
    order = {"secrets": 0, "schema": 1, "logs": 2, "budget": 3, "contract": 4}
    items = []
    sec = res.get("secrets-scan")
    if sec and not sec.get("_compose_error"):
        for it in sec.get("data", {}).get("suspected", [])[:5]:
            items.append((order["secrets"], it.get("file", "?"),
                          "secret: " + str(it.get("kind", ""))))
    sch = res.get("schema-guard")
    if sch and not sch.get("_compose_error"):
        for it in sch.get("data", {}).get("violations", [])[:5]:
            items.append((order["schema"], it.get("file", "?"),
                          "schema: " + str(it.get("error", ""))[:100]))
    log = res.get("log-lens")
    if log and not log.get("_compose_error"):
        for it in log.get("data", {}).get("errors", [])[:5]:
            items.append((order["logs"], it.get("file", "?"),
                          "log: " + str(it.get("text", ""))[:100]))
    if st["est_tokens"] > st["budget"]:
        items.append((order["budget"], drop,
                      "over budget: est %s > %s" % (st["est_tokens"], st["budget"])))
    lint = res.get("worker-lint")
    if lint and not lint.get("_compose_error"):
        for it in lint.get("data", {}).get("violations", [])[:5]:
            reason = "contract: " + str(it.get("kind", it.get("issue", "")))
            detail = it.get("detail")
            if detail:
                if isinstance(detail, dict):
                    detail = ", ".join(f"{k}={v}" for k, v in detail.items())
                reason += " (" + str(detail) + ")"
            items.append((order["contract"], it.get("file", "?"), reason))
    items.sort(key=lambda x: x[0])
    out = []
    for _, f, r in items[:5]:
        if f not in [o["file"] for o in out]:
            out.append({"file": f, "reason": r})
    return out


def _flagged_files(res):
    flagged = []
    for w in ("secrets-scan", "schema-guard", "log-lens", "worker-lint"):
        rr = res.get(w)
        if not rr:
            continue
        for key in ("hits", "violations", "errors"):
            for it in rr.get("data", {}).get(key, []):
                f = it.get("file")
                if f and f not in flagged:
                    flagged.append(f)
    return flagged


def manifest():
    return {
        "ok": True,
        "id": "drop-triage",
        "title": "Drop Folder Triage Meta-Worker",
        "source": "local",
        "priority": 95,
        "keywords": ["meta", "drop", "intake", "triage", "compose", "orchestration"],
        "default_action": "d1",
        "actions": [
            {"name": "d1", "use_bot": False, "summary": "Discover new/changed files in the drop folder"},
            {"name": "d2", "use_bot": False, "summary": "Compose Tier-1 workers; summarize safety/budget/evidence"},
            {"name": "pack", "use_bot": True, "summary": "Assemble bounded review pack for judgment"},
        ],
    }


def do_action(args, name):
    drop = _drop_dir(args)
    lib = _lib_dir(args)
    budget = max(1000, int(args.budget or 20000))
    missing = not os.path.isdir(drop)

    cur = _snapshot(drop)
    prev = _load_cache(drop)
    added, changed, removed = _diff(prev, cur)
    _save_cache(drop, cur)
    delta = {"added": len(added), "changed": len(changed), "removed": len(removed)}

    if name == "d1":
        a, at = _cap(added)
        c, ct = _cap(changed)
        r, rt = _cap(removed)
        _emit({"ok": True, "alert": bool(added or changed or removed or missing),
               "summary": f"drop: +{len(added)} ~{len(changed)} -{len(removed)} (total {len(cur)})",
               "data": {"drop_dir": drop, "drop_missing": missing,
                        "counts": {"total": len(cur), "added": len(added),
                                   "changed": len(changed), "removed": len(removed)},
                        "added": a, "added_total": at,
                        "changed": c, "changed_total": ct,
                        "removed": r, "removed_total": rt},
               "action": "d1"})

    elif name == "d2":
        res = _compose(drop, lib, budget)
        st = _status(res, budget)
        ev = {}
        sel_res = _call(lib, "context-budget", "select", drop, ["--budget", str(budget)])
        if isinstance(sel_res, dict) and sel_res.get("ok"):
            dd = sel_res.get("data", {})
            ev = {"selected": dd.get("selected", []),
                  "selected_total": dd.get("selected_total", 0),
                  "est_tokens": dd.get("used_tokens", st["est_tokens"]),
                  "budget": dd.get("budget", budget)}
        fl, flt = _cap(_flagged_files(res))
        evd = _rank_evidence(res, st, drop)
        _emit({"ok": True, "alert": st["needs_judgment"],
               "summary": f"verdict={st['verdict']} safe={st['safe']} "
                          f"budget={st['within_budget']} clean={st['clean']}",
                "data": {"drop_dir": drop, "drop_missing": missing,
                         "status": {k: st[k] for k in
                                    ("safe", "within_budget", "clean",
                                     "needs_judgment", "verdict", "compose_errors")},
                         "counts": {"secrets_hits": st["secrets_suspected"],
                                    "schema_invalid": st["schema_invalid"],
                                    "contract_invalid": st["contract_invalid"],
                                    "log_errors": st["log_errors"],
                                    "est_tokens": st["est_tokens"], "budget": budget},
                         "evidence_package": ev,
                         "evidence": evd, "evidence_total": len(evd),
                         "flagged_files": fl, "flagged_files_total": flt,
                         "drop_delta": delta},
                "action": "d2"})

    elif name == "pack":
        res = _compose(drop, lib, budget)
        st = _status(res, budget)
        findings = {}
        sec = res.get("secrets-scan")
        if sec:
            s, stot = _cap(sec.get("data", {}).get("suspected", []))
            findings["secrets"] = s
            findings["secrets_total"] = stot
        sch = res.get("schema-guard")
        if sch:
            v, vt = _cap(sch.get("data", {}).get("violations", []))
            findings["schema"] = v
            findings["schema_total"] = vt
        log = res.get("log-lens")
        if log:
            e, et = _cap(log.get("data", {}).get("errors", []))
            findings["logs"] = e
            findings["logs_total"] = et
        lint = res.get("worker-lint")
        if lint:
            v, vt = _cap(lint.get("data", {}).get("violations", []))
            findings["contract"] = v
            findings["contract_total"] = vt
        bud = res.get("context-budget")
        if bud:
            d = bud.get("data", {})
            findings["budget"] = {"est_tokens": d.get("est_tokens", st["est_tokens"]),
                                  "budget": d.get("budget", budget),
                                  "over": st["est_tokens"] > budget}
        sel_res = _call(lib, "context-budget", "select", drop, ["--budget", str(budget)])
        ev = {}
        if isinstance(sel_res, dict) and sel_res.get("ok"):
            dd = sel_res.get("data", {})
            ev = {"selected": dd.get("selected", []),
                  "selected_total": dd.get("selected_total", 0),
                  "used_tokens": dd.get("used_tokens", 0),
                  "budget": dd.get("budget", budget)}
        evd = _rank_evidence(res, st, drop)
        _emit({"ok": True, "alert": st["needs_judgment"],
               "summary": ("review pack ready: verdict=%s safe=%s"
                           % (st["verdict"], st["safe"]))
                           if st["needs_judgment"] else "drop clean; no judgment needed",
                "data": {"drop_dir": drop, "drop_missing": missing,
                         "status": {k: st[k] for k in
                                    ("safe", "within_budget", "clean",
                                     "needs_judgment", "verdict", "compose_errors")},
                         "findings": findings,
                        "evidence_package": ev,
                        "evidence": evd, "evidence_total": len(evd),
                        "drop_delta": delta,
                        "needs_review": st["needs_judgment"]},
                "action": "pack"})

    else:
        _err(f"unknown action: {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--list-actions", action="store_true")
    ap.add_argument("--action")
    ap.add_argument("--drop", default=None)
    ap.add_argument("--lib", default=None)
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
