---
name: devkit:context
description: Discover and assemble context from knowledge graphs, memory snapshots, workstreams, and blueprints. Sub-commands: list, add, build, budget, clear, refresh, register.
argument-hint: ["list [--project name] [--type graph|snapshot|workstream|blueprint]", "add <item-id>", "build [--token-cap 8000]", "budget", "clear", "refresh"]
allowed-tools: Bash(python -m devkit.cli context *)
---

Discover available context and inject it into the session.

## Sub-commands

- **list** — show all available context items across all projects
- **add** `<item-id>` — inject a specific item (use ID from `list` output)
- **build** — interactive multi-select with live token budget
- **budget** — show current token usage of assembled context
- **clear** — clear assembled context
- **refresh** — rescan all projects for new knowledge graphs

## Steps

1. Parse the sub-command from `$ARGUMENTS`
2. Run: `python -m devkit.cli context $ARGUMENTS`
3. For `add` and `build`: display the injected context so user can confirm it
