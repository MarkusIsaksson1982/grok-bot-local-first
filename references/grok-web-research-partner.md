# Pairing web Grok 4.5 Fast as research partner

Use this reference when a Grok Bot instance follows `grok-bot-local-first` (current and near-horizon). The bot spends allotment only on judgment: route, one `--action`, read last-line JSON. Deterministic work and research packs live outside the bot.

## Roles

| Role | Who | Does |
|------|-----|------|
| Research / writer | Web Grok 4.5 Fast (this harness) | Draft workers, progressive `rN` packs, Agensi-oriented reference snippets. No bot system prompt, no harness internals. |
| Runner / judgment | Grok Bot instance | `--manifest`, `--list-actions`, `--action NAME`. Climb one level per turn. Stay quiet on `quiet: true`. |
| Local worker | Python 3.12 stdlib file in drop folder | Last stdout line JSON. No flags = no work. |

## Workflow

1. **Ask web Grok** for a worker or a single progressive slice. Give only the CLI contract and the goal (e.g. “Agensi distribution rules for `references/`”). Do not paste bot profile, memory, or product-named specs.
2. **Save** the emitted file to the drop folder the Windows runner watches (e.g. `drop/research-agensi-skill.py`).
3. **Bot turn (cheap):**  
   `python <worker>.py --manifest`  
   then one `--action rN` (or `dN`). Read only the last JSON line.
4. **Climb** one level per bot turn. Default is the lowest level. Do not dump top-level content “just in case.”
5. **When `use_bot: true`** or `alert: true` / `ok: false`, the bot may spend a judgment turn; otherwise stay local.
6. **Ship to Agensi:** folder name = frontmatter `name`. Zip the folder (skill root containing `SKILL.md` and `references/`), not a bare `SKILL.md`. Optional detail stays in `references/` so discovery stays tiny.

## What to request from web Grok

- One stdlib worker that implements `--manifest` / `--list-actions` / `--action`.
- Progressive research flags (`r1`…`rN`) whose `data` is small and keyed.
- Short reference bodies for this folder (Agensi publish steps, zip layout, marketplace notes).
- Diff notes vs pure name+description `SKILL.md` when the skill is about to be listed.

## What not to request from web Grok

- Full rewritten `SKILL.md` bodies on every turn.
- Re-implementation of an existing worker inside the model.
- Bot system prompts, allotment numbers, or hidden reasoning names.
- GitHub-first install docs when the target is Agensi listing.

## Near-horizon stability

Contract that should hold across 0.27.x and the next week of forks:

- Manifest fields: `id`, `title`, `source`, `provider`, `ui`, `default_action`, `actions[]` with `name` / `use_bot` / `summary`.
- Action result: `{ok, alert, summary, data, action}` only.
- `provider` = public family (`grok`); `ui` = `grok-web` when the research came from this harness.
- Description and memory-profile stay tiny; procedures live here or in workers.

## One-line bot command after a research drop

```
python research-agensi-skill.py --action r3
```

(or the lowest open `rN` / `dN`). If the worker is missing, emit only  
`{"ok":true,"alert":false,"summary":"no worker","data":{},"action":"skip"}`.
