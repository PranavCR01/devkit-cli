# DevKit — Slice 7: Skill/Plugin Transformation

## Overview

Slice 7 packages the working CLI as a Claude Code plugin. All 6 commands become slash commands. No new functionality — pure packaging and registration. The CLI continues to work identically as a standalone tool.

**Prerequisite: All 6 slices working end-to-end as a CLI tool.**

---

## Goals

- All 6 DevKit commands available as Claude Code slash commands
- Plugin installable from GitHub (marketplace publishing optional later)
- SessionStart hook wired for automatic snapshot injection
- Commands namespaced as `/devkit:scan`, `/devkit:memory`, etc.
- Plugin structure follows Understand Anything's proven pattern (SKILL.md per command)
- CLAUDE.md stays as contributor guide only (not command registration)

---

## Success Criteria

- `/devkit:scan .` works inside Claude Code and calls the CLI correctly
- `/devkit:memory save "test decision"` stores a fact
- `/devkit:search "RLS pattern"` returns results
- SessionStart hook injects snapshot at session start (verify Claude acknowledges it)
- `/plugin install PranavCR01/devkit-cli` works from GitHub
- Namespacing prevents collision with any other installed skills

---

## File Structure Additions

```
devkit-cli/
├── .claude-plugin/
│   ├── plugin.json             # Plugin manifest (minimal — name + source only for marketplace)
│   └── plugin-local.json       # Extended manifest for local install
├── skills/
│   ├── scan/
│   │   └── SKILL.md            # /devkit:scan
│   ├── memory/
│   │   └── SKILL.md            # /devkit:memory
│   ├── search/
│   │   └── SKILL.md            # /devkit:search
│   ├── context/
│   │   └── SKILL.md            # /devkit:context
│   ├── fork/
│   │   └── SKILL.md            # /devkit:fork
│   └── eval/
│       └── SKILL.md            # /devkit:eval
├── agents/
│   ├── scan-triage.md          # Subagent: enriches findings with education content
│   └── blueprint-adapter.md    # Subagent: helps adapt blueprints to new stacks
└── hooks/
    └── hooks.json              # SessionStart snapshot injection
```

---

## Plugin Manifest

`.claude-plugin/plugin.json` — marketplace-compatible (minimal fields only):

```json
{
    "name": "devkit",
    "source": "https://github.com/PranavCR01/devkit-cli"
}
```

**Warning:** The marketplace schema only supports `name` and `source`. Adding extra fields causes schema validation failures and breaks installation. Keep this file minimal.

`.claude-plugin/plugin-local.json` — for local install (extended metadata):

```json
{
    "name": "devkit",
    "version": "1.0.0",
    "description": "Developer context, security scanning, memory management, and token optimization for Claude Code",
    "author": "PranavCR01",
    "source": "https://github.com/PranavCR01/devkit-cli",
    "components": {
        "skills": [
            "skills/scan/SKILL.md",
            "skills/memory/SKILL.md",
            "skills/search/SKILL.md",
            "skills/context/SKILL.md",
            "skills/fork/SKILL.md",
            "skills/eval/SKILL.md"
        ],
        "agents": [
            "agents/scan-triage.md",
            "agents/blueprint-adapter.md"
        ],
        "hooks": "hooks/hooks.json"
    }
}
```

---

## SKILL.md Files

Each skill file follows the Understand Anything pattern exactly: YAML frontmatter + Markdown body.

The `name` field = the slash command name. The `allowed-tools` field = what the skill is permitted to call.

### `skills/scan/SKILL.md`

```markdown
---
name: devkit:scan
description: Security scan a local codebase. Graph-guided Tier 1 prioritization when .understand-anything/knowledge-graph.json exists. Supports --mode web|api|ai|all (default: all), --output json|text, --no-graph, --severity critical|high|medium|low, --save, --dismiss <id>.
argument-hint: ["[path] [--mode web|api|ai|all] [--output json|text] [--severity critical|high|medium|low] [--save] [--no-graph]"]
allowed-tools: Bash(python3 -m devkit.cli scan *)
---

Run a DevKit security scan on the specified path.

## Pre-flight checks

1. Verify `devkit` is installed: run `python3 -m devkit.cli --version`
2. If not installed, run: `pip install -e /path/to/devkit-cli`
3. Verify `ANTHROPIC_API_KEY` is set: run `python3 -m devkit.cli config get ANTHROPIC_API_KEY`

## Steps

1. Confirm the path to scan (default: `.` = current project directory)
2. Run: `python3 -m devkit.cli scan $ARGUMENTS`
3. Display the full findings output to the user
4. If `--save` was specified, confirm findings were stored in memory

## Notes

- `.understand-anything/knowledge-graph.json` activates graph-guided mode automatically
- Run `devkit context list` first to see if a knowledge graph exists for this project
- For large codebases (>50K lines), use `--no-claude` for Semgrep-only first pass
- Use `--dismiss <finding-id>` to mark a finding as a false positive
```

### `skills/memory/SKILL.md`

```markdown
---
name: devkit:memory
description: Save and retrieve developer decisions, patterns, and architectural choices across projects. Sub-commands: save, list, switch, snapshot, contradict, workstreams.
argument-hint: ["save <content> [--type decision|pattern|bug|architecture] [--workstream name]", "list [--project name] [--type type]", "switch <workstream>", "snapshot", "contradict <fact-id>", "workstreams"]
allowed-tools: Bash(python3 -m devkit.cli memory *)
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
2. Run: `python3 -m devkit.cli memory $ARGUMENTS`
3. Display the output

## Notes

- All facts are stored in `~/.devkit/memory.db`
- Facts are scoped by project (detected from git root directory name)
- Contradiction invalidates old fact but preserves it in history
```

### `skills/search/SKILL.md`

```markdown
---
name: devkit:search
description: Cross-project semantic + keyword search across all stored developer decisions, patterns, and vulnerability findings. Supports --project, --type, --limit, --output json, --include-invalid.
argument-hint: ["<query> [--project name] [--type decision|pattern|bug|architecture|vulnerability_pattern] [--limit 10] [--output json]"]
allowed-tools: Bash(python3 -m devkit.cli search *)
---

Search across all DevKit memory for relevant decisions and patterns.

## Steps

1. Take the search query from `$ARGUMENTS`
2. Run: `python3 -m devkit.cli search $ARGUMENTS`
3. Display results with source attribution (project, date, type)

## Notes

- Returns results from ALL projects by default (cross-project)
- Results include semantic similarity score and source citation
- Superseded facts excluded by default (--include-invalid to show them)
```

### `skills/context/SKILL.md`

```markdown
---
name: devkit:context
description: Discover and assemble context from knowledge graphs, memory snapshots, workstreams, and blueprints. Sub-commands: list, add, build, budget, clear, refresh, register.
argument-hint: ["list [--project name] [--type graph|snapshot|workstream|blueprint]", "add <item-id>", "build [--token-cap 8000]", "budget", "clear", "refresh"]
allowed-tools: Bash(python3 -m devkit.cli context *)
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
2. Run: `python3 -m devkit.cli context $ARGUMENTS`
3. For `add` and `build`: display the injected context so user can confirm it
```

### `skills/fork/SKILL.md`

```markdown
---
name: devkit:fork
description: Extract a feature pattern (subgraph + memory decisions) from one project as a reusable blueprint, then apply it as context in a new project. Sub-commands: <feature> --from <project>, list, inspect, apply, delete.
argument-hint: ["<feature> --from <project> [--name blueprint-name] [--max-nodes 30]", "list", "inspect <blueprint-name>", "apply <blueprint-name> [--context-only]", "delete <blueprint-name>"]
allowed-tools: Bash(python3 -m devkit.cli fork *)
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
2. Run: `python3 -m devkit.cli fork $ARGUMENTS`
3. For `apply`: display the injected blueprint context

## Notes

- Requires `.understand-anything/knowledge-graph.json` for extraction
- Run `devkit context refresh` first if the graph isn't detected
- Blueprints transfer pattern/reasoning; code exemplars need adaptation
```

### `skills/eval/SKILL.md`

```markdown
---
name: devkit:eval
description: Token optimization and prompt evaluation proxy. Intercepts Claude API calls, detects context waste, verifies suggestions with Claude-as-judge. Sub-commands: start, stop, status, report, versions, compare, verify.
argument-hint: ["start [--port 9999]", "stop", "status", "report [--session id] [--output json]", "versions", "compare <v1-id> <v2-id>", "verify <call-id>"]
allowed-tools: Bash(python3 -m devkit.cli eval *)
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
2. Run: `python3 -m devkit.cli eval $ARGUMENTS`
3. Display the output
```

---

## Agents

### `agents/scan-triage.md`

```markdown
---
name: scan-triage
description: Enriches security findings with educational explanations and business impact. Called by the scan skill for high-severity findings that need deeper explanation.
---

You are a security educator. Given a list of security findings, enrich each one with:
- why_it_happens: why AI code generators produce this vulnerability
- real_world_example: a real breach caused by this pattern (company + year)
- remediation_priority: why this should be fixed first/second/last

Return JSON array of enriched findings.
```

### `agents/blueprint-adapter.md`

```markdown
---
name: blueprint-adapter
description: Helps developers adapt a feature blueprint to their current project's stack. Takes a blueprint and the current project's tech stack, returns adaptation guidance.
---

You are a software architect. Given a feature blueprint from one project and a description
of the current project's tech stack, explain:
1. What transfers directly (patterns, decisions, architecture)
2. What needs adaptation (specific code, library names, API calls)
3. Step-by-step adaptation guide for the key differences

Be specific about what to change, not just that things are different.
```

---

## Hooks

`hooks/hooks.json`:

```json
{
    "SessionStart": [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": "python3 -m devkit.cli memory snapshot --format hook --project \"$(basename $(git rev-parse --show-toplevel 2>/dev/null || pwd))\" 2>/dev/null || true"
                }
            ]
        }
    ]
}
```

This replaces the shell script from Slice 2 with the native plugin hook mechanism. The `|| true` ensures the hook never fails silently and breaks Claude Code startup.

---

## Installation Instructions

### Local development install

```bash
# In the devkit-cli directory
pip install -e ".[memory,graph,eval]"
```

Then in Claude Code:
```
/plugin install /path/to/devkit-cli
```

### From GitHub

```
/plugin marketplace add PranavCR01/devkit-cli
/plugin install devkit
```

### Verify installation

```
/devkit:scan --version
/devkit:memory snapshot
```

---

## Namespace Decision

Use `devkit:` prefix on all commands:

- `/devkit:scan`
- `/devkit:memory`
- `/devkit:search`
- `/devkit:context`
- `/devkit:fork`
- `/devkit:eval`

**Why namespace:** Prevents collision with any other installed skills. `scan`, `memory`, `search` are common enough that other skills might use them. The namespace makes DevKit's commands unambiguous.

**Shorthand option:** Users who want shorter commands can add aliases to their own project CLAUDE.md:
```
When I type /scan, run /devkit:scan with the same arguments.
```

---

## Open Decisions

1. **Marketplace publishing** — requires Anthropic approval. For portfolio purposes, GitHub install is sufficient. Submit for marketplace when all slices are stable and tested.

2. **Windows path handling** — the hook command uses bash syntax (`$(...)`) which doesn't work in Windows cmd.exe. Test if Claude Code's hook execution uses bash on Windows, or if you need a PowerShell fallback.

3. **`allowed-tools` scope** — `Bash(python3 -m devkit.cli scan *)` restricts the skill to only call the specific CLI command. This is correct and prevents the skill from running arbitrary bash. Keep it narrow.

4. **Versioning across files** — when releasing a new version, update version in: `pyproject.toml`, `.claude-plugin/plugin-local.json`, and the top of each SKILL.md. Consider a `scripts/bump-version.sh` to automate this.

5. **Subagent invocation** — the agents (scan-triage, blueprint-adapter) are NOT automatically called by skills. Skills must explicitly invoke them by name. Add invocation instructions to the relevant SKILL.md bodies when the agents are ready.

---

## v2 Additions (Post Slice 7)

Document here, not in the slice docs:

- **Graphiti backend** — implement `GraphitiBackend` in `devkit/core/memory/graphiti_backend.py`, expose as `devkit config set memory_backend graphiti`, add `devkit memory migrate --to graphiti`
- **Full CPG scanning** — add `tree-climber` dependency behind `devkit[cpg]` extra, integrate with scan orchestrator as enhanced Tier 1 analysis
- **LLMLingua-2 compression** — add to `devkit[eval]` extra, expose as config option `devkit config set eval_compress true`
- **RouteLLM model routing** — add trained router for task-based model selection, expose as `devkit config set eval_routing true`
- **MCP integration** — add `.mcp.json` registering Semgrep MCP and Graphiti MCP as optional extensions
- **Marketplace publishing** — submit to Claude Code marketplace after v2 is stable
