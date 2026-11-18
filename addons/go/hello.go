package main

import (
	"encoding/json"
	"os"
)

func emit(v any, code int) {
	enc := json.NewEncoder(os.Stdout)
	_ = enc.Encode(v)
	os.Exit(code)
}

func main() {
	args := os.Args[1:]
	if len(args) < 1 {
		emit(map[string]any{"ok": false, "alert": false, "reason": "no_flags", "hint": "--manifest"}, 2)
	}
	switch args[0] {
	case "--manifest":
		emit(map[string]any{
			"ok":                 true,
			"id":                 "go-hello",
			"title":              "Example Go worker",
			"source":             "local",
			"priority":           99,
			"keywords":           []string{"go-hello"},
			"default_action":     "echo",
			"verified_grok_bot":  "0.58.0",
			"runtime":            "go",
			"hosts":              map[string]any{"class": "any"},
			"actions": []map[string]any{
				{"name": "echo", "use_bot": false, "summary": "Print a tiny hello as last-line JSON"},
				{"name": "pack", "use_bot": true, "summary": "Tiny judgment pack (none needed)"},
			},
		}, 0)
	case "--list-actions":
		emit(map[string]any{
			"ok":             true,
			"id":             "go-hello",
			"default_action": "echo",
			"actions": []map[string]any{
				{"name": "echo", "use_bot": false, "summary": "Print a tiny hello as last-line JSON"},
				{"name": "pack", "use_bot": true, "summary": "Tiny judgment pack (none needed)"},
			},
		}, 0)
	case "--action":
		action := ""
		if len(args) > 1 {
			action = args[1]
		}
		if action == "" {
			emit(map[string]any{"ok": false, "reason": "missing_action", "action": ""}, 2)
		}
		switch action {
		case "echo":
			emit(map[string]any{
				"ok":      true,
				"alert":   false,
				"summary": "go-hello runtime=go",
				"data":    map[string]any{"runtime": "go"},
				"action":  "echo",
			}, 0)
		case "pack":
			emit(map[string]any{
				"ok":      true,
				"alert":   false,
				"summary": "no judgment needed",
				"data":    map[string]any{"needs_judgment": false},
				"action":  "pack",
			}, 0)
		default:
			emit(map[string]any{"ok": false, "alert": true, "reason": "unknown_action", "action": action}, 2)
		}
	default:
		emit(map[string]any{"ok": false, "reason": "unknown_flag", "hint": "--manifest"}, 2)
	}
}
