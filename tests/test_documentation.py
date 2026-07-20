"""Checks for the maintained installed-workflow documentation."""

from pathlib import Path
import re


ROOT = Path(__file__).parents[1]


def test_readme_documents_current_directory_project_workflow():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    workflow = re.compile(
        r"mkdir my-project.*cd my-project.*normflow init.*normflow ui",
        re.DOTALL,
    )
    assert workflow.search(readme)
    assert "--workspace" not in readme
    assert "--project" not in readme
    assert re.search(r"subdirector", readme, re.IGNORECASE)
    assert re.search(r"discover", readme, re.IGNORECASE)


def test_release_docs_define_the_public_distribution_contract():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    adr_path = ROOT / "docs" / "adr" / "0005-distribute-through-github-releases.md"

    assert "https://github.com/tjsanti/NormFlow/releases/latest/download/install.sh" in readme
    assert "uv tool install normflow" not in readme
    assert "pip install normflow" not in readme
    assert re.search(r"macOS.*Linux", readme, re.IGNORECASE | re.DOTALL)
    assert re.search(r"Project data", readme, re.IGNORECASE)
    assert re.search(r"migrate safely.*refuse clearly", readme, re.IGNORECASE | re.DOTALL)

    assert adr_path.exists()
    decision = adr_path.read_text(encoding="utf-8")
    assert re.search(r"^Status: Accepted$", decision, re.MULTILINE)
    assert re.search(r"immutable\s+GitHub\s+Releases", decision, re.IGNORECASE)
    assert re.search(r"never.*PyPI", decision, re.IGNORECASE | re.DOTALL)
    assert re.search(r"no\s+silent\s+self.update", decision, re.IGNORECASE)
    assert re.search(r"migrate safely.*refuse clearly", decision, re.IGNORECASE | re.DOTALL)


def test_release_docs_explain_managed_uninstall_and_preserved_user_data():
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())

    assert "normflow uninstall" in readme
    assert "Projects will be preserved" in readme
    assert "private uv bootstrap, Python runtime, bundled model, and NormFlow-owned installation caches" in readme
    assert "shell configuration" in readme


def test_readme_documents_safe_ui_automation_and_project_switching():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "normflow ui --no-open" in readme
    assert re.search(r"normflow ui --(?:no-open .*--port|port).*\d", readme)
    assert re.search(r"never nest|must not be nested|do not nest", readme, re.IGNORECASE)
    assert re.search(r"stop.*(?:cd|change director).*normflow ui", readme, re.IGNORECASE | re.DOTALL)


def test_readme_documents_server_side_llm_configuration():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" in readme
    assert "OPENAI_BASE_URL" in readme
    assert "NORMFLOW_LLM_MODEL" in readme
    assert re.search(r"Project.*\.env", readme, re.IGNORECASE | re.DOTALL)
    assert re.search(r"shell.*(?:precedence|override|preferred)", readme, re.IGNORECASE)
    assert re.search(r"server.side", readme, re.IGNORECASE)


def test_maintained_interfaces_use_project_vocabulary():
    maintained = [ROOT / "README.md", ROOT / "src" / "normflow", ROOT / "frontend" / "src"]
    obsolete = []
    for location in maintained:
        files = [location] if location.is_file() else location.rglob("*")
        for path in files:
            if (
                not path.is_file()
                or path.suffix not in {".md", ".py", ".ts", ".css", ".html"}
                or "static/assets" in path.as_posix()
            ):
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search("workspace", line, re.IGNORECASE):
                    obsolete.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")

    assert obsolete == []


def test_readme_documents_unified_review_item_acceptance():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "normflow review accept --review-item-id 1" in readme
    assert "--normalized-text \"Oxygen Sensor\"" in readme
    assert "edit-and-accept" not in readme
    assert "--record-id" not in readme


def test_readme_documents_durable_batch_import_workflow():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "normflow batch-import records.csv --column name" in readme
    assert "normflow batch-import-status [RUN_ID]" in readme
    assert (
        "normflow batch-import-retry RUN_ID records.csv --column name" in readme
    )
    assert "normflow export-batch results.csv --source-column name" in readme
    assert re.search(r"exact.*semantic.*LLM", readme, re.IGNORECASE | re.DOTALL)
    assert re.search(r"all.or.nothing|atomic", readme, re.IGNORECASE)
    assert re.search(r"sole retained Batch CSV", readme, re.IGNORECASE)
    assert re.search(
        r"(?:ui.*batch-import|batch-import.*ui).*require(?:s)?.*LLM configuration",
        readme,
        re.IGNORECASE | re.DOTALL,
    )


def test_domain_docs_define_the_batch_import_run_contract():
    context = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    adr = ROOT / "docs" / "adr" / "0001-batch-import-coordination.md"

    assert re.search(
        r"\*\*Batch Import Run\*\*:\s+One identified attempt",
        context,
    )
    assert adr.exists()

    decision = adr.read_text(encoding="utf-8")
    assert re.search(r"^Status: Accepted$", decision, re.MULTILINE)
    for heading in (
        "## Coordination and ownership",
        "## Run lifecycle and recovery",
        "## CLI and HTTP contract",
        "## Acceptance scenarios",
    ):
        assert heading in decision
