#!/usr/bin/env node
// Tiny example of a no-dependency Node grokkit worker.
// Last stdout line is JSON. No flags must not do work.

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

const args = process.argv.slice(2);
if (args.length < 1) {
  emit({ ok: false, alert: false, reason: "no_flags", hint: "--manifest" });
  process.exit(2);
}

if (args[0] === "--manifest") {
  emit({
    ok: true,
    id: "node-hello",
    title: "Example Node worker",
    source: "local",
    priority: 99,
    keywords: ["node-hello"],
    default_action: "echo",
    verified_grok_bot: "0.58.0",
    runtime: "node",
    hosts: { class: "any" },
    actions: [
      { name: "echo", use_bot: false, summary: "Print a tiny hello as last-line JSON" },
      { name: "pack", use_bot: true, summary: "Tiny judgment pack (none needed)" },
    ],
  });
  process.exit(0);
}

if (args[0] === "--list-actions") {
  emit({
    ok: true,
    id: "node-hello",
    default_action: "echo",
    actions: [
      { name: "echo", use_bot: false, summary: "Print a tiny hello as last-line JSON" },
      { name: "pack", use_bot: true, summary: "Tiny judgment pack (none needed)" },
    ],
  });
  process.exit(0);
}

if (args[0] === "--action") {
  const action = args[1] || "";
  if (!action) {
    emit({ ok: false, reason: "missing_action", action: "" });
    process.exit(2);
  }
  if (action === "echo") {
    emit({
      ok: true,
      alert: false,
      summary: "node-hello runtime=node",
      data: { runtime: "node" },
      action: "echo",
    });
    process.exit(0);
  }
  if (action === "pack") {
    emit({
      ok: true,
      alert: false,
      summary: "no judgment needed",
      data: { needs_judgment: false },
      action: "pack",
    });
    process.exit(0);
  }
  emit({ ok: false, alert: true, reason: "unknown_action", action });
  process.exit(2);
}

emit({ ok: false, reason: "unknown_flag", hint: "--manifest" });
process.exit(2);
