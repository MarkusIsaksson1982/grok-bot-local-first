---
name: grok-bot-local-first
description: >-
  Use when a Grok Bot should spend a turn only on judgment. grokkit.py runs
  one local worker action and prints last-line JSON. Ships a flat lib/ of
  stdlib workers.
compatibility: Python 3.12 stdlib; offline; Grok Bot 0.61.0 verified on Windows 11; paths are pathlib-based.
metadata:
  version: "0.2.1"
  grok_build: "1.0.41"
  os: windows-11
when_to_use: >-
  Setting up a fresh Grok Bot on this folder, routing a live request to a
  local worker, or grouping lib/ into sections for the user's own work.
tags: grok-bot, local-first, cli-worker, lib
---

# Grok Bot local-first

Grok Bot turns are expensive. Scripts are cheap. Deterministic work stays in `lib/`. The bot routes, runs one action, and reads the last line of JSON. Do not reimplement a worker in the model. Do not read worker source unless a command failed and the JSON is not enough to fix it.

## Point a new bot here

Copy this folder onto the machine that will run the bot, then follow `references/setup.md`. That note creates `state/` and `drop/`, records Grok Bot `0.61.0` as the Windows-verified version, and describes optional library sections. The first bot from this kit on a machine can also use `references/first-probe.md`. Later bots skip that.

Suggested description:

```text
Direct this instance at <this-folder>.
Read SKILL.md, then references/setup.md.
Run python grokkit.py.
Spend a turn only when the JSON has use_bot true, alert true, or ok false.
Do not reimplement local tasks. Do not read worker source unless debugging a failed command.
Prefer workers already in lib/. New files land in drop/ and are ingested only after worker-lint is quiet.
```

## When to spend a turn

Spend one turn if:

- the user is talking live and `python grokkit.py route "<text>"` matches no local task, or
- the last JSON line has `alert: true` or `ok: false`, or
- the chosen action is marked `use_bot: true`.

Stay quiet when inbox JSON has `quiet: true`.

## Commands

```text
python grokkit.py list
python grokkit.py inbox
python grokkit.py route <text>
python grokkit.py action <id> <action>
python grokkit.py ingest <path>
```

`action` runs the worker registered for that id. Ids and default actions are in `LIBRARY_INVENTORY.md`.

## Worker contract

One Python 3.12 stdlib file. No flags must not do work. The last stdout line is JSON.

```text
python worker.py --manifest
python worker.py --list-actions
python worker.py --action NAME
```

`--manifest` includes `ok`, `id`, `title`, `source`, `priority`, `keywords`, `default_action`, and `actions[]` with `{name, use_bot, summary}`. `source` is `grok-4.7-high`. `origin.grok_build` is `1.0.41` (Grok Build 1.0.41) and `origin.os` is `windows-11` (Windows 11). `origin` records where the worker was last iterated. It is not a required target platform. An externally sourced worker may use another `grok_build` value, such as `opencode`.

`--action` prints `{ok, alert, summary, data, action}`. Keep `summary` short. Keep `data` small. Cap lists and include a total when truncated.

`use_bot: false` means the action is deterministic. `use_bot: true` means a judgment pack. Do not open a pack unless a person or the inbox asked for it.

The reference shape is `references/worker_template.py`. Copy it to a new name under `drop/` when writing a worker. It is not a live task. Stamp `verified_grok_bot` with the same value as `sources.json` `current_grok_bot`.

## Library

Shipped workers are files directly in `lib/`. See `LIBRARY_INVENTORY.md`.

Ingest writes a new worker into `lib/`. Set `GROKKIT_LIB_LANE` to a single folder name when that file should go to `lib/<section>/` instead. Moving the shipped files into `lib/core/` and adding further sections is optional and is written out in `references/setup.md`.

## Marketplace

Optional. If the user wants to consider Agensi, they download https://www.agensi.io/skills/getting-started-with-agensi-mcp themselves and tell you the folder. The steps are in `references/setup.md` under Marketplace connection.

## Local-first rules

- Stay on a local action when the work is deterministic and the JSON stays small.
- Emit a review pack only when a person must approve, or the payload would blow the cap.
- Do not rewrite a worker another model already wrote.
- Do not put raw secrets, full logs, or whole files into the bot context. Workers return digests and capped lists.
