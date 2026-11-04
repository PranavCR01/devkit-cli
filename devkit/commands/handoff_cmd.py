from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

import rich.box
import typer
from rich.console import Console
from rich.table import Table

handoff_app = typer.Typer(
    name="handoff",
    help="Shared task ledger for coding agents: claim, hand over, take over, recover.",
    no_args_is_help=True,
)
_console = Console()

_AGENT_HELP = "Agent identity (default: $DEVKIT_AGENT or 'human')"


def _agent(agent: Optional[str]) -> str:
    return agent or os.environ.get("DEVKIT_AGENT") or "human"


def _ledger():
    from devkit.core.handoff.ledger import Ledger
    return Ledger()


def _fail(exc: Exception) -> None:
    typer.echo(f"[!] {exc}")
    raise typer.Exit(1)


def _project(project: Optional[str]) -> str:
    from devkit.core.context.injector import detect_project
    return project or detect_project()


@handoff_app.command("create")
def create(
    title: str = typer.Argument(...),
    accept: List[str] = typer.Option([], "--accept", "-a", help="Acceptance criterion (repeatable)"),
    depends: List[str] = typer.Option([], "--depends", "-d", help="Task id this depends on (repeatable)"),
    description: str = typer.Option("", "--description"),
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    agent: Optional[str] = typer.Option(None, "--agent", help=_AGENT_HELP),
) -> None:
    """Create a task with acceptance criteria and dependencies."""
    from devkit.core.handoff.ledger import LedgerError
    try:
        tid = _ledger().create(title, _project(project), description, accept, depends, _agent(agent))
    except LedgerError as exc:
        _fail(exc)
    typer.echo(f"[OK] Task {tid} created: {title}")


@handoff_app.command("list")
def list_cmd(
    status: Optional[str] = typer.Option(None, "--status", help="open|claimed|handoff|done"),
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    all_projects: bool = typer.Option(False, "--all", help="All projects"),
) -> None:
    """List tasks."""
    tasks = _ledger().list(None if all_projects else _project(project), status)
    if not tasks:
        typer.echo("No tasks.")
        return
    table = Table(box=rich.box.ASCII)
    for col in ("ID", "Status", "Owner", "Lease until", "Title"):
        table.add_column(col)
    for t in tasks:
        table.add_row(t.id, t.status, t.owner or "-", (t.lease_until or "-")[:16], t.title[:60])
    _console.print(table)


@handoff_app.command("claim")
def claim(
    task_id: str = typer.Argument(...),
    agent: Optional[str] = typer.Option(None, "--agent", help=_AGENT_HELP),
    lease: int = typer.Option(30, "--lease", help="Lease minutes; renew with heartbeat"),
    worktree: bool = typer.Option(False, "--worktree", help="Create/reuse an isolated git worktree + branch"),
    base: Optional[str] = typer.Option(None, "--base", help="Base ref for the worktree (default: current branch)"),
) -> None:
    """Claim (or take over) a task. Prints the handover brief."""
    from devkit.core.handoff.ledger import LedgerError, create_worktree
    ledger = _ledger()
    try:
        task = ledger.claim(task_id, _agent(agent), lease)
        if worktree and not task.worktree:
            path, branch, base_ref = create_worktree(Path.cwd(), task.id, base)
            ledger.set_workspace(task.id, path, branch, base_ref)
    except LedgerError as exc:
        _fail(exc)
    typer.echo(f"[OK] {task.id} claimed by {_agent(agent)} until {task.lease_until[:16]}")
    typer.echo(ledger.brief(task.id))


@handoff_app.command("heartbeat")
def heartbeat(task_id: str, agent: Optional[str] = typer.Option(None, "--agent", help=_AGENT_HELP),
              lease: int = typer.Option(30, "--lease")) -> None:
    """Renew your lease on a task."""
    from devkit.core.handoff.ledger import LedgerError
    try:
        until = _ledger().heartbeat(task_id, _agent(agent), lease)
    except LedgerError as exc:
        _fail(exc)
    typer.echo(f"[OK] lease extended to {until[:16]}")


@handoff_app.command("note")
def note(task_id: str, text: str,
         agent: Optional[str] = typer.Option(None, "--agent", help=_AGENT_HELP),
         intervention: bool = typer.Option(False, "--intervention",
                                           help="Record as a human intervention (counted in stats)")) -> None:
    """Append a progress note."""
    from devkit.core.handoff.ledger import LedgerError
    try:
        _ledger().note(task_id, _agent(agent), text, intervention)
    except LedgerError as exc:
        _fail(exc)
    typer.echo("[OK] noted")


@handoff_app.command("check")
def check(task_id: str, criterion: int = typer.Argument(..., help="Criterion number"),
          agent: Optional[str] = typer.Option(None, "--agent", help=_AGENT_HELP)) -> None:
    """Mark an acceptance criterion as met."""
    from devkit.core.handoff.ledger import LedgerError
    try:
        _ledger().check(task_id, criterion, _agent(agent))
    except LedgerError as exc:
        _fail(exc)
    typer.echo(f"[OK] criterion #{criterion} checked")


@handoff_app.command("release")
def release(task_id: str, note_text: str = typer.Option(..., "--note", "-m",
                                                        help="Handover: done / next / risks"),
            agent: Optional[str] = typer.Option(None, "--agent", help=_AGENT_HELP)) -> None:
    """Hand a task over to the next agent, with notes."""
    from devkit.core.handoff.ledger import LedgerError
    try:
        _ledger().release(task_id, _agent(agent), note_text)
    except LedgerError as exc:
        _fail(exc)
    typer.echo(f"[OK] {task_id} released for takeover")


@handoff_app.command("done")
def done(
    task_id: str,
    agent: Optional[str] = typer.Option(None, "--agent", help=_AGENT_HELP),
    commit: List[str] = typer.Option([], "--commit", help="Commit sha (repeatable; default: branch commits)"),
    note_text: Optional[str] = typer.Option(None, "--note", "-m"),
    force: bool = typer.Option(False, "--force", help="Complete despite unchecked criteria/conflicts"),
) -> None:
    """Complete a task: checks criteria, records commits, detects merge conflicts."""
    from devkit.core.handoff.ledger import LedgerError, branch_commits, has_merge_conflict
    ledger = _ledger()
    try:
        task = ledger.get(task_id)
        commits, conflict = list(commit), False
        if task.branch and task.base_ref:
            cwd = task.worktree or Path.cwd()
            if not commits:
                commits = branch_commits(cwd, task.base_ref, task.branch)
            conflict = bool(has_merge_conflict(cwd, task.base_ref, task.branch))
        ledger.complete(task.id, _agent(agent), commits, conflict=conflict, force=force, note=note_text)
    except LedgerError as exc:
        _fail(exc)
    typer.echo(f"[OK] {task_id} done ({len(commits)} commit(s) recorded)")


@handoff_app.command("recover")
def recover() -> None:
    """Return tasks whose agent lease expired (crashed/stalled agents) to the pool."""
    tasks = _ledger().recover()
    if not tasks:
        typer.echo("No expired leases.")
    for t in tasks:
        typer.echo(f"[OK] recovered {t.id} (was {t.owner}): {t.title}")


@handoff_app.command("brief")
def brief(task_id: str, no_memory: bool = typer.Option(False, "--no-memory")) -> None:
    """Print the context block a new agent needs to continue a task."""
    from devkit.core.handoff.ledger import LedgerError
    ledger = _ledger()
    try:
        task = ledger.get(task_id)
    except LedgerError as exc:
        _fail(exc)
    memory: list[str] = []
    if not no_memory:
        try:
            from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend
            if MEMORY_DB.exists():
                hits = SQLiteBackend(MEMORY_DB).search(task.title, projects=[task.project], limit=5)
                memory = [f"[{h.fact.fact_type}] {h.fact.content}" for h in hits]
        except Exception:
            memory = []
    typer.echo(ledger.brief(task.id, memory))


@handoff_app.command("show")
def show(task_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    """Full task record and event timeline."""
    from dataclasses import asdict
    from devkit.core.handoff.ledger import LedgerError
    try:
        d = _ledger().details(task_id)
    except LedgerError as exc:
        _fail(exc)
    if as_json:
        typer.echo(json.dumps({**d, "task": asdict(d["task"])}, indent=2))
        return
    t = d["task"]
    typer.echo(f"{t.id}  [{t.status}]  {t.title}  (owner: {t.owner or '-'})")
    for e in d["events"]:
        typer.echo(f"  {e['ts'][:19]}  {e['agent'] or '-':<12} {e['kind']:<18} {e['note'] or ''}")


@handoff_app.command("stats")
def stats(project: Optional[str] = typer.Option(None, "--project", "-p"),
          all_projects: bool = typer.Option(False, "--all")) -> None:
    """Orchestration indicators: completions, handoffs, takeovers, recoveries, conflicts, interventions."""
    s = _ledger().stats(None if all_projects else _project(project))
    for k, v in s.items():
        typer.echo(f"{k:<24} {v}")
