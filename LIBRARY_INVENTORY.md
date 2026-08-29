# Grok Bot local-first library inventory

Generated from live `lib/` manifests after Hy3 meta-process rounds.

## Smoke-test snapshot (2026-08-28)

| Worker | Action | Result |
|--------|--------|--------|
| doc-outline | outline | ok, alert=false — 1 md file, 3 headings |
| state-compact | verify | ok, alert=true — last↔log drift detected (expected in this runner state) |
| todo-state | next | ok, alert=false — next item from tasks.json (16 open) |

## Full library

| Pri | Id | Default | Local actions | Bot actions | Purpose (title) |
|-----|----|---------|---------------|-------------|-----------------|
| 55 | `freshness-gate` | `check` | ages, check | — | Timestamp / staleness detector for watched inputs |
| 60 | `artifact-gate` | `verify` | inspect, verify | — | Expected-deliverable existence and property checks |
| 78 | `doc-outline` | `outline` | collect, outline, pack | — | Markdown header-only outline for progressive reading |
| 80 | `git-pulse` | `collect` | collect, files | pack | Local Git State Digest |
| 85 | `context-budget` | `collect` | collect, select | pack | Context Budget Estimator |
| 85 | `decision-gate` | `gate` | gate, scores, signals | — | Threshold/escalation gate: whether the bot should engage at  |
| 85 | `log-lens` | `collect` | collect, errors | pack | Structured Log / JSONL Lens |
| 86 | `todo-state` | `next` | collect, next | pack | Next actionable item + counts from local TODO/ledger |
| 87 | `state-compact` | `verify` | collect, verify | pack | State reconciliation and compaction for last.json and log.js |
| 88 | `worker-lint` | `collect` | collect, violations | pack | Worker Contract Linter |
| 90 | `schema-guard` | `collect` | collect, violations | pack | Local Schema / Contract Guard |
| 92 | `worker-index` | `collect` | collect, diff | pack | Sibling Worker Manifest Index |
| 93 | `review-pack` | `d1` | d1, d2 | pack | Progressive Evidence Pack Builder |
| 94 | `drop-gate` | `d1` | d1, d2 | pack | Drop Gate — External Intake Triage |
| 95 | `drop-triage` | `d1` | d1, d2 | pack | Drop Folder Triage Meta-Worker |
| 98 | `secrets-scan` | `collect` | collect, locate | pack | Secret Pattern Scanner |

## Tiers

1. **Policy gates** — decision-gate, artifact-gate, freshness-gate
2. **Core intake / safety** — worker-index, secrets-scan, schema-guard, log-lens, git-pulse, context-budget, review-pack
3. **Meta / contract** — worker-lint, drop-triage
4. **Gap fillers** — doc-outline, state-compact, todo-state

## Contract reminder

- CLI: `--manifest` | `--list-actions` | `--action NAME`
- Action JSON keys exactly: `ok`, `alert`, `summary`, `data`, `action`
- `use_bot:false` for deterministic work; `use_bot:true` only for judgment packs
- Runner: `python grokkit.py action <id> <action>` then read last-line JSON / inbox

## Transfer notes

- All workers are Python 3.12 stdlib-only, offline, read-only by default.
- Promote new drop content only after `worker-lint` + `drop-triage` classify it as clean/high-worth.
- Stay quiet when `inbox.quiet` is true; spend a bot turn only on alert / ok=false / live unrouted / use_bot packs.
