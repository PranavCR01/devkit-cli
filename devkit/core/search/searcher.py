from __future__ import annotations

import subprocess
from pathlib import Path

from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend
from devkit.core.memory.store import FactType, SearchResult


def detect_project(cwd: Path | None = None) -> str:
    """Detect current project name from git root; fall back to cwd name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
            cwd=str(cwd or Path.cwd()),
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).name
    except (OSError, FileNotFoundError):
        pass
    return (cwd or Path.cwd()).name


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
