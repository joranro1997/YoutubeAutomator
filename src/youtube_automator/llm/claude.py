"""Thin wrapper around the Anthropic Claude API.

Centralises:
- Client construction (lazy, reads ANTHROPIC_API_KEY from env).
- Model selection via task names from settings.yaml.
- Prompt caching: when the caller marks a system block as cacheable, we tag
  the LAST cacheable block with `cache_control={"type": "ephemeral"}` so the
  Anthropic API caches it for the 5-minute TTL. This matters a lot for us —
  the style corpus + the contract guardrails are ~10k tokens reused across
  every script/metadata generation in a session.
- Retries on overloaded/rate-limit errors with exponential backoff.

Callers pass a task name (e.g. "script_generation") and this module resolves
the model from settings.llm.models.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Any, Iterable, Literal

import anthropic
from anthropic import APIStatusError, APIConnectionError

from ..config import get_env, get_settings


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class SystemBlock:
    """A system-prompt segment, optionally marked as cacheable.

    Order matters: blocks are concatenated in the order given. Per Anthropic's
    caching semantics, you can have up to 4 cache breakpoints — we only set
    one on the last cacheable block (which implicitly caches everything before
    it that's also tagged).
    """

    text: str
    cacheable: bool = False


def model_for(task: str) -> str:
    """Resolve a task name to a model id, falling back to a sensible default."""
    models = get_settings().llm.models
    if task in models:
        return models[task]
    return "claude-sonnet-4-6"


def _client() -> anthropic.Anthropic:
    api_key = get_env().anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY missing. Set it in .env or the environment before calling Claude."
        )
    return anthropic.Anthropic(api_key=api_key)


def _build_system(blocks: Iterable[SystemBlock]) -> list[dict[str, Any]] | None:
    """Build the structured `system` argument with one cache breakpoint.

    Returns None if no blocks were provided (Anthropic accepts an absent
    system arg).
    """
    items: list[dict[str, Any]] = []
    last_cacheable_idx: int | None = None
    for i, b in enumerate(blocks):
        if not b.text:
            continue
        items.append({"type": "text", "text": b.text})
        if b.cacheable:
            last_cacheable_idx = len(items) - 1
    if not items:
        return None
    if last_cacheable_idx is not None:
        items[last_cacheable_idx] = {
            **items[last_cacheable_idx],
            "cache_control": {"type": "ephemeral"},
        }
    return items


def complete(
    task: str,
    *,
    system: list[SystemBlock] | str | None = None,
    messages: list[dict[str, Any]],
    max_tokens: int | None = None,
    temperature: float = 0.7,
    retries: int = 6,
) -> LLMResponse:
    """Call Claude with the model bound to `task`.

    `system` accepts either a plain string (no caching) or a list of
    SystemBlock (one cache breakpoint on the last cacheable block).
    """
    client = _client()
    model = model_for(task)
    settings = get_settings()
    max_tokens = max_tokens or settings.llm.max_tokens_default

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if isinstance(system, str):
        kwargs["system"] = system
    elif system:
        structured = _build_system(system)
        if structured:
            kwargs["system"] = structured

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.messages.create(**kwargs)
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", None) == "text"
            )
            usage = resp.usage
            return LLMResponse(
                text=text,
                model=resp.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            )
        except (APIStatusError, APIConnectionError) as e:
            last_err = e
            status = getattr(e, "status_code", None)
            # Only retry on overload / rate-limit / network blip
            if status not in (None, 429, 500, 502, 503, 529) and not isinstance(
                e, APIConnectionError
            ):
                raise
            if attempt == retries - 1:
                break                      # last try failed — don't sleep, just re-raise
            # Exponential backoff (capped) + jitter so a sustained 529 overload
            # (seconds-to-minutes) is ridden out instead of failing after ~7s.
            # Honour a server Retry-After header when present (429s carry it).
            backoff = min(2 ** attempt, 30)
            retry_after = _retry_after_seconds(e)
            if retry_after is not None:
                backoff = max(backoff, retry_after)
            time.sleep(backoff + random.uniform(0, 0.5 * backoff))
    assert last_err is not None
    raise last_err


def _retry_after_seconds(exc: Exception) -> float | None:
    """Parse a Retry-After header (seconds) off an API error, if any."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    val = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None
