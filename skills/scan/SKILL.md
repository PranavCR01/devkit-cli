---
name: scan
description: Security scan a local codebase. Graph-guided Tier 1 prioritization when .understand-anything/knowledge-graph.json exists. Supports --mode web|api|ai|all (default: all), --output json|text, --no-graph, --severity critical|high|medium|low, --save, --dismiss <id>.
argument-hint: ["[path] [--mode web|api|ai|all] [--output json|text] [--severity critical|high|medium|low] [--save] [--no-graph]"]
allowed-tools: Bash(python -m devkit.cli scan *)
---

Run a DevKit security scan on the specified path.

## Pre-flight checks

1. Verify `devkit` is installed: run `python -m devkit.cli --version`
2. If not installed, run: `pip install -e "/path/to/devkit-cli[scan,memory]"`
3. Unless using `--no-claude`, verify an API key is set: `python -m devkit.cli config get anthropic_api_key` (or `ANTHROPIC_API_KEY` in the environment)

## Steps

1. Confirm the path to scan (default: `.` = current project directory)
2. Run: `python -m devkit.cli scan $ARGUMENTS`
3. Display the full findings output to the user
4. If `--save` was specified, confirm findings were stored in memory

## Notes

- `.understand-anything/knowledge-graph.json` activates graph-guided mode automatically
- Run `devkit context list` first to see if a knowledge graph exists for this project
- For large codebases (>50K lines), use `--no-claude` for Semgrep-only first pass
- Use `--dismiss <memory-fact-id>` to mark a remembered finding as a false positive (find ids with `devkit memory list --type vulnerability_pattern`)
- If the grade shows INCOMPLETE, part of the analysis failed (see the WARN lines); do not report it as a clean result
