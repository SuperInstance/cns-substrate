"""Tests for the base Agent class."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cns_bridge import (
    Agent,
    EscalationRule,
    FileSystemTransport,
    Intent,
    PacketBuilder,
    Priority,
    ProtocolContext,
)


@pytest.fixture
def transport(tmp_path: Path) -> FileSystemTransport:
    return FileSystemTransport(
        inbox_path=tmp_path / "inbox",
        outbox_path=tmp_path / "outbox",
    )


def test_agent_send_builds_packet(transport: FileSystemTransport) -> None:
    agent = Agent(agent_id="lucineer", transport=transport)
    packet = agent.send(
        intent=Intent.QUERY,
        data={"question": "status"},
        message="Hello Hermes",
        priority=Priority.HIGH,
    )
    assert packet.header.origin_id == "lucineer"
    assert packet.header.destination_id == "hermes"
    assert packet.header.intent == Intent.QUERY
    assert packet.header.priority == Priority.HIGH
    assert packet.body.message == "Hello Hermes"
    assert packet.body.data["question"] == "status"

    outbox = transport.list_outbox()
    assert len(outbox) == 1


def test_agent_send_with_secret_signs_packet(transport: FileSystemTransport) -> None:
    agent = Agent(agent_id="lucineer", transport=transport, secret="secret")
    packet = agent.send(intent=Intent.HEARTBEAT)
    assert packet.signature.value
    assert packet.verify("secret")


def test_agent_receive_verifies_signature(transport: FileSystemTransport) -> None:
    agent = Agent(agent_id="lucineer", transport=transport, secret="secret")

    incoming = (
        PacketBuilder(origin_id="hermes")
        .to("lucineer")
        .with_data(answer=42)
        .signed_with("secret", key_id="hermes")
        .build()
    )
    (transport.inbox_path / "hermes_1.uscp.json").write_text(incoming.to_json())

    received = agent.receive()
    assert received is not None
    assert received.signature.verified is True


def test_agent_handle_not_implemented(transport: FileSystemTransport) -> None:
    agent = Agent(agent_id="lucineer", transport=transport)
    packet = PacketBuilder(origin_id="hermes").to("lucineer").build()
    with pytest.raises(NotImplementedError):
        agent.handle(packet)


def test_agent_on_response_decorator(transport: FileSystemTransport) -> None:
    agent = Agent(agent_id="lucineer", transport=transport)
    calls = []

    @agent.on_response
    def handler(packet):
        calls.append(packet.header.packet_id)

    packet = PacketBuilder(origin_id="hermes").to("lucineer").build()
    agent._dispatch_response(packet)

    assert len(calls) == 1


def test_agent_escalation(transport: FileSystemTransport) -> None:
    context = ProtocolContext(
        escalation_rules=[
            EscalationRule(
                min_priority=Priority.HIGH,
                no_response_seconds=1.0,
                bump_to=Priority.CRITICAL,
            )
        ]
    )
    agent = Agent(agent_id="lucineer", transport=transport, context=context)

    packet = PacketBuilder(origin_id="lucineer").with_priority(Priority.HIGH).build()
    # Rewind timestamp manually so the rule triggers.
    packet.header.timestamp = (
        datetime.now(timezone.utc) - timedelta(seconds=2)
    ).isoformat()

    escalated = agent.escalate_if_needed(packet, has_response=False)
    assert escalated == Priority.CRITICAL

    not_escalated = agent.escalate_if_needed(packet, has_response=True)
    assert not_escalated == Priority.HIGH


def test_agent_heartbeat_start_stop(transport: FileSystemTransport) -> None:
    agent = Agent(agent_id="lucineer", transport=transport)
    poller = agent.start_heartbeat(interval=0.1)
    assert poller.is_running()
    agent.stop_heartbeat()
    assert not poller.is_running()
