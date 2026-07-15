# NormFlow

CLI-first, human-in-the-loop text normalization workbench. A user can work on multiple independent **Projects** without cross-pollination.

A project's physical container (created by `normflow init`) holds:
- `normflow.db` — the SQLite database, source of truth for Mappings and Review Items
- `input/` — raw text records awaiting normalization
- `output/` — results after normalization
- `samples/` — portable flat files for demos, seed data, import/export examples, and evaluation fixtures. Not runtime state, not the canonical store.

## Core Concepts

**Project**:
A discrete normalization task and its on-disk project folder. A user may have multiple Projects for different domains that require separate editing styles and do not cross-pollinate; Projects are independent and must not be nested inside one another.
_Avoid_: Workspace

**Suggestion**:
A candidate normalized text proposed by the system for a Review Item.
_Avoid_: Candidate, Proposal

**Review Item**:
A raw text input awaiting human review. It may contain a Suggestion or have no proposed normalized text yet; a user accepts it either with its Suggestion or with replacement normalized text. Acceptance requires nonblank normalized text, with surrounding whitespace removed, creates a Mapping, and removes the Review Item.

**Mapping**:
A pair of strings — the messy original text and its approved clean version.
_Avoid_: Normalization, Standardization, Standard

**Mapping Import**:
The CSV workflow for adding already-approved source and target text pairs directly as Mappings. It does not generate Suggestions or Review Items.
_Avoid_: Seed Import

**Batch Import**:
The CSV workflow that processes each unique raw text through the complete fallback chain—exact match, semantic match, then an LLM-generated Suggestion—and is valid even with an empty Mapping library; exact or semantic matches auto-commit as Mappings, while LLM Suggestions and no-matches become pending Review Items. A failed Batch Import commits nothing, while a successful one becomes the Project's sole retained Batch CSV for export with a normalized text column.
_Avoid_: Ingestion, Pipeline, ETL

**Batch Import Run**: One identified attempt to perform a Batch Import for a Project, tracked from creation through its terminal result.
_Avoid_: Job, Task
