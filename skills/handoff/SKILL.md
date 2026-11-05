---
name: handoff
description: Shared task ledger for coordinating multiple coding agents -- create tasks with acceptance criteria and dependencies, claim with a lease, hand over with notes, take over, recover crashed agents, and complete with commit traceability and merge-conflict checks.
argument-hint: ["create <title> -a <criterion> [-d <dep-id>]", "list", "claim <id> [--worktree]", "note <id> <text>", "check <id> <n>", "release <id> -m <handover note>", "done <id>", "recover", "brief <id>", "stats"]
allowed-tools: Bash(python -m devkit.cli handoff *)
---

Use this when work is split across agents or sessions.

## Rules for agents

1. Set `DEVKIT_AGENT=<your-name>` (or pass `--agent`).
2. `claim` before working; read the printed brief, especially START HERE.
3. `heartbeat` during long work so the lease does not expire.
4. Before stopping without finishing: `release <id> -m "done: ... next: ... risks: ..."`.
5. `check` each acceptance criterion only after verifying it (tests, review); then `done`.
6. A human correcting an agent should log it: `note <id> "..." --intervention`.

## Steps

1. Run: `python -m devkit.cli handoff $ARGUMENTS`
2. Display the output.
