import typer

from devkit.commands.config_cmd import config_app
from devkit.commands.context_cmd import context_app
from devkit.commands.memory_cmd import memory_app
from devkit.commands.scan import scan
from devkit.commands.search_cmd import search_cmd

app = typer.Typer(
    name="devkit",
    help="DevKit -- security scanning, memory, search, context, fork, and eval.",
    no_args_is_help=True,
)

app.add_typer(config_app, name="config")
app.add_typer(memory_app, name="memory")
app.add_typer(context_app, name="context")
app.command()(scan)
app.command(name="search")(search_cmd)


@app.command()
def init() -> None:
    """Initialize ~/.devkit/ directory structure."""
    from devkit.config import DEVKIT_DIR, Config
    from devkit.state import State

    for d in [
        DEVKIT_DIR,
        DEVKIT_DIR / "graphs",
        DEVKIT_DIR / "blueprints",
        DEVKIT_DIR / "hooks",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    cfg = Config()
    created = cfg.init_if_missing()

    state = State()
    state.init_db()

    try:
        from devkit.core.memory.sqlite_backend import MEMORY_DB, SQLiteBackend
        SQLiteBackend(MEMORY_DB)
        typer.echo("[OK] Memory database initialized")
    except RuntimeError:
        memory_db = DEVKIT_DIR / "memory.db"
        if not memory_db.exists():
            memory_db.touch()
        typer.echo("  Memory extras not installed -- run: pip install -e '.[memory]'")

    try:
        from pathlib import Path as _Path
        from devkit.core.context.manifest import Manifest
        proj_name = _Path.cwd().name
        proj_path = str(_Path.cwd())
        Manifest().register_project(proj_name, proj_path)
        typer.echo(f"[OK] Registered project '{proj_name}' in manifest")
    except Exception:
        pass

    _write_session_hook(DEVKIT_DIR / "hooks" / "session-start.sh")

    typer.echo(f"[OK] Initialized {DEVKIT_DIR}")
    if created:
        typer.echo("  Run: devkit config set ANTHROPIC_API_KEY <your-key>")


def _write_session_hook(hook_path: "typer.Path") -> None:  # type: ignore[name-defined]
    from pathlib import Path as _Path

    hook_path = _Path(str(hook_path))
    content = (
        "#!/bin/bash\n"
        "# DevKit session start hook -- injects context into Claude Code via SessionStart\n"
        "PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)\n"
        'PROJECT_NAME=$(basename "$PROJECT_ROOT")\n'
        "\n"
        'SNAPSHOT=$(python3 -m devkit.cli context inject --project "$PROJECT_NAME" --format hook 2>/dev/null)\n'
        'if [ -n "$SNAPSHOT" ]; then\n'
        '    echo "$SNAPSHOT"\n'
        "fi\n"
    )
    hook_path.write_text(content, encoding="utf-8")
    typer.echo(f"[OK] Session hook written: {hook_path}")
    typer.echo(
        "  Register in ~/.claude/settings.json under hooks.SessionStart to enable injection."
    )


@app.command()
def fork(
    source: str = typer.Argument(..., help="Source project path"),
    target: str = typer.Argument(..., help="Target project path"),
) -> None:
    """Feature transplanting between projects (Slice 5)."""
    typer.echo("fork: not yet implemented")


@app.command()
def eval(
    action: str = typer.Argument(..., help="analyze | compress | dedupe"),
) -> None:
    """Token optimization and evaluation (Slice 6)."""
    typer.echo("eval: not yet implemented")


if __name__ == "__main__":
    app()
