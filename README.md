# grok-bot-local-first

Local-first kit for a Grok Bot. `grokkit.py` runs one Python worker and prints a single line of JSON. The bot spends a turn when that JSON asks for judgment.

A worker in `lib/` or in `lib/<section>/` finds this folder by walking up from its own file. `GROKKIT_ROOT` overrides that. Paths are not tied to a Windows username. `0.61.0` in `sources.json` is the Grok Bot version verified on Windows on 2026-09-27.

## Layout

- `grokkit.py` is the runner.
- `lib/` is the shipped worker library, as files directly in that folder. `references/setup.md` explains how a bot can later group those files under `lib/core/` and add other sections for its own work.
- `SKILL.md` is the file to point a fresh Grok Bot at.
- `references/setup.md` tells that bot how to create `state/` and `drop/` on the machine that will run the kit.
- `references/worker_template.py` is the shape of a new worker. It is not registered in `tasks.json`.
- `LIBRARY_INVENTORY.md` lists ids, priorities, and actions.
- `tasks.json` registers the shipped workers.
- `sources.json` holds `current_grok_bot`.

`state/` and `drop/` are local. They are gitignored and are not part of the tree until the bot creates them.

## Check

```text
python grokkit.py list
```

## Agensi

An Agensi marketplace edition of this kit is at https://www.agensi.io/skills/grok-bot-local-first-v0-1-0

What that marketplace is, and whether it is useful here, is left to the person running the bot. The optional download step is in `references/setup.md`.
