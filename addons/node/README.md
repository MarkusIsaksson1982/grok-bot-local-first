# Node add-on

Optional. Example worker: `hello.mjs`. No npm packages.

Needs `node` on PATH. grokkit launches it with `runtime: "node"`.

```text
node addons/node/hello.mjs --manifest
node addons/node/hello.mjs --action echo
```

Local `tasks.json` snippet:

```json
{
  "id": "node-hello",
  "title": "Example Node worker",
  "priority": 99,
  "kind": "recorded",
  "enabled": true,
  "source": "local",
  "keywords": ["node-hello"],
  "local": {
    "script": "addons/node/hello.mjs",
    "default_action": "echo",
    "runtime": "node",
    "hosts": { "class": "any", "needs": ["node"] }
  },
  "bot_when": ["fail", "alert"],
  "bot_never": ["ok"]
}
```
