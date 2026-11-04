import typer

from devkit.commands.config_cmd import config_app
from devkit.commands.context_cmd import context_app
from devkit.commands.eval_cmd import eval_app
from devkit.commands.fork_cmd import fork_app
from devkit.commands.memory_cmd import memory_app
from devkit.commands.metrics_cmd import metrics_cmd
from devkit.commands.scan import scan
from devkit.commands.search_cmd import search_cmd

app = typer.Typer(
    name="devkit",
    help="DevKit -- security scanning, memory, search, context, fork, eval, metrics, and handoff.",
    no_args_is_help=True,
)

app.add_typer(config_app, name="config")
app.add_typer(memory_app, name="memory")
app.add_typer(context_app, name="context")
app.add_typer(fork_app, name="fork")
app.add_typer(eval_app, name="eval")
app.command()(scan)
app.command(name="search")(search_cmd)
app.command(name="metrics")(metrics_cmd)


HOOK_COMMAND = "devkit context inject --format hook 2>/dev/null || true"


@app.command()
def init(
    register_hook: bool = typer.Option(
        False, "--register-hook",
        help="Also register the SessionStart hook in ~/.claude/settings.json "
             "(skip this if you installed DevKit as a Claude Code plugin).",
    ),
) -> None:
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
    if register_hook:
        from devkit.paths import claude_home
        changed = register_session_hook(claude_home() / "settings.json")
        typer.echo("[OK] SessionStart hook registered in Claude Code settings"
                   if changed else "[OK] SessionStart hook already registered")
    else:
        typer.echo("  To enable auto-injection: devkit init --register-hook  (or install the plugin)")

    typer.echo(f"[OK] Initialized {DEVKIT_DIR}")
    if created:
        typer.echo("  Run: devkit config set ANTHROPIC_API_KEY <your-key>")


def _write_session_hook(hook_path) -> None:
    """Shell wrapper for users who prefer to reference a script path in settings."""
    from pathlib import Path as _Path

    hook_path = _Path(str(hook_path))
    hook_path.write_text(
        "#!/bin/bash\n"
        "# DevKit SessionStart hook: prints pinned + retrieved context for this project.\n"
        "# Claude Code passes the session JSON (incl. cwd) on stdin; inject reads it.\n"
        f"{HOOK_COMMAND}\n",
        encoding="utf-8",
    )
    typer.echo(f"[OK] Session hook script written: {hook_path}")


def register_session_hook(settings_path) -> bool:
    """Idempotently add the DevKit SessionStart hook to a Claude Code settings file.

    Returns True if the file was changed. Existing settings and hooks are preserved.
    """
    import json
    from pathlib import Path as _Path

    settings_path = _Path(str(settings_path))
    data: dict = {}
    if settings_path.exists():
        text = settings_path.read_text(encoding="utf-8").strip()
        data = json.loads(text) if text else {}
    groups = data.setdefault("hooks", {}).setdefault("SessionStart", [])
    for group in groups:
        for hook in group.get("hooks", []):
            if "devkit context inject" in str(hook.get("command", "")):
                return False
    groups.append({"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 30}]})
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


if __name__ == "__main__":
    app()
