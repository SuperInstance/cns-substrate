"""Escalation Engine — Mechanical → Small LM → Big LLM → Human.

Implements the tiered escalation pattern from the AI Society D&D architecture:
cheapest tier handles first, escalates only when confidence is too low or
budget is exhausted.  Designed to map directly onto the Lucineer model
routing strategy (deterministic bots → DeepSeek Flash → GLM-5.2 → Human).

Example
-------
    from cns_bridge.escalation import (
        EscalationEngine, EscalationTier, TierResult,
    )

    def mechanical_handler(query: str) -> TierResult:
        if query.startswith("/roll"):
            return TierResult(response="42", confidence=0.99)
        return TierResult(response=None, confidence=0.0)

    engine = EscalationEngine()
    engine.register_handler(EscalationTier.MECHANICAL, mechanical_handler)
    result = engine.handle("hello")
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from threading import Lock
from typing import Any, Callable


class EscalationTier(IntEnum):
    """Processing tiers ordered from cheapest to most expensive."""

    MECHANICAL = 0
    SMALL_LM = 1
    BIG_LM = 2
    HUMAN = 3


@dataclass
class TierResult:
    """Value object returned by a tier handler.

    Attributes:
        response:  The produced output (or ``None`` if the tier could not answer).
        confidence: Self-reported score in [0.0, 1.0].
        tokens_used: Approximate tokens consumed (0 for mechanical tier).
        metadata:   Arbitrary handler-specific payload.
    """

    response: Any = None
    confidence: float = 0.0
    tokens_used: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


TierHandler = Callable[[str], TierResult]


@dataclass
class TierBudget:
    """Per-tier rolling-window budget.

    Args:
        max_calls_per_hr: Maximum handler invocations per hour (0 = unlimited).
        max_tokens_per_hr: Maximum tokens per hour (0 = unlimited).
    """

    max_calls_per_hr: int = 0
    max_tokens_per_hr: int = 0

    # Internal state -------------------------------------------------
    _call_times: list[float] = field(default_factory=list, repr=False)
    _token_entries: list[tuple[float, int]] = field(default_factory=list, repr=False)

    def _prune(self) -> None:
        """Drop entries older than one hour."""
        cutoff = time.time() - 3600.0
        self._call_times = [t for t in self._call_times if t > cutoff]
        self._token_entries = [(t, a) for t, a in self._token_entries if t > cutoff]

    def can_afford(self, tokens: int = 0) -> bool:
        """Check whether a call consuming *tokens* fits within budget."""
        self._prune()
        if self.max_calls_per_hr and len(self._call_times) >= self.max_calls_per_hr:
            return False
        if self.max_tokens_per_hr and tokens > 0:
            current = sum(a for _, a in self._token_entries)
            if current + tokens > self.max_tokens_per_hr:
                return False
        return True

    def consume(self, tokens: int = 0) -> bool:
        """Record a call; return True if within budget, False if exhausted."""
        if not self.can_afford(tokens):
            return False
        now = time.time()
        self._call_times.append(now)
        if tokens > 0:
            self._token_entries.append((now, tokens))
        return True

    def remaining(self) -> dict[str, int]:
        """Return remaining calls and tokens in the current window.

        A value of ``-1`` means "unlimited" (no limit configured).
        """
        self._prune()
        calls_used = len(self._call_times)
        tokens_used = sum(a for _, a in self._token_entries)
        return {
            "calls_remaining": (
                self.max_calls_per_hr - calls_used
                if self.max_calls_per_hr
                else -1
            ),
            "tokens_remaining": (
                self.max_tokens_per_hr - tokens_used
                if self.max_tokens_per_hr
                else -1
            ),
        }


@dataclass
class _TierMetrics:
    """Internal per-tier metrics counters."""

    total_calls: int = 0
    total_tokens: int = 0
    confidence_sum: float = 0.0
    confidence_scores: list[float] = field(default_factory=list)
    escalations_out: int = 0


@dataclass
class EscalationOutcome:
    """Full result of an :meth:`EscalationEngine.handle` call.

    Attributes:
        response:     The final response from the resolving tier.
        confidence:   Confidence of the resolving tier.
        resolved_by:  Which tier produced the final answer.
        escalated_from: Which tier first attempted (always MECHANICAL unless
                        the lowest configured tier is higher).
        tiers_tried:  Ordered list of tiers that were attempted.
        tokens_used:  Total tokens across all tiers for this call.
    """

    response: Any = None
    confidence: float = 0.0
    resolved_by: EscalationTier = EscalationTier.HUMAN
    escalated_from: EscalationTier = EscalationTier.MECHANICAL
    tiers_tried: list[EscalationTier] = field(default_factory=list)
    tokens_used: int = 0


class EscalationEngine:
    """Tiered request handler with budget-aware escalation.

    Requests flow through tiers from cheapest to most expensive.  A tier's
    output is accepted when its confidence ≥ ``confidence_threshold`` **and**
    it did not return a ``None`` response.  A tier is skipped when its budget
    is exhausted.

    Args:
        confidence_threshold: Minimum confidence to accept a tier's answer.
        budgets: Optional dict mapping tiers to custom :class:`TierBudget`.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.7,
        budgets: dict[EscalationTier, TierBudget] | None = None,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self._handlers: dict[EscalationTier, TierHandler] = {}
        self._budgets: dict[EscalationTier, TierBudget] = budgets or {}
        self._metrics: dict[EscalationTier, _TierMetrics] = defaultdict(
            _TierMetrics
        )
        self._lock = Lock()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def register_handler(
        self, tier: EscalationTier, handler: TierHandler
    ) -> None:
        """Register or replace the handler for *tier*."""
        self._handlers[tier] = handler

    def set_budget(self, tier: EscalationTier, budget: TierBudget) -> None:
        """Attach or replace the budget for *tier*."""
        self._budgets[tier] = budget

    @property
    def configured_tiers(self) -> list[EscalationTier]:
        """Tiers that have handlers, sorted cheapest-first."""
        return sorted(self._handlers.keys())

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def handle(self, query: str) -> EscalationOutcome:
        """Process *query*, escalating through tiers as needed.

        Returns an :class:`EscalationOutcome` describing the resolution.
        If no tier can answer, ``resolved_by`` is :attr:`EscalationTier.HUMAN`
        with a ``None`` response.
        """
        tiers = self.configured_tiers
        if not tiers:
            return EscalationOutcome()

        outcome = EscalationOutcome(escalated_from=tiers[0])
        total_tokens = 0

        for i, tier in enumerate(tiers):
            # Budget check
            budget = self._budgets.get(tier)
            est_tokens = 0 if tier is EscalationTier.MECHANICAL else 100
            if budget is not None and not budget.can_afford(est_tokens):
                continue

            handler = self._handlers.get(tier)
            if handler is None:
                continue

            # Record attempt
            outcome.tiers_tried.append(tier)

            # Budget consumption
            if budget is not None:
                budget.consume(est_tokens)

            with self._lock:
                self._metrics[tier].total_calls += 1

            # Invoke handler
            result = handler(query)

            total_tokens += result.tokens_used

            # Accept if confidence high enough and response non-None
            accepted = (
                result.confidence >= self.confidence_threshold
                and result.response is not None
            )

            # Record metrics
            with self._lock:
                self._metrics[tier].total_tokens += result.tokens_used
                self._metrics[tier].confidence_sum += result.confidence
                self._metrics[tier].confidence_scores.append(
                    result.confidence
                )
                if not accepted:
                    self._metrics[tier].escalations_out += 1

            if accepted:
                outcome.response = result.response
                outcome.confidence = result.confidence
                outcome.resolved_by = tier
                outcome.tokens_used = total_tokens
                return outcome

        # Nobody could answer
        outcome.resolved_by = EscalationTier.HUMAN
        outcome.tokens_used = total_tokens
        return outcome

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return per-tier metrics and aggregate escalation stats.

        Schema::

            {
                "tiers": {
                    "MECHANICAL": {
                        "calls": 100,
                        "tokens": 0,
                        "avg_confidence": 0.95,
                        "escalation_rate": 0.05,
                    },
                    ...
                },
                "total_calls": 110,
                "overall_escalation_rate": 0.09,
                "configured_threshold": 0.7,
            }
        """
        result: dict[str, Any] = {"tiers": {}, "configured_threshold": self.confidence_threshold}
        total_calls = 0
        total_escalations = 0

        for tier in EscalationTier:
            m = self._metrics.get(tier)
            if m is None or m.total_calls == 0:
                continue
            avg_conf = m.confidence_sum / m.total_calls if m.total_calls else 0.0
            esc_rate = m.escalations_out / m.total_calls if m.total_calls else 0.0
            result["tiers"][tier.name] = {
                "calls": m.total_calls,
                "tokens": m.total_tokens,
                "avg_confidence": round(avg_conf, 4),
                "escalation_rate": round(esc_rate, 4),
            }
            total_calls += m.total_calls
            total_escalations += m.escalations_out

        result["total_calls"] = total_calls
        result["overall_escalation_rate"] = (
            round(total_escalations / total_calls, 4) if total_calls else 0.0
        )
        return result

    def export_metrics(self) -> dict[str, Any]:
        """Alias for :meth:`stats`, semantically named for dashboards."""
        return self.stats()

    def reset_metrics(self) -> None:
        """Clear all accumulated metrics (handlers and budgets untouched)."""
        with self._lock:
            self._metrics.clear()
