from __future__ import annotations
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import rich.box
import typer
from rich.console import Console
from rich.table import Table

eval_app = typer.Typer(
    name="eval",
    help="Token optimization via Headroom proxy.",
    no_args_is_help=True,
)
_console = Console()

from devkit.paths import SUGGESTIONS_DIR as _SUGGESTIONS_DIR


def _api_key() -> str | None:
    try:
        from devkit.config import Config
        return Config().get("anthropic_api_key")
    except Exception:
        return None


def _load_suggestions() -> list[dict]:
    if not _SUGGESTIONS_DIR.exists():
        return []
    results = []
    for path in sorted(_SUGGESTIONS_DIR.glob("candidate-*.json")):
        try:
            results.append(json.loads(path.read_text()))
        except Exception:
            continue
    return results


def _find_suggestion(candidate_id: str) -> tuple[Path, dict] | None:
    """Find a suggestion by full or prefix ID. Returns (path, data) or None."""
    if not _SUGGESTIONS_DIR.exists():
        return None
    for path in _SUGGESTIONS_DIR.glob(f"candidate-{candidate_id}*.json"):
        try:
            return path, json.loads(path.read_text())
        except Exception:
            continue
    return None


@eval_app.command("start")
def start(
    port: int = typer.Option(8787, "--port", help="Port for Headroom proxy."),
) -> None:
    """Start the Headroom proxy."""
    import shutil
    from devkit.core.eval.headroom_bridge import HeadroomBridge

    if not shutil.which("headroom"):
        typer.echo("[!] headroom not found. Run: pip install -e '.[eval]'")
        raise typer.Exit(1)

    bridge = HeadroomBridge(port=port)

    if bridge.is_running():
        typer.echo(f"Headroom is already running on port {port}.")
        typer.echo("Use: devkit eval status")
        return

    typer.echo(f"Starting Headroom on port {port}...")
    proc = bridge.start()
    if proc is None:
        typer.echo("[!] headroom not found. Run: pip install -e '.[eval]'")
        raise typer.Exit(1)

    time.sleep(3)

    if bridge.is_running():
        typer.echo("[OK] Headroom proxy started.")
        typer.echo(bridge.setup_instructions())
    else:
        typer.echo("[WARN] Proxy may still be starting. Check: devkit eval status")


@eval_app.command("stop")
def stop() -> None:
    """Stop the Headroom proxy."""
    from devkit.core.eval.headroom_bridge import HeadroomBridge
    bridge = HeadroomBridge()
    killed = bridge.stop()
    if killed:
        typer.echo("[OK] Headroom proxy stopped.")
    else:
        typer.echo("No running Headroom process found (no PID file).")


@eval_app.command("status")
def status() -> None:
    """Show Headroom proxy status and live stats."""
    from devkit.core.eval.headroom_bridge import HeadroomBridge, PID_FILE, _read_pid_file

    pf = _read_pid_file()
    port = pf[1] if pf else 8787
    bridge = HeadroomBridge(port=port)
    running = bridge.is_running()

    typer.echo(f"Status:  {'RUNNING' if running else 'STOPPED'}")
    if pf:
        typer.echo(f"PID:     {pf[0]}")
        typer.echo(f"Port:    {pf[1]}")

    if not running:
        typer.echo("Start with: devkit eval start")
        return

    stats = bridge.get_stats()
    if not stats:
        typer.echo("(Could not fetch stats from proxy)")
        return

    table = Table(title="Headroom Stats", box=rich.box.ASCII, show_header=True)
    table.add_column("Metric", no_wrap=True)
    table.add_column("Value")

    def _add_scalar(prefix: str, obj: dict, depth: int = 0) -> None:
        if depth > 2:
            return
        for k, v in obj.items():
            label = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (int, float, str, bool)):
                table.add_row(label, str(v))
            elif isinstance(v, dict) and depth < 2:
                _add_scalar(label, v, depth + 1)

    _add_scalar("", stats)
    _console.print(table)


@eval_app.command("report")
def report() -> None:
    """Show Headroom session savings and ablation suggestions."""
    from devkit.core.eval.headroom_bridge import HeadroomBridge, _read_pid_file

    pf = _read_pid_file()
    port = pf[1] if pf else 8787
    bridge = HeadroomBridge(port=port)

    # Session savings
    calls = bridge.get_session_calls()
    savings = bridge.compute_session_savings(calls)

    s_table = Table(title="Session Savings", box=rich.box.ASCII, show_header=False)
    s_table.add_column("Metric")
    s_table.add_column("Value")
    s_table.add_row("Calls logged", str(savings["calls"]))
    s_table.add_row("Input tokens (original)", str(savings["total_input_tokens"]))
    s_table.add_row("Input tokens (after compression)", str(savings["total_compressed_tokens"]))
    s_table.add_row("Tokens saved", str(savings["tokens_saved"]))
    ratio_pct = f"{savings['compression_ratio']:.1%}"
    s_table.add_row("Compression ratio", ratio_pct)
    s_table.add_row("Est. cost saved (USD)", f"${savings['estimated_cost_saved_usd']:.4f}")
    _console.print(s_table)

    if savings["calls"] == 0:
        typer.echo("No session calls found in ~/.headroom/logs/proxy.log")
        typer.echo("Route Claude Code through Headroom first: devkit eval start")

    # Ablation suggestions
    suggestions = _load_suggestions()
    if not suggestions:
        typer.echo("\nNo ablation suggestions yet.")
        typer.echo("Generate some with: devkit eval ablate <request.json | transcript.jsonl>")
        return

    a_table = Table(title="Ablation Suggestions", box=rich.box.ASCII, show_header=True)
    a_table.add_column("ID")
    a_table.add_column("Tokens")
    a_table.add_column("Verified")
    a_table.add_column("Verdict")
    a_table.add_column("Chunk Preview")

    for s in suggestions:
        preview = s.get("chunk_preview", "")[:50].replace("\n", " ").replace("\r", "")
        a_table.add_row(
            s.get("id", "?"),
            str(s.get("tokens_saved", 0)),
            "yes" if s.get("verified") else "no",
            s.get("verdict") or "-",
            preview,
        )

    _console.print(a_table)
    typer.echo(f"\nVerify a suggestion: devkit eval verify <id>")


@eval_app.command("learn")
def learn() -> None:
    """Run `headroom learn --verbosity` to train verbosity detection."""
    import shutil
    exe = shutil.which("headroom")  # headroom does not support `python -m headroom`
    if not exe:
        typer.echo("[!] headroom not found. Run: pip install -e '.[eval]'")
        raise typer.Exit(1)
    result = subprocess.run([exe, "learn", "--verbosity"], check=False)
    raise typer.Exit(result.returncode)


@eval_app.command("ablate")
def ablate_cmd(
    source: Path = typer.Argument(..., exists=True, dir_okay=False,
                                  help="Captured request body (.json) or Claude Code transcript (.jsonl)."),
    max_chunks: int = typer.Option(8, "--max-chunks", help="Ablate at most N chunks (largest first)."),
    min_tokens: int = typer.Option(100, "--min-tokens", help="Ignore chunks smaller than this."),
    model: Optional[str] = typer.Option(None, "--model", help="Replay model (default: fast_model)."),
    samples: int = typer.Option(1, "--samples", help="Judge samples per ordering."),
    no_judge: bool = typer.Option(False, "--no-judge", help="Skip judge; candidates stay unverified."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan and call budget; no API calls."),
) -> None:
    """Ablate context chunks of a captured request and verify with the judge."""
    from devkit.core.eval.ablation import AblationRunner, load_request

    try:
        body, fidelity = load_request(source)
    except (ValueError, json.JSONDecodeError) as exc:
        typer.echo(f"[ERROR] Could not load {source}: {exc}")
        raise typer.Exit(1)

    if fidelity == "transcript":
        typer.echo("[i] Transcript input: system prompt and tool schemas are not recorded there,")
        typer.echo("    so tool calls are replayed as text. Use a captured request body for full fidelity.")

    if dry_run:
        runner = AblationRunner(client=object(), model=model or "-", max_chunks=max_chunks,
                                min_chunk_tokens=min_tokens)
        chunks, rep = runner.plan(body)
        judge_calls = 0 if no_judge else 2 * samples
        typer.echo(f"Chunks: {rep.chunks_considered} found | {rep.skipped_cached} in cached prefix (skipped) | "
                   f"{rep.skipped_small} below {min_tokens} tokens | {len(chunks)} planned")
        for c in chunks:
            typer.echo(f"  {c.location:<40} ~{c.tokens} tokens")
        typer.echo(f"Max API calls: {2 + len(chunks) * (1 + judge_calls)}")
        return

    key = _api_key()
    if not key:
        typer.echo("[ERROR] No API key configured. Run: devkit config set anthropic_api_key <key>")
        raise typer.Exit(1)

    import anthropic
    from devkit.core.eval.judge import ClaudeJudge
    client = anthropic.Anthropic(api_key=key)
    judge = None if no_judge else ClaudeJudge(client=client, model=model, samples=samples)
    runner = AblationRunner(client=client, model=model, judge=judge, max_chunks=max_chunks,
                            min_chunk_tokens=min_tokens, on_progress=lambda m: typer.echo(f"  {m}"))
    rep = runner.run(body, source=str(source), fidelity=fidelity)

    typer.echo(f"\nAPI calls: {rep.api_calls} | baseline noise (Jaccard): {rep.baseline_distance}")
    for err in rep.errors:
        typer.echo(f"[WARN] {err}")
    if not rep.candidates:
        typer.echo("No removable chunks found (every ablation changed the output, or nothing to ablate).")
        return
    safe = [c for c in rep.candidates if c["verdict"] == "safe"]
    typer.echo(f"{len(rep.candidates)} candidate(s); {len(safe)} judged SAFE "
               f"(~{sum(c['tokens_saved'] for c in safe)} tokens removable).")
    typer.echo("Details: devkit eval report")


@eval_app.command("bench")
def bench_cmd(
    dataset: Optional[Path] = typer.Argument(None, help="JSONL dataset (default: bundled judge_bench.jsonl)."),
    samples: int = typer.Option(1, "--samples", help="Judge samples per ordering."),
    model: Optional[str] = typer.Option(None, "--model", help="Judge model (default: fast_model)."),
    output: str = typer.Option("text", "--output", help="text | json"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate the dataset and print the call budget."),
) -> None:
    """Benchmark the judge on labeled cases: accuracy, false-SAFE rate, order agreement."""
    from devkit.core.eval.bench import DEFAULT_DATASET, load_dataset, run_bench, save_report

    path = dataset or DEFAULT_DATASET
    try:
        cases = load_dataset(path)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        typer.echo(f"[ERROR] {exc}")
        raise typer.Exit(1)
    n_eq = sum(c["label"] == "equivalent" for c in cases)
    if dry_run:
        typer.echo(f"{len(cases)} cases ({n_eq} equivalent, {len(cases) - n_eq} degraded); "
                   f"{len(cases) * 2 * samples} judge calls.")
        return
    key = _api_key()
    if not key:
        typer.echo("[ERROR] No API key configured. Run: devkit config set anthropic_api_key <key>")
        raise typer.Exit(1)
    from devkit.core.eval.judge import ClaudeJudge
    judge = ClaudeJudge(api_key=key, model=model, samples=samples)
    rep = run_bench(judge, cases)
    saved = save_report(rep, judge.model, samples, str(path))
    if output == "json":
        typer.echo(json.dumps(rep.to_dict(), indent=2))
        return
    table = Table(title=f"Judge benchmark ({judge.model}, samples={samples})", box=rich.box.ASCII)
    table.add_column("Metric")
    table.add_column("Value")
    for k, v in rep.summary().items():
        table.add_row(k, str(v))
    _console.print(table)
    for row in rep.rows:
        if not row["correct"]:
            typer.echo(f"  MISS {row['id']:<22} label={row['label']:<10} verdict={row['verdict']}")
    typer.echo(f"Saved: {saved}")


@eval_app.command("verify")
def verify_cmd(
    candidate_id: str = typer.Argument(..., help="Candidate ID or prefix to verify."),
    samples: int = typer.Option(1, "--samples", help="Judge samples per ordering."),
    force: bool = typer.Option(False, "--force", help="Re-judge even if already verified."),
) -> None:
    """Run Claude-as-judge on an ablation suggestion."""
    found = _find_suggestion(candidate_id)
    if found is None:
        typer.echo(f"[ERROR] No suggestion found with ID prefix: {candidate_id}")
        typer.echo(f"List suggestions: devkit eval report")
        raise typer.Exit(1)

    path, candidate = found

    if candidate.get("verified") and not force:
        typer.echo(f"Already verified. Verdict: {candidate.get('verdict')}")
        typer.echo(f"Reasoning: {candidate.get('verdict_reasoning', '-')}")
        return

    key = _api_key()
    if not key:
        typer.echo("[ERROR] No API key configured. Run: devkit config set anthropic_api_key <key>")
        raise typer.Exit(1)

    typer.echo(f"Running Claude-as-judge on suggestion {candidate['id']}...")
    typer.echo(f"Tokens at stake: {candidate.get('tokens_saved', 0)}")

    from devkit.core.eval.judge import ClaudeJudge, JudgeVerdict
    judge = ClaudeJudge(api_key=key, samples=samples)

    result = judge.judge(
        candidate.get("original_output", ""),
        candidate.get("reduced_output", ""),
        candidate.get("task", ""),
    )
    verdict, reasoning = result.verdict, result.reasoning

    candidate["verified"] = True
    candidate["verdict"] = verdict.value
    candidate["verdict_reasoning"] = reasoning
    try:
        path.write_text(json.dumps(candidate, indent=2))
    except Exception:
        pass

    verdict_label = {
        JudgeVerdict.SAFE: "[SAFE] Chunk can be safely removed.",
        JudgeVerdict.UNSAFE: "[UNSAFE] Removing this chunk degrades output.",
        JudgeVerdict.INCONCLUSIVE: "[INCONCLUSIVE] Not safe to apply (disagreement or judge error).",
    }[verdict]

    typer.echo(verdict_label)
    typer.echo(f"Reasoning: {reasoning}")


@eval_app.command("versions")
def versions(
    project: str = typer.Option("", "--project", "-p", help="Filter by project name."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows to show."),
    save: Optional[Path] = typer.Option(None, "--save", exists=True, dir_okay=False,
                                        help="Snapshot this prompt file as a new version."),
) -> None:
    """Show (or save) prompt version history in the memory store."""
    from devkit.core.eval.versions import list_versions, save_version

    if save is not None:
        proj = project or Path.cwd().name
        fid = save_version(save.read_text(encoding="utf-8"), proj)
        if fid is None:
            typer.echo("[!] Could not save version (needs: pip install -e '.[memory]').")
            raise typer.Exit(1)
        typer.echo(f"[OK] Saved prompt version {str(fid)[:8]} for project '{proj}'")
        return

    rows = list_versions(project=project or None, limit=limit)

    if not rows:
        if project:
            typer.echo(f"No prompt versions found for project: {project}")
        else:
            typer.echo("No prompt versions found.")
            typer.echo("Save one with: devkit eval versions --save <prompt-file> --project <name>")
        return

    table = Table(title="Prompt Versions", box=rich.box.ASCII, show_header=True)
    table.add_column("ID", no_wrap=True)
    table.add_column("Project")
    table.add_column("Created")
    table.add_column("Preview")

    for row in rows:
        preview = row["content"][:60].replace("\n", " ").replace("\r", "")
        table.add_row(
            row["id"][:8],
            row["project"],
            row["created_at"][:19],
            preview,
        )

    _console.print(table)
