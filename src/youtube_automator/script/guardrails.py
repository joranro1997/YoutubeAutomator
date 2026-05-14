"""Contractual guardrails for generated scripts and metadata.

These checks are non-negotiable — they map to clauses of the Aptoide Connect
Affiliate Agreement. Any pipeline output that fails a guardrail is blocked
before it reaches YouTube.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import GameConfig, get_settings
from .generator import Script


@dataclass
class GuardrailViolation:
    rule: str
    detail: str


def check_script(script: Script, game: GameConfig) -> list[GuardrailViolation]:
    """Verify a Script against the contract guardrails.

    Checks:
    - At least N spoken mentions of "Aptoide" (§4 Appendix).
    - At least one segment carries the affiliate-code CTA.
    - Every "topic" segment with a factual claim must include >=1 citation (§4.5).
    - No banned phrasing about Aptoide hosting paid apps for free (§4.11).
    - An aptoide_ad_marker segment exists (splice point for the pre-recorded ad).
    """
    raise NotImplementedError("Stub — implement script guardrail checks")


def check_description(text: str, game: GameConfig) -> list[GuardrailViolation]:
    """Verify a YouTube description against guardrails."""
    settings = get_settings().contract_guardrails
    violations: list[GuardrailViolation] = []

    for required in settings.description_must_contain:
        token = required.replace("{affiliate_code}", game.sponsorship.affiliate_code)
        if token and token not in text:
            violations.append(
                GuardrailViolation(rule="description_must_contain", detail=f"missing: {token!r}")
            )

    for banned in settings.description_must_not_contain:
        if banned and banned.lower() in text.lower():
            violations.append(
                GuardrailViolation(rule="description_must_not_contain", detail=f"banned: {banned!r}")
            )

    return violations
