"""Edge-case tests for Agent, Packet, Protocol, and Transport."""

import json
import time
import pytest
from pathlib import Path
from datetime import datetime, timezone

from cns_bridge import (
    Agent,
    Body,
    EscalationRule,
    FileSystemTransport,
    Header,
    Intent,
    Packet,
    PacketBuilder,
    Priority,
    ProtocolContext,
    Signature,
)


# ── Agent edge cases ──────────────────────────────────────────────

@pytest.fixture
def transport(tmp_path):
    return FileSystemTransport(
        inbox_path=tmp_path / "inbox",
        outbox_path=tmp_path / "outbox",
    )


@pytest.fixture
def agent(transport):
    return Agent(agent_id="test-agent", transport=transport)


class TestAgentEdgeCases:
    def test_send_with_defaults(self, agent, transport):
        """Send with no args uses context defaults."""
        packet = agent.send()
        assert packet.header.origin_id == "test-agent"
        assert packet.header.destination_id == "hermes"
        assert len(transport.list_outbox()) == 1

    def test_send_with_correlation_id(self, agent):
        packet = agent.send(correlation_id="corr-123")
        assert packet.header.correlation_id == "corr-123"

    def test_send_with_schema(self, agent):
        packet = agent.send(schema="build.v1")
        assert packet.body.schema == "build.v1"

    def test_send_with_data_kwargs(self, agent):
        packet = agent.send(data={"key": "value", "count": 42})
        assert packet.body.data["key"] == "value"
        assert packet.body.data["count"] == 42

    def test_receive_empty_inbox(self, agent):
        assert agent.receive() is None

    def test_receive_without_secret_no_verification(self, transport):
        """Agent without secret should still receive packets."""
        agent = Agent(agent_id="a", transport=transport)
        packet = PacketBuilder(origin_id="hermes").to("a").build()
        (transport.inbox_path / "h_1.uscp.json").write_text(packet.to_json())
        received = agent.receive()
        assert received is not None
        assert received.signature.value == ""  # unsigned

    def test_multiple_response_handlers(self, agent):
        """Multiple on_response handlers should all fire."""
        calls = []

        @agent.on_response
        def h1(pkt):
            calls.append("h1")

        @agent.on_response
        def h2(pkt):
            calls.append("h2")

        pkt = PacketBuilder(origin_id="x").build()
        agent._dispatch_response(pkt)
        assert "h1" in calls
        assert "h2" in calls

    def test_dispatch_response_handles_not_implemented(self, agent):
        """_dispatch_response should swallow NotImplementedError from handle()."""
        pkt = PacketBuilder(origin_id="x").build()
        # Should not raise
        agent._dispatch_response(pkt)

    def test_builder_returns_fresh_instance(self, agent):
        b1 = agent.builder()
        b2 = agent.builder()
        assert b1 is not b2

    def test_stop_heartbeat_without_start(self, agent):
        """stop_heartbeat should be safe to call if never started."""
        agent.stop_heartbeat()  # should not raise

    def test_escalate_no_rules(self, transport):
        """Agent without escalation rules should return same priority."""
        agent = Agent(agent_id="a", transport=transport)
        packet = PacketBuilder(origin_id="a").with_priority(Priority.NORMAL).build()
        result = agent.escalate_if_needed(packet, has_response=False)
        assert result == Priority.NORMAL


# ── Packet edge cases ─────────────────────────────────────────────

class TestPacketEdgeCases:
    def test_packet_to_json_roundtrip(self):
        """Packet should survive JSON serialization."""
        pkt = (
            PacketBuilder(origin_id="test")
            .to("dest")
            .with_intent(Intent.HEARTBEAT)
            .with_priority(Priority.HIGH)
            .with_message("hello")
            .with_data(key="val")
            .build()
        )
        json_str = pkt.to_json()
        data = json.loads(json_str)
        assert data["header"]["origin_id"] == "test"
        assert data["header"]["destination_id"] == "dest"
        assert data["body"]["message"] == "hello"

    def test_packet_id_is_unique(self):
        p1 = PacketBuilder(origin_id="a").build()
        time.sleep(0.001)
        p2 = PacketBuilder(origin_id="a").build()
        assert p1.header.packet_id != p2.header.packet_id

    def test_packet_timestamp_is_iso(self):
        pkt = PacketBuilder(origin_id="a").build()
        ts = pkt.header.timestamp
        # Should parse as ISO datetime
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None

    def test_signature_empty_by_default(self):
        pkt = PacketBuilder(origin_id="a").build()
        assert pkt.signature.value == ""

    def test_signed_packet_verification_wrong_secret(self):
        pkt = (
            PacketBuilder(origin_id="a")
            .signed_with("correct-secret", key_id="a")
            .build()
        )
        # Wrong secret should fail verification
        result = pkt.verify("wrong-secret")
        assert result is False

    def test_header_construction(self):
        h = Header(
            packet_id="id-1",
            origin_id="origin",
            destination_id="dest",
            timestamp="2026-08-05T12:00:00+00:00",
            intent=Intent.QUERY,
            priority=Priority.LOW,
        )
        assert h.packet_id == "id-1"
        assert h.destination_id == "dest"

    def test_body_construction(self):
        b = Body(message="test", data={"x": 1})
        assert b.message == "test"
        assert b.data["x"] == 1

    def test_signature_construction(self):
        s = Signature(value="abc123", key_id="key1", algorithm="HS256")
        assert s.value == "abc123"
        assert s.algorithm == "HS256"


# ── PacketBuilder edge cases ──────────────────────────────────────

class TestPacketBuilderEdgeCases:
    def test_builder_chaining(self):
        pkt = (
            PacketBuilder(origin_id="x")
            .to("y")
            .with_intent(Intent.ALERT)
            .with_priority(Priority.CRITICAL)
            .with_message("STOP")
            .with_data(reason="test")
            .with_correlation_id("corr-1")
            .with_schema("v1")
            .build()
        )
        assert pkt.header.destination_id == "y"
        assert pkt.header.intent == Intent.ALERT
        assert pkt.header.priority == Priority.CRITICAL
        assert pkt.body.message == "STOP"
        assert pkt.body.data["reason"] == "test"
        assert pkt.header.correlation_id == "corr-1"
        assert pkt.body.schema == "v1"

    def test_builder_minimal(self):
        """Builder with just origin_id should produce valid packet."""
        pkt = PacketBuilder(origin_id="x").build()
        assert pkt.header.origin_id == "x"
        assert pkt.header.destination_id  # should have a default
        assert pkt.body is not None

    def test_builder_with_data_multiple_kwargs(self):
        pkt = (
            PacketBuilder(origin_id="x")
            .with_data(a=1, b="two", c=[1, 2, 3], d={"nested": True})
            .build()
        )
        assert pkt.body.data["a"] == 1
        assert pkt.body.data["b"] == "two"
        assert pkt.body.data["c"] == [1, 2, 3]
        assert pkt.body.data["d"]["nested"] is True


# ── Protocol edge cases ───────────────────────────────────────────

class TestProtocolEdgeCases:
    def test_default_context(self):
        ctx = ProtocolContext()
        assert ctx.default_intent is not None
        assert ctx.default_priority is not None

    def test_context_with_custom_rules(self):
        rules = [
            EscalationRule(
                min_priority=Priority.NORMAL,
                no_response_seconds=5.0,
                bump_to=Priority.HIGH,
            ),
            EscalationRule(
                min_priority=Priority.HIGH,
                no_response_seconds=10.0,
                bump_to=Priority.CRITICAL,
            ),
        ]
        ctx = ProtocolContext(escalation_rules=rules)
        assert len(ctx.escalation_rules) == 2

    def test_escalation_no_bump_when_response_received(self):
        rule = EscalationRule(
            min_priority=Priority.HIGH,
            no_response_seconds=1.0,
            bump_to=Priority.CRITICAL,
        )
        ctx = ProtocolContext(escalation_rules=[rule])
        result = ctx.check_escalation(
            sent_at=datetime.now(timezone.utc),
            priority=Priority.HIGH,
            has_response=True,
        )
        assert result == Priority.HIGH

    def test_escalation_respects_min_priority(self):
        """LOW priority should not trigger HIGH escalation rule."""
        rule = EscalationRule(
            min_priority=Priority.HIGH,
            no_response_seconds=0.0,
            bump_to=Priority.CRITICAL,
        )
        ctx = ProtocolContext(escalation_rules=[rule])
        from datetime import timedelta
        old_time = datetime.now(timezone.utc) - timedelta(seconds=100)
        result = ctx.check_escalation(
            sent_at=old_time,
            priority=Priority.LOW,
            has_response=False,
        )
        assert result == Priority.LOW  # below threshold, no escalation


# ── Transport edge cases ──────────────────────────────────────────

class TestTransportEdgeCases:
    def test_send_and_list(self, tmp_path):
        transport = FileSystemTransport(
            inbox_path=tmp_path / "in",
            outbox_path=tmp_path / "out",
        )
        pkt = PacketBuilder(origin_id="x").build()
        transport.send(pkt)
        files = transport.list_outbox()
        assert len(files) == 1

    def test_receive_removes_from_inbox(self, tmp_path):
        transport = FileSystemTransport(
            inbox_path=tmp_path / "in",
            outbox_path=tmp_path / "out",
        )
        pkt = PacketBuilder(origin_id="hermes").to("me").build()
        (transport.inbox_path / "h_1.uscp.json").write_text(pkt.to_json())

        received = transport.receive()
        assert received is not None
        # Second receive should find nothing
        assert transport.receive() is None

    def test_transport_creates_directories(self, tmp_path):
        """Transport should create inbox/outbox dirs lazily."""
        transport = FileSystemTransport(
            inbox_path=tmp_path / "deep" / "nested" / "in",
            outbox_path=tmp_path / "deep" / "nested" / "out",
        )
        pkt = PacketBuilder(origin_id="x").build()
        transport.send(pkt)
        assert (tmp_path / "deep" / "nested" / "out").is_dir()

    def test_empty_outbox_list(self, tmp_path):
        transport = FileSystemTransport(
            inbox_path=tmp_path / "in",
            outbox_path=tmp_path / "out",
        )
        assert transport.list_outbox() == []
