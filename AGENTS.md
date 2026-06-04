# Agent Conventions

- Build in small feature slices with passing tests before moving on.
-
- - Follow TDD: write or update tests first, then implement the smallest change that passes.
-
- - Keep CLI, API, and UI as thin adapters over shared core services.
-
- - Prefer deep modules: expose simple interfaces and hide implementation details.
-
- - Keep the core workflow usable from the CLI for future agent automation.
-
- - Apply DRY, but avoid premature abstraction.
-
- - Apply KISS: choose simple, boring solutions unless complexity is clearly justified.
-
- - Keep business logic out of the UI.
-
- - Use typed interfaces and explicit schemas for inputs/outputs.
-
- - Make commands predictable: clear arguments, useful errors, stable exit codes, and machine-readable output where practical.

# Agent skills

## Issue tracker

Issues tracked in this repo's GitHub Issues via the `gh` CLI. See `docs/agents/issue-tracker.md`.

## Triage labels

Five canonical labels used as-is: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

## Domain docs

Single-context layout: `CONTEXT.md` at root + `docs/adr/` for past decisions. See `docs/agents/domain.md`.
