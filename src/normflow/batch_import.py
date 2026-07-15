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
from typing import TYPE_CHECKING, Callable, Literal, TypedDict

if TYPE_CHECKING:
    from .mapping_service import ImportRecordsResult, MappingService


RunStatus = Literal["active", "succeeded", "failed", "interrupted"]


class BatchImportRun(TypedDict):
    id: str
    status: RunStatus
    input_name: str
    input_fingerprint: str
    created_at: str
    started_at: str
    updated_at: str
    terminal_at: str | None
    result: dict | None
    error: str | None
    replacement_run_id: str | None


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
            runs = BatchImportRuns(self)
            active = runs._active()
            owned = getattr(_run_ownership, "ids", set())
            if active and active["id"] not in owned:
                runs._reconcile()
            return method(self, *args, **kwargs)
    return guarded


class BatchImportRuns:
    """Deep Project service for starting, observing, and recovering Batch Imports."""

    def __init__(self, service: MappingService):
        self.service = service
        self.project = service._path
        self.database = service._db_path
        self.runs_dir = self.project / ".batches" / "runs"
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
        with self._connection() as connection:
            journal = {
                "mapping_ids": [r[0] for r in connection.execute("SELECT id FROM examplemapping")],
                "review_item_ids": [r[0] for r in connection.execute("SELECT id FROM reviewitem")],
                "revision": connection.execute(
                    "SELECT revision FROM mappingrevision WHERE id=1"
                ).fetchone()[0],
                "retained": (self.project / ".batches" / "current.csv").exists(),
            }
        (self.runs_dir / f"{run_id}.snapshot.json").write_text(
            json.dumps(journal), encoding="utf-8"
        )
        retained = self.project / ".batches" / "current.csv"
        if retained.exists():
            shutil.copy2(retained, self.runs_dir / f"{run_id}.previous.csv")
        semantic_snapshot = self.runs_dir / f"{run_id}.semantic"
        semantic_snapshot.mkdir()
        normflow = self.project / ".normflow"
        for name in (
            "faiss_index", "semantic_index_refresh_required",
            "semantic_index_refresh_failed",
        ):
            source = normflow / name
            if source.is_dir():
                shutil.copytree(source, semantic_snapshot / name)
            elif source.exists():
                shutil.copy2(source, semantic_snapshot / name)

    def _cleanup_recovery(self, run_id: str) -> None:
        (self.runs_dir / f"{run_id}.snapshot.json").unlink(missing_ok=True)
        (self.runs_dir / f"{run_id}.previous.csv").unlink(missing_ok=True)
        shutil.rmtree(self.runs_dir / f"{run_id}.semantic", ignore_errors=True)

    def _cleanup_batch_temporaries(self) -> None:
        batch_dir = self.project / ".batches"
        for pattern in (".current-*.tmp", ".previous-*.tmp"):
            for temporary in batch_dir.glob(pattern):
                temporary.unlink(missing_ok=True)

    def _compensate(self, run_id: str) -> None:
        snapshot_path = self.runs_dir / f"{run_id}.snapshot.json"
        if not snapshot_path.exists():
            return
        journal = json.loads(snapshot_path.read_text(encoding="utf-8"))
        with self._connection() as connection:
            for table, ids in (
                ("examplemapping", journal["mapping_ids"]),
                ("reviewitem", journal["review_item_ids"]),
            ):
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    connection.execute(
                        f"DELETE FROM {table} WHERE id NOT IN ({placeholders})", ids
                    )
                else:
                    connection.execute(f"DELETE FROM {table}")
            connection.execute(
                "UPDATE mappingrevision SET revision=? WHERE id=1",
                (journal["revision"],),
            )
        retained = self.project / ".batches" / "current.csv"
        previous = self.runs_dir / f"{run_id}.previous.csv"
        if journal["retained"] and previous.exists():
            shutil.copy2(previous, retained)
        else:
            retained.unlink(missing_ok=True)
        normflow = self.project / ".normflow"
        semantic_snapshot = self.runs_dir / f"{run_id}.semantic"
        for name in (
            "faiss_index", "semantic_index_refresh_required",
            "semantic_index_refresh_failed",
        ):
            destination = normflow / name
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink(missing_ok=True)
            source = semantic_snapshot / name
            if source.is_dir():
                shutil.copytree(source, destination)
            elif source.exists():
                shutil.copy2(source, destination)

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

    def run(
        self,
        csv_path: str | Path,
        column: str,
        *,
        semantic: bool = True,
        llm: bool = True,
        threshold: float = 0.85,
        on_started: Callable[[BatchImportRun], None] | None = None,
        on_committed: Callable[[BatchImportRun], None] | None = None,
        replaces: str | None = None,
    ) -> BatchImportRun:
        """Execute one run; ``on_committed`` is the narrow crash-test seam.

        That callback runs after commit evidence is durable but before terminal
        status, allowing recovery of that otherwise impractical process-exit window.
        """
        source = Path(csv_path).expanduser().resolve()
        # Validate before creating an attempt that can never execute.
        self.service._read_csv(str(source), column)
        with project_writer(self.project):
            self._reconcile()
            run_id = str(uuid.uuid4())
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            staged = self.runs_dir / f"{run_id}.csv"
            shutil.copy2(source, staged)
            fingerprint = hashlib.sha256(staged.read_bytes()).hexdigest()
            self._snapshot(run_id)
            now = self._now()
            with self._connection() as connection:
                connection.execute(
                    "INSERT INTO batchimportrun VALUES (?, 'active', ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                    (run_id, source.name, fingerprint, now, now, now),
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
                marker = self.runs_dir / f"{run_id}.committed.json"

                def record_commit(result):
                    temporary = marker.with_suffix(".tmp")
                    temporary.write_text(json.dumps(result), encoding="utf-8")
                    os.replace(temporary, marker)
                    if on_committed:
                        on_committed(active)

                owned = getattr(_run_ownership, "ids", set())
                _run_ownership.ids = {*owned, run_id}
                try:
                    result = self.service.import_records_for_review(
                        str(staged), column, semantic=semantic, llm=llm,
                        threshold=threshold,
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
                if isinstance(error, Exception):
                    raise BatchImportExecutionError(terminal) from error
                raise

    def retry(self, run_id: str, csv_path: str | Path, column: str, **kwargs):
        previous = self.status(run_id)
        if previous["status"] not in ("failed", "interrupted"):
            raise ValueError("Only failed or interrupted Batch Import Runs can be retried")
        return self.run(csv_path, column, replaces=run_id, **kwargs)

    def retry_background(
        self, run_id: str, csv_path: str | Path, column: str, **kwargs,
    ) -> BatchImportRun:
        previous = self.status(run_id)
        if previous["status"] not in ("failed", "interrupted"):
            raise ValueError("Only failed or interrupted Batch Import Runs can be retried")
        return self.start_background(csv_path, column, replaces=run_id, **kwargs)

    def start_background(self, csv_path: str | Path, column: str, **kwargs):
        ready = threading.Event()
        box: dict[str, object] = {}

        def started(run):
            box["run"] = run
            ready.set()

        def worker():
            try:
                self.run(csv_path, column, on_started=started, **kwargs)
            except Exception as error:
                box["error"] = error
                ready.set()

        threading.Thread(target=worker, daemon=True).start()
        ready.wait()
        if "run" not in box:
            raise box["error"]  # type: ignore[misc]
        return box["run"]
