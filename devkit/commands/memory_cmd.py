from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import rich.box
import typer
from rich.console import Console
from rich.table import Table

from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend

memory_app = typer.Typer(
    name="memory",
    help="Temporal memory: save decisions, patterns, bugs, architecture.",
    no_args_is_help=True,
)
console = Console()

VALID_TYPES = [
    "decision", "pattern", "bug", "architecture",
    "preference", "vulnerability_pattern",
]


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


def _get_backend() -> SQLiteBackend:
    try:
        return SQLiteBackend(MEMORY_DB)
    except RuntimeError as exc:
        typer.echo(f"[!] {exc}")
        raise typer.Exit(code=1)


@memory_app.command("save")
def memory_save(
    content: str = typer.Argument(..., help="The fact to store"),
    fact_type: str = typer.Option(
        "decision", "--type", "-t",
        help="decision|pattern|bug|architecture|preference|vulnerability_pattern",
    ),
    workstream: Optional[str] = typer.Option(None, "--workstream", "-w",
                                              help="Tag to a named workstream"),
    project: Optional[str] = typer.Option(None, "--project", "-p",
                                           help="Project name (default: git root name)"),
    source: str = typer.Option("manual", "--source", hidden=True),
) -> None:
    """Save a fact to memory."""
    if fact_type not in VALID_TYPES:
        typer.echo(f"[!] Invalid type '{fact_type}'. Choose: {', '.join(VALID_TYPES)}")
        raise typer.Exit(code=1)

    proj = project or _detect_project()
    backend = _get_backend()
    fact = backend.save(
        content=content, fact_type=fact_type, project=proj,
        workstream=workstream, source=source,
    )

    typer.echo(f"[OK] Saved ({fact.fact_type}) to project '{fact.project}'")
    typer.echo(f"     id: {fact.id}")
    if fact.workstream:
        typer.echo(f"     workstream: {fact.workstream}")

    try:
        from devkit.core.context.manifest import Manifest
        Manifest().update_fact_count(proj)
    except Exception:
        pass


@memory_app.command("list")
def memory_list(
    project: Optional[str] = typer.Option(
        None, "--project", "-p",
        help="Project name or 'all' (default: current git project)",
    ),
    fact_type: Optional[str] = typer.Option(None, "--type", "-t"),
    workstream: Optional[str] = typer.Option(None, "--workstream", "-w"),
    include_invalid: bool = typer.Option(False, "--include-invalid",
                                         help="Show superseded facts too"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """List stored facts."""
    proj = project or _detect_project()
    backend = _get_backend()
    facts = backend.list_facts(
        project=proj, fact_type=fact_type, workstream=workstream,
        include_invalid=include_invalid, limit=limit,
    )

    if not facts:
        typer.echo("No facts found.")
        return

    table = Table(box=rich.box.ASCII, show_header=True, header_style="bold")
    table.add_column("ID", width=9)
    table.add_column("Type", width=16)
    table.add_column("Date", width=12)
    table.add_column("Project", width=16)
    table.add_column("Content")

    for fact in facts:
        status = " [SUPERSEDED]" if fact.invalid_at else ""
        snippet = fact.content[:68] + "..." if len(fact.content) > 68 else fact.content
        table.add_row(
            fact.id[:8],
            fact.fact_type,
            fact.valid_at[:10],
            fact.project,
            snippet + status,
        )

    console.print(table)
    label = f"project '{proj}'" if proj != "all" else "all projects"
    typer.echo(f"Showing {len(facts)} fact(s) for {label}")


@memory_app.command("contradict")
def memory_contradict(
    fact_id: str = typer.Argument(..., help="Fact ID (or 8-char prefix) to invalidate"),
    reason: Optional[str] = typer.Option(None, "--reason", "-r",
                                          help="Why this fact was superseded"),
) -> None:
    """Mark a fact as superseded."""
    backend = _get_backend()

    if len(fact_id) < 36:
        facts = backend.list_facts(include_invalid=True, limit=5000)
        matches = [f for f in facts if f.id.startswith(fact_id)]
        if not matches:
            typer.echo(f"[!] No fact found with id prefix: {fact_id}")
            raise typer.Exit(code=1)
        if len(matches) > 1:
            typer.echo(f"[!] Ambiguous prefix -- {len(matches)} facts match. Use more characters.")
            raise typer.Exit(code=1)
        fact_id = matches[0].id

    backend.contradict(fact_id, reason)
    typer.echo(f"[OK] Marked {fact_id[:8]}... as superseded")
    if reason:
        typer.echo(f"     reason: {reason}")


@memory_app.command("switch")
def memory_switch(
    name: str = typer.Argument(..., help="Workstream name to switch to"),
    project: Optional[str] = typer.Option(None, "--project", "-p"),
) -> None:
    """Save current context and load a named workstream."""
    proj = project or _detect_project()
    backend = _get_backend()

    existing = backend.load_workstream(name, proj)
    backend.save_workstream("main", proj, {"switched_to": name})

    if existing:
        typer.echo(f"[OK] Switched to workstream '{name}' (project: {proj})")
    else:
        backend.save_workstream(name, proj, {})
        typer.echo(f"[OK] Created and switched to workstream '{name}' (project: {proj})")

    try:
        from devkit.core.context.manifest import Manifest
        Manifest().update_workstream(proj, name)
    except Exception:
        pass


@memory_app.command("workstreams")
def memory_workstreams(
    project: Optional[str] = typer.Option(None, "--project", "-p"),
) -> None:
    """List workstreams for current project."""
    proj = project or _detect_project()
    backend = _get_backend()
    wss = backend.list_workstreams(proj)

    if not wss:
        typer.echo(f"No workstreams found for project '{proj}'")
        return

    typer.echo(f"Workstreams for '{proj}':")
    for ws in wss:
        typer.echo(f"  {ws['name']:<20} updated: {ws['updated_at'][:10]}")


@memory_app.command("snapshot")
def memory_snapshot(
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    token_cap: int = typer.Option(2000, "--token-cap"),
    fmt: str = typer.Option("human", "--format", help="human | hook"),
) -> None:
    """Show what would be injected at Claude Code session start."""
    proj = project or _detect_project()
    backend = _get_backend()
    snapshot = backend.get_snapshot(proj, token_cap)
    typer.echo(snapshot)
