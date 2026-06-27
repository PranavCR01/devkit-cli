# DevKit CLI

**Security scanning, temporal memory, cross-project search, context assembly, feature forking, and token optimization — all in one CLI for developers building with Claude Code.**

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![Typer](https://img.shields.io/badge/Typer-0.12+-000000?logo=fastapi&logoColor=white)](https://typer.tiangolo.com)
[![Claude API](https://img.shields.io/badge/Claude-Sonnet%204.6%20%2B%20Haiku%204.5-CC785C?logo=anthropic&logoColor=white)](https://anthropic.com)
[![Semgrep](https://img.shields.io/badge/Semgrep-OSS-20B2AA?logo=semgrep&logoColor=white)](https://semgrep.dev)
[![SQLite](https://img.shields.io/badge/SQLite%20%2B%20sqlite--vec-embedded-003B57?logo=sqlite&logoColor=white)](https://sqlite.org)
[![sentence-transformers](https://img.shields.io/badge/sentence--transformers-all--MiniLM--L6--v2-FF6F00?logo=huggingface&logoColor=white)](https://sbert.net)
[![Headroom AI](https://img.shields.io/badge/Headroom%20AI-proxy-5C6BC0?logoColor=white)](https://github.com/headroom-ai/headroom)
[![NetworkX](https://img.shields.io/badge/NetworkX-PageRank-4CAF50?logoColor=white)](https://networkx.org)
[![Rich](https://img.shields.io/badge/Rich-terminal%20UI-FAD02E?logoColor=black)](https://github.com/Textualize/rich)
[![Windows](https://img.shields.io/badge/Windows-compatible-0078D4?logo=windows&logoColor=white)](https://python.org)

---

[GitHub](https://github.com/PranavCR01/devkit-cli) · [Portfolio](https://github.com/PranavCR01)

---

## The Problem

Every time you start a Claude Code session, you start from zero.

You re-explain your architecture. You paste in the same auth pattern you've already debugged twice. You wonder whether the codebase you're touching has any SQL injection vectors, but running a full security scan and interpreting the findings takes more context budget than the fix itself. You switch between projects and lose the thread of what you decided last week. By the time Claude understands what you're building, you've burned half your context window on orientation.

Claude Code's context window is the most expensive real estate in your development loop, and most of it gets wasted on:

- **No persistent memory.** Decisions, patterns, and bug discoveries vanish at session end. Claude re-learns the same things every time you open a new chat.
- **No security baseline.** There's no built-in way to know whether the code Claude just generated has OWASP Top 10 vulnerabilities before you ship it.
- **No context management.** Knowledge graphs, workstream context, and cross-project patterns exist in separate files with no way to selectively inject them into a session under a token budget.
- **No cross-project leverage.** The auth pattern you built in Project A is invisible when you start Project B. The same architectural mistake gets made twice.
- **Unchecked token waste.** Claude Code sends the full conversation on every request. There's no visibility into what's actually being read versus what's just padding the bill.

DevKit solves all of this. One local Python CLI, six commands, zero servers.

---

## What DevKit Does

### `devkit scan` — Security scanning with architectural awareness

Before DevKit, security scanning meant running Semgrep, getting 200 raw findings, and manually triaging which ones matter. Claude had no idea which files were auth-critical versus dead utility code.

DevKit runs Semgrep OSS and Claude Sonnet 4.6 in parallel, using an 18-rule security prompt ported from battle-tested production use. When an Understand Anything knowledge graph is present, it activates graph-guided Tier 1 prioritization — endpoint files, schema files, and high-inbound-degree nodes get scanned first. Each finding comes with a plain-English explanation, a copy-paste fix snippet, OWASP/CWE references, and a blast radius: the exact set of files that call into the vulnerable file, derived by reverse BFS over the call graph. Scoring uses a weighted formula (security × 0.7 + quality × 0.3) to produce a Grade A–F. Three scan modes — `--mode web`, `--mode api`, `--mode ai` — focus the ruleset on OWASP Top 10, OWASP API Top 10, or OWASP LLM Top 10 respectively.

```
devkit scan .                        # Full scan, current directory
devkit scan . --mode api --save      # API-focused, persist findings to memory
devkit scan . --severity high        # Show only high/critical
devkit scan . --no-claude            # Semgrep-only fast pass for large repos
```

### `devkit memory` — Temporal developer memory across sessions

Before DevKit, every Claude Code session re-learned your codebase. "We use pgvector for multi-tenant isolation" had to be pasted in every time. "The show renumbering uses a two-pass approach via temp values" was a comment in one file that Claude might not even read.

DevKit stores developer decisions, patterns, bugs, and architectural choices in a local SQLite database with 384-dimensional vector embeddings (sentence-transformers `all-MiniLM-L6-v2`, CPU-only, ~22MB). Contradiction detection automatically invalidates stale facts when a semantically similar but newer fact is saved — cosine similarity threshold 0.85. Named workstreams let you save and restore entire context branches (payment-feature, auth-bugfix, main) as you switch between tasks. A SessionStart hook automatically injects the relevant snapshot into every Claude Code session before you type your first message.

```
devkit memory save "decided: pgvector with user_id claim from JWT for RLS" --type decision
devkit memory save "supabase upsert silently fails without UNIQUE constraint" --type bug
devkit memory switch payment-feature    # save current context, load payment workstream
devkit memory snapshot                  # preview what gets injected at session start
devkit memory contradict <fact-id>      # mark a fact as superseded
```

### `devkit search` — Cross-project semantic + keyword search

Before DevKit, the auth decisions from your last project were in that project's chat history. Gone. The CORS configuration pattern you figured out the hard way lived in a comment in a file you'd have to remember to open.

DevKit's search runs hybrid retrieval — semantic similarity over sqlite-vec KNN embeddings fused with FTS5 BM25 keyword results using Reciprocal Rank Fusion (k=60) — across every project you've ever registered. Results come back with source attribution: project name, date, fact type, and relevance score. All offline after the initial model download.

```
devkit search "RLS pattern"
devkit search "JWT validation" --project swagath-central --type decision
devkit search "supabase" --output json | jq '.[] | .content'
```

```
DevKit Search: "RLS pattern"
Found 4 results across 3 projects

[1] swagath-central  decision  2025-06-10  score: 0.94
    "Used pgvector with row-level scoping — each user's data filtered by user_id claim from JWT"

[2] sentinel  pattern  2025-04-28  score: 0.87
    "Supabase RLS policies must be enabled explicitly: ALTER TABLE ... ENABLE ROW LEVEL SECURITY"

[3] cia-project  architecture  2025-05-02  score: 0.68
    "PostgreSQL pgvector extension for embedding storage with RLS for multi-tenant isolation"
```

### `devkit context` — Deliberate context assembly under a token budget

Before DevKit, injecting context into Claude Code meant either pasting entire files (expensive) or manually picking snippets (tedious). There was no inventory of what context even existed across your projects.

DevKit maintains a `~/.devkit/manifest.json` that tracks every registered project's knowledge graph, memory snapshot, workstreams, and blueprints — updated automatically after every scan, save, and fork. `devkit context list` gives you the full inventory in under 500ms. `devkit context build` opens an interactive multi-select with a live token counter. The assembler enforces a hard token budget by dropping whole items (never truncating mid-item) and appending an omission notice. The SessionStart hook calls `devkit context inject` automatically, so your session always opens with the right context already loaded.

```
devkit context list                              # Full inventory across all projects
devkit context add swagath-central:snapshot      # Inject one item
devkit context build --token-cap 10000           # Interactive multi-select
devkit context budget                            # Current token usage
devkit context refresh                           # Rescan all projects for new graphs
```

### `devkit fork` — Feature pattern transplanting via personalized PageRank

Before DevKit, reusing a feature pattern from a previous project meant reading through old code, extracting the relevant logic, and explaining it to Claude piece by piece. The architectural decisions behind the pattern — the ones that took hours of debugging to reach — existed only in your memory.

DevKit extracts feature subgraphs from Understand Anything knowledge graphs using personalized PageRank (Aider repomap–inspired). Seed nodes matching the feature name get a 50× weight boost; the PageRank algorithm propagates relevance through the call graph to select the top 30 nodes. Shared dependencies (files with full-graph in-degree ≥ 3) are detected and marked external — they're referenced but not copied, avoiding blueprint bloat. The result: a `~/.devkit/blueprints/<name>/` directory containing `subgraph.json` (extracted nodes and edges) and `blueprint.json` (memory decisions, stack context, transfer notes, token estimate). Applying a blueprint injects structured context that Claude can use to adapt the pattern to a new stack.

```
devkit fork auth --from swagath-central          # Extract auth subgraph + memory decisions
devkit fork payment --from swagath-central --max-nodes 20
devkit fork list                                 # All available blueprints
devkit fork apply auth-pattern                   # Inject as context into current session
devkit fork inspect auth-pattern                 # Show blueprint contents
```

```
DevKit Fork — extracting 'auth' from swagath-central

Loading knowledge graph... 847 nodes, 1203 edges
Finding seed nodes for 'auth'... 6 found
Running personalized PageRank (alpha=0.85)...
Detecting shared dependencies...
  -> src/lib/supabase.ts (in-degree: 14) — marked external
  -> src/utils/logger.ts (in-degree: 8)  — marked external

Saving blueprint: auth-pattern
  subgraph.json  (28 nodes, 41 edges)
  blueprint.json (3 memory facts, ~1,850 token estimate)
```

### `devkit eval` — Token optimization with ablation verification

Before DevKit, there was no visibility into which parts of Claude Code's context window were actually being used versus silently padding every request. Token costs accumulated invisibly.

DevKit wraps [Headroom AI](https://github.com/headroom-ai/headroom) as its compression engine — Headroom handles SSE passthrough, cache prefix stabilization, and content compression (JSON, logs, verbose text). DevKit's layer adds what Headroom doesn't have: ablation-based chunk verification and Claude-as-judge quality gating. The ablation worker runs async in the background, sampling 20% of requests over 2,000 tokens. It removes one context chunk at a time, re-runs the request with Claude Haiku, and measures output divergence via Jaccard distance. Chunks with distance < 0.15 (outputs are functionally equivalent without them) are saved as candidates. A Claude-as-judge with mandatory order-swap bias mitigation then verifies each candidate — requiring both orderings (A→B and B→A) to agree before marking a suggestion SAFE. Prompt version history is stored in the memory layer for regression tracking.

```
devkit eval start                    # Start Headroom proxy on port 9999
devkit eval status                   # Live stats: calls intercepted, tokens tracked
devkit eval report                   # Waste findings + verified suggestions
devkit eval versions                 # Prompt version history from memory
devkit eval verify <candidate-id>    # Run judge on a pending suggestion
devkit eval stop                     # Stop proxy
```

---

## Architecture

All six commands share a single local state directory at `~/.devkit/`. No server. No Docker. No cloud sync.

```
  Claude Code session
        |
        | SessionStart hook
        v
  devkit context inject          # auto-assembles snapshot + active workstream
        |
        +---> ~/.devkit/manifest.json        # registry: projects, graphs, blueprints
        |
  ======================================================
  |                   COMMANDS                        |
  |                                                   |
  |  devkit scan                devkit memory         |
  |    |                          |                   |
  |    +--> Semgrep OSS           +--> SQLite         |
  |    +--> Claude Sonnet         |    memory.db      |
  |    |    (Tier 1 files)        |    (facts,        |
  |    +--> graph_guide.py        |     workstreams,  |
  |    |    (Tier 1 priority      |     snapshots)    |
  |    |     + blast radius BFS)  |                   |
  |    |                          +--> sqlite-vec     |
  |    v                          |    (384-dim KNN   |
  |  state.db                     |     embeddings)   |
  |  (scan history, grades)       |                   |
  |                             devkit search         |
  |  devkit fork                  |                   |
  |    |                          +--> RRF fusion     |
  |    +--> NetworkX DiGraph       |    (semantic KNN  |
  |    +--> Personalized           |     + FTS5 BM25) |
  |    |    PageRank               |                   |
  |    +--> blueprints/            |                   |
  |         (subgraph.json +       |                   |
  |          blueprint.json)       |                   |
  |                                                   |
  |  devkit context             devkit eval           |
  |    |                          |                   |
  |    +--> manifest.json         +--> Headroom proxy |
  |    +--> ContextAssembler      |    (port 9999)    |
  |         (token budget,        +--> AblationWorker |
  |          whole-item drop)     |    (async, 20%    |
  |                               |     sampling)     |
  |                               +--> ClaudeJudge    |
  |                                    (order-swap    |
  |                                     bias mitiga-  |
  |                                     tion, Haiku)  |
  ======================================================
        |
        v
    ~/.devkit/
      config.json          API keys, settings
      state.db             scan history, grades
      memory.db            facts, contradictions, workstreams, snapshots
                           + FTS5 (facts_fts) + sqlite-vec (fact_vec)
      manifest.json        project registry, knowledge graphs, blueprints
      blueprints/<name>/   subgraph.json + blueprint.json
      eval/suggestions/    ablation candidates (candidate-<id>.json)
      hooks/               session-start.sh (SessionStart hook)
```

---

## Command Reference

### `devkit scan`

| Flag | Values | Description |
|------|--------|-------------|
| `[path]` | directory | Target directory (default: `.`) |
| `--mode` | `web \| api \| ai \| all` | Ruleset focus (default: `all`) |
| `--output` | `text \| json` | Output format (default: `text`) |
| `--severity` | `critical \| high \| medium \| low` | Filter results by minimum severity |
| `--no-graph` | flag | Skip graph-guided Tier 1; use heuristic fallback |
| `--no-semgrep` | flag | Claude-only analysis |
| `--no-claude` | flag | Semgrep-only fast pass |
| `--save` | flag | Persist findings to `state.db` and memory |
| `--dismiss` | fact ID prefix | Mark a memory fact as a false positive |

### `devkit memory`

| Sub-command | Signature | Description |
|-------------|-----------|-------------|
| `save` | `<content> [--type] [--workstream]` | Store a fact (types: `decision \| pattern \| bug \| architecture \| preference`) |
| `list` | `[--project] [--type] [--workstream]` | List facts for current or named project |
| `switch` | `<workstream>` | Save current context, load named workstream |
| `snapshot` | `[--token-cap 2000]` | Preview session injection content |
| `contradict` | `<fact-id>` | Invalidate a fact; preserves history |
| `workstreams` | — | List all workstreams for current project |

### `devkit search`

| Flag | Values | Description |
|------|--------|-------------|
| `<query>` | string | Search terms (hybrid semantic + keyword) |
| `--project` | name | Scope to one project (default: all projects) |
| `--type` | fact type | Filter by `decision \| pattern \| bug \| architecture \| vulnerability_pattern` |
| `--limit` | integer | Result count (default: 10) |
| `--output` | `text \| json` | Output format |
| `--include-invalid` | flag | Include superseded facts in results |

### `devkit context`

| Sub-command | Signature | Description |
|-------------|-----------|-------------|
| `list` | `[--project] [--type]` | All available context items with token estimates |
| `add` | `<item-id>` | Inject one item (`project:graph`, `project:snapshot`, `blueprint:name`) |
| `build` | `[--token-cap 8000]` | Interactive multi-select with live token counter |
| `budget` | — | Current assembled context token usage |
| `clear` | — | Clear assembled context |
| `refresh` | — | Rescan all project paths for new knowledge graphs |
| `register` | `<name> <path>` | Manually register a project |

### `devkit fork`

| Sub-command | Signature | Description |
|-------------|-----------|-------------|
| `<feature> --from <project>` | `[--name] [--max-nodes 30]` | Extract feature subgraph via personalized PageRank |
| `list` | — | All available blueprints with token estimates |
| `inspect` | `<name>` | Show blueprint contents: seed nodes, memory facts, stack context |
| `apply` | `<name> [--context-only]` | Inject blueprint into current session |
| `delete` | `<name>` | Remove blueprint and manifest entry |

### `devkit eval`

| Sub-command | Signature | Description |
|-------------|-----------|-------------|
| `start` | `[--port 9999]` | Start Headroom proxy, print `ANTHROPIC_BASE_URL` setup |
| `stop` | — | Terminate proxy via PID file kill |
| `status` | — | Proxy health + live session stats from `/stats` endpoint |
| `report` | `[--output json]` | Headroom savings + ablation candidates + judge verdicts |
| `versions` | — | Prompt version history from memory store |
| `verify` | `<candidate-id>` | Run Claude-as-judge on a pending ablation suggestion |
| `learn` | `--verbosity <level>` | Adjust Headroom verbosity steering |

---

## Smoke Test Results

Tested on `devkit-cli` itself — the tool scanning its own source.

### `devkit scan .`

```
DevKit Security Scan -- devkit-cli/
Mode: all | Graph-guided: NO (no knowledge graph for this project)

Files scanned : 131
Lines scanned : 21,814
Scan duration : 63.2s

Grade: B  |  Security: 81  |  Quality: 88

HIGH (3)
-------------------------------------------------------------
[1] Hardcoded API key path in test fixture
    devkit/tests/fixtures/config_sample.json:4
    Source: semgrep | Rule: p/secrets.generic-api-key

[2] Subprocess shell=True with user-controlled input
    devkit/core/scanner/semgrep_runner.py:47
    Source: claude | OWASP: A03:2021 | CWE-78

[3] Missing input validation on project name parameter
    devkit/commands/memory_cmd.py:89
    Source: claude | OWASP: A03:2021
```

### `devkit search "RLS pattern"`

```
Found 7 results across 3 projects
(swagath-central, sentinel, cia-project)

Top result: score 0.94 -- decision from swagath-central (2025-06-10)
"Used pgvector with row-level scoping -- each user's data filtered by user_id claim from JWT"

Search time: 1.1s (offline, CPU-only embeddings)
```

### `devkit eval status` (after one Claude Code session)

```
Headroom proxy: RUNNING (port 9999)

Session stats:
  Calls intercepted : 8
  Tokens tracked    : 67,281
  Tokens optimized  : 61,804
  Tokens saved      : 5,477  (8.1%)
  Est. cost saved   : $0.016

Ablation candidates : 2 pending verification
Run: devkit eval verify <id>
```

---

## Installation

**Prerequisites:** Python 3.11, `pip`, an Anthropic API key. Semgrep requires a separate install (`pip install semgrep` or see [semgrep.dev](https://semgrep.dev/docs/getting-started)).

```bash
# Clone the repo
git clone https://github.com/PranavCR01/devkit-cli.git
cd devkit-cli

# Install with all extras
pip install -e ".[memory,graph,eval]"

# Initialize ~/.devkit/ directory structure
devkit init

# Set your API key
devkit config set anthropic_api_key sk-ant-...

# Verify
devkit --help
```

**Extras breakdown:**

| Extra | Installs | Required for |
|-------|----------|--------------|
| `memory` | sentence-transformers, sqlite-vec, numpy | `devkit memory`, `devkit search`, scan enrichment |
| `graph` | networkx, numpy | `devkit fork create` |
| `eval` | headroom-ai, httpx, datasketch, xxhash | `devkit eval` |

**Claude Code slash commands** (Slice 7): commands also install as `/devkit:scan`, `/devkit:memory`, `/devkit:search`, `/devkit:context`, `/devkit:fork`, `/devkit:eval` in Claude Code. See [`skills/`](./skills/) for the skill definitions and [`hooks/`](./hooks/) for the SessionStart hook.

**SessionStart hook** (automatic context injection): run `devkit init` and it writes `~/.devkit/hooks/session-start.sh` and registers it in `~/.claude/settings.json`. Every new Claude Code session opens with your relevant memory snapshot already loaded.

---

## Local Development

```bash
# Install in editable mode with all extras
pip install -e ".[memory,graph,eval]"

# Run the CLI directly
python -m devkit.cli --help

# Run a scan on the repo itself
python -m devkit.cli scan . --no-claude   # Semgrep-only, fast

# Smoke-test memory
python -m devkit.cli memory save "test decision" --type decision
python -m devkit.cli search "test"

# Run tests
pytest tests/ -v
```

**Runtime state** lives entirely in `~/.devkit/` — safe to delete and re-run `devkit init` to reset. No migrations needed in development; production schema migrations are documented in each slice spec.

**Windows notes:** tested on Python 3.11, Windows 11, cp1252 terminal. All Rich output uses ASCII box-drawing. No emoji in `typer.echo()`. SQLite `enable_load_extension` is available in the Python 3.11 Windows build. sqlite-vec wheel is `py3-none-win_amd64`.

---

## Built By

**Pranav CR** — [github.com/PranavCR01](https://github.com/PranavCR01) · prc4@illinois.edu

DevKit grew out of frustration with building production AI applications in Claude Code and losing context, security awareness, and cross-project knowledge at every session boundary. It's local-first by design: no accounts, no cloud sync, no API endpoints to maintain — just a Python CLI that makes Claude Code sessions smarter the longer you use it.

The scan engine is ported from [Sentinel](https://github.com/PranavCR01/sentinel), a production security scanner with a TypeScript backend and Railway deployment. The memory layer is designed to upgrade to [Graphiti](https://github.com/getzep/graphiti) in v2 — the `MemoryStore` ABC is the stability contract. The eval layer wraps [Headroom AI](https://github.com/headroom-ai/headroom) and adds the ablation verification and Claude-as-judge layer it was missing.
