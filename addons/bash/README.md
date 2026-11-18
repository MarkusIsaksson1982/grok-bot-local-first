# bash add-on

Optional. Example worker: `host-echo.sh`.

It prints last-line JSON for `--manifest`, `--list-actions`, and
`--action echo`. Python remains the default runtime for the kit.

Run directly:

```text
bash addons/bash/host-echo.sh --manifest
bash addons/bash/host-echo.sh --action echo
```

To register it locally, add a task like this to your `tasks.json` (not
required for the core kit):

```json
{
  "id": "host-echo",
  "title": "Example non-Python worker (bash)",
  "priority": 99,
  "kind": "recorded",
  "enabled": true,
  "source": "local",
  "keywords": ["host-echo"],
  "local": {
    "script": "addons/bash/host-echo.sh",
    "default_action": "echo",
    "runtime": "bash",
    "hosts": { "class": "any" }
  },
  "bot_when": ["fail", "alert"],
  "bot_never": ["ok"]
}
```
