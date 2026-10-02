#!/usr/bin/env python3
"""Local workhorse for Grok Bot instances.

Call this CLI and read stdout JSON. Do not reimplement tasks. Do not read
worker sources unless debugging grokkit.

Usage:
  python grokkit.py sources
  python grokkit.py list
  python grokkit.py inbox
  python grokkit.py next
  python grokkit.py run <id>
  python grokkit.py manifest <id>
  python grokkit.py action <id> <action> [worker flags...]
  python grokkit.py ingest <path> [--source <source-id>] [--overwrite]
  python grokkit.py route <live request text>
  python grokkit.py returns ingest <path> [--source <id>] [--model <label>] [--date YYYY-MM-DD]
  python grokkit.py returns list [--limit N]
  python grokkit.py returns extract <ret-id> <section-id>
  python grokkit.py returns lint <path>

On Linux/macOS use python3 if python is not on PATH.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TASKS_PATH = ROOT / "tasks.json"
SOURCES_PATH = ROOT / "sources.json"
STATE_DIR = ROOT / "state"
LAST_PATH = STATE_DIR / "last.json"
INBOX_PATH = STATE_DIR / "inbox.json"
LOG_PATH = STATE_DIR / "log.jsonl"
LIB_DIR = ROOT / "lib"

def _section_name_ok(name: str) -> bool:
    """One folder name under lib/. The operator chooses it."""
    if name in {"", ".", "..", "__pycache__"}:
        return False
    return not any(ch in name for ch in "\\/")


def lib_dest_for(src_name: str, lane: str | None = None) -> Path:
    """Place an ingested worker in lib/, or in lib/<section>/ when a section is set.

    The shipped library is flat. GROKKIT_LIB_LANE is a single folder name the
    operator chooses, such as core, when they want a section.
    """
    lane = (lane or "").strip().lower()
    if lane in {"", "flat", "lib"}:
        dest_dir = LIB_DIR
    else:
        if not _section_name_ok(lane):
            raise ValueError("section must be one folder name, got %r" % lane)
        dest_dir = LIB_DIR / lane
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir / src_name

DROP_DIR = ROOT / "drop"
RETURNS_DIR = STATE_DIR / "returns"
RETURNS_INDEX = RETURNS_DIR / "index.jsonl"
RET_LINT = LIB_DIR / "ret-lint.py"

# Re-ingest keeps these when they are already on the row. --overwrite puts the template back.
_OPERATOR_FIELDS = ("enabled", "notes", "priority", "kind", "bot_when", "bot_never")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tasks() -> dict:
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))


def save_tasks(cfg: dict) -> None:
    TASKS_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def load_sources() -> dict:
    if not SOURCES_PATH.exists():
        return {"ok": False, "reason": "missing_sources_json", "root": str(ROOT)}
    data = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    data["ok"] = True
    data["resolved_root"] = str(ROOT)
    return data


def load_last() -> dict:
    if not LAST_PATH.exists():
        return {}
    try:
        return json.loads(LAST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_json(path: Path, obj: object) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def emit(obj: object) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")


def sorted_tasks(cfg: dict) -> list:
    tasks = [t for t in cfg.get("tasks", []) if t.get("enabled", True)]
    return sorted(tasks, key=lambda t: (t.get("priority", 100), t.get("id", "")))


def last_for(last: dict, task_id: str) -> dict | None:
    return (last.get("by_id") or {}).get(task_id)


def needs_bot(task: dict, result: dict | None) -> tuple[bool, str]:
    kind = task.get("kind") or "recorded"
    when = set(task.get("bot_when") or [])
    never = set(task.get("bot_never") or [])
    if kind == "live":
        if "live_unrouted" in when:
            return True, "live_unrouted"
        return False, "live_without_bot_when"
    if result is None:
        if "missing_result" in when or "always" in when:
            return True, "no_result_yet"
        return False, "recorded_not_run_local_first"
    status = "fail" if not result.get("ok", False) else ("alert" if result.get("alert") else "ok")
    if status in never:
        return False, "bot_never:%s" % status
    if status in when or "always" in when:
        return True, "bot_when:%s" % status
    return False, "no_bot_condition"


def build_inbox(cfg: dict, last: dict) -> dict:
    items = []
    for task in sorted_tasks(cfg):
        if task.get("kind") == "live":
            continue
        result = last_for(last, task["id"])
        use, reason = needs_bot(task, result)
        if use:
            items.append({
                "id": task["id"],
                "priority": task.get("priority", 100),
                "title": task.get("title"),
                "reason": reason,
                "summary": (result or {}).get("summary"),
            })
    return {
        "ok": True,
        "at": _now(),
        "bot_needed": bool(items),
        "quiet": not items and bool(cfg.get("policy", {}).get("quiet_if_inbox_empty", True)),
        "count": len(items),
        "items": items,
        "policy": cfg.get("policy"),
    }


def parse_json_out(stdout: str) -> dict | None:
    lines = [ln for ln in (stdout or "").strip().splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def run_script(path: Path, args: list[str]) -> dict:
    proc = subprocess.run(
        [sys.executable, str(path), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = parse_json_out(proc.stdout)
    if proc.returncode != 0 and payload is None:
        return {
            "ok": False,
            "alert": True,
            "summary": "Script failed (%s): %s" % (proc.returncode, (proc.stderr or proc.stdout)[:400]),
            "data": {"stderr": proc.stderr, "stdout": proc.stdout},
        }
    if payload is None:
        return {
            "ok": False,
            "alert": True,
            "summary": "Script did not print JSON",
            "data": {"stdout": proc.stdout},
        }
    payload.setdefault("ok", proc.returncode == 0)
    payload.setdefault("alert", not payload.get("ok"))
    payload.setdefault("summary", path.name)
    payload.setdefault("data", {})
    return payload


def task_script(task: dict) -> Path | None:
    script = (task.get("local") or {}).get("script")
    if not script:
        return None
    path = ROOT / script
    return path if path.exists() else None


def default_action(task: dict, manifest: dict | None = None) -> str:
    local = task.get("local") or {}
    if local.get("default_action"):
        return str(local["default_action"])
    if manifest and manifest.get("default_action"):
        return str(manifest["default_action"])
    return "collect"


def run_local(task: dict, action: str | None = None, extra: list[str] | None = None) -> dict:
    path = task_script(task)
    if path is None:
        return {
            "ok": False,
            "alert": True,
            "summary": "Task %s has no local script" % task.get("id"),
            "data": {},
        }
    name = action or default_action(task)
    args = ["--action", name]
    if extra:
        args.extend(extra)
    result = run_script(path, args)
    result.setdefault("action", name)
    return result


def record_result(task: dict, result: dict) -> None:
    last = load_last()
    by_id = last.setdefault("by_id", {})
    entry = {
        "id": task["id"],
        "at": _now(),
        "ok": bool(result.get("ok")),
        "alert": bool(result.get("alert")),
        "summary": result.get("summary"),
        "action": result.get("action"),
    }
    by_id[task["id"]] = entry
    last["at"] = _now()
    save_json(LAST_PATH, last)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": _now(), **entry}, ensure_ascii=True) + "\n")


def cmd_list(cfg: dict, last: dict) -> dict:
    rows = []
    for task in sorted_tasks(cfg):
        result = last_for(last, task["id"])
        use, reason = needs_bot(task, result)
        rows.append({
            "id": task["id"],
            "priority": task.get("priority", 100),
            "kind": task.get("kind"),
            "title": task.get("title"),
            "source": (task.get("local") or {}).get("source") or task.get("source"),
            "bot_now": use,
            "bot_reason": reason,
            "last": None if result is None else {
                "ok": result.get("ok"),
                "alert": result.get("alert"),
                "summary": result.get("summary"),
            },
        })
    return {"ok": True, "root": str(ROOT), "count": len(rows), "tasks": rows, "policy": cfg.get("policy")}


def find_task(cfg: dict, task_id: str) -> dict | None:
    return next((t for t in cfg.get("tasks", []) if t.get("id") == task_id), None)


def cmd_run(cfg: dict, task_id: str, action: str | None = None, extra: list[str] | None = None) -> dict:
    task = find_task(cfg, task_id)
    if task is None:
        return {"ok": False, "alert": True, "use_bot": True, "reason": "unknown_task", "id": task_id}
    result = run_local(task, action, extra)
    record_result(task, result)
    use, reason = needs_bot(task, result)
    return {
        "ok": bool(result.get("ok")),
        "alert": bool(result.get("alert")),
        "id": task_id,
        "action": result.get("action"),
        "use_bot": use,
        "reason": reason,
        "summary": result.get("summary"),
        "data": result.get("data") or {},
    }


def cmd_manifest(cfg: dict, task_id: str) -> dict:
    task = find_task(cfg, task_id)
    if task is None:
        return {"ok": False, "use_bot": True, "reason": "unknown_task", "id": task_id}
    path = task_script(task)
    if path is None:
        return {"ok": False, "use_bot": False, "reason": "no_script", "id": task_id}
    man = run_script(path, ["--manifest"])
    man["task_id"] = task_id
    man["script"] = str(path)
    bot_actions = [a.get("name") for a in (man.get("actions") or []) if isinstance(a, dict) and a.get("use_bot")]
    local_actions = [a.get("name") for a in (man.get("actions") or []) if isinstance(a, dict) and not a.get("use_bot")]
    man["bot_actions"] = bot_actions
    man["local_actions"] = local_actions
    man["use_bot"] = False
    man["rule"] = "Run local_actions via grokkit action. Run bot_actions only if inbox/alert/live says so."
    return man


def cmd_next(cfg: dict, last: dict) -> dict:
    for task in sorted_tasks(cfg):
        if task.get("kind") != "recorded":
            continue
        if not (task.get("local") or {}).get("script"):
            continue
        result = last_for(last, task["id"])
        if result is None or not result.get("ok") or result.get("alert"):
            ran = cmd_run(cfg, task["id"])
            ran["picked"] = "next"
            return ran
    inbox = build_inbox(cfg, load_last())
    return {
        "ok": True,
        "picked": None,
        "use_bot": inbox["bot_needed"],
        "reason": "nothing_due" if not inbox["bot_needed"] else "inbox_has_alerts",
        "summary": "No recorded local task due",
        "inbox": inbox,
    }


def tokenize(text: str) -> set[str]:
    raw = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return {p for p in raw.split() if p}


def cmd_route(cfg: dict, text: str) -> dict:
    tokens = tokenize(text)
    scored = []
    for task in sorted_tasks(cfg):
        if task.get("kind") == "live":
            continue
        keys = [task.get("id", "")] + list(task.get("keywords") or [])
        keys = [k.lower() for k in keys if k]
        hits = [k for k in keys if k in tokens or any(k in tok or tok in k for tok in tokens)]
        if hits:
            scored.append((len(hits), -task.get("priority", 100), task, hits))
    if not scored:
        return {
            "ok": True,
            "use_bot": True,
            "reason": "live_unrouted",
            "summary": "No local task matched this live request; Grok Bot usage is warranted.",
            "matched": None,
            "text": text,
        }
    scored.sort(reverse=True)
    _, _, task, hits = scored[0]
    action = default_action(task)
    if task.get("kind") == "recorded" and (task.get("local") or {}).get("script"):
        return {
            "ok": True,
            "use_bot": False,
            "reason": "live_matched_local",
            "summary": "Run local task %s action %s instead of spending a Grok Bot loop." % (task["id"], action),
            "matched": task["id"],
            "hits": hits,
            "run": [sys.executable, str(ROOT / "grokkit.py"), "action", task["id"], action],
        }
    use, reason = needs_bot(task, last_for(load_last(), task["id"]))
    return {
        "ok": True,
        "use_bot": use,
        "reason": reason,
        "summary": task.get("title"),
        "matched": task["id"],
        "hits": hits,
    }


def slug_from_path(path: Path) -> str:
    return path.stem.replace("_", "-").lower()


def _refresh_existing_task(existing: dict, row: dict, overwrite: bool) -> dict:
    """Refresh manifest-derived fields. Keep operator fields unless overwrite."""
    if not overwrite:
        for key in _OPERATOR_FIELDS:
            if key in existing:
                row[key] = existing[key]
        old_local = existing.get("local")
        new_local = row.get("local")
        if isinstance(old_local, dict) and isinstance(new_local, dict):
            merged = {k: v for k, v in old_local.items() if k not in new_local}
            merged.update(new_local)
            row["local"] = merged
    existing.update(row)
    return existing


def cmd_ingest(cfg: dict, raw_path: str, source: str | None, overwrite: bool = False) -> dict:
    src = Path(raw_path)
    if not src.is_absolute():
        src = (ROOT / src).resolve()
    if not src.exists():
        return {"ok": False, "use_bot": False, "reason": "missing_file", "path": str(src)}
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    lane = os.environ.get("GROKKIT_LIB_LANE") or ""
    dest = lib_dest_for(src.name, lane)
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    man = run_script(dest, ["--manifest"])
    if not man.get("ok"):
        return {
            "ok": False,
            "use_bot": True,
            "reason": "manifest_failed",
            "summary": man.get("summary") or man.get("reason"),
            "data": man,
        }
    task_id = str(man.get("id") or slug_from_path(dest))
    wanted = dest.with_name(task_id.replace("-", "_") + ".py")
    # keep original filename; record relative script path
    rel = str(dest.relative_to(ROOT)).replace("\\", "/")
    source_id = source or man.get("source") or "other"
    default = str(man.get("default_action") or "collect")
    keywords = list(man.get("keywords") or [task_id])
    bot_when = list(man.get("bot_when") or ["fail", "alert"])
    bot_never = list(man.get("bot_never") or ["ok"])
    row = {
        "id": task_id,
        "title": man.get("title") or task_id,
        "priority": int(man.get("priority") or 50),
        "kind": "recorded",
        "enabled": True,
        "source": source_id,
        "keywords": keywords,
        "local": {"script": rel, "default_action": default, "source": source_id},
        "bot_when": bot_when,
        "bot_never": bot_never,
        "notes": "ingested from %s" % src.name,
    }
    tasks = cfg.setdefault("tasks", [])
    existing = find_task(cfg, task_id)
    if existing is not None:
        stored = _refresh_existing_task(existing, row, overwrite)
        updated = True
    else:
        tasks.append(row)
        stored = row
        updated = False
    save_tasks(cfg)
    bot_actions = [a.get("name") for a in (man.get("actions") or []) if isinstance(a, dict) and a.get("use_bot")]
    local_actions = [a.get("name") for a in (man.get("actions") or []) if isinstance(a, dict) and not a.get("use_bot")]
    return {
        "ok": True,
        "use_bot": False,
        "id": task_id,
        "updated": updated,
        "enabled": stored.get("enabled", True),
        "source": source_id,
        "script": rel,
        "default_action": default,
        "local_actions": local_actions,
        "bot_actions": bot_actions,
        "summary": "Ingested %s. Run local_actions only unless inbox/alert says otherwise." % task_id,
        "run_default": [sys.executable, str(ROOT / "grokkit.py"), "action", task_id, default],
    }



def parse_iso(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def cmd_last_run(cfg: dict, last: dict, min_days: float = 7.0, max_span_days: float = 30.0) -> dict:
    """Hub last-run report from state/last.json (written by action/run). Flagging only."""
    now = datetime.now(timezone.utc)
    by_id = last.get("by_id") if isinstance(last, dict) else {}
    if not isinstance(by_id, dict):
        by_id = {}
    rows = []
    flagged = []
    max_band = []
    never = []
    for task in sorted_tasks(cfg):
        tid = task["id"]
        entry = by_id.get(tid) if isinstance(by_id.get(tid), dict) else None
        if not entry:
            never.append(tid)
            rows.append({
                "id": tid,
                "enabled": bool(task.get("enabled", True)),
                "last_at": None,
                "age_days": None,
                "band": "never_run",
                "action": None,
            })
            continue
        dt = parse_iso(entry.get("at"))
        if dt is None:
            rows.append({
                "id": tid,
                "enabled": bool(task.get("enabled", True)),
                "last_at": entry.get("at"),
                "age_days": None,
                "band": "unparseable_at",
                "action": entry.get("action"),
            })
            continue
        age = (now - dt.astimezone(timezone.utc)).total_seconds() / 86400.0
        if age >= max_span_days:
            band = "max_span"
            max_band.append(tid)
            flagged.append(tid)
        elif age >= min_days:
            band = "flag"
            flagged.append(tid)
        else:
            band = "fresh"
        rows.append({
            "id": tid,
            "enabled": bool(task.get("enabled", True)),
            "last_at": entry.get("at"),
            "age_days": round(age, 2),
            "band": band,
            "action": entry.get("action"),
            "ok": entry.get("ok"),
            "alert": entry.get("alert"),
        })
    return {
        "ok": True,
        "alert": bool(flagged),
        "summary": "last-run flag=%d max_span=%d never=%d (min=%.0fd max=%.0fd)" % (
            len(flagged), len(max_band), len(never), min_days, max_span_days,
        ),
        "data": {
            "min_days": min_days,
            "max_span_days": max_span_days,
            "flagged": flagged,
            "max_span": max_band,
            "never_run": never,
            "rows": rows,
            "source": "state/last.json via hub action/run record_result",
            "note": "proposals only; does not disable workers; full retest not implied",
        },
        "action": "last-run",
    }


# ---------------------------------------------------------------------------
# returns: non-code answers from external models/workers (schemas/return-v1.json)
# ---------------------------------------------------------------------------

def _source_ids() -> list[str]:
    data = load_sources()
    return [str(r.get("id")) for r in (data.get("sources") or []) if isinstance(r, dict) and r.get("id")]


def _ret_lint_path() -> Path | None:
    if RET_LINT.exists():
        return RET_LINT
    hits = sorted(LIB_DIR.glob("*/ret-lint.py"))
    return hits[0] if hits else None


def _ret_lint(args: list[str]) -> dict:
    path = _ret_lint_path()
    if path is None:
        return {"ok": False, "alert": True, "summary": "lib/ret-lint.py missing", "data": {}}
    return run_script(path, args)


def _read_index() -> list[dict]:
    if not RETURNS_INDEX.exists():
        return []
    rows = []
    for ln in RETURNS_INDEX.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return rows


def _slug(text: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:40] or "x"


def cmd_returns_ingest(raw_path: str, source: str | None, model: str | None, date: str | None) -> dict:
    src = Path(raw_path)
    if not src.is_absolute():
        src = (Path.cwd() / src) if (Path.cwd() / src).exists() else (ROOT / src)
    src = src.resolve()
    if not src.exists():
        return {"ok": False, "use_bot": False, "reason": "missing_file", "path": str(src)}
    lint = _ret_lint(["--action", "check", "--file", str(src)])
    data = lint.get("data") or {}
    if not lint.get("ok"):
        return {
            "ok": False, "alert": True, "use_bot": False, "reason": "return_lint_failed",
            "summary": lint.get("summary"), "errors": data.get("errors") or [],
            "hint": "Re-ask the source with prompts/common.md, or fix the file, then ingest again.",
        }
    header = data.get("header") or {}
    source = source or header.get("source")
    model = model or header.get("model")
    date = date or header.get("date") or datetime.now().strftime("%Y-%m-%d")
    if not source or not model:
        return {"ok": False, "alert": True, "use_bot": False, "reason": "missing_provenance",
                "summary": "Need source and model (header or --source/--model).", "have": {"source": source, "model": model}}
    known = _source_ids()
    if source not in known:
        return {"ok": False, "alert": True, "use_bot": False, "reason": "unknown_source", "source": source, "known": known}
    if len(date) != 10 or date[4] != "-" or date[7] != "-" or parse_iso(date + "T00:00:00") is None:
        return {"ok": False, "alert": True, "use_bot": False, "reason": "bad_date", "date": date}
    raw = src.read_bytes()
    sha = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    ids = [s.get("id") for s in data.get("sections") or []]
    for row in _read_index():
        if row.get("sha256") == sha:
            return {"ok": True, "use_bot": False, "duplicate": True, "ret_id": row.get("ret_id"),
                    "sections": row.get("sections"), "summary": "Already ingested as %s." % row.get("ret_id")}
    ret_id = "%s-%s-%s" % (date, _slug(source), sha[:8])
    dest = RETURNS_DIR / date / (ret_id + ".md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    row = {
        "ret_id": ret_id,
        "source": source,
        "model": model,
        "date": date,
        "ask": header.get("ask"),
        "audience": header.get("audience"),
        "sha256": sha,
        "sections": ids,
        "lines": data.get("lines"),
        "budget": data.get("budget"),
        "from": src.name,
        "archive": str(dest.relative_to(ROOT)).replace("\\", "/"),
        "ingested_at": _now(),
    }
    with RETURNS_INDEX.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True) + "\n")
    return {
        "ok": True,
        "use_bot": False,
        "duplicate": False,
        "ret_id": ret_id,
        "source": source,
        "model": model,
        "date": date,
        "sections": ids,
        "lines": data.get("lines"),
        "warnings": (data.get("warnings") or [])[:6],
        "archive": row["archive"],
        "summary": "Archived %s (%d section(s)). Extract only the sections you need." % (ret_id, len(ids)),
        "extract": [sys.executable, str(ROOT / "grokkit.py"), "returns", "extract", ret_id, "<section-id>"],
    }


def cmd_returns_list(limit: int = 12) -> dict:
    rows = _read_index()
    tail = rows[-limit:] if limit > 0 else rows
    slim = [{k: r.get(k) for k in ("ret_id", "source", "model", "date", "ask", "sections")} for r in reversed(tail)]
    return {"ok": True, "use_bot": False, "total": len(rows), "count": len(slim), "returns": slim}


def cmd_returns_extract(ret_id: str, section: str) -> dict:
    row = next((r for r in reversed(_read_index()) if r.get("ret_id") == ret_id), None)
    if row is None:
        return {"ok": False, "use_bot": False, "reason": "unknown_ret_id", "ret_id": ret_id}
    path = ROOT / str(row.get("archive"))
    if not path.exists():
        return {"ok": False, "alert": True, "use_bot": False, "reason": "archive_missing", "path": str(path)}
    res = _ret_lint(["--action", "extract", "--file", str(path), "--section", section])
    res["ret_id"] = ret_id
    res["provenance"] = {k: row.get(k) for k in ("source", "model", "date", "ask")}
    res["use_bot"] = False
    return res


def cmd_returns(argv: list[str]) -> tuple[dict, int]:
    sub = argv[0] if argv else ""
    rest = argv[1:]

    def opt(name: str) -> str | None:
        if name in rest:
            i = rest.index(name)
            if i + 1 < len(rest):
                return rest[i + 1]
        return None

    if sub == "ingest" and rest:
        return cmd_returns_ingest(rest[0], opt("--source"), opt("--model"), opt("--date")), 0
    if sub == "list":
        try:
            limit = int(opt("--limit") or 12)
        except ValueError:
            return {"ok": False, "use_bot": False, "reason": "bad_limit"}, 2
        return cmd_returns_list(limit), 0
    if sub == "extract" and len(rest) >= 2:
        return cmd_returns_extract(rest[0], rest[1]), 0
    if sub == "lint" and rest:
        p = Path(rest[0])
        if not p.is_absolute():
            p = (Path.cwd() / p) if (Path.cwd() / p).exists() else (ROOT / p)
        res = _ret_lint(["--action", "check", "--file", str(p.resolve())])
        res["use_bot"] = False
        return res, 0
    return {"ok": False, "use_bot": False, "reason": "bad_returns_usage",
            "usage": ["returns ingest <path> [--source ID] [--model LABEL] [--date YYYY-MM-DD]",
                      "returns list [--limit N]", "returns extract <ret-id> <section-id>", "returns lint <path>"]}, 2


def main(argv: list[str]) -> int:
    help_obj = {
        "ok": True,
        "root": str(ROOT),
        "commands": [
            "sources", "list", "inbox", "next",
            "run <id>", "manifest <id>", "action <id> <name> [worker flags...]",
            "last-run [--min-days N] [--max-span-days N]", "ingest <path> [--source <id>] [--overwrite]", "route <text>",
            "returns ingest|list|extract|lint ...",
        ],
        "rule": "Bots: never read worker source. inbox/route first. action for one slice. quiet if inbox.quiet.",
    }
    if len(argv) < 2 or argv[1] in {"-h", "--help", "help"}:
        emit(help_obj)
        return 0
    cmd = argv[1]
    if cmd == "sources":
        emit(load_sources())
        return 0
    if cmd == "returns":
        obj, code = cmd_returns(argv[2:])
        emit(obj)
        return code
    cfg = load_tasks()
    last = load_last()
    if cmd == "list":
        emit(cmd_list(cfg, last))
        return 0
    if cmd == "inbox":
        inbox = build_inbox(cfg, last)
        save_json(INBOX_PATH, inbox)
        emit(inbox)
        return 0
    if cmd == "next":
        emit(cmd_next(cfg, last))
        return 0
    if cmd == "run":
        if len(argv) < 3:
            emit({"ok": False, "use_bot": False, "reason": "missing_id"})
            return 2
        action = None
        if len(argv) >= 5 and argv[3] == "--action":
            action = argv[4]
        emit(cmd_run(cfg, argv[2], action))
        return 0
    if cmd == "manifest":
        if len(argv) < 3:
            emit({"ok": False, "use_bot": False, "reason": "missing_id"})
            return 2
        emit(cmd_manifest(cfg, argv[2]))
        return 0
    if cmd == "action":
        if len(argv) < 4:
            emit({"ok": False, "use_bot": False, "reason": "missing_id_or_action"})
            return 2
        emit(cmd_run(cfg, argv[2], argv[3], argv[4:]))
        return 0
    if cmd == "ingest":
        if len(argv) < 3:
            emit({"ok": False, "use_bot": False, "reason": "missing_path"})
            return 2
        source = None
        overwrite = False
        extra = argv[3:]
        i = 0
        while i < len(extra):
            if extra[i] == "--overwrite":
                overwrite = True
                i += 1
                continue
            if extra[i] == "--source" and i + 1 < len(extra):
                source = extra[i + 1]
                i += 2
                continue
            i += 1
        emit(cmd_ingest(cfg, argv[2], source, overwrite))
        return 0
    if cmd in {"last-run", "lastrun", "last_run"}:
        min_days = 7.0
        max_span = 30.0
        extra = argv[2:]
        if "--min-days" in extra:
            i = extra.index("--min-days")
            if i + 1 < len(extra):
                try:
                    min_days = float(extra[i + 1])
                except ValueError:
                    emit({"ok": False, "use_bot": False, "reason": "bad_min_days"})
                    return 2
        if "--max-span-days" in extra:
            i = extra.index("--max-span-days")
            if i + 1 < len(extra):
                try:
                    max_span = float(extra[i + 1])
                except ValueError:
                    emit({"ok": False, "use_bot": False, "reason": "bad_max_span_days"})
                    return 2
        emit(cmd_last_run(cfg, last, min_days, max_span))
        return 0
    if cmd == "route":
        text = " ".join(argv[2:]).strip()
        if not text:
            emit({"ok": False, "use_bot": True, "reason": "empty_live_request"})
            return 2
        emit(cmd_route(cfg, text))
        return 0
    emit({"ok": False, "use_bot": True, "reason": "unknown_command", "command": cmd})
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
