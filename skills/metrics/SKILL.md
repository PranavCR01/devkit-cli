---
name: metrics
description: Weekly or monthly delivery report -- PRs opened/merged, median cycle time, and AI cost per merged PR from Claude Code transcripts, plus agent-orchestration counts from the handoff ledger.
argument-hint: ["[--period week|month] [--since 90d] [--repo owner/name] [--output text|json|md]"]
allowed-tools: Bash(python -m devkit.cli metrics *)
---

## Steps

1. Run: `python -m devkit.cli metrics $ARGUMENTS` (requires the `gh` CLI, or `--prs export.json`)
2. Show the table. State that costs are list-price estimates and quote the attribution rate:
   cost on branches without a PR is reported as unattributed, not hidden.
