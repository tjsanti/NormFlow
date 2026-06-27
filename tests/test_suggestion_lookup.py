"""Tests for Suggestion lookup fallback behavior."""

from normflow.suggestion_lookup import SuggestionLookup


def test_llm_fallback_uses_examples_when_exact_and_semantic_miss():
    """LLM fallback should receive nearby Mapping examples after semantic miss."""
    examples = [{"raw_text": "organised", "normalized_text": "organized", "score": 0.2}]
    llm_calls = []

    class FakeIndex:
        def exists(self) -> bool:
            return True

        def search(self, query_text: str, *, limit: int = 1, threshold: float = 0.85):
            if threshold == 0.0:
                return examples
            return []

    def fake_llm(raw_text, few_shot_examples):
        llm_calls.append((raw_text, few_shot_examples))
        return "organized"

    lookup = SuggestionLookup(
        exact_lookup=lambda raw_text: None,
        index=FakeIndex(),
        llm_suggest=fake_llm,
    )

    suggestions = lookup.lookup("orgnisd", semantic=True, llm=True, threshold=0.85)

    assert [item.model_dump() for item in suggestions] == [
        {"suggested_text": "organized", "method": "llm", "confidence": None}
    ]
    assert llm_calls == [("orgnisd", examples)]
