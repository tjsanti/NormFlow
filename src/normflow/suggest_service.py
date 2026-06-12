"""Suggestion service: generate normalization suggestions for raw text."""

from pydantic import BaseModel, Field
from sqlmodel import select

from .models import ExampleMapping
from .workspace import WorkspaceService


class SuggestionItem(BaseModel):
    """A single suggestion returned by the suggest command."""

    suggested_text: str
    method: str
    confidence: float = Field(ge=0.0, le=1.0)


class SuggestionResult(BaseModel):
    """The output shape of the suggest command."""

    raw_text: str
    suggestions: list[SuggestionItem]


def suggest_exact(
    workspace_path: str,
    raw_text: str,
    limit: int = 5,
) -> SuggestionResult:
    """Look up exact-match suggestions from the mapping library.

    Queries ExampleMapping by raw_text. Returns at most one result
    (enforced by limit), wrapped in the suggest output shape.

    Future slices can add more retrieval strategies while keeping
    the same return type.
    """
    ws = WorkspaceService(workspace_path)

    suggestions: list[SuggestionItem] = []

    with ws.session() as session:
        mapping = session.exec(
            select(ExampleMapping).where(ExampleMapping.raw_text == raw_text)
        ).first()

        if mapping:
            suggestions.append(SuggestionItem(
                suggested_text=mapping.normalized_text,
                method="exact",
                confidence=1.0,
            ))

    # Apply limit (no-op for exact match, but establishes the contract)
    suggestions = suggestions[:limit]

    return SuggestionResult(raw_text=raw_text, suggestions=suggestions)
