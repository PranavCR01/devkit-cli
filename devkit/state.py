from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from devkit.paths import STATE_DB


class State:
    """Persistent SQLite state shared across all commands."""

    def __init__(self, db_path: Path = STATE_DB) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    def init_db(self) -> None:
        """Create tables if not exist. Slice 1: scan_history only."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_history (
                    scan_id        TEXT PRIMARY KEY,
                    path           TEXT NOT NULL,
                    mode           TEXT NOT NULL,
                    findings_count INTEGER NOT NULL,
                    grade          TEXT,
                    security_score INTEGER,
                    quality_score  INTEGER,
                    created_at     TEXT NOT NULL
                )
            """)

    def record_scan(
        self,
        scan_id: str,
        path: str,
        mode: str,
        findings_count: int,
        grade: str | None = None,
        security_score: int | None = None,
        quality_score: int | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO scan_history
                    (scan_id, path, mode, findings_count, grade,
                     security_score, quality_score, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id, path, mode, findings_count,
                    grade, security_score, quality_score,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def get_scan_history(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT scan_id, path, mode, findings_count, grade,
                       security_score, quality_score, created_at
                FROM scan_history
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Commit on success, roll back on error, always close (Windows file locks)."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn
        finally:
            conn.close()
