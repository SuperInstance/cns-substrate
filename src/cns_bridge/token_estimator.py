"""Token estimation utilities for CNS Bridge agents.

Provides lightweight, dependency-free token estimation and context-health
checking.  Every agent in the fleet can call these functions to decide
whether it is about to run out of context window.

The estimation uses a simple heuristic: roughly 1 token per 4 characters
for English text, with a floor of 1 token per whitespace-delimited word.
This is intentionally imprecise — the goal is a fast triage signal, not
a billing-grade tokenizer.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4.0  # average for English prose / code
_WORDS_PER_TOKEN = 0.75  # average words per token (some tokens span words)


# ---------------------------------------------------------------------------
# Health levels
# ---------------------------------------------------------------------------

class HealthLevel(str, Enum):
    """Traffic-light context-health indicator."""

    GREEN = "green"    # plenty of room
    YELLOW = "yellow"  # getting crowded — start thinking about wrapping up
    RED = "red"        # critical — trigger creative break immediately


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenEstimate:
    """Detailed breakdown of a token estimate."""

    tokens: int
    chars: int
    words: int
    method: str  # "char" | "word" | "blended"

    @property
    def approx_tokens(self) -> int:
        """Alias for ``tokens`` — semantically clearer at call sites."""
        return self.tokens


# ---------------------------------------------------------------------------
# Core estimation functions
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Return a fast, approximate token count for *text*.

    Uses a blended heuristic: the average of a character-based estimate
    (chars / 4) and a word-based estimate (words / 0.75).  The blend
    cancels out the worst biases of either method alone.

    Examples
    --------
    >>> estimate_tokens("hello world")
    3
    >>> estimate_tokens("")
    0
    """
    if not text:
        return 0
    return _estimate_detailed(text).tokens


def estimate_tokens_detailed(text: str) -> TokenEstimate:
    """Like :func:`estimate_tokens` but returns a full :class:`TokenEstimate`."""
    if not text:
        return TokenEstimate(tokens=0, chars=0, words=0, method="blended")
    return _estimate_detailed(text)


def _estimate_detailed(text: str) -> TokenEstimate:
    chars = len(text)
    words = len(text.split())
    char_estimate = chars / _CHARS_PER_TOKEN
    word_estimate = words / _WORDS_PER_TOKEN
    blended = int(math.ceil((char_estimate + word_estimate) / 2))
    return TokenEstimate(
        tokens=max(blended, 1),
        chars=chars,
        words=words,
        method="blended",
    )


def estimate_messages(messages: Iterable[dict]) -> int:
    """Estimate total tokens across an iterable of message dicts.

    Each message should have at least a ``"content"`` key.  A per-message
    overhead of 4 tokens is added to account for role/type wrappers.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        total += estimate_tokens(content) if isinstance(content, str) else estimate_tokens(str(content))
        total += 4  # structural overhead per message
    return total


# ---------------------------------------------------------------------------
# Context health
# ---------------------------------------------------------------------------

def context_health(used: int, limit: int) -> str:
    """Return a traffic-light string indicating context-window pressure.

    Parameters
    ----------
    used : int
        Estimated tokens already consumed.
    limit : int
        Total context-window size in tokens.

    Returns
    -------
    str
        One of ``"green"``, ``"yellow"``, ``"red"``.

    The thresholds are:

    * **green** — under 60 % used.
    * **yellow** — 60–80 % used.
    * **red** — above 80 % used.

    Examples
    --------
    >>> context_health(40_000, 100_000)
    'green'
    >>> context_health(65_000, 100_000)
    'yellow'
    >>> context_health(85_000, 100_000)
    'red'
    """
    return _context_health_level(used, limit).value


def _context_health_level(used: int, limit: int) -> HealthLevel:
    _validate_usage(used, limit)
    ratio = used / limit
    if ratio >= 0.80:
        return HealthLevel.RED
    if ratio >= 0.60:
        return HealthLevel.YELLOW
    return HealthLevel.GREEN


def _validate_usage(used: int, limit: int) -> None:
    """Reject nonsensical context-window inputs.

    Every public health function must agree on what is invalid, or a caller
    that switches between them (e.g. ``context_health`` for a label,
    ``context_pressure`` for a fraction) will see one raise and the other
    return garbage — a negative fraction, or a "remaining" count larger
    than the window itself.

    Raises
    ------
    ValueError
        If *limit* is not positive or *used* is negative.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if used < 0:
        raise ValueError("used must be non-negative")


def context_pressure(used: int, limit: int) -> float:
    """Return the context-window utilisation as a fraction (0.0–1.0).

    Raises ValueError if *limit* is not positive or *used* is negative.
    """
    _validate_usage(used, limit)
    return min(used / limit, 1.0)


def tokens_remaining(used: int, limit: int) -> int:
    """Return how many tokens are left before the context window is full.

    Raises ValueError if *limit* is not positive or *used* is negative.
    """
    _validate_usage(used, limit)
    return max(limit - used, 0)


def should_trigger_creative_break(
    used: int,
    limit: int,
    *,
    threshold: float = 0.80,
) -> bool:
    """Return True if context pressure has crossed the creative-break threshold."""
    return context_pressure(used, limit) >= threshold


# ---------------------------------------------------------------------------
# String formatting helpers
# ---------------------------------------------------------------------------

def format_health(used: int, limit: int) -> str:
    """Human-readable one-liner for logging."""
    health = context_health(used, limit)
    remaining = tokens_remaining(used, limit)
    pct = context_pressure(used, limit) * 100
    return (
        f"[{health.upper()}] {used:,}/{limit:,} tokens "
        f"({pct:.0f}%) — {remaining:,} remaining"
    )
