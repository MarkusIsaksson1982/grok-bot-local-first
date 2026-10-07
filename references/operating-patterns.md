# Operating patterns (generic)

Process lessons from running the kit unattended. They add no workers; they describe how to call them safely.

## Build effort ladder (cheapest green first)
- Start Grok Build at `low`. A local worker or test is the judge, not the Build's own report.
- Escalate one step only when the result is NO-GO. Use `high` only with a prepared package and the owner's confirmation.
- Headless runs that write files need `--always-approve`. Check the disk and `events.jsonl`; do not trust a "files touched" summary.
- If Build was the thing you were asked to spend and it fails, report it. Don't redo the work by hand without saying so.
- Escalating effort does not fix a contradictory prompt (see Anti-patterns).

## Auth probe before every poll
- Every scheduled poll starts with a cheap authenticated call, for example `git ls-remote origin HEAD`.
- A failed probe is its own state (`no_access`), never "no news".
- After the first auth failure, pause the poll, write one log line, and don't retry in a loop.

## Credential lifetime vs the night plan
- Before an unattended run, compare the scoped token's expiry with the last planned slot plus a margin (1 h). Ask the owner to renew before the run, not after.
- If access dies mid-run, stage deliveries in a private outbox (`staging/outbox/`) that mirrors the target paths 1:1, plus a file of inbox lines to append later. Mark each one `pending`, and move it over once access is back.
- If a partner is waiting live, tell them right away instead.

## Handoff and defer
- Anything the owner must decide (naming, rights, sensitive content, cost) is deferred: note it, flag `<owner> decides`, and decide nothing on their behalf.
- No paid spend during unattended runs without the paying owner's prior OK.

## Anti-patterns
- **Silent poll:** a loop that can't tell "no commits" from "no access" looks healthy while it is dead.
- **Create-on-append vs missing file:** a spec that says both "append to PATH" and "missing files exit 2" makes agents refuse the first create. Split it: a missing *input* exits 2; a missing *ledger/output* is created, then appended to.
- **Schema after content:** agree on ASCII keys and a schema before filling a shared file.
