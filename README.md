# grok-bot-local-first

Local-first pattern for Grok Bot: deterministic work lives in cheap Python workers; the bot only routes, runs one `--action`, and reads tiny last-line JSON.

## Quick start (Windows – most common)

1. Create or open a folder for the runner, e.g.  
   `C:\Users\<your-username>\grokkit`  
   (Grok Bot often chooses this name automatically; any folder is fine.)

2. Copy this skill into that folder (or into a `skills/grok-bot-local-first` subfolder if you prefer).

3. Copy (or symlink) the workers from `lib/` into the runner’s `lib/` folder (or into `drop/` and ingest them).

4. In the Grok Bot sidebar:
   - Click **+** → **New Bot** (or right-click an existing bot → **Edit Profile**).
   - **Name**: `grok-bot-local-first` (or any short name you like).
   - **Description** (paste this, then adapt the path):

     ```
     Direct this instance at C:\Users\<your-username>\grokkit (or your chosen folder).
     Run python grokkit.py for recorded and live routing.
     Spend Grok Bot only when the JSON says use_bot or alert/ok=false.
     Do not reimplement local tasks or read worker source unless debugging.
     Prefer workers under lib/; promote new drop/ content only after local lint/triage.
     ```

5. Optional: drop additional candidate `.py` files into the runner’s `drop/` folder.  
   Grok Bot generally accomplishes triaging before promoting content into `lib/` but `worker-lint` and `drop-triage` can be attempted for this purpose.

## Paths on other platforms

Replace the Windows path with the equivalent on your system, for example:

- Linux / macOS: `/home/<you>/grokkit` or `~/grokkit`
- Or any absolute path you prefer.

The exact folder should not need to be `grokkit`. That is likely only a convenient default.

## What ships in this skill

- **`lib/`** — coherent set of stdlib-only workers (policy gates, intake/safety, meta/lint, helpers). See `LIBRARY_INVENTORY.md` for ids, priorities, and default actions.
- **`SKILL.md`** — local-first contract and when to spend a bot turn.

## What this skill does **not** do

- It does not require you to paste Grok Bot system prompts or harness internals into other models.
- It does not ask the bot to rewrite workers that already exist.
- It does not force network access; workers are stdlib-only and offline.
- It does not bundle a full `grokkit.py` runner; the runner is created/owned by the Grok Bot instance (or your existing setup).

## Next steps

- Ingest workers: `python grokkit.py ingest lib/<name>.py` (or your runner’s equivalent).
- Smoke a few defaults: `python grokkit.py action <id> <default_action>`.
- Stay quiet when status/inbox says quiet; spend a turn only on alert / ok=false / use_bot packs / live unrouted requests.