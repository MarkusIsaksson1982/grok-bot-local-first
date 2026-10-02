#!/usr/bin/env python3
"""ret-lint: check, index, and slice non-code returns (schemas/return-v1.json).

A return is a text file from an external model or worker. It starts with
`# RETURN v1`, has `key: value` header lines, then `## [ID] Title` sections.
Stdlib only. Last stdout line is JSON.

  python ret-lint.py --action collect [--dir DIR]
  python ret-lint.py --action check --file PATH
  python ret-lint.py --action extract --file PATH --section ID
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

MANIFEST = {
    "verified_at": "2026-10-02",
    "verified_grok_bot": "0.58.0",
    "ok": True,
    "id": "ret-lint",
    "title": "Return Contract Linter (non-code returns)",
    "source": "local",
    "origin": {"grok_build": "none", "os": "linux"},
    "priority": 87,
    "keywords": ["return", "returns", "ingest", "section", "extract", "external", "provenance"],
    "default_action": "collect",
    "actions": [
        {"name": "collect", "use_bot": False, "summary": "Lint every return in drop/returns (or --dir)"},
        {"name": "check", "use_bot": False, "summary": "Lint one return (--file) and list its section ids"},
        {"name": "extract", "use_bot": False, "summary": "Print one section (--file, --section) capped"},
        {"name": "pack", "use_bot": True, "summary": "Tiny pack of failing returns, only if a person asks"},
    ],
}

DEFAULTS = {
    "magic": "# RETURN v1",
    "header_keys": ["source", "model", "date", "ask", "audience", "sections", "budget"],
    "provenance_keys": ["source", "model", "date"],
    "section_heading": r"^## \[([A-Za-z][A-Za-z0-9_.-]{0,31})\](?:[ \t]+(.*))?$",
    "audience": ["common", "bot", "agent"],
    "default_budget": 200,
    "max_budget": 400,
    "extract_cap_lines": 200,
}
LIST_CAP = 12
EXTS = (".md", ".txt", ".ret")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def emit(obj: object, code: int = 0) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    return code


def _lane_aware_root(script_dir: str) -> str | None:
    base = os.path.basename(script_dir).lower()
    parent = os.path.dirname(script_dir)
    if base == "lib":
        return parent
    if os.path.basename(parent).lower() == "lib":
        return os.path.dirname(parent)
    return None


def root_dir() -> str:
    env = os.environ.get("GROKKIT_ROOT")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    lane_root = _lane_aware_root(script_dir)
    if lane_root:
        return lane_root
    if env:
        return os.path.abspath(env)
    return script_dir


def load_schema(root: str) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    path = os.path.join(root, "schemas", "return-v1.json")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (OSError, json.JSONDecodeError):
        pass
    return cfg


def flag(argv: list[str], name: str) -> str | None:
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def resolve(root: str, p: str) -> str:
    return p if os.path.isabs(p) else os.path.abspath(os.path.join(root, p))


def _strip_outer_fence(lines: list[str]) -> list[str]:
    """Chat UIs often wrap the whole return in one fence. Drop that wrapper."""
    idx = [i for i, ln in enumerate(lines) if ln.strip()]
    if len(idx) < 2:
        return lines
    first, last = idx[0], idx[-1]
    if lines[first].strip().startswith(("```", "~~~")) and lines[last].strip() in ("```", "~~~"):
        return lines[first + 1:last]
    return lines


def parse(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Return {header, sections[{id,title,start,body}], errors, warnings, lines}."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").split("\n")
    lines = _strip_outer_fence(raw)
    while lines and not lines[-1].strip():
        lines.pop()
    errors: list[str] = []
    warnings: list[str] = []
    head_re = re.compile(str(schema["section_heading"]))
    magic = str(schema["magic"]).strip()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != magic:
        errors.append("missing first line %r" % magic)
    else:
        i += 1
    header: dict[str, str] = {}
    known = set(schema["header_keys"])
    while i < len(lines) and not head_re.match(lines[i]):
        ln = lines[i].strip()
        if ln and ":" in ln and not ln.startswith("#"):
            k, v = ln.split(":", 1)
            k = k.strip().lower()
            if k in known:
                header[k] = v.strip()
            else:
                warnings.append("unknown header key %r" % k)
        elif ln:
            warnings.append("stray text before first section (line %d)" % (i + 1))
        i += 1
    sections: list[dict[str, Any]] = []
    in_fence = False
    cur: dict[str, Any] | None = None
    for n in range(i, len(lines)):
        ln = lines[n]
        st = ln.strip()
        if st.startswith(("```", "~~~")):
            in_fence = not in_fence
        m = None if in_fence else head_re.match(ln)
        if m:
            cur = {"id": m.group(1), "title": (m.group(2) or "").strip(), "start": n + 1, "body": []}
            sections.append(cur)
        elif cur is not None:
            cur["body"].append(ln)
    if in_fence:
        warnings.append("unclosed code fence")
    for s in sections:
        while s["body"] and not s["body"][-1].strip():
            s["body"].pop()
        while s["body"] and not s["body"][0].strip():
            s["body"].pop(0)
        if not s["body"]:
            warnings.append("empty section %s" % s["id"])
    ids = [s["id"] for s in sections]
    if not sections:
        errors.append("no '## [ID] Title' sections")
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    if dupes:
        errors.append("duplicate section ids: %s" % ",".join(dupes))
    declared = [x.strip() for x in header.get("sections", "").replace(";", ",").split(",") if x.strip()]
    missing = [x for x in declared if x not in ids]
    if missing:
        errors.append("declared sections missing: %s" % ",".join(missing[:LIST_CAP]))
    extra = [x for x in ids if declared and x not in declared]
    if extra:
        warnings.append("undeclared sections: %s" % ",".join(extra[:LIST_CAP]))
    budget = int(schema["default_budget"])
    if "budget" in header:
        try:
            budget = int(header["budget"])
        except ValueError:
            errors.append("budget not an integer")
    hard = int(schema["max_budget"])
    if budget > hard:
        warnings.append("budget %d above max %d; using max" % (budget, hard))
        budget = hard
    if len(lines) > budget:
        errors.append("over line budget: %d > %d" % (len(lines), budget))
    aud = header.get("audience")
    if aud and aud not in schema["audience"]:
        warnings.append("audience %r not in %s" % (aud, "|".join(schema["audience"])))
    date = header.get("date")
    if date and not DATE_RE.match(date):
        errors.append("date not YYYY-MM-DD")
    for k in schema["provenance_keys"]:
        if not header.get(k):
            warnings.append("header missing %s (ingest can supply it)" % k)
    return {
        "header": header,
        "sections": sections,
        "errors": errors,
        "warnings": warnings,
        "lines": len(lines),
        "budget": budget,
    }


def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def check_file(path: str, schema: dict[str, Any]) -> dict[str, Any]:
    text = read_text(path)
    if text is None:
        return {"ok": False, "file": path, "errors": ["unreadable"], "warnings": []}
    p = parse(text, schema)
    return {
        "ok": not p["errors"],
        "file": path,
        "header": p["header"],
        "sections": [{"id": s["id"], "title": s["title"], "lines": len(s["body"])} for s in p["sections"]][:40],
        "section_total": len(p["sections"]),
        "lines": p["lines"],
        "budget": p["budget"],
        "errors": p["errors"][:LIST_CAP],
        "warnings": p["warnings"][:LIST_CAP],
    }


def action_check(argv: list[str], root: str, schema: dict[str, Any]) -> dict:
    f = flag(argv, "--file")
    if not f:
        return {"ok": False, "alert": True, "summary": "check needs --file PATH", "data": {}, "action": "check"}
    res = check_file(resolve(root, f), schema)
    ids = ",".join(s["id"] for s in res.get("sections") or [])
    if res["ok"]:
        summary = "return ok: %d section(s) [%s], %d/%d lines" % (
            res.get("section_total", 0), ids[:80], res.get("lines", 0), res.get("budget", 0))
    else:
        summary = "return invalid: %s" % "; ".join(res["errors"][:3])
    return {"ok": res["ok"], "alert": not res["ok"], "summary": summary[:200], "data": res, "action": "check"}


def action_extract(argv: list[str], root: str, schema: dict[str, Any]) -> dict:
    f = flag(argv, "--file")
    sid = flag(argv, "--section")
    if not f or not sid:
        return {"ok": False, "alert": True, "summary": "extract needs --file and --section", "data": {}, "action": "extract"}
    path = resolve(root, f)
    text = read_text(path)
    if text is None:
        return {"ok": False, "alert": True, "summary": "unreadable file", "data": {"file": path}, "action": "extract"}
    p = parse(text, schema)
    hit = next((s for s in p["sections"] if s["id"] == sid), None)
    if hit is None:
        return {
            "ok": False, "alert": True, "summary": "section %s not found" % sid,
            "data": {"file": path, "ids": [s["id"] for s in p["sections"]][:40]}, "action": "extract",
        }
    cap = int(schema["extract_cap_lines"])
    body = hit["body"]
    return {
        "ok": True,
        "alert": False,
        "summary": "section %s: %d line(s)%s" % (sid, len(body), " (truncated)" if len(body) > cap else ""),
        "data": {
            "id": sid,
            "title": hit["title"],
            "lines": len(body),
            "truncated": len(body) > cap,
            "body": "\n".join(body[:cap]),
        },
        "action": "extract",
    }


def _collect_rows(argv: list[str], root: str, schema: dict[str, Any]) -> tuple[str, list[dict]]:
    d = resolve(root, flag(argv, "--dir") or os.path.join("drop", "returns"))
    rows: list[dict] = []
    if os.path.isdir(d):
        for dirpath, dirnames, filenames in os.walk(d):
            dirnames[:] = sorted(x for x in dirnames if not x.startswith((".", "_")))
            for fn in sorted(filenames):
                if fn.lower().endswith(EXTS):
                    full = os.path.join(dirpath, fn)
                    r = check_file(full, schema)
                    r["file"] = os.path.relpath(full, root).replace("\\", "/")
                    rows.append(r)
    return d, rows


def action_collect(argv: list[str], root: str, schema: dict[str, Any]) -> dict:
    d, rows = _collect_rows(argv, root, schema)
    bad = [r for r in rows if not r["ok"]]
    return {
        "ok": True,
        "alert": bool(bad),
        "summary": "%d return(s); %d valid, %d invalid" % (len(rows), len(rows) - len(bad), len(bad)),
        "data": {
            "dir": d,
            "counts": {"returns": len(rows), "valid": len(rows) - len(bad), "invalid": len(bad)},
            "invalid": [{"file": r["file"], "errors": r["errors"][:3]} for r in bad[:LIST_CAP]],
            "invalid_total": len(bad),
        },
        "action": "collect",
    }


def action_pack(argv: list[str], root: str, schema: dict[str, Any]) -> dict:
    _, rows = _collect_rows(argv, root, schema)
    bad = [r for r in rows if not r["ok"]]
    return {
        "ok": True,
        "alert": False,
        "summary": "no judgment needed" if not bad else "%d invalid return(s) to re-ask or fix" % len(bad),
        "data": {
            "needs_judgment": bool(bad),
            "items": [{"file": r["file"], "errors": r["errors"][:3], "warnings": r["warnings"][:2]} for r in bad[:6]],
            "total": len(bad),
        },
        "action": "pack",
    }


ACTIONS = {"collect": action_collect, "check": action_check, "extract": action_extract, "pack": action_pack}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit({"ok": False, "alert": False, "reason": "no_flags", "hint": "--manifest"}, 2)
    if argv[1] == "--manifest":
        return emit(MANIFEST)
    if argv[1] == "--list-actions":
        return emit({"ok": True, "actions": MANIFEST["actions"], "default_action": MANIFEST["default_action"]})
    if argv[1] == "--action":
        if len(argv) < 3:
            return emit({"ok": False, "reason": "missing_action"}, 2)
        fn = ACTIONS.get(argv[2])
        if fn is None:
            return emit({"ok": False, "reason": "unknown_action", "action": argv[2]}, 2)
        root = os.path.abspath(flag(argv, "--root") or root_dir())
        return emit(fn(argv[3:], root, load_schema(root)))
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest"}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
