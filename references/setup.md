# Setup for a fresh Grok Bot

Do this on the machine that will run the bot, after you have read `SKILL.md`. `state/` and `drop/` are not in the kit. You create them.

## Place the kit

Copy this folder to a path you control. The folder can be named `grokkit`. On Linux, `~/grokkit` is a typical path; on Windows, `%USERPROFILE%\grokkit`. Python 3.12 or newer is required (`python3` on Linux/macOS, `python` or `py` on Windows).

Workers use `pathlib`. Forward slashes in commands work on every platform. 0.3.0 is developed and tested on Linux; 0.2.1 was run on Windows 11. `adapters/linux/setup.sh` or `adapters/windows/setup.ps1` does the folder step below and a smoke check.

## Create the local folders

Next to `grokkit.py`:

- `state/` holds `last.json`, `inbox.json`, and `log.jsonl`. The runner creates the files when you use `inbox` or `action`. Creating the directory first is enough.
- `drop/` holds incoming files that are not library workers yet.
- `drop/returns/` holds non-code answers from external models (return-v1, see `prompts/common.md`). `returns ingest` archives them under `state/returns/`.
- `drop/_archive` is optional. `freshness-gate` treats a path as an archive only when it is under `drop/_archive`. A directory that merely has "archive" in its name is not an archive.

## First bot on this machine

If a grok-bot-local-first bot is already running here, skip this. For the first one, `references/first-probe.md` suggests the name Grokkit and a single companion probe. One probe covers later bots from this same kit.

## First commands

```text
python grokkit.py list
python grokkit.py inbox
```

`list` should include every id in `LIBRARY_INVENTORY.md`. `inbox` stays quiet until a worker alerts.

## Version stamp

`sources.json` field `current_grok_bot` is `0.58.0`, the Grok Bot version verified on Linux on 2026-10-02. On Windows 11, 0.2.1 was verified with `0.61.0`. Worker manifests use the same `verified_grok_bot` value. `version-gate` compares those stamps to `sources.json`.

Grok Bot version numbers can differ per platform. Ask the user for the version they run, write it in `sources.json` `current_grok_bot`, then restamp with `python grokkit.py action version-gate stamp`.

## Optional library sections

Shipped workers are files directly in `lib/`. That is enough for this kit.

When the user's own work needs a separate group of workers, you can section the library. One comparable layout is a `core` section for the shipped set, plus other single-level folders for specialized work:

1. Create `lib/core/`.
2. Move the shipped `lib/*.py` files into `lib/core/`.
3. In `tasks.json`, change each `local.script` from `lib/<file>.py` to `lib/core/<file>.py`.
4. Add other folders directly under `lib/`, each named for the work that belongs there. Keep those names to one path segment.
5. Point later ingests at a section with `GROKKIT_LIB_LANE` set to that folder name. When the variable is unset, ingest still writes `lib/<file>.py`.

A worker in `lib/` or in `lib/<section>/` still finds the kit root. Do this only when the user wants more than one kind of worker library.

## A new worker

Match `references/worker_template.py`. Put the candidate in `drop/`.

```text
python grokkit.py action worker-lint collect
python grokkit.py action drop-triage d1
python grokkit.py ingest drop/<file>.py
```

Ingest copies the file into `lib/` and enables the task. Set `GROKKIT_LIB_LANE` when the file should land in `lib/<section>/` instead. The section name is whatever folder name you pass. It is not a fixed list.

## How a worker finds the kit

A file in `lib/` uses the parent of `lib/` as the kit root. A file in `lib/<section>/` uses the parent of that section. `GROKKIT_ROOT` overrides both when you must run a worker from another directory.

## Marketplace connection

An optional step, for the person who wants to look at the Agensi marketplace and decide whether it has value for this bot.

Agensi publishes a starting skill at https://www.agensi.io/skills/getting-started-with-agensi-mcp . The user downloads that skill manually and tells you the folder where they placed it. Read that folder and follow it from there. This kit does not download it, and it does not change `lib/`.
