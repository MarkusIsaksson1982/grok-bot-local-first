#!/usr/bin/env python3
"""artifact-pd: PD fitness + keep/harvest/archive/helper/worker for drop artifacts.

Adjacent to artifact-gate. Stdlib only. Proposals authorized false.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

MANIFEST = {
    "ok": True,
    "id": "artifact-pd",
    "title": "PD fitness + keep/harvest/archive/helper/worker for drop artifacts",
    "source": "grok-4.7-high",
    "origin": {"grok_build": "1.0.41", "os": "windows-11"},
    "priority": 58,
    "keywords": [
        "artifact",
        "progressive-disclosure",
        "handoff",
        "helper",
        "worker-candidate",
        "harvest",
        "archive",
        "markdown",
        "json",
        "yaml",
        "powershell",
        "diff",
    ],
    "default_action": "scan",
    "verified_grok_bot": "0.58.0",
    "verified_at": "2026-10-02",
    "actions": [
        {
            "name": "scan",
            "use_bot": False,
            "summary": "PD fitness over files/dir; verdicts keep|harvest|archive|helper_script|worker_candidate|bot_review",
        },
        {
            "name": "harvest-hint",
            "use_bot": False,
            "summary": "Scan with harvest + helper consolidate excerpts emphasized",
        },
        {
            "name": "pack",
            "use_bot": True,
            "summary": "Judgment pack when bot_review or helper consolidate needs a call",
        },
    ],
}

SKIP_DIRS = frozenset({
    "__pycache__", ".git", "node_modules", "target", ".venv", "dist",
    "projects", "workspaces", "_archive", "_backups", "_cold_archive",
})
KNOWN_EXT = {
    ".py": "python",
    ".ps1": "powershell",
    ".diff": "diff",
    ".patch": "diff",
    ".c": "c",
    ".h": "c_header",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp_header",
    ".rs": "rust",
    ".toml": "toml",
    ".dll": "native_bin",
    ".so": "native_bin",
    ".dylib": "native_bin",
    ".lib": "native_bin",
    ".a": "native_bin",
    ".exp": "native_bin",
    ".pdb": "native_bin",
    ".o": "native_bin",
    ".obj": "native_bin",
    ".lock": "lockfile",
    ".cargo-lock": "lockfile",
    ".json": "json",
    ".jsonl": "jsonl",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".txt": "text",
}
PD_KEYS = [
    "disclosure",
    "progressive",
    "envelope",
    "handoff",
    "schema",
    "l0",
    "l1",
    "l2",
    "l3",
    "alert",
    "summary",
    "truncated",
    "asset_flags",
    "--level",
    "--scope",
    "integrate",
    "rework",
    "discard",
    "ok",
    "next",
]
ARCHIVE_PY_MARKERS = [
    "intentional_bug",
    "intentional bug",
    "dogfood calibration",
    "dogfood",
    "minimal calibration",
    "minimal text output",
    "off-by-one",
    "calibration script",
]
HELPER_MARKERS = [
    "argparse",
    "click",
    "typer",
    "if __name__",
    "def main",
    "pathlib",
    "subprocess",
]
CONSOLIDATE_HINTS = [
    "argparse",
    "--mode",
    "json.dumps",
    "two output",
    "directions",
]
LIST_CAP = 12
EXCERPT_CHARS = 480
READ_CAP = 64_000


def emit(obj: object, code: int = 0) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    return code


def _parse_flags(argv: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"paths": [], "level": "L0", "action": None}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("--manifest", "--list-actions"):
            out["flag"] = a
            return out
        if a == "--action" and i + 1 < len(argv):
            out["action"] = argv[i + 1]
            i += 2
            continue
        if a.startswith("--action="):
            out["action"] = a.split("=", 1)[1]
            i += 1
            continue
        if a == "--level" and i + 1 < len(argv):
            out["level"] = argv[i + 1].upper()
            i += 2
            continue
        if a.startswith("--level="):
            out["level"] = a.split("=", 1)[1].upper()
            i += 1
            continue
        if a in ("--path", "--file", "--dir", "--root") and i + 1 < len(argv):
            out["paths"].append(argv[i + 1])
            i += 2
            continue
        if a.startswith("--path=") or a.startswith("--file=") or a.startswith("--dir=") or a.startswith("--root="):
            out["paths"].append(a.split("=", 1)[1])
            i += 1
            continue
        if a == "--" and i + 1 < len(argv):
            out["paths"].extend(argv[i + 1 :])
            break
        if not a.startswith("-"):
            out["paths"].append(a)
            i += 1
            continue
        out.setdefault("unknown", []).append(a)
        i += 1
    return out


def _iter_files(paths: list[str]) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            continue
        if p.is_file():
            found.append(p)
            continue
        for dp, dirs, files in os.walk(p):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d.lower() != "lib"]
            for fn in files:
                found.append(Path(dp) / fn)
    seen: set[str] = set()
    out: list[Path] = []
    for f in sorted(found, key=lambda x: str(x).lower()):
        key = str(f.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def _read_text(path: Path) -> str:
    try:
        return path.read_bytes()[:READ_CAP].decode("utf-8", errors="replace")
    except OSError:
        return ""


def _rel_of(path: Path, root: Path | None) -> str:
    if root:
        try:
            return str(path.relative_to(root)).replace("\\", "/")
        except ValueError:
            pass
    return path.name


def _classify_python(path: Path, text: str, rel: str) -> dict[str, Any]:
    """Decision tree for .py: worker_candidate | helper_script | archive | keep."""
    low = text.lower()
    name_low = rel.lower()
    size = len(text.encode("utf-8", errors="ignore"))

    worker_hints = 0
    if "--manifest" in low or "manifest =" in low or '"manifest"' in low:
        worker_hints += 1
    if re.search(r'["\']id["\']\s*:\s*["\'][a-z0-9_-]+["\']', text[:12000]):
        worker_hints += 1
    if "default_action" in low or "list-actions" in low or "list_actions" in low:
        worker_hints += 1
    if "use_bot" in low and ("actions" in low or "action_" in low):
        worker_hints += 1
    if worker_hints >= 2:
        return {
            "class": "worker_candidate",
            "verdict": "worker_candidate",
            "helper_disposition": None,
            "hints": worker_hints,
            "note": "v1 stub: manifest-shaped worker; not auto-promoted",
        }

    archive_hit = any(m in low or m in name_low for m in ARCHIVE_PY_MARKERS)
    # path under returns/ with tiny demo scripts
    if archive_hit or (
        "/returns/" in f"/{name_low}"
        and size < 2500
        and worker_hints == 0
        and any(x in name_low for x in ("minimal", "bug", "dogfood", "direction", "calib"))
    ):
        return {
            "class": "helper_script",
            "verdict": "archive",
            "helper_disposition": "archive_helper",
            "hints": worker_hints,
            "note": "one-off dogfood/demo/intentional-bug script; tag archive",
        }

    helper_hits = sum(1 for m in HELPER_MARKERS if m in low)
    consolidate = any(m in low for m in CONSOLIDATE_HINTS) and size < 8000
    if helper_hits >= 2 or ("def main" in low and size < 12_000 and worker_hints < 2):
        disposition = "consolidate_universal" if consolidate else "save_as_helper"
        # very tiny with no reuse signals -> archive
        if size < 400 and helper_hits <= 2 and not consolidate:
            disposition = "archive_helper"
            verdict = "archive"
        else:
            verdict = "helper_script"
        return {
            "class": "helper_script",
            "verdict": verdict,
            "helper_disposition": disposition,
            "hints": helper_hits,
            "note": f"helper path; prefer {disposition}",
        }

    return {
        "class": "non_worker_python_stub",
        "verdict": "keep",
        "helper_disposition": None,
        "hints": worker_hints,
        "note": "unclassified py; keep pending Bot/refine",
    }



def _classify_powershell(path: Path, text: str, rel: str) -> dict[str, Any]:
    """v1: .ps1 as helper_script (save/consolidate/archive), not worker."""
    low = text.lower()
    name_low = rel.lower()
    size = len(text.encode("utf-8", errors="ignore"))
    archive_hit = any(m in low or m in name_low for m in ARCHIVE_PY_MARKERS)
    if archive_hit and size < 4000:
        return {
            "class": "helper_script",
            "verdict": "archive",
            "helper_disposition": "archive_helper",
            "hints": 0,
            "note": "dogfood/probe ps1; tag archive",
        }
    helper_hits = 0
    for m in ("param(", "param (", "get-childitem", "invoke-", "foreach-object", "write-host", "powershell", "-file", "grok"):
        if m in low:
            helper_hits += 1
    # probe runners / batch launchers often worth consolidate or save
    if any(x in name_low for x in ("run-", "probe", "ladder", "batch", "launch")) or helper_hits >= 2:
        disposition = "consolidate_universal" if ("foreach" in low or "get-childitem" in low or "batch" in name_low) else "save_as_helper"
        return {
            "class": "helper_script",
            "verdict": "helper_script",
            "helper_disposition": disposition,
            "hints": helper_hits,
            "note": f"powershell helper; prefer {disposition}",
        }
    if size < 500:
        return {
            "class": "helper_script",
            "verdict": "archive",
            "helper_disposition": "archive_helper",
            "hints": helper_hits,
            "note": "tiny ps1; archive unless named runner",
        }
    return {
        "class": "helper_script",
        "verdict": "helper_script",
        "helper_disposition": "save_as_helper",
        "hints": helper_hits,
        "note": "powershell helper default save_as_helper",
    }



def _classify_diff(path: Path, text: str, rel: str) -> dict[str, Any]:
    """Diff/patch: harvest the format signal, not the patch body.

    External handoffs may ask models for unified diffs instead of full files.
    Body content is usually superseded once applied; occurrence matters.
    """
    lines = text.splitlines()
    unified = False
    if lines:
        first = lines[0].lstrip()
        unified = first.startswith("---") or first.startswith("diff --git")
    if not unified:
        unified = any(ln.startswith("+++") for ln in lines[:20])
    hunks = sum(1 for ln in lines if ln.startswith("@@"))
    return {
        "class": "diff_patch",
        "verdict": "harvest",
        "helper_disposition": None,
        "hints": hunks,
        "note": "diff/patch format signal for external handoffs; prefer unified diff over full-file rewrites; body often archive-after-apply",
        "diff_meta": {
            "unified_like": bool(unified),
            "hunk_markers": hunks,
            "bytes": len(text.encode("utf-8", errors="ignore")),
            "harvest_body": False,
        },
    }



def _classify_native_source(path: Path, text: str, rel: str, kind: str) -> dict[str, Any]:
    """C/C++/Rust/toml sources. These are not Python workers for lib/."""
    name = rel.replace("\\", "/").lower()
    size = len(text.encode("utf-8", errors="ignore"))
    if kind == "toml" and ("cargo.toml" in name or "[package]" in text[:500].lower()):
        return {
            "class": "native_manifest",
            "verdict": "harvest",
            "helper_disposition": "save_as_helper",
            "hints": 1,
            "note": "Native package manifest. Keep it with that package, outside lib/.",
        }
    if kind in ("c", "c_header", "cpp", "cpp_header", "rust"):
        # build residue names
        if any(x in name for x in ("/target/", "/out/", ".obj", ".o")):
            return {
                "class": "native_build_residue",
                "verdict": "archive",
                "helper_disposition": "archive_helper",
                "hints": 0,
                "note": "native build residue path",
            }
        return {
            "class": "native_source",
            "verdict": "helper_script",
            "helper_disposition": "consolidate_universal" if size < 2000 else "save_as_helper",
            "hints": 1,
            "note": "Native source. File it in a specialized section outside lib/, not as a Python worker.",
        }
    return {
        "class": "native_other",
        "verdict": "keep",
        "helper_disposition": None,
        "hints": 0,
        "note": "Native-adjacent file. Leave it outside lib/.",
    }


def _classify_native_bin(path: Path, rel: str) -> dict[str, Any]:
    return {
        "class": "native_bin",
        "verdict": "archive",
        "helper_disposition": "archive_helper",
        "hints": 0,
        "note": "build product (.dll/.so/.lib/.pdb); do not ingest into lib/; archive after verify",
        "diff_meta": None,
    }


def _classify_lockfile(path: Path, rel: str) -> dict[str, Any]:
    return {
        "class": "lockfile",
        "verdict": "archive",
        "helper_disposition": "archive_helper",
        "hints": 0,
        "note": "lockfile usually archives with its build tree; keep it only when that package is kept",
    }


def _pd_score(kind: str, text: str) -> dict[str, Any]:
    low = text.lower()
    key_hits = {k: low.count(k) for k in PD_KEYS if k in low}
    headings = len(re.findall(r"(?m)^#+\s+\S+", text))
    fences = len(re.findall(r"(?m)^```", text))
    tables = len(re.findall(r"(?m)^\|.+\|", text))
    jsonish = False
    if kind in ("json", "jsonl"):
        jsonish = text.lstrip()[:1] in "{["
    if kind == "yaml":
        jsonish = bool(re.search(r"(?m)^[A-Za-z0-9_\"-]+:\s", text))
    structure = headings + fences + tables + (2 if jsonish else 0)
    density = sum(key_hits.values())
    score = min(100, density * 3 + structure * 4 + (10 if kind in ("markdown", "json", "yaml") else 0))
    form = "poor"
    if score >= 55 and (headings or fences or jsonish or key_hits.get("disclosure") or key_hits.get("schema")):
        form = "good"
    elif score >= 25:
        form = "partial"
    return {
        "score": score,
        "form": form,
        "key_hits": key_hits,
        "headings": headings,
        "fences": fences,
        "tables": tables,
        "structured_data": jsonish,
    }


def _verdict_doc(kind: str | None, pd: dict[str, Any], rel: str) -> str:
    if kind is None:
        return "bot_review"
    name = rel.replace("\\", "/").lower()
    harvest_name = any(
        x in name
        for x in (
            "handoff",
            "schema",
            "prompt",
            "envelope",
            "progressive",
            "dogfood-report",
            "result_placement",
            "context",
            "protocol",
            "learning",
            "scale",
            "forward",
            "pass1",
            "pass2",
            "summary",
        )
    )
    if pd["form"] == "good" and (harvest_name or pd["score"] >= 60):
        return "harvest"
    if pd["form"] in ("good", "partial") and harvest_name:
        return "harvest"
    if pd["form"] == "partial":
        return "keep"
    if pd["form"] == "poor" and pd["score"] < 20 and not harvest_name:
        return "archive"
    return "archive"


def _excerpt(text: str) -> str:
    for pat in (
        r"(?s)```json\n.*?```",
        r"(?s)```yaml\n.*?```",
        r"(?m)^#+\s+.*(handoff|disclosure|schema|envelope|progressive|protocol|scale).*$\n(?:.*\n){0,12}",
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()[:EXCERPT_CHARS]
    lines = [ln for ln in text.splitlines() if ln.strip()][:12]
    return "\n".join(lines)[:EXCERPT_CHARS]


def _analyze_file(path: Path, root: Path | None) -> dict[str, Any]:
    ext = path.suffix.lower()
    kind = KNOWN_EXT.get(ext)
    rel = _rel_of(path, root)
    text = _read_text(path) if kind else ""
    row: dict[str, Any] = {
        "file": rel,
        "bytes": path.stat().st_size if path.exists() else 0,
        "kind": kind or f"unknown:{ext or 'none'}",
        "authorized": False,
    }
    if kind in ("native_bin",):
        cls = _classify_native_bin(path, rel)
        row.update(
            {
                "pd_form": "n/a",
                "pd_score": 0,
                "verdict": cls["verdict"],
                "py_class": cls["class"],
                "helper_disposition": cls.get("helper_disposition"),
                "note": cls.get("note"),
            }
        )
        return row
    if kind in ("lockfile",):
        cls = _classify_lockfile(path, rel)
        row.update(
            {
                "pd_form": "n/a",
                "pd_score": 0,
                "verdict": cls["verdict"],
                "py_class": cls["class"],
                "helper_disposition": cls.get("helper_disposition"),
                "note": cls.get("note"),
            }
        )
        return row
    if kind in ("c", "c_header", "cpp", "cpp_header", "rust", "toml"):
        cls = _classify_native_source(path, text, rel, kind)
        row.update(
            {
                "pd_form": "n/a",
                "pd_score": 0,
                "verdict": cls["verdict"],
                "py_class": cls["class"],
                "helper_disposition": cls.get("helper_disposition"),
                "note": cls.get("note"),
            }
        )
        if cls["verdict"] in ("helper_script", "harvest"):
            row["excerpt"] = _excerpt(text)
        return row
    if kind in ("python", "powershell", "diff"):
        if kind == "python":
            cls = _classify_python(path, text, rel)
        elif kind == "powershell":
            cls = _classify_powershell(path, text, rel)
        else:
            cls = _classify_diff(path, text, rel)
        row.update(
            {
                "pd_form": "n/a",
                "pd_score": 0,
                "verdict": cls["verdict"],
                "py_class": cls["class"],
                "helper_disposition": cls.get("helper_disposition"),
                "note": cls.get("note"),
            }
        )
        if cls.get("diff_meta") is not None:
            row["diff_meta"] = cls["diff_meta"]
            head_lines = [ln for ln in text.splitlines()[:8] if ln.strip()]
            row["excerpt"] = "\n".join(head_lines)[:240]
            row["key_hits"] = {"diff_format": 1, "unified_like": int(bool(cls["diff_meta"].get("unified_like")))}
        elif cls["verdict"] in ("helper_script", "worker_candidate") or cls.get("helper_disposition"):
            row["excerpt"] = _excerpt(text)
        return row

    if not kind:
        row.update({"pd_form": "n/a", "pd_score": 0, "verdict": "bot_review", "reason": "unsupported_filetype_v1"})
        return row

    pd = _pd_score(kind, text)
    verdict = _verdict_doc(kind, pd, rel)
    row.update({"pd_form": pd["form"], "pd_score": pd["score"], "verdict": verdict, "py_class": None})
    if verdict in ("harvest", "helper_script", "worker_candidate"):
        row["excerpt"] = _excerpt(text)
        row["key_hits"] = pd.get("key_hits") or {}
    return row


def _folder_verdict(counts: dict[str, int]) -> str:
    if counts.get("bot_review"):
        return "bot_review"
    if counts.get("worker_candidate"):
        return "review_workers"
    if counts.get("helper_script") or counts.get("archive"):
        if counts.get("harvest"):
            return "harvest_helpers_archive"
        return "helpers_or_archive"
    if counts.get("harvest"):
        return "harvest_then_archive"
    if counts.get("keep") and not counts.get("archive"):
        return "keep"
    return "archive"


def _project(rows: list[dict[str, Any]], level: str) -> dict[str, Any]:
    keys = [
        "files",
        "harvest",
        "keep",
        "archive",
        "helper_script",
        "worker_candidate",
        "bot_review",
        "good_pd",
        "partial_pd",
        "archive_helper",
        "save_as_helper",
        "consolidate_universal",
    ]
    counts = {k: 0 for k in keys}
    counts["files"] = len(rows)
    for r in rows:
        v = r.get("verdict")
        if v in counts:
            counts[v] += 1
        if r.get("pd_form") == "good":
            counts["good_pd"] += 1
        if r.get("pd_form") == "partial":
            counts["partial_pd"] += 1
        hd = r.get("helper_disposition")
        if hd in counts:
            counts[hd] += 1
    order = {
        "worker_candidate": 0,
        "helper_script": 1,
        "harvest": 2,
        "bot_review": 3,
        "keep": 4,
        "archive": 5,
    }
    ranked = sorted(rows, key=lambda r: (order.get(r["verdict"], 9), -int(r.get("pd_score") or 0), r.get("file") or ""))
    truncated = len(ranked) > LIST_CAP and level in ("L0", "L1")
    folder_v = _folder_verdict(counts)
    if level == "L0":
        return {"counts": counts, "verdict": folder_v}
    if level == "L1":
        slim = [
            {
                "file": r["file"],
                "kind": r["kind"],
                "verdict": r["verdict"],
                "pd_form": r.get("pd_form"),
                "pd_score": r.get("pd_score", 0),
                "helper_disposition": r.get("helper_disposition"),
                "py_class": r.get("py_class"),
            }
            for r in ranked[:LIST_CAP]
        ]
        return {"counts": counts, "verdict": folder_v, "items": slim, "truncated": truncated}
    return {
        "counts": counts,
        "verdict": folder_v,
        "items": ranked,
        "truncated": False,
        "decision_tree": {
            "python": [
                "worker_candidate if manifest-shaped (--manifest/id/default_action/use_bot)",
                "archive (+ archive_helper) if dogfood/demo/intentional-bug/tiny returns calib",
                "helper_script with save_as_helper | consolidate_universal otherwise",
                "keep if unclassified stub",
            ],
            "docs_data": ["harvest if PD-good/spine name", "keep if partial", "archive if poor", "bot_review if unknown ext"],
        },
        "proposed": {
            "authorized": False,
            "text": "Archive helpers only after harvest. Promote workers only with signature. Consolidate helpers into lib/tools only when universal.",
            "items": [
                {"kind": "archive_tagged_py", "authorized": False, "note": "move archive-verdict .py with folder or to drop\\_archive"},
                {"kind": "harvest_docs", "authorized": False, "note": "copy harvest spine before folder archive"},
            ],
        },
    }


def run_scan(paths: list[str], level: str, harvest_focus: bool = False) -> dict[str, Any]:
    if not paths:
        return {
            "ok": True,
            "alert": False,
            "summary": "skip: no --path/--dir",
            "action": "harvest-hint" if harvest_focus else "scan",
            "data": {
                "verdict": "skip",
                "disclosure": {"level": level, "next": None, "truncated": False, "totals": {}},
            },
        }
    roots = [Path(p) for p in paths]
    root = roots[0] if len(roots) == 1 and roots[0].is_dir() else None
    files = _iter_files(paths)
    rows = [_analyze_file(f, root if root and root.is_dir() else None) for f in files]
    data = _project(rows, level)
    if harvest_focus:
        harvest_rows = [r for r in rows if r["verdict"] == "harvest"]
        helper_rows = [r for r in rows if r["verdict"] in ("helper_script", "archive") and r.get("kind") in ("python", "powershell")]
        worker_rows = [r for r in rows if r["verdict"] == "worker_candidate"]
        data["harvest"] = harvest_rows
        data["python_tagged"] = helper_rows + worker_rows
        data["handoff_worth"] = [
            {"file": r["file"], "pd_score": r.get("pd_score", 0), "excerpt": (r.get("excerpt") or "")[:EXCERPT_CHARS]}
            for r in sorted(harvest_rows, key=lambda x: -int(x.get("pd_score") or 0))
        ]
        data["helpers"] = [
            {
                "file": r["file"],
                "verdict": r["verdict"],
                "helper_disposition": r.get("helper_disposition"),
                "note": r.get("note"),
                "excerpt": (r.get("excerpt") or "")[:EXCERPT_CHARS],
            }
            for r in helper_rows
        ]
        data["diff_signals"] = [
            {
                "file": r["file"],
                "note": r.get("note"),
                "diff_meta": r.get("diff_meta"),
                "excerpt_headers_only": (r.get("excerpt") or "")[:240],
            }
            for r in rows
            if r.get("kind") == "diff" or r.get("py_class") == "diff_patch"
        ]
        data["worker_candidates"] = [
            {"file": r["file"], "note": r.get("note"), "excerpt": (r.get("excerpt") or "")[:EXCERPT_CHARS]}
            for r in worker_rows
        ]
    counts = data["counts"]
    folder_v = data["verdict"]
    alert = bool(counts.get("bot_review") or counts.get("worker_candidate"))
    summary = (
        f"artifact-pd {folder_v}: files={counts['files']} harvest={counts['harvest']} "
        f"helper={counts['helper_script']} worker={counts['worker_candidate']} "
        f"archive={counts['archive']} keep={counts['keep']} bot_review={counts['bot_review']}"
    )[:160]
    disc_next = None
    if level == "L0" and counts["files"]:
        disc_next = "scan"
    elif level == "L1" and (counts["harvest"] or counts["helper_script"] or counts["worker_candidate"] or counts["bot_review"]):
        disc_next = "scan"
    elif level == "L2" and (counts["bot_review"] or counts["worker_candidate"]):
        disc_next = "pack"
    data = dict(data)
    data["disclosure"] = {
        "level": level,
        "next": disc_next,
        "truncated": bool(data.get("truncated")),
        "totals": counts,
    }
    return {
        "ok": True,
        "alert": alert,
        "summary": summary,
        "action": "harvest-hint" if harvest_focus else "scan",
        "data": data,
    }


def run_pack(paths: list[str]) -> dict[str, Any]:
    base = run_scan(paths, "L2", harvest_focus=True)
    d = base.get("data") or {}
    c = d.get("counts") or {}
    need = bool(c.get("bot_review") or c.get("worker_candidate") or c.get("consolidate_universal"))
    base["action"] = "pack"
    base["alert"] = True if need else base.get("alert", False)
    d = dict(base.get("data") or {})
    d["needs_judgment"] = bool(need)
    d["choices"] = [
        "keep",
        "harvest",
        "archive",
        "save_as_helper",
        "consolidate_universal",
        "promote_worker_candidate",
        "rework",
    ]
    d["ask"] = (
        "Workers to promote, helpers to consolidate, or unsupported types to review?" if need else None
    )
    disc = dict(d.get("disclosure") or {})
    disc["level"] = "L3"
    disc["next"] = None
    d["disclosure"] = disc
    base["data"] = d
    base["summary"] = ("pack: judgment needed" if need else "pack: no judgment; harvest/archive/helper tags only")[:160]
    return base


def main(argv: list[str]) -> int:
    flags = _parse_flags(argv)
    if flags.get("flag") == "--manifest":
        return emit(MANIFEST)
    if flags.get("flag") == "--list-actions":
        return emit({"ok": True, "actions": MANIFEST["actions"], "default_action": MANIFEST["default_action"]})
    # Bare invocation (no --action/--path): contract shape for worker-lint / harness.
    if flags.get("action") is None and not flags.get("paths") and not flags.get("unknown"):
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest", "alert": False}, 2)
    action = flags.get("action") or MANIFEST["default_action"]
    paths = flags.get("paths") or []
    level = flags.get("level") or "L0"
    if level not in ("L0", "L1", "L2", "L3"):
        level = "L0"
    if action == "scan":
        return emit(run_scan(paths, level, harvest_focus=False))
    if action == "harvest-hint":
        lv = level if level in ("L1", "L2", "L3") else "L2"
        return emit(run_scan(paths, lv, harvest_focus=True))
    if action == "pack":
        return emit(run_pack(paths))
    return emit({"ok": False, "reason": "unknown_action", "action": action, "alert": False, "summary": "unknown_action", "data": {}}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))