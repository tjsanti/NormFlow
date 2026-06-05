# NormFlow

CLI-first, human-in-the-loop text normalization workbench.

Import approved `raw_text → normalized_text` mappings, get suggestions for new records, review and edit them, then feed accepted changes back into your mapping library.

**Current state:** Project skeleton — workspace initialization and CLI scaffolding.

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
uv run normflow init <path>
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
uv run normflow info <path>
```

Shows the workspace path, database location, and current counts of mappings and suggestions.

### Show version

```bash
uv run normflow version
```

## Project structure

```
src/normflow/
├── __init__.py       # Package entry, __version__
├── __main__.py       # python -m normflow
├── cli.py            # Typer CLI: version, init, info
├── models.py         # SQLModel domain models
└── workspace.py      # Workspace init and info operations
tests/
└── test_cli.py       # CLI tests
```

## Roadmap

- [x] Project skeleton (this release)
- [ ] Import/export mappings (CSV)
- [ ] Exact matching suggestions
- [ ] Semantic search with embeddings
- [ ] LLM fallback
- [ ] Human review workflow (accept/edit/reject)
- [ ] TypeScript web UI
