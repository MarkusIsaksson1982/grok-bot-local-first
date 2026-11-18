# Go add-on

Optional. Example worker: `hello.go`, stdlib only.

Needs a Go toolchain on PATH. grokkit launches `.go` files with
`go run` when `runtime` is `go`. If Go is missing, the action returns
`runtime_unavailable` and does not spend a bot turn.

```text
go run addons/go/hello.go --manifest
go run addons/go/hello.go --action echo
```

Local `tasks.json` snippet:

```json
{
  "id": "go-hello",
  "title": "Example Go worker",
  "priority": 99,
  "kind": "recorded",
  "enabled": true,
  "source": "local",
  "keywords": ["go-hello"],
  "local": {
    "script": "addons/go/hello.go",
    "default_action": "echo",
    "runtime": "go",
    "hosts": { "class": "any", "needs": ["go"] }
  },
  "bot_when": ["fail", "alert"],
  "bot_never": ["ok"]
}
```

A pre-built binary can use `runtime: "exec"` instead of `go run`.
