"""Public LLM configuration loading and validation behavior."""

import pytest

from normflow.llm_config import DEFAULT_LLM_MODEL, LLMConfig, load_llm_config
from normflow.project import Project


def test_load_llm_config_uses_project_dotenv_with_default_model(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".env").write_text("OPENAI_API_KEY=project-key\n", encoding="utf-8")
    project = Project(root=project_root, database=project_root / "normflow.db")
    environment: dict[str, str] = {}

    config = load_llm_config(project, environment)

    assert config == LLMConfig(
        api_key="project-key",
        base_url=None,
        model=DEFAULT_LLM_MODEL,
    )
    assert environment["OPENAI_API_KEY"] == "project-key"


def test_load_llm_config_preserves_shell_values_over_project_dotenv(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".env").write_text(
        "OPENAI_API_KEY=project-key\n"
        "OPENAI_BASE_URL=https://project.example/v1\n"
        "NORMFLOW_LLM_MODEL=project-model\n",
        encoding="utf-8",
    )
    project = Project(root=project_root, database=project_root / "normflow.db")
    environment = {
        "OPENAI_API_KEY": "shell-key",
        "OPENAI_BASE_URL": "https://shell.example/v1",
        "NORMFLOW_LLM_MODEL": "shell-model",
    }

    config = load_llm_config(project, environment)

    assert config == LLMConfig(
        api_key="shell-key",
        base_url="https://shell.example/v1",
        model="shell-model",
    )


@pytest.mark.parametrize("environment", [{}, {"OPENAI_API_KEY": "  "}])
def test_load_llm_config_requires_nonblank_api_key(tmp_path, environment):
    project_root = tmp_path / "project"
    project_root.mkdir()
    project = Project(root=project_root, database=project_root / "normflow.db")

    with pytest.raises(ValueError, match=r"OPENAI_API_KEY.*required.*blank"):
        load_llm_config(project, environment)


def test_load_llm_config_rejects_explicitly_blank_model(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    project = Project(root=project_root, database=project_root / "normflow.db")

    with pytest.raises(ValueError, match=r"NORMFLOW_LLM_MODEL.*blank"):
        load_llm_config(
            project,
            {"OPENAI_API_KEY": "test-key", "NORMFLOW_LLM_MODEL": "  "},
        )


@pytest.mark.parametrize(
    "base_url",
    ["not-a-url", "ftp://llm.example/v1", "http://[bad", "  "],
)
def test_load_llm_config_rejects_invalid_configured_base_url(tmp_path, base_url):
    project_root = tmp_path / "project"
    project_root.mkdir()
    project = Project(root=project_root, database=project_root / "normflow.db")

    with pytest.raises(ValueError, match=r"OPENAI_BASE_URL.*valid HTTP.*URL"):
        load_llm_config(
            project,
            {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": base_url},
        )
