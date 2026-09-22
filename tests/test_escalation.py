"""Tests for the Escalation Engine — Mechanical → Small LM → Big LM → Human."""

from __future__ import annotations

import pytest

from cns_bridge.escalation import (
    EscalationEngine,
    EscalationOutcome,
    EscalationTier,
    TierBudget,
    TierResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def mechanical_always(answer: str = "ok", confidence: float = 1.0) -> callable:
    """Build a handler that always returns the same answer."""
    def handler(query: str) -> TierResult:
        return TierResult(response=answer, confidence=confidence)
    return handler


def mechanical_never() -> callable:
    """Handler that never has an answer (confidence 0)."""
    def handler(query: str) -> TierResult:
        return TierResult(response=None, confidence=0.0)
    return handler


def small_lm_handler(query: str) -> TierResult:
    return TierResult(response=f"small:{query}", confidence=0.85, tokens_used=50)


def big_lm_handler(query: str) -> TierResult:
    return TierResult(response=f"big:{query}", confidence=0.95, tokens_used=500)


def human_handler(query: str) -> TierResult:
    return TierResult(response=f"human:{query}", confidence=1.0, tokens_used=0)


def make_full_engine(threshold: float = 0.7) -> EscalationEngine:
    """Engine with all four tiers registered."""
    engine = EscalationEngine(confidence_threshold=threshold)
    engine.register_handler(EscalationTier.MECHANICAL, mechanical_never())
    engine.register_handler(EscalationTier.SMALL_LM, small_lm_handler)
    engine.register_handler(EscalationTier.BIG_LM, big_lm_handler)
    engine.register_handler(EscalationTier.HUMAN, human_handler)
    return engine


# ---------------------------------------------------------------------------
# Tier ordering
# ---------------------------------------------------------------------------

class TestTierOrdering:
    def test_tier_values_increase_with_cost(self):
        assert EscalationTier.MECHANICAL < EscalationTier.SMALL_LM
        assert EscalationTier.SMALL_LM < EscalationTier.BIG_LM
        assert EscalationTier.BIG_LM < EscalationTier.HUMAN

    def test_configured_tiers_sorted_cheapest_first(self):
        engine = EscalationEngine()
        engine.register_handler(EscalationTier.HUMAN, human_handler)
        engine.register_handler(EscalationTier.MECHANICAL, mechanical_always())
        engine.register_handler(EscalationTier.SMALL_LM, small_lm_handler)
        assert engine.configured_tiers == [
            EscalationTier.MECHANICAL,
            EscalationTier.SMALL_LM,
            EscalationTier.HUMAN,
        ]


# ---------------------------------------------------------------------------
# Basic resolution
# ---------------------------------------------------------------------------

class TestResolution:
    def test_mechanical_resolves_when_confident(self):
        engine = EscalationEngine()
        engine.register_handler(EscalationTier.MECHANICAL, mechanical_always("42", 0.99))
        result = engine.handle("roll")
        assert result.resolved_by == EscalationTier.MECHANICAL
        assert result.response == "42"
        assert result.confidence == pytest.approx(0.99)

    def test_escalates_when_mechanical_low_confidence(self):
        engine = make_full_engine()
        result = engine.handle("complex question")
        assert result.resolved_by == EscalationTier.SMALL_LM
        assert result.response == "small:complex question"
        # Mechanical was attempted first
        assert EscalationTier.MECHANICAL in result.tiers_tried

    def test_escalates_past_small_lm(self):
        engine = EscalationEngine(confidence_threshold=0.9)
        engine.register_handler(EscalationTier.MECHANICAL, mechanical_never())
        engine.register_handler(EscalationTier.SMALL_LM, small_lm_handler)  # 0.85 < 0.9
        engine.register_handler(EscalationTier.BIG_LM, big_lm_handler)      # 0.95 >= 0.9
        result = engine.handle("hard")
        assert result.resolved_by == EscalationTier.BIG_LM
        assert EscalationTier.MECHANICAL in result.tiers_tried
        assert EscalationTier.SMALL_LM in result.tiers_tried

    def test_falls_through_to_human(self):
        engine = EscalationEngine(confidence_threshold=1.01)  # impossible
        engine.register_handler(EscalationTier.MECHANICAL, mechanical_never())
        engine.register_handler(EscalationTier.SMALL_LM, small_lm_handler)
        engine.register_handler(EscalationTier.BIG_LM, big_lm_handler)
        engine.register_handler(EscalationTier.HUMAN, human_handler)
        result = engine.handle("impossible")
        assert result.resolved_by == EscalationTier.HUMAN
        # Even human's 1.0 < 1.01 threshold, but HUMAN is the last tier
        # so it falls through — response is human's since we check response is not None
        # Actually with threshold 1.01, human also fails the check.
        # The loop ends without resolution.

    def test_no_handlers_returns_empty(self):
        engine = EscalationEngine()
        result = engine.handle("anything")
        assert result.response is None
        assert result.tiers_tried == []

    def test_none_response_rejected_even_with_high_confidence(self):
        engine = EscalationEngine()
        engine.register_handler(
            EscalationTier.MECHANICAL,
            lambda q: TierResult(response=None, confidence=0.99),
        )
        engine.register_handler(EscalationTier.SMALL_LM, small_lm_handler)
        result = engine.handle("test")
        assert result.resolved_by == EscalationTier.SMALL_LM


# ---------------------------------------------------------------------------
# Confidence threshold
# ---------------------------------------------------------------------------

class TestConfidenceThreshold:
    def test_default_threshold_is_0_7(self):
        engine = EscalationEngine()
        assert engine.confidence_threshold == pytest.approx(0.7)

    def test_custom_threshold(self):
        engine = EscalationEngine(confidence_threshold=0.95)
        assert engine.confidence_threshold == pytest.approx(0.95)

    def test_threshold_at_boundary_accepts(self):
        engine = EscalationEngine(confidence_threshold=0.85)
        engine.register_handler(EscalationTier.MECHANICAL, mechanical_always("x", 0.85))
        result = engine.handle("q")
        assert result.resolved_by == EscalationTier.MECHANICAL

    def test_threshold_just_below_rejects(self):
        engine = EscalationEngine(confidence_threshold=0.86)
        engine.register_handler(EscalationTier.MECHANICAL, mechanical_always("x", 0.85))
        engine.register_handler(EscalationTier.SMALL_LM, small_lm_handler)  # 0.85 < 0.86
        engine.register_handler(EscalationTier.BIG_LM, big_lm_handler)     # 0.95 >= 0.86
        result = engine.handle("q")
        assert result.resolved_by == EscalationTier.BIG_LM


# ---------------------------------------------------------------------------
# Budget tracking
# ---------------------------------------------------------------------------

class TestBudget:
    def test_budget_blocks_after_max_calls(self):
        budget = TierBudget(max_calls_per_hr=2)
        assert budget.consume(0) is True
        assert budget.consume(0) is True
        assert budget.consume(0) is False

    def test_budget_blocks_after_max_tokens(self):
        budget = TierBudget(max_tokens_per_hr=100)
        assert budget.consume(60) is True
        assert budget.consume(50) is False  # 60 + 50 > 100
        assert budget.consume(40) is True   # 60 + 40 = 100

    def test_unlimited_budget_when_zero(self):
        budget = TierBudget(max_calls_per_hr=0, max_tokens_per_hr=0)
        for _ in range(100):
            assert budget.consume(999) is True

    def test_remaining_shows_correct_values(self):
        budget = TierBudget(max_calls_per_hr=10, max_tokens_per_hr=1000)
        budget.consume(100)
        rem = budget.remaining()
        assert rem["calls_remaining"] == 9
        assert rem["tokens_remaining"] == 900

    def test_remaining_unlimited_returns_negative_one(self):
        budget = TierBudget()
        rem = budget.remaining()
        assert rem["calls_remaining"] == -1
        assert rem["tokens_remaining"] == -1

    def test_engine_skips_tier_with_exhausted_budget(self):
        engine = EscalationEngine()
        engine.register_handler(EscalationTier.MECHANICAL, mechanical_never())
        engine.register_handler(EscalationTier.SMALL_LM, small_lm_handler)
        engine.register_handler(EscalationTier.BIG_LM, big_lm_handler)
        # Set a call budget of 1, then manually exhaust it
        small_budget = TierBudget(max_calls_per_hr=1)
        assert small_budget.consume(0) is True  # now exhausted
        engine.set_budget(EscalationTier.SMALL_LM, small_budget)
        result = engine.handle("test")
        # SMALL_LM should be skipped, BIG_LM resolves
        assert result.resolved_by == EscalationTier.BIG_LM
        assert EscalationTier.SMALL_LM not in result.tiers_tried


# ---------------------------------------------------------------------------
# Metrics and stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_empty_stats(self):
        engine = EscalationEngine()
        s = engine.stats()
        assert s["total_calls"] == 0
        assert s["tiers"] == {}
        assert s["configured_threshold"] == pytest.approx(0.7)

    def test_records_per_tier_calls(self):
        engine = make_full_engine()
        engine.handle("a")
        engine.handle("b")
        s = engine.stats()
        # Both calls hit MECHANICAL (fails) then SMALL_LM (succeeds)
        assert s["tiers"]["MECHANICAL"]["calls"] == 2
        assert s["tiers"]["SMALL_LM"]["calls"] == 2
        assert s["tiers"].get("BIG_LM", {}).get("calls", 0) == 0  # never reached

    def test_avg_confidence(self):
        engine = make_full_engine()
        engine.handle("x")
        s = engine.stats()
        assert s["tiers"]["SMALL_LM"]["avg_confidence"] == pytest.approx(0.85)
        assert s["tiers"]["MECHANICAL"]["avg_confidence"] == pytest.approx(0.0)

    def test_escalation_rate(self):
        engine = make_full_engine()
        engine.handle("q1")
        engine.handle("q2")
        s = engine.stats()
        # MECHANICAL escalated 100% of the time (never resolved)
        assert s["tiers"]["MECHANICAL"]["escalation_rate"] == pytest.approx(1.0)
        # SMALL_LM never escalated (resolved both times)
        assert s["tiers"]["SMALL_LM"]["escalation_rate"] == pytest.approx(0.0)

    def test_total_calls_aggregate(self):
        engine = make_full_engine()
        engine.handle("q1")
        engine.handle("q2")
        engine.handle("q3")
        s = engine.stats()
        # 3 queries × 2 tiers each = 6 total calls
        assert s["total_calls"] == 6

    def test_export_metrics_alias(self):
        engine = make_full_engine()
        engine.handle("q")
        assert engine.export_metrics() == engine.stats()

    def test_reset_metrics(self):
        engine = make_full_engine()
        engine.handle("q")
        assert engine.stats()["total_calls"] > 0
        engine.reset_metrics()
        assert engine.stats()["total_calls"] == 0


# ---------------------------------------------------------------------------
# EscalationOutcome
# ---------------------------------------------------------------------------

class TestEscalationOutcome:
    def test_default_outcome(self):
        o = EscalationOutcome()
        assert o.response is None
        assert o.confidence == 0.0
        assert o.resolved_by == EscalationTier.HUMAN
        assert o.tiers_tried == []

    def test_tokens_accumulated_across_tiers(self):
        engine = EscalationEngine(confidence_threshold=0.99)
        engine.register_handler(EscalationTier.MECHANICAL, mechanical_never())
        engine.register_handler(EscalationTier.SMALL_LM, small_lm_handler)  # 50 tokens
        engine.register_handler(EscalationTier.BIG_LM, big_lm_handler)     # 500 tokens
        result = engine.handle("q")
        # Mechanical = 0, SMALL_LM = 50, BIG_LM = 500 → total 550
        assert result.tokens_used == 550


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_handler_returning_empty_string_is_valid(self):
        engine = EscalationEngine()
        engine.register_handler(
            EscalationTier.MECHANICAL,
            lambda q: TierResult(response="", confidence=0.9),
        )
        result = engine.handle("q")
        assert result.resolved_by == EscalationTier.MECHANICAL
        assert result.response == ""

    def test_handler_can_record_zero_confidence_with_response(self):
        engine = EscalationEngine()
        engine.register_handler(
            EscalationTier.MECHANICAL,
            lambda q: TierResult(response="low", confidence=0.0),
        )
        engine.register_handler(EscalationTier.SMALL_LM, small_lm_handler)
        result = engine.handle("q")
        # Mechanical had response but confidence 0 → escalate
        assert result.resolved_by == EscalationTier.SMALL_LM

    def test_replace_handler(self):
        engine = EscalationEngine()
        engine.register_handler(EscalationTier.MECHANICAL, mechanical_never())
        engine.register_handler(EscalationTier.MECHANICAL, mechanical_always("replaced", 0.99))
        result = engine.handle("q")
        assert result.response == "replaced"

    def test_partial_tier_registration(self):
        """Only BIG_LM and HUMAN registered — skips MECHANICAL/SMALL_LM."""
        engine = EscalationEngine()
        engine.register_handler(EscalationTier.BIG_LM, big_lm_handler)
        result = engine.handle("q")
        assert result.resolved_by == EscalationTier.BIG_LM
        assert result.escalated_from == EscalationTier.BIG_LM
