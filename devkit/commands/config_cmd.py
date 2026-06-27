from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from devkit.config import DEVKIT_DIR, Config

console = Console()
config_app = typer.Typer(help="Manage API keys and settings.", no_args_is_help=True)

_SENSITIVE = {"anthropic_api_key"}
_HIDDEN = "****"


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config key to set"),
    value: str = typer.Argument(..., help="Value to assign"),
) -> None:
    """Set a config value."""
    key = key.lower()
    cfg = Config()
    cfg.set(key, value)
    display = _HIDDEN if key in _SENSITIVE else value
    console.print(f"[green]OK[/green] {key} = {display}")


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Config key to retrieve"),
) -> None:
    """Get a config value."""
    key = key.lower()
    cfg = Config()
    value = cfg.get(key)
    if value is None:
        console.print(f"[yellow]{key}[/yellow] is not set")
        raise typer.Exit(code=1)
    display = _HIDDEN if key in _SENSITIVE else str(value)
    console.print(f"{key} = {display}")


@config_app.command("list")
def config_list() -> None:
    """List all config values."""
    cfg = Config()
    table = Table(title="DevKit Config  (~/.devkit/config.json)", show_header=True)
    table.add_column("Key", style="bold")
    table.add_column("Value")
    table.add_column("Source")

    for key in cfg.DEFAULTS:
        value = cfg.get(key)
        source = "[green]set[/green]" if cfg.is_set(key) else "[dim]default[/dim]"
        if key in _SENSITIVE and value:
            display = _HIDDEN
        elif value is None:
            display = "[dim]not set[/dim]"
        else:
            display = str(value)
        table.add_row(key, display, source)

    console.print(table)
    console.print(f"\n[dim]{DEVKIT_DIR / 'config.json'}[/dim]")
