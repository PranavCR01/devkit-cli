# DevKit — Slice 2: `/memory` + `/search`

## Overview

Slice 2 builds the temporal memory layer and cross-project search. This is the thesis validation slice — it proves that developer decisions, patterns, and architectural choices can be stored, retrieved semantically, and cited across projects. Uses a lightweight custom SQLite + sqlite-vec store (no server, no Docker). Graphiti is deferred to v2.

**Do not proceed to Slice 3 until `/memory save`, `/memory search`, and session snapshot injection are all working.**

---

## Goals

- `MemoryStore` interface with SQLite backend (swappable in v2 for Graphiti)
- Local embeddings via `sentence-transformers/all-MiniLM-L6-v2` (22MB, CPU-only)
- Store developer facts: decisions, patterns, bugs, architecture, preferences
- Contradiction detection: newer fact supersedes older (timestamp + semantic similarity)
- Named workstreams: save/restore context per feature branch
- Cross-project semantic search with RRF fusion
- Cited output: project, date, fact type alongside every retrieved result
- Session snapshot injection via Claude Code hook (echo stdout)

---

## Success Criteria

- `devkit memory save "decided to use pgvector for RLS scoping" --type decision` stores fact
- `devkit search "RLS pattern"` returns cited results within 2 seconds
- Contradiction: saving "switched from pgvector to SQLite" auto-invalidates the previous pgvector decision
- `devkit memory switch auth-feature` saves current context, loads auth-feature workstream
- Session hook: `.devkit/hooks/session-start.sh` injects snapshot into Claude Code session
- Search works cross-project (results from multiple projects returned with source attribution)
- All operations work offline with no internet beyond initial model download

---

## File Structure Additions

```
devkit/
└── core/
    ├── memory/
    │   ├── __init__.py
    │   ├── store.py          # MemoryStore abstract interface
    │   ├── sqlite_backend.py # v1 SQLite + sqlite-vec implementation
    │   └── embedder.py       # sentence-transformers wrapper
    └── search/
        ├── __init__.py
        ├── rrf.py            # Reciprocal Rank Fusion
        └── searcher.py       # Cross-source search orchestrator

~/.devkit/
├── memory.db                 # SQLite — facts, episodes, workstreams
├── embeddings/               # sqlite-vec database files
│   └── facts.vec
└── hooks/
    └── session-start.sh      # Claude Code SessionStart hook
```

---

## Database Schema

### `~/.devkit/memory.db`

```sql
-- Core facts table
CREATE TABLE IF NOT EXISTS facts (
    id          TEXT PRIMARY KEY,           -- UUID
    project     TEXT NOT NULL,              -- project name/path identifier
    workstream  TEXT,                       -- named workstream (nullable = global)
    content     TEXT NOT NULL,              -- the fact itself
    fact_type   TEXT NOT NULL,              -- decision|pattern|bug|architecture|preference
    valid_at    TEXT NOT NULL,              -- ISO 8601 — when this became true
    invalid_at  TEXT,                       -- ISO 8601 — when superseded (NULL = current)
    created_at  TEXT NOT NULL,             
    source      TEXT DEFAULT 'manual',      -- manual|scan|auto
    confidence  REAL DEFAULT 1.0           -- 0.0-1.0
);

-- Contradiction log
CREATE TABLE IF NOT EXISTS contradictions (
    id              TEXT PRIMARY KEY,
    new_fact_id     TEXT REFERENCES facts(id),
    old_fact_id     TEXT REFERENCES facts(id),
    similarity      REAL,                   -- cosine similarity that triggered detection
    detected_at     TEXT NOT NULL
);

-- Raw episodes (source material for facts)
CREATE TABLE IF NOT EXISTS episodes (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    fact_ids    TEXT                        -- JSON array of derived fact IDs
);

-- Workstream state snapshots
CREATE TABLE IF NOT EXISTS workstreams (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    name        TEXT NOT NULL,              -- "auth-feature", "payment-flow"
    snapshot    TEXT NOT NULL,              -- JSON: {active_files, decisions, context}
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(project, name)
);

-- Session snapshot cache (what gets injected at session start)
CREATE TABLE IF NOT EXISTS session_snapshots (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    content     TEXT NOT NULL,              -- rendered snapshot text
    token_count INT,
    created_at  TEXT NOT NULL,
    UNIQUE(project)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_facts_project ON facts(project);
CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_facts_valid ON facts(valid_at, invalid_at);
CREATE INDEX IF NOT EXISTS idx_facts_workstream ON facts(workstream);
```

---

## Key Classes and Interfaces

### `devkit/core/memory/store.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

FactType = Literal["decision", "pattern", "bug", "architecture", "preference", "vulnerability_pattern"]

@dataclass
class Fact:
    id: str
    project: str
    workstream: str | None
    content: str
    fact_type: FactType
    valid_at: str          # ISO 8601
    invalid_at: str | None # None = currently valid
    created_at: str
    source: str            # "manual" | "scan" | "auto"
    confidence: float

@dataclass
class SearchResult:
    fact: Fact
    score: float           # RRF combined score
    match_type: str        # "semantic" | "keyword" | "graph"

class MemoryStore(ABC):
    """Abstract interface. v1 = SQLiteBackend. v2 = GraphitiBackend."""
    
    @abstractmethod
    def save(
        self,
        content: str,
        fact_type: FactType,
        project: str,
        workstream: str | None = None,
        source: str = "manual",
    ) -> Fact:
        """Save a fact. Automatically detects and handles contradictions."""
    
    @abstractmethod
    def search(
        self,
        query: str,
        projects: list[str] | None = None,  # None = all projects
        fact_types: list[FactType] | None = None,
        limit: int = 10,
        include_invalid: bool = False,
    ) -> list[SearchResult]:
        """Hybrid semantic + keyword search across all stored facts."""
    
    @abstractmethod
    def contradict(self, fact_id: str, reason: str | None = None) -> None:
        """Explicitly invalidate a fact (e.g. developer dismissed a finding)."""
    
    @abstractmethod
    def get_snapshot(self, project: str, token_cap: int = 2000) -> str:
        """Get session snapshot for injection. Returns capped text."""
    
    @abstractmethod
    def save_workstream(self, name: str, project: str, context: dict) -> None:
        """Save current workstream state."""
    
    @abstractmethod
    def load_workstream(self, name: str, project: str) -> dict | None:
        """Load a saved workstream state."""
    
    @abstractmethod
    def list_projects(self) -> list[str]:
        """List all projects with stored facts."""
```

### `devkit/core/memory/sqlite_backend.py`

```python
import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .store import MemoryStore, Fact, SearchResult, FactType
from .embedder import Embedder

CONTRADICTION_THRESHOLD = 0.85  # cosine similarity above this = contradiction

class SQLiteBackend(MemoryStore):
    """v1 memory backend. SQLite for facts, sqlite-vec for embeddings.
    
    Contradiction detection:
    When saving a new fact, embed it and compare against all CURRENT facts
    (invalid_at IS NULL) of the same fact_type in the same project.
    If cosine similarity > CONTRADICTION_THRESHOLD, set invalid_at on old fact.
    
    This is simpler than Graphiti's LLM-based contradiction but sufficient for v1.
    The threshold is empirically tuned — start at 0.85 and adjust.
    """
    
    def __init__(self, db_path: Path, embedder: "Embedder"):
        self.db_path = db_path
        self.embedder = embedder
        self._init_db()
    
    def save(self, content: str, fact_type: FactType, project: str, 
             workstream: str | None = None, source: str = "manual") -> Fact:
        
        fact_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        embedding = self.embedder.embed(content)
        
        # Check for contradictions before saving
        contradicted_ids = self._find_contradictions(
            embedding, fact_type, project, threshold=CONTRADICTION_THRESHOLD
        )
        
        with sqlite3.connect(self.db_path) as conn:
            # Invalidate contradicted facts
            for old_id in contradicted_ids:
                conn.execute(
                    "UPDATE facts SET invalid_at = ? WHERE id = ?",
                    (now, old_id)
                )
                conn.execute(
                    """INSERT INTO contradictions (id, new_fact_id, old_fact_id, detected_at)
                       VALUES (?, ?, ?, ?)""",
                    (str(uuid.uuid4()), fact_id, old_id, now)
                )
            
            # Save new fact
            conn.execute(
                """INSERT INTO facts (id, project, workstream, content, fact_type, 
                   valid_at, invalid_at, created_at, source, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 1.0)""",
                (fact_id, project, workstream, content, fact_type, now, now, source)
            )
        
        # Save embedding to sqlite-vec
        self._save_embedding(fact_id, embedding)
        
        return Fact(id=fact_id, project=project, workstream=workstream,
                    content=content, fact_type=fact_type, valid_at=now,
                    invalid_at=None, created_at=now, source=source, confidence=1.0)
    
    def search(self, query: str, projects=None, fact_types=None, 
               limit=10, include_invalid=False) -> list[SearchResult]:
        """
        Three-pass search with RRF fusion:
        1. Semantic: embed query, cosine similarity over sqlite-vec
        2. Keyword: FTS5 BM25 over facts.content
        3. RRF merge: combine both ranked lists
        """
        query_embedding = self.embedder.embed(query)
        
        semantic_results = self._semantic_search(query_embedding, projects, fact_types, 
                                                  include_invalid, limit * 2)
        keyword_results = self._keyword_search(query, projects, fact_types,
                                               include_invalid, limit * 2)
        
        merged = rrf_merge([semantic_results, keyword_results], limit=limit)
        return merged
    
    def _find_contradictions(self, embedding: list[float], fact_type: str, 
                              project: str, threshold: float) -> list[str]:
        """Find existing valid facts that are semantically similar enough to be contradictions."""
        # Get all valid facts of same type in same project
        # Compare embeddings using cosine similarity
        # Return IDs of those above threshold
        ...
    
    def get_snapshot(self, project: str, token_cap: int = 2000) -> str:
        """Build session snapshot for injection.
        
        Priority order for snapshot content:
        1. Current workstream context (if active)
        2. Most recent decisions (last 7 days)
        3. High-confidence patterns for this project
        4. Active architecture facts
        
        Truncate whole items when approaching token_cap.
        """
        ...
```

### `devkit/core/memory/embedder.py`

```python
from functools import lru_cache
from pathlib import Path
import numpy as np

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

class Embedder:
    """Local sentence-transformers embedder. Loaded once, cached.
    
    Model: all-MiniLM-L6-v2
    - Size: ~22MB
    - Dims: 384
    - CPU inference: ~5-20ms per sentence
    - No internet after first download
    """
    
    _instance = None
    _model = None
    
    @classmethod
    def get_instance(cls) -> "Embedder":
        """Singleton pattern — load model once per process."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        # Lazy import to avoid slow startup for commands that don't need embeddings
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(MODEL_NAME)
    
    def embed(self, text: str) -> list[float]:
        """Embed a single text. Returns 384-dim vector."""
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embed. More efficient than calling embed() in a loop."""
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
        return vectors.tolist()
    
    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity between two normalized vectors."""
        return float(np.dot(a, b))  # already normalized, dot product = cosine sim
```

### `devkit/core/search/rrf.py`

```python
from collections import defaultdict

def rrf_merge(
    ranked_lists: list[list[tuple[str, float]]],  # list of (id, score) lists
    k: int = 60,      # standard RRF constant (NOT Graphiti's k=1)
    limit: int = 10,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion.
    
    Uses k=60 (standard, NOT Graphiti's non-standard k=1).
    k=60 provides more balanced fusion across multiple ranked lists.
    
    Formula: score(id) = Σ 1/(k + rank_in_list)
    """
    scores: dict[str, float] = defaultdict(float)
    
    for ranked_list in ranked_lists:
        for rank, (item_id, _) in enumerate(ranked_list, start=1):
            scores[item_id] += 1.0 / (k + rank)
    
    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_items[:limit]
```

---

## Session Snapshot Injection

### Hook file: `~/.devkit/hooks/session-start.sh`

```bash
#!/bin/bash
# DevKit session start hook
# Injected into Claude Code via SessionStart hook mechanism
# Outputs instructions that Claude reads at session start

DEVKIT_DIR="$HOME/.devkit"
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PROJECT_NAME=$(basename "$PROJECT_ROOT")

# Only inject if devkit is initialized and project has memory
if [ -f "$DEVKIT_DIR/state.db" ]; then
    SNAPSHOT=$(python3 -m devkit.commands.memory snapshot --project "$PROJECT_NAME" --format hook 2>/dev/null)
    if [ -n "$SNAPSHOT" ]; then
        echo "$SNAPSHOT"
    fi
fi
```

Register this in Claude Code's `~/.claude/settings.json`:

```json
{
    "hooks": {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "~/.devkit/hooks/session-start.sh"
                    }
                ]
            }
        ]
    }
}
```

### Snapshot format (what gets echoed):

```
<devkit-memory>
Project: swagath-central | Workstream: payment-feature | Updated: 2025-06-10

RECENT DECISIONS:
- [2025-06-10] Used pgvector with row-level scoping for user isolation (decision)
- [2025-06-09] B.Cash formula: Total Sales − totalUpi (decision)
- [2025-06-08] Show renumbering uses two-pass via temp values to avoid deadlock (pattern)

ACTIVE PATTERNS:
- Supabase upsert requires UNIQUE constraint or it silently fails (bug, confidence: 1.0)
- SSE streaming: use stable row IDs as Map keys to prevent deduplication issues (bug)

ARCHITECTURE:
- Theatre IDs: Sandhya=628dba7b, Manasa=8c30caa5 (preference)
</devkit-memory>

Above is your DevKit memory context for this project. Apply it silently.
```

Token cap: 2000 tokens. Truncate whole items if over cap.

---

## CLI Commands

### `devkit memory`

```
devkit memory save <content>                   # Save a fact (interactive type selection)
devkit memory save <content> --type decision   # Save with explicit type
devkit memory save <content> --type pattern    # Types: decision|pattern|bug|architecture|preference
devkit memory save <content> --workstream auth # Tag to workstream
devkit memory save <content> --project myapp  # Override project (default: git root name)

devkit memory list                             # List recent facts (current project)
devkit memory list --project all               # All projects
devkit memory list --type decision             # Filter by type
devkit memory list --workstream auth           # Filter by workstream
devkit memory list --include-invalid           # Show superseded facts too

devkit memory contradict <fact-id>             # Mark fact as superseded
devkit memory contradict <fact-id> --reason "switched to SQLite"

devkit memory switch <workstream>              # Save current + load named workstream
devkit memory switch main                      # Return to main context
devkit memory workstreams                      # List all workstreams for current project

devkit memory snapshot                         # Show what would be injected at session start
devkit memory snapshot --token-cap 3000        # Custom token cap
```

### `devkit search`

```
devkit search <query>                          # Cross-project semantic + keyword search
devkit search <query> --project swagath        # Scope to one project
devkit search <query> --type decision          # Filter by fact type
devkit search <query> --limit 5                # Number of results (default: 10)
devkit search <query> --output json            # JSON output
devkit search <query> --include-invalid        # Include superseded facts
```

**Example output:**

```
DevKit Search: "RLS pattern"
Found 4 results across 2 projects

[1] swagath-central • decision • 2025-06-10 • score: 0.94
    "Used pgvector with row-level scoping — each user's data filtered by user_id claim from JWT"
    
[2] sentinel • pattern • 2025-04-28 • score: 0.87
    "Supabase RLS policies must be enabled explicitly with ALTER TABLE ... ENABLE ROW LEVEL SECURITY"

[3] swagath-central • bug • 2025-05-15 • score: 0.71  [SUPERSEDED]
    "Missing RLS on item_cost_history table — fixed by adding policy"

[4] cia-project • architecture • 2025-05-02 • score: 0.68
    "PostgreSQL pgvector extension for embedding storage with RLS for multi-tenant isolation"
```

---

## Dependencies Added in Slice 2

Under `devkit[memory]` extra:

```toml
memory = [
    "sentence-transformers>=3.0.0",
    "sqlite-vec>=0.1.0",
    "numpy>=1.26.0",
]
```

Install: `pip install -e ".[memory]"`

---

## Open Decisions

1. **Contradiction threshold** — 0.85 is a starting point. Too low = valid facts get invalidated; too high = actual contradictions missed. Tune empirically on real projects. Log all contradiction detections to `contradictions` table so you can review false positives.

2. **Snapshot token cap** — 2000 tokens is conservative. Test against Claude Code's actual behavior with the hook. May want to increase to 3000-4000 if injection works reliably.

3. **Hook injection mechanism** — echo vs additionalContext JSON. Start with echo (simpler, matches Understand Anything pattern). Test if Claude reads it reliably. If not, implement the JSON `additionalContext` approach from Claude Code hooks docs.

4. **Model download on first use** — `all-MiniLM-L6-v2` downloads ~22MB on first embed call. Show a progress indicator. Consider caching the model path in config.

5. **sqlite-vec availability** — Check if sqlite-vec wheel is available for Python 3.10+ on all platforms. If not, fall back to pure numpy cosine similarity on stored embeddings (slower but no native deps).

---

## v2 Upgrade Path (Graphiti)

When upgrading to Graphiti in v2:
1. Create `devkit/core/memory/graphiti_backend.py` implementing the same `MemoryStore` interface
2. Export all facts from SQLite using `devkit memory export --format graphiti`
3. Import into Graphiti using `add_episode` with `source=EpisodeType.text`
4. For known vulnerability patterns from `/scan`, use `add_triplet` (bypasses 10-step pipeline)
5. Swap backend in config: `devkit config set memory_backend graphiti`

The `MemoryStore` interface is the stability contract — no command code changes when swapping backends.
