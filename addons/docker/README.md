# Docker add-on

Optional, documentation only. The core kit does not require Docker, and
there is no shipped container worker.

Use this pattern when you already run Docker and want to isolate a
stdlib Python worker. Declare the need on the task so hosts without
Docker skip the action without spending a bot turn:

```json
{
  "local": {
    "script": "lib/secrets-scan.py",
    "runtime": "python",
    "hosts": { "class": "any", "needs": ["docker"] }
  }
}
```

A minimal image that only adds Python 3 and the kit (build from the kit
root, when you want it):

```text
FROM python:3.13-slim
WORKDIR /kit
COPY grokkit.py tasks.json sources.json ./
COPY lib ./lib
COPY schemas ./schemas
# state/ and drop/ are created at run time
CMD ["python3", "grokkit.py", "list"]
```

Do not put credentials in the image. Mount `state/` and `drop/` as
volumes if the worker needs them. Nested virtualisation and heavy images
may not fit a small local computer; declare `min_ram_mb` or a specific
host label when that matters.

The public kit still runs the same worker with plain `python3` when
Docker is not involved.
