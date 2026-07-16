"""Behavioral tests for the shared update-check service."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
import json
from pathlib import Path
from threading import Barrier

import pytest

from normflow.update_check import (
    INSTALL_COMMAND,
    FileUpdateLock,
    GitHubReleaseTransport,
    JsonUpdateCache,
    UpdateCheckService,
    UpdateCheckState,
    UpdateNotice,
    default_update_check_service,
)


class MemoryCache:
    def __init__(self, state: UpdateCheckState | None = None) -> None:
        self.state = state or UpdateCheckState()

    def load(self) -> UpdateCheckState:
        return self.state

    def save(self, state: UpdateCheckState) -> None:
        self.state = state


class StaticReleaseTransport:
    def __init__(self, latest_version: str) -> None:
        self.latest_version = latest_version
        self.timeouts: list[float] = []

    def latest_stable_version(self, *, timeout_seconds: float) -> str:
        self.timeouts.append(timeout_seconds)
        return self.latest_version


class OfflineTransport:
    def __init__(self) -> None:
        self.attempts = 0

    def latest_stable_version(self, *, timeout_seconds: float) -> str:
        self.attempts += 1
        raise OSError("offline")


def test_newer_stable_release_returns_notice_and_records_check() -> None:
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    cache = MemoryCache()
    transport = StaticReleaseTransport("v0.2.0")
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=transport,
        cache=cache,
        now=lambda: now,
        environment={},
    )

    notice = service.check()

    assert notice == UpdateNotice(
        installed_version="0.1.0",
        latest_version="0.2.0",
        install_command=INSTALL_COMMAND,
    )
    assert cache.state == UpdateCheckState(
        last_attempt=now,
        latest_version="0.2.0",
        last_notified=now,
    )
    assert transport.timeouts == [1.0]


def test_check_and_notice_happen_at_most_once_within_24_hours() -> None:
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    cache = MemoryCache()
    transport = StaticReleaseTransport("0.2.0")
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=transport,
        cache=cache,
        now=lambda: now,
        environment={},
    )

    first = service.check()
    second = service.check()

    assert first is not None
    assert second is None
    assert transport.timeouts == [1.0]


def test_opt_out_avoids_network_access_entirely() -> None:
    transport = StaticReleaseTransport("0.2.0")
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=transport,
        cache=MemoryCache(),
        now=lambda: datetime(2026, 7, 16, 12, tzinfo=UTC),
        environment={"NORMFLOW_NO_UPDATE_CHECK": "1"},
    )

    notice = service.check()

    assert notice is None
    assert transport.timeouts == []


def test_network_failure_is_suppressed_and_not_retried_that_day() -> None:
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    cache = MemoryCache()
    transport = OfflineTransport()
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=transport,
        cache=cache,
        now=lambda: now,
        environment={},
    )

    first = service.check()
    second = service.check()

    assert first is None
    assert second is None
    assert transport.attempts == 1
    assert cache.state == UpdateCheckState(last_attempt=now)


def test_json_cache_recovers_from_corrupt_data_with_atomic_replacement(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "normflow" / "update-check.json"
    cache_path.parent.mkdir()
    cache_path.write_text("not json", encoding="utf-8")
    cache = JsonUpdateCache(cache_path)
    state = UpdateCheckState(
        last_attempt=datetime(2026, 7, 16, 12, tzinfo=UTC),
        latest_version="0.2.0",
        last_notified=datetime(2026, 7, 16, 12, tzinfo=UTC),
    )

    recovered = cache.load()
    cache.save(state)

    assert recovered == UpdateCheckState()
    assert cache.load() == state
    assert sorted(path.name for path in cache_path.parent.iterdir()) == [
        "update-check.json"
    ]


def test_global_lock_prevents_simultaneous_processes_from_checking_twice(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    cache_path = tmp_path / "normflow" / "update-check.json"
    transport = StaticReleaseTransport("0.2.0")
    start = Barrier(2)

    def run_check() -> UpdateNotice | None:
        service = UpdateCheckService(
            installed_version="0.1.0",
            transport=transport,
            cache=JsonUpdateCache(cache_path),
            lock=FileUpdateLock(cache_path.with_suffix(".lock")),
            now=lambda: now,
            environment={},
        )
        start.wait()
        return service.check()

    with ThreadPoolExecutor(max_workers=2) as executor:
        notices = list(executor.map(lambda _: run_check(), range(2)))

    assert sum(notice is not None for notice in notices) == 1
    assert transport.timeouts == [1.0]


def test_default_service_uses_global_xdg_cache_outside_a_project(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    xdg_cache = tmp_path / "global-cache"
    service = default_update_check_service(
        "0.1.0",
        environment={"XDG_CACHE_HOME": str(xdg_cache)},
        transport=StaticReleaseTransport("0.1.0"),
        now=lambda: datetime(2026, 7, 16, 12, tzinfo=UTC),
    )

    service.check()

    assert (xdg_cache / "normflow" / "update-check.json").is_file()
    assert list(project.iterdir()) == []


@pytest.mark.parametrize(
    ("draft", "prerelease"),
    [(True, False), (False, True)],
)
def test_github_transport_rejects_nonstable_releases(
    monkeypatch: pytest.MonkeyPatch, draft: bool, prerelease: bool,
) -> None:
    payload = BytesIO(
        json.dumps(
            {
                "tag_name": "v0.2.0",
                "draft": draft,
                "prerelease": prerelease,
            }
        ).encode()
    )
    monkeypatch.setattr(
        "normflow.update_check.urlopen",
        lambda request, timeout: payload,
    )

    with pytest.raises(ValueError, match="stable Release"):
        GitHubReleaseTransport().latest_stable_version(timeout_seconds=1.0)


def test_github_transport_reads_latest_stable_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = BytesIO(
        json.dumps(
            {
                "tag_name": "v0.2.0",
                "draft": False,
                "prerelease": False,
            }
        ).encode()
    )
    observed: dict[str, object] = {}

    def open_release(request, timeout):
        observed.update(url=request.full_url, timeout=timeout)
        return payload

    monkeypatch.setattr("normflow.update_check.urlopen", open_release)

    latest = GitHubReleaseTransport().latest_stable_version(
        timeout_seconds=1.0
    )

    assert latest == "v0.2.0"
    assert observed == {
        "url": "https://api.github.com/repos/tjsanti/NormFlow/releases/latest",
        "timeout": 1.0,
    }
