# DevKit — Slice 1: Foundation + `/scan`

## Overview

Slice 1 establishes the entire CLI scaffold and delivers the first working command: `/scan`. This is the highest-value, lowest-ambiguity slice — it reuses battle-tested logic from Sentinel (prompts, file classification, scoring) and ports it to Python. By end of slice, `devkit scan` runs on any local directory and produces security findings with severity, plain-English explanation, fix snippet, and blast radius.

**Do not proceed to Slice 2 until `devkit scan` runs end-to-end on a real codebase.**

---

## Goals

- Typer CLI scaffolded with shared config/state pattern
- `devkit init` initializes `~/.devkit/` directory structure
- `devkit config` manages API keys and settings
- `devkit scan` runs full security scan on local directory
- Semgrep subprocess wrapper with JSON parsing
- Claude enrichment (plain-English explanation, fix snippet, severity contextualization)
- Tree-sitter Tier 1 prioritization (graph-guided if knowledge-graph.json exists, heuristic fallback)
- Blast radius BFS over reverse call graph
- Three scan modes: `--mode web`, `--mode api`, `--mode ai`
- Output as human-readable text (default) or JSON (`--output json`)

---

## Success Criteria

- `devkit scan .` runs on a 3K–20K line Python/TypeScript codebase without error
- Produces at least one finding with: title, severity, file path, line number, plain-English description, fix snippet, blast radius (if graph available)
- `devkit scan . --mode api` runs OWASP API Top 10 focused rules
- `devkit scan . --output json` produces valid parseable JSON
- `devkit scan . --no-graph` falls back to heuristic Tier 1 when no knowledge-graph.json
- Total scan time under 60 seconds on a 10K line codebase

---

## Repository Decision

**Do NOT repurpose the Sentinel repo.** Sentinel is a live portfolio project with a frontend, Supabase backend, and Railway deployment. Keep it exactly as-is.

Create a fresh Python repo: `devkit-cli` (GitHub: `PranavCR01/devkit-cli`).

Port the following logic from Sentinel to Python (do not copy TypeScript — rewrite in Python using the same rules/patterns):
- `backend/src/services/claude/prompts.ts` → `devkit/core/scanner/prompts.py` (18 security rules system prompt)
- `backend/src/services/scanner/scorer.ts` → `devkit/core/scanner/scorer.py` (scoring math)
- `backend/src/services/claude/analyzer.ts` → `devkit/core/scanner/classifier.py` (Tier 1/2 file classification patterns)
- `backend/src/services/semgrep/runner.ts` + `parser.ts` → `devkit/core/scanner/semgrep_runner.py`

---

## File Structure

```
devkit-cli/
├── devkit/
│   ├── __init__.py
│   ├── cli.py                    # Typer app, command registration
│   ├── config.py                 # Config management (~/.devkit/config.json)
│   ├── state.py                  # SQLite state (~/.devkit/state.db)
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── scan.py               # /scan command logic
│   │   └── config_cmd.py         # /config command logic
│   └── core/
│       ├── __init__.py
│       └── scanner/
│           ├── __init__.py
│           ├── orchestrator.py   # Main scan pipeline
│           ├── semgrep_runner.py # Semgrep subprocess + JSON parsing
│           ├── classifier.py     # Tier 1/2 file classification
│           ├── claude_analyzer.py # Claude API calls for enrichment
│           ├── prompts.py        # System prompts (ported from Sentinel)
│           ├── scorer.py         # Security/quality/grade math
│           └── graph_guide.py    # Knowledge graph Tier 1 guide + blast radius
├── tests/
│   └── test_scan.py
├── pyproject.toml
├── README.md
└── CLAUDE.md                     # DevKit's own CLAUDE.md for Claude Code sessions
```

---

## Architecture

```
devkit scan [path] --mode web
        │
        ▼
  orchestrator.py
        │
        ├─── graph_guide.py ──────► load .understand-anything/knowledge-graph.json
        │         │                  if exists → graph-guided Tier 1
        │         │                  if not → heuristic Tier 1 fallback
        │         ▼
        ├─── classifier.py ────────► classify files into Tier 1 / Tier 2 / skip
        │
        ├─── [parallel via asyncio.gather]
        │         │
        │         ├── semgrep_runner.py ──► semgrep subprocess → JSON → parse findings
        │         │
        │         └── claude_analyzer.py ─► chunk Tier 1 files → Claude API → findings
        │
        ├─── merge + deduplicate findings (file_path:line_start key)
        │         Semgrep wins on conflicts, Claude enriches prose fields
        │
        ├─── scorer.py ────────────► security score, quality score, grade A-F
        │
        ├─── graph_guide.py ────────► blast radius: reverse BFS from each finding
        │
        └─── output formatter ──────► text (rich) or JSON
```

---

## Key Classes and Interfaces

### `devkit/config.py`

```python
from pathlib import Path
import json
from typing import Any

DEVKIT_DIR = Path.home() / ".devkit"
CONFIG_FILE = DEVKIT_DIR / "config.json"

class Config:
    """Manages ~/.devkit/config.json"""
    
    DEFAULTS = {
        "anthropic_api_key": None,
        "default_model": "claude-sonnet-4-6",
        "fast_model": "claude-haiku-4-5",
        "semgrep_timeout": 120,
        "scan_max_file_size_kb": 500,
        "scan_tier2_line_cap": 8000,
    }
    
    def get(self, key: str) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def validate(self) -> list[str]:  # returns list of missing required keys
```

### `devkit/state.py`

```python
import sqlite3
from pathlib import Path

STATE_DB = Path.home() / ".devkit" / "state.db"

class State:
    """Persistent SQLite state. Shared across all commands."""
    
    def init_db(self) -> None:
        """Create tables if not exist."""
        # Tables created in Slice 1: scan_history
        # Tables added in Slice 2: facts, episodes, workstreams
    
    def record_scan(self, scan_id: str, path: str, mode: str, findings_count: int) -> None: ...
    def get_scan_history(self, limit: int = 10) -> list[dict]: ...
```

### `devkit/core/scanner/orchestrator.py`

```python
from dataclasses import dataclass
from typing import Literal
import asyncio

@dataclass
class Finding:
    id: str                          # uuid
    category: Literal["security", "quality", "ai_antipattern"]
    severity: Literal["critical", "high", "medium", "low", "info"]
    title: str
    plain_english_desc: str          # plain-language explanation
    business_impact: str
    fix_snippet: str                 # copy-paste fix
    file_path: str
    line_start: int
    line_end: int | None
    owasp_ref: str | None
    cwe_ref: str | None
    source: Literal["semgrep", "claude"]
    blast_radius: list[str]          # file paths reachable from this finding

@dataclass 
class ScanResult:
    scan_id: str
    project_path: str
    mode: str
    security_score: int              # 0-100
    quality_score: int               # 0-100
    grade: Literal["A", "B", "C", "D", "F"]
    findings: list[Finding]
    scan_duration_seconds: float
    files_scanned: int
    lines_scanned: int
    graph_guided: bool               # whether knowledge graph was used

class ScanOrchestrator:
    async def run(
        self,
        path: str,
        mode: Literal["web", "api", "ai", "all"],
        output_format: Literal["text", "json"],
        use_graph: bool = True,
    ) -> ScanResult: ...
```

### `devkit/core/scanner/classifier.py`

Port exactly from Sentinel's `analyzer.ts:classifyFiles()`. 

```python
from pathlib import Path

# Tier 1 patterns — always scanned, prioritized
TIER1_PATTERNS = [
    "auth", "authentication", "login", "logout", "session",
    "api", "route", "router", "endpoint", "handler",
    "middleware", "interceptor",
    "db", "database", "supabase", "prisma", "orm", "query",
    "stripe", "payment", "billing", "webhook",
    "crypto", "hash", "encrypt", "decrypt", "token", "jwt", "secret",
    "admin", "permission", "role", "rbac",
    "upload", "file", "storage",
    "config", "env", "settings",
]

# Tier 2 — scanned up to line cap
TIER2_LINE_CAP = 8000

# Skip entirely
SKIP_PATTERNS = [
    "node_modules", ".git", "__pycache__", ".venv",
    "dist", "build", ".next", ".nuxt",
    "*.min.js", "*.min.css", "package-lock.json",
    "yarn.lock", "poetry.lock", "*.lock",
    "*.png", "*.jpg", "*.gif", "*.svg", "*.ico",
    "*.woff", "*.ttf", "*.eot",
]

class FileClassifier:
    def classify(
        self,
        project_path: str,
        graph: dict | None = None,   # knowledge-graph.json if available
    ) -> tuple[list[str], list[str]]:
        """Returns (tier1_files, tier2_files)"""
        # If graph available: use architectural layer + node type to identify tier 1
        # If no graph: use filename/path pattern matching
        ...
    
    def _graph_guided_tier1(self, files: list[str], graph: dict) -> list[str]:
        """Use knowledge graph node types and layers to identify high-priority files.
        
        High-priority node types: endpoint, service (auth-layer), schema, table
        High-priority edge types: reads_from, writes_to, validates (on auth/api nodes)
        """
        ...
    
    def _heuristic_tier1(self, files: list[str]) -> list[str]:
        """Fallback: filename/content pattern matching."""
        ...
```

### `devkit/core/scanner/semgrep_runner.py`

```python
import subprocess
import json
from pathlib import Path

# Rulesets by scan mode
RULESETS = {
    "web": ["p/owasp-top-ten", "p/javascript", "p/typescript", "p/react", "p/nodejs"],
    "api": ["p/owasp-top-ten", "p/javascript", "p/typescript", "p/nodejs"],
    "ai": ["p/owasp-top-ten", "p/secrets"],
    "all": ["p/owasp-top-ten", "p/secrets", "p/javascript", "p/typescript", "p/react", "p/nodejs"],
}

class SemgrepRunner:
    def run(self, path: str, mode: str, timeout: int = 120) -> list[dict]:
        """Run semgrep as subprocess, return parsed findings."""
        cmd = [
            "semgrep",
            "--config", self._get_configs(mode),
            "--json",
            "--quiet",
            "--timeout", str(timeout),
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        return self._parse_output(result.stdout)
    
    def _parse_output(self, json_str: str) -> list[dict]:
        """Parse semgrep JSON output into normalized finding dicts."""
        # Returns list of dicts with: check_id, path, start.line, end.line,
        # message, severity, metadata.owasp, metadata.cwe
        ...
    
    def check_installed(self) -> bool:
        """Check if semgrep is available on PATH."""
        ...
```

### `devkit/core/scanner/prompts.py`

Port the 18-rule system prompt from Sentinel's `backend/src/services/claude/prompts.ts`. Keep the same rules, rewrite as Python string.

```python
SECURITY_SYSTEM_PROMPT = """
You are a security code analyzer. Analyze the provided code and identify security vulnerabilities.

Return findings as a JSON array. Each finding must have:
- category: "security" | "quality" | "ai_antipattern"  
- severity: "critical" | "high" | "medium" | "low"
- title: short title
- plain_english_desc: explanation a non-technical person can understand
- business_impact: what could go wrong if exploited
- fix_snippet: exact code fix
- file_path: the file this was found in
- line_start: line number
- owasp_ref: OWASP category if applicable (e.g. "A01:2025")
- cwe_ref: CWE number if applicable (e.g. "CWE-639")

Focus on these 18 AI-specific anti-patterns [PORT FROM SENTINEL]:
1. Hardcoded secrets and API keys
2. Missing server-side authentication on API routes
3. Client-side-only authorization checks
...
[full 18 rules ported from Sentinel's SECURITY_SYSTEM_PROMPT]

Also detect standard OWASP Top 10 issues.
Return ONLY the JSON array. No preamble or explanation.
"""

# Mode-specific system prompt additions
WEB_ADDITIONS = """Additional focus for web applications:
- Broken Access Control (A01:2025) — check all data access for ownership verification
- Security Misconfiguration (A02:2025) — check headers, CORS, debug mode
- Injection (A05:2025) — SQL, NoSQL, command injection via string formatting
"""

API_ADDITIONS = """Additional focus for API security:
- BOLA (Broken Object Level Authorization) — check every endpoint for resource ownership
- BFLA (Broken Function Level Authorization) — check admin routes for role enforcement
- Excessive Data Exposure — check response shapes for over-fetching
- Mass Assignment — check if user-provided fields are directly used in DB writes
"""

AI_ADDITIONS = """Additional focus for AI/LLM applications:
- Prompt injection via user-controlled input reaching LLM system prompts
- API key exposure in client-side bundles or logs
- Unbounded consumption — no rate limits on LLM-calling endpoints
- System prompt leakage in responses
- Missing output sanitization before rendering LLM responses
"""
```

### `devkit/core/scanner/claude_analyzer.py`

```python
import anthropic
from .prompts import SECURITY_SYSTEM_PROMPT, WEB_ADDITIONS, API_ADDITIONS, AI_ADDITIONS
from .classifier import FileClassifier

CHUNK_SIZE_LINES = 2000   # max lines per Claude call

class ClaudeAnalyzer:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
    
    async def analyze(
        self,
        tier1_files: list[str],
        tier2_files: list[str],
        mode: str,
    ) -> list[dict]:
        """Analyze files with Claude. Returns raw findings list."""
        system_prompt = self._build_system_prompt(mode)
        chunks = self._build_chunks(tier1_files, tier2_files)
        
        # Use prompt caching on system prompt (stable across all chunks)
        all_findings = []
        for chunk in chunks:
            findings = await self._analyze_chunk(chunk, system_prompt)
            all_findings.extend(findings)
        
        return all_findings
    
    def _build_system_prompt(self, mode: str) -> str:
        base = SECURITY_SYSTEM_PROMPT
        additions = {"web": WEB_ADDITIONS, "api": API_ADDITIONS, "ai": AI_ADDITIONS}
        if mode in additions:
            return base + additions[mode]
        # "all" mode: include all additions
        return base + WEB_ADDITIONS + API_ADDITIONS + AI_ADDITIONS
    
    def _build_chunks(self, tier1_files: list[str], tier2_files: list[str]) -> list[str]:
        """Read files, chunk into ≤2000 line blocks. Tier 1 first."""
        ...
    
    async def _analyze_chunk(self, chunk: str, system_prompt: str) -> list[dict]:
        """Single Claude API call with prompt caching on system prompt."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},  # cache the stable system prompt
                }
            ],
            messages=[{"role": "user", "content": chunk}],
        )
        return self._parse_response(response.content[0].text)
    
    def _parse_response(self, text: str) -> list[dict]:
        """Parse Claude JSON response. Strip markdown fences if present."""
        ...
```

### `devkit/core/scanner/scorer.py`

Port exactly from Sentinel's `scorer.ts`. Same math.

```python
from typing import Literal

SEVERITY_DEDUCTIONS = {
    "critical": 25,
    "high": 10,
    "medium": 3,
    "low": 1,
    "info": 0,
}

def calculate_scores(findings: list[dict]) -> tuple[int, int, str]:
    """Returns (security_score, quality_score, grade)"""
    security_score = 100
    quality_score = 100
    
    for finding in findings:
        deduction = SEVERITY_DEDUCTIONS.get(finding["severity"], 0)
        if finding["category"] == "security":
            security_score = max(0, security_score - deduction)
        else:
            quality_score = max(0, quality_score - deduction)
    
    # Grade = security × 0.7 + quality × 0.3
    combined = security_score * 0.7 + quality_score * 0.3
    
    grade: Literal["A", "B", "C", "D", "F"]
    if combined >= 90: grade = "A"
    elif combined >= 80: grade = "B"
    elif combined >= 70: grade = "C"
    elif combined >= 60: grade = "D"
    else: grade = "F"
    
    return security_score, quality_score, grade
```

### `devkit/core/scanner/graph_guide.py`

```python
import json
from pathlib import Path
from collections import defaultdict, deque

class GraphGuide:
    """Loads Understand Anything knowledge graph and provides:
    1. Graph-guided Tier 1 file prioritization
    2. Blast radius tracing via reverse BFS
    """
    
    def __init__(self, project_path: str):
        graph_path = Path(project_path) / ".understand-anything" / "knowledge-graph.json"
        self.graph = None
        self.available = False
        
        if graph_path.exists():
            with open(graph_path) as f:
                self.graph = json.load(f)
            self._build_reverse_adj()
            self.available = True
    
    def _build_reverse_adj(self) -> None:
        """Build reverse adjacency list: target → [sources].
        Used for blast radius: given a vulnerable file, find all files that call/import it.
        """
        self.reverse_adj: dict[str, list[str]] = defaultdict(list)
        self.node_by_id: dict[str, dict] = {}
        
        for node in self.graph["nodes"]:
            self.node_by_id[node["id"]] = node
        
        for edge in self.graph["edges"]:
            # Edges that represent "X depends on Y" — reverse means Y's blast radius includes X
            if edge["type"] in ["calls", "imports", "depends_on", "reads_from", "writes_to"]:
                self.reverse_adj[edge["target"]].append(edge["source"])
    
    def get_tier1_files(self) -> list[str]:
        """Return files that are high-priority based on graph structure.
        
        Priority nodes: type=endpoint, type=service in auth layer, type=schema, type=table
        High inbound call count (many things depend on this file)
        """
        if not self.available:
            return []
        
        priority_files = []
        for node in self.graph["nodes"]:
            if node.get("type") in ["endpoint", "schema", "table"]:
                if fp := node.get("filePath"):
                    priority_files.append(fp)
            # Also include nodes with high inbound degree
            node_id = node["id"]
            inbound_count = len(self.reverse_adj.get(node_id, []))
            if inbound_count >= 5 and (fp := node.get("filePath")):
                priority_files.append(fp)
        
        return list(set(priority_files))
    
    def get_blast_radius(self, file_path: str) -> list[str]:
        """Backward BFS from file_path over reverse call graph.
        Returns all files that can reach (call/import/depend on) the vulnerable file.
        
        Algorithm: O(V+E) BFS on reverse adjacency list.
        """
        if not self.available:
            return []
        
        # Find node IDs for this file path
        start_ids = [
            node["id"] for node in self.graph["nodes"]
            if node.get("filePath") == file_path
        ]
        
        visited = set()
        queue = deque(start_ids)
        
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            
            for parent in self.reverse_adj.get(current, []):
                if parent not in visited:
                    queue.append(parent)
        
        # Convert node IDs back to file paths, deduplicate, exclude the source file
        blast_files = []
        for node_id in visited:
            if node := self.node_by_id.get(node_id):
                if fp := node.get("filePath"):
                    if fp != file_path:
                        blast_files.append(fp)
        
        return list(set(blast_files))
```

---

## CLI Commands

### `devkit init`
Initializes `~/.devkit/` directory structure. Must be run before other commands.

```
devkit init
```

Creates:
- `~/.devkit/config.json` with defaults
- `~/.devkit/state.db` with empty tables
- `~/.devkit/memory.db` (placeholder for Slice 2)
- `~/.devkit/graphs/` directory
- `~/.devkit/blueprints/` directory (placeholder for Slice 5)

### `devkit config`

```
devkit config set ANTHROPIC_API_KEY sk-ant-...
devkit config set default_model claude-sonnet-4-6
devkit config set fast_model claude-haiku-4-5
devkit config get ANTHROPIC_API_KEY
devkit config list
```

### `devkit scan`

```
devkit scan [PATH]                    # Scan PATH (default: current directory)
devkit scan [PATH] --mode web         # OWASP Top 10 web focus
devkit scan [PATH] --mode api         # OWASP API Top 10 focus
devkit scan [PATH] --mode ai          # OWASP LLM Top 10 focus
devkit scan [PATH] --mode all         # All three modes (default)
devkit scan [PATH] --output json      # JSON output to stdout
devkit scan [PATH] --output text      # Human-readable (default)
devkit scan [PATH] --no-graph         # Skip graph-guided Tier 1
devkit scan [PATH] --severity high    # Show only high/critical findings
devkit scan [PATH] --no-semgrep       # Claude only (skip Semgrep)
devkit scan [PATH] --no-claude        # Semgrep only (skip Claude)
devkit scan [PATH] --save             # Save findings to state.db
```

**Example output (text mode):**

```
DevKit Security Scan — /path/to/project
Mode: web | Files: 47 | Lines: 12,340
Graph-guided: YES (.understand-anything/knowledge-graph.json found)

Grade: C  |  Security: 74  |  Quality: 88

CRITICAL (2)
─────────────────────────────────────────────────
[1] Missing Row Level Security — src/lib/supabase.ts:23
    Anyone can read any user's data by changing the user ID.
    Fix: Add RLS policy: ALTER TABLE users ENABLE ROW LEVEL SECURITY;
    Blast radius: 4 files (api/users.ts, api/profile.ts, hooks/useUser.ts, pages/dashboard.tsx)
    OWASP: A01:2025 | CWE: 863 | Source: claude

[2] Hardcoded API Key — src/config/index.ts:8
    API key exposed in source code, visible to anyone with repo access.
    Fix: Move to environment variable: const key = process.env.STRIPE_KEY;
    Source: semgrep | Rule: p/secrets.generic-api-key

HIGH (3)
...
```

---

## Dependencies

Add to `pyproject.toml`:

```toml
[project]
name = "devkit-cli"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "typer>=0.12.0",
    "rich>=13.0.0",          # terminal formatting
    "anthropic>=0.40.0",     # Claude API
    "semgrep>=1.90.0",       # security scanning
    "httpx>=0.27.0",         # async HTTP
]

[project.optional-dependencies]
memory = [
    "sentence-transformers>=3.0.0",   # local embeddings
    "sqlite-vec>=0.1.0",              # vector search in SQLite
]
graph = [
    "tree-sitter>=0.23.0",            # code parsing
    "networkx>=3.0",                  # graph operations
    "numpy>=1.26.0",                  # PageRank
]
eval = [
    "llmlingua>=0.2.0",               # prompt compression (optional)
    "datasketch>=1.6.0",              # MinHash for near-dedup
    "xxhash>=3.4.0",                  # fast hashing
]

[project.scripts]
devkit = "devkit.cli:app"
```

---

## Open Decisions to Resolve During Build

1. **LiteLLM vs direct Anthropic SDK** — Currently using Anthropic SDK directly. LiteLLM adds model routing flexibility for Slice 6. Decision: use Anthropic SDK in Slice 1, wrap in a thin `llm.py` abstraction that can be swapped later.

2. **Semgrep ruleset for `--mode all`** — Running all 6 rulesets simultaneously may be slow. Measure actual time on a 10K line codebase. If >30s, parallelize ruleset runs.

3. **Chunk size** — 2000 lines is inherited from Sentinel. May need tuning for token limits with new Claude models. Measure tokens per chunk and adjust.

4. **Blast radius depth** — BFS has no depth limit currently. On highly interconnected codebases this could return the entire repo. Add a `max_depth` parameter (default 3) if blast radius is too broad.

5. **Semgrep on Windows** — Known weak platform. Test on Windows during build. If pip wheels fail, document Docker fallback.

---

## CLAUDE.md for DevKit Repo

Create this at the repo root to guide Claude Code sessions:

```markdown
# DevKit CLI

Python CLI tool for developer context, security scanning, memory management, and token optimization.
Transforms into a Claude Code skill in Slice 7.

## Stack
- Python 3.10+ (3.12+ for memory extras)
- Typer (CLI framework)
- Anthropic SDK (direct, not LiteLLM in v1)
- SQLite for state + memory
- Semgrep (subprocess)

## Commands
- `devkit scan` — security scanning
- `devkit memory` — temporal memory (Slice 2)
- `devkit search` — cross-project search (Slice 2)
- `devkit context` — context assembly (Slice 4)
- `devkit fork` — feature forking (Slice 5)
- `devkit eval` — token optimization (Slice 6)

## Development
pip install -e ".[memory,graph,eval]"
python -m devkit.cli --help

## Key files
- devkit/cli.py — entry point, command registration
- devkit/core/scanner/orchestrator.py — scan pipeline
- devkit/core/scanner/prompts.py — 18 security rules (ported from Sentinel)
- ~/.devkit/ — all runtime state (config, state.db, memory.db)

## Rules
- Always run /plan before writing any code
- Never modify ~/.devkit/ schema without migrating existing data
- All Claude API calls must use cache_control on stable system prompts
- Semgrep subprocess must have timeout (default 120s)
```

---

## What to Port from Sentinel

Run this prompt in the Sentinel Claude Code chat:

```
Extract the following from this codebase and output as Python-compatible strings/logic 
(not TypeScript — convert the logic):

1. The full SECURITY_SYSTEM_PROMPT from backend/src/services/claude/prompts.ts — 
   convert to a Python string, keep all 18 rules exactly

2. The Tier 1 file classification patterns from backend/src/services/claude/analyzer.ts 
   classifyFiles() — list all filename/path patterns that trigger Tier 1

3. The scoring math from backend/src/services/scanner/scorer.ts — severity deduction 
   values and grade thresholds

4. The Semgrep JSON output field names that you parse in 
   backend/src/services/semgrep/parser.ts — what fields does semgrep JSON actually 
   return that you use?

Output as four clearly labeled sections.
```
