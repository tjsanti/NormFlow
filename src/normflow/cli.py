"""NormFlow CLI — Typer application."""

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

import typer

from . import __version__
from .mapping_service import MappingService
from .workspace import init_workspace

app = typer.Typer(
    name="normflow",
    help="CLI-first, human-in-the-loop text normalization workbench.",
    add_completion=False,
)

console = Console()

_ws_opt = typer.Option(..., "--workspace", "-w", help="Path to the NormFlow project workspace.")


def _ms(workspace: str) -> MappingService:
    """Get a MappingService for the workspace."""
    return MappingService(workspace)


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
def info(workspace: str = _ws_opt) -> None:
    """Show information about a NormFlow project workspace."""
    info = _ms(workspace).workspace_info()
    console.print(f"Workspace:  {info['workspace']}")
    console.print(f"Database:   {info['database']}")
    console.print(f"Mappings:   {info['mappings']}")
    console.print(f"Suggestions: {info['suggestions']}")


@app.command(name="import")
def import_cmd(
    csv_path: str = typer.Argument(..., help="Path to the CSV file to import."),
    source_column: str = typer.Option(..., "--source-column", help="CSV header name for raw_text values."),
    target_column: str = typer.Option(..., "--target-column", help="CSV header name for normalized_text values."),
    workspace: str = _ws_opt,
) -> None:
    """Import mappings from a CSV file into the workspace database."""
    try:
        imported, skipped = _ms(workspace).import_mappings(csv_path, source_column, target_column)
        console.print(f"[green]Imported {imported} new mappings. {skipped} skipped.[/green]")
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


@app.command(name="export")
def export_cmd(
    csv_path: str = typer.Argument(..., help="Path to export mappings to."),
    source_column: str = typer.Option("raw_text", "--source-column", help="CSV header name for raw_text column."),
    target_column: str = typer.Option("normalized_text", "--target-column", help="CSV header name for normalized_text column."),
    workspace: str = _ws_opt,
) -> None:
    """Export mappings from the workspace database to a CSV file."""
    try:
        count = _ms(workspace).export_mappings(csv_path, source_column, target_column)
        console.print(f"[green]Exported {count} mappings to {csv_path}[/green]")
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


@app.command(name="suggest")
def suggest_cmd(
    raw_text: str = typer.Argument(..., help="The raw text value to find suggestions for."),
    limit: int = typer.Option(1, "--limit", help="Maximum number of suggestions to return."),
    no_semantic: bool = typer.Option(False, "--no-semantic", help="Disable semantic matching fallback."),
    semantic_threshold: float = typer.Option(0.85, "--semantic-threshold", help="Minimum cosine similarity for semantic matches."),
    workspace: str = _ws_opt,
) -> None:
    """Return normalization suggestions for a single raw text value."""
    try:
        items = _ms(workspace).lookup(
            raw_text, semantic=not no_semantic, threshold=semantic_threshold, limit=limit,
        )
        import json
        print(json.dumps({"raw_text": raw_text, "suggestions": [s.model_dump() for s in items]}, indent=2))
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


@app.command(name="suggest-batch")
def suggest_batch_cmd(
    csv_path: str = typer.Argument(..., help="Path to the CSV file with raw text records."),
    column: str = typer.Option(..., "--column", help="CSV column that holds the raw texts needing mapping."),
    output_column: str = typer.Option("normalized_text", "--output-column", help="Name for the output suggestion column."),
    output: str = typer.Option(None, "--output", help="Path to write the output CSV (defaults to stdout)."),
    no_semantic: bool = typer.Option(False, "--no-semantic", help="Disable semantic matching fallback."),
    semantic_threshold: float = typer.Option(0.85, "--semantic-threshold", help="Minimum cosine similarity for semantic matches."),
    workspace: str = _ws_opt,
) -> None:
    """Batch-suggest normalizations for all rows in a CSV file."""
    try:
        result_csv = _ms(workspace).lookup_batch(
            csv_path, column, output_column, semantic=not no_semantic, threshold=semantic_threshold,
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
    as_json: bool = typer.Option(False, "--json", help="Output as JSON instead of a table."),
    workspace: str = _ws_opt,
) -> None:
    """List pending suggestions awaiting review."""
    try:
        items = _ms(workspace).list_pending_suggestions()
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None

    if as_json:
        print(json.dumps(items, indent=2))
    else:
        table = Table()
        table.add_column("ID", style="cyan")
        table.add_column("raw_text")
        table.add_column("suggested_text")
        for item in items:
            table.add_row(str(item["id"]), item["raw_text"], item["suggested_text"])
        console.print(table)


@review_app.command()
def accept(
    record_id: int = typer.Option(..., "--record-id", help="ID of the suggestion to accept."),
    workspace: str = _ws_opt,
) -> None:
    """Accept a suggestion, inserting it into the mapping library."""
    try:
        _ms(workspace).accept_suggestion(record_id)
        console.print(f"[green]Suggestion {record_id} accepted.[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


@review_app.command()
def edit(
    record_id: int = typer.Option(..., "--record-id", help="ID of the suggestion to edit."),
    normalized_text: str = typer.Option(..., "--normalized-text", help="Edited normalized text to store."),
    workspace: str = _ws_opt,
) -> None:
    """Accept a suggestion with an edit, inserting the edited text into the mapping library."""
    try:
        _ms(workspace).edit_suggestion(record_id, normalized_text)
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
def index_build(workspace: str = _ws_opt) -> None:
    """Build or rebuild the FAISS semantic search index from current mappings."""
    try:
        count = _ms(workspace).build_index()
        console.print(f"[green]Index built with {count} entries.[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


@index_app.command(name="clear")
def index_clear(workspace: str = _ws_opt) -> None:
    """Remove the persisted FAISS index."""
    try:
        _ms(workspace).clear_index()
        console.print("[green]Index cleared.[/green]")
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None


app.add_typer(index_app, name="index")
