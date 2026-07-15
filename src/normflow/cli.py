"""NormFlow CLI — Typer application."""
from pathlib import Path

import typer

from . import __version__
from .llm_config import load_llm_config
from .mapping_service import MappingService
from .project import resolve_project
from .project_service import init_project

app = typer.Typer(
    name="normflow",
    help="CLI-first, human-in-the-loop text normalization workbench.",
    add_completion=False,
)

def _project_service() -> MappingService:
    """Return the service for the Project selected by the process directory."""
    project = resolve_project(Path.cwd())
    return MappingService(str(project.root))


def _notify_semantic_refresh(service: MappingService, *, enabled: bool) -> bool:
    """Tell an interactive caller before a lazy semantic refresh may block."""
    if not enabled:
        return False
    status = service.project_info()["semantic_index_status"]
    if status == "fresh":
        return False
    if status == "missing":
        message = "Building semantic index before Suggestions…"
    else:
        message = "Semantic index is out of date; rebuilding before Suggestions…"
    typer.echo(message, err=True)
    return True


def _notify_semantic_refresh_failure(service: MappingService, *, attempted: bool) -> None:
    if not attempted:
        return
    info = service.project_info()
    if info["semantic_index_status"] != "fresh" and info["semantic_index_warning"]:
        typer.echo(info["semantic_index_warning"], err=True)


@app.command()
def version() -> None:
    """Show the NormFlow version."""
    print(__version__)


@app.command()
def ui(
    no_open: bool = typer.Option(False, "--no-open", help="Do not open the default browser."),
    port: int | None = typer.Option(
        None,
        "--port",
        min=1,
        max=65535,
        help="Local port to use (defaults to a free port).",
    ),
) -> None:
    """Launch the browser UI for the active Project."""
    import os
    import socket
    import uvicorn
    import webbrowser

    from .api import create_app

    try:
        project = resolve_project(Path.cwd())
        load_llm_config(project, os.environ)
    except ValueError as exc:
        print(f"Error: {exc}")
        raise typer.Exit(1) from None

    requested_port = port or 0
    try:
        with socket.socket() as local_socket:
            local_socket.bind(("127.0.0.1", requested_port))
            selected_port = port or local_socket.getsockname()[1]
    except OSError as exc:
        description = f"port {port}" if port is not None else "a local port"
        print(f"Error: {description} is unavailable: {exc}")
        raise typer.Exit(1) from None

    url = f"http://127.0.0.1:{selected_port}"
    print(url)
    if not no_open:
        webbrowser.open(url)
    uvicorn.run(create_app(project), host="127.0.0.1", port=selected_port)


@app.command()
def init() -> None:
    """Initialize the current directory as a NormFlow Project."""
    try:
        project_root = init_project(Path.cwd())
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}")
        raise typer.Exit(1) from None
    print(f"Project initialized at: {project_root}")


@app.command()
def info() -> None:
    """Show information about the active NormFlow Project."""
    try:
        project = resolve_project(Path.cwd())
        statistics = MappingService(str(project.root)).project_info()
    except ValueError as exc:
        print(f"Error: {exc}")
        raise typer.Exit(1) from None

    print(f"Project:    {project.root}")
    print(f"Database:   {project.database}")
    print(f"Mappings:   {statistics['mappings']}")
    print(f"Review Items: {statistics['review_items']}")
    print(f"Semantic index: {statistics['semantic_index_status']}")


@app.command(name="import")
def import_cmd(
    csv_path: str = typer.Argument(..., help="Path to the CSV file to import."),
    source_column: str = typer.Option(..., "--source-column", help="CSV header name for raw_text values."),
    target_column: str = typer.Option(..., "--target-column", help="CSV header name for normalized_text values."),
) -> None:
    """Import Mappings from a CSV file into the active Project."""
    try:
        imported, skipped = _project_service().import_mappings(
            csv_path, source_column, target_column,
        )
        print(f"Imported {imported} new mappings. {skipped} skipped.")
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None


@app.command(name="export")
def export_cmd(
    csv_path: str = typer.Argument(..., help="Path to export mappings to."),
    source_column: str = typer.Option("raw_text", "--source-column", help="CSV header name for raw_text column."),
    target_column: str = typer.Option("normalized_text", "--target-column", help="CSV header name for normalized_text column."),
) -> None:
    """Export Mappings from the active Project to a CSV file."""
    try:
        count = _project_service().export_mappings(
            csv_path, source_column, target_column,
        )
        print(f"Exported {count} mappings to {csv_path}")
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None


@app.command(name="suggest")
def suggest_cmd(
    raw_text: str = typer.Argument(..., help="The raw text value to find suggestions for."),
    limit: int = typer.Option(1, "--limit", help="Maximum number of suggestions to return."),
    no_semantic: bool = typer.Option(False, "--no-semantic", help="Disable semantic matching fallback."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Disable LLM matching fallback."),
    semantic_threshold: float = typer.Option(0.85, "--semantic-threshold", help="Minimum cosine similarity for semantic matches."),
) -> None:
    """Return Suggestions for a single raw text value."""
    try:
        service = _project_service()
        refresh_attempted = _notify_semantic_refresh(
            service, enabled=not no_semantic or not no_llm,
        )
        items = service.lookup(
            raw_text, semantic=not no_semantic, llm=not no_llm, threshold=semantic_threshold, limit=limit,
        )
        _notify_semantic_refresh_failure(service, attempted=refresh_attempted)
        import json as _json
        print(_json.dumps({"raw_text": raw_text, "suggestions": [s.model_dump() for s in items]}, indent=2))
    except ValueError as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None


@app.command(name="suggest-batch")
def suggest_batch_cmd(
    csv_path: str = typer.Argument(..., help="Path to the CSV file with raw text records."),
    column: str = typer.Option(..., "--column", help="CSV column that holds the raw texts needing mapping."),
    output_column: str = typer.Option("normalized_text", "--output-column", help="Name for the output suggestion column."),
    output: str = typer.Option(None, "--output", help="Path to write the output CSV (defaults to stdout)."),
    no_semantic: bool = typer.Option(False, "--no-semantic", help="Disable semantic matching fallback."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Disable LLM matching fallback."),
    semantic_threshold: float = typer.Option(0.85, "--semantic-threshold", help="Minimum cosine similarity for semantic matches."),
) -> None:
    """Suggest normalized text for every row in a CSV file."""
    try:
        service = _project_service()
        refresh_attempted = _notify_semantic_refresh(
            service, enabled=not no_semantic or not no_llm,
        )
        result_csv = service.lookup_batch(
            csv_path, column, output_column, semantic=not no_semantic, llm=not no_llm, threshold=semantic_threshold,
        )
        _notify_semantic_refresh_failure(service, attempted=refresh_attempted)
        if output:
            out_path = Path(output).expanduser().resolve()
            out_path.write_text(result_csv, encoding="utf-8")
            print(f"Wrote suggestions to {out_path}")
        else:
            print(result_csv, end="")
    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None


# ---- review command group ----

review_app = typer.Typer(
    name="review",
    help="Review pending normalization work.",
)


@review_app.command(name="list")
def list_review_items(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON instead of a table."),
) -> None:
    """List pending Review Items."""
    try:
        items = _project_service().list_review_items()
    except ValueError as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None

    if as_json:
        import json as _json
        print(_json.dumps(items, indent=2))
    else:
        for item in items:
            print(f"{item['id']}\t{item['raw_text']}\t{item['suggested_text']}")


@review_app.command()
def accept(
    review_item_id: int = typer.Option(
        ...,
        "--review-item-id",
        help="ID of the Review Item to accept.",
    ),
    normalized_text: str | None = typer.Option(
        None,
        "--normalized-text",
        help="Replacement normalized text to store instead of the Suggestion.",
    ),
) -> None:
    """Accept a Review Item, inserting it into the Mapping library."""
    try:
        _project_service().accept_review_item(review_item_id, normalized_text)
        print(f"Review Item {review_item_id} accepted.")
    except ValueError as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None

app.add_typer(review_app, name="review")


# ---- index command group ----

index_app = typer.Typer(
    name="index",
    help="Manage the semantic search index.",
)


@index_app.command(name="build")
def index_build() -> None:
    """Build or rebuild the FAISS semantic search index from current Mappings."""
    try:
        count = _project_service().build_index()
        print(f"Index built with {count} entries.")
    except ValueError as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None


@index_app.command(name="clear")
def index_clear() -> None:
    """Remove the persisted FAISS index."""
    try:
        _project_service().clear_index()
        print("Index cleared.")
    except ValueError as e:
        print(f"Error: {e}")
        raise typer.Exit(1) from None


app.add_typer(index_app, name="index")
