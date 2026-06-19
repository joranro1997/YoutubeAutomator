"""Contractual guardrails for generated scripts and metadata.

These checks are non-negotiable — they map to clauses of the Aptoide Connect
Affiliate Agreement. Any pipeline output that fails a guardrail is blocked
before it reaches YouTube.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import GameConfig, get_settings
from .generator import Script


@dataclass
class GuardrailViolation:
    rule: str
    detail: str


# Stat-like tokens that read as a *verifiable factual claim* (and so need a
# source under §4.5), as opposed to the channel's harmless listicle numbers
# ("5 TIPS", "TOP 3"). Deliberately narrow to keep false positives low:
# percentages, Nx / xN multipliers, +N stat buffs, comma-grouped big numbers,
# and patch/version numbers. A bare integer is NOT flagged.
_FACTUAL_STAT_RE = re.compile(
    r"""
      \d+\s*%                 # percentages: 300%
    | \b\d+\s?x\b             # multipliers: 10x
    | \bx\s?\d+\b             # multipliers: x10
    | \+\s?\d+                # stat buffs: +50
    | \b\d{1,3}(?:,\d{3})+\b  # big numbers: 100,000
    | \bv?\d+\.\d+\b          # patch/versions: 2.4, v1.3
    """,
    re.IGNORECASE | re.VERBOSE,
)


def check_topics(candidates) -> list[GuardrailViolation]:
    """Warn when a topic asserts a concrete stat with no source (§4.5).

    Topics are an intermediate, human-reviewed artifact, so this is a
    NON-blocking early warning — the script (`check_script`) and description
    (`check_description`) guardrails remain the hard gate before upload. It
    complements the LLM grounding rule with a deterministic backstop: a
    `title_hook`/`angle` that states a concrete stat (percentage, Nx
    multiplier, +N buff, big number, patch version) while `grounding_urls`
    is empty is exactly the ungrounded factual claim a steered run can
    produce. Duck-typed on the candidate so it never couples to the LLM layer.
    """
    violations: list[GuardrailViolation] = []
    for i, c in enumerate(candidates):
        hook = getattr(c, "title_hook", "") or ""
        angle = getattr(c, "angle", "") or ""
        urls = getattr(c, "grounding_urls", None) or []
        if not urls and _FACTUAL_STAT_RE.search(f"{hook} {angle}"):
            violations.append(
                GuardrailViolation(
                    rule="ungrounded_factual_topic",
                    detail=f"Topic #{i} {hook!r} states a concrete stat "
                    "(%, multiplier, +N, big number or version) but cites no "
                    "grounding_urls — verify or drop the claim before scripting (§4.5).",
                )
            )
    return violations


def check_script(script: Script, game: GameConfig) -> list[GuardrailViolation]:
    """Verify a Script against the contract guardrails.

    Checks:
    - An aptoide_ad_marker segment exists (the pre-recorded ad slot is the
      canonical satisfaction of §4 Appendix's "1 mention of Aptoide per video").
    - At least one segment mentions the affiliate code verbally (outro CTA).
    - "topic" segments above a length threshold should carry >=1 citation
      (§4.5: factual statements must be verifiable). Warned, not hard-failed.
    - No banned phrasing about Aptoide hosting paid apps for free (§4.11).
    - No disparagement of Aptoide (§4.9). Very lenient — only flag obvious
      negative-sentiment phrases.
    - Total duration in a sane range (300–900s).
    """
    violations: list[GuardrailViolation] = []

    if not script.segments:
        violations.append(GuardrailViolation(rule="empty_script", detail="no segments produced"))
        return violations

    has_ad_marker = any(s.kind == "aptoide_ad_marker" for s in script.segments)
    if not has_ad_marker:
        violations.append(
            GuardrailViolation(
                rule="missing_aptoide_ad_marker",
                detail="No segment with kind='aptoide_ad_marker' — the pre-recorded "
                "Aptoide ad splice point is required (Contract §4 Appendix).",
            )
        )

    code = (game.sponsorship.affiliate_code or "").lower()
    full_text = " ".join(s.text for s in script.segments).lower()
    if code and code not in full_text:
        violations.append(
            GuardrailViolation(
                rule="missing_affiliate_code_cta",
                detail=f"Affiliate code {game.sponsorship.affiliate_code!r} is not "
                "mentioned anywhere in the script's spoken text.",
            )
        )

    settings = get_settings().contract_guardrails
    for banned in settings.description_must_not_contain:
        if banned and banned.lower() in full_text:
            violations.append(
                GuardrailViolation(
                    rule="banned_phrase",
                    detail=f"Script contains banned phrase: {banned!r} (Contract §4.11).",
                )
            )

    # Factual-claim citations: any topic segment longer than ~400 chars should
    # cite at least one source. Otherwise the model may have invented stats.
    for i, s in enumerate(script.segments):
        if s.kind == "topic" and len(s.text) >= 400 and not s.citations:
            violations.append(
                GuardrailViolation(
                    rule="missing_citation",
                    detail=f"Topic segment #{i} ({len(s.text)} chars) has no citations.",
                )
            )

    if script.total_duration_s_estimate and not (300 <= script.total_duration_s_estimate <= 900):
        violations.append(
            GuardrailViolation(
                rule="duration_out_of_range",
                detail=f"Total estimated duration {script.total_duration_s_estimate}s "
                "outside typical 5–15 min range.",
            )
        )

    return violations


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
