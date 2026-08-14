"""Compose a Project's MappingService with optional LLM fallback."""

from collections.abc import Mapping

from .llm_config import LLMConfig, load_llm_config
from .llm_matcher import configured_suggest
from .mapping_service import MappingService
from .project import Project


def build_mapping_service(
    project: Project,
    *,
    llm_config: LLMConfig | None = None,
    environment: Mapping[str, str] | None = None,
) -> MappingService:
    """Bind one Project service to resolved LLM configuration or an explicit bypass."""
    config = (
        load_llm_config(project, environment)
        if environment is not None else llm_config
    )
    return MappingService(
        str(project.root),
        llm_suggest=configured_suggest(config) if config is not None else None,
        llm_enabled=config is not None,
    )
