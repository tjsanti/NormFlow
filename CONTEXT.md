# NormFlow

CLI-first, human-in-the-loop text normalization workbench. A user can work on multiple independent **Projects** without cross-pollination.

A project's physical container (created by `normflow init`) holds:
- `normflow.db` — the SQLite database, source of truth for Mappings and Review Items
- `input/` — raw text records awaiting normalization
- `output/` — results after normalization
- `samples/` — portable flat files for demos, seed data, import/export examples, and evaluation fixtures. Not runtime state, not the canonical store.

## Core Concepts

**Project**:
A discrete normalization task and its on-disk project folder. A user may have multiple Projects for different domains that require separate editing styles and do not cross-pollinate.
_Avoid_: Workspace

**Suggestion**:
A candidate normalized text proposed by the system for a Review Item.
_Avoid_: Candidate, Proposal

**Review Item**:
A raw text input awaiting human review. It may contain a Suggestion or have no proposed normalized text yet; acceptance requires nonblank normalized text, with surrounding whitespace removed. Acceptance creates a Mapping and removes the Review Item.

**Mapping**:
A pair of strings — the messy original text and its approved clean version.
_Avoid_: Normalization, Standardization, Standard

**Batch Import**:
The CSV workflow: user uploads a CSV of raw records, system matches each unique raw_text against the mapping library, and routes results: exact or semantic matches auto-commit to the library; LLM suggestions and no-matches become pending Review Items. The original CSV is preserved in the Project for export. On export, the original CSV is returned with a normalized_text column filled in.
_Avoid_: Ingestion, Pipeline, ETL
