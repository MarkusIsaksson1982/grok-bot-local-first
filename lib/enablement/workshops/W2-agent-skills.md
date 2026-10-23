# W2 - Agent Skills: turn a repeated task into a shared skill (90 min)

**Goal:** each pair ships one `SKILL.md` that passes `enablement-kit.py --action skill-lint` and works in more than one agent.

1. (10 min) What a skill is: a folder with `SKILL.md` (name, description with *when to use*), plus scripts or templates it references.
2. (20 min) Pick a task the team repeats weekly (release notes, SQL review, test ideas). Write the steps a careful colleague follows.
3. (20 min) Lint it. Fix name, trigger, missing files, personal paths. Compare with `fixtures/skills/` (one clean, one with problems).
4. (25 min) Run it in two agents (for example the team's IDE assistant and a CLI agent). Note differences.
5. (15 min) Add a verification line to the skill: who checks the output, and how.

Take-away: the skill goes into `.agents/skills/` or `.github/skills/` in the team repo; the champion reviews it next week.
