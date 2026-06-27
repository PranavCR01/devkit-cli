from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

import typer


def _detect_project() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, cwd=str(Path.cwd()),
        )
        if result.returncode == 0:
            return Path(result.stdout.strip()).name
    except (OSError, FileNotFoundError):
        pass
    return Path.cwd().name


def search_cmd(
    query: str = typer.Argument(..., help="Search query"),
    project: Optional[str] = typer.Option(
        None, "--project", "-p",
        help="Scope to one project (default: all projects)",
    ),
    fact_type: Optional[str] = typer.Option(
        None, "--type", "-t",
        help="Filter: decision|pattern|bug|architecture|preference",
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
    output: str = typer.Option("text", "--output", "-o", help="text | json"),
    include_invalid: bool = typer.Option(False, "--include-invalid",
                                         help="Include superseded facts"),
) -> None:
    """Cross-project semantic + keyword search over stored facts."""
    from devkit.core.search.searcher import search

    projects = [project] if project else None
    fact_types = [fact_type] if fact_type else None  # type: ignore[list-item]

    try:
        results = search(
            query=query,
            projects=projects,
            fact_types=fact_types,
            limit=limit,
            include_invalid=include_invalid,
        )
    except RuntimeError as exc:
        typer.echo(f"[!] {exc}")
        raise typer.Exit(code=1)

    if output == "json":
        data = [
            {
                "id": r.fact.id,
                "project": r.fact.project,
                "fact_type": r.fact.fact_type,
                "content": r.fact.content,
                "score": round(r.score, 4),
                "match_type": r.match_type,
                "valid_at": r.fact.valid_at,
                "invalid_at": r.fact.invalid_at,
            }
            for r in results
        ]
        typer.echo(json.dumps(data, indent=2))
        return

    unique_projects = sorted({r.fact.project for r in results})
    typer.echo(f'DevKit Search: "{query}"')
    typer.echo(f"Found {len(results)} result(s) across {len(unique_projects)} project(s)")
    typer.echo("")

    for i, result in enumerate(results, 1):
        fact = result.fact
        status = "  [SUPERSEDED]" if fact.invalid_at else ""
        typer.echo(
            f"[{i}] {fact.project} | {fact.fact_type} | {fact.valid_at[:10]}"
            f" | score: {result.score:.4f}  [{result.match_type}]{status}"
        )
        typer.echo(f'    "{fact.content}"')
        if fact.workstream:
            typer.echo(f"    workstream: {fact.workstream}")
        typer.echo("")
