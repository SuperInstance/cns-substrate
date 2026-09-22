"""Tests for protocol intents, priorities, and escalation rules."""

from datetime import datetime, timedelta, timezone

import pytest

from cns_bridge.protocol import (
    EscalationRule,
    Intent,
    Priority,
    ProtocolContext,
    higher_priority,
)


def test_intent_values() -> None:
    assert Intent.SENSE.value == "sense"
    assert Intent.COMMAND.value == "command"
    assert Intent.QUERY.value == "query"
    assert Intent.RESPONSE.value == "response"
    assert Intent.ALERT.value == "alert"
    assert Intent.HEARTBEAT.value == "heartbeat"
    assert Intent.REGISTER.value == "register"
    assert Intent.ESCALATION.value == "escalation"


def test_priority_ordering() -> None:
    assert Priority.LOW.rank < Priority.NORMAL.rank
    assert Priority.NORMAL.rank < Priority.HIGH.rank
    assert Priority.HIGH.rank < Priority.CRITICAL.rank


def test_higher_priority() -> None:
    assert higher_priority(Priority.NORMAL, Priority.HIGH) == Priority.HIGH
    assert higher_priority(Priority.CRITICAL, Priority.HIGH) == Priority.CRITICAL
    assert higher_priority(Priority.LOW, Priority.LOW) == Priority.LOW


def test_escalation_rule_matches() -> None:
    rule = EscalationRule(
        min_priority=Priority.HIGH,
        no_response_seconds=10.0,
        bump_to=Priority.CRITICAL,
    )
    sent_at = datetime.now(timezone.utc) - timedelta(seconds=15)
    assert rule.should_escalate(sent_at, Priority.HIGH, has_response=False)


def test_escalation_rule_no_match_when_responded() -> None:
    rule = EscalationRule(
        min_priority=Priority.HIGH,
        no_response_seconds=10.0,
        bump_to=Priority.CRITICAL,
    )
    sent_at = datetime.now(timezone.utc) - timedelta(seconds=15)
    assert not rule.should_escalate(sent_at, Priority.HIGH, has_response=True)


def test_escalation_rule_no_match_for_low_priority() -> None:
    rule = EscalationRule(
        min_priority=Priority.HIGH,
        no_response_seconds=10.0,
        bump_to=Priority.CRITICAL,
    )
    sent_at = datetime.now(timezone.utc) - timedelta(seconds=15)
    assert not rule.should_escalate(sent_at, Priority.NORMAL, has_response=False)


def test_escalation_rule_not_yet_due() -> None:
    rule = EscalationRule(
        min_priority=Priority.HIGH,
        no_response_seconds=10.0,
        bump_to=Priority.CRITICAL,
    )
    sent_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    assert not rule.should_escalate(sent_at, Priority.HIGH, has_response=False)


def test_protocol_context_escalation() -> None:
    ctx = ProtocolContext(
        escalation_rules=[
            EscalationRule(
                min_priority=Priority.HIGH,
                no_response_seconds=1.0,
                bump_to=Priority.CRITICAL,
            )
        ]
    )
    sent_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    assert (
        ctx.check_escalation(sent_at, Priority.HIGH, has_response=False)
        == Priority.CRITICAL
    )
    assert (
        ctx.check_escalation(sent_at, Priority.HIGH, has_response=True)
        == Priority.HIGH
    )


def test_protocol_context_secret_lookup() -> None:
    ctx = ProtocolContext(secrets={"lucineer": "abc"})
    assert ctx.get_secret("lucineer") == "abc"
    assert ctx.get_secret("wesley") is None
