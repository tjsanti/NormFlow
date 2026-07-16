# ADR 0001: Coordinate Project writes around durable Batch Import Runs

Status: Accepted

## Context

A Batch Import can perform provider calls before it changes Mappings, Review
Items, the semantic index, and the Project's one retained Batch CSV. CLI and
HTTP callers must be able to identify an attempt, observe its result, and
recover honest state after a process interruption. At the same time, a Project
must never expose a partly committed Batch Import or silently allow another
writer to invalidate the snapshot being processed.

## Decision

Each attempt is a durable **Batch Import Run** with a unique run ID. Its status
is exactly one of `active`, `succeeded`, `failed`, or `interrupted`; the latter
three are terminal. Durable metadata records the run ID, status, input name and
content fingerprint, creation, start, update, and terminal timestamps, terminal
counts or error detail when applicable, and an optional replacement-run ID
linking a retry. Metadata remains available for the lifetime of the Project
rather than disappearing when a process or request ends.

### One retained Batch and atomic outcome

The run reads and processes a candidate CSV without changing visible Project
state. On success, its Mappings, Review Items, semantic-index state, terminal
result, and retained CSV are published as one all-or-nothing outcome. The new
CSV then replaces the Project's sole previously retained Batch CSV. On any
provider, validation, persistence, or publication failure, none of the run's
Project changes remain and the previous retained Batch CSV remains unchanged.
Temporary candidate CSVs are not additional retained Batches.

Database uniqueness constraints prevent recovery or retry from creating more
than one Mapping or pending Review Item for the same raw text. Reconciliation
does not depend on process-local deduplication state.

## Coordination and ownership

At most one writer owns a Project, and therefore at most one Batch Import Run
is `active`, at a time. Ownership is enforced by one Project-scoped operating-
system advisory lock. Acquisition is non-blocking: a competing writer fails
immediately, with no timeout, queue, polling, or lock stealing. The operating
system releases ownership if the process exits. A living process that still
holds the lock retains ownership even if it appears hung; ownership changes
only when that process releases the lock or is terminated.

The following operations must acquire that same lock before changing or
rebuilding Project state:

- Mapping Import;
- Review Item acceptance, both single and bulk;
- semantic-index build;
- semantic-index clear;
- another Batch Import; and
- any lookup that must rebuild the semantic index.

Read-only operations continue while the lock is held and see the last committed
Project state. They never see in-progress Batch Import changes. A lookup that
can use the already committed semantic index remains a read; one that needs to
rebuild it is a writer and fails fast when another writer owns the Project.

## Run lifecycle and recovery

A start creates and durably records a unique run ID before processing and marks
it `active`. There is no cancellation operation. A CLI interruption attempts to
record `interrupted`; the staged input for an interrupted run remains only until
that run is retried or another Batch Import supersedes it. An uncatchable process
exit leaves an `active` record for reconciliation. An HTTP client disconnect
does not cancel the server-owned run.

A provider failure marks the run `failed`, discards the failed run's staged
input, and preserves the prior retained Batch CSV. Retrying it therefore
requires the caller to resubmit the CSV.

Before starting another writer, and when observing run state, NormFlow
reconciles a stale `active` record only after it can acquire the Project lock.
Reconciliation uses durable commit evidence, never elapsed time:

- if the run's complete Project outcome and retained CSV were committed, it is
  repaired to `succeeded` with its terminal result; or
- if that complete outcome was not committed, staged or partial effects are
  removed or compensated, the previous retained CSV is restored, and the run
  is marked `interrupted`.

Thus an interrupted attempt cannot leave Mappings, Review Items, semantic-index
state, or the retained Batch CSV from an uncommitted run visible. An `active`
run is never declared stale merely because it has run for a long time.

Run IDs are never reused. A failed or interrupted run is retried only by an
explicit retry operation that resubmits the CSV. A retry performs the full
canonical chain again, always creates a new run ID, and may link the prior run
to that replacement through its optional replacement-run ID. NormFlow performs
no automatic retry. Succeeded and active runs are not retryable.

## CLI and HTTP contract

The canonical CLI start operation is exactly:

```text
normflow batch-import BATCH_CSV --column RAW_TEXT_COLUMN
```

It is synchronous and always runs the complete exact-match, semantic-match,
then LLM-Suggestion fallback chain. It prints the durable run ID immediately to
stderr and waits for a terminal result. In JSON mode it prints exactly one
terminal JSON document to stdout. It exits `0` when the run succeeds, `1` when
execution or a provider fails or the run is interrupted, `2` for command usage
errors, and `3`
when another writer or Batch Import Run is active.

The CLI also exposes status and retry operations, but their command spellings
are intentionally deferred. A status observation accepts an optional run ID;
with an ID it returns that durable run, while without one it returns the active
run or, if none is active, the most recent run. Observing a known failed or
interrupted run is itself a successful lookup and does not retry it. An explicit
retry resubmits the CSV, reports its new run ID immediately, follows the same
synchronous terminal-output behavior as start, and optionally records the new
ID as the prior run's replacement. Status and retry use the same machine-
readable run representation as start. A successful status lookup exits `0`, a
lookup or Project failure exits `1`, malformed usage exits `2`, and a retry
conflict exits `3`.

An accepted HTTP start returns `202 Accepted` and a `Location` header for the
durable run status resource. Processing is server-owned and continues if the
requesting client disconnects. A competing start returns `409 Conflict`, makes
no new run, and includes `Location` for the currently active run. Status reads
return the durable representation identified by `Location`. HTTP route
spellings beyond that resource relationship are intentionally deferred.

## Acceptance scenarios

1. Starting a Batch Import in an idle Project records an `active` run ID before
   provider work; successful completion publishes all changes and the new sole
   retained Batch CSV, records `succeeded`, emits terminal JSON, and exits `0`.
2. A provider failure records `failed`, reports actionable error text (or one
   terminal document in JSON mode), and exits `1`; no new Mapping, Review Item,
   semantic-index state, or candidate Batch CSV remains, the staged input is
   discarded, and the previous retained Batch CSV is byte-for-byte preserved.
3. While a run owns the lock, each coordinated Project write fails immediately;
   a read returns the last committed state. A lookup requiring index rebuild is
   treated as a competing write.
4. A competing CLI start exits `3`. A competing HTTP start returns `409` with
   the active run's `Location`. Neither creates another run.
5. An HTTP start returns `202` with the new run's `Location`; disconnecting the
   client does not cancel or interrupt the run.
6. After a process exit, reconciliation marks a fully committed stale run
   `succeeded`; otherwise it restores the prior all-or-nothing Project snapshot
   and marks the stale run `interrupted`. Runtime length alone changes nothing.
7. Status with an ID returns that run. Status without an ID returns the active
   run or the most recent run. Observing `failed` or `interrupted` never starts
   work and exits `0` when the lookup itself succeeds.
8. Explicitly retrying a failed or interrupted run resubmits the CSV, creates a
   distinct linked run ID, and executes the full chain. No automatic retry or
   cancellation behavior exists.

## Consequences

Every Project writer shares one coordination boundary, while read paths remain
available against committed state. Implementations must persist run and
recovery evidence and compensate across the database and retained CSV boundary.
The CLI and HTTP adapters stay thin over the same run service and expose stable,
machine-observable outcomes without introducing background queue semantics.
