# Agensi wrap (from Grok 4.5 Fast research, 2026-08-26)

Loaded on demand. Keep SKILL.md short.

## Required frontmatter
- `name`: 1-64 chars, lowercase a-z0-9 and hyphens, no lead/trail/consecutive hyphens, must match folder name
- `description`: 1-1024 chars; what it does and when to use it (routing keywords). Not the full workflow.

## Optional keys seen in Agensi / nearby specs
license, compatibility, metadata (string map), allowed-tools, when_to_use, tags, argument-hint, arguments, context, model, effort, hooks

## Layout
```
grok-bot-local-first/SKILL.md          # required
grok-bot-local-first/references/       # optional, one level deep
grok-bot-local-first/scripts/
grok-bot-local-first/assets/
```
Zip contains the skill folder. Not SKILL.md at zip root. No README/changelog/install guide inside the skill directory.

## Distribution
- Agensi marketplace (agensi.io) indexes community SKILL.md
- GitHub: repo then `npx skills add owner/repo`; validate `skills-ref validate ./grok-bot-local-first`
- Relative paths from skill root only; no deep reference chains
- Progressive disclosure: frontmatter at startup, body on activation, references/ on demand
