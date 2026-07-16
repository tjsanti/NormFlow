"""Unit tests for MappingService."""

import os
import resource
import tempfile
from pathlib import Path

import pytest

import normflow.mapping_service as mapping_module
from normflow.mapping_service import MappingService
from normflow.project_service import init_project


def test_many_projects_do_not_retain_database_descriptors(tmp_path: Path):
    """Independent Projects remain usable under a bounded file-descriptor budget."""
    baseline = len(os.listdir("/dev/fd"))
    original_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    constrained_limit = min(original_limit[0], baseline + 12)
    if constrained_limit <= baseline + 4:
        pytest.skip("The process has no safe descriptor headroom to constrain")

    resource.setrlimit(
        resource.RLIMIT_NOFILE,
        (constrained_limit, original_limit[1]),
    )
    try:
        services = []
        for index in range(20):
            project = init_project(tmp_path / f"project-{index}")
            services.append(MappingService(project))
            assert services[-1].project_info()["mappings"] == 0
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, original_limit)


def test_raises_on_missing_database():
    """MappingService should raise ValueError immediately when the path has no normflow.db."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match="Not a NormFlow Project"):
            MappingService(tmpdir)


def test_accepts_initialized_project():
    """MappingService should accept a path that was just initialized."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_project(tmpdir)
        service = MappingService(tmpdir)
        assert service._path.resolve() == Path(tmpdir).resolve()


def test_project_info_reports_missing_semantic_index():
    """A new Project should report that no semantic index exists yet."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_project(tmpdir)

        info = MappingService(tmpdir).project_info()

        assert info["semantic_index_status"] == "missing"


def test_project_info_reports_legacy_index_as_unverified():
    """An index without freshness metadata should be rebuilt before trusted use."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = init_project(tmpdir)
        index_dir = project / ".normflow" / "faiss_index"
        index_dir.mkdir(parents=True)
        (index_dir / "index.faiss").write_bytes(b"legacy")

        info = MappingService(tmpdir).project_info()

        assert info["semantic_index_status"] == "unverified"


def test_persistence_is_not_exposed_by_the_mapping_interface():
    """Sessions and SQLModel schemas remain Mapping implementation details."""
    with tempfile.TemporaryDirectory() as tmpdir:
        init_project(tmpdir)
        service = MappingService(tmpdir)

        assert not hasattr(service, "session")
        assert not hasattr(mapping_module, "ExampleMapping")
        assert not hasattr(mapping_module, "ReviewItem")
        assert not hasattr(mapping_module, "SQLModel")
        assert not hasattr(mapping_module, "Session")
