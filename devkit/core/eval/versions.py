from __future__ import annotations
import sqlite3
from pathlib import Path

MEMORY_DB = Path.home() / ".devkit" / "memory.db"
_PREFIX = "[PROMPT VERSION] "


def save_version(content: str, project: str) -> str | None:
    """Save a prompt snapshot to memory store. Requires [memory] extras.

    Returns the fact ID on success, None if [memory] is not installed or save fails.
    """
    try:
        from devkit.core.memory.sqlite_backend import MEMORY_DB as MDB, SQLiteBackend
        backend = SQLiteBackend(MDB)
        return backend.save(
            content=f"{_PREFIX}{content}",
            fact_type="pattern",
            project=project,
            workstream="eval",
            source="devkit-eval",
        )
    except Exception:
        return None


def list_versions(project: str | None = None, limit: int = 20) -> list[dict]:
    """List prompt versions via raw sqlite3 — no [memory] extras needed for reading."""
    if not MEMORY_DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(MEMORY_DB))
        conn.row_factory = sqlite3.Row
        if project:
            rows = conn.execute(
                "SELECT id, project, content, created_at FROM facts "
                "WHERE content LIKE ? AND project = ? AND invalid_at IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (f"{_PREFIX}%", project, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, project, content, created_at FROM facts "
                "WHERE content LIKE ? AND invalid_at IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (f"{_PREFIX}%", limit),
            ).fetchall()
        conn.close()
        return [
            {
                "id": r["id"],
                "project": r["project"],
                "content": r["content"][len(_PREFIX):],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    except Exception:
        return []
