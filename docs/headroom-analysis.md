# Headroom Codebase Analysis

Source: `D:/Python files/repos/headroom-analysis`
Date: 2026-06-26

---

## 1. Proxy Startup

**Entry point:** `headroom/cli/proxy.py:804` — `@main.command()` registering the `proxy()` function.

**Default port:** 8787. Configurable via `--port` CLI flag or `HEADROOM_PORT` env var.
- Option defined at `headroom/cli/proxy.py:107–115`, default value at line 111.

**What it spawns:** Uvicorn ASGI workers, imported from `headroom.proxy.server` (`headroom/cli/proxy.py:902–908`).
- Default: 1 worker. Configurable via `--workers` flag (`headroom/cli/proxy.py:149–154`).
- Server starts via `run_server(config, **run_kwargs)` at `headroom/cli/proxy.py:1393`.
- Optional embedding sidecar can be started before the main server if `--embedding-server` flag is passed (`headroom/cli/proxy.py:1345–1381`).

**Host:** `127.0.0.1` by default.

---

## 2. `headroom perf` and `headroom dashboard`

### `headroom dashboard`
- Defined at `headroom/cli/proxy.py:117–131`.
- Opens browser to `http://127.0.0.1:{port}/dashboard` (default port 8787).
- The HTML response is served at `headroom/proxy/server.py:2555–2596`.

### `headroom perf`
- Defined at `headroom/cli/perf.py:27–105`.
- **Input:** Reads JSONL log entries from `~/.headroom/logs/proxy.log`.
- **Output formats:** JSON (default), CSV, or human-readable text via `--format` flag.

**Fields in `PERF_RECORD_FIELDS`** (`headroom/perf/analyzer.py:748–766`):

| Field | Description |
|---|---|
| `timestamp` | Request timestamp |
| `request_id` | Unique request identifier |
| `model` | Claude model used |
| `client` | Client identifier |
| `num_messages` | Message count |
| `tokens_before` | Tokens before optimization |
| `tokens_after` | Tokens after optimization |
| `tokens_saved` | Delta |
| `cache_read` | Cache read tokens |
| `cache_write` | Cache write tokens |
| `cache_hit_pct` | Cache hit percentage |
| `optimization_ms` | Optimization latency |
| `transforms` | Applied transforms |
| `total_ms` | Total request latency |
| `tokens_out` | Output tokens |
| `ttfb_ms` | Time to first byte |
| `stages` | Pipeline stages |

**Aggregation:** `build_perf_summary()` in `headroom/perf/analyzer.py` produces per-model breakdowns with savings percentages.

### `/stats` HTTP endpoint
- `GET /stats` at `headroom/proxy/server.py:3143–3179`.
- Returns JSON with: request metrics, token usage, cost tracking, compression stats, cache stats, telemetry.

---

## 3. Python API for Compression Stats

**Class:** `CompressionStore` at `headroom/cache/compression_store.py:197–1180`.

**Access function (singleton):** `get_compression_store()` at `headroom/cache/compression_store.py:1255–1294`.
- Lazy-initializes with SQLite backend by default.
- Respects `HEADROOM_CCR_BACKEND` env var (default: `"sqlite"`).

**Key methods:**

```python
from headroom.cache.compression_store import get_compression_store

store = get_compression_store()

# Store compressed content
hash_key: str = store.store(
    original: str,
    compressed: str,
    original_tokens: int,
    compressed_tokens: int,
    original_item_count: int,
    compressed_item_count: int,
    tool_name: str | None,
    tool_signature_hash: str | None,
    compression_strategy: str | None,
    ttl: int | None,
)

# Retrieve by hash
entry = store.retrieve(hash_key: str, query: str | None = None)
# Returns CompressionEntry or None

# Get store-level stats
stats: dict = store.get_stats()
```

**Data class:** `CompressionEntry` at `headroom/cache/compression_store.py:141–180`:

| Field | Type | Notes |
|---|---|---|
| `hash` | str | Primary key |
| `original_content` | str | Pre-compression text |
| `compressed_content` | str | Post-compression text |
| `original_tokens` | int | |
| `compressed_tokens` | int | |
| `original_item_count` | int | |
| `compressed_item_count` | int | |
| `tool_name` | str \| None | |
| `tool_call_id` | str \| None | |
| `query_context` | str \| None | |
| `created_at` | float | Unix timestamp |
| `ttl` | int | Seconds until expiry |
| `tool_signature_hash` | str \| None | |
| `compression_strategy` | str \| None | |
| `retrieval_count` | int | |
| `search_queries` | list | |
| `last_accessed` | float | Unix timestamp |

---

## 4. CCR Store on Disk

**Default path:** `~/.headroom/ccr_store.db`

**Override env var:** `HEADROOM_CCR_SQLITE_PATH`

**Path computation:** `default_db_path()` at `headroom/cache/backends/sqlite.py:51–56`.

**Format:** SQLite, running in WAL mode for multi-worker safety.

**Schema** (`headroom/cache/backends/sqlite.py:36–44`):

```sql
CREATE TABLE IF NOT EXISTS ccr_entries (
    hash        TEXT PRIMARY KEY,
    entry_json  TEXT NOT NULL,   -- Full CompressionEntry serialized as JSON
    created_at  REAL NOT NULL,
    ttl         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ccr_expiry ON ccr_entries (created_at);
```

**TTL enforcement:** Entries expire after `created_at + ttl` seconds (default TTL: 1800 s = 30 min).

**Auto-purge:** Runs at most every 60 s (`_PURGE_INTERVAL` at `headroom/cache/backends/sqlite.py:48`).

**Backend class:** `SQLiteBackend` at `headroom/cache/backends/sqlite.py:59–276`.

---

## 5. Session Logs

**Exist:** Yes.

**Format:** JSONL — one JSON object per line.

**Default path:** `~/.headroom/logs/proxy.log` (or `{workspace}/logs/proxy.log`).

**Path computation:** `headroom/paths.py:266–269`.

**Configuration:**
- CLI flag: `--log-file PATH`
- Env var: `HEADROOM_LOG_FILE`
- Disabled entirely in `--stateless` mode.

**Fields per log entry** (from `headroom/cli/proxy.py:415–419`):

| Field | Notes |
|---|---|
| `timestamp` | ISO 8601 |
| `request_id` | UUID |
| `model` | Model string |
| `tokens_before` | |
| `tokens_after` | |
| `latency_ms` | |
| Full message content | Only logged if `--log-messages` flag is set |

**Log parsing:** `headroom/perf/analyzer.py:231–330` parses PERF records using regex patterns defined at lines 27–49.

---

## 6. `headroom wrap`

**What it does:** Starts the proxy as a subprocess, then launches the wrapped tool (e.g., `claude`) with environment variables pointing to the local proxy.

**Implementation:** `headroom/cli/wrap.py:3510–3522`.

**Environment variable injection:**

```python
# Standard mode (Claude Code / Anthropic SDK)
env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"

# Google Vertex AI mode
env["ANTHROPIC_VERTEX_BASE_URL"] = proxy_url

# Anthropic Foundry mode
env["ANTHROPIC_FOUNDRY_BASE_URL"] = _foundry_proxy_url(proxy_url)
```

**URL format:** `http://127.0.0.1:{port}` — default `http://127.0.0.1:8787`.

**Proxy startup:** `_ensure_proxy()` in `headroom/cli/wrap.py` ensures the proxy is running before the wrapped process is launched.

---

## 7. `headroom learn --verbosity`

**Command:** `headroom/cli/learn.py:131–311`.

**What it reads:**
- Project conversation history from coding agents (Claude Code, Codex, Gemini).
- Agent auto-detected or specified via `--agent` flag.
- Agent adapters registered in `headroom/learn/registry.py`.

**What it produces:**
- Recommendations written to `CLAUDE.local.md` (personal) or `CLAUDE.md` (team) by default.
- Output file overridable with `--target PATH`.
- Default is dry-run mode; pass `--apply` to actually write files.

**Output data structure:** `LearnResult` with a `content_by_file` dict mapping file paths to written content.

**`--verbosity` flag** (`headroom/cli/learn.py:117–124`):
- Learns the user's preferred response verbosity from behavioral signals in conversation history (interrupts, fast-skips, explicit feedback).
- Optional LLM judge via `--llm-judge` flag for override/augmentation.
- Output: verbosity preference appended to the target instruction file.

---

## 8. MCP Server

**Exists:** Yes.

**Implementation:** `headroom/ccr/mcp_server.py` (full file, ~650+ lines).

**Creation function:** `create_ccr_mcp_server(proxy_url)` at `headroom/ccr/mcp_server.py:958+`.

**Registered tools:**

### `headroom_compress` (lines 514–536)
- **Description:** Compress content on demand.
- **Input schema:**
  ```json
  { "content": { "type": "string" } }
  ```
- **Returns:** Compressed text + hash key for later retrieval.

### `headroom_retrieve` (lines 537–561)
- **Description:** Retrieve original uncompressed content by hash.
- **Input schema:**
  ```json
  {
    "hash":  { "type": "string" },
    "query": { "type": "string", "optional": true }
  }
  ```
- **Returns:** Original content, or filtered subset if `query` is provided.

### `headroom_stats` (lines 563–575)
- **Description:** Show compression statistics for the current session.
- **Input schema:** `{}` (no inputs)
- **Returns:** Formatted session summary — token savings, cost impact.

### `headroom_read` (lines 581–609) — optional
- **Gated by:** `HEADROOM_MCP_READ` env var / feature flag.
- **Description:** Read a file with smart caching — first read returns full content, subsequent reads return a cache marker (hash) so the model avoids re-reading unchanged files.
- **Input schema:**
  ```json
  {
    "file_path": { "type": "string" },
    "fresh":     { "type": "boolean", "optional": true }
  }
  ```

**Dispatch:** `call_tool()` at `headroom/ccr/mcp_server.py:613–655` routes to `_handle_compress()`, `_handle_retrieve()`, `_handle_stats()`, `_handle_read()`.

---

## Integration Summary

| Concern | Path | Notes |
|---|---|---|
| Proxy start | `headroom/cli/proxy.py:804` | Port 8787, Uvicorn, `HEADROOM_PORT` env |
| Perf metrics | `headroom/cli/perf.py:27` | JSONL input, JSON/CSV/text output |
| Dashboard | `headroom/proxy/server.py:2555` | `GET /dashboard` or `headroom dashboard` |
| Stats REST | `headroom/proxy/server.py:3143` | `GET /stats` → JSON |
| CCR Python API | `headroom/cache/compression_store.py:197` | `get_compression_store()` singleton |
| CCR disk store | `~/.headroom/ccr_store.db` | SQLite, WAL, 30-min TTL |
| Session logs | `~/.headroom/logs/proxy.log` | JSONL, `HEADROOM_LOG_FILE` override |
| `wrap` mechanism | `headroom/cli/wrap.py:3510` | Sets `ANTHROPIC_BASE_URL=http://127.0.0.1:8787` |
| `learn` output | `headroom/cli/learn.py:131` | Writes `CLAUDE.local.md` or `CLAUDE.md` |
| MCP server | `headroom/ccr/mcp_server.py:958` | 3 core tools + optional `headroom_read` |
