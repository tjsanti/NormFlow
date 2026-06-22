"""NormFlow CLI — Typer application."""

from pathlib import Path
from typing import Optional

from rich.console import Console
from typer import Context

import typer

from . import __version__
from .csv_ops import import_mappings, export_mappings
from .review_service import list_pending, accept_suggestion, edit_suggestion
from .suggest_service import suggest, suggest_batch
from .workspace import init_workspace, workspace_info

app = typer.Typer(
    name="normflow",
    help="CLI-first, human-in-the-loop text normalization workbench.",
    add_completion=False,
)

console = Console()


def __resolve_workspace(ctx: typer.Context) -> Path | None:
    """Resolve and validate the workspace path from the --workspace flag."""
    ws_path = ctx.params.get("workspace")
    if not ws_path:
        return None
    return Path(ws_path).expanduser().resolve()


@app.command()
def version() -> None:
    """Show the NormFlow version."""
    console.print(__version__)


@app.command()
def init(workspace: str = typer.Option(..., "--workspace", help="Path to initialize as a NormFlow project.")) -> None:
    """Initialize a new NormFlow project workspace."""
    ws = init_workspace(workspace)
    console.print(f"[green]Project initialized at: {ws}[/green]")


@app.command()
def info(workspace: str = typer.Option(..., "--workspace", help="Path to the NormFlow project workspace.")) -> None:
    """Show information about a NormFlow project workspace."""
    info = workspace_info(workspace)
    console.print(f"Workspace:  {info['workspace']}")
    console.print(f"Database:   {info['database']}")
    console.print(f"Mappings:   {info['mappings']}")
    console.print(f"Suggestions: {info['suggestions']}")


@app.command(name="import")
def import_cmd(
    workspace: str = typer.Option(..., "--workspace", help="Path to the NormFlow project workspace."),
    csv_path: str = typer.Argument(..., help="Path to the CSV file to import."),
    source_column: str = typer.Option(..., "--source-column", help="CSV header name for raw_text values."),
    target_column: str = typer.Option(..., "--target-column", help="CSV header name for normalized_text values."),
) -> None:
    """Import mappings from a CSV file into the workspace database."""
    try:
        imported, skipped = import_mappings(workspace, csv_path, source_column, target_column)
        console.print(f"[green]Imported {imported} new mappings. {skipped} skipped.[/green]")
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


@app.command(name="export")
def export_cmd(
    workspace: str = typer.Option(..., "--workspace", help="Path to export mappings to."),
    csv_path: str = typer.Argument(..., help="Path to export mappings to."),
    source_column: str = typer.Option("raw_text", "--source-column", help="CSV header name for raw_text column."),
    target_column: str = typer.Option("normalized_text", "--target-column", help="CSV header name for normalized_text column."),
) -> None:
    """Export mappings from the workspace database to a CSV file."""
    try:
        count = export_mappings(workspace, csv_path, source_column, target_column)
        console.print(f"[green]Exported {count} mappings to {csv_path}[/green]")
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


@app.command(name="suggest")
def suggest_cmd(
    workspace: str = typer.Option(..., "--workspace", help="Path to the NormFlow project workspace."),
    raw_text: str = typer.Argument(..., help="The raw text value to find suggestions for."),
    limit: int = typer.Option(1, "--limit", help="Maximum number of suggestions to return."),
    no_semantic: bool = typer.Option(False, "--no-semantic", help="Disable semantic matching fallback."),
    semantic_threshold: float = typer.Option(0.85, "--semantic-threshold", help="Minimum cosine similarity for semantic matches."),
) -> None:
    """Return normalization suggestions for a single raw text value."""
    try:
        result = suggest(
            workspace, raw_text, limit=limit,
            semantic=not no_semantic,
            semantic_threshold=semantic_threshold,
        )
        print(result.model_dump_json(indent=2))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


@app.command(name="suggest-batch")
def suggest_batch_cmd(
    workspace: str = typer.Option(..., "--workspace", help="Path to the NormFlow project workspace."),
    csv_path: str = typer.Argument(..., help="Path to the CSV file with raw text records."),
    column: str = typer.Option(..., "--column", help="CSV column that holds the raw texts needing mapping."),
    output_column: str = typer.Option("normalized_text", "--output-column", help="Name for the output suggestion column."),
    output: str = typer.Option(None, "--output", help="Path to write the output CSV (defaults to stdout)."),
    no_semantic: bool = typer.Option(False, "--no-semantic", help="Disable semantic matching fallback."),
    semantic_threshold: float = typer.Option(0.85, "--semantic-threshold", help="Minimum cosine similarity for semantic matches."),
) -> None:
    """Batch-suggest normalizations for all rows in a CSV file."""
    try:
        result_csv = suggest_batch(
            workspace, csv_path, column, output_column,
            semantic=not no_semantic,
            semantic_threshold=semantic_threshold,
        )
        if output:
            out_path = Path(output).expanduser().resolve()
            out_path.write_text(result_csv, encoding="utf-8")
            console.print(f"[green]Wrote suggestions to {out_path}[/green]")
        else:
            print(result_csv, end="")
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


# ---- review command group ----

review_app = typer.Typer(
    name="review",
    help="Review normalization suggestions.",
)


@review_app.command(name="list")
def list_suggestions(
    workspace: str = typer.Option(..., "--workspace", help="Path to the NormFlow project workspace."),
    json: bool = typer.Option(False, "--json", help="Output as JSON instead of a table."),
) -> None:
    """List pending suggestions awaiting review."""
    try:
        items = list_pending(workspace)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None

    if json:
        import json as _json
        print(_json.dumps(items, indent=2))
    else:
        from rich.table import Table
        table = Table()
        table.add_column("ID", style="cyan")
        table.add_column("raw_text")
        table.add_column("suggested_text")
        for item in items:
            table.add_row(str(item["id"]), item["raw_text"], item["suggested_text"])
        console.print(table)


@review_app.command()
def accept(
    workspace: str = typer.Option(..., "--workspace", help="Path to the NormFlow project workspace."),
    record_id: int = typer.Option(..., "--record-id", help="ID of the suggestion to accept."),
) -> None:
    """Accept a suggestion, inserting it into the mapping library."""
    try:
        accept_suggestion(workspace, record_id)
        console.print(f"[green]Suggestion {record_id} accepted.[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


@review_app.command()
def edit(
    workspace: str = typer.Option(..., "--workspace", help="Path to the NormFlow project workspace."),
    record_id: int = typer.Option(..., "--record-id", help="ID of the suggestion to edit."),
    normalized_text: str = typer.Option(..., "--normalized-text", help="Edited normalized text to store."),
) -> None:
    """Accept a suggestion with an edit, inserting the edited text into the mapping library."""
    try:
        edit_suggestion(workspace, record_id, normalized_text)
        console.print(f"[green]Suggestion {record_id} accepted with edit.[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


app.add_typer(review_app, name="review")


# ---- index command group ----

index_app = typer.Typer(
    name="index",
    help="Manage the semantic search index.",
)


@index_app.command(name="build")
def index_build(
    workspace: str = typer.Option(..., "--workspace", help="Path to the NormFlow project workspace."),
) -> None:
    """Build or rebuild the FAISS semantic search index from current mappings."""
    try:
        from .semantic_index import SemanticIndex
        idx = SemanticIndex(workspace)
        count = idx.build()
        console.print(f"[green]Index built with {count} entries.[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


@index_app.command(name="clear")
def index_clear(
    workspace: str = typer.Option(..., "--workspace", help="Path to the NormFlow project workspace."),
) -> None:
    """Remove the persisted FAISS index."""
    try:
        from .semantic_index import SemanticIndex
        idx = SemanticIndex(workspace)
        idx.clear()
        console.print("[green]Index cleared.[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


app.add_typer(index_app, name="index")
