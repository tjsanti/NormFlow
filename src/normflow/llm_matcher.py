"""LLM-based normalization suggestion using few-shot examples."""

from __future__ import annotations

import os
from functools import cache

from openai import OpenAI


_SYSTEM_PROMPT = (
    "You normalize text based on the pattern shown in the examples. "
    "Return only the normalized text — nothing else."
)


@cache
def build_client() -> OpenAI:
    return OpenAI()


def suggest(raw_text: str, examples: list[dict[str, object]]) -> str:
    """Ask an LLM to normalize raw_text using examples as few-shot context."""
    model = os.environ.get("NORMFLOW_LLM_MODEL", "gpt-4o-mini")

    few_shot = "\n".join(
        f"Input: {e['raw_text']} → Output: {e['normalized_text']}"
        for e in examples
    )

    user_msg = f"{few_shot}\nInput: {raw_text} → Output:"

    client = build_client()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0,
        max_tokens=64,
    )

    return resp.choices[0].message.content.strip()
