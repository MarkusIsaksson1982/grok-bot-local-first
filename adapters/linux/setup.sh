#!/usr/bin/env sh
# Create local folders and smoke-check the kit on Linux/macOS. Safe to re-run.
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' || { echo "need Python 3.12+" >&2; exit 1; }
mkdir -p "$ROOT/state" "$ROOT/drop/returns" "$ROOT/drop/_archive"
cd "$ROOT"
"$PY" grokkit.py list >/dev/null && echo "grokkit ok at $ROOT ($("$PY" -V))"
"$PY" grokkit.py inbox
