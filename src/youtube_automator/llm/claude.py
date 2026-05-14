"""Thin wrapper around the Anthropic Claude API.

Centralizes model selection, retries and token accounting so callers across the
pipeline (research, script, metadata) don't repeat boilerplate.

Default models come from settings.llm.models; callers pass a task name
(e.g. "script_generation") and this module resolves it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import get_env, get_settings


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


def model_for(task: str) -> str:
    """Resolve a task name to a model id, falling back to a sensible default."""
    models = get_settings().llm.models
    if task in models:
        return models[task]
    return "claude-sonnet-4-6"


def complete(
    task: str,
    *,
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
) -> LLMResponse:
    """Call Claude with the model bound to `task`.

    NOTE: stub. Implementation will import `anthropic` lazily, build the client
    from env, retry on rate limits, and return a typed response.
    """
    raise NotImplementedError("Stub — implement with anthropic SDK")
