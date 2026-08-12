"""LLM-based normalization suggestion using few-shot examples."""

from __future__ import annotations

import os
from functools import partial
from functools import cache
from collections.abc import Callable

from openai import OpenAI

from .llm_config import LLMConfig


_SYSTEM_PROMPT = (
    "You normalize text based on the pattern shown in the examples. "
    "Return only the normalized text — nothing else."
)


@cache
def build_client(config: LLMConfig | None = None) -> OpenAI:
    if config is None:
        return OpenAI(base_url="https://api.openai.com/v1")
    return OpenAI(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.openai.com/v1",
    )


def suggest(
    raw_text: str,
    examples: list[dict[str, object]],
    *,
    config: LLMConfig | None = None,
) -> str:
    """Ask an LLM to normalize raw_text using examples as few-shot context."""
    model = config.model if config is not None else os.environ.get(
        "NORMFLOW_LLM_MODEL", "gpt-4o-mini",
    )

    few_shot = "\n".join(
        f"Input: {e['raw_text']} → Output: {e['normalized_text']}"
        for e in examples
    )

    user_msg = f"{few_shot}\nInput: {raw_text} → Output:"

    client = build_client(config)
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


def configured_suggest(config: LLMConfig) -> Callable[[str, list[dict[str, object]]], str]:
    """Bind validated endpoint-neutral configuration to the provider adapter."""
    return partial(suggest, config=config)
