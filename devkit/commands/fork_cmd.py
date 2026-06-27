from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from devkit.core.fork.blueprint import BLUEPRINTS_DIR, Blueprint
from devkit.core.context.manifest import Manifest

fork_app = typer.Typer(
    name="fork",
    help="Extract feature blueprints from projects and apply them as context.",
    no_args_is_help=True,
)
console = Console()


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


def _get_project_facts(project_name: str, project_path: str) -> list[dict]:
    """Fetch valid facts for a project via raw sqlite3 (no [memory] extra needed)."""
    memory_db = Path.home() / ".devkit" / "memory.db"
    if not memory_db.exists():
        return []
    try:
        conn = sqlite3.connect(str(memory_db))
        rows = conn.execute(
            "SELECT id, fact_type, content, valid_at FROM facts "
            "WHERE (project = ? OR project = ?) AND invalid_at IS NULL "
            "ORDER BY valid_at DESC",
            (project_name, project_path),
        ).fetchall()
        conn.close()
        return [
            {"id": r[0], "fact_type": r[1], "content": r[2], "valid_at": r[3]}
            for r in rows
        ]
    except Exception:
        return []


@fork_app.command("create")
def fork_create(
    feature: str = typer.Argument(..., help="Feature name to extract (e.g. auth, payment, user)"),
    from_project: str = typer.Option(..., "--from", help="Source project name (as registered in manifest)"),
    name: Optional[str] = typer.Option(None, "--name", help="Blueprint name (default: <feature>-pattern)"),
    max_nodes: int = typer.Option(30, "--max-nodes", help="Max nodes in extracted subgraph"),
) -> None:
    """Extract a feature subgraph and memory decisions into a reusable blueprint."""
    manifest = Manifest()
    data = manifest.load()

    if from_project not in data["projects"]:
        typer.echo(f"[!] Project '{from_project}' not found in manifest.")
        typer.echo("    Registered projects: " + ", ".join(data["projects"].keys()) or "(none)")
        typer.echo("    Run: devkit context register <name> <path>")
        raise typer.Exit(code=1)

    proj_data = data["projects"][from_project]
    kg = proj_data.get("knowledge_graph")

    if not kg:
        proj_path = proj_data.get("path", "")
        expected = Path(proj_path) / ".understand-anything" / "knowledge-graph.json"
        typer.echo(f"[!] No knowledge graph found for project '{from_project}'.")
        typer.echo(f"    Expected: {expected}")
        typer.echo("    Generate one with Understand Anything, then run:")
        typer.echo("      devkit context refresh")
        typer.echo("    to register it in the manifest.")
        raise typer.Exit(code=1)

    graph_path = kg["path"]
    if not Path(graph_path).exists():
        typer.echo(f"[!] Knowledge graph file no longer exists: {graph_path}")
        typer.echo("    Run: devkit context refresh")
        raise typer.Exit(code=1)

    typer.echo(f"\nDevKit Fork -- extracting '{feature}' from {from_project}")
    typer.echo()

    # Load graph
    try:
        from devkit.core.fork.extractor import SubgraphExtractor
    except ImportError as exc:
        typer.echo(f"[!] {exc}")
        raise typer.Exit(code=1)

    try:
        extractor = SubgraphExtractor(graph_path)
    except Exception as exc:
        typer.echo(f"[!] Failed to load knowledge graph: {exc}")
        raise typer.Exit(code=1)

    n_nodes = len(extractor.raw_graph.get("nodes", []))
    n_edges = len(extractor.raw_graph.get("edges", []))
    typer.echo(f"Loading knowledge graph... {n_nodes:,} nodes, {n_edges:,} edges")

    # Find seed nodes
    seeds = extractor.find_seed_nodes(feature)
    if not seeds:
        typer.echo(f"[!] No nodes found matching '{feature}'.")
        typer.echo("    Try a broader term (e.g. 'auth', 'payment', 'user').")
        raise typer.Exit(code=1)

    typer.echo(f"Finding seed nodes for '{feature}'... {len(seeds)} found")
    for sid in seeds[:8]:
        typer.echo(f"  - {sid}")
    if len(seeds) > 8:
        typer.echo(f"  ... and {len(seeds) - 8} more")

    # Extract subgraph
    typer.echo(f"\nRunning personalized PageRank...")
    typer.echo(f"Selecting top {max_nodes} nodes by relevance...")
    try:
        subgraph_nodes, subgraph_edges, external_deps = extractor.extract(feature, max_nodes)
    except ValueError as exc:
        typer.echo(f"[!] {exc}")
        raise typer.Exit(code=1)

    typer.echo("Detecting shared dependencies...")
    if external_deps:
        for dep in external_deps:
            typer.echo(f"  --> {dep} -- marked external")
    else:
        typer.echo("  (none detected)")

    typer.echo(f"\nSubgraph: {len(subgraph_nodes)} nodes, {len(subgraph_edges)} edges")

    # Fetch memory facts
    proj_path = proj_data.get("path", "")
    all_facts = _get_project_facts(from_project, proj_path)
    typer.echo(f"\nLoading memory facts for '{from_project}'...")

    memory_facts: list[dict] = []
    if all_facts:
        typer.echo(f"  Found {len(all_facts)} fact(s):")
        for i, fact in enumerate(all_facts, 1):
            snippet = fact["content"][:80]
            if len(fact["content"]) > 80:
                snippet += "..."
            typer.echo(f"  {i:>3}. [{fact['fact_type'].upper()}] {snippet}")

        typer.echo()
        raw = typer.prompt(
            "Select facts to include (e.g. 1,3,5 or Enter = all)", default=""
        )
        if raw.strip():
            selected = []
            for part in raw.split(","):
                part = part.strip()
                if part.isdigit():
                    n = int(part)
                    if 1 <= n <= len(all_facts):
                        selected.append(all_facts[n - 1])
            chosen_facts = selected
        else:
            chosen_facts = all_facts

        memory_facts = [
            {
                "content": f["content"],
                "fact_type": f["fact_type"],
                "valid_at": f["valid_at"],
            }
            for f in chosen_facts
        ]
        typer.echo(f"  Including {len(memory_facts)} fact(s) in blueprint.")
    else:
        typer.echo("  No memory facts found for this project.")

    # Stack context from graph metadata if available
    stack_context: dict = extractor.raw_graph.get("metadata", {}) or {}

    # Determine blueprint name
    bp_name = name or f"{feature.lower().replace(' ', '-')}-pattern"

    bp_dir = BLUEPRINTS_DIR / bp_name
    if bp_dir.exists():
        overwrite = typer.confirm(
            f"\nBlueprint '{bp_name}' already exists. Overwrite?", default=False
        )
        if not overwrite:
            typer.echo("Cancelled.")
            raise typer.Exit()

    # Create blueprint
    typer.echo(f"\nSaving blueprint to {BLUEPRINTS_DIR / bp_name}/")
    try:
        created_dir = Blueprint.create(
            name=bp_name,
            feature_name=feature,
            source_project=from_project,
            subgraph_nodes=subgraph_nodes,
            subgraph_edges=subgraph_edges,
            external_dependencies=external_deps,
            memory_facts=memory_facts,
            stack_context=stack_context,
        )
    except Exception as exc:
        typer.echo(f"[!] Failed to create blueprint: {exc}")
        raise typer.Exit(code=1)

    meta = Blueprint.load(bp_name)
    typer.echo(f"  [OK] subgraph.json ({len(subgraph_nodes)} nodes, {len(subgraph_edges)} edges)")
    typer.echo(f"  [OK] blueprint.json ({len(memory_facts)} memory fact(s), ~{meta['token_estimate']:,} token estimate)")

    # Register in manifest
    try:
        manifest.register_blueprint(bp_name, from_project, created_dir, meta["token_estimate"])
    except Exception:
        pass  # manifest update is best-effort

    typer.echo(f"\nBlueprint '{bp_name}' saved.")
    typer.echo(f"Run: devkit fork apply {bp_name}")


@fork_app.command("list")
def fork_list() -> None:
    """List all saved blueprints."""
    blueprints = Blueprint.list_all()
    if not blueprints:
        typer.echo("No blueprints found.")
        typer.echo("  Run: devkit fork create <feature> --from <project>")
        return

    console.print()
    console.print("[bold]DevKit Blueprints[/bold]")
    console.print("-" * 64)
    for bp in blueprints:
        age = _format_age(bp.get("extracted_at", ""))
        nodes = bp.get("node_count", 0)
        n_facts = len(bp.get("memory_facts", []))
        tokens = bp.get("token_estimate", 0)
        fact_label = f"{n_facts} fact{'s' if n_facts != 1 else ''}"
        console.print(
            f"  {bp['name']:<22} from: {bp.get('source_project',''):<20} "
            f"{nodes} nodes   {fact_label:<10} {age:<10}  ~{tokens:,} tokens"
        )
    console.print("-" * 64)
    n = len(blueprints)
    console.print(f"{n} blueprint{'s' if n != 1 else ''} available")
    console.print("Run: devkit fork apply <name>  or  devkit fork inspect <name>")


@fork_app.command("inspect")
def fork_inspect(
    name: str = typer.Argument(..., help="Blueprint name"),
) -> None:
    """Show full contents of a blueprint."""
    try:
        meta = Blueprint.load(name)
        subgraph = Blueprint.load_subgraph(name)
    except ValueError as exc:
        typer.echo(f"[!] {exc}")
        raise typer.Exit(code=1)

    console.print(f"\n[bold]Blueprint: {name}[/bold]")
    console.print("-" * 48)
    console.print(f"  Source project : {meta.get('source_project', '')}")
    console.print(f"  Feature query  : {meta.get('seed_query', '')}")
    console.print(f"  Extracted      : {_format_age(meta.get('extracted_at', ''))}")
    console.print(f"  Nodes          : {meta.get('node_count', 0)}")
    console.print(f"  Edges          : {meta.get('edge_count', 0)}")
    console.print(f"  Token estimate : ~{meta.get('token_estimate', 0):,}")
    console.print(f"  Description    : {meta.get('description', '')}")

    if meta.get("stack_context"):
        sc = meta["stack_context"]
        console.print(f"\n  Stack context:")
        for k, v in sc.items():
            console.print(f"    {k}: {v}")

    if meta.get("memory_facts"):
        console.print(f"\n  Memory facts ({len(meta['memory_facts'])}):")
        for fact in meta["memory_facts"]:
            console.print(f"    [{fact['fact_type'].upper()}] {fact['content']}")

    if meta.get("external_dependencies"):
        console.print(f"\n  External dependencies (not in blueprint):")
        for dep in meta["external_dependencies"]:
            console.print(f"    - {dep}")

    if meta.get("transfer_notes"):
        console.print(f"\n  Transfer notes: {meta['transfer_notes']}")

    nodes = subgraph.get("nodes", [])
    console.print(f"\n  Subgraph nodes ({len(nodes)}):")
    for node in nodes[:15]:
        ntype = node.get("type", "")
        nname = node.get("name", node.get("id", ""))
        console.print(f"    [{ntype}] {nname}")
    if len(nodes) > 15:
        console.print(f"    ... and {len(nodes) - 15} more")


@fork_app.command("delete")
def fork_delete(
    name: str = typer.Argument(..., help="Blueprint name to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a blueprint and its manifest entry."""
    bp_dir = BLUEPRINTS_DIR / name
    if not bp_dir.exists():
        typer.echo(f"[!] Blueprint '{name}' not found.")
        raise typer.Exit(code=1)

    if not yes:
        confirmed = typer.confirm(f"Delete blueprint '{name}'?", default=False)
        if not confirmed:
            typer.echo("Cancelled.")
            return

    shutil.rmtree(bp_dir)

    try:
        manifest = Manifest()
        data = manifest.load()
        data.get("blueprints", {}).pop(name, None)
        manifest.save(data)
    except Exception:
        pass

    typer.echo(f"[OK] Blueprint '{name}' deleted.")


@fork_app.command("apply")
def fork_apply(
    name: str = typer.Argument(..., help="Blueprint name to inject"),
    context_only: bool = typer.Option(
        False, "--context-only", help="Inject memory facts only, no subgraph structure"
    ),
) -> None:
    """Inject a blueprint as context into the current session."""
    try:
        rendered = Blueprint.render_for_injection(name, context_only=context_only)
    except ValueError as exc:
        typer.echo(f"[!] {exc}")
        raise typer.Exit(code=1)
    except Exception as exc:
        typer.echo(f"[!] Failed to render blueprint: {exc}")
        raise typer.Exit(code=1)

    typer.echo(rendered)
