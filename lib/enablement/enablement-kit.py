#!/usr/bin/env python3
"""enablement-kit -- deterministic helpers for rolling out AI agent tooling (skills, MCP connectors) in an organisation.

Stdlib only, offline. Core logic: core/enablement_core.py. The bot spends a turn only on judgment
(an adoption blocker to discuss, a governance case to word for Legal), never on counting.

Usage:
  python enablement-kit.py --manifest | --list-actions
  python enablement-kit.py --action skill-lint [--dir SKILLS_DIR]
  python enablement-kit.py --action adoption   [--events CSV] [--headcount JSON] [--week 2026-W42] [--target 0.5]
  python enablement-kit.py --action intake     [--proposals JSON]
  python enablement-kit.py --action pack       [--events CSV] [--headcount JSON] [--week W] [--proposals JSON]
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("enablement_core", HERE / "core" / "enablement_core.py")
core = importlib.util.module_from_spec(_spec)
sys.modules["enablement_core"] = core
_spec.loader.exec_module(core)
FX = HERE / "fixtures"

MANIFEST = {
    "verified_at": "2026-10-19",
    "verified_grok_bot": "0.58.0",
    "ok": True,
    "id": "enablement-kit",
    "title": "AI adoption kit: portable skill lint, adoption KPIs, governance intake screening",
    "source": "grok-bot",
    "origin": {"os": "linux"},
    "priority": 72,
    "keywords": ["enablement", "adoption", "skills", "skill.md", "mcp", "governance", "kpi", "training", "rollout"],
    "default_action": "skill-lint",
    "actions": [
        {"name": "skill-lint", "use_bot": False, "summary": "Lint a shared library of Agent Skills for portability (no model)"},
        {"name": "adoption", "use_bot": False, "summary": "Weekly active usage, adoption rate, maturity and spend flags per function"},
        {"name": "intake", "use_bot": False, "summary": "Rules-first screening of proposed AI tools / MCP connectors -> forum agenda"},
        {"name": "pack", "use_bot": True, "summary": "Small judgment pack: functions below target + top agenda items"},
    ],
}


def emit(obj: object, code: int = 0) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    return code


def opt(argv: list[str], flag: str, default: str) -> str:
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default


def _adoption(argv: list[str]) -> dict:
    ev = core.load_events(Path(opt(argv, "--events", str(FX / "usage.csv"))))
    hc = json.loads(Path(opt(argv, "--headcount", str(FX / "headcount.json"))).read_text())
    week = opt(argv, "--week", max(e["week"] for e in ev) if ev else "")
    return core.adoption_report(ev, hc, week, float(opt(argv, "--target", "0.5")))


def action_skill_lint(argv: list[str]) -> dict:
    r = core.lint_tree(Path(opt(argv, "--dir", str(FX / "skills"))))
    return {"ok": True, "alert": bool(r["with_errors"]), "action": "skill-lint",
            "summary": f"{r['skills']} skill(s); {r['clean']} clean; errors in {len(r['with_errors'])}", "data": r}


def action_adoption(argv: list[str]) -> dict:
    r = _adoption(argv)
    return {"ok": True, "alert": bool(r["below_target"] or r["spend_flags"]), "action": "adoption",
            "summary": f"{r['week']}: overall {r['overall_rate']:.0%}; below target {r['below_target']}; spend flags {r['spend_flags']}",
            "data": r}


def action_intake(argv: list[str]) -> dict:
    ps = json.loads(Path(opt(argv, "--proposals", str(FX / "proposals.json"))).read_text())
    r = core.agenda(ps)
    return {"ok": True, "alert": bool(r["agenda"] or r["rejected"]), "action": "intake",
            "summary": f"{r['proposals']} proposal(s): {len(r['fast_track'])} fast-track, {len(r['agenda'])} to forum, {len(r['rejected'])} rejected",
            "data": r}


def action_pack(argv: list[str]) -> dict:
    a = _adoption(argv)
    ps = json.loads(Path(opt(argv, "--proposals", str(FX / "proposals.json"))).read_text())
    g = core.agenda(ps)
    need = bool(a["below_target"] or g["agenda"])
    return {"ok": True, "alert": False, "action": "pack", "summary": "judgment pack" if need else "no judgment needed",
            "data": {"needs_judgment": need,
                     "ask": "For each function below target, propose one concrete next step (session, champion, skill). "
                            "For each agenda item, write two plain-language sentences for the Tech + Legal forum.",
                     "below_target": [r for r in a["rows"] if r["function"] in a["below_target"]],
                     "agenda": g["agenda"][:5]}}


ACTIONS = {"skill-lint": action_skill_lint, "adoption": action_adoption, "intake": action_intake, "pack": action_pack}


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
        try:
            return emit(fn(argv[3:]))
        except (OSError, ValueError, KeyError) as e:
            return emit({"ok": False, "alert": True, "action": argv[2], "reason": "bad_input", "summary": str(e)[:200], "data": {}}, 1)
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest"}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
