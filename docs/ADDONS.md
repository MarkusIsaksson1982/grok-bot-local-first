# Add-ons

The core kit is stdlib Python plus the language-neutral worker contract in
`schemas/worker-v1.json`. Everything under `addons/` is an optional
reference adapter. None of it is imported when `grokkit.py` starts, and
the core tests do not need these folders or Node, Go, or Docker:

```text
python3 -m unittest discover -s tests
```

Add an adapter only when the language or tool is common, easy to set up,
and clearly useful. Keep Python as the default runtime for shipped
workers. To try an example worker, point a *local* `tasks.json` entry at
its script (snippets are in each add-on's README). Do not make an add-on
a required dependency of the core kit.

`grokkit.py` can already launch `python`, `node`, `bash`, `go`, and
`exec` when those tools are on PATH. If a requested runtime is missing,
it prints JSON with `reason: runtime_unavailable`, `use_bot: false`, and
does not spend a bot turn.

## bash

`addons/bash/host-echo.sh` is a tiny worker that speaks the same
last-line JSON contract. It needs only `bash`, which is usual on Linux
and macOS. See `addons/bash/README.md`.

Useful for shell-side wrappers and for checking that a non-Python worker
is skipped cleanly when `local.hosts` does not match this machine.

## node

`addons/node/hello.mjs` is a no-dependency Node worker. It needs a `node`
binary on PATH (no `npm install`). See `addons/node/README.md`.

The enablement kit already ships a strict TypeScript helper at
`lib/enablement/ts/` for skill and tool selection. That library is part
of the enablement kit, not this add-on. Use this add-on when you want a
standalone Node worker that grokkit can launch with `runtime: "node"`.

## go

`addons/go/hello.go` is a stdlib-only Go worker, launched with
`go run` when `runtime` is `go`. See `addons/go/README.md`. If Go is not
installed, grokkit reports `runtime_unavailable` rather than starting
the worker. A pre-built binary can use `runtime: "exec"` instead.

## docker

There is no shipped Docker worker. `addons/docker/README.md` shows a
optional pattern for running a stdlib Python worker inside a container
when you already use Docker. The core kit does not require Docker, and
workers that need it should declare `hosts.needs: ["docker"]` so other
machines skip them without spending a bot turn.
