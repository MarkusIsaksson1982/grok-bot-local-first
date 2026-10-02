#!/usr/bin/env python3
"""secrets-scan: redacted pattern scan of text files under the kit (stdlib only)."""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

MANIFEST = {
    "ok": True,
    "id": "secrets-scan",
    "title": "Secret Pattern Scanner",
    "source": "grok-4.7-high",
    "origin": {"grok_build": "1.0.41", "os": "windows-11"},
    "priority": 98,
    "keywords": ["security", "secrets", "redaction", "safety"],
    "default_action": "collect",
    "verified_grok_bot": "0.58.0",
    "verified_at": "2026-10-02",
    "actions": [
        {"name": "collect", "use_bot": False, "summary": "Count likely secret hits by type"},
        {"name": "locate", "use_bot": False, "summary": "Safe locations + redaction needs, no values"},
        {"name": "pack", "use_bot": True, "summary": "Escalate only real suspected hits"},
    ],
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "dist",
    "build",
    ".idea",
    ".vscode",
    "target",
}
TEXT_EXT = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".cfg",
    ".ini",
    ".env",
    ".sh",
    ".md",
    ".csv",
    ".xml",
    ".html",
    ".css",
}
PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret", re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?([A-Za-z0-9/+=]{40})")),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("github", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b")),
    (
        "secret_assign",
        re.compile(
            r"(?i)(?:api[_-]?key|apikey|secret|token|passwd|password|access[_-]?token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-./+]{8,})"
        ),
    ),
]
STRONG = {"private_key", "aws_key", "aws_secret", "slack", "github", "google", "jwt"}
MAX_FILE = 1_000_000
MAX_HITS = 500
MAX_SHOW = 5
PLACEHOLDER = {
    "xxx",
    "changeme",
    "example",
    "test",
    "your",
    "your_",
    "your-",
    "placeholder",
    "todo",
    "dummy",
    "sample",
    "redacted",
}


def emit(obj: object, code: int = 0) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    return code


def cap_summary(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."



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
    parent = os.path.dirname(script_dir)
    lane_root = _lane_aware_root(script_dir)
    if lane_root:
        return lane_root
    if os.path.basename(script_dir).lower() == "drop":
        return parent
    env = os.environ.get("GROKKIT_ROOT")
    if env:
        return os.path.abspath(env)
    return script_dir


def parse_root(argv: list[str]) -> str:
    root = root_dir()
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            root = argv[i + 1]
            i += 2
            continue
        i += 1
    return os.path.abspath(root)


def mask_line(line: str) -> str:
    out = line
    for _t, rx in PATTERNS:
        out = rx.sub("***", out)
    return out


def _emptyish(val: str) -> bool:
    core = val.strip().strip("'\"")
    if not core:
        return True
    if len(set(core)) <= 1:
        return True
    return all(c in "*_-." for c in core)


def is_real(tname: str, val: str | None) -> bool:
    if tname == "secret_assign":
        low = (val or "").lower()
        if _emptyish(low) or any(w in low for w in PLACEHOLDER):
            return False
        return True
    if tname in STRONG:
        return True
    return False


def slim_hit(h: dict[str, Any]) -> dict[str, Any]:
    return {
        "file": h["file"],
        "line": h["line"],
        "type": h["type"],
        "real": h["real"],
        "redacted": h["redacted"],
    }


def scan(root: str) -> tuple[list[dict[str, Any]], int]:
    hits: list[dict[str, Any]] = []
    scanned = 0
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
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except OSError:
                continue
            scanned += 1
            for i, line in enumerate(lines, 1):
                if len(hits) >= MAX_HITS:
                    break
                if not line.strip():
                    continue
                masked = mask_line(line)
                if masked == line:
                    continue
                for tname, rx in PATTERNS:
                    for m in rx.finditer(line):
                        val = m.group(1) if m.groups() else None
                        hits.append(
                            {
                                "file": os.path.relpath(fp, root),
                                "line": i,
                                "type": tname,
                                "real": is_real(tname, val),
                                "redacted": masked.strip()[:200],
                            }
                        )
                        if len(hits) >= MAX_HITS:
                            break
                    if len(hits) >= MAX_HITS:
                        break
        if len(hits) >= MAX_HITS:
            break
    return hits, scanned


def action_collect(root: str) -> dict[str, Any]:
    hits, scanned = scan(root)
    by_type: dict[str, int] = {}
    for h in hits:
        by_type[h["type"]] = by_type.get(h["type"], 0) + 1
    return {
        "ok": True,
        "alert": False,
        "summary": cap_summary("%d likely secret hit(s) across %d file(s)" % (len(hits), scanned)),
        "data": {
            "counts": {"files_scanned": scanned, "hits_total": len(hits)},
            "by_type": by_type,
        },
        "action": "collect",
    }


def action_locate(root: str) -> dict[str, Any]:
    hits, _scanned = scan(root)
    loc = [slim_hit(h) for h in hits[:MAX_SHOW]]
    return {
        "ok": True,
        "alert": False,
        "summary": cap_summary("%d hit(s); showing up to %d (values redacted)" % (len(hits), MAX_SHOW)),
        "data": {"hits": loc, "hits_total": len(hits), "truncated": len(hits) > MAX_SHOW},
        "action": "locate",
    }


def action_pack(root: str) -> dict[str, Any]:
    hits, _scanned = scan(root)
    sus = [slim_hit(h) for h in hits if h.get("real")]
    show = sus[:MAX_SHOW]
    has = bool(sus)
    if has:
        summary = "%d suspected real secret(s) for review" % len(sus)
    else:
        summary = "no strongly suspected secrets"
    return {
        "ok": True,
        "alert": has,
        "use_bot": has,
        "summary": cap_summary(summary),
        "data": {
            "suspected": show,
            "suspected_total": len(sus),
            "needs_review": has,
            "needs_judgment": has,
            "truncated": len(sus) > MAX_SHOW,
        },
        "action": "pack",
    }


ACTIONS = {
    "collect": action_collect,
    "locate": action_locate,
    "pack": action_pack,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit({"ok": False, "alert": False, "reason": "no_flags", "hint": "--manifest"}, 2)
    if argv[1] == "--manifest":
        return emit(MANIFEST)
    if argv[1] == "--list-actions":
        return emit(
            {
                "ok": True,
                "actions": MANIFEST["actions"],
                "default_action": MANIFEST["default_action"],
            }
        )
    if argv[1] == "--action":
        if len(argv) < 3:
            return emit({"ok": False, "reason": "missing_action"}, 2)
        name = argv[2]
        fn = ACTIONS.get(name)
        if fn is None:
            return emit({"ok": False, "reason": "unknown_action", "action": name}, 2)
        return emit(fn(parse_root(argv)))
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest"}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
