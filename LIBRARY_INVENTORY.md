# Library inventory

Workers live in `lib/`. Each one is stdlib-only. The last stdout line is JSON.

Contract: `python worker.py --manifest`, `--list-actions`, `--action NAME`.

| id | priority | default | actions | title |
|---|---|---|---|---|
| `artifact-gate` | 60 | `verify` | inspect, pack, schema, verify | Expected-deliverable existence and property checks |
| `artifact-pd` | 58 | `scan` | scan, harvest-hint, pack | PD fitness + keep/harvest/archive/helper/worker for drop artifacts |
| `audit-harness` | 65 | `validate` | scan, validate, pack | Harness auditor |
| `context-budget` | 85 | `collect` | collect, select, pack | Context Budget Estimator |
| `decision-gate` | 85 | `gate` | gate, schema, scores, signals | Threshold/escalation gate: whether the bot should engage at all |
| `drop-triage` | 95 | `d1` | d1, d2, pack, review-pack | Drop Folder Triage Meta-Worker |
| `freshness-gate` | 55 | `check` | ages, archive-age-check, cadence-stub, check, checksum-dupe-stub, lib-scan-stub, schema | Timestamp / staleness detector for watched inputs |
| `review-pack` | 93 | `d1` | d1, d2, pack | Progressive Evidence Pack Builder |
| `schema-guard` | 90 | `collect` | collect, violations, pack | Local Schema / Contract Guard |
| `secrets-scan` | 98 | `collect` | collect, locate, pack | Secret Pattern Scanner |
| `version-gate` | 50 | `check` | scan, check, stamp, pack, archive-diff | Compare lib worker verified_grok_bot stamps to current |
| `worker-index` | 92 | `collect` | collect, diff, pack | Sibling Worker Manifest Index |
| `worker-lint` | 88 | `collect` | collect, violations, pack | Worker Contract Linter |
