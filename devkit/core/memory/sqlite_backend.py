from __future__ import annotations

import json
import sqlite3
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from devkit.core.memory.store import Fact, FactType, MemoryStore, SearchResult
from devkit.core.search.rrf import rrf_merge

MEMORY_DB = Path.home() / ".devkit" / "memory.db"
CONTRADICTION_THRESHOLD = 0.85
EMBEDDING_DIM = 384

_VEC_STRUCT = struct.Struct(f"{EMBEDDING_DIM}f")


def _pack(vec: list[float]) -> bytes:
    return _VEC_STRUCT.pack(*vec)


def _unpack(data: bytes) -> list[float]:
    return list(_VEC_STRUCT.unpack(data))


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError, sqlite3.OperationalError) as exc:
        raise RuntimeError(
            "sqlite-vec not available. Run: pip install -e '.[memory]'"
        ) from exc


class SQLiteBackend(MemoryStore):
    """v1 memory backend: SQLite (facts/FTS5) + sqlite-vec (KNN embeddings).

    Contradiction detection: cosine similarity >= 0.85 between a new fact and
    any existing valid fact of the same type in the same project triggers
    invalidation of the old fact.
    """

    def __init__(self, db_path: Path = MEMORY_DB, embedder=None) -> None:
        self.db_path = db_path
        self._embedder = embedder  # pre-warmed Embedder instance, or None for lazy load
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_embedder(self):
        if self._embedder is not None:
            return self._embedder
        from devkit.core.memory.embedder import Embedder
        return Embedder.get_instance()

    # ── MemoryStore interface ─────────────────────────────────────────────────

    def save(
        self,
        content: str,
        fact_type: FactType,
        project: str,
        workstream: str | None = None,
        source: str = "manual",
    ) -> Fact:
        embedder = self._get_embedder()
        embedding = embedder.embed(content)

        fact_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        contradicted = self._find_contradictions(embedding, fact_type, project)

        with self._connect() as conn:
            for old_id in contradicted:
                conn.execute(
                    "UPDATE facts SET invalid_at = ? WHERE id = ?", (now, old_id)
                )
                conn.execute(
                    """INSERT INTO contradictions
                           (id, new_fact_id, old_fact_id, similarity, detected_at)
                           VALUES (?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), fact_id, old_id, CONTRADICTION_THRESHOLD, now),
                )

            conn.execute(
                """INSERT INTO facts
                       (id, project, workstream, content, fact_type,
                        valid_at, invalid_at, created_at, source, confidence, embedding)
                       VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 1.0, ?)""",
                (
                    fact_id, project, workstream, content, fact_type,
                    now, now, source, _pack(embedding),
                ),
            )
            conn.execute(
                "INSERT INTO facts_fts (fact_id, content) VALUES (?, ?)",
                (fact_id, content),
            )

            cursor = conn.execute(
                "INSERT INTO fact_vec_map (fact_id) VALUES (?)", (fact_id,)
            )
            rowid = cursor.lastrowid
            conn.execute(
                "INSERT INTO fact_vec (rowid, embedding) VALUES (?, ?)",
                (rowid, _pack(embedding)),
            )

        return Fact(
            id=fact_id, project=project, workstream=workstream,
            content=content, fact_type=fact_type, valid_at=now,
            invalid_at=None, created_at=now, source=source, confidence=1.0,
        )

    def search(
        self,
        query: str,
        projects: list[str] | None = None,
        fact_types: list[FactType] | None = None,
        limit: int = 10,
        include_invalid: bool = False,
    ) -> list[SearchResult]:
        embedding = self._get_embedder().embed(query)

        semantic = self._semantic_search(embedding, projects, fact_types, include_invalid, limit * 2)
        keyword = self._keyword_search(query, projects, fact_types, include_invalid, limit * 2)

        merged = rrf_merge([semantic, keyword], limit=limit)

        sem_ids = {fid for fid, _ in semantic}
        kw_ids = {fid for fid, _ in keyword}

        results: list[SearchResult] = []
        for fact_id, score in merged:
            fact = self._get_fact(fact_id)
            if fact is None:
                continue
            if fact_id in sem_ids and fact_id in kw_ids:
                match_type = "hybrid"
            elif fact_id in sem_ids:
                match_type = "semantic"
            else:
                match_type = "keyword"
            results.append(SearchResult(fact=fact, score=score, match_type=match_type))

        return results

    def contradict(self, fact_id: str, reason: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE facts SET invalid_at = ? WHERE id = ?", (now, fact_id)
            )

    def get_snapshot(self, project: str, token_cap: int = 2000) -> str:
        now = datetime.now(timezone.utc)
        sections: list[str] = []

        with self._connect() as conn:
            ws = conn.execute(
                "SELECT name FROM workstreams WHERE project = ? ORDER BY updated_at DESC LIMIT 1",
                (project,),
            ).fetchone()
            ws_name = ws["name"] if ws else None

            decisions = conn.execute(
                """SELECT content, valid_at FROM facts
                   WHERE project = ? AND fact_type = 'decision' AND invalid_at IS NULL
                   ORDER BY valid_at DESC LIMIT 10""",
                (project,),
            ).fetchall()

            patterns = conn.execute(
                """SELECT content, fact_type, confidence FROM facts
                   WHERE project = ? AND fact_type IN ('pattern', 'bug')
                     AND invalid_at IS NULL
                   ORDER BY confidence DESC, valid_at DESC LIMIT 5""",
                (project,),
            ).fetchall()

            arch = conn.execute(
                """SELECT content FROM facts
                   WHERE project = ? AND fact_type = 'architecture' AND invalid_at IS NULL
                   ORDER BY valid_at DESC LIMIT 5""",
                (project,),
            ).fetchall()

        header = f"Project: {project}"
        if ws_name:
            header += f" | Workstream: {ws_name}"
        header += f" | Updated: {now.strftime('%Y-%m-%d')}"
        sections.append(header)

        if decisions:
            lines = ["\nRECENT DECISIONS:"]
            for row in decisions:
                lines.append(f"- [{row['valid_at'][:10]}] {row['content']}")
            sections.append("\n".join(lines))

        if patterns:
            lines = ["\nACTIVE PATTERNS:"]
            for row in patterns:
                conf = f", confidence: {row['confidence']:.1f}" if row["confidence"] < 1.0 else ""
                lines.append(f"- {row['content']} ({row['fact_type']}{conf})")
            sections.append("\n".join(lines))

        if arch:
            lines = ["\nARCHITECTURE:"]
            for row in arch:
                lines.append(f"- {row['content']}")
            sections.append("\n".join(lines))

        body = "\n".join(sections)

        # Rough token cap: ~4 chars per token; trim on whole-line boundaries
        char_cap = token_cap * 4
        if len(body) > char_cap:
            body = body[:char_cap].rsplit("\n", 1)[0]

        return (
            "<devkit-memory>\n"
            + body
            + "\n</devkit-memory>\n\n"
            + "Above is your DevKit memory context for this project. Apply it silently."
        )

    def save_workstream(self, name: str, project: str, context: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM workstreams WHERE project = ? AND name = ?",
                (project, name),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE workstreams SET snapshot = ?, updated_at = ? WHERE project = ? AND name = ?",
                    (json.dumps(context), now, project, name),
                )
            else:
                conn.execute(
                    """INSERT INTO workstreams
                           (id, project, name, snapshot, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), project, name, json.dumps(context), now, now),
                )

    def load_workstream(self, name: str, project: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot FROM workstreams WHERE project = ? AND name = ?",
                (project, name),
            ).fetchone()
        return json.loads(row["snapshot"]) if row else None

    def list_projects(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT project FROM facts ORDER BY project"
            ).fetchall()
        return [row["project"] for row in rows]

    # ── additional helpers exposed to CLI ─────────────────────────────────────

    def list_facts(
        self,
        project: str | None = None,
        fact_type: str | None = None,
        workstream: str | None = None,
        include_invalid: bool = False,
        limit: int = 50,
    ) -> list[Fact]:
        conditions: list[str] = []
        params: list = []
        if project and project != "all":
            conditions.append("project = ?")
            params.append(project)
        if fact_type:
            conditions.append("fact_type = ?")
            params.append(fact_type)
        if workstream:
            conditions.append("workstream = ?")
            params.append(workstream)
        if not include_invalid:
            conditions.append("invalid_at IS NULL")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM facts {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [_row_to_fact(row) for row in rows]

    def list_workstreams(self, project: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, updated_at FROM workstreams WHERE project = ? ORDER BY updated_at DESC",
                (project,),
            ).fetchall()
        return [{"name": row["name"], "updated_at": row["updated_at"]} for row in rows]

    def get_fact(self, fact_id: str) -> Fact | None:
        return self._get_fact(fact_id)

    # ── private ───────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        _load_sqlite_vec(conn)
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    id          TEXT PRIMARY KEY,
                    project     TEXT NOT NULL,
                    workstream  TEXT,
                    content     TEXT NOT NULL,
                    fact_type   TEXT NOT NULL,
                    valid_at    TEXT NOT NULL,
                    invalid_at  TEXT,
                    created_at  TEXT NOT NULL,
                    source      TEXT DEFAULT 'manual',
                    confidence  REAL DEFAULT 1.0,
                    embedding   BLOB
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS contradictions (
                    id          TEXT PRIMARY KEY,
                    new_fact_id TEXT REFERENCES facts(id),
                    old_fact_id TEXT REFERENCES facts(id),
                    similarity  REAL,
                    detected_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id          TEXT PRIMARY KEY,
                    project     TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    fact_ids    TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workstreams (
                    id          TEXT PRIMARY KEY,
                    project     TEXT NOT NULL,
                    name        TEXT NOT NULL,
                    snapshot    TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    UNIQUE(project, name)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_snapshots (
                    id          TEXT PRIMARY KEY,
                    project     TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    token_count INT,
                    created_at  TEXT NOT NULL,
                    UNIQUE(project)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fact_vec_map (
                    rowid   INTEGER PRIMARY KEY AUTOINCREMENT,
                    fact_id TEXT UNIQUE NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_project ON facts(project)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(fact_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_valid ON facts(valid_at, invalid_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_workstream ON facts(workstream)"
            )
            # FTS5 virtual table for keyword search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                    fact_id UNINDEXED,
                    content,
                    tokenize='porter ascii'
                )
            """)
            # sqlite-vec KNN table
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS fact_vec USING vec0(
                    rowid INTEGER PRIMARY KEY,
                    embedding FLOAT[384]
                )
            """)

    def _find_contradictions(
        self, embedding: list[float], fact_type: str, project: str
    ) -> list[str]:
        """Return IDs of valid same-type facts above the cosine similarity threshold."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, embedding FROM facts
                   WHERE fact_type = ? AND project = ? AND invalid_at IS NULL""",
                (fact_type, project),
            ).fetchall()

        new_vec = np.array(embedding, dtype=np.float32)
        contradicted: list[str] = []
        for row in rows:
            if not row["embedding"]:
                continue
            existing = np.frombuffer(row["embedding"], dtype=np.float32)
            sim = float(np.dot(new_vec, existing))
            if sim >= CONTRADICTION_THRESHOLD:
                contradicted.append(row["id"])
        return contradicted

    def _semantic_search(
        self,
        embedding: list[float],
        projects: list[str] | None,
        fact_types: list[FactType] | None,
        include_invalid: bool,
        limit: int,
    ) -> list[tuple[str, float]]:
        query_bytes = _pack(embedding)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT fvm.fact_id, fv.distance
                   FROM fact_vec fv
                   JOIN fact_vec_map fvm ON fv.rowid = fvm.rowid
                   WHERE fv.embedding MATCH ? AND k = ?
                   ORDER BY fv.distance""",
                (query_bytes, limit * 3),
            ).fetchall()

        results: list[tuple[str, float]] = []
        for row in rows:
            fact = self._get_fact(row["fact_id"])
            if fact is None:
                continue
            if not include_invalid and fact.invalid_at is not None:
                continue
            if projects and fact.project not in projects:
                continue
            if fact_types and fact.fact_type not in fact_types:
                continue
            # Convert L2 distance to a positive score (lower distance = higher score)
            score = 1.0 / (1.0 + row["distance"])
            results.append((row["fact_id"], score))
            if len(results) >= limit:
                break
        return results

    def _keyword_search(
        self,
        query: str,
        projects: list[str] | None,
        fact_types: list[FactType] | None,
        include_invalid: bool,
        limit: int,
    ) -> list[tuple[str, float]]:
        # Build an OR query so any matching term contributes; BM25 ranks by relevance.
        # FTS5 default AND would require every word present in one document.
        terms = [t.replace('"', '""') for t in query.split() if t]
        fts_query = " OR ".join(f'"{t}"' for t in terms) if terms else query
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    """SELECT fact_id, rank
                       FROM facts_fts
                       WHERE content MATCH ?
                       ORDER BY rank
                       LIMIT ?""",
                    (fts_query, limit * 3),
                ).fetchall()
            except sqlite3.OperationalError:
                return []

        results: list[tuple[str, float]] = []
        for row in rows:
            fact = self._get_fact(row["fact_id"])
            if fact is None:
                continue
            if not include_invalid and fact.invalid_at is not None:
                continue
            if projects and fact.project not in projects:
                continue
            if fact_types and fact.fact_type not in fact_types:
                continue
            # BM25 rank is negative in FTS5; negate for a positive score
            results.append((row["fact_id"], -row["rank"]))
            if len(results) >= limit:
                break
        return results

    def _get_fact(self, fact_id: str) -> Fact | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM facts WHERE id = ?", (fact_id,)
            ).fetchone()
        return _row_to_fact(row) if row else None


def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"],
        project=row["project"],
        workstream=row["workstream"],
        content=row["content"],
        fact_type=row["fact_type"],
        valid_at=row["valid_at"],
        invalid_at=row["invalid_at"],
        created_at=row["created_at"],
        source=row["source"],
        confidence=row["confidence"],
    )
