"""Compose a Project's MappingService with optional LLM fallback."""

import os
from collections.abc import MutableMapping

from .llm_config import LLMConfig, load_llm_config
from .llm_matcher import configured_suggest
from .mapping_service import MappingService
from .project import Project


def build_mapping_service(
    project: Project,
    *,
    llm_enabled: bool = True,
    llm_config: LLMConfig | None = None,
    environment: MutableMapping[str, str] | None = None,
) -> MappingService:
    """Bind one Project service to validated optional LLM configuration."""
    config = llm_config
    if config is None and llm_enabled:
        config = load_llm_config(project, os.environ if environment is None else environment)
    return MappingService(
        str(project.root),
        llm_suggest=configured_suggest(config) if config is not None else None,
        llm_enabled=config is not None,
    )
