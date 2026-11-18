#!/usr/bin/env bash
# host-echo -- tiny example of a non-Python grokkit worker.
# Last stdout line is JSON. No flags must not do work.
set -euo pipefail

emit() {
  printf '%s\n' "$1"
}

host=${HOSTNAME:-unknown}
host=${host//\\/\\\\}
host=${host//\"/\\\"}

if [[ $# -lt 1 ]]; then
  emit '{"ok":false,"alert":false,"reason":"no_flags","hint":"--manifest"}'
  exit 2
fi

case "$1" in
  --manifest)
    emit '{"ok":true,"id":"host-echo","title":"Example non-Python worker (bash)","source":"local","priority":99,"keywords":["host-echo","polyglot-example"],"default_action":"echo","verified_grok_bot":"0.58.0","verified_at":"2026-11-18","runtime":"bash","origin":{"os":"linux"},"hosts":{"class":"any"},"actions":[{"name":"echo","use_bot":false,"summary":"Print a tiny hello as last-line JSON"},{"name":"pack","use_bot":true,"summary":"Tiny judgment pack (none needed)"}]}'
    exit 0
    ;;
  --list-actions)
    emit '{"ok":true,"id":"host-echo","default_action":"echo","actions":[{"name":"echo","use_bot":false,"summary":"Print a tiny hello as last-line JSON"},{"name":"pack","use_bot":true,"summary":"Tiny judgment pack (none needed)"}]}'
    exit 0
    ;;
  --action)
    if [[ $# -lt 2 ]]; then
      emit '{"ok":false,"reason":"missing_action","action":""}'
      exit 2
    fi
    case "$2" in
      echo)
        emit "{\"ok\":true,\"alert\":false,\"summary\":\"host-echo runtime=bash host=${host}\",\"data\":{\"runtime\":\"bash\",\"host\":\"${host}\"},\"action\":\"echo\"}"
        exit 0
        ;;
      pack)
        emit '{"ok":true,"alert":false,"summary":"no judgment needed","data":{"needs_judgment":false},"action":"pack"}'
        exit 0
        ;;
      *)
        emit "{\"ok\":false,\"alert\":true,\"reason\":\"unknown_action\",\"action\":\"$2\"}"
        exit 2
        ;;
    esac
    ;;
  *)
    emit '{"ok":false,"reason":"unknown_flag","hint":"--manifest"}'
    exit 2
    ;;
esac
