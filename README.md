# NormFlow

NormFlow is a CLI-first, human-in-the-loop text normalization tool inspired by a production workflow for cleaning messy domain-specific records.

The goal is to start simple: import approved `raw_text -> normalized_text` mappings, suggest normalized values for new records, support human review, and feed accepted edits back into the library.

Initial direction:
- Python backend and CLI
- - SQLite local workspace
- - Exact matching first
- - Semantic matching and LLM fallback later
- - Thin TypeScript UI after the core workflow is stable
