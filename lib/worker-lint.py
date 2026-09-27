#!/usr/bin/env python3
"""worker-lint: contract checks for lib/*.py. Fast collect. --root DIR."""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
from typing import Any

MANIFEST = {
    "ok": True,
    "id": "worker-lint",
    "title": "Worker Contract Linter",
    "source": "grok-4.7-high",
    "origin": {"grok_build": "1.0.41", "os": "windows-11"},
    "priority": 88,
    "keywords": ["contract", "lint", "validation", "meta"],
    "default_action": "collect",
    "verified_grok_bot": "0.61.0",
    "verified_at": "2026-09-27",
    "actions": [
        {"name": "collect", "use_bot": False, "summary": "Count lib workers and contract issues"},
        {"name": "violations", "use_bot": False, "summary": "First contract violations per file"},
        {"name": "pack", "use_bot": True, "summary": "Tiny pack only if contract drift needs judgment"},
    ],
}

MANIFEST_KEYS = [
    "ok", "id", "title", "source", "priority", "keywords", "default_action", "actions",
]
ACTION_KEYS = ["ok", "alert", "summary", "data", "action"]
TIMEOUT = 3
COLLECT_BUDGET_S = 8.0
# Mutual with review-pack SKIP_RUN_IDS. collect never sample-runs these (executor storm).
SKIP_ACTION_IDS = {
    "worker-lint", "worker-index", "review-pack", "ecosystem-poll",
}

_ASSET_HINT = re.compile(
    r"(?i)(?:(?<![A-Za-z])(?:faucet|transfer|cdp|wallet)(?![A-Za-z])|0x[0-9a-fA-F]{40})"
)
_ASSET_KEYS = {
    "asset_class",
    "capabilities",
    "allowed_networks",
    "max_transfer_eth",
    "destinations",
    "secrets",
    "faucet_rolling_cap_eth",
}
_ASSET_REQ = ("asset_class", "capabilities", "allowed_networks", "max_transfer_eth", "secrets")
_ASSET_CLASS = {"none", "testnet", "mainnet"}
_ASSET_CAPS = {"read", "faucet", "transfer", "sign", "export_key"}
_ASSET_DEST = {"ledger_or_named", "any"}
_INLINE_SECRET = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|" + "CDP_API_KEY_PRIVATE_KEY" + r"\s*=")
_POLICY_MAINNET = re.compile(r"(?i)\bmainnet\b")
_POLICY_EXPORT = re.compile(r"(?i)\bexport_key\b")
_REVIEW_DEST_ANY = re.compile(r"(?i)destinations\s*[:=]\s*any|destinations.*\bany\b|\bany\b.*destinations")


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


def last_json_line(text: str) -> Any | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


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


def parse_dir(argv: list[str]) -> str | None:
    for i, a in enumerate(argv):
        if a == "--dir" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--dir="):
            return a.split("=", 1)[1]
    return None


def scan_dir(argv: list[str] | None = None) -> str:
    argv = argv or []
    override = parse_dir(argv)
    if override:
        return os.path.abspath(override)
    return os.path.join(parse_root(argv), "lib")


def run_worker(py_path: str, *args: str) -> tuple[Any | None, int | None, str | None]:
    try:
        proc = subprocess.run(
            [sys.executable, py_path, *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=os.path.dirname(py_path) or root_dir(),
        )
    except subprocess.TimeoutExpired:
        return None, None, "TIMEOUT"
    except OSError:
        return None, None, "OSERROR"
    obj = last_json_line(proc.stdout or "")
    return obj, proc.returncode, None


def non_stdlib_imports(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return ["<unparseable>"]
    std = getattr(sys, "stdlib_module_names", set())
    bad: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [n.name.split(".")[0] for n in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod:
                names = [mod]
        for top in names:
            if top and top not in std and top != "__future__" and top not in seen:
                seen.add(top)
                bad.append(top)
    return bad


def list_candidates(folder: str) -> list[str]:
    """*.py under lib/ and lib/<lane>/ (one level)."""
    selfp = os.path.abspath(__file__)
    out: list[str] = []
    if not os.path.isdir(folder):
        return out
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [x for x in dirnames if x.lower() != "__pycache__" and not x.startswith(".")]
        rel = os.path.relpath(dirpath, folder)
        if rel != "." and os.sep in rel:
            dirnames[:] = []
            continue
        for name in sorted(filenames):
            if not name.lower().endswith(".py"):
                continue
            if name.startswith("_") or name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            if not os.path.isfile(path):
                continue
            if os.path.abspath(path) == selfp:
                continue
            out.append(path)
    return out


def rel_label(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except ValueError:
        return os.path.basename(path)


def _read_capped(path: str, n: int = 200_000) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read(n)
    except OSError:
        return ""


def _nearest_named(start_dir: str, name: str) -> str | None:
    cur = os.path.abspath(start_dir)
    for _ in range(8):
        p = os.path.join(cur, name)
        if os.path.isfile(p):
            return p
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def _asset_touch(path: str) -> bool:
    blob = _read_capped(path)
    contract = os.path.join(os.path.dirname(path), "CONTRACT.md")
    if os.path.isfile(contract):
        blob = blob + "\n" + _read_capped(contract, 80_000)
    return bool(_ASSET_HINT.search(blob))


def _load_asset_meta(path: str, man: dict[str, Any] | None) -> tuple[Any | None, str | None]:
    sidecar = os.path.join(os.path.dirname(path), "asset_meta.json")
    if os.path.isfile(sidecar):
        try:
            obj = json.loads(_read_capped(sidecar, 80_000))
        except json.JSONDecodeError:
            return None, "asset_meta_unparseable"
        return obj, None
    if isinstance(man, dict) and "asset_meta" in man:
        return man.get("asset_meta"), None
    return None, None


def _is_nonneg_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def lint_asset(path: str, man: dict[str, Any] | None) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if os.path.basename(path).lower() in ("worker-lint.py", "worker-lint.candidate.py"):
        return issues
    meta, err = _load_asset_meta(path, man)
    if err:
        issues.append({"kind": err, "detail": "asset_meta.json"})
        return issues
    caps_hint = False
    if isinstance(meta, dict) and isinstance(meta.get("capabilities"), list):
        caps_hint = "transfer" in meta["capabilities"]
    if isinstance(man, dict):
        am = man.get("asset_meta")
        if isinstance(am, dict) and isinstance(am.get("capabilities"), list):
            caps_hint = caps_hint or ("transfer" in am["capabilities"])
    need = _asset_touch(path) or caps_hint
    if meta is None:
        if need:
            issues.append(
                {
                    "kind": "asset_meta_missing",
                    "detail": "need asset_meta.json or manifest.asset_meta",
                }
            )
        return issues
    if not isinstance(meta, dict):
        issues.append({"kind": "asset_meta_unparseable", "detail": "asset_meta not an object"})
        return issues

    extra = [k for k in meta if k not in _ASSET_KEYS]
    if extra:
        issues.append({"kind": "asset_meta_extra_keys", "detail": extra[:8]})
    missing = [k for k in _ASSET_REQ if k not in meta]
    if missing:
        issues.append({"kind": "asset_meta_missing_keys", "detail": missing})

    aclass = meta.get("asset_class")
    if aclass not in _ASSET_CLASS:
        issues.append({"kind": "asset_class_invalid", "detail": aclass})
        aclass = None

    caps = meta.get("capabilities")
    if not isinstance(caps, list) or any(c not in _ASSET_CAPS for c in caps):
        issues.append({"kind": "asset_capabilities_invalid", "detail": caps if isinstance(caps, list) else type(caps).__name__})
        capset = {c for c in caps if isinstance(c, str)} if isinstance(caps, list) else set()
    else:
        capset = set(caps)

    nets = meta.get("allowed_networks")
    if not isinstance(nets, list) or any(not isinstance(n, str) or not n for n in nets):
        issues.append({"kind": "asset_networks_invalid", "detail": ""})
        nets = []

    mx = meta.get("max_transfer_eth")
    if not _is_nonneg_num(mx):
        issues.append({"kind": "asset_max_transfer", "detail": "must be number >= 0"})
        mx = None

    dest = meta.get("destinations", "ledger_or_named")
    if dest not in _ASSET_DEST:
        issues.append({"kind": "asset_destinations_invalid", "detail": dest})
        dest = "ledger_or_named"

    secrets = meta.get("secrets")
    if secrets != "env_path_only":
        issues.append({"kind": "asset_secrets_invalid", "detail": secrets})

    if "faucet_rolling_cap_eth" in meta and not _is_nonneg_num(meta.get("faucet_rolling_cap_eth")):
        issues.append({"kind": "asset_faucet_rolling_cap", "detail": "must be number >= 0"})

    if aclass == "none":
        if capset or nets or (mx is not None and mx != 0):
            issues.append({"kind": "asset_none_not_empty", "detail": "none => empty caps/networks and max_transfer_eth=0"})
    elif aclass is not None and not nets:
        issues.append({"kind": "asset_networks_empty", "detail": "allowed_networks required unless none"})

    if "transfer" in capset:
        if mx is not None and not (mx > 0):
            issues.append({"kind": "asset_max_transfer", "detail": "transfer requires max_transfer_eth > 0"})
    elif mx is not None and mx != 0:
        issues.append({"kind": "asset_max_transfer", "detail": "max_transfer_eth must be 0 without transfer"})

    if ("faucet" in capset or "transfer" in capset) and "read" not in capset:
        issues.append({"kind": "asset_warn_no_read", "detail": "faucet/transfer usually also list read"})
    if "faucet" in capset and aclass == "mainnet":
        issues.append({"kind": "asset_faucet_mainnet", "detail": "faucet not allowed on mainnet"})
    if "export_key" in capset and secrets != "env_path_only":
        issues.append({"kind": "asset_export_key_secrets", "detail": "export_key requires secrets=env_path_only"})

    folder = os.path.dirname(path)
    policy = _read_capped(_nearest_named(folder, "POLICY.md") or "", 80_000)
    if aclass == "mainnet" and not _POLICY_MAINNET.search(policy):
        issues.append({"kind": "asset_mainnet_no_policy", "detail": "mainnet requires POLICY.md covering mainnet"})
    if "export_key" in capset and not _POLICY_EXPORT.search(policy):
        issues.append({"kind": "asset_export_key_no_policy", "detail": "export_key requires POLICY.md covering export_key"})

    if dest == "any":
        rtxt = _read_capped(os.path.join(folder, "REVIEW.md"), 80_000)
        if not _REVIEW_DEST_ANY.search(rtxt):
            issues.append({"kind": "asset_warn_dest_any", "detail": "destinations=any needs REVIEW.md coverage"})

    header = "\n".join(_read_capped(path, 16_000).splitlines()[:80])
    blobs = [header]
    try:
        for name in os.listdir(folder):
            low = name.lower()
            if not low.endswith(".json") or low in ("package-lock.json", "package.json"):
                continue
            blobs.append(_read_capped(os.path.join(folder, name), 40_000))
    except OSError:
        pass
    if _INLINE_SECRET.search("\n".join(blobs)):
        issues.append({"kind": "asset_inline_secret", "detail": "inline key/PEM; use secrets env_path_only"})
    return issues


def lint_one(path: str, run_actions: bool, deadline: float | None) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    def finish(man_obj: Any | None = None) -> list[dict[str, Any]]:
        issues.extend(lint_asset(path, man_obj if isinstance(man_obj, dict) else None))
        return issues

    if deadline is not None and time.monotonic() >= deadline:
        issues.append({"kind": "scan_budget", "detail": "collect time cap"})
        return issues

    imps = non_stdlib_imports(path)
    if imps:
        issues.append({"kind": "non_stdlib_import", "detail": imps[:8]})

    obj, code, err = run_worker(path)
    if err == "TIMEOUT":
        issues.append({"kind": "bare_timeout", "detail": "no-flag run exceeded %ds" % TIMEOUT})
    elif obj is None or not isinstance(obj, dict):
        issues.append({"kind": "bare_nonjson", "detail": "bare run did not emit JSON"})
    else:
        if code != 2:
            issues.append({"kind": "bare_exit", "detail": {"expected": 2, "got": code}})
        if obj.get("ok") is not False:
            issues.append({"kind": "bare_ok", "detail": "bare must set ok false"})
        if obj.get("reason") != "no_flags" or obj.get("hint") != "--manifest":
            issues.append(
                {"kind": "bare_shape", "detail": {"reason": obj.get("reason"), "hint": obj.get("hint")}}
            )
        if obj.get("alert") is not False:
            issues.append({"kind": "bare_alert", "detail": "bare must set alert false"})

    man, _code, err = run_worker(path, "--manifest")
    if err == "TIMEOUT":
        issues.append({"kind": "manifest_timeout", "detail": "manifest exceeded %ds" % TIMEOUT})
        return finish(None)
    if not isinstance(man, dict) or man.get("ok") is not True:
        issues.append({"kind": "manifest_invalid", "detail": "not ok or unparseable"})
        return finish(man)

    missing = [k for k in MANIFEST_KEYS if k not in man]
    if missing:
        issues.append({"kind": "manifest_keys", "detail": {"missing": missing}})

    # verified_grok_bot and origin are provenance, not a required place to run the worker.
    stamp = man.get("verified_grok_bot")
    if not isinstance(stamp, str) or not stamp.strip():
        issues.append({"kind": "verified_grok_bot_missing", "detail": "manifest requires verified_grok_bot"})

    acts = man.get("actions")
    if not isinstance(acts, list) or not acts:
        issues.append({"kind": "no_actions", "detail": ""})
        return finish(man)

    wid = man.get("id") if isinstance(man.get("id"), str) else os.path.splitext(os.path.basename(path))[0]
    skip_run = (not run_actions) or wid in SKIP_ACTION_IDS
    base = os.path.basename(path).lower()
    if base in ("worker-lint.py",):
        skip_run = True

    for a in acts:
        if not isinstance(a, dict):
            issues.append({"kind": "action_no_name", "detail": ""})
            continue
        missing_a = [k for k in ("name", "use_bot", "summary") if k not in a]
        if missing_a:
            issues.append({"kind": "action_decl_keys", "detail": {"missing": missing_a}})
        an = a.get("name")
        if not isinstance(an, str) or not an:
            issues.append({"kind": "action_no_name", "detail": ""})
            continue
        if skip_run:
            continue
        if deadline is not None and time.monotonic() >= deadline:
            issues.append({"kind": "scan_budget", "detail": {"action": an}})
            break
        res, _rc, err = run_worker(path, "--action", an)
        if err == "TIMEOUT":
            issues.append({"kind": "action_timeout", "detail": {"action": an, "limit_s": TIMEOUT}})
            continue
        if not isinstance(res, dict):
            issues.append({"kind": "action_nonjson", "detail": {"action": an}})
            continue
        ak = set(res.keys())
        if ak != set(ACTION_KEYS):
            issues.append(
                {
                    "kind": "action_keys",
                    "detail": {
                        "action": an,
                        "missing": [k for k in ACTION_KEYS if k not in ak],
                        "extra": [k for k in res if k not in ACTION_KEYS],
                    },
                }
            )
        elif res.get("action") != an:
            issues.append({"kind": "action_echo_mismatch", "detail": {"action": an, "echoed": res.get("action")}})
    return finish(man)


def scan(folder: str, run_actions: bool, deadline: float | None, root: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in list_candidates(folder):
        if deadline is not None and time.monotonic() >= deadline:
            rows.append({"file": rel_label(path, root), "issues": [{"kind": "scan_budget", "detail": "collect time cap"}]})
            continue
        rows.append({"file": rel_label(path, root), "issues": lint_one(path, run_actions, deadline)})
    return rows


def flatten(workers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for w in workers:
        for iss in w["issues"]:
            flat.append({"file": w["file"], "kind": iss["kind"], "detail": iss["detail"]})
    return flat


def action_collect(argv: list[str]) -> dict[str, Any]:
    root = parse_root(argv)
    folder = scan_dir(argv)
    deadline = time.monotonic() + COLLECT_BUDGET_S
    # collect: bare + manifest only (no per-action exec) so harness always gets JSON fast
    workers = scan(folder, run_actions=False, deadline=deadline, root=root)
    total = len(workers)
    clean = sum(1 for w in workers if not w["issues"])
    issue_count = sum(len(w["issues"]) for w in workers)
    return {
        "ok": True,
        "alert": bool(issue_count),
        "summary": cap_summary("%d worker(s); %d clean, %d contract issue(s)" % (total, clean, issue_count)),
        "data": {
            "counts": {
                "workers": total,
                "clean": clean,
                "with_issues": total - clean,
                "issues": issue_count,
            },
            "scan_dir": folder.replace("\\", "/"),
        },
        "action": "collect",
    }


def action_violations(argv: list[str]) -> dict[str, Any]:
    root = parse_root(argv)
    folder = scan_dir(argv)
    deadline = time.monotonic() + COLLECT_BUDGET_S
    workers = scan(folder, run_actions=True, deadline=deadline, root=root)
    flat = flatten(workers)
    dirty = sum(1 for w in workers if w["issues"])
    return {
        "ok": True,
        "alert": bool(flat),
        "summary": cap_summary("%d contract violation(s) across %d file(s)" % (len(flat), dirty)),
        "data": {"violations": flat[:12], "violations_total": len(flat), "truncated": len(flat) > 12},
        "action": "violations",
    }


def action_pack(argv: list[str]) -> dict[str, Any]:
    root = parse_root(argv)
    folder = scan_dir(argv)
    deadline = time.monotonic() + COLLECT_BUDGET_S
    workers = scan(folder, run_actions=True, deadline=deadline, root=root)
    flat = flatten(workers)
    if not flat:
        return {
            "ok": True,
            "alert": False,
            "summary": cap_summary("no judgment needed"),
            "data": {"emit": False, "needs_judgment": False},
            "action": "pack",
        }
    kinds = sorted({x["kind"] for x in flat})[:12]
    files = sorted({x["file"] for x in flat})[:12]
    return {
        "ok": True,
        "alert": True,
        "summary": cap_summary("%d contract violation(s) in %d file(s)" % (len(flat), len(files))),
        "data": {
            "emit": True,
            "needs_judgment": True,
            "violations_total": len(flat),
            "kinds": kinds,
            "files": files,
        },
        "action": "pack",
    }


ACTIONS = {"collect": action_collect, "violations": action_violations, "pack": action_pack}


def known_flag(token: str) -> bool:
    return token in ("--manifest", "--list-actions", "--action", "--dir", "--root") or token.startswith("--dir=")


def _skip_opt(argv: list[str], i: int) -> int | None:
    if argv[i] in ("--dir", "--root") and i + 1 < len(argv):
        return i + 2
    if argv[i].startswith("--dir="):
        return i + 1
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}, 2)
    primary = None
    i = 1
    while i < len(argv):
        nxt = _skip_opt(argv, i)
        if nxt is not None:
            i = nxt
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
                nxt = _skip_opt(argv, j)
                if nxt is not None:
                    j = nxt
                    continue
                name = argv[j]
                break
        if not name:
            return emit({"ok": False, "reason": "missing_action", "alert": False}, 2)
        fn = ACTIONS.get(name)
        if fn is None:
            return emit({"ok": False, "reason": "unknown_action", "action": name, "alert": False}, 2)
        return emit(fn(argv))
    if not known_flag(primary):
        return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest", "alert": False}, 2)
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest", "alert": False}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
