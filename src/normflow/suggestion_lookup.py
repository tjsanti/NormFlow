"""Suggestion lookup: exact, semantic, then LLM fallback."""

from collections.abc import Callable

from pydantic import BaseModel, Field

from .llm_matcher import suggest as default_llm_suggest


class SuggestionItem(BaseModel):
    """A single suggestion returned by lookup."""

    suggested_text: str
    method: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class SuggestionLookup:
    """Deep module for Suggestion fallback order and failure policy."""

    def __init__(
        self,
        exact_lookup: Callable[[str], str | None],
        index,
        llm_suggest: Callable[[str, list[dict[str, object]]], str] = default_llm_suggest,
    ) -> None:
        self._exact_lookup = exact_lookup
        self._index = index
        self._llm_suggest = llm_suggest

    def lookup(
        self,
        raw_text: str,
        *,
        semantic: bool = True,
        llm: bool = True,
        threshold: float = 0.85,
        limit: int = 1,
    ) -> list[SuggestionItem]:
        suggestions: list[SuggestionItem] = []

        exact = self._exact_lookup(raw_text)
        if exact is not None:
            suggestions.append(SuggestionItem(
                suggested_text=exact,
                method="exact",
                confidence=1.0,
            ))

        if not suggestions and semantic and self._index.exists():
            for result in self._index.search(raw_text, limit=1, threshold=threshold):
                suggestions.append(SuggestionItem(
                    suggested_text=result["normalized_text"],
                    method="semantic",
                    confidence=result["score"],
                ))

        if not suggestions and llm and self._index.exists():
            examples = self._index.search(raw_text, limit=3, threshold=0.0)
            if examples:
                try:
                    normalized = self._llm_suggest(raw_text, examples)
                    suggestions.append(SuggestionItem(
                        suggested_text=normalized,
                        method="llm",
                    ))
                except Exception:
                    pass  # ponytail: silent fallback, user re-tries or edits manually

        return suggestions[:limit]
