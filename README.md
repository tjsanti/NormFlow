# NormFlow

CLI-first, human-in-the-loop text normalization workbench.

Import approved `raw_text → normalized_text` Mappings, process new records through an exact → semantic → LLM fallback chain, review uncertain Suggestions, and export normalized results.

**Current state:** Project init, a local browser UI, Mapping and Batch CSV imports, exact/semantic/LLM Suggestions, durable Batch Import Runs, Review Item acceptance, and CSV export.

## Install

NormFlow is distributed only through immutable GitHub Releases. The managed
installer supports Apple Silicon macOS 14 or later and x86-64 Linux with glibc.
It does not support Intel macOS, Linux ARM, musl/Alpine, or Windows. It installs
its own private uv bootstrap, Python 3.13 runtime, CPU-only dependencies, and
the bundled local embedding model; no existing Python or uv configuration is
changed.

```bash
curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
  https://github.com/tjsanti/NormFlow/releases/latest/download/install.sh | sh
```

Do not install the package named `normflow` from PyPI. That name belongs to an
unrelated project, and this NormFlow project does not publish to PyPI. The
GitHub Release installer URL above is the stable installation entry point.

The first installation downloads the NormFlow wheel, locked CPU dependencies,
and the embedding model (expect several hundred MB and a broadband connection).
Each release asset is SHA-256 verified before installation. If you prefer to
inspect the script first, download and review it before executing it:

```bash
curl --fail --silent --show-error --location \
  https://github.com/tjsanti/NormFlow/releases/latest/download/install.sh \
  --output install.sh
less install.sh
sh install.sh
```

FastAPI, Uvicorn, multipart upload support, and the production browser assets are included in the normal installation. No optional server extra is needed.

For development from a clone:

```bash
git clone <repo-url>
cd normflow
uv sync
```

## Build the release wheel

From a clean checkout, run the complete release build:

```bash
./scripts/build-wheel
```

This installs the locked frontend dependencies with `npm ci`, runs the frontend
tests and typecheck, builds the production browser assets, and builds only the
wheel into `dist/`. The command fails if the frontend output is incomplete or
if the finished wheel does not contain the browser UI. Generated files under
`src/normflow/static/` are ignored; authored frontend source and
`package-lock.json` are the inputs to the release artifact.

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

### Configure the LLM

`normflow ui` and `normflow batch-import` require valid server-side LLM configuration before they start the local server or a Batch Import Run:

| Variable | Requirement |
|----------|-------------|
| `OPENAI_API_KEY` | Required and nonblank. |
| `OPENAI_BASE_URL` | Optional; when set, it must be a valid HTTP(S) URL. |
| `NORMFLOW_LLM_MODEL` | Optional; defaults to `gpt-4o-mini`, but cannot be explicitly blank. |

Set these variables in the shell, or put them in an optional `.env` file at the
active Project root. NormFlow resolves the active Project first, so its `.env`
is found when `normflow ui` or `normflow batch-import` is launched from a nested
Project directory. Existing shell values take precedence over Project `.env`
values.

Credentials remain in the NormFlow process: they are not stored in the Project
database or sent to the browser. Launch validation parses local configuration
only and makes no provider or network request. Invalid configuration exits with
an error before the server, browser, or Batch Import starts.

### Project contents

Initialization creates:

| Item | Purpose |
|------|---------|
| `normflow.db` | SQLite database — source of truth for Mappings, Review Items, and Batch Import Runs |
| `input/` | Raw text records awaiting normalization |
| `output/` | Results after normalization |
| `samples/` | Portable flat files for demos, seed data, and evaluation fixtures |
| `.normflow/` | Internal data (FAISS semantic index) |

Existing Project databases remain compatible and are opened in place; no Project marker or schema migration is required for this workflow.

### 0.x Project-data policy

Command and application interfaces may change during the 0.x series. Project
data has a stronger guarantee: a newer NormFlow must migrate safely when it
supports the existing Project data, or refuse clearly before changing it. It
must never silently corrupt a Project.

The first Batch Import also creates internal `.batches/` storage for the sole retained Batch CSV and run recovery data.

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

### Batch Import records

Use the canonical Batch Import when a CSV contains raw records that need to become Mappings or Review Items:

```bash
normflow batch-import records.csv --column name
```

For each unique, nonblank value, NormFlow runs the complete exact-match → semantic-match → LLM-Suggestion fallback chain. Exact and semantic matches are auto-committed as Mappings; LLM Suggestions become pending Review Items for human approval. Duplicate values and values already pending review are skipped. A Batch Import also works when the Mapping library is empty.

Each attempt is a durable Batch Import Run with a unique ID and an `active`, `succeeded`, `failed`, or `interrupted` status. The command prints the run ID immediately to stderr, waits synchronously, and writes one terminal JSON document to stdout.

The outcome is all-or-nothing: a successful run publishes its Mappings, Review Items, semantic-index state, and the Project's sole retained Batch CSV atomically. A failure preserves the previous Project state and retained Batch CSV. Only one writer may modify a Project at a time. When a Batch Import owns the Project, a competing Batch Import fails immediately with exit code `3` and reports the active run.

Inspect a run by ID, or omit the ID to inspect the active or most recent run:

```bash
normflow batch-import-status [RUN_ID]
```

Failed and interrupted runs are never retried automatically. Retry one explicitly by resubmitting its CSV; the retry receives a new run ID:

```bash
normflow batch-import-retry RUN_ID records.csv --column name
```

### Export mappings to CSV

```bash
normflow export mappings.csv [--source-column raw_text] [--target-column normalized_text]
```

Writes all current mappings to a CSV file. Column names default to `raw_text` and `normalized_text` but can be overridden with `--source-column` and `--target-column`.

### Export the retained Batch

After reviewing the Batch's Review Items, export the original retained CSV with a normalized-text column populated from approved Mappings:

```bash
normflow export-batch results.csv --source-column name
```

The original columns and row order are preserved. Rows without an approved Mapping have a blank value. Use `--output-column` to change the added column name from its `normalized_text` default.

### Build the semantic search index

```bash
normflow index build
```

Builds a FAISS semantic index from the current Mappings. NormFlow normally builds or refreshes it automatically before the next semantic or LLM Suggestion after Mappings change. Use this command to prewarm the index or retry after an automatic refresh warning.

```bash
normflow index clear
```

Removes the persisted FAISS index.

### Get normalization suggestions

```bash
normflow suggest "raw text value"
```

Queries the Mapping library for an exact match on the raw text, then falls back to semantic search and LLM matching when enabled. If the semantic index needs refreshing, the CLI reports that progress on stderr before rebuilding; JSON remains on stdout. If refresh fails, NormFlow preserves the previous index, warns that Suggestions may use earlier Mappings, and recommends `normflow index build`.

```bash
normflow suggest "colour" --limit 10
```

- `--limit` (default: 1) — maximum number of suggestions to return.
- `--no-semantic` — disable semantic matching fallback (exact match only).
- `--no-llm` — disable LLM matching fallback.
- `--semantic-threshold` (default: 0.85) — minimum cosine similarity for semantic matches.

### Batch-suggest normalizations for a CSV

```bash
normflow suggest-batch records.csv --column name
```

Reads every row from a CSV, looks up exact, semantic, and LLM Suggestions for the specified column, and outputs a CSV with the original columns plus a `normalized_text` column containing the top Suggestion (blank if no match). Unlike `batch-import`, this command does not create Mappings or Review Items and does not retain the CSV in the Project.

- `--column` is required — the CSV column holding the raw texts to normalize.
- `--output-column` (default: `normalized_text`) sets the name of the suggestion column in the output.
- `--output` writes the result to a file instead of stdout.
- `--no-semantic` — disable semantic matching fallback.
- `--no-llm` — disable LLM matching fallback.
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
normflow review accept --review-item-id 1
```

Trims and validates the suggested text, creates its Mapping, and removes the Review Item atomically.

Accept a Review Item with replacement normalized text:

```bash
normflow review accept --review-item-id 1 --normalized-text "Oxygen Sensor"
```

Both forms trim and validate the chosen text, create its Mapping, and remove the Review Item atomically.

- Normalized text must contain at least one non-whitespace character.
- Once accepted, a Review Item no longer exists and cannot be reviewed again.
- Existing Projects are upgraded automatically when opened: pending legacy queue records become Review Items, accepted records are discarded, and existing Mappings are preserved.

### Show version

```bash
normflow --version
# Short form:
normflow -V
```

## Project structure

```
src/normflow/
├── __init__.py           # Package entry, __version__
├── __main__.py           # python -m normflow
├── api.py                # Same-origin FastAPI adapter and production UI serving
├── batch_import.py       # Durable Batch Import coordination, recovery, status, and retry
├── cli.py                # Thin Typer adapter over the core services
├── llm_config.py         # Project-aware server-side LLM configuration
├── llm_matcher.py        # LLM Suggestion provider adapter
├── mapping_service.py    # Mapping, Suggestion, Review Item, Batch, export, and index workflows
├── project.py            # Current-directory Project discovery and validation
├── project_service.py    # Project initialization
├── semantic_index.py     # FAISS + SentenceTransformer index (build, persist, query)
├── static/               # Built browser UI served by FastAPI
└── suggestion_lookup.py  # Exact, semantic, and LLM fallback chain
frontend/                 # Framework-free TypeScript UI, Vite build, and Vitest tests
tests/                    # Pytest unit, adapter, workflow, recovery, and packaging tests
```

## Roadmap

- [x] Project skeleton
- [x] Import/export mappings (CSV)
- [x] Exact matching suggestions
- [x] Batch CSV suggestions
- [x] Semantic search with embeddings (FAISS + sentence-transformers)
- [x] LLM fallback
- [x] Unified Review Item acceptance
- [x] Atomic, durable Batch Import Runs with status and explicit retry
- [x] Retained Batch export
- [x] Local TypeScript UI bound to the current Project
