---
name: grok-bot-local-first
description: >-
  Use when a Grok Bot should spend allotment only on judgment. Local CLI
  workers do the work; the bot runs one --action and reads last-line JSON.
compatibility: Grok Bot 0.27.x; Python 3.12 stdlib workers on Windows; no network required to run a worker.
metadata:
  version: "0.1"
  horizon: about one week from 2026-08-26
  channels: Agensi, GitHub
when_to_use: >-
  Live Grok Bot work about allotment, local workers, ingest/flags, or shipping
  this skill to Agensi or GitHub.
tags: grok-bot, local-first, allotment, cli-worker, agensi
---

# Grok Bot local-first

Grok Bot turns are expensive (big harness + every wake). Scripts are cheap. Put deterministic work in a local worker. The bot only routes, runs one `--action`, and reads tiny JSON. Do not reimplement the worker in the model. Do not read worker source unless debugging the runner.

This file is Grok-optimized. Other harnesses can adapt it; that adaptation is out of scope here.

## When to spend a Grok Bot turn

Spend one if and only if the user is talking live and no local worker matches, last-line JSON has `alert: true` or `ok: false`, or the chosen action is marked `use_bot: true` (a review pack).

Do not spend a turn to understand a script, to loop files, or to regenerate a worker that already exists. Stay quiet when status JSON says `quiet: true`.

## Worker contract (stable)

One Python 3.12 stdlib file. Last stdout line is JSON. No flags must not do work.

```
python worker.py --manifest
python worker.py --list-actions
python worker.py --action NAME
```

`--manifest` includes `id`, `title`, `source`, `default_action`, and `actions[]` with `{name, use_bot, summary}`.
`--action` prints `{ok, alert, summary, data, action}`. Keep `summary` short. Keep `data` a small object with known keys.

`use_bot` is only a boolean: false = local slice, true = skip unless a later reviewer asks.

## Progressive disclosure

Name actions `d1`..`dN` (or `r1`..`rN` for research). Higher number = more content, still capped. Climb one level per turn. Default is the lowest level. Do not dump the top level into context just in case.

Proven this week: identity ping, then skills, proposed actions, local-vs-review rules, next-file spec. Schema held across Grok 4.5 Fast, Claude, GPT, OpenCode Nemotron, OpenCode Muse Spark.

## Who writes the worker

External models write the file. Grok Bot ingests via `--manifest` and runs flags. Do not paste Grok Bot system prompts, harness internals, or product-named specs into other vendors' models. Give them the CLI contract only. Stamp `provider` (public family) and `ui` (opencode, web, …) in JSON. Use the public picker name, not a hidden reasoning name.

## What not to put in the bot profile

Description and memory-profile are in mind every turn. Keep them tiny: where the runner lives, run flags, don't read source. Procedures belong in workers or in this skill body (paid only when read).

## Local-first rules

Stay on a local flag when the work is deterministic and the JSON will stay tiny. Emit a review pack only when a human must approve or the payload would blow the cap. Never have the bot rewrite a worker another model already wrote.

## Week-horizon

Written against Grok Bot 0.27.0 and a local Windows runner that registers workers from a drop folder. Fork anytime. After about a week, refresh names, paths, and examples; keep the northern star.

## Agensi / GitHub

Folder name must match frontmatter `name` (`grok-bot-local-first`). Zip the folder, not a bare `SKILL.md`. Ship optional detail in `references/` (see `references/agensi.md`). Do not put the full workflow in `description`. Validate with community skill tooling if you have it (`skills-ref` / `npx skills add`).
