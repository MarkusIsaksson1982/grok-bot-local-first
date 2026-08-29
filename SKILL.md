---
name: grok-bot-local-first
description: >-
  Use when a Grok Bot should spend allotment only on judgment. Local CLI
  workers do the work; the bot runs one --action and reads last-line JSON.
  Ships a coherent stdlib lib/ of policy gates, intake/safety, meta, and helpers.
compatibility: Grok Bot 0.27.0+; Python 3.12 stdlib workers; offline; Windows-first, path-agnostic.
metadata:
  version: "0.1.3"
  horizon: "about one week from 2026-08-26"
when_to_use: >-
  Live Grok Bot work about allotment, local workers, ingest/flags, drop triage,
  or shipping this skill to Agensi or GitHub.
tags: grok-bot, local-first, allotment, cli-worker, lib, agensi
---

# Grok Bot local-first

Grok Bot turns are expensive (big harness + every wake). Scripts are cheap. Put deterministic work in a local worker. The bot only routes, runs one `--action`, and reads tiny JSON. Do not reimplement the worker in the model. Do not read worker source unless debugging the runner.

This file is Grok-optimized. Other harnesses can adapt it; that adaptation is out of scope here.

## Bootstrapping during setup

During initial setup on Windows, Grok Bot often creates a runner such as `C:\Users\<username>\grokkit\grokkit.py` that can run local CLI workers and read last-line JSON.

This skill ships the **worker library** under `lib/`, not a full runner. Point the bot at your runner folder, place or ingest the `lib/*.py` files, and use the runner’s `ingest` / `action` / `inbox` (or equivalent) commands.

Suggested bot description (adapt the path):

```
Direct this instance at <runner-folder>.
Run python grokkit.py for recorded and live routing.
Spend Grok Bot only when the JSON says use_bot or alert/ok=false.
Do not reimplement local tasks or read worker source unless debugging.
Prefer workers under lib/; promote new drop/ content only after local lint/triage.
```

## When to spend a Grok Bot turn

Spend one if and only if:

- the user is talking live and no local worker matches, or
- last-line JSON has `alert: true` or `ok: false`, or
- the chosen action is marked `use_bot: true` (a review pack).

Do not spend a turn to understand a script, to loop files, or to regenerate a worker that already exists. Stay quiet when status JSON says `quiet: true`.

## Worker contract (stable)

One Python 3.12 stdlib file. Last stdout line is JSON. No flags must not do work.

```
python worker.py --manifest
python worker.py --list-actions
python worker.py --action NAME
```

`--manifest` includes at least: `ok`, `id`, `title`, `source`, `priority`, `keywords`, `default_action`, and `actions[]` with `{name, use_bot, summary}`.

`--action` prints exactly: `{ok, alert, summary, data, action}`. Keep `summary` short. Keep `data` a small object with known keys. Cap lists (e.g. ≤5) and always include a total count when truncated.

`use_bot` is only a boolean: false = local deterministic work; true = judgment pack (skip unless a reviewer or policy asks).

## Library (`lib/`)

Coherent set grown by external drafts + local meta evaluation (lint/triage before promote). Approximate priorities:

| Tier | Workers | Role |
|------|---------|------|
| Policy gates | decision-gate, artifact-gate, freshness-gate | Pre-flight escalate/hold, deliverable existence, staleness |
| Core intake / safety | worker-index, secrets-scan, schema-guard, log-lens, git-pulse, context-budget, review-pack | Discovery, secrets (no raw values), schema, logs, git, budget, progressive evidence |
| Meta / contract | worker-lint, drop-triage | Contract+import lint; drop intake orchestration with verdict + ranked evidence |
| Helpers | doc-outline, state-compact, todo-state | MD header outline; last↔log reconcile; next actionable item |

See `LIBRARY_INVENTORY.md` for exact ids, default actions, and smoke notes.

**Intake rule:** new candidates land in `drop/`. Run `worker-lint` and `drop-triage` locally. Promote to `lib/` only when contract-clean and worth the slot. Do not blind-merge parallel reimplementations.

## Progressive disclosure

Name actions so higher levels add more content but stay capped (e.g. `d1`/`d2`/`pack`, or `collect`/`select`/`pack`). Climb one level per turn when needed. Default is the lowest useful level. Do not dump the top level into context “just in case.”

## Who writes the worker

External models write candidate files. Grok Bot (or the local runner) ingests via `--manifest` and runs flags. Do not paste Grok Bot system prompts, harness internals, or product-named specs into other vendors’ models. Give them the CLI contract and the “already present” gap list only.

Stamp optional provenance in JSON if useful (`source`, public family name). Prefer `source: "local"` for promoted lib workers.

## What not to put in the bot profile

Description and memory-profile are in mind every turn. Keep them tiny: where the runner lives, run flags, don’t read source, prefer `lib/`. Procedures belong in workers or in this skill body (paid only when read).

## Local-first rules

- Stay on a local flag when the work is deterministic and the JSON will stay tiny.
- Emit a review pack only when a human must approve or the payload would blow the cap.
- Never have the bot rewrite a worker another model already wrote.
- Never put raw secrets, full logs, or unbounded file bodies into bot context; workers return digests and capped lists.

## Week-horizon

Written against Grok Bot ~0.27 and a local runner that registers workers from `lib/` / `drop/`. Fork anytime. After about a week, refresh names, paths, and examples; keep the northern star: **scripts cheap, bot for judgment only.**