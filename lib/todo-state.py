#!/usr/bin/env python3
"""todo-state -- next actionable item + counts from local TODO/ledger.

Offline, read-only, stdlib-only. Scans local task sources (TODO.md,
tasks.json, ledger, backlog, plus TODO/FIXME comments) and returns a tiny,
capped summary plus the single next actionable item. Lets Grok Bot skip
full ledger ingestion. Deterministic except pack (genuine judgment on
blocked/stale).

Usage:
  python todo-state.py --manifest
  python todo-state.py --list-actions
  python todo-state.py --action next [--dir PATH]
  python todo-state.py --action collect [--dir PATH]
  python todo-state.py --action pack [--dir PATH]

Lists in data are always capped at 5 plus totals.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ID = "todo-state"
TITLE = "Next actionable item + counts from local TODO/ledger"
SOURCE = "local"
KEYWORDS = ["todo", "tasks", "ledger", "backlog", "next"]
DEFAULT_ACTION = "next"
PRIORITY = 86
SCAN_CAP = 4000
MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".idea", ".vscode", "target"}

ACTIONS = {
    "next": "Next actionable open item + bounded queue (default).",
    "collect": "Counts by status/priority across all sources.",
    "pack": "Bounded review pack when blocked or stale items need judgment.",
}

TASK_FILE_RE = re.compile(r"(todo|task|ledger|backlog|issues)", re.I)
CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+?)\s*$")
NUM_CHECKBOX_RE = re.compile(r"^\s*\d+\.\s*\[([ xX])\]\s*(.+?)\s*$")
TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG)\b\s*[:\-]?\s*(.+)", re.I)
PRIORITY_RE = re.compile(r"\b(P0|P1|P2|urgent|high|critical|blocked|blocker)\b", re.I)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _abort(summary: str, action: str = "abort") -> dict:
    return {"ok": False, "alert": True, "summary": summary, "data": {"error": summary}, "action": action}


def _manifest() -> dict:
    return {
        "ok": True,
        "id": ID,
        "title": TITLE,
        "source": SOURCE,
        "priority": PRIORITY,
        "keywords": KEYWORDS,
        "default_action": DEFAULT_ACTION,
        "actions": [
            {"name": n, "use_bot": (n == "pack"), "summary": ACTIONS[n]}
            for n in sorted(ACTIONS)
        ],
    }


def _list_actions() -> dict:
    return {
        "ok": True,
        "id": ID,
        "default_action": DEFAULT_ACTION,
        "actions": [{"name": n, "use_bot": (n == "pack"), "summary": ACTIONS[n]} for n in sorted(ACTIONS)],
    }


def _cap(lst: list, n: int = 5):
    return lst[:n], len(lst)


def _is_skipped(p: Path, base: Path) -> bool:
    try:
        rel = p.relative_to(base)
    except ValueError:
        return True
    for part in rel.parts:
        if part in SKIP_DIRS:
            return True
    return False


def _priority_of(text: str, kind: str = "") -> int:
    t = text.lower()
    if "p0" in t or "critical" in t or "urgent" in t or "blocker" in t:
        return 3
    if "p1" in t or "high" in t or kind.upper() == "FIXME":
        return 2
    if "p2" in t:
        return 1
    if kind.upper() in ("TODO", "FIXME", "HACK", "XXX", "BUG"):
        return 1
    # blocked is high priority for pack but not for next ordering? keep high
    if "blocked" in t:
        return 2
    return 0


def _is_blocked(text: str) -> bool:
    return bool(re.search(r"\b(blocked|blocker|waiting|depends|stuck)\b", text, re.I))


def _parse_json_tasks(path: Path, rel: str) -> list[dict]:
    out = []
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    if path.suffix.lower() == ".jsonl":
        lines = txt.splitlines()
        for idx, ln in enumerate(lines, 1):
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if isinstance(obj, dict):
                title = str(obj.get("title") or obj.get("task") or obj.get("text") or obj.get("name") or "")[:160]
                if not title:
                    continue
                status = str(obj.get("status") or ("done" if obj.get("done") else "open")).lower()
                if status not in ("open", "done", "closed", "completed"):
                    status = "open" if not obj.get("done") else "done"
                if status in ("closed", "completed"):
                    status = "done"
                prio = int(obj.get("priority", _priority_of(title)))
                blocked = bool(obj.get("blocked") or _is_blocked(title))
                out.append({"file": rel, "line": idx, "text": title, "status": status, "priority": prio, "blocked": blocked})
    else:
        try:
            data = json.loads(txt)
        except Exception:
            return out
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            # common shapes: {"tasks": [...]} or {"items": [...]}
            for k in ("tasks", "items", "todos", "ledger"):
                if isinstance(data.get(k), list):
                    items = data[k]
                    break
            if not items:
                # single task object
                items = [data]
        for idx, obj in enumerate(items, 1):
            if not isinstance(obj, dict):
                # string entry
                if isinstance(obj, str) and obj.strip():
                    out.append({"file": rel, "line": idx, "text": obj.strip()[:160], "status": "open", "priority": _priority_of(obj), "blocked": _is_blocked(obj)})
                continue
            title = str(obj.get("title") or obj.get("task") or obj.get("text") or obj.get("name") or "")[:160]
            if not title:
                continue
            status = str(obj.get("status") or ("done" if obj.get("done") else "open")).lower()
            if status not in ("open", "done"):
                status = "open" if status in ("todo", "backlog", "pending") else "done" if status in ("done", "closed", "completed") else "open"
            prio = int(obj.get("priority", _priority_of(title)))
            blocked = bool(obj.get("blocked") or _is_blocked(title))
            out.append({"file": rel, "line": idx, "text": title, "status": status, "priority": prio, "blocked": blocked})
    return out


def _parse_text_tasks(path: Path, rel: str) -> list[dict]:
    out = []
    try:
        txt = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    ext = path.suffix.lower()
    is_code = ext in (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".sh")
    for idx, line in enumerate(txt.splitlines(), 1):
        if len(line) > 500:
            line = line[:500]
        # checkbox first (applies to md/txt mainly but also generic)
        m = CHECKBOX_RE.match(line) or NUM_CHECKBOX_RE.match(line)
        if m:
            checked = m.group(1).lower() == "x"
            text = m.group(2).strip()[:160]
            if not text:
                continue
            status = "done" if checked else "open"
            prio = _priority_of(text)
            blocked = _is_blocked(text)
            out.append({"file": rel, "line": idx, "text": text, "status": status, "priority": prio, "blocked": blocked})
            continue
        # task marker - for code, require comment context and that comment starts with marker
        m2 = TODO_RE.search(line)
        if m2:
            if is_code:
                # extract comment part after delimiter; require it starts with marker
                comment = ""
                if ext == ".py":
                    pos = line.find("#")
                    if pos != -1:
                        comment = line[pos + 1 :].lstrip()
                elif ext in (".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs"):
                    pos = line.find("//")
                    if pos != -1:
                        comment = line[pos + 2 :].lstrip()
                    else:
                        pos = line.find("/*")
                        if pos != -1:
                            comment = line[pos + 2 :].lstrip()
                elif ext == ".sh":
                    pos = line.find("#")
                    if pos != -1:
                        comment = line[pos + 1 :].lstrip()
                if not comment:
                    continue
                # now comment must start with marker
                if not comment.upper().startswith(m2.group(1).upper()):
                    continue
            kind = m2.group(1)
            rest = m2.group(2).strip()[:160]
            text = f"{kind}: {rest}" if rest else kind
            prio = _priority_of(text, kind)
            blocked = _is_blocked(text)
            out.append({"file": rel, "line": idx, "text": text, "status": "open", "priority": prio, "blocked": blocked})
    return out


def _scan(base: Path):
    tasks: list[dict] = []
    files_scanned = 0
    # candidate task files (named) plus general code/md scan
    candidates: list[Path] = []
    all_files: list[Path] = []
    for p in base.rglob("*"):
        if len(all_files) >= SCAN_CAP:
            break
        if not p.is_file():
            continue
        if _is_skipped(p, base):
            continue
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        all_files.append(p)
        name_low = p.name.lower()
        if TASK_FILE_RE.search(name_low):
            candidates.append(p)

    # parse candidate ledger files first
    for p in candidates[:200]:  # cap ledger files
        try:
            rel = p.relative_to(base).as_posix()
        except Exception:
            rel = p.name
        ext = p.suffix.lower()
        if ext in (".json", ".jsonl"):
            tasks.extend(_parse_json_tasks(p, rel))
        else:
            tasks.extend(_parse_text_tasks(p, rel))
        files_scanned += 1

    # also scan general files for TODO markers if not already candidate
    # limit to relevant extensions to avoid binary
    TEXT_EXTS = {".md", ".markdown", ".mdx", ".txt", ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".rs", ".go", ".java"}
    for p in all_files:
        if p in candidates:
            continue
        if p.suffix.lower() not in TEXT_EXTS:
            continue
        try:
            rel = p.relative_to(base).as_posix()
        except Exception:
            rel = p.name
        # only scan if not already counted as ledger; but we want TODOs from code too
        parsed = _parse_text_tasks(p, rel)
        # filter to only TODO lines (checkbox already covered but we also want TODOs)
        # _parse_text_tasks for generic files will only return TODO/checkbox lines, so fine
        if parsed:
            tasks.extend(parsed)
            # count file as scanned if it contributed
        files_scanned += 1 if parsed else 0

    # dedup by file:line
    seen = set()
    uniq = []
    for t in tasks:
        key = (t["file"], t["line"], t["text"][:80])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)
    return uniq, files_scanned


def run_collect(base: Path) -> dict:
    tasks, files_scanned = _scan(base)
    open_tasks = [t for t in tasks if t["status"] == "open"]
    done_tasks = [t for t in tasks if t["status"] == "done"]
    blocked = [t for t in open_tasks if t["blocked"]]
    by_priority: dict[int, int] = {}
    for t in tasks:
        by_priority[t["priority"]] = by_priority.get(t["priority"], 0) + 1
    summary = f"{len(tasks)} task(s): {len(open_tasks)} open, {len(done_tasks)} done, {len(blocked)} blocked"
    sample, sample_total = _cap([{"file": t["file"], "text": t["text"][:80], "priority": t["priority"], "blocked": t["blocked"]} for t in open_tasks[:5]])
    # but need capped sample of open tasks; already capped via _cap
    data = {
        "counts": {"tasks": len(tasks), "open": len(open_tasks), "done": len(done_tasks), "blocked": len(blocked), "files_scanned": files_scanned},
        "by_priority": by_priority,
        "sample": sample,
        "sample_total": len(open_tasks),
        "truncated": len(open_tasks) > 5,
    }
    return {"ok": True, "alert": bool(blocked), "summary": summary, "data": data, "action": "collect"}


def run_next(base: Path) -> dict:
    tasks, files_scanned = _scan(base)
    open_tasks = [t for t in tasks if t["status"] == "open"]
    # order: priority desc, not blocked first, then file, line
    def _key(t):
        return (-t["priority"], t["blocked"], t["file"], t["line"])
    open_tasks.sort(key=_key)
    nxt = open_tasks[0] if open_tasks else None
    queue = [{"file": t["file"], "line": t["line"], "text": t["text"][:100], "priority": t["priority"], "blocked": t["blocked"]} for t in open_tasks]
    q, qt = _cap(queue)
    blocked = sum(1 for t in open_tasks if t["blocked"])
    summary = f"next: {nxt['text'][:60] if nxt else 'none'} ({len(open_tasks)} open)"
    data = {
        "counts": {"open": len(open_tasks), "blocked": blocked, "total": len(tasks), "files_scanned": files_scanned},
        "next": nxt if nxt else None,
        "queue": q,
        "queue_total": qt,
        "truncated": qt > 5,
    }
    return {"ok": True, "alert": bool(blocked and open_tasks), "summary": summary, "data": data, "action": "next"}


def run_pack(base: Path) -> dict:
    tasks, files_scanned = _scan(base)
    open_tasks = [t for t in tasks if t["status"] == "open"]
    blocked = [t for t in open_tasks if t["blocked"]]
    # stale: tasks that contain overdue-like words? use simple heuristic
    stale = [t for t in open_tasks if re.search(r"\b(overdue|stale|expired)\b", t["text"], re.I)]
    needs = bool(blocked or stale or len(open_tasks) > 50)
    q, qt = _cap([{"file": t["file"], "line": t["line"], "text": t["text"][:100], "priority": t["priority"]} for t in blocked])
    s, st = _cap([{"file": t["file"], "text": t["text"][:80]} for t in stale])
    # next item for context
    open_tasks.sort(key=lambda t: (-t["priority"], t["blocked"], t["file"], t["line"]))
    nxt = open_tasks[0] if open_tasks else None
    summary = f"pack: {len(blocked)} blocked, {len(stale)} stale" if needs else "no blocked/stale items"
    data = {
        "counts": {"open": len(open_tasks), "blocked": len(blocked), "stale": len(stale)},
        "needs_review": needs,
        "blocked": q,
        "blocked_total": qt,
        "stale": s,
        "stale_total": st,
        "next": nxt,
        "truncated": (qt > 5 or st > 5),
    }
    return {"ok": True, "alert": needs, "summary": summary, "data": data, "action": "pack"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=ID, description=TITLE)
    ap.add_argument("--manifest", action="store_true", help="Print manifest (last line JSON).")
    ap.add_argument("--list-actions", action="store_true", help="List actions.")
    ap.add_argument("--action", choices=sorted(ACTIONS), default=None, help="Run action.")
    ap.add_argument("--dir", default=".", help="Base directory to inspect (read-only).")
    ap.add_argument("--root", default=None, help="Alias for --dir.")
    args = ap.parse_args(argv)

    base_arg = args.root if args.root is not None else args.dir
    base = Path(base_arg)
    if not base.is_dir():
        _emit(_abort(f"not a directory: {base_arg}", args.action or DEFAULT_ACTION))
        return 2

    if args.manifest:
        _emit(_manifest())
        return 0
    if args.list_actions:
        _emit(_list_actions())
        return 0

    action = args.action or DEFAULT_ACTION
    if action == "collect":
        _emit(run_collect(base))
    elif action == "next":
        _emit(run_next(base))
    elif action == "pack":
        _emit(run_pack(base))
    else:
        _emit(_abort(f"unknown action: {action}", action))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
