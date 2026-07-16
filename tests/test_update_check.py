"""Behavioral tests for the shared update-check service."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
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


def test_browser_status_keeps_newer_release_visible_without_duplicate_check() -> None:
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

    first = service.browser_status(now.date())
    refreshed = service.browser_status(now.date())

    expected = UpdateNotice(
        installed_version="0.1.0",
        latest_version="0.2.0",
        install_command=INSTALL_COMMAND,
    )
    assert first == expected
    assert refreshed == expected
    assert transport.timeouts == [1.0]


def test_dismissing_browser_notice_hides_that_release_for_the_day() -> None:
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=StaticReleaseTransport("0.2.0"),
        cache=MemoryCache(),
        now=lambda: now,
        environment={},
    )

    notice = service.browser_status(now.date())
    service.dismiss_browser_notice("0.2.0", now.date())

    assert notice is not None
    assert service.browser_status(now.date()) is None


def test_dismissed_browser_notice_returns_on_the_next_day() -> None:
    current = [datetime(2026, 7, 16, 23, 59, tzinfo=UTC)]
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=StaticReleaseTransport("0.2.0"),
        cache=MemoryCache(),
        now=lambda: current[0],
        environment={},
    )
    service.browser_status(current[0].date())
    service.dismiss_browser_notice("0.2.0", current[0].date())

    current[0] = datetime(2026, 7, 17, 0, 1, tzinfo=UTC)

    assert service.browser_status(current[0].date()) == UpdateNotice(
        installed_version="0.1.0",
        latest_version="0.2.0",
        install_command=INSTALL_COMMAND,
    )


def test_browser_dismissal_uses_the_callers_local_calendar_day() -> None:
    now = datetime(2026, 7, 17, 0, 31, tzinfo=UTC)
    browser_day = date(2026, 7, 16)
    cache = MemoryCache(
        UpdateCheckState(
            last_attempt=now,
            latest_version="0.2.0",
        )
    )
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=StaticReleaseTransport("0.2.0"),
        cache=cache,
        now=lambda: now,
        environment={},
    )

    service.dismiss_browser_notice("0.2.0", browser_day)

    assert service.browser_status(browser_day) is None
    assert service.browser_status(date(2026, 7, 17)) == UpdateNotice(
        installed_version="0.1.0",
        latest_version="0.2.0",
        install_command=INSTALL_COMMAND,
    )


def test_browser_dismissal_expires_on_the_next_local_day_across_dst() -> None:
    daylight_time = timezone(-timedelta(hours=7))
    now = datetime(2026, 3, 8, 0, 30, tzinfo=daylight_time)
    cache = MemoryCache(
        UpdateCheckState(
            last_attempt=now,
            latest_version="0.2.0",
            dismissed_version="0.2.0",
            dismissed_on=date(2026, 3, 7),
        )
    )
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=StaticReleaseTransport("0.2.0"),
        cache=cache,
        now=lambda: now,
        environment={},
    )

    assert service.browser_status(now.date()) == UpdateNotice(
        installed_version="0.1.0",
        latest_version="0.2.0",
        install_command=INSTALL_COMMAND,
    )


def test_new_release_is_visible_despite_an_older_release_dismissal() -> None:
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    cache = MemoryCache(
        UpdateCheckState(
            last_attempt=now,
            latest_version="0.3.0",
            dismissed_version="0.2.0",
            dismissed_on=now.date(),
        )
    )
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=StaticReleaseTransport("0.3.0"),
        cache=cache,
        now=lambda: now,
        environment={},
    )

    assert service.browser_status(now.date()) == UpdateNotice(
        installed_version="0.1.0",
        latest_version="0.3.0",
        install_command=INSTALL_COMMAND,
    )


def test_browser_status_is_absent_when_installed_release_is_current() -> None:
    service = UpdateCheckService(
        installed_version="0.2.0",
        transport=StaticReleaseTransport("0.2.0"),
        cache=MemoryCache(),
        now=lambda: datetime(2026, 7, 16, 12, tzinfo=UTC),
        environment={},
    )

    assert service.browser_status(date(2026, 7, 16)) is None


@pytest.mark.parametrize(
    ("installed_version", "latest_version", "update_available"),
    [
        ("0.2", "0.2.0", False),
        ("0.2.0.post1", "0.2.0", False),
        ("0.2.0rc1", "0.2.0", True),
    ],
)
def test_update_comparison_accepts_installed_package_metadata_versions(
    installed_version: str,
    latest_version: str,
    update_available: bool,
) -> None:
    service = UpdateCheckService(
        installed_version=installed_version,
        transport=StaticReleaseTransport(latest_version),
        cache=MemoryCache(),
        now=lambda: datetime(2026, 7, 16, 12, tzinfo=UTC),
        environment={},
    )

    notice = service.browser_status(date(2026, 7, 16))

    assert (notice is not None) is update_available


def test_browser_status_opt_out_avoids_network_access() -> None:
    transport = StaticReleaseTransport("0.2.0")
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=transport,
        cache=MemoryCache(),
        now=lambda: datetime(2026, 7, 16, 12, tzinfo=UTC),
        environment={"NORMFLOW_NO_UPDATE_CHECK": "1"},
    )

    assert service.browser_status(date(2026, 7, 16)) is None
    assert transport.timeouts == []


@pytest.mark.parametrize(
    "transport",
    [OfflineTransport(), StaticReleaseTransport("malformed")],
    ids=["offline", "malformed-response"],
)
def test_browser_status_suppresses_unavailable_release_information(
    transport,
) -> None:
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=transport,
        cache=MemoryCache(),
        now=lambda: datetime(2026, 7, 16, 12, tzinfo=UTC),
        environment={},
    )

    assert service.browser_status(date(2026, 7, 16)) is None


@pytest.mark.parametrize(
    "transport",
    [OfflineTransport(), StaticReleaseTransport("malformed")],
    ids=["offline", "invalid-version"],
)
def test_failed_refresh_keeps_the_last_known_release(
    transport,
) -> None:
    now = datetime(2026, 7, 16, 12, tzinfo=UTC)
    cache = MemoryCache(
        UpdateCheckState(
            last_attempt=now - timedelta(hours=25),
            latest_version="0.2.0",
        )
    )
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=transport,
        cache=cache,
        now=lambda: now,
        environment={},
    )

    notice = service.browser_status(now.date())

    assert notice == UpdateNotice(
        installed_version="0.1.0",
        latest_version="0.2.0",
        install_command=INSTALL_COMMAND,
    )
    assert cache.state.last_attempt == now
    assert cache.state.latest_version == "0.2.0"


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
        dismissed_version="0.2.0",
        dismissed_on=date(2026, 7, 16),
    )

    recovered = cache.load()
    cache.save(state)

    assert recovered == UpdateCheckState()
    assert cache.load() == state
    assert sorted(path.name for path in cache_path.parent.iterdir()) == [
        "update-check.json"
    ]


def test_json_cache_rejects_an_invalid_latest_version(tmp_path: Path) -> None:
    cache_path = tmp_path / "normflow" / "update-check.json"
    cache_path.parent.mkdir()
    cache_path.write_text(
        json.dumps({"latest_version": "not-a-package-version"}),
        encoding="utf-8",
    )

    assert JsonUpdateCache(cache_path).load() == UpdateCheckState()


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


def test_busy_global_lock_does_not_delay_normal_work(tmp_path: Path) -> None:
    lock = FileUpdateLock(tmp_path / "update-check.lock")
    service = UpdateCheckService(
        installed_version="0.1.0",
        transport=StaticReleaseTransport("0.2.0"),
        cache=MemoryCache(),
        lock=FileUpdateLock(tmp_path / "update-check.lock"),
        now=lambda: datetime(2026, 7, 16, 12, tzinfo=UTC),
        environment={},
    )
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with lock.hold():
            result = executor.submit(service.check).result(timeout=0.2)
    finally:
        executor.shutdown(wait=True)

    assert result is None


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
    draft: bool, prerelease: bool,
) -> None:
    def run_release(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "tag_name": "v0.2.0",
                    "draft": draft,
                    "prerelease": prerelease,
                }
            ),
            stderr="",
        )

    with pytest.raises(ValueError, match="stable Release"):
        GitHubReleaseTransport(run=run_release).latest_stable_version(
            timeout_seconds=1.0
        )


def test_github_transport_reads_latest_stable_release(
) -> None:
    observed: dict[str, object] = {}

    def run_release(command, **kwargs):
        observed.update(command=command, **kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "tag_name": "v0.2.0",
                    "draft": False,
                    "prerelease": False,
                }
            ),
            stderr="",
        )

    latest = GitHubReleaseTransport(run=run_release).latest_stable_version(
        timeout_seconds=1.0
    )

    assert latest == "v0.2.0"
    assert observed["timeout"] == 1.0
    assert observed["capture_output"] is True
    assert observed["text"] is True
    assert observed["check"] is True
    command = observed["command"]
    assert command[:2] == ["curl", "--proto"]
    assert command[command.index("--max-time") + 1] == "1.0"
    assert command[-1] == (
        "https://api.github.com/repos/tjsanti/NormFlow/releases/latest"
    )
