# NormFlow

CLI-first, human-in-the-loop text normalization workbench.

Import approved `raw_text → normalized_text` mappings, get suggestions for new records, review and edit them, then feed accepted changes back into your mapping library.

**Current state:** Workspace init, CSV import/export, exact-match suggestions, batch CSV suggestions, and human review workflow (accept/edit).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python project manager)
- Python ≥ 3.13

## Setup

```bash
git clone <repo-url>
cd normflow
uv sync
```

## Usage

### Initialize a project workspace

```bash
uv run normflow init --workspace <path>
```

Creates a project directory with:

| Item | Purpose |
|------|---------|
| `normflow.db` | SQLite database — source of truth for mappings and suggestions |
| `input/` | Raw text records awaiting normalization |
| `output/` | Results after normalization |
| `samples/` | Portable flat files for demos, seed data, and evaluation fixtures |

### Check workspace status

```bash
uv run normflow info --workspace <path>
```

Shows the workspace path, database location, and current counts of mappings and suggestions.

### Import mappings from CSV

```bash
uv run normflow import --workspace <path> --source-column source --target-column target mappings.csv
```

Reads a CSV file with a header row. Each row becomes a `raw_text → normalized_text` mapping in the database.
- `--source-column` and `--target-column` are required.
- Duplicate sources (already in the database) are skipped.
- Empty rows and whitespace are handled gracefully.

### Export mappings to CSV

```bash
uv run normflow export --workspace <path> mappings.csv [--source-column raw_text] [--target-column normalized_text]
```

Writes all current mappings to a CSV file. Column names default to `raw_text` and `normalized_text` but can be overridden with `--source-column` and `--target-column`.

### Get normalization suggestions

```bash
uv run normflow suggest --workspace <path> "raw text value"
```

Queries the mapping library for an exact match on the raw text. Returns JSON with suggestions, method used, and confidence score.

```bash
uv run normflow suggest --workspace <path> "colour" --limit 10
```

### Batch-suggest normalizations for a CSV

```bash
uv run normflow suggest-batch --workspace <path> records.csv --column name
```

Reads every row from a CSV, looks up exact-match suggestions for the specified column, and outputs a CSV with the original columns plus a `normalized_text` column containing the top suggestion (blank if no match).

- `--column` is required — the CSV column holding the raw texts to normalize.
- `--output-column` (default: `normalized_text`) sets the name of the suggestion column in the output.
- `--output` writes the result to a file instead of stdout.
- Entirely blank rows are excluded; partial rows are included with a blank suggestion.

```bash
uv run normflow suggest-batch --workspace <path> records.csv --column product_name --output results.csv
```

### Review suggestions

List pending suggestions awaiting review:

```bash
uv run normflow review list --workspace <path>
```

Shows a table of pending suggestions with their ID, raw text, and suggested normalized text. Add `--json` for machine-readable output.

Accept a suggestion as-is:

```bash
uv run normflow review accept --workspace <path> --record-id 1
```

Marks the suggestion as accepted and inserts `raw_text → suggested_text` into the mapping library.

Accept a suggestion with an edit:

```bash
uv run normflow review edit --workspace <path> --record-id 1 --normalized-text "Oxygen Sensor"
```

Marks the suggestion as accepted with edits and inserts `raw_text → normalized_text` (your edited text) into the mapping library.

- Once a suggestion is reviewed, it cannot be reviewed again — commands fail clearly if the record is already accepted.
- If a suggestion is not a good fit, edit it to the text you want rather than rejecting it.

### Show version

```bash
uv run normflow version
```

## Project structure

```
src/normflow/
├── __init__.py        # Package entry, __version__
├── __main__.py        # python -m normflow
├── cli.py             # Typer CLI: version, init, info, import, export, suggest, suggest-batch, review
├── csv_ops.py         # CSV import/export operations
├── suggest_service.py # Exact-match and batch suggestion service
├── review_service.py  # Accept, edit, and list pending suggestions
├── models.py          # SQLModel domain models
└── workspace.py       # Workspace init and info operations
tests/
├── test_cli.py                # CLI tests
└── test_workspace_service.py  # Workspace service tests
```

## Roadmap

- [x] Project skeleton (this release)
- [x] Import/export mappings (CSV)
- [x] Exact matching suggestions
- [x] Batch CSV suggestions
- [ ] Semantic search with embeddings
- [ ] LLM fallback
- [x] Human review workflow (accept/edit)
- [ ] TypeScript web UI
