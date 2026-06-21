"""Review service: accept, edit, and list pending suggestions."""

from sqlmodel import select

from .models import ExampleMapping, Suggestion
from .workspace import WorkspaceService


def list_pending(workspace_path: str) -> list[dict]:
    """Return all suggestions with status 'pending'."""
    ws = WorkspaceService(workspace_path)

    with ws.session() as session:
        suggestions = session.exec(
            select(Suggestion).where(Suggestion.status == "pending")
        ).all()

    return [
        {
            "id": s.id,
            "raw_text": s.raw_text,
            "suggested_text": s.suggested_text,
        }
        for s in suggestions
    ]


def accept_suggestion(workspace_path: str, record_id: int) -> None:
    """Mark a suggestion as accepted and insert the mapping.

    Raises ValueError if the suggestion is not found or already reviewed.
    """
    ws = WorkspaceService(workspace_path)

    with ws.session() as session:
        suggestion = session.exec(
            select(Suggestion).where(Suggestion.id == record_id)
        ).first()

        if suggestion is None:
            msg = f"Suggestion with id {record_id} not found"
            raise ValueError(msg)

        if suggestion.status != "pending":
            msg = f"Suggestion {record_id} already reviewed with status '{suggestion.status}'"
            raise ValueError(msg)

        suggestion.status = "accepted"
        session.add(
            ExampleMapping(
                raw_text=suggestion.raw_text,
                normalized_text=suggestion.suggested_text,
            )
        )
        session.commit()


def edit_suggestion(workspace_path: str, record_id: int, normalized_text: str) -> None:
    """Mark a suggestion as accepted_edited and insert mapping with edited text.

    Raises ValueError if the suggestion is not found or already reviewed.
    """
    ws = WorkspaceService(workspace_path)

    with ws.session() as session:
        suggestion = session.exec(
            select(Suggestion).where(Suggestion.id == record_id)
        ).first()

        if suggestion is None:
            msg = f"Suggestion with id {record_id} not found"
            raise ValueError(msg)

        if suggestion.status != "pending":
            msg = f"Suggestion {record_id} already reviewed with status '{suggestion.status}'"
            raise ValueError(msg)

        suggestion.status = "accepted_edited"
        session.add(
            ExampleMapping(
                raw_text=suggestion.raw_text,
                normalized_text=normalized_text,
            )
        )
        session.commit()
