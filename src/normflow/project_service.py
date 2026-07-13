"""Project initialization operations."""

from pathlib import Path

from .mapping_service import MappingService
from .project import Project, ProjectNotFoundError, ProjectNestingError, project_at, resolve_project


def _descendant_project(root: Path) -> Project | None:
    for database in sorted(root.rglob("normflow.db")):
        if database.parent != root:
            return project_at(database.parent)
    return None


def init_project(path: str | Path) -> Path:
    """Initialize a NormFlow Project at the given path."""
    project_root = Path(path).expanduser().resolve()

    db_path = project_root / "normflow.db"
    if db_path.exists() or db_path.is_symlink():
        project_at(project_root)
    else:
        try:
            ancestor = resolve_project(project_root.parent)
        except ProjectNotFoundError:
            pass
        else:
            msg = (
                f"Cannot initialize a nested Project at {project_root}; "
                f"the directory is already inside the Project at {ancestor.root}."
            )
            raise ProjectNestingError(msg)

        descendant = _descendant_project(project_root) if project_root.is_dir() else None
        if descendant is not None:
            msg = (
                f"Cannot initialize the Project at {project_root}; it would contain the "
                f"nested Project at {descendant.root}."
            )
            raise ProjectNestingError(msg)

    project_root.mkdir(parents=True, exist_ok=True)

    for directory in ("input", "output", "samples", ".normflow"):
        (project_root / directory).mkdir(exist_ok=True)

    MappingService.initialize(project_root)

    return project_root
