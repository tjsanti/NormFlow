"""Shared, failure-tolerant checks for newer stable NormFlow releases."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Protocol
from urllib.request import Request, urlopen


INSTALL_COMMAND = (
    "curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location "
    "https://github.com/tjsanti/NormFlow/releases/latest/download/install.sh | sh"
)
LATEST_RELEASE_URL = "https://api.github.com/repos/tjsanti/NormFlow/releases/latest"


@dataclass(frozen=True)
class UpdateCheckState:
    """Globally cached update-check and notification state."""

    last_attempt: datetime | None = None
    latest_version: str | None = None
    last_notified: datetime | None = None


@dataclass(frozen=True)
class UpdateNotice:
    """A newer stable release that an adapter may present to a human."""

    installed_version: str
    latest_version: str
    install_command: str


class ReleaseTransport(Protocol):
    """Boundary for reading GitHub's latest stable Release."""

    def latest_stable_version(self, *, timeout_seconds: float) -> str: ...


class UpdateCache(Protocol):
    """Boundary for globally persisted update-check state."""

    def load(self) -> UpdateCheckState: ...

    def save(self, state: UpdateCheckState) -> None: ...


class UpdateLock(Protocol):
    """Boundary for serializing global update-check attempts."""

    def hold(self) -> AbstractContextManager[None]: ...


class NoopUpdateLock:
    """In-process default for fully injected service use."""

    @contextmanager
    def hold(self) -> Iterator[None]:
        yield


class FileUpdateLock:
    """Serialize update checks across supported macOS/Linux processes."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def hold(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)


class JsonUpdateCache:
    """Atomically persist update state in one global JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> UpdateCheckState:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return UpdateCheckState()
            latest_version = payload.get("latest_version")
            if latest_version is not None and not isinstance(latest_version, str):
                return UpdateCheckState()
            return UpdateCheckState(
                last_attempt=_parse_datetime(payload.get("last_attempt")),
                latest_version=latest_version,
                last_notified=_parse_datetime(payload.get("last_notified")),
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return UpdateCheckState()

    def save(self, state: UpdateCheckState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_attempt": _format_datetime(state.last_attempt),
            "latest_version": state.latest_version,
            "last_notified": _format_datetime(state.last_notified),
        }
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(payload, temporary, separators=(",", ":"))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


class GitHubReleaseTransport:
    """Read the latest stable release identity from GitHub Releases."""

    def latest_stable_version(self, *, timeout_seconds: float) -> str:
        request = Request(
            LATEST_RELEASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "NormFlow update check",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.load(response)
        if (
            not isinstance(payload, dict)
            or payload.get("draft") is not False
            or payload.get("prerelease") is not False
            or not isinstance(payload.get("tag_name"), str)
        ):
            raise ValueError("GitHub did not return a stable Release")
        return payload["tag_name"]


class UpdateCheckService:
    """Compare installed metadata with GitHub's latest stable Release."""

    def __init__(
        self,
        *,
        installed_version: str,
        transport: ReleaseTransport,
        cache: UpdateCache,
        lock: UpdateLock | None = None,
        now: Callable[[], datetime],
        environment: Mapping[str, str],
    ) -> None:
        self._installed_version = installed_version
        self._transport = transport
        self._cache = cache
        self._lock = lock or NoopUpdateLock()
        self._now = now
        self._environment = environment

    def check(self) -> UpdateNotice | None:
        if self._environment.get("NORMFLOW_NO_UPDATE_CHECK") == "1":
            return None
        try:
            with self._lock.hold():
                return self._check_locked()
        except Exception:
            return None

    def _check_locked(self) -> UpdateNotice | None:
        checked_at = self._now()
        try:
            state = self._cache.load()
        except Exception:
            state = UpdateCheckState()
        if _recent(state.last_attempt, checked_at):
            if (
                state.latest_version is not None
                and _version_key(state.latest_version)
                > _version_key(self._installed_version)
                and not _recent(state.last_notified, checked_at)
            ):
                try:
                    self._cache.save(
                        UpdateCheckState(
                            last_attempt=state.last_attempt,
                            latest_version=state.latest_version,
                            last_notified=checked_at,
                        )
                    )
                except Exception:
                    return None
                return UpdateNotice(
                    installed_version=self._installed_version,
                    latest_version=state.latest_version,
                    install_command=INSTALL_COMMAND,
                )
            return None
        attempted = UpdateCheckState(
            last_attempt=checked_at,
            last_notified=state.last_notified,
        )
        try:
            self._cache.save(attempted)
        except Exception:
            return None
        try:
            latest_version = self._transport.latest_stable_version(
                timeout_seconds=1.0
            ).removeprefix("v")
            latest_key = _version_key(latest_version)
            installed_key = _version_key(self._installed_version)
        except Exception:
            return None
        if latest_key <= installed_key:
            try:
                self._cache.save(
                    UpdateCheckState(
                        last_attempt=checked_at,
                        latest_version=latest_version,
                    )
                )
            except Exception:
                pass
            return None
        try:
            self._cache.save(
                UpdateCheckState(
                    last_attempt=checked_at,
                    latest_version=latest_version,
                    last_notified=checked_at,
                )
            )
        except Exception:
            return None
        return UpdateNotice(
            installed_version=self._installed_version,
            latest_version=latest_version,
            install_command=INSTALL_COMMAND,
        )


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _recent(earlier: datetime | None, now: datetime) -> bool:
    if earlier is None:
        return False
    age = now - earlier
    return timedelta(0) <= age < timedelta(hours=24)


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def default_update_check_service(
    installed_version: str,
    *,
    environment: Mapping[str, str],
    transport: ReleaseTransport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> UpdateCheckService:
    """Create the production service with XDG cache and GitHub boundaries."""
    xdg_cache = environment.get("XDG_CACHE_HOME")
    cache_root = (
        Path(xdg_cache)
        if xdg_cache and Path(xdg_cache).is_absolute()
        else Path.home() / ".cache"
    )
    return UpdateCheckService(
        installed_version=installed_version,
        transport=transport or GitHubReleaseTransport(),
        cache=JsonUpdateCache(cache_root / "normflow" / "update-check.json"),
        lock=FileUpdateLock(cache_root / "normflow" / "update-check.lock"),
        now=now,
        environment=environment,
    )
