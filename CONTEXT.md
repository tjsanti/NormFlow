# NormFlow

CLI-first, human-in-the-loop text normalization workbench. A user can work on multiple independent **Projects** without cross-pollination.

A project's physical container (created by `normflow init`) holds:
- `normflow.db` — the SQLite database, source of truth for mappings and suggestions
- `input/` — raw text records awaiting normalization
- `output/` — results after normalization
- `samples/` — portable flat files for demos, seed data, import/export examples, and evaluation fixtures. Not runtime state, not the canonical store.

## Core Concepts

**Project**:
A discrete normalization task. A user may have multiple projects for different domains that require separate editing styles and do not cross-pollinate.

**Suggestion**:
A candidate normalized text for a given raw text input, produced by the system and awaiting human approval.
_Avoid_: Candidate, Proposal

**Mapping**:
A pair of strings — the messy original text and its approved clean version.
_Avoid_: Normalization, Standardization, Standard
