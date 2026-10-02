# Windows porting notes (Linux core -> Windows)

The core (`grokkit.py`, `lib/`, `schemas/`, `prompts/`) is the same files on both platforms. Develop and test on Linux; on Windows only these differ.

| Topic | Linux | Windows |
|---|---|---|
| Setup | `adapters/linux/setup.sh` | `adapters/windows/setup.ps1` |
| Python | `python3` | `python` or `py` (3.12+) |
| Kit path | e.g. `~/grokkit` | e.g. `%USERPROFILE%\grokkit` |
| Paths in commands | `/` | `/` works too (pathlib); `\` fine in PowerShell |
| Text files | UTF-8, LF | Workers read UTF-8 and accept CRLF; keep files UTF-8 |
| Version stamp | `current_grok_bot` = Linux Grok Bot version | set to the Windows Grok Bot version and restamp (`version-gate stamp`) |

Gotchas seen on Windows:
- PowerShell 5 here-strings and non-ASCII dashes in `.ps1` can break the parser. Keep scripts ASCII and short, or write a `.py` and run it.
- `.ps1` files dropped in `drop/` are classified by `artifact-pd` as helpers, never workers.
- Hard-coded `python` was replaced with `sys.executable` in runner hints (0.3.0), so the printed commands match the interpreter you used.

Frozen Windows-verified reference of 0.2.1: private repo `grokkit-windows-ref`, tag `v0.2.1-win`.
