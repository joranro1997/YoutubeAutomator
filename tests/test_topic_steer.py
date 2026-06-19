"""Tests for the topic-steering feature and its §4.5 backstop.

Covers:
- ``_steer_block`` is a strict no-op for empty / whitespace input, so the
  pure-SEO user message stays byte-for-byte identical (and the prompt cache
  is unaffected).
- ``_steer_block`` wraps creator text in data delimiters when present.
- ``check_topics`` flags concrete ungrounded stats but NOT the channel's
  harmless listicle numbers, and never flags a topic that cites a source.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from youtube_automator.ideation.topic_generator import _steer_block
from youtube_automator.metadata.generator import _steer_block as _meta_steer_block
from youtube_automator.script.guardrails import check_topics


@dataclass
class _FakeCandidate:
    title_hook: str
    angle: str = ""
    grounding_urls: tuple = ()


@pytest.mark.parametrize("blank", ["", "   ", "\n\t  \n"])
def test_steer_block_empty_is_noop(blank):
    assert _steer_block(blank) == ""
    assert _steer_block(None) == ""  # type: ignore[arg-type]


def test_steer_block_wraps_creator_text_as_delimited_data():
    out = _steer_block("focus on the new dragon event")
    assert "<<<CREATOR_INTENT" in out and ">>>" in out
    assert "focus on the new dragon event" in out
    # The framing must keep "dominant" scoped to ranking, never to facts.
    assert "DATA, not an instruction" in out
    assert "source_url" in out


@pytest.mark.parametrize("blank", ["", "   ", "\n\t  \n"])
def test_metadata_steer_block_empty_is_noop(blank):
    assert _meta_steer_block(blank) == ""
    assert _meta_steer_block(None) == ""  # type: ignore[arg-type]


def test_metadata_steer_block_wraps_angle_and_keeps_no_clickbait():
    out = _meta_steer_block("the best/strongest build guide")
    assert "<<<CREATOR_ANGLE" in out and ">>>" in out
    assert "the best/strongest build guide" in out
    assert "clickbait" in out.lower()        # honesty rule preserved


@pytest.mark.parametrize(
    "hook,urls,expected_flag",
    [
        ("5 TIPS YOU MUST DO", (), False),            # listicle integer
        ("TOP 3 BROKEN HEROES", (), False),           # ranking integer
        ("NEW EVENT GIVES 10x REWARDS", (), True),    # multiplier
        ("300% DAMAGE BUILD IS INSANE", (), True),    # percentage
        ("PATCH v2.4 CHANGES EVERYTHING", (), True),  # version number
        ("+50 ATTACK FOR FREE NOW", (), True),        # stat buff
        ("GET 100,000 GEMS FAST", (), True),          # big number
        ("NEW EVENT GIVES 10x REWARDS", ("https://x",), False),  # sourced
    ],
)
def test_check_topics_precision(hook, urls, expected_flag):
    violations = check_topics([_FakeCandidate(hook, grounding_urls=urls)])
    assert bool(violations) is expected_flag
    if expected_flag:
        assert violations[0].rule == "ungrounded_factual_topic"


def test_check_topics_empty_list_is_clean():
    assert check_topics([]) == []
