---
name: devkit:fork
description: Extract a feature pattern (subgraph + memory decisions) from one project as a reusable blueprint, then apply it as context in a new project. Sub-commands: <feature> --from <project>, list, inspect, apply, delete.
argument-hint: ["<feature> --from <project> [--name blueprint-name] [--max-nodes 30]", "list", "inspect <blueprint-name>", "apply <blueprint-name> [--context-only]", "delete <blueprint-name>"]
allowed-tools: Bash(python -m devkit.cli fork *)
---

Extract and reuse feature patterns across projects.

## Sub-commands

- **`<feature>` --from `<project>`** — extract feature and save as blueprint
- **list** — show all available blueprints
- **inspect** `<name>` — show blueprint contents
- **apply** `<name>` — inject blueprint as context into current session
- **delete** `<name>` — remove a blueprint

## Steps

1. Parse the sub-command from `$ARGUMENTS`
2. Run: `python -m devkit.cli fork $ARGUMENTS`
3. For `apply`: display the injected blueprint context

## Notes

- Requires `.understand-anything/knowledge-graph.json` for extraction
- Run `devkit context refresh` first if the graph isn't detected
- Blueprints transfer pattern/reasoning; code exemplars need adaptation
