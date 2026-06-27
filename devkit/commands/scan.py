from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Optional

import typer
from rich.console import Console

from devkit.config import Config
from devkit.core.scanner.orchestrator import Finding, ScanOrchestrator, ScanResult
from devkit.state import State

console = Console()


def _get_memory_store():
    """Return a pre-warmed SQLiteBackend or None if [memory] extra is not installed."""
    try:
        from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend
        from devkit.core.memory.embedder import Embedder
        return SQLiteBackend(MEMORY_DB, embedder=Embedder.get_instance())
    except (ImportError, RuntimeError):
        return None


_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_SEVERITY_COLORS = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "blue",
    "info": "dim",
}


def scan(
    path: str = typer.Argument(".", help="Directory to scan (default: current directory)"),
    mode: str = typer.Option("all", help="web | api | ai | all"),
    output: str = typer.Option("text", help="text | json"),
    no_graph: bool = typer.Option(False, "--no-graph", help="Skip graph-guided Tier 1"),
    severity: Optional[str] = typer.Option(None, "--severity", help="Filter: critical | high | medium | low"),
    no_semgrep: bool = typer.Option(False, "--no-semgrep", help="Skip Semgrep, use Claude only"),
    no_claude: bool = typer.Option(False, "--no-claude", help="Skip Claude, use Semgrep only"),
    save: bool = typer.Option(False, "--save", help="Persist findings to state.db"),
    dismiss: Optional[str] = typer.Option(None, "--dismiss", help="Dismiss a memory fact ID (prefix ok)"),
) -> None:
    """Run a security scan on a local directory."""
    cfg = Config()
    missing = cfg.validate()
    if missing:
        console.print(f"[red]Error:[/red] Missing config: {', '.join(missing)}")
        console.print("  Run: devkit config set ANTHROPIC_API_KEY <your-key>")
        raise typer.Exit(code=1)

    if dismiss:
        store = _get_memory_store()
        if store is None:
            typer.echo("[!] Memory not available. Run: pip install -e '.[memory]'")
            raise typer.Exit(code=1)
        if len(dismiss) < 36:
            all_facts = store.list_facts(include_invalid=True, limit=5000)
            matches = [f for f in all_facts if f.id.startswith(dismiss)]
            if not matches:
                typer.echo(f"[!] No memory fact found with id prefix: {dismiss}")
                raise typer.Exit(code=1)
            if len(matches) > 1:
                typer.echo(f"[!] Ambiguous prefix -- {len(matches)} facts match. Use more characters.")
                raise typer.Exit(code=1)
            dismiss = matches[0].id
        store.contradict(
            fact_id=dismiss,
            reason=f"Dismissed via scan on {datetime.now(timezone.utc).isoformat()}",
        )
        typer.echo(f"[OK] Finding {dismiss[:8]}... dismissed. Will not resurface in future scans.")
        return

    memory_store = _get_memory_store()
    auto_learn = bool(cfg.get("auto_learn"))

    orchestrator = ScanOrchestrator(
        api_key=cfg.get("anthropic_api_key"),
        model=cfg.get("default_model") or "claude-sonnet-4-6",
        memory_store=memory_store,
        auto_learn=auto_learn,
    )

    result = asyncio.run(
        orchestrator.run(
            path=path,
            mode=mode,
            use_graph=not no_graph,
            use_semgrep=not no_semgrep,
            use_claude=not no_claude,
        )
    )

    if severity:
        cutoff = _SEVERITY_RANK.get(severity.lower(), 4)
        result.findings = [f for f in result.findings if _SEVERITY_RANK.get(f.severity, 5) <= cutoff]

    if output == "json":
        sys.stdout.write(_to_json(result))
    else:
        _print_text(result)

    if save:
        state = State()
        state.init_db()
        state.record_scan(
            scan_id=result.scan_id,
            path=result.project_path,
            mode=result.mode,
            findings_count=len(result.findings),
            grade=result.grade,
            security_score=result.security_score,
            quality_score=result.quality_score,
        )
        console.print(f"\n[dim]Saved: {result.scan_id}[/dim]")

    try:
        from devkit.core.context.manifest import Manifest
        proj_name = Path(path).resolve().name
        Manifest().update_scan(proj_name, result.grade)
    except Exception:
        pass

    if auto_learn and memory_store and result.findings:
        medium_low = [
            f for f in result.findings
            if f.severity in ("medium", "low") and f.memory_match is None
        ]
        if medium_low:
            store_ml = typer.confirm(
                f"\nStore {len(medium_low)} medium/low finding(s) as learned patterns?",
                default=False,
            )
            if store_ml:
                seen: set[str] = set()
                for finding in medium_low:
                    if finding.title in seen:
                        continue
                    seen.add(finding.title)
                    content = (
                        f"Vulnerability pattern: {finding.title}. "
                        f"Found in {finding.file_path}. "
                        f"{finding.plain_english_desc} "
                        f"Fix: {finding.fix_snippet[:200]}. "
                        f"OWASP: {finding.owasp_ref}. CWE: {finding.cwe_ref}."
                    )
                    memory_store.save(
                        content=content,
                        fact_type="vulnerability_pattern",
                        project=result.project_path,
                        source="scan",
                    )
                typer.echo(f"[OK] Stored {len(seen)} pattern(s) in memory.")


def _print_text(result: ScanResult) -> None:
    graph_label = "YES" if result.graph_guided else "NO"
    console.print()
    console.print(f"[bold]DevKit Security Scan[/bold] — {result.project_path}")
    console.print(
        f"Mode: {result.mode} | Files: {result.files_scanned:,} | "
        f"Lines: {result.lines_scanned:,} | Time: {result.scan_duration_seconds}s"
    )
    console.print(f"Graph-guided: {graph_label}")
    console.print()

    grade_color = {"A": "green", "B": "green", "C": "yellow", "D": "red", "F": "bold red"}.get(
        result.grade, "white"
    )
    console.print(
        f"Grade: [{grade_color}]{result.grade}[/{grade_color}]  |  "
        f"Security: {result.security_score}  |  Quality: {result.quality_score}"
    )
    console.print()

    if not result.findings:
        console.print("[green]No findings. Clean scan.[/green]")
        return

    by_severity: dict[str, list[Finding]] = {}
    for f in result.findings:
        by_severity.setdefault(f.severity, []).append(f)

    idx = 1
    for sev in ("critical", "high", "medium", "low", "info"):
        group = by_severity.get(sev, [])
        if not group:
            continue
        color = _SEVERITY_COLORS[sev]
        console.print(f"[{color}]{sev.upper()} ({len(group)})[/{color}]")
        console.print("─" * 50)
        for finding in group:
            loc = f"{finding.file_path}:{finding.line_start}"
            mem_badge = "  [M] Seen before" if finding.memory_match else ""
            console.print(f"[bold][{idx}] {finding.title}{mem_badge}[/bold] — {loc}")
            if finding.memory_match:
                m = finding.memory_match
                console.print(
                    f"    [dim]Memory match:[/dim] {m.fact.project}"
                    f" ({m.fact.valid_at[:10]}, score: {m.score:.4f})"
                )
            if finding.plain_english_desc:
                console.print(f"    {finding.plain_english_desc}")
            if finding.fix_snippet:
                snippet = finding.fix_snippet[:120].replace("\n", " ")
                console.print(f"    [dim]Fix:[/dim] {snippet}")
            if finding.blast_radius:
                br_list = ", ".join(finding.blast_radius[:4])
                extra = f" +{len(finding.blast_radius) - 4} more" if len(finding.blast_radius) > 4 else ""
                console.print(
                    f"    [dim]Blast radius:[/dim] {len(finding.blast_radius)} files ({br_list}{extra})"
                )
            refs = " | ".join(
                filter(None, [finding.owasp_ref, finding.cwe_ref, f"Source: {finding.source}"])
            )
            if refs:
                console.print(f"    [dim]{refs}[/dim]")
            console.print()
            idx += 1


def _to_json(result: ScanResult) -> str:
    data = {
        "scan_id": result.scan_id,
        "project_path": result.project_path,
        "mode": result.mode,
        "grade": result.grade,
        "security_score": result.security_score,
        "quality_score": result.quality_score,
        "files_scanned": result.files_scanned,
        "lines_scanned": result.lines_scanned,
        "scan_duration_seconds": result.scan_duration_seconds,
        "graph_guided": result.graph_guided,
        "findings": [
            {
                "id": f.id,
                "category": f.category,
                "severity": f.severity,
                "title": f.title,
                "plain_english_desc": f.plain_english_desc,
                "business_impact": f.business_impact,
                "fix_snippet": f.fix_snippet,
                "file_path": f.file_path,
                "line_start": f.line_start,
                "line_end": f.line_end,
                "owasp_ref": f.owasp_ref,
                "cwe_ref": f.cwe_ref,
                "source": f.source,
                "blast_radius": f.blast_radius,
                "memory_match": {
                    "fact_id": f.memory_match.fact.id,
                    "project": f.memory_match.fact.project,
                    "score": f.memory_match.score,
                    "valid_at": f.memory_match.fact.valid_at,
                } if f.memory_match else None,
            }
            for f in result.findings
        ],
    }
    return json.dumps(data, indent=2) + "\n"
