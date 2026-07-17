"""Release-grade continuous-integration workflow contract tests."""

from pathlib import Path
import re


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/ci.yml"


def test_ci_runs_for_pull_requests_and_main_pushes_with_read_only_permissions():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "permissions:\n  contents: read" in workflow


def test_ci_cancels_only_stale_runs_for_the_same_pull_request_or_branch():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert (
        "concurrency:\n"
        "  group: ${{ github.workflow }}-"
        "${{ github.event.pull_request.number || github.ref }}\n"
        "  cancel-in-progress: true"
    ) in workflow


def test_ci_pins_supported_toolchains_and_caches_locked_dependencies():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    action_references = re.findall(r"^\s*uses:\s+([^\s#]+)", workflow, re.MULTILINE)
    assert action_references
    assert all(
        re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference)
        for reference in action_references
    )
    assert action_references == [
        "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
        "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020",
        "astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990",
    ]
    assert "python-version: \"3.13.14\"" in workflow
    assert "node-version: \"22.23.1\"" in workflow
    assert "version: \"0.11.29\"" in workflow
    assert "cache: npm" in workflow
    assert "cache-dependency-path: package-lock.json" in workflow
    assert "enable-cache: true" in workflow
    assert "cache-dependency-glob: uv.lock" in workflow


def test_ci_builds_frontend_before_running_all_application_tests():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    expected_stages = (
        "Install frontend dependencies",
        "Test frontend",
        "Typecheck frontend",
        "Build frontend from clean output",
        "Run Python suite",
    )
    assert all(f"- name: {stage}" in workflow for stage in expected_stages)
    commands = (
        "npm ci",
        "npm test",
        "npm run typecheck",
        "rm -rf src/normflow/static",
        "npm run build",
        "uv run --frozen --extra test pytest",
    )
    positions = [workflow.index(command) for command in commands]
    assert positions == sorted(positions)


def test_ci_disables_typer_terminal_rendering_for_the_python_suite():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    python_stage = workflow.split("- name: Run Python suite", 1)[1].split(
        "\n      - name:", 1
    )[0]

    assert 'env:\n          _TYPER_FORCE_DISABLE_TERMINAL: "1"' in python_stage


def test_ci_builds_the_wheel_and_smokes_the_reproducible_release_payload():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "- name: Build and inspect release wheel" in workflow
    assert "./scripts/build-wheel dist/ci-wheel" in workflow
    assert "- name: Build and smoke reproducible release payload" in workflow
    assert (
        "./scripts/build-release-payload dist/ci-release-payload" in workflow
    )
    assert workflow.index("./scripts/build-wheel") < workflow.index(
        "./scripts/build-release-payload"
    )


def test_ci_never_publishes_or_exposes_credentials_and_failures_stay_visible():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    lowered = workflow.lower()

    assert "persist-credentials: false" in workflow
    assert "pull_request_target:" not in workflow
    assert "${{ secrets." not in lowered
    assert "id-token:" not in lowered
    assert not re.search(r"\bwrite\b", lowered)
    assert "continue-on-error:" not in lowered
    assert not re.search(r"\b(?:twine|npm)\s+publish\b", lowered)
    assert not re.search(r"\bgh\s+release\b", lowered)
    assert "name: Release validation" in workflow
