#!/usr/bin/env python3
"""Secret Pattern Scanner — local-first Grok Bot worker (stdlib only, read-only)."""
import argparse, json, os, sys, re, glob

DEFAULT_ROOT = os.getcwd()
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist",
             "build", ".idea", ".vscode", "target"}
TEXT_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml",
            ".toml", ".txt", ".cfg", ".ini", ".env", ".sh", ".md", ".csv",
            ".xml", ".html", ".css"}
PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})")),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("github", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
    ("secret_assign", re.compile(r"(?i)(?:api[_-]?key|apikey|secret|token|passwd|password|access[_-]?token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+]{8,})")),
]
STRONG = {"private_key", "aws_key", "aws_secret", "slack", "github", "google", "jwt"}
MAX_FILE = 1_000_000
MAX_HITS = 500
PLACEHOLDER = {"xxx", "changeme", "example", "test", "your",
               "placeholder", "todo", "dummy", "sample"}


def _emit(o):
    sys.stdout.write(json.dumps(o, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.exit(0)


def _err(m):
    _emit({"ok": False, "alert": True, "summary": m, "data": {}, "action": "error"})


def _cap(lst, n=5):
    return lst[:n], len(lst)


def _mask(line):
    out = line
    for _t, rx in PATTERNS:
        out = rx.sub("***", out)
    return out


def _scan(root):
    hits, scanned = [], 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if len(hits) >= MAX_HITS:
                break
            if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                continue
            fp = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(fp) > MAX_FILE:
                    continue
                with open(fp, "r", errors="ignore") as f:
                    lines = f.readlines()
            except Exception:
                continue
            scanned += 1
            for i, line in enumerate(lines, 1):
                if len(hits) >= MAX_HITS:
                    break
                if not line.strip():
                    continue
                masked = _mask(line)
                if masked == line:
                    continue
                for tname, rx in PATTERNS:
                    for m in rx.finditer(line):
                        val = m.group(1) if m.groups() else None
                        if tname in STRONG:
                            real = True
                        elif tname == "secret_assign":
                            low = (val or "").lower()
                            real = not any(w in low for w in PLACEHOLDER)
                        else:
                            real = False
                        hits.append({"file": os.path.relpath(fp, root), "line": i,
                                     "type": tname, "real": bool(real),
                                     "redacted": masked.strip()[:200]})
                        if len(hits) >= MAX_HITS:
                            break
                    if len(hits) >= MAX_HITS:
                        break
    return hits, scanned


def _suspected(hits):
    return [h for h in hits if h.get("real")]


def manifest():
    return {
        "ok": True,
        "id": "secrets-scan",
        "title": "Secret Pattern Scanner",
        "source": "local",
        "priority": 98,
        "keywords": ["security", "secrets", "redaction", "safety"],
        "default_action": "collect",
        "actions": [
            {"name": "collect", "use_bot": False, "summary": "Count likely secret hits by type"},
            {"name": "locate", "use_bot": False, "summary": "Safe locations + redaction needs, no values"},
            {"name": "pack", "use_bot": True, "summary": "Escalate only real suspected hits"},
        ],
    }


def do_action(args, name):
    root = os.path.abspath(args.root or DEFAULT_ROOT)
    hits, scanned = _scan(root)
    by_type = {}
    for h in hits:
        by_type[h["type"]] = by_type.get(h["type"], 0) + 1

    if name == "collect":
        _emit({"ok": True, "alert": bool(hits),
               "summary": f"{len(hits)} likely secret hit(s) across {scanned} file(s)",
               "data": {"counts": {"files_scanned": scanned,
                                   "hits_total": len(hits)},
                        "by_type": by_type},
               "action": "collect"})

    elif name == "locate":
        loc, tot = _cap(hits)
        _emit({"ok": True, "alert": bool(hits),
               "summary": f"{len(hits)} hit(s); showing up to 5 (values redacted)",
               "data": {"hits": loc, "hits_total": tot},
               "action": "locate"})

    elif name == "pack":
        sus = _suspected(hits)
        s, st = _cap(sus)
        _emit({"ok": True, "alert": bool(sus),
               "summary": (f"{len(sus)} suspected real secret(s) for review"
                           if sus else "no strongly suspected secrets"),
               "data": {"suspected": s, "suspected_total": st,
                        "needs_review": bool(sus)},
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
