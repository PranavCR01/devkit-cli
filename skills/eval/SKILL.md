---
name: devkit:eval
description: Token optimization and prompt evaluation proxy. Intercepts Claude API calls, detects context waste, verifies suggestions with Claude-as-judge. Sub-commands: start, stop, status, report, versions, compare, verify.
argument-hint: ["start [--port 9999]", "stop", "status", "report [--session id] [--output json]", "versions", "compare <v1-id> <v2-id>", "verify <call-id>"]
allowed-tools: Bash(python -m devkit.cli eval *)
---

Token optimization and prompt evaluation for Claude Code sessions.

## Sub-commands

- **start** — start local proxy (sets ANTHROPIC_BASE_URL=http://localhost:9999)
- **stop** — stop proxy
- **status** — proxy running? current session stats?
- **report** — waste findings and verified suggestions for current session
- **versions** — prompt version history (stored in memory)
- **compare** — diff two prompt versions
- **verify** — manually run judge on a specific intercepted call

## Setup (first time)

After `devkit:eval start`, set in Claude Code settings:
```json
{ "env": { "ANTHROPIC_BASE_URL": "http://localhost:9999" } }
```
Restart Claude Code after setting.

## Steps

1. Parse the sub-command from `$ARGUMENTS`
2. Run: `python -m devkit.cli eval $ARGUMENTS`
3. Display the output
