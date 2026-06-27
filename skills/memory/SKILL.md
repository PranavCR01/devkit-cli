---
name: devkit:memory
description: Save and retrieve developer decisions, patterns, and architectural choices across projects. Sub-commands: save, list, switch, snapshot, contradict, workstreams.
argument-hint: ["save <content> [--type decision|pattern|bug|architecture] [--workstream name]", "list [--project name] [--type type]", "switch <workstream>", "snapshot", "contradict <fact-id>", "workstreams"]
allowed-tools: Bash(python -m devkit.cli memory *)
---

Manage temporal developer memory across projects.

## Sub-commands

- **save** `<content>` — save a fact with optional type and workstream
- **list** — show recent facts for current project
- **switch** `<workstream>` — save current context and load named workstream
- **snapshot** — show what will be injected at next session start
- **contradict** `<fact-id>` — mark a fact as superseded
- **workstreams** — list all workstreams for current project

## Steps

1. Parse the sub-command from `$ARGUMENTS`
2. Run: `python -m devkit.cli memory $ARGUMENTS`
3. Display the output

## Notes

- All facts are stored in `~/.devkit/memory.db`
- Facts are scoped by project (detected from git root directory name)
- Contradiction invalidates old fact but preserves it in history
