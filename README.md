# NormFlow

CLI-first, human-in-the-loop text normalization workbench.

Import approved `raw_text → normalized_text` mappings, get Suggestions for new records, and resolve pending Review Items into Mappings.

**Current state:** Project init, a local browser UI, CSV import/export, exact-match Suggestions, semantic search (FAISS), batch CSV Suggestions, and a Review Item workflow (accept/edit-and-accept).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python project manager)
- Python ≥ 3.13

## Setup

```bash
git clone <repo-url>
cd normflow
uv sync
```

Include the local server dependencies before launching the browser UI:

```bash
uv sync --extra server
```

## Usage

### Launch the local browser UI

```bash
uv run normflow ui
```

NormFlow prints its localhost URL, opens it in the default browser, and lets you open a Project by entering its folder path. Valid Projects are remembered locally for quick reopening and switching. Use `--no-open` to start the same server without opening a browser.

### Initialize a project workspace

```bash
uv run normflow init --workspace <path>
```

Creates a project directory with:

| Item | Purpose |
|------|---------|
| `normflow.db` | SQLite database — source of truth for Mappings and Review Items |
| `input/` | Raw text records awaiting normalization |
| `output/` | Results after normalization |
| `samples/` | Portable flat files for demos, seed data, and evaluation fixtures |
| `.normflow/` | Internal data (FAISS semantic index) |

### Check workspace status

```bash
uv run normflow info --workspace <path>
```

Shows the Project path, database location, and current counts of Mappings and Review Items.

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

### Build the semantic search index

```bash
uv run normflow index build --workspace <path>
```

Builds a FAISS semantic index from the current mappings. Required before semantic search will return results. Rebuild after importing new mappings.

```bash
uv run normflow index clear --workspace <path>
```

Removes the persisted FAISS index.

### Get normalization suggestions

```bash
uv run normflow suggest --workspace <path> "raw text value"
```

Queries the mapping library for an exact match on the raw text. If no exact match is found and the semantic index is built, falls back to semantic search. Returns JSON with suggestions, method used, and confidence score.

```bash
uv run normflow suggest --workspace <path> "colour" --limit 10
```

- `--limit` (default: 1) — maximum number of suggestions to return.
- `--no-semantic` — disable semantic matching fallback (exact match only).
- `--semantic-threshold` (default: 0.85) — minimum cosine similarity for semantic matches.

### Batch-suggest normalizations for a CSV

```bash
uv run normflow suggest-batch --workspace <path> records.csv --column name
```

Reads every row from a CSV, looks up exact-match and semantic suggestions for the specified column, and outputs a CSV with the original columns plus a `normalized_text` column containing the top suggestion (blank if no match).

- `--column` is required — the CSV column holding the raw texts to normalize.
- `--output-column` (default: `normalized_text`) sets the name of the suggestion column in the output.
- `--output` writes the result to a file instead of stdout.
- `--no-semantic` — disable semantic matching fallback.
- `--semantic-threshold` (default: 0.85) — minimum cosine similarity for semantic matches.
- Entirely blank rows are excluded; partial rows are included with a blank suggestion.

```bash
uv run normflow suggest-batch --workspace <path> records.csv --column product_name --output results.csv
```

### Review Items

List pending Review Items:

```bash
uv run normflow review list --workspace <path>
```

Shows pending Review Items oldest-first with a stable ID, raw text, and optional Suggestion. Add `--json` for machine-readable output.

Accept a Review Item's Suggestion as-is:

```bash
uv run normflow review accept --workspace <path> --record-id 1
```

Trims and validates the suggested text, creates its Mapping, and removes the Review Item atomically.

Edit and accept a Review Item:

```bash
uv run normflow review edit-and-accept --workspace <path> --record-id 1 --normalized-text "Oxygen Sensor"
```

Trims and validates the edited text, creates its Mapping, and removes the Review Item atomically.

- Normalized text must contain at least one non-whitespace character.
- Once accepted, a Review Item no longer exists and cannot be reviewed again.
- Existing Projects are upgraded automatically when opened: pending legacy queue records become Review Items, accepted records are discarded, and existing Mappings are preserved.

### Show version

```bash
uv run normflow version
```

## Project structure

```
src/normflow/
├── __init__.py           # Package entry, __version__
├── __main__.py           # python -m normflow
├── cli.py                # Typer CLI: version, init, info, import, export, suggest, suggest-batch, review, index
├── api.py                # Same-origin FastAPI adapter and production UI serving
├── mapping_service.py    # Single seam: CSV import/export, suggest (exact + semantic), review, index build/clear
├── semantic_index.py     # FAISS + SentenceTransformer index (build, persist, query)
├── static/               # Built browser UI served by FastAPI
└── workspace.py          # Workspace init
frontend/                 # Framework-free TypeScript UI, Vite build, and Vitest tests
tests/
├── conftest.py               # Shared fixtures
├── helpers.py                # Test helpers
├── test_cli.py               # CLI tests
├── test_workspace_service.py # Workspace service tests
├── test_semantic_index.py    # Semantic index tests
└── test_suggest_semantic.py  # Semantic suggestion tests
```

## Roadmap

- [x] Project skeleton
- [x] Import/export mappings (CSV)
- [x] Exact matching suggestions
- [x] Batch CSV suggestions
- [x] Semantic search with embeddings (FAISS + sentence-transformers)
- [ ] LLM fallback
- [x] Review Item workflow (accept/edit-and-accept)
- [x] Local TypeScript Project-selection UI
