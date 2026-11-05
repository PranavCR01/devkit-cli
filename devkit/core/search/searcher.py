from __future__ import annotations

from pathlib import Path

from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend
from devkit.core.memory.store import FactType, SearchResult


def detect_project(cwd: Path | None = None) -> str:
    """Detect current project name from git root; fall back to cwd name."""
    from devkit.core.context.injector import detect_project as _detect
    return _detect(cwd)


def get_backend(db_path: Path = MEMORY_DB) -> SQLiteBackend:
    return SQLiteBackend(db_path)


def search(
    query: str,
    projects: list[str] | None = None,
    fact_types: list[FactType] | None = None,
    limit: int = 10,
    include_invalid: bool = False,
    db_path: Path = MEMORY_DB,
) -> list[SearchResult]:
    """Cross-project semantic + keyword search. Public API for programmatic use."""
    return get_backend(db_path).search(
        query=query,
        projects=projects,
        fact_types=fact_types,
        limit=limit,
        include_invalid=include_invalid,
    )
