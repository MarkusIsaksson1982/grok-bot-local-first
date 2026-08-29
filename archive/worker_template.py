#!/usr/bin/env python3
"""Reference worker. External agents should match this shape."""
from __future__ import annotations

import json
import sys

MANIFEST = {
    "ok": True,
    "id": "worker-template",
    "title": "Reference worker (replace)",
    "source": "other",
    "priority": 90,
    "keywords": ["template"],
    "default_action": "collect",
    "actions": [
        {"name": "collect", "use_bot": False, "summary": "Do the local work"},
        {"name": "pack", "use_bot": True, "summary": "Tiny judgment pack for a bot"},
    ],
}


def emit(obj: object, code: int = 0) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    return code


def action_collect() -> dict:
    return {"ok": True, "alert": False, "summary": "collected", "data": {}, "action": "collect"}


def action_pack() -> dict:
    return {
        "ok": True,
        "alert": False,
        "summary": "no judgment needed",
        "data": {"needs_judgment": False},
        "action": "pack",
    }


ACTIONS = {"collect": action_collect, "pack": action_pack}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return emit({"ok": False, "reason": "no_flags", "hint": "--manifest"}, 2)
    if argv[1] == "--manifest":
        return emit(MANIFEST)
    if argv[1] == "--list-actions":
        return emit({"ok": True, "actions": MANIFEST["actions"], "default_action": MANIFEST["default_action"]})
    if argv[1] == "--action":
        if len(argv) < 3:
            return emit({"ok": False, "reason": "missing_action"}, 2)
        name = argv[2]
        fn = ACTIONS.get(name)
        if fn is None:
            return emit({"ok": False, "reason": "unknown_action", "action": name}, 2)
        return emit(fn())
    return emit({"ok": False, "reason": "unknown_flag", "hint": "--manifest"}, 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
