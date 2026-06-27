---
name: devkit:search
description: Cross-project semantic + keyword search across all stored developer decisions, patterns, and vulnerability findings. Supports --project, --type, --limit, --output json, --include-invalid.
argument-hint: ["<query> [--project name] [--type decision|pattern|bug|architecture|vulnerability_pattern] [--limit 10] [--output json]"]
allowed-tools: Bash(python -m devkit.cli search *)
---

Search across all DevKit memory for relevant decisions and patterns.

## Steps

1. Take the search query from `$ARGUMENTS`
2. Run: `python -m devkit.cli search $ARGUMENTS`
3. Display results with source attribution (project, date, type)

## Notes

- Returns results from ALL projects by default (cross-project)
- Results include semantic similarity score and source citation
- Superseded facts excluded by default (--include-invalid to show them)
