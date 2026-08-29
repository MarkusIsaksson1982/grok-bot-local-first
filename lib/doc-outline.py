#!/usr/bin/env python3
"""doc-outline -- header-only markdown outline for progressive reading.

Offline, read-only, stdlib-only. Scans local markdown files and extracts
ATX headings only, so Grok Bot can progressively disclose docs without
paying the token cost of full bodies. Deterministic (use_bot:false).
No writes, no network.

Usage:
  python doc-outline.py --manifest
  python doc-outline.py --list-actions
  python doc-outline.py --action outline [--dir PATH]
  python doc-outline.py --action collect [--dir PATH]
  python doc-outline.py --action pack [--dir PATH]

Data lists are capped at 5 entries with accompanying totals.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ID = "doc-outline"
TITLE = "Markdown header-only outline for progressive reading"
SOURCE = "local"
KEYWORDS = ["docs", "markdown", "outline", "headers", "progressive"]
DEFAULT_ACTION = "outline"
USE_BOT = False
PRIORITY = 78
SCAN_CAP = 4000
MAX_FILE_BYTES = 2_000_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".idea", ".vscode", "target"}
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*(?:#+\s*)?$")
FENCE_RE = re.compile(r"^\s*```")

ACTIONS = {
    "outline": "Header-only outline per markdown file (default, capped).",
    "collect": "Counts: files, headings, depth distribution.",
    "pack": "Bounded review pack when outline is unusually large.",
}


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
            {"name": n, "use_bot": USE_BOT, "summary": ACTIONS[n]}
            for n in sorted(ACTIONS)
        ],
    }


def _list_actions() -> dict:
    return {
        "ok": True,
        "id": ID,
        "default_action": DEFAULT_ACTION,
        "actions": [{"name": n, "use_bot": USE_BOT, "summary": ACTIONS[n]} for n in sorted(ACTIONS)],
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


def _find_markdown(base: Path) -> list[Path]:
    out: list[Path] = []
    for p in base.rglob("*.md"):
        if len(out) >= SCAN_CAP:
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
        # also consider .markdown
        out.append(p)
    # also *.markdown
    for ext in (".markdown", ".mdx"):
        for p in base.rglob(f"*{ext}"):
            if len(out) >= SCAN_CAP:
                break
            if not p.is_file() or _is_skipped(p, base):
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(p)
    out.sort(key=lambda x: x.as_posix())
    return out[:SCAN_CAP]


def _extract_headings(path: Path) -> list[dict]:
    headings: list[dict] = []
    in_fence = False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return headings
    for idx, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            # strip trailing # already handled; truncate
            if len(heading_text) > 120:
                heading_text = heading_text[:117] + "..."
            if heading_text:
                headings.append({"level": level, "text": heading_text, "line": idx})
    return headings


def _scan(base: Path):
    files = _find_markdown(base)
    per_file: list[dict] = []
    total_headings = 0
    depth_counts: dict[int, int] = {}
    max_headings = 0
    for fp in files:
        rel = fp.relative_to(base).as_posix() if fp.is_absolute() else fp.as_posix()
        # for base="." pathlib relative fails if not under; use try
        try:
            rel = fp.relative_to(base).as_posix()
        except Exception:
            rel = fp.name
        hs = _extract_headings(fp)
        total_headings += len(hs)
        max_headings = max(max_headings, len(hs))
        for h in hs:
            depth_counts[h["level"]] = depth_counts.get(h["level"], 0) + 1
        per_file.append({"file": rel, "headings": hs, "headings_total": len(hs)})
    per_file.sort(key=lambda x: (-x["headings_total"], x["file"]))
    return files, per_file, total_headings, depth_counts, max_headings


def run_collect(base: Path) -> dict:
    files, per_file, total, depth_counts, max_h = _scan(base)
    avg = round(total / len(files), 1) if files else 0
    top = per_file[:5]
    top_out = [{"file": r["file"], "headings": r["headings_total"], "depths": sorted({h["level"] for h in r["headings"]})} for r in top]
    c, _ = _cap(top_out)
    # depth summary as sorted list
    depth_list = [{"level": k, "count": v} for k, v in sorted(depth_counts.items())]
    d, dt = _cap(depth_list)
    summary = f"{len(files)} md file(s), {total} heading(s), deepest h{max(depth_counts) if depth_counts else 0}"
    data = {
        "counts": {"files": len(files), "headings": total, "avg_per_file": avg, "max_per_file": max_h},
        "depths": d,
        "depths_total": dt,
        "top_files": c,
        "top_files_total": len(per_file),
        "truncated": len(per_file) > 5,
    }
    return {"ok": True, "alert": False, "summary": summary, "data": data, "action": "collect"}


def run_outline(base: Path) -> dict:
    files, per_file, total, _, _ = _scan(base)
    # cap files and headings per file
    outlines = []
    for r in per_file:
        hs, ht = _cap(r["headings"])
        # hs already is list of dicts level/text/line
        outlines.append({"file": r["file"], "headings": hs, "headings_total": ht, "truncated": ht > 5})
    o, ot = _cap(outlines)
    summary = f"outlined {len(per_file)} file(s), {total} heading(s)"
    data = {
        "counts": {"files": len(per_file), "headings": total},
        "outlines": o,
        "outlines_total": ot,
        "truncated": ot > 5,
    }
    return {"ok": True, "alert": False, "summary": summary, "data": data, "action": "outline"}


def run_pack(base: Path) -> dict:
    files, per_file, total, depth_counts, max_h = _scan(base)
    # pack thresholds: many files or many headings or very deep nesting
    large = total > 200 or len(files) > 50 or max_h > 50 or any(k >= 5 for k in depth_counts)
    # pick flagged files (those with many headings)
    flagged = [r for r in per_file if r["headings_total"] > 30]
    f, ft = _cap([{"file": r["file"], "headings": r["headings_total"]} for r in flagged])
    outlines = []
    for r in per_file[:5]:
        hs, ht = _cap(r["headings"])
        outlines.append({"file": r["file"], "headings": hs, "headings_total": ht})
    o, ot = _cap(outlines)
    summary = f"pack: {total} heading(s) in {len(files)} file(s)" + (" needs review" if large else " within bounds")
    data = {
        "counts": {"files": len(files), "headings": total},
        "needs_review": large,
        "flagged": f,
        "flagged_total": ft,
        "outlines": o,
        "outlines_total": ot,
        "truncated": ot > 5,
    }
    return {"ok": True, "alert": large, "summary": summary, "data": data, "action": "pack"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=ID, description=TITLE)
    ap.add_argument("--manifest", action="store_true", help="Print manifest (last line JSON).")
    ap.add_argument("--list-actions", action="store_true", help="List actions.")
    ap.add_argument("--action", choices=sorted(ACTIONS), default=None, help="Run action.")
    ap.add_argument("--dir", default=".", help="Base directory to inspect (read-only).")
    ap.add_argument("--root", default=None, help="Alias for --dir.")
    args = ap.parse_args(argv)

    # resolve base without hardcoded absolute
    base_arg = args.root if args.root is not None else args.dir
    base = Path(base_arg)
    if not base.is_dir():
        _emit(_abort(f"not a directory: {base_arg}", args.action or DEFAULT_ACTION))
        return 2

    if args.manifest:
        _emit(_manifest())
        return 0
    if args.list_actions:
        # validator only checks rc; emit JSON for consistency and also print names
        _emit(_list_actions())
        return 0
    action = args.action or DEFAULT_ACTION
    if action == "collect":
        _emit(run_collect(base))
    elif action == "outline":
        _emit(run_outline(base))
    elif action == "pack":
        _emit(run_pack(base))
    else:
        _emit(_abort(f"unknown action: {action}", action))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
