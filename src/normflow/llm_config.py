"""Server-side LLM configuration for a NormFlow Project."""

from collections.abc import MutableMapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import dotenv_values

from .project import Project


DEFAULT_LLM_MODEL = "gpt-4o-mini"
_LLM_ENVIRONMENT_VARIABLES = (
    "OPENAI_API_KEY",
    "NORMFLOW_LLM_BASE_URL",
    "NORMFLOW_LLM_MODEL",
)


@dataclass(frozen=True)
class LLMConfig:
    """Validated server-side values used for LLM requests."""

    api_key: str
    base_url: str | None
    model: str


def load_llm_config(
    project: Project,
    environment: MutableMapping[str, str],
) -> LLMConfig | None:
    """Load shell-first LLM settings with a Project ``.env`` fallback."""
    dotenv = dotenv_values(project.root / ".env")
    for name in _LLM_ENVIRONMENT_VARIABLES:
        value = dotenv.get(name)
        if name not in environment and value is not None:
            environment[name] = value

    if not any(name in environment for name in _LLM_ENVIRONMENT_VARIABLES):
        return None

    api_key = environment.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required and must not be blank.")

    model_value = environment.get("NORMFLOW_LLM_MODEL")
    model = (DEFAULT_LLM_MODEL if model_value is None else model_value).strip()
    if not model:
        raise ValueError("NORMFLOW_LLM_MODEL must not be blank when configured.")

    base_url = environment.get("NORMFLOW_LLM_BASE_URL")
    if base_url is not None:
        base_url = base_url.strip()
        try:
            parsed_url = urlsplit(base_url)
            valid_base_url = (
                parsed_url.scheme in {"http", "https"}
                and parsed_url.hostname is not None
                and not any(character.isspace() for character in base_url)
                and (parsed_url.port is None or 0 < parsed_url.port < 65536)
            )
        except ValueError:
            valid_base_url = False
        if not valid_base_url:
            raise ValueError(
                "NORMFLOW_LLM_BASE_URL must be a valid HTTP(S) URL when configured."
            )
        if model_value is None:
            raise ValueError(
                "NORMFLOW_LLM_MODEL is required when NORMFLOW_LLM_BASE_URL is configured."
            )

    return LLMConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
