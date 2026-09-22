"""USCP protocol definitions: intents, priorities, and escalation rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Intent(str, Enum):
    """Kinds of message an agent can place on the CNS bus."""

    SENSE = "sense"
    COMMAND = "command"
    QUERY = "query"
    RESPONSE = "response"
    ALERT = "alert"
    HEARTBEAT = "heartbeat"
    REGISTER = "register"
    ESCALATION = "escalation"


class Priority(str, Enum):
    """Urgency levels for CNS packets.

    Order is intentional: LOW < NORMAL < HIGH < CRITICAL.
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _PRIORITY_RANK[self]


_PRIORITY_RANK = {
    Priority.LOW: 0,
    Priority.NORMAL: 1,
    Priority.HIGH: 2,
    Priority.CRITICAL: 3,
}


def higher_priority(a: Priority, b: Priority) -> Priority:
    """Return the more urgent of two priorities."""
    return a if a.rank >= b.rank else b


@dataclass(frozen=True)
class EscalationRule:
    """Rule describing when a packet's priority should be escalated.

    Attributes:
        min_priority: Only packets at or above this priority are eligible.
        no_response_seconds: If no response has been seen within this many
            seconds, escalate.
        bump_to: The priority level to escalate to.
    """

    min_priority: Priority = Priority.HIGH
    no_response_seconds: float = 30.0
    bump_to: Priority = Priority.CRITICAL

    def should_escalate(
        self,
        sent_at: datetime,
        current_priority: Priority,
        has_response: bool,
        now: datetime | None = None,
    ) -> bool:
        """Return True if the packet should be escalated."""
        if has_response:
            return False
        if current_priority.rank < self.min_priority.rank:
            return False
        if current_priority.rank >= self.bump_to.rank:
            return False
        now = now or datetime.now(timezone.utc)
        elapsed = (now - sent_at).total_seconds()
        return elapsed >= self.no_response_seconds


@dataclass
class ProtocolContext:
    """Runtime policy bundle for an agent's CNS interactions."""

    default_intent: Intent = Intent.QUERY
    default_priority: Priority = Priority.NORMAL
    escalation_rules: list[EscalationRule] = field(default_factory=list)
    secrets: dict[str, str] = field(default_factory=dict)

    def check_escalation(
        self,
        sent_at: datetime,
        priority: Priority,
        has_response: bool,
        now: datetime | None = None,
    ) -> Priority:
        """Return the escalated priority, or the original if no rule matches."""
        for rule in self.escalation_rules:
            if rule.should_escalate(sent_at, priority, has_response, now):
                return rule.bump_to
        return priority

    def get_secret(self, origin_id: str) -> str | None:
        return self.secrets.get(origin_id)
