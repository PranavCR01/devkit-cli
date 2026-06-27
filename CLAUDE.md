# DevKit CLI

Python CLI tool for developer context, security scanning, memory management, and token optimization.
Transforms into a Claude Code skill in Slice 7.

## Stack
- Python 3.11 (Windows), 3.10+ minimum
- Typer (CLI framework) + Rich (terminal output)
- Anthropic SDK (direct, not LiteLLM in v1)
- SQLite for state + memory; sqlite-vec for KNN embeddings; FTS5 for keyword search
- sentence-transformers all-MiniLM-L6-v2 (384-dim, CPU-only, ~22MB, cached after first download)
- Semgrep (subprocess, timeout required)

## Commands
- `devkit init` — create ~/.devkit/ dirs, state.db, memory.db schema, manifest.json, session hook
- `devkit config set/get/list` — manage API keys and settings
- `devkit scan` — security scanning with memory enrichment (Slices 1+3, working)
- `devkit memory save/list/contradict/switch/workstreams/snapshot` — temporal memory (Slice 2, working)
- `devkit search` — cross-project semantic + keyword search (Slice 2, working)
- `devkit context list/add/build/budget/clear/refresh/register/inject` — context assembly (Slice 4, working)
- `devkit fork` — feature forking (Slice 5)
- `devkit eval` — token optimization (Slice 6)

## Development
```
pip install -e ".[memory,graph,eval]"
python -m devkit.cli --help
```

## Key files

### Slice 1 — Security scanning
- `devkit/cli.py` — entry point, command registration
- `devkit/config.py` — Config class, ~/.devkit/config.json
- `devkit/state.py` — State class, SQLite state.db
- `devkit/commands/config_cmd.py` — config set/get/list
- `devkit/core/scanner/prompts.py` — 18 security rules (ported from Sentinel)
- `devkit/core/scanner/classifier.py` — Tier 1/2 file classification
- `devkit/core/scanner/scorer.py` — scoring math (weights: security 0.7, quality 0.3)
- `devkit/core/scanner/semgrep_runner.py` — Semgrep subprocess + JSON parsing
- `devkit/core/scanner/claude_analyzer.py` — Claude API calls with prompt caching
- `devkit/core/scanner/graph_guide.py` — knowledge graph + blast radius BFS

### Slices 1+3 — Scan pipeline (modified by Slice 3)
- `devkit/commands/scan.py` — scan CLI; `--dismiss <id>` dismisses a memory fact; auto_learn prompt for medium/low; `[M] Seen before` badge in output
- `devkit/core/scanner/orchestrator.py` — `Finding` has `memory_match: SearchResult | None`; `ScanOrchestrator` accepts `memory_store` + `auto_learn`; `_store_findings_in_memory()` stores critical/high (deduped by title); `_enrich_with_memory()` attaches badge at RRF score >= 0.020

### Slice 2 — Memory + search
- `devkit/commands/memory_cmd.py` — memory_app sub-commands (save/list/contradict/switch/workstreams/snapshot)
- `devkit/commands/search_cmd.py` — search command (text + json output)
- `devkit/core/memory/store.py` — MemoryStore ABC, Fact, SearchResult, FactType
- `devkit/core/memory/embedder.py` — Embedder singleton (lazy-loads model, Rich spinner on first run)
- `devkit/core/memory/sqlite_backend.py` — SQLiteBackend: `__init__(db_path, embedder=None)`; pre-warmed embedder skips lazy load; save/search/contradict/snapshot/workstreams
- `devkit/core/search/rrf.py` — Reciprocal Rank Fusion (k=60)
- `devkit/core/search/searcher.py` — public Python API: search(), detect_project(), get_backend()

### Slice 4 — Context assembly
- `devkit/commands/context_cmd.py` — context_app: list/add/build/budget/clear/refresh/register/inject subcommands
- `devkit/core/context/manifest.py` — Manifest class: load/save ~/.devkit/manifest.json; register_project, update_scan, update_fact_count, update_workstream, refresh_knowledge_graphs (rescans graphs + recounts facts), get_all_context_items
- `devkit/core/context/assembler.py` — ContextAssembler: add_item (whole-item budget drop), render (XML block), summary; tiktoken optional with word-count fallback

### Runtime state (~/.devkit/)
- `config.json` — API keys and settings
- `state.db` — scan history
- `memory.db` — facts, contradictions, episodes, workstreams, session_snapshots, FTS5 (facts_fts), sqlite-vec (fact_vec + fact_vec_map)
- `manifest.json` — registry of all projects, knowledge graphs, fact counts, workstreams, blueprints; written by `devkit init`, updated by scan/memory save/memory switch
- `context_session.json` — ephemeral; tracks current assembled context for `devkit context budget/clear`
- `hooks/session-start.sh` — Claude Code SessionStart hook; calls `devkit context inject --format hook` (written by `devkit init`)

## Rules
- Manifest triggers are best-effort — all calls to `Manifest()` in scan.py, memory_cmd.py are wrapped in `try/except Exception: pass`; never block the caller
- `context inject` must never crash or print to stdout on error — it has 5 nested guards + outer `except Exception: pass`; the session hook depends on silent failure
- `Manifest.refresh_knowledge_graphs()` also recounts facts via raw `sqlite3` (no sqlite-vec): `SELECT project, COUNT(*) FROM facts WHERE invalid_at IS NULL GROUP BY project`; matches by direct name then basename fallback
- `context inject` collects all facts into one content string before calling `add_item` once — renders a single `[SNAPSHOT] project / session-snapshot` header, not one per fact
- `context list` summary line uses `|` not `·` — the middle-dot is non-ASCII and crashes cp1252
- Always run /plan before writing any code; show file list and wait for approval
- Never modify `~/.devkit/` schema without migrating existing data
- All Claude API calls must use `cache_control: ephemeral` on stable system prompts
- Semgrep subprocess must have timeout (default 120s); exit code 1 = findings, not error
- Config keys are always stored lowercase; normalize with `.lower()` at command boundary
- sqlite-vec requires loading per-connection: call `_load_sqlite_vec(conn)` in every `_connect()`
- FTS5 keyword search uses OR terms (`"word1" OR "word2"`), not AND — avoids zero results on multi-word queries
- Contradiction threshold is 0.85 cosine similarity; tunable via `CONTRADICTION_THRESHOLD` in sqlite_backend.py
- MemoryStore ABC is the v1/v2 stability contract — CLI and scan commands must only call methods on the interface
- `auto_learn` is OFF by default — enable with `devkit config set auto_learn true`; critical/high findings auto-stored, medium/low prompt user
- Scanner has no runtime dep on `[memory]` — `TYPE_CHECKING` guard in orchestrator.py; `_get_memory_store()` in scan.py catches `(ImportError, RuntimeError)` and returns None
- `_enrich_with_memory()` threshold is 0.020 RRF score (≈ rank-1 semantic result); spec's "0.80 cosine" doesn't map to RRF values
- Pass a pre-warmed `Embedder` instance to `SQLiteBackend(db_path, embedder=...)` so the download spinner fires before the scan, not mid-scan
- `--dismiss` in scan takes a memory fact ID (prefix ok), not a scan finding ID — use `devkit memory list --type vulnerability_pattern` to find IDs

## Windows Gotchas (Python 3.11, cp1252 terminal)
- Do NOT use `✓`, `✗`, or any non-ASCII in `typer.echo()` or `console.print()` — cp1252 will crash
- Rich markup strings like `[hidden]` render as empty; use plain strings (e.g. `"****"`) instead
- Rich tables: use `box=rich.box.ASCII` to avoid unicode box-drawing characters
- pyproject.toml build-backend must be `setuptools.build_meta`, NOT `setuptools.backends.legacy:build`
- Semgrep on Windows: pip wheels may fail — Docker fallback documented in devkit-setup-and-sentinel-prompt.md
- sqlite-vec wheel is `py3-none-win_amd64` (confirmed working); `enable_load_extension` is available in Python 3.11 Windows
- HuggingFace model cache shows symlink warning on Windows (non-fatal — just a Developer Mode advisory)
