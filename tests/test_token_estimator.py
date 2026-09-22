"""Tests for the token estimator module."""

import pytest

from cns_bridge.token_estimator import (
    HealthLevel,
    TokenEstimate,
    context_health,
    context_pressure,
    estimate_messages,
    estimate_tokens,
    estimate_tokens_detailed,
    format_health,
    should_trigger_creative_break,
    tokens_remaining,
)


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string(self) -> None:
        assert estimate_tokens("") == 0

    def test_simple_text(self) -> None:
        result = estimate_tokens("hello world")
        assert 2 <= result <= 4  # should be in a reasonable range

    def test_longer_text(self) -> None:
        text = "The quick brown fox jumps over the lazy dog. " * 10
        result = estimate_tokens(text)
        assert result > 20  # 90 words should produce meaningful token count

    def test_code_text(self) -> None:
        code = "def hello():\n    return 'world'\n"
        result = estimate_tokens(code)
        assert result >= 5

    def test_very_long_text(self) -> None:
        text = "word " * 10_000
        result = estimate_tokens(text)
        assert result > 1_000

    def test_unicode_text(self) -> None:
        result = estimate_tokens("héllo wörld — em dash — arrow →")
        assert result >= 3


class TestEstimateTokensDetailed:
    def test_returns_dataclass(self) -> None:
        result = estimate_tokens_detailed("hello world")
        assert isinstance(result, TokenEstimate)
        assert result.chars == 11
        assert result.words == 2
        assert result.method == "blended"

    def test_empty_returns_zero(self) -> None:
        result = estimate_tokens_detailed("")
        assert result.tokens == 0
        assert result.chars == 0

    def test_approx_tokens_alias(self) -> None:
        result = estimate_tokens_detailed("test text here")
        assert result.approx_tokens == result.tokens


# ---------------------------------------------------------------------------
# estimate_messages
# ---------------------------------------------------------------------------

class TestEstimateMessages:
    def test_empty_list(self) -> None:
        assert estimate_messages([]) == 0

    def test_single_message(self) -> None:
        msgs = [{"content": "hello world"}]
        result = estimate_messages(msgs)
        assert result > 0
        # Should include 4-token overhead
        assert result >= 4

    def test_multiple_messages(self) -> None:
        msgs = [
            {"content": "first message"},
            {"content": "second longer message with more words"},
            {"content": "third"},
        ]
        result = estimate_messages(msgs)
        # 3 messages × 4 overhead = 12, plus content tokens
        assert result >= 12

    def test_missing_content_key(self) -> None:
        msgs = [{"role": "user"}, {"content": "hi"}]
        result = estimate_messages(msgs)
        # Should not crash, should still count the "hi" message
        assert result >= 4


# ---------------------------------------------------------------------------
# context_health
# ---------------------------------------------------------------------------

class TestContextHealth:
    def test_green_under_60(self) -> None:
        assert context_health(40_000, 100_000) == "green"
        assert context_health(0, 100_000) == "green"
        assert context_health(59_999, 100_000) == "green"

    def test_yellow_60_to_80(self) -> None:
        assert context_health(60_000, 100_000) == "yellow"
        assert context_health(70_000, 100_000) == "yellow"
        assert context_health(79_999, 100_000) == "yellow"

    def test_red_above_80(self) -> None:
        assert context_health(80_000, 100_000) == "red"
        assert context_health(95_000, 100_000) == "red"
        assert context_health(100_000, 100_000) == "red"

    def test_over_100_percent(self) -> None:
        # Over the limit is still red, not an error
        assert context_health(120_000, 100_000) == "red"

    def test_zero_limit_raises(self) -> None:
        with pytest.raises(ValueError):
            context_health(100, 0)

    def test_negative_used_raises(self) -> None:
        with pytest.raises(ValueError):
            context_health(-1, 1000)


# ---------------------------------------------------------------------------
# context_pressure, tokens_remaining
# ---------------------------------------------------------------------------

class TestPressureAndRemaining:
    def test_pressure_half(self) -> None:
        assert context_pressure(50_000, 100_000) == 0.5

    def test_pressure_clamps_at_1(self) -> None:
        assert context_pressure(150_000, 100_000) == 1.0

    def test_remaining_basic(self) -> None:
        assert tokens_remaining(30_000, 100_000) == 70_000

    def test_remaining_zero(self) -> None:
        assert tokens_remaining(100_000, 100_000) == 0

    def test_remaining_clamps_negative(self) -> None:
        assert tokens_remaining(120_000, 100_000) == 0


# ---------------------------------------------------------------------------
# should_trigger_creative_break
# ---------------------------------------------------------------------------

class TestShouldTrigger:
    def test_below_threshold(self) -> None:
        assert not should_trigger_creative_break(50_000, 100_000)

    def test_at_threshold(self) -> None:
        assert should_trigger_creative_break(80_000, 100_000)

    def test_above_threshold(self) -> None:
        assert should_trigger_creative_break(95_000, 100_000)

    def test_custom_threshold(self) -> None:
        assert should_trigger_creative_break(50_000, 100_000, threshold=0.50)
        assert not should_trigger_creative_break(49_000, 100_000, threshold=0.50)


# ---------------------------------------------------------------------------
# format_health
# ---------------------------------------------------------------------------

class TestFormatHealth:
    def test_contains_level(self) -> None:
        result = format_health(50_000, 100_000)
        assert "GREEN" in result

    def test_contains_numbers(self) -> None:
        result = format_health(50_000, 100_000)
        assert "50,000" in result
        assert "100,000" in result

    def test_contains_percentage(self) -> None:
        result = format_health(50_000, 100_000)
        assert "50%" in result

    def test_contains_remaining(self) -> None:
        result = format_health(30_000, 100_000)
        assert "70,000" in result

class TestValidationConsistency:
    """Every public health function must reject the same bad inputs.

    Previously context_health raised on negative ``used`` while
    context_pressure silently returned a negative fraction and
    tokens_remaining returned a count larger than the window — a caller
    switching between them would see contradictory behavior.
    """

    @pytest.mark.parametrize("bad_used", [-1, -10_000])
    def test_negative_used_raises_everywhere(self, bad_used: int) -> None:
        for call in (
            lambda: context_health(bad_used, 100_000),
            lambda: context_pressure(bad_used, 100_000),
            lambda: tokens_remaining(bad_used, 100_000),
            lambda: should_trigger_creative_break(bad_used, 100_000),
            lambda: format_health(bad_used, 100_000),
        ):
            with pytest.raises(ValueError, match="used must be non-negative"):
                call()

    @pytest.mark.parametrize("bad_limit", [0, -100])
    def test_bad_limit_raises_everywhere(self, bad_limit: int) -> None:
        for call in (
            lambda: context_health(10, bad_limit),
            lambda: context_pressure(10, bad_limit),
            lambda: tokens_remaining(10, bad_limit),
            lambda: should_trigger_creative_break(10, bad_limit),
            lambda: format_health(10, bad_limit),
        ):
            with pytest.raises(ValueError, match="limit must be positive"):
                call()
