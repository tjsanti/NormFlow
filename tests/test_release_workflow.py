"""Contract tests for the release-draft GitHub Actions workflow."""

from pathlib import Path
import re


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/release-draft.yml"


def _parse_workflow() -> str:
    """Return raw workflow YAML text."""
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_draft_is_dispatch_triggered_with_required_version():
    """The workflow must only be triggered manually with a required version input."""
    text = _parse_workflow()
    assert "workflow_dispatch:" in text
    assert "inputs:" in text
    assert "version:" in text
    assert "required: true" in text
    assert "type: string" in text


def test_release_draft_allows_optional_ref_input():
    """The workflow must accept an optional ref input defaulting to main."""
    text = _parse_workflow()
    assert "ref:" in text
    assert "default: main" in text


def test_release_draft_has_read_write_contents_permission():
    """The workflow needs write access to create tags and releases."""
    text = _parse_workflow()
    assert "permissions:\n  contents: write" in text


def test_release_draft_is_concurrent_per_version():
    """Multiple runs for the same version should not overlap."""
    text = _parse_workflow()
    assert "concurrency:" in text
    assert "group: ${{ github.workflow }}-${{ inputs.version }}" in text


def test_release_draft_pins_checkout_action():
    """All checkout steps must use pinned action references."""
    text = _parse_workflow()
    action_refs = re.findall(
        r"uses:\s+([^\s#]+)@([^\s#]+)", text
    )
    assert action_refs
    for org_repo, sha_ref in action_refs:
        assert re.fullmatch(r"[0-9a-f]{40}", sha_ref), (
            f"Action {org_repo}@{sha_ref} is not pinned to a 40-char SHA"
        )


def test_release_draft_uses_same_toolchain_as_ci():
    """The release workflow should use the same pinned toolchain as CI."""
    text = _parse_workflow()
    assert 'python-version: "3.13.14"' in text
    assert 'node-version: "22.23.1"' in text
    assert 'version: "0.11.29"' in text


def test_release_draft_has_validate_job():
    """There must be a job that validates version format and tag availability."""
    text = _parse_workflow()
    assert "validate-version-and-tag:" in text
    assert "Check version format" in text
    assert "Check out repository" in text
    assert "Verify no tag exists" in text
    assert "Verify no draft release exists" in text


def test_release_draft_resolves_ref_from_step_environment():
    """SHA resolution must pass the ref safely through the step environment."""
    text = _parse_workflow()
    resolve_step = text.split("      - name: Resolve ref to SHA\n", 1)[1].split(
        "\n      - name:", 1
    )[0]
    env_block, run_command = resolve_step.split("        run: |\n", 1)
    assert "INPUT_REF: ${{ inputs.ref }}" in env_block
    assert resolve_step.count("${{ inputs.ref }}") == 1
    assert "${{ inputs.ref }}" not in run_command
    assert 'echo "Resolved $INPUT_REF to $sha"' in run_command


def test_release_draft_has_macos_and_linux_build_jobs():
    """There must be parallel payload build jobs for macOS and Linux."""
    text = _parse_workflow()
    assert "build-payload-macos:" in text
    assert "build-payload-linux:" in text
    assert "runs-on: macos-15" in text
    assert "runs-on: ubuntu-24.04" in text


def test_release_draft_jobs_require_validation():
    """Build jobs must depend on the validation job."""
    text = _parse_workflow()
    macos_section = text.split("  build-payload-macos:\n", 1)[1].split("\n  build-payload-linux:", 1)[0]
    linux_section = text.split("  build-payload-linux:\n", 1)[1].split("\n  create-draft-release:", 1)[0]

    assert "needs: validate-version-and-tag" in macos_section
    assert "needs: validate-version-and-tag" in linux_section


def test_release_draft_verifies_cli_version_flags():
    """Build jobs must verify --version and -V output match the target version."""
    text = _parse_workflow()
    assert "Verify CLI version flags match" in text
    # Version check is extracted to scripts/release_version_check.sh
    scripts_text = (Path(__file__).parents[1] / "scripts" / "release_version_check.sh").read_text()
    assert "normflow --version" in scripts_text
    assert "normflow -V" in scripts_text


def test_release_version_check_captures_cli_version_output():
    """Both version invocations must be command substitutions, not assignments."""
    scripts_text = (Path(__file__).parents[1] / "scripts" / "release_version_check.sh").read_text()
    assert 'cli_version=$("$temp_dir/venv/bin/normflow" --version 2>/dev/null || true)' in scripts_text
    assert 'cli_version_long=$("$temp_dir/venv/bin/normflow" -V 2>/dev/null || true)' in scripts_text


def test_release_draft_platform_version_checks_are_consistent():
    """Both platform checks should only discover the wheel and invoke the checker."""
    text = _parse_workflow()
    platform_sections = (
        text.split("  build-payload-macos:\n", 1)[1].split(
            "\n  build-payload-linux:", 1
        )[0],
        text.split("  build-payload-linux:\n", 1)[1].split(
            "\n  create-draft-release:", 1
        )[0],
    )

    for platform_section in platform_sections:
        version_step = platform_section.split(
            "      - name: Verify CLI version flags match\n", 1
        )[1].split("\n      - name:", 1)[0]
        assert "wheel=$(find dist/release-wheel" in version_step
        assert 'expected_version="${{ inputs.version }}"' in version_step
        assert 'scripts/release_version_check.sh "$wheel" "$expected_version"' in version_step
        for unused_setup in ("wheel_dir=", "staging=", "python3 -m zipfile", "rm -rf"):
            assert unused_setup not in version_step


def test_release_draft_runs_install_sh_integration_smoke_test():
    """Build jobs must run install.sh integration smoke tests."""
    text = _parse_workflow()
    # Each platform has one step that exercises clean and repeated installs.
    assert text.count("Install.sh integration smoke test") == 2
    assert "smoke test" in text.lower()


def test_release_draft_smoke_test_tests_offline_model_and_api():
    """The smoke test must verify offline API and model usage."""
    text = _parse_workflow()
    assert "Install.sh integration smoke test" in text
    # Smoke test is extracted to scripts/release_smoke_test.sh
    smoke_text = (Path(__file__).parents[1] / "scripts" / "release_smoke_test.sh").read_text()
    assert "HF_HUB_OFFLINE=1" in smoke_text
    assert "TRANSFORMERS_OFFLINE=1" in smoke_text
    assert "NORMFLOW_DISABLE_NETWORK=1" in smoke_text
    assert "create_app" in smoke_text
    assert "load_embedding_model" in smoke_text


def test_release_draft_workflow_has_valid_yaml_structure():
    """The workflow YAML must parse without errors (catches broken block scalars)."""
    import yaml

    text = _parse_workflow()
    # yaml.safe_load raises on malformed YAML (e.g., broken indentation, unquoted strings)
    data = yaml.safe_load(text)
    assert isinstance(data, dict), "workflow must be a YAML mapping"
    assert "jobs" in data, "workflow must have a jobs key"


def test_release_draft_uploads_payload_artifacts():
    """Build jobs must upload their payload as workflow artifacts."""
    text = _parse_workflow()
    assert "upload-artifact" in text
    assert "release-payload-macos-aarch64-py313" in text
    assert "release-payload-linux-x86_64-py313" in text


def test_release_draft_creates_draft_release_only():
    """The create job must create a draft release, never auto-publish."""
    text = _parse_workflow()
    assert "Create draft release" in text
    assert "draft: true" in text
    assert "Generate release notes" in text or "generate_release_notes: true" in text


def test_release_draft_create_job_depends_on_both_platforms():
    """The release creation job must depend on both macOS and Linux builds."""
    text = _parse_workflow()
    create_section = text.split("create-draft-release:", 1)[1]
    assert "build-payload-macos" in create_section
    assert "build-payload-linux" in create_section


def test_release_draft_verifies_release_after_creation():
    """The create job must verify the release was created correctly."""
    text = _parse_workflow()
    assert "Verify release assets" in text
    assert "isDraft" in text


def test_release_version_check_sh_uses_venv_not_checkout():
    """The version check script must install the wheel into a venv, not run against the checkout."""
    scripts_text = (
        Path(__file__).parents[1] / "scripts" / "release_version_check.sh"
    ).read_text()
    assert "uv venv" in scripts_text
    assert "uv pip install" in scripts_text
    assert "uv run" not in scripts_text


def test_release_draft_never_exposes_secrets_or_credentials():
    """The workflow must never contain secrets other than GITHUB_TOKEN."""
    text = _parse_workflow()
    lowered = text.lower()
    # No PyPI credentials
    assert "pypi" not in lowered
    assert "twine" not in lowered
    assert "npm publish" not in lowered
    # Only GITHUB_TOKEN should be used as a secret
    secret_refs = re.findall(r"\$\{\{\s*secrets\.(\w+)\s*\}\}", text)
    assert all(ref == "GITHUB_TOKEN" for ref in secret_refs), (
        f"Unexpected secrets: {set(secret_refs)}"
    )


def test_release_draft_never_auto_publishes():
    """The workflow must never auto-publish the release."""
    text = _parse_workflow()
    assert "gh release create" not in text
    assert "publish: true" not in text
    assert "draft: true" in text


def test_release_draft_refuses_existing_tag_or_release():
    """The validation job must refuse to proceed if the tag or release already exists."""
    text = _parse_workflow()
    validate_section = text.split("validate-version-and-tag:", 1)[1].split(
        "\nbuild-payload-macos:", 1
    )[0]
    assert "error: tag" in validate_section
    assert "error: draft release" in validate_section
    assert "exit 1" in validate_section


def test_release_draft_workflow_is_not_triggered_by_push_or_pr():
    """The workflow should not run on push or pull_request events."""
    text = _parse_workflow()
    on_section = text.split("on:\n", 1)[1].split("\n", 1)[0]
    assert "pull_request:" not in on_section
    assert "push:" not in on_section
