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


def test_readme_documents_safe_ui_automation_and_project_switching():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "normflow ui --no-open" in readme
    assert re.search(r"normflow ui --(?:no-open .*--port|port).*\d", readme)
    assert re.search(r"never nest|must not be nested|do not nest", readme, re.IGNORECASE)
    assert re.search(r"stop.*(?:cd|change director).*normflow ui", readme, re.IGNORECASE | re.DOTALL)


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
