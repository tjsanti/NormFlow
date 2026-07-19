"""Ownership checks and removal for the release installer-owned application."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import shutil


class ManagedInstallationError(RuntimeError):
    """The current executable is not safe to remove as a managed installation."""


@dataclass(frozen=True)
class ManagedInstallation:
    """The installer-owned files that may be removed as one application."""

    version: str
    app_home: Path
    runtime: Path
    exposed_executable: Path


class ManagedInstallationService:
    """Find and remove only the installation layout created by ``install.sh``."""

    def __init__(self, *, environment: dict[str, str], invocation_path: str):
        self._environment = environment
        self._invocation_path = invocation_path

    def inspect(self, *, version: str) -> ManagedInstallation:
        """Return the current managed layout, or refuse to touch an unowned one."""
        app_home = self._app_home()
        exposed_executable = self._bin_dir() / "normflow"
        executable = Path(self._invocation_path).expanduser().resolve()

        try:
            runtime = executable.parent.parent
            relative_runtime = runtime.relative_to(app_home)
        except ValueError as exc:
            raise ManagedInstallationError(
                "uninstall is unavailable from a source/development executable; "
                "run the managed NormFlow command instead"
            ) from exc

        if (
            executable != runtime / "bin" / "normflow"
            or len(relative_runtime.parts) != 2
            or relative_runtime.parts[0] not in {"runtimes", "releases"}
        ):
            raise ManagedInstallationError(
                "uninstall is unavailable from a source/development executable; "
                "run the managed NormFlow command instead"
            )

        current = app_home / "current"
        expected_exposed_target = current / "bin" / "normflow"
        if (
            not current.is_symlink()
            or current.resolve() != runtime
            or not exposed_executable.is_symlink()
            or exposed_executable.readlink() != expected_exposed_target
        ):
            raise ManagedInstallationError(
                "uninstall refused because this executable is not owned by the managed installer"
            )

        return ManagedInstallation(
            version=version,
            app_home=app_home,
            runtime=runtime,
            exposed_executable=exposed_executable,
        )

    def remove(self, installation: ManagedInstallation) -> None:
        """Remove every installer-owned application file and no shared user files."""
        try:
            shutil.rmtree(installation.app_home)
            installation.exposed_executable.unlink()
        except OSError as exc:
            raise ManagedInstallationError(f"could not remove managed NormFlow: {exc}") from exc

    def _app_home(self) -> Path:
        data_home = self._environment.get("XDG_DATA_HOME")
        if data_home is None:
            data_home = str(self._home() / ".local" / "share")
        return (Path(data_home).expanduser() / "normflow").resolve()

    def _bin_dir(self) -> Path:
        bin_home = self._environment.get("XDG_BIN_HOME")
        if bin_home is None:
            bin_home = str(self._home() / ".local" / "bin")
        return Path(bin_home).expanduser().resolve()

    def _home(self) -> Path:
        return Path(self._environment.get("HOME", os.fspath(Path.home()))).expanduser()
