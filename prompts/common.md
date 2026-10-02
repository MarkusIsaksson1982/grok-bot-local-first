# Common return format (return-v1)

Paste the **Ask preamble** below above your task when asking an external model or worker for a non-code answer. Save its reply verbatim under `drop/returns/` (`.md`, `.txt` or `.ret`), then:

```text
python grokkit.py returns lint drop/returns/<file>.md
python grokkit.py returns ingest drop/returns/<file>.md --source <source-id> --model "<label>"
python grokkit.py returns extract <ret-id> <SECTION>
```

`<source-id>` is an id from `sources.json`. `--model` is a free label (what the UI showed). Header values win only when the flag is absent. Schema: `schemas/return-v1.json`. Checker: `lib/ret-lint.py`.

## Ask preamble (copy from here)

```text
Reply in exactly this format and nothing else (no prose before or after).

# RETURN v1
source: <source-id>
model: <model name as you know it>
date: <YYYY-MM-DD>
ask: <ask-id>
audience: <common|bot|agent>
sections: <comma-separated ids in the order you will use>
budget: <max total lines, e.g. 120>

## [SUMMARY] one line
<1-3 lines>

## [<ID>] <short title>
<body: prefer YAML or JSON; short bullets otherwise>

Rules:
- Section ids: start with a letter; letters, digits, . _ - only; max 32 chars; unique.
- Every id in "sections:" must appear as "## [ID]". Stay within "budget:" lines total.
- Inside a section, use ~~~ fences (not ```), so the whole reply can be copied as one block.
- No secrets, tokens, or personal data.
```

## Audience tags

- `common`: anyone, including the person.
- `bot`: material for Grok Bot judgment only.
- `agent`: instructions for a coding agent or local worker.

## Ask spine (optional, for the person writing the task)

GOAL, SCOPE / OUT, DONE WHEN, CONSTRAINTS, then the list of section ids you want back with a line budget. Keep one outcome per ask.
