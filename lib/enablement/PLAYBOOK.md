# AI adoption playbook (enablement section)

A small, tool-agnostic playbook for rolling out AI assistants, coding agents, Agent Skills and MCP connectors
inside an organisation. The worker `enablement-kit.py` does the counting; people do the teaching and the judgment.

## 1. Rollout in waves
| Wave | Who | Entry criteria | Exit criteria |
|---|---|---|---|
| 0 Pilot | 1 team + 2 champions | licences, data classes agreed, verification rule written | 3 workflows that save time and pass review |
| 1 Early functions | 2-3 functions with a sponsor | pilot workflows documented as skills | adoption >= 25 % weekly active, no open governance cases |
| 2 Broad | all functions | train-the-trainer done, champions in each function | adoption >= 50 %, cost per active user within budget |
| 3 Embedded | proven self-serve workflows | used weekly by several teams | promoted to a centrally built engineering project or retired |

## 2. Standards
- **Shared library.** Prompts, skills and workflows live in one repo, one folder per skill, `SKILL.md` with `name` + `description`
  (what it does and when to use it). `enablement-kit.py --action skill-lint` must be clean before a skill is shared: lowercase
  hyphenated name, description with a trigger, referenced files present, no personal paths, no credentials.
- **Portable by default.** The Agent Skills format is read by several coding agents (for example GitHub Copilot in VS Code and the CLI,
  Codex, OpenCode). Keep skills in `.agents/skills/` or `.github/skills/` so the same file works across tools.
- **Project structure.** Every AI-assisted project states its data class, its owner, the model/tool used and its verification step.

## 3. Output verification protocol (mandatory)
| Level | When | Required check |
|---|---|---|
| V1 | Internal draft, nobody else relies on it | The author reads it before using it |
| V2 | Shared inside the company | Deterministic checks where possible (tests, schemas, links, numbers against the source) + author review |
| V3 | Leaves the company, or changes money, contracts, code in production or personal data | V2 + a second person signs off; the tool's output is never sent directly |

Rule of thumb for training: *the model proposes, a person decides*. Make checks cheap enough that people actually run them.

## 4. Measuring adoption
`enablement-kit.py --action adoption --events usage.csv --headcount headcount.json --week 2026-W42`
- Weekly active users and adoption rate per function, share of power users (>= 10 sessions a week).
- Maturity 0-4 per function: none, pilot (< 25 %), spreading (< 50 %), established (< 75 % or few power users), embedded.
- Spend flags: functions whose share of cost is more than 1.5x their share of sessions (often long contexts or the wrong model tier).
- Functions below target go to the leadership update with one concrete next step each (a session, a champion, a skill).

## 5. Governance intake (Tech + Legal forum)
`enablement-kit.py --action intake --proposals proposals.json`
- **Fast-track:** public/internal data, read-only, human verification in place.
- **Forum review:** confidential/restricted data, personal data, write scopes, MCP connectors without a tool allowlist,
  external output without human review, vendors outside the EU/EEA. Legal items first on the agenda.
- **Reject:** restricted data with a vendor outside the EU/EEA.
These rules are a starting point to agree with Legal, not legal advice.

## 6. Training and champions
See `workshops/`. Each workshop is hands-on, 90 minutes, built on the team's own tasks, and ends with one artefact
(an instructions file, a skill, a checklist) that goes into the shared library. Champions run the second round themselves.
