"""Public LLM configuration loading and validation behavior."""

import pytest

from normflow.llm_config import DEFAULT_LLM_MODEL, LLMConfig, load_llm_config
from normflow.project import Project, resolve_project
from normflow.project_service import init_project
from normflow.service_factory import build_mapping_service


def test_load_llm_config_disables_llm_when_no_settings_are_present(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    project = Project(root=project_root, database=project_root / "normflow.db")

    assert load_llm_config(project, {}) is None


def test_service_factory_binds_a_project_without_llm_fallback(tmp_path):
    project = resolve_project(init_project(tmp_path / "project"))

    assert build_mapping_service(project).project_info()["llm_mode"] == "disabled"


def test_service_factory_defaults_to_disabled_when_no_llm_configuration_is_resolved(tmp_path):
    project = resolve_project(init_project(tmp_path / "project"))

    service = build_mapping_service(project, environment={})

    assert service.project_info()["llm_mode"] == "disabled"


def test_service_factory_uses_supplied_validated_llm_configuration(tmp_path):
    project = resolve_project(init_project(tmp_path / "project"))

    service = build_mapping_service(
        project,
        llm_config=LLMConfig(api_key="test-key", base_url=None, model="test-model"),
    )

    assert service.project_info()["llm_mode"] == "enabled"


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
    assert environment == {}


def test_load_llm_config_preserves_shell_values_over_project_dotenv(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".env").write_text(
        "OPENAI_API_KEY=project-key\n"
        "NORMFLOW_LLM_BASE_URL=https://project.example/v1\n"
        "NORMFLOW_LLM_MODEL=project-model\n",
        encoding="utf-8",
    )
    project = Project(root=project_root, database=project_root / "normflow.db")
    environment = {
        "OPENAI_API_KEY": "shell-key",
        "NORMFLOW_LLM_BASE_URL": "https://shell.example/v1",
        "NORMFLOW_LLM_MODEL": "shell-model",
    }

    config = load_llm_config(project, environment)

    assert config == LLMConfig(
        api_key="shell-key",
        base_url="https://shell.example/v1",
        model="shell-model",
    )


@pytest.mark.parametrize("environment", [{"OPENAI_API_KEY": "  "}])
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


def test_load_llm_config_requires_model_for_a_custom_endpoint(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    project = Project(root=project_root, database=project_root / "normflow.db")

    with pytest.raises(ValueError, match=r"NORMFLOW_LLM_MODEL.*required.*NORMFLOW_LLM_BASE_URL"):
        load_llm_config(
            project,
            {
                "OPENAI_API_KEY": "test-key",
                "NORMFLOW_LLM_BASE_URL": "https://llm.example/v1",
            },
        )


def test_load_llm_config_ignores_legacy_openai_base_url(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    project = Project(root=project_root, database=project_root / "normflow.db")

    config = load_llm_config(
        project,
        {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "not-a-url",
        },
    )

    assert config == LLMConfig(
        api_key="test-key",
        base_url=None,
        model=DEFAULT_LLM_MODEL,
    )


@pytest.mark.parametrize(
    "base_url",
    ["not-a-url", "ftp://llm.example/v1", "http://[bad", "  "],
)
def test_load_llm_config_rejects_invalid_configured_base_url(tmp_path, base_url):
    project_root = tmp_path / "project"
    project_root.mkdir()
    project = Project(root=project_root, database=project_root / "normflow.db")

    with pytest.raises(ValueError, match=r"NORMFLOW_LLM_BASE_URL.*valid HTTP.*URL"):
        load_llm_config(
            project,
            {
                "OPENAI_API_KEY": "test-key",
                "NORMFLOW_LLM_BASE_URL": base_url,
                "NORMFLOW_LLM_MODEL": "test-model",
            },
        )
