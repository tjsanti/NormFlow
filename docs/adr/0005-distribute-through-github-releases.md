# ADR-0005: Distribute through GitHub Releases

Status: Accepted

Date: 2026-07-16

## Context

NormFlow needs one truthful public release identity before release artifacts
are built. The `normflow` name on PyPI belongs to an unrelated project, so
publishing or recommending installation by that name would direct users to the
wrong software. Releases also need a clear supported-platform boundary and a
predictable relationship with persisted Project data.

## Decision

NormFlow is an MIT-licensed product distributed only through immutable GitHub
Releases. We never publish NormFlow to PyPI or GitHub Packages. A fix to a
published release is delivered as a new version; tags and attached artifacts
are not replaced in place.

The stable installer entry point is:

`https://github.com/tjsanti/NormFlow/releases/latest/download/install.sh`

The installer uses a curl-to-shell flow and initially supports macOS and Linux.
It installs a private, pinned uv-managed Python environment rather than using a
user's ambient Python packages. Windows is outside the initial supported
platform boundary.

Installation and upgrades are explicit user actions. NormFlow performs no
silent self-update and no automatic downgrade. The installer must clearly
report an unsupported platform or failed installation.

Installed package metadata is the source of the public version. `normflow
--version` and `normflow -V` report that metadata and no separate CLI version
constant is maintained.

During the 0.x series, command and application interfaces may change. Project
data has a stronger compatibility policy: a newer NormFlow must migrate safely
when it supports the existing Project data, or refuse clearly before mutation.
It must never silently corrupt a Project.

## Consequences

- Release automation must build and attach supported-platform artifacts and
  `install.sh` to a GitHub Release.
- Documentation must link the GitHub Release installer, never recommend the
  unrelated PyPI package, and state the supported platform boundary.
- Updates remain observable and user-controlled.
- Project format changes require a safe migration or a clear refusal path.
