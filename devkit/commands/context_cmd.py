from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from devkit.core.context.assembler import ContextAssembler
from devkit.core.context.manifest import Manifest

context_app = typer.Typer(
    name="context",
    help="Context assembly, discovery, and session injection.",
    no_args_is_help=True,
)
console = Console()

SESSION_FILE = Path.home() / ".devkit" / "context_session.json"

_TYPE_LABEL = {"graph": "[G]", "snapshot": "[S]", "workstream": "[W]", "blueprint": "[B]"}


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


def _format_age(dt_str: str) -> str:
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        s = (now - dt).total_seconds()
        if s < 3600:
            return f"{int(s / 60)}m ago"
        elif s < 86400:
            return f"{int(s / 3600)}h ago"
        elif s < 604800:
            return f"{int(s / 86400)}d ago"
        else:
            return f"{int(s / 604800)}w ago"
    except Exception:
        return dt_str[:10] if dt_str else ""


def _fetch_content(item: dict, manifest_data: dict) -> str:
    item_type = item["type"]
    proj_name = item["project"]

    if item_type == "graph":
        proj = manifest_data["projects"].get(proj_name, {})
        kg = proj.get("knowledge_graph") or {}
        graph_path = Path(kg.get("path", ""))
        if not graph_path.exists():
            return ""
        try:
            with open(graph_path, encoding="utf-8") as f:
                graph = json.load(f)
            nodes = graph.get("nodes", [])
            edges = graph.get("edges", [])
            lines = [
                f"Knowledge Graph: {proj_name}",
                f"Nodes: {len(nodes)}, Edges: {len(edges)}",
            ]
            for node in nodes[:50]:
                name = node.get("name") or node.get("file") or str(node.get("id", ""))
                ntype = node.get("type", "")
                if name:
                    lines.append(f"  {ntype + ': ' if ntype else ''}{name}")
            if len(nodes) > 50:
                lines.append(f"  ... and {len(nodes) - 50} more nodes")
            return "\n".join(lines)
        except Exception:
            return ""

    elif item_type in ("snapshot", "workstream"):
        try:
            from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend
        except ImportError:
            return ""
        if not MEMORY_DB.exists():
            return ""
        try:
            backend = SQLiteBackend(MEMORY_DB)
            ws = item["name"] if item_type == "workstream" else None
            facts = backend.list_facts(project=proj_name, workstream=ws, limit=20)
            if not facts:
                return ""
            lines = [f"Project: {proj_name}"]
            if ws:
                lines.append(f"Workstream: {ws}")
            for fact in facts:
                lines.append(f"[{fact.fact_type.upper()}] {fact.content}")
            return "\n".join(lines)
        except Exception:
            return ""

    elif item_type == "blueprint":
        bp = manifest_data.get("blueprints", {}).get(item["name"], {})
        bp_path = Path(bp.get("path", ""))
        if not bp_path.exists():
            return ""
        try:
            return bp_path.read_text(encoding="utf-8")
        except Exception:
            return ""

    return ""


def _save_session(assembler: ContextAssembler) -> None:
    try:
        session = {
            "assembled_at": datetime.now(timezone.utc).isoformat(),
            "token_count": assembler.current_tokens,
            "token_cap": assembler.token_cap,
            "items": [
                {
                    "type": i["meta"]["type"],
                    "project": i["meta"]["project"],
                    "name": i["meta"]["name"],
                }
                for i in assembler.items
            ],
        }
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps(session, indent=2), encoding="utf-8")
    except Exception:
        pass


@context_app.command("list")
def context_list(
    project: Optional[str] = typer.Option(None, "--project", "-p", help="Filter by project name"),
    type_filter: Optional[str] = typer.Option(
        None, "--type", "-t", help="graph|snapshot|workstream|blueprint"
    ),
    as_json: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    """List all available context items grouped by project."""
    manifest = Manifest()
    items = manifest.get_all_context_items()
    data = manifest.load()

    if project:
        items = [i for i in items if i["project"] == project]
    if type_filter:
        items = [i for i in items if i["type"] == type_filter]

    if as_json:
        sys.stdout.write(json.dumps(items, indent=2) + "\n")
        return

    if not items:
        typer.echo("No context items found.")
        typer.echo("  Run: devkit init  or  devkit context register <name> <path>")
        return

    projects: dict[str, list[dict]] = {}
    blueprints: list[dict] = []
    for item in items:
        if item["type"] == "blueprint":
            blueprints.append(item)
        else:
            projects.setdefault(item["project"], []).append(item)

    console.print()
    console.print("[bold]DevKit Context Registry[/bold]")
    console.print("-" * 56)

    for proj_name, proj_items in projects.items():
        proj_path = data["projects"].get(proj_name, {}).get("path", "")
        console.print(f"\n[bold]{proj_name}[/bold]  ({proj_path})")
        for item in proj_items:
            label = _TYPE_LABEL.get(item["type"], "[?]")
            age = _format_age(item["updated_at"])
            tokens = f"~{item['token_estimate']:,} tokens"
            desc = item["description"]
            console.print(
                f"  {label} {item['type']:<12} {item['name']:<22} {desc:<18} {age:<10} {tokens}"
            )

    if blueprints:
        console.print("\n[bold]blueprints[/bold]")
        for item in blueprints:
            label = _TYPE_LABEL.get(item["type"], "[?]")
            age = _format_age(item["updated_at"])
            tokens = f"~{item['token_estimate']:,} tokens"
            console.print(
                f"  {label} {item['name']:<22} {item['description']:<26} {age:<10} {tokens}"
            )

    n_proj = len(projects)
    n_bp = len(blueprints)
    n_items = len(items)
    console.print()
    console.print("-" * 56)
    proj_label = f"{n_proj} project{'s' if n_proj != 1 else ''}"
    bp_label = f"{n_bp} blueprint{'s' if n_bp != 1 else ''}"
    console.print(f"{proj_label} | {bp_label} | {n_items} items available")
    console.print("Run: devkit context add <item-id>  or  devkit context build")


@context_app.command("add")
def context_add(
    item_id: str = typer.Argument(..., help="Item ID from 'devkit context list'"),
    token_cap: int = typer.Option(8000, "--token-cap"),
) -> None:
    """Inject a single context item into the session."""
    manifest = Manifest()
    data = manifest.load()
    all_items = manifest.get_all_context_items()

    matches = [i for i in all_items if i["id"] == item_id]
    if not matches:
        typer.echo(f"[!] Item not found: {item_id}")
        typer.echo("    Run: devkit context list  to see available items")
        raise typer.Exit(code=1)

    item = matches[0]
    content = _fetch_content(item, data)
    if not content:
        typer.echo(f"[!] Could not load content for: {item_id}")
        raise typer.Exit(code=1)

    assembler = ContextAssembler(token_cap=token_cap)
    assembler.add_item(item, content)
    rendered = assembler.render()

    console.print(rendered)
    console.print()
    console.print(f"[dim]{assembler.summary()}[/dim]")
    _save_session(assembler)


@context_app.command("build")
def context_build(
    token_cap: int = typer.Option(8000, "--token-cap"),
) -> None:
    """Interactive context builder with numbered selection and token budget."""
    manifest = Manifest()
    data = manifest.load()
    all_items = manifest.get_all_context_items()

    if not all_items:
        typer.echo("No context items found.")
        typer.echo("  Run: devkit init  or  devkit context register <name> <path>")
        return

    console.print(f"\n[bold]DevKit Context Builder[/bold]  [budget: {token_cap:,} tokens]")
    console.print()

    idx = 1
    index_map: dict[int, dict] = {}
    projects: dict[str, list[tuple[int, dict]]] = {}
    blueprints: list[tuple[int, dict]] = []

    for item in all_items:
        index_map[idx] = item
        if item["type"] == "blueprint":
            blueprints.append((idx, item))
        else:
            projects.setdefault(item["project"], []).append((idx, item))
        idx += 1

    for proj_name, proj_items in projects.items():
        console.print(f"[bold]{proj_name}[/bold]")
        for n, item in proj_items:
            label = _TYPE_LABEL.get(item["type"], "[?]")
            tokens = f"~{item['token_estimate']:,} tokens"
            console.print(f"  {n:>3}. {label} {item['name']:<28} {tokens}")

    if blueprints:
        console.print("[bold]blueprints[/bold]")
        for n, item in blueprints:
            tokens = f"~{item['token_estimate']:,} tokens"
            console.print(f"  {n:>3}. [B] {item['name']:<28} {tokens}")

    console.print()
    raw = typer.prompt("Select items (numbers separated by commas, e.g. 1,3,5)")

    selected: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and int(part) in index_map:
            selected.append(int(part))
        elif part:
            typer.echo(f"  [!] Skipping invalid selection: {part}")

    if not selected:
        typer.echo("Nothing selected.")
        return

    console.print()
    assembler = ContextAssembler(token_cap=token_cap)
    for n in selected:
        item = index_map[n]
        content = _fetch_content(item, data)
        if not content:
            typer.echo(f"  [!] Could not load content for {item['id']} -- skipping")
            continue
        if not assembler.add_item(item, content):
            typer.echo(f"  [!] {item['name']} dropped -- would exceed token budget")

    if not assembler.items:
        typer.echo("No items assembled (budget too small or content unavailable).")
        return

    console.print(f"  {assembler.summary()}")
    if not typer.confirm("\nInject selected context?", default=True):
        typer.echo("Cancelled.")
        return

    console.print()
    console.print(assembler.render())
    _save_session(assembler)


@context_app.command("budget")
def context_budget() -> None:
    """Show current assembled context token usage."""
    if not SESSION_FILE.exists():
        typer.echo("No active context session.")
        typer.echo("  Run: devkit context build  or  devkit context add <id>")
        return
    try:
        session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        items = session.get("items", [])
        token_count = session.get("token_count", 0)
        token_cap = session.get("token_cap", 8000)
        assembled_at = session.get("assembled_at", "")[:19]

        console.print(f"\n[bold]Context Session[/bold]  (assembled: {assembled_at})")
        console.print(f"  {token_count:,} / {token_cap:,} tokens used  ({len(items)} items)")
        for item in items:
            label = _TYPE_LABEL.get(item.get("type", ""), "[?]")
            console.print(f"    {label} {item.get('project', '')} / {item.get('name', '')}")
        console.print(f"  Remaining: {token_cap - token_count:,} tokens")
    except Exception as exc:
        typer.echo(f"[!] Could not read session file: {exc}")


@context_app.command("clear")
def context_clear() -> None:
    """Clear the current assembled context session."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
        typer.echo("[OK] Context session cleared.")
    else:
        typer.echo("No active context session to clear.")


@context_app.command("refresh")
def context_refresh() -> None:
    """Rescan all registered projects for new knowledge graphs."""
    manifest = Manifest()
    manifest.refresh_knowledge_graphs()
    data = manifest.load()
    n = len(data.get("projects", {}))
    typer.echo(f"[OK] Refreshed manifest ({n} project(s) scanned).")


@context_app.command("register")
def context_register(
    name: str = typer.Argument(..., help="Project name"),
    path: str = typer.Argument(..., help="Absolute path to project directory"),
) -> None:
    """Manually register a project in the manifest."""
    p = Path(path)
    if not p.exists():
        typer.echo(f"[!] Path does not exist: {path}")
        raise typer.Exit(code=1)
    Manifest().register_project(name, str(p))
    typer.echo(f"[OK] Registered project '{name}' at {path}")


@context_app.command("inject")
def context_inject(
    project: Optional[str] = typer.Option(None, "--project", "-p"),
    fmt: str = typer.Option("human", "--format", help="human | hook"),
    token_cap: int = typer.Option(2000, "--token-cap"),
) -> None:
    """Auto-inject session snapshot (used by the session-start hook)."""
    try:
        proj = project or _detect_project()

        try:
            from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend
        except ImportError:
            return

        if not MEMORY_DB.exists():
            return

        try:
            backend = SQLiteBackend(MEMORY_DB)
        except Exception:
            return

        try:
            facts = backend.list_facts(project=proj, limit=50)
        except Exception:
            return

        if not facts:
            return

        content = "\n".join(
            f"[{fact.fact_type.upper()}] {fact.content}" for fact in facts
        )
        assembler = ContextAssembler(token_cap=token_cap)
        meta = {"type": "snapshot", "project": proj, "name": "session-snapshot"}
        assembler.add_item(meta, content)

        rendered = assembler.render()
        if rendered:
            typer.echo(rendered)

    except Exception:
        pass
