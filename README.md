# DevKit CLI

**Context engineering, security scanning, memory, token-waste analysis, delivery metrics, and multi-agent handoff for developers working with Claude Code.**

[![CI](https://github.com/PranavCR01/devkit-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/PranavCR01/devkit-cli/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-cp1252--safe-0078D4?logo=windows&logoColor=white)

DevKit is a local Python CLI (and Claude Code plugin). It keeps state in `~/.devkit/`, or in `$DEVKIT_HOME` if you set it, and runs no server. The test suite is offline: it uses a fake embedder and a fake Claude client, needs no API key, and runs in CI on Linux and Windows.

---

## What it does

| Command | What it does | How |
|---|---|---|
| `devkit context` | Injects relevant, token-budgeted context at session start | SessionStart hook. Pinned items come first, then project facts ranked by **RRF over hybrid BM25 + vector search**, recency, and fact type. Facts are packed one at a time under the token cap. |
| `devkit memory` | Stores decisions, bugs, and patterns over time, with workstreams | SQLite, FTS5, sqlite-vec with 384-dim MiniLM embeddings, and `valid_at` / `invalid_at` history |
| `devkit search` | Searches across projects | Hybrid search: FTS5 BM25 plus vector search, fused with Reciprocal Rank Fusion (k=60) |
| `devkit scan` | Security and quality scan, graded A to F | Semgrep (rule packs for the languages it finds) plus Claude reading line-numbered chunks. Partial scans are marked **INCOMPLETE** and never shown as clean. |
| `devkit fork` | Reuses a feature pattern from another project | Personalized PageRank over an Understand Anything knowledge graph |
| `devkit eval` | Finds context you can remove from a request without changing the answer | Offline ablation of a captured request, checked by a Claude judge that runs both orderings of each comparison, plus a benchmark of the judge itself |
| `devkit metrics` | Weekly and monthly delivery report | PRs opened and merged, median cycle time, and **AI cost per merged PR**. Cost comes from Claude Code transcripts and is matched to PRs by branch. |
| `devkit handoff` | Coordinates several coding agents | Task ledger with leases, handover notes, takeover, crash recovery, acceptance criteria, worktrees, commit traceability, and merge-conflict checks |

---

## Install

```bash
git clone https://github.com/PranavCR01/devkit-cli.git
cd devkit-cli
pip install -e ".[memory,graph,scan]"   # add ,eval for the Headroom proxy
devkit init --register-hook              # creates ~/.devkit and registers the SessionStart hook
devkit config set anthropic_api_key sk-ant-...   # or export ANTHROPIC_API_KEY
```

| Extra | Installs | Needed for |
|---|---|---|
| *(base)* | typer, rich, anthropic, httpx | `config`, `context`, `eval ablate/verify/bench`, `metrics`, `handoff`, `scan --no-semgrep` |
| `memory` | sentence-transformers, sqlite-vec, numpy | `memory`, `search`, retrieval in the hook, scan memory |
| `graph` | networkx, scipy, numpy | `fork create` |
| `scan` | semgrep | Semgrep pass of `scan` |
| `eval` | headroom-ai, datasketch, xxhash | `eval start/stop/status/learn` (Headroom proxy) |
| `dev` | pytest, numpy, sqlite-vec, networkx, scipy | running the tests |

**As a Claude Code plugin:** the repo contains `.claude-plugin/plugin.json`, `skills/`, `agents/`, and `hooks/hooks.json`. If you install the plugin, skip `--register-hook`, or the context will be injected twice.

---

## Commands

### `devkit context`: what each session starts with

```bash
devkit context list                       # inventory: graphs, snapshots, workstreams, blueprints
devkit context add blueprint:auth-pattern # pin an item (the hook includes it until `context clear`)
devkit context build --token-cap 8000     # interactive multi-select, pins the selection
devkit context inject                     # what the hook prints (try it)
devkit context clear
```

The hook runs `devkit context inject --format hook`. It reads the hook's JSON from stdin to find `cwd`, then builds the output in three steps:

1. Pinned items go in first, whole.
2. Project facts are ranked by **Reciprocal Rank Fusion** over three lists. The first is hybrid BM25 + vector search with a query built from the active workstream, the git branch, and the last five commit subjects. The second is recency. The third is fact-type priority (decisions and architecture before preferences).
3. Facts are added one by one until the token cap is reached, so a large project still gets its most relevant facts. Output stays under Claude Code's ~10k-character limit for hook output.

On any error the hook prints nothing and exits 0.

### `devkit memory` and `devkit search`

```bash
devkit memory save "supabase upsert silently fails without UNIQUE constraint" --type bug
devkit memory switch payments --note "half-way through webhook retries"
devkit memory workstreams           # * marks the active one
devkit search "RLS pattern" --project api --type decision
```

`switch` saves the current workstream's note, git branch, and recent fact ids, makes the target active, and shows what you saved there earlier. New facts are tagged with the active workstream. If you save a fact with cosine similarity of 0.85 or more to an existing fact of the same type, the older one is **superseded**. DevKit records the measured similarity. This catches near-duplicates; it is not logical contradiction detection. Searches scoped with `--project` or `--type` apply the filter in SQL, so in-scope results aren't crowded out by other projects. Scores shown are RRF scores, which fall roughly between 0.016 and 0.033.

### `devkit scan`

```bash
devkit scan . --mode api --severity high
devkit scan . --no-claude            # Semgrep only, no API key needed
devkit scan . --output json
```

- Tier 1 files (auth, API, DB, payments, framework imports) are picked by matching words in the **project-relative** path. When a knowledge graph is present, its endpoint, schema, and table nodes, plus nodes with high inbound degree, are also Tier 1.
- Claude sees line-numbered code (`N| ...`). Chunks repeat the file header when a file is split, and requests run with bounded concurrency (`scan_max_concurrency`).
- Score = security × 0.7 + quality × 0.3, mapped to a grade from A to F. **If any Claude chunk fails or Semgrep is skipped, the grade shows as `INCOMPLETE`** with warnings, and it isn't written to the manifest.
- With `auto_learn` on, critical and high findings are saved to memory under the project name. On later scans they're marked `[M] Seen before`. Memory is checked before new findings are saved, so a finding never matches itself.
- Config keys: `semgrep_timeout`, `scan_max_file_size_kb`, `scan_tier2_line_cap`, `scan_max_concurrency`, `auto_learn`, `default_model`.

### `devkit fork`

```bash
devkit fork create auth --from my-saas --max-nodes 30
devkit fork list
devkit fork apply auth-pattern
```

Seed nodes are matched on whole words, case-insensitively, so `auth` matches `AuthController` and the tag `Auth` but not `author`. Personalized PageRank (seed weight 50×) ranks the rest of the graph. Nodes imported by three or more files are marked as external shared dependencies, except the seeds themselves. Blueprint names are validated so they can't escape `~/.devkit/blueprints/`.

### `devkit eval`: measured context waste

DevKit doesn't sit on the request path; Headroom does. So ablation is an explicit, budgeted, offline job over a request you've captured:

```bash
devkit eval ablate request.json --dry-run     # plan + max API calls, no spend
devkit eval ablate request.json --samples 3   # run it
devkit eval report
devkit eval verify <id> --samples 3
devkit eval bench --split test --runs 3        # how trustworthy is the judge?
```

Real output from `--dry-run` on a request whose first message is behind a cache breakpoint:

```
Chunks: 2 found | 1 in cached prefix (skipped) | 0 below 100 tokens | 1 planned
  messages[2].content[0] (tool_result)     ~272 tokens
Max API calls: 5
```

How it works:

- The replay keeps `system` and `tools`. It removes thinking blocks from both versions of the request, and it replaces the chunk's content with a placeholder. `tool_use`/`tool_result` pairs stay intact and no message is sent empty.
- **Nothing inside the cached prefix is ablated.** That means nothing up to the last `cache_control` breakpoint, and nothing at all when the request uses top-level automatic caching. Changing a cached prefix forces a cache re-write at 1.25× the input price, which can cost more than the removal saves.
- The original request is replayed twice to measure noise. Jaccard distance against that baseline only filters out outputs that clearly changed; **the judge makes the decision**.
- The judge returns structured JSON and runs every comparison in both orderings, `--samples N` times each. It returns SAFE only if every run finds the reduced output no worse and no run reports lost information. If any call errors, the verdict is **INCONCLUSIVE, never SAFE**.
- `eval bench` measures the judge on 120 cases built from 30 annotated seeds: 60 degraded and 60 equivalent, split 60/60 into `dev` and `test` by seed. **Degraded labels come from construction**, not from a model: a deterministic operator changes exactly one annotated part of a correct answer, and each mutation records what it contradicts. The 9 operators are drop a step, change a constant, off-by-one, swap a path, flip a condition, drop a constraint, invent an API, wrong tool argument, and drop a tool call.
- It reports the **false-SAFE rate** (degraded output judged safe, the dangerous failure) with a 95% Wilson interval, errors per operator, and ranges across `--runs`. Model-written paraphrases are reported separately. Method, results, and limits are in [`docs/eval/judge-bench.md`](docs/eval/judge-bench.md); `devkit eval bench-build` regenerates the dataset from the seeds deterministically.
- Claude Code transcripts (`.jsonl`) are accepted too. They don't record the system prompt or tool schemas, so tool calls are replayed as text and the run is labeled `fidelity: transcript`.

`devkit eval start|stop|status|learn` manage the [Headroom](https://github.com/headroom-ai/headroom) compression proxy (port 8787; set `ANTHROPIC_BASE_URL=http://127.0.0.1:8787`).

### `devkit metrics`: throughput and AI cost per merged PR

```bash
devkit metrics --period week --since 90d            # uses `gh pr list`
devkit metrics --period month --output md           # paste into a weekly/monthly report
devkit metrics --prs prs.json --transcripts ~/.claude/projects/my-repo
```

The report shows PRs opened and merged, median cycle time, AI cost, cost per merged PR, and sessions, per week or month. Token usage comes from Claude Code transcripts and is deduplicated by message id. It's priced from a list-price table: cache writes at 1.25× input and cache reads at the per-model rate, overridable with `--prices`. Usage is matched to PRs through `gitBranch` → PR head branch. **Cost on branches without a PR is reported as unattributed**, along with the attribution rate. Tokens from models missing from the price table are counted separately. If a handoff ledger exists, its counts are added to the report.

Caveat: the transcript line format is internal to Claude Code and can change between versions. The parser skips lines it doesn't recognize instead of failing.

### `devkit handoff`: coordinating coding agents

```bash
devkit handoff create "Add idempotent retries to webhook sender" \
    -a "unit tests pass" -a "Idempotency-Key reused across attempts"
DEVKIT_AGENT=agent-a devkit handoff claim <id> --worktree   # isolated worktree + devkit/<id> branch
DEVKIT_AGENT=agent-a devkit handoff check <id> 1
DEVKIT_AGENT=agent-a devkit handoff release <id> -m "retry loop + tests done; key is regenerated per attempt in sender.py:41 - fix next"
DEVKIT_AGENT=agent-b devkit handoff claim <id>               # takeover; prints the brief
devkit handoff recover                                      # return expired leases to the pool
devkit handoff done <id>                                    # needs every criterion checked; records commits; refuses on merge conflicts
devkit handoff stats
```

Real output from the takeover above:

```
<devkit-task id='4bd818' project='demo' status='claimed'>
TASK: Add idempotent retries to webhook sender

ACCEPTANCE CRITERIA (all must be checked before `devkit handoff done`):
  [x] #1 unit tests pass
  [ ] #2 Idempotency-Key reused across attempts

HISTORY (oldest first):
  2026-11-04T23:29 agent-a handoff: retry loop + tests done; key is regenerated per attempt in sender.py:41 - fix next

START HERE (last handover note): retry loop + tests done; key is regenerated per attempt in sender.py:41 - fix next
</devkit-task>
```

- Claims are atomic (`BEGIN IMMEDIATE`). A test races eight agents for one task and expects exactly one winner.
- Dependencies block claims until they're done, and a lease must be renewed with `heartbeat`.
- `stats` counts completions, handoffs, takeovers, recoveries, integration conflicts, and manual interventions (logged with `note --intervention`).

---

## Architecture

```
Claude Code ──SessionStart hook──> devkit context inject
                                      │  pinned items (context_session.json)
                                      │  RRF( hybrid search(query from workstream+git), recency, type )
                                      v
 ~/.devkit/  (or $DEVKIT_HOME)
   config.json            settings (typed), API key
   memory.db              facts + FTS5 + sqlite-vec, workstreams (active flag), contradictions
   state.db               scan history
   manifest.json          projects, graphs, blueprints
   blueprints/<name>/     blueprint.json + subgraph.json
   eval/suggestions/      ablation candidates (+ judge verdicts)
   eval/bench/            judge benchmark reports
   handoff/ledger.db      tasks, criteria, deps, events, commits
```

---

## Status and limitations

- Ablation replays use the configured `fast_model` (default `claude-haiku-5-5`), not the model that served the original request. Measured equivalence is a strong signal, not proof, for the production model. `--model` overrides it.
- The bundled judge benchmark is small (14 cases) and hand-written. Treat its numbers as a smoke test and extend the dataset with your own labeled pairs.
- Cost figures in `metrics` are list-price estimates. They don't come from billing.
- Compression and attribution research is mostly validated on prose and QA. Code-heavy context is exactly what this tool is meant to measure, so check its suggestions before you apply them.

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

Windows notes: CLI output is ASCII-only (cp1252-safe), Rich tables use `box.ASCII`, and the sqlite-vec wheel `py3-none-win_amd64` works with Python 3.11's `enable_load_extension`.

---

**Pranav CR**, [github.com/PranavCR01](https://github.com/PranavCR01), prc4@illinois.edu. The scan engine is ported from Sentinel. The memory layer's `MemoryStore` ABC is the upgrade path to Graphiti.
