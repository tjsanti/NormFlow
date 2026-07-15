"""Durable Batch Import Runs and the Project-wide writer boundary."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Callable, Literal, Protocol, TypedDict, Unpack

from .semantic_index import SemanticIndexStatus


RunStatus = Literal["active", "succeeded", "failed", "interrupted"]


class BatchImportResult(TypedDict):
    auto_committed: int
    review_items: int
    skipped: int
    semantic_index_status: SemanticIndexStatus
    semantic_index_warning: str | None


class BatchImportRun(TypedDict):
    id: str
    status: RunStatus
    input_name: str
    input_fingerprint: str
    created_at: str
    started_at: str
    updated_at: str
    terminal_at: str | None
    result: BatchImportResult | None
    error: str | None
    replacement_run_id: str | None


class BatchImportOptions(TypedDict, total=False):
    on_started: Callable[[BatchImportRun], None]
    on_committed: Callable[[BatchImportRun], None]


class BatchImportExecutor(Protocol):
    def __call__(
        self,
        csv_path: str,
        column: str,
        *,
        semantic: bool,
        llm: bool,
        threshold: float,
        _on_published: Callable[[BatchImportResult], None] | None,
    ) -> BatchImportResult: ...


class ProjectBusyError(RuntimeError):
    """Another caller currently owns the Project writer boundary."""

    def __init__(self, active_run: BatchImportRun | None = None):
        self.active_run = active_run
        detail = f" by Batch Import Run {active_run['id']}" if active_run else ""
        super().__init__(f"The Project is currently being changed{detail}; try again later.")


class BatchImportRunNotFoundError(ValueError):
    pass


class BatchImportExecutionError(RuntimeError):
    def __init__(self, run: BatchImportRun):
        self.run = run
        super().__init__(run["error"] or "Batch Import failed")


_owners_guard = threading.Lock()
_owners: dict[Path, tuple[int, int, object]] = {}
_run_ownership = threading.local()


@contextmanager
def project_writer(project: Path):
    """Acquire the one non-blocking, re-entrant-per-thread Project writer lock."""
    lock_path = project / ".normflow" / "writer.lock"
    lock_path.parent.mkdir(exist_ok=True)
    thread_id = threading.get_ident()
    with _owners_guard:
        owner = _owners.get(lock_path)
        if owner and owner[0] == thread_id:
            _owners[lock_path] = (thread_id, owner[1] + 1, owner[2])
            nested = True
        else:
            nested = False
    if nested:
        try:
            yield
        finally:
            with _owners_guard:
                current = _owners[lock_path]
                _owners[lock_path] = (thread_id, current[1] - 1, current[2])
        return

    descriptor = lock_path.open("a+")
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ProjectBusyError() from error
        with _owners_guard:
            # A different thread in this process can own another flock descriptor.
            if lock_path in _owners:
                raise ProjectBusyError()
            _owners[lock_path] = (thread_id, 1, descriptor)
        try:
            yield
        finally:
            with _owners_guard:
                _owners.pop(lock_path, None)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        descriptor.close()


def coordinated_writer(method):
    """Put a MappingService mutation behind the shared Project lock."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with project_writer(self._path):
            runs = self._batch_import_runs()
            active = runs._active()
            owned = getattr(_run_ownership, "ids", set())
            if active and active["id"] not in owned:
                runs._reconcile()
            return method(self, *args, **kwargs)
    return guarded


class BatchImportRuns:
    """Deep Project service for starting, observing, and recovering Batch Imports."""

    def __init__(
        self,
        *,
        project: Path,
        database: Path,
        validate_input: Callable[[str, str], None],
        execute: BatchImportExecutor,
        snapshot_state: Callable[[Path], None],
        restore_state: Callable[[Path], None],
        cleanup_temporaries: Callable[[], None],
    ):
        self.project = project
        self.database = database
        self._validate_input = validate_input
        self._execute = execute
        self._snapshot_state = snapshot_state
        self._restore_state = restore_state
        self._cleanup_temporaries = cleanup_temporaries
        self.runs_dir = self.project / ".batches" / "runs"
        with closing(sqlite3.connect(self.database)) as connection:
            schema = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE name IN "
                    "('batchimportrun', 'one_active_batch_import')"
                )
            }
        if schema == {"batchimportrun", "one_active_batch_import"}:
            return

        with project_writer(self.project):
            with closing(sqlite3.connect(self.database)) as connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS batchimportrun (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        input_name TEXT NOT NULL,
                        input_fingerprint TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        terminal_at TEXT,
                        result_json TEXT,
                        error TEXT,
                        replacement_run_id TEXT
                    )
                """)
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS one_active_batch_import "
                    "ON batchimportrun(status) WHERE status = 'active'"
                )
                connection.commit()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _dict(row: sqlite3.Row) -> BatchImportRun:
        return {
            "id": row["id"], "status": row["status"],
            "input_name": row["input_name"],
            "input_fingerprint": row["input_fingerprint"],
            "created_at": row["created_at"], "started_at": row["started_at"],
            "updated_at": row["updated_at"], "terminal_at": row["terminal_at"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"], "replacement_run_id": row["replacement_run_id"],
        }

    def _get(self, run_id: str) -> BatchImportRun:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM batchimportrun WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise BatchImportRunNotFoundError(f"Batch Import Run {run_id} was not found")
        return self._dict(row)

    def _active(self) -> BatchImportRun | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM batchimportrun WHERE status = 'active'"
            ).fetchone()
        return self._dict(row) if row else None

    def _terminal(self, run_id: str, status: RunStatus, *, result=None, error=None):
        now = self._now()
        with self._connection() as connection:
            connection.execute(
                "UPDATE batchimportrun SET status=?, updated_at=?, terminal_at=?, "
                "result_json=?, error=? WHERE id=?",
                (status, now, now, json.dumps(result) if result else None, error, run_id),
            )
        return self._get(run_id)

    def _snapshot(self, run_id: str) -> None:
        self._snapshot_state(self.runs_dir / f"{run_id}.snapshot")

    def _cleanup_recovery(self, run_id: str) -> None:
        snapshot = self.runs_dir / f"{run_id}.snapshot"
        if snapshot.exists():
            shutil.rmtree(snapshot)

    def _cleanup_batch_temporaries(self) -> None:
        self._cleanup_temporaries()

    def _compensate(self, run_id: str) -> None:
        snapshot = self.runs_dir / f"{run_id}.snapshot"
        if not snapshot.exists():
            return
        self._restore_state(snapshot)

    def _reconcile(self) -> None:
        active = self._active()
        if not active:
            return
        marker = self.runs_dir / f"{active['id']}.committed.json"
        if marker.exists():
            self._terminal(
                active["id"], "succeeded",
                result=json.loads(marker.read_text(encoding="utf-8")),
            )
        else:
            self._compensate(active["id"])
            self._terminal(
                active["id"], "interrupted",
                error="The owning process stopped before the Batch Import committed.",
            )
        self._cleanup_recovery(active["id"])
        self._cleanup_batch_temporaries()
        marker.unlink(missing_ok=True)

    def status(self, run_id: str | None = None) -> BatchImportRun:
        try:
            with project_writer(self.project):
                self._reconcile()
        except ProjectBusyError:
            pass
        if run_id:
            return self._get(run_id)
        active = self._active()
        if active:
            return active
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM batchimportrun ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            raise BatchImportRunNotFoundError("No Batch Import Runs exist for this Project")
        return self._dict(row)

    def active(self) -> BatchImportRun | None:
        """Return only the currently active durable run, never historical state."""
        return self._active()

    def run(
        self,
        csv_path: str | Path,
        column: str,
        *,
        on_started: Callable[[BatchImportRun], None] | None = None,
        on_input_owned: Callable[[BatchImportRun], None] | None = None,
        on_committed: Callable[[BatchImportRun], None] | None = None,
        replaces: str | None = None,
    ) -> BatchImportRun:
        """Execute one run; ``on_committed`` is the narrow crash-test seam.

        That callback runs after commit evidence is durable but before terminal
        status, allowing recovery of that otherwise impractical process-exit window.
        """
        source = Path(csv_path).expanduser().resolve()
        with project_writer(self.project):
            self._reconcile()
            run_id = str(uuid.uuid4())
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            staged = self.runs_dir / f"{run_id}.csv"
            marker = self.runs_dir / f"{run_id}.committed.json"
            now = self._now()
            with self._connection() as connection:
                connection.execute(
                    "INSERT INTO batchimportrun VALUES (?, 'active', ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                    (run_id, source.name, "pending", now, now, now),
                )
                if replaces:
                    connection.execute(
                        "UPDATE batchimportrun SET replacement_run_id=? WHERE id=?",
                        (run_id, replaces),
                    )
                interrupted_ids = [
                    row[0] for row in connection.execute(
                        "SELECT id FROM batchimportrun WHERE status='interrupted' AND id != ?",
                        (run_id,),
                    )
                ]
            for interrupted_id in interrupted_ids:
                (self.runs_dir / f"{interrupted_id}.csv").unlink(missing_ok=True)
            active = self._get(run_id)
            if on_started:
                on_started(active)
            try:
                shutil.copy2(source, staged)
                fingerprint = hashlib.sha256(staged.read_bytes()).hexdigest()
                with self._connection() as connection:
                    connection.execute(
                        "UPDATE batchimportrun SET input_fingerprint=?, updated_at=? "
                        "WHERE id=?",
                        (fingerprint, self._now(), run_id),
                    )
                active = self._get(run_id)
                if on_input_owned:
                    on_input_owned(active)
                self._validate_input(str(staged), column)
                self._snapshot(run_id)

                def record_commit(result: BatchImportResult) -> None:
                    temporary = marker.with_suffix(".tmp")
                    temporary.write_text(json.dumps(result), encoding="utf-8")
                    os.replace(temporary, marker)
                    if on_committed:
                        on_committed(active)

                owned = getattr(_run_ownership, "ids", set())
                _run_ownership.ids = {*owned, run_id}
                try:
                    result = self._execute(
                        str(staged), column, semantic=True, llm=True,
                        threshold=0.85,
                        _on_published=record_commit,
                    )
                finally:
                    _run_ownership.ids = owned
                terminal = self._terminal(run_id, "succeeded", result=result)
                staged.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                self._cleanup_recovery(run_id)
                return terminal
            except BaseException as error:
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    status: RunStatus = "interrupted"
                else:
                    status = "failed"
                self._compensate(run_id)
                marker.unlink(missing_ok=True)
                terminal = self._terminal(run_id, status, error=str(error))
                self._cleanup_recovery(run_id)
                if status == "failed":
                    staged.unlink(missing_ok=True)
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise BatchImportExecutionError(terminal) from error
                if isinstance(error, Exception):
                    raise BatchImportExecutionError(terminal) from error
                raise

    def _retryable(self, run_id: str) -> BatchImportRun:
        previous = self.status(run_id)
        if previous["status"] not in ("failed", "interrupted"):
            raise ValueError("Only failed or interrupted Batch Import Runs can be retried")
        return previous

    def retry(
        self,
        run_id: str,
        csv_path: str | Path,
        column: str,
        **kwargs: Unpack[BatchImportOptions],
    ) -> BatchImportRun:
        self._retryable(run_id)
        return self.run(csv_path, column, replaces=run_id, **kwargs)

    def retry_background(
        self,
        run_id: str,
        csv_path: str | Path,
        column: str,
    ) -> BatchImportRun:
        self._retryable(run_id)
        return self.start_background(csv_path, column, replaces=run_id)

    def start_background(
        self,
        csv_path: str | Path,
        column: str,
        *,
        replaces: str | None = None,
    ) -> BatchImportRun:
        ready = threading.Event()
        runs: list[BatchImportRun] = []
        errors: list[Exception] = []

        def started(run: BatchImportRun) -> None:
            runs.append(run)

        def input_owned(run: BatchImportRun) -> None:
            runs[0] = run
            ready.set()

        def worker() -> None:
            try:
                self.run(
                    csv_path,
                    column,
                    on_started=started,
                    on_input_owned=input_owned,
                    replaces=replaces,
                )
            except BatchImportExecutionError as error:
                if runs:
                    runs[0] = error.run
                else:
                    runs.append(error.run)
                ready.set()
            except Exception as error:
                errors.append(error)
                ready.set()

        threading.Thread(target=worker, daemon=True).start()
        ready.wait()
        if not runs:
            raise errors[0]
        return runs[0]
