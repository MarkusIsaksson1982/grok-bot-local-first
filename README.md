# grok-bot-local-first

Local-first kit for a Grok Bot. `grokkit.py` runs one Python worker and prints a single line of JSON. The bot spends a turn when that JSON asks for judgment.

A worker in `lib/` or in `lib/<section>/` finds this folder by walking up from its own file. `GROKKIT_ROOT` overrides that. Paths are not tied to a username or OS. Version 0.3.0 is developed on Linux; `0.58.0` in `sources.json` is the Grok Bot version verified on Linux on 2026-10-02 (0.2.1 was verified on Windows 11 with 0.61.0).

## Layout

- `grokkit.py` is the runner. `returns` ingests non-code answers from external models (see `prompts/common.md`).
- `lib/` is the shipped worker library, as files directly in that folder. `references/setup.md` explains how a bot can later group those files under `lib/core/` and add other sections for its own work.
- `SKILL.md` is the file to point a fresh Grok Bot at.
- `references/setup.md` tells that bot how to create `state/` and `drop/` on the machine that will run the kit.
- `references/worker_template.py` is the shape of a new worker. It is not registered in `tasks.json`.
- `LIBRARY_INVENTORY.md` lists ids, priorities, and actions.
- `tasks.json` registers the shipped workers.
- `sources.json` holds `current_grok_bot` and the source ids (local, external drafts, web chats, coding agents).
- `prompts/` holds the common return format and short per-provider notes. `schemas/return-v1.json` is its contract.
- `adapters/linux/setup.sh` and `adapters/windows/setup.ps1` create `state/` and `drop/` and smoke-check the kit. `adapters/windows/PORTING.md` lists the few platform differences.
- `tests/` holds a stdlib smoke suite: `python3 -m unittest discover -s tests`.

`state/` and `drop/` are local. They are gitignored and are not part of the tree until the bot creates them.

## Check

```text
python3 grokkit.py list          # Windows: python grokkit.py list
python3 -m unittest discover -s tests
```

## Enablement kit (`lib/enablement/`)

`lib/enablement/enablement-kit.py` helps roll out AI assistants, coding agents, Agent Skills and MCP connectors inside an
organisation. It does the counting so people can do the teaching:

- `--action skill-lint` checks a shared library of Agent Skills (`<skill>/SKILL.md`) for portability across agents: frontmatter,
  lowercase hyphenated `name` matching the folder, a `description` that says when to use the skill, referenced files present,
  no personal paths, no credentials.
- `--action adoption` reads a usage export (week, user, function, tool, sessions, cost) and a headcount file and reports weekly active
  users, adoption rate, share of power users and a 0-4 maturity level per function, cost per active user, functions below target and
  functions whose spend is out of line with their usage.
- `--action intake` screens proposed AI tools and MCP connectors with plain rules (data class, personal data, write scopes, tool
  allowlist, human verification, vendor region) into fast-track, forum review (Tech and/or Legal) or reject, and orders the agenda.
- `--action pack` is the only bot step: functions below target and the top agenda items, for wording next steps and forum notes.
- `lib/enablement/ts/src/skill-router.ts` (strict TypeScript, no dependencies) selects which Agent Skills / MCP tools to load for a
  request within a cost budget and packs retrieved chunks into a context budget; `lib/enablement/ARCHITECTURE.md` shows how
  retrieval, skills, MCP connectors, verification and human approval fit together.

Playbook (rollout waves, standards, a three-level output verification protocol, KPIs, governance): `lib/enablement/PLAYBOOK.md`.
Hands-on 90-minute workshops for development teams: `lib/enablement/workshops/` (prompting and context, Agent Skills, AI in testing
and review; W1 also in Swedish). Synthetic fixtures in `lib/enablement/fixtures/`. Tests: `tests/test_enablement.py`.

## License

MIT. See `LICENSE`.

## Agensi

An Agensi marketplace edition of this kit is at https://www.agensi.io/skills/grok-bot-local-first-v0-1-0

What that marketplace is, and whether it is useful here, is left to the person running the bot. The optional download step is in `references/setup.md`.

## Ops pack (`lib/ops/`)

`lib/ops/ops-pack.py` routes Finance, Legal and BizOps requests rules-first: ingest with de-duplication, SQLite enrichment
(requester team, vendor by normalised name or email domain), keyword rules for team/owner/SLA, and a human approval gate for
money (finance approver) and contracts (legal/finance approver). The bot is asked for one thing only: `--action pack` lists
free-text requests no rule matched, with the prompt and JSON schema; the bot writes its answers to a file and
`--action classify-apply --answers FILE` accepts each answer only if it passes the schema and the confidence bar. Approval status is
recomputed from the request, never taken from the bot. `--action dq` runs seven SQL data-quality checks nightly.
Runbook: `lib/ops/RUNBOOK.md`. Data lives in `state/ops/ops.db`. Tests: `tests/test_ops_pack.py`.
The same core, with pluggable model backends and an evaluation across models, is the standalone repo `ops-pack-demo`.
