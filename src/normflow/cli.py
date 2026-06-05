"""NormFlow CLI — Typer application."""

from rich.console import Console

import typer

from . import __version__
from .workspace import init_workspace, workspace_info

app = typer.Typer(
    name="normflow",
    help="CLI-first, human-in-the-loop text normalization workbench.",
    add_completion=False,
)

console = Console()


@app.command()
def version() -> None:
    """Show the NormFlow version."""
    console.print(__version__)


@app.command()
def init(workspace_path: str) -> None:
    """Initialize a new NormFlow project workspace."""
    ws = init_workspace(workspace_path)
    console.print(f"[green]Project initialized at: {ws}[/green]")


@app.command()
def info(workspace_path: str) -> None:
    """Show information about a NormFlow project workspace."""
    info = workspace_info(workspace_path)
    console.print(f"Workspace:  {info['workspace']}")
    console.print(f"Database:   {info['database']}")
    console.print(f"Mappings:   {info['mappings']}")
    console.print(f"Suggestions: {info['suggestions']}")
