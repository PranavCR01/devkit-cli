from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import rich.box
import typer
from rich.console import Console
from rich.table import Table

_console = Console()


def metrics_cmd(
    period: str = typer.Option("week", "--period", help="week | month"),
    since: Optional[str] = typer.Option("90d", "--since", help="YYYY-MM-DD or Nd (default 90d)"),
    repo: Optional[str] = typer.Option(None, "--repo", help="owner/name for gh (default: current repo)"),
    prs: Optional[Path] = typer.Option(None, "--prs", exists=True, dir_okay=False,
                                       help="Saved `gh pr list --json number,title,headRefName,"
                                            "createdAt,mergedAt,state` export instead of calling gh"),
    transcripts: Optional[List[Path]] = typer.Option(None, "--transcripts",
                                                     help="Transcript .jsonl files or dirs "
                                                          "(default: ~/.claude/projects)"),
    project: Optional[str] = typer.Option(None, "--project",
                                          help="Only count AI usage from sessions in this directory name"),
    prices: Optional[Path] = typer.Option(None, "--prices", exists=True, dir_okay=False,
                                          help='JSON {"model-prefix": [in, out, cache_read]} USD/MTok'),
    output: str = typer.Option("text", "--output", help="text | json | md"),
) -> None:
    """PR throughput (opened/merged/cycle time) joined with AI cost per merged PR."""
    from devkit.core.metrics import throughput as tp

    if period not in ("week", "month"):
        typer.echo("[ERROR] --period must be week or month")
        raise typer.Exit(1)
    try:
        since_dt = tp.since_from(since, period)
    except ValueError as exc:
        typer.echo(f"[ERROR] {exc}")
        raise typer.Exit(1)

    try:
        pr_list = tp.load_prs_from_json(json.loads(prs.read_text())) if prs else tp.fetch_prs_gh(repo)
    except (RuntimeError, json.JSONDecodeError, OSError) as exc:
        typer.echo(f"[ERROR] Could not load PRs: {exc}")
        raise typer.Exit(1)

    paths: list[Path] = []
    for t in transcripts or []:
        paths += sorted(t.glob("**/*.jsonl")) if t.is_dir() else [t]
    if not transcripts:
        paths = tp.default_transcript_paths()
    price_table = dict(tp.PRICES)
    if prices:
        price_table.update({k: tuple(v) for k, v in json.loads(prices.read_text()).items()})
    usage, skipped = tp.load_usage(paths, price_table, project_filter=project)

    report = tp.compute(pr_list, usage, period=period, since=since_dt)
    try:
        from devkit.core.handoff.ledger import LEDGER_DB, Ledger
        if LEDGER_DB.exists():
            report["orchestration"] = Ledger().stats(project)
    except Exception:
        pass
    report["sources"] = {"transcript_files": len(paths), "usage_records": len(usage),
                         "unparseable_lines": skipped, "prs": len(pr_list)}

    if output == "json":
        typer.echo(json.dumps(report, indent=2))
        return
    if output == "md":
        typer.echo(tp.to_markdown(report))
        return

    table = Table(title=f"Delivery & AI cost by {period}", box=rich.box.ASCII)
    for col in ("Period", "Opened", "Merged", "Median cycle h", "AI cost", "Cost/merged PR", "Sessions"):
        table.add_column(col)
    for r in report["rows"]:
        table.add_row(
            r["period"], str(r["prs_opened"]), str(r["prs_merged"]),
            "-" if r["median_cycle_hours"] is None else str(r["median_cycle_hours"]),
            f"${r['ai_cost_usd']:.2f}",
            "-" if r["ai_cost_per_merged_pr"] is None else f"${r['ai_cost_per_merged_pr']:.2f}",
            str(r["sessions"]),
        )
    _console.print(table)
    t = report["totals"]
    rate = "n/a" if t["attribution_rate"] is None else f"{t['attribution_rate']:.0%}"
    typer.echo(f"AI cost ${t['ai_cost_usd']:.2f} | attributed to PRs {rate} | "
               f"unattributed ${t['ai_cost_unattributed']:.2f}")
    if t["unpriced_tokens"]:
        typer.echo(f"[i] {t['unpriced_tokens']:,} tokens from models without a price entry (use --prices)")
    for p in report["top_prs_by_cost"]:
        typer.echo(f"  #{p['number']:<5} ${p['ai_cost_usd']:>7.2f}  {p['title'][:60]}")
    if report.get("orchestration"):
        o = report["orchestration"]
        typer.echo(f"Agent tasks: {o['completed']} done | {o['handoffs']} handoffs | "
                   f"{o['recoveries']} recoveries | {o['integration_conflicts']} conflicts | "
                   f"{o['manual_interventions']} interventions")
    typer.echo("Costs are list-price estimates; transcript format is internal to Claude Code.")
