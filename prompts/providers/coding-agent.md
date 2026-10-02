# coding-agent (any CLI or IDE agent)

- Code: write one stdlib worker per `references/worker_template.py` into `drop/`; it goes through worker-lint, drop-triage, then `grokkit.py ingest`.
- Findings/plans: write a return-v1 file into `drop/returns/` (`audience: agent` or `bot`).
- Run with explicit approval prompts on (no auto-approve). Pin reasoning effort and record it in `--model`.
