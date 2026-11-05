---
name: eval
description: Context-waste analysis for Claude Code requests. Ablates context chunks of a captured request offline, verifies each removal with an order-swapped Claude judge, benchmarks the judge, and wraps the Headroom compression proxy. Sub-commands: ablate, verify, report, bench, start, stop, status, versions, learn.
argument-hint: ["ablate <request.json|transcript.jsonl> [--dry-run] [--max-chunks 8] [--samples 1]", "report", "verify <candidate-id> [--samples 3]", "bench [dataset.jsonl] [--dry-run]", "start [--port 8787]", "stop", "status", "versions [--save prompt.md]"]
allowed-tools: Bash(python -m devkit.cli eval *)
---

Find context that can be removed from Claude Code requests without changing the answer.

## Sub-commands

- **ablate** `<file>` -- replay a captured request with one chunk at a time replaced by a placeholder; chunks inside the cached prefix are never touched. Always run with `--dry-run` first and show the user the API-call budget before running for real.
- **report** -- Headroom savings plus ablation candidates and verdicts
- **verify** `<id>` -- re-run the judge on a candidate (`--samples N` for more stability)
- **bench** -- measure the judge on labeled cases (accuracy, false-SAFE rate, order agreement)
- **start / stop / status** -- Headroom proxy (default port 8787)
- **versions** -- prompt version history; `--save <file>` snapshots a prompt

## Setup for the proxy

After `eval start`, set in Claude Code settings and restart Claude Code:
```json
{ "env": { "ANTHROPIC_BASE_URL": "http://127.0.0.1:8787" } }
```

## Steps

1. Parse the sub-command from `$ARGUMENTS`
2. Run: `python -m devkit.cli eval $ARGUMENTS`
3. Display the output. Only call a suggestion safe when its verdict is SAFE; INCONCLUSIVE means "do not apply".
