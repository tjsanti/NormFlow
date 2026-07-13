# NormFlow

CLI-first, human-in-the-loop text normalization workbench.

Import approved `raw_text → normalized_text` mappings, get Suggestions for new records, and resolve pending Review Items into Mappings.

**Current state:** Project init, a local browser UI, CSV import/export, exact-match Suggestions, semantic search (FAISS), batch CSV Suggestions, and a Review Item workflow (accept/edit-and-accept).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python project manager)
- Python ≥ 3.13

## Install

```bash
uv tool install normflow
```

FastAPI, Uvicorn, multipart upload support, and the production browser assets are included in the normal installation. No optional server extra is needed.

For development from a clone:

```bash
git clone <repo-url>
cd normflow
uv sync
```

## Usage

### Create and open a Project

```bash
mkdir my-project
cd my-project
normflow init
normflow ui
```

`normflow init` initializes the current directory. Every data command and `normflow ui` discovers the nearest Project from the current directory, so commands work at the Project root or from any subdirectory inside it. NormFlow prints the local UI URL and opens it in the default browser.

Projects are independent and must not be nested. To switch the Project served by the UI, stop the current server, `cd` to the other Project (or one of its subdirectories), and run `normflow ui` again.

For headless automation, prevent browser launch and select a stable port:

```bash
normflow ui --no-open --port 43123
```

The port must be available on localhost. Without `--port`, NormFlow chooses a free port.

### Project contents

Initialization creates:

| Item | Purpose |
|------|---------|
| `normflow.db` | SQLite database — source of truth for Mappings and Review Items |
| `input/` | Raw text records awaiting normalization |
| `output/` | Results after normalization |
| `samples/` | Portable flat files for demos, seed data, and evaluation fixtures |
| `.normflow/` | Internal data (FAISS semantic index) |

Existing Project databases remain compatible and are opened in place; no Project marker or schema migration is required for this workflow.

### Check Project status

```bash
normflow info
```

Shows the Project path, database location, and current counts of Mappings and Review Items.

### Import mappings from CSV

```bash
normflow import --source-column source --target-column target mappings.csv
```

Reads a CSV file with a header row. Each row becomes a `raw_text → normalized_text` mapping in the database.
- `--source-column` and `--target-column` are required.
- Duplicate sources (already in the database) are skipped.
- Empty rows and whitespace are handled gracefully.

### Export mappings to CSV

```bash
normflow export mappings.csv [--source-column raw_text] [--target-column normalized_text]
```

Writes all current mappings to a CSV file. Column names default to `raw_text` and `normalized_text` but can be overridden with `--source-column` and `--target-column`.

### Build the semantic search index

```bash
normflow index build
```

Builds a FAISS semantic index from the current mappings. Required before semantic search will return results. Rebuild after importing new mappings.

```bash
normflow index clear
```

Removes the persisted FAISS index.

### Get normalization suggestions

```bash
normflow suggest "raw text value"
```

Queries the mapping library for an exact match on the raw text. If no exact match is found and the semantic index is built, falls back to semantic search. Returns JSON with suggestions, method used, and confidence score.

```bash
normflow suggest "colour" --limit 10
```

- `--limit` (default: 1) — maximum number of suggestions to return.
- `--no-semantic` — disable semantic matching fallback (exact match only).
- `--semantic-threshold` (default: 0.85) — minimum cosine similarity for semantic matches.

### Batch-suggest normalizations for a CSV

```bash
normflow suggest-batch records.csv --column name
```

Reads every row from a CSV, looks up exact-match and semantic suggestions for the specified column, and outputs a CSV with the original columns plus a `normalized_text` column containing the top suggestion (blank if no match).

- `--column` is required — the CSV column holding the raw texts to normalize.
- `--output-column` (default: `normalized_text`) sets the name of the suggestion column in the output.
- `--output` writes the result to a file instead of stdout.
- `--no-semantic` — disable semantic matching fallback.
- `--semantic-threshold` (default: 0.85) — minimum cosine similarity for semantic matches.
- Entirely blank rows are excluded; partial rows are included with a blank suggestion.

```bash
normflow suggest-batch records.csv --column product_name --output results.csv
```

### Review Items

List pending Review Items:

```bash
normflow review list
```

Shows pending Review Items oldest-first with a stable ID, raw text, and optional Suggestion. Add `--json` for machine-readable output.

Accept a Review Item's Suggestion as-is:

```bash
normflow review accept --record-id 1
```

Trims and validates the suggested text, creates its Mapping, and removes the Review Item atomically.

Edit and accept a Review Item:

```bash
normflow review edit-and-accept --record-id 1 --normalized-text "Oxygen Sensor"
```

Trims and validates the edited text, creates its Mapping, and removes the Review Item atomically.

- Normalized text must contain at least one non-whitespace character.
- Once accepted, a Review Item no longer exists and cannot be reviewed again.
- Existing Projects are upgraded automatically when opened: pending legacy queue records become Review Items, accepted records are discarded, and existing Mappings are preserved.

### Show version

```bash
normflow version
```

## Project structure

```
src/normflow/
├── __init__.py           # Package entry, __version__
├── __main__.py           # python -m normflow
├── cli.py                # Typer CLI: version, init, info, import, export, suggest, suggest-batch, review, index
├── api.py                # Same-origin FastAPI adapter and production UI serving
├── mapping_service.py    # Single seam: CSV import/export, suggest (exact + semantic), review, index build/clear
├── project.py            # Current-directory Project discovery and validation
├── project_service.py    # Project initialization
├── semantic_index.py     # FAISS + SentenceTransformer index (build, persist, query)
├── static/               # Built browser UI served by FastAPI
└── suggestion_lookup.py  # Exact, semantic, and LLM Suggestion lookup
frontend/                 # Framework-free TypeScript UI, Vite build, and Vitest tests
tests/
├── conftest.py               # Shared fixtures
├── helpers.py                # Test helpers
├── test_cli.py               # CLI tests
├── test_project_service.py   # Project service tests
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
- [x] Local TypeScript UI bound to the current Project
