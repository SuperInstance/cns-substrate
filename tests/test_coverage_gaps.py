"""
Tests for coverage gaps in cns-bridge: transport exception paths,
heartbeat exception/reset paths, packet builder edge cases, and
protocol escalation edge cases.

Targeting the missing lines identified by pytest --cov.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from cns_bridge.packet import Packet, PacketBuilder, Priority, Intent
from cns_bridge.protocol import EscalationRule, ProtocolContext
from cns_bridge.transport import FileSystemTransport
from cns_bridge.heartbeat import HeartbeatPoller


# ─── Transport: exception paths ───

class TestTransportExceptionPaths:
    def test_send_cleans_up_temp_on_failure(self, tmp_path):
        """When os.replace fails, the temp file should be cleaned up."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out")
        packet = PacketBuilder(origin_id="alice").to("bob").with_data(msg="hello").build()

        temp_files_created = []
        def tracking_replace(src, dst):
            temp_files_created.append(src)
            raise OSError("simulated failure")

        with patch("cns_bridge.transport.os.replace", tracking_replace):
            with pytest.raises(OSError):
                t.send(packet)

        for tf in temp_files_created:
            assert not Path(tf).exists()

    def test_send_temp_cleanup_handles_already_deleted(self, tmp_path):
        """If the temp file is already gone when cleanup runs, don't crash."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out")
        packet = PacketBuilder(origin_id="alice").to("bob").with_data(msg="hello").build()

        with patch("cns_bridge.transport.os.replace", side_effect=OSError("fail")), \
             patch("cns_bridge.transport.os.unlink", side_effect=FileNotFoundError("gone")):
            with pytest.raises(OSError):
                t.send(packet)

    def test_peek_with_corrupt_file_skipped(self, tmp_path):
        """Peek should skip corrupt JSON files."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        (tmp_path / "in" / "bad.uscp.json").write_text("not json at all")

        packet = PacketBuilder(origin_id="alice").to("bob").build()
        good_path = t.send(packet)
        import shutil
        shutil.move(str(good_path), str(tmp_path / "in" / "alice_p1.uscp.json"))

        result = t.peek()
        assert result is not None
        assert result.header.origin_id == "alice"

    def test_receive_corrupt_file_skipped(self, tmp_path):
        """Receive should skip corrupt JSON files."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        (tmp_path / "in" / "bad.uscp.json").write_text("not json")

        packet = PacketBuilder(origin_id="alice").to("bob").build()
        packet_path = tmp_path / "in" / "alice_p1.uscp.json"
        packet_path.write_text(packet.to_json())

        result = t.receive()
        assert result is not None
        assert result.header.origin_id == "alice"
        assert (tmp_path / "in" / "bad.uscp.json").exists()

    def test_next_inbox_file_os_error_skipped(self, tmp_path):
        """_next_inbox_file should skip files that raise OSError on read."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        f = tmp_path / "in" / "unreadable.uscp.json"
        f.write_text('{"header": {"origin_id": "x"}}')

        original_read = Path.read_text
        def maybe_fail(self, *args, **kwargs):
            if "unreadable" in str(self):
                raise OSError("permission denied")
            return original_read(self, *args, **kwargs)

        with patch.object(Path, "read_text", maybe_fail):
            result = t._next_inbox_file(None)
            assert result is None

    def test_poll_with_origin_filter_skips_corrupt(self, tmp_path):
        """poll(origin_id=...) should skip corrupt JSON files."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        (tmp_path / "in" / "bad.uscp.json").write_text("corrupt")
        packet = PacketBuilder(origin_id="alice").to("bob").build()
        (tmp_path / "in" / "good.uscp.json").write_text(packet.to_json())

        # With origin filter, corrupt files are caught by the try/except
        results = list(t.poll(origin_id="alice"))
        assert len(results) == 1
        assert results[0].header.origin_id == "alice"


# ─── Heartbeat: exception and reset paths ───

class TestHeartbeatExceptionPaths:
    def test_callback_exception_does_not_crash_poller(self, tmp_path):
        """If the callback raises, the poller should continue."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        packet = (PacketBuilder(origin_id="alice")
                  .to("bob")
                  .with_intent(Intent.RESPONSE)
                  .build())
        (tmp_path / "in" / "p1.uscp.json").write_text(packet.to_json())

        call_count = [0]
        def buggy_callback(pkt):
            call_count[0] += 1
            raise ValueError("handler bug")

        poller = HeartbeatPoller(t, agent_id="bob", callback=buggy_callback, interval=0.05)
        poller.start()
        time.sleep(0.3)
        poller.stop(timeout=2)

        assert call_count[0] >= 1
        assert not poller.is_running()

    def test_transport_error_does_not_crash_poller(self, tmp_path):
        """If transport.poll() raises, the background thread should survive."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)

        def failing_poll():
            raise OSError("filesystem went away")
            yield  # never reached

        with patch.object(t, "poll", failing_poll):
            poller = HeartbeatPoller(t, agent_id="bob", callback=lambda p: None, interval=0.05)
            poller.start()
            time.sleep(0.2)
            assert poller.is_running()
            poller.stop(timeout=2)

    def test_reset_seen_clears_set(self, tmp_path):
        """reset_seen should empty the _seen set."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        poller = HeartbeatPoller(t, agent_id="bob", callback=lambda p: None)

        poller._seen.add("pkt-1")
        poller._seen.add("pkt-2")
        assert len(poller._seen) == 2

        poller.reset_seen()
        assert len(poller._seen) == 0

    def test_reset_seen_with_lock(self, tmp_path):
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        poller = HeartbeatPoller(t, agent_id="bob", callback=lambda p: None)

        assert hasattr(poller, '_lock')
        poller._seen.add("x")
        poller.reset_seen()
        assert poller._seen == set()

    def test_seen_prevents_duplicate_delivery(self, tmp_path):
        """A packet already delivered should not be delivered again."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        packet = (PacketBuilder(origin_id="alice")
                  .to("bob")
                  .with_intent(Intent.RESPONSE)
                  .with_packet_id("unique-123")
                  .build())

        delivered = []
        def callback(pkt):
            delivered.append(pkt)

        poller = HeartbeatPoller(t, agent_id="bob", callback=callback, interval=0.05)

        (tmp_path / "in" / "p1.uscp.json").write_text(packet.to_json())
        poller._poll_once()
        assert len(delivered) == 1

        (tmp_path / "in" / "p1.uscp.json").write_text(packet.to_json())
        poller._poll_once()
        assert len(delivered) == 1

    def test_start_when_already_running_returns_self(self, tmp_path):
        """Starting an already-running poller should return self."""
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        poller = HeartbeatPoller(t, agent_id="bob", callback=lambda p: None, interval=0.5)
        poller.start()
        first_thread = poller._thread

        poller.start()
        assert poller._thread is first_thread

        poller.stop(timeout=2)


# ─── Packet Builder: with_packet_id ───

class TestPacketBuilderEdgeCases:
    def test_with_packet_id_sets_id(self):
        pkt = PacketBuilder(origin_id="a").to("b").with_packet_id("custom-42").build()
        assert pkt.header.packet_id == "custom-42"

    def test_with_packet_id_returns_builder_for_chaining(self):
        b = PacketBuilder(origin_id="a").to("b")
        result = b.with_packet_id("test")
        assert result is b

    def test_with_packet_id_overwrites_previous(self):
        b = (PacketBuilder(origin_id="a")
             .to("b")
             .with_packet_id("first"))
        pkt = b.with_packet_id("second").build()
        assert pkt.header.packet_id == "second"

    def test_with_packet_id_empty_string(self):
        pkt = PacketBuilder(origin_id="a").to("b").with_packet_id("").build()
        assert pkt.header.packet_id == ""


# ─── Protocol: escalation already at bump_to ───

class TestProtocolEscalationEdgeCases:
    def test_should_not_escalate_if_already_at_bump_to(self):
        """If current_priority is already at bump_to level, don't escalate."""
        rule = EscalationRule(
            min_priority=Priority.HIGH,
            no_response_seconds=10,
            bump_to=Priority.CRITICAL,
        )
        sent = datetime.now(timezone.utc) - timedelta(seconds=100)
        assert rule.should_escalate(sent, Priority.CRITICAL, has_response=False) is False

    def test_should_not_escalate_if_above_bump_to(self):
        rule = EscalationRule(
            min_priority=Priority.LOW,
            no_response_seconds=1,
            bump_to=Priority.NORMAL,
        )
        sent = datetime.now(timezone.utc) - timedelta(seconds=100)
        assert rule.should_escalate(sent, Priority.HIGH, has_response=False) is False

    def test_check_escalation_returns_original_when_no_rule_matches(self):
        ctx = ProtocolContext(escalation_rules=[])
        sent = datetime.now(timezone.utc)
        result = ctx.check_escalation(sent, Priority.NORMAL, has_response=False)
        assert result == Priority.NORMAL

    def test_check_escalation_applies_first_matching_rule(self):
        rule1 = EscalationRule(min_priority=Priority.LOW, no_response_seconds=1, bump_to=Priority.HIGH)
        rule2 = EscalationRule(min_priority=Priority.NORMAL, no_response_seconds=1, bump_to=Priority.CRITICAL)
        ctx = ProtocolContext(escalation_rules=[rule1, rule2])
        sent = datetime.now(timezone.utc) - timedelta(seconds=100)
        result = ctx.check_escalation(sent, Priority.LOW, has_response=False)
        assert result == Priority.HIGH

    def test_get_secret_returns_none_for_unknown(self):
        ctx = ProtocolContext(secrets={"alice": "key123"})
        assert ctx.get_secret("alice") == "key123"
        assert ctx.get_secret("unknown") is None

    def test_get_secret_empty_context(self):
        ctx = ProtocolContext()
        assert ctx.get_secret("anyone") is None


# ─── Transport: additional edge cases ───

class TestTransportAdditionalEdges:
    def test_packet_files_nonexistent_directory(self, tmp_path):
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out")
        result = t._packet_files(tmp_path / "does_not_exist")
        assert result == []

    def test_packet_files_empty_directory(self, tmp_path):
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out")
        result = t._packet_files(tmp_path)
        assert result == []

    def test_packet_files_sorted_by_mtime(self, tmp_path):
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out")
        f1 = tmp_path / "b.uscp.json"; f1.write_text("{}")
        time.sleep(0.05)
        f2 = tmp_path / "a.uscp.json"; f2.write_text("{}")
        time.sleep(0.05)
        f3 = tmp_path / "c.uscp.json"; f3.write_text("{}")

        files = t._packet_files(tmp_path)
        assert [f.name for f in files] == ["b.uscp.json", "a.uscp.json", "c.uscp.json"]

    def test_packet_files_ignores_directories(self, tmp_path):
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out")
        (tmp_path / "weird.uscp.json").mkdir()
        (tmp_path / "real.uscp.json").write_text("{}")
        files = t._packet_files(tmp_path)
        names = [f.name for f in files]
        assert "real.uscp.json" in names
        assert "weird.uscp.json" not in names

    def test_list_inbox_and_outbox(self, tmp_path):
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        assert t.list_inbox() == []
        assert t.list_outbox() == []

        packet = PacketBuilder(origin_id="a").to("b").build()
        t.send(packet)
        assert len(t.list_outbox()) == 1

    def test_receive_with_origin_filter_skips_non_matching(self, tmp_path):
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        p1 = PacketBuilder(origin_id="alice").to("bob").build()
        p2 = PacketBuilder(origin_id="charlie").to("bob").build()
        (tmp_path / "in" / "p1.uscp.json").write_text(p1.to_json())
        (tmp_path / "in" / "p2.uscp.json").write_text(p2.to_json())

        result = t.receive(origin_id="charlie")
        assert result is not None
        assert result.header.origin_id == "charlie"
        assert len(t.list_inbox()) == 1

    def test_poll_with_origin_filter(self, tmp_path):
        t = FileSystemTransport(inbox_path=tmp_path/"in", outbox_path=tmp_path/"out", create_dirs=True)
        p1 = PacketBuilder(origin_id="alice").to("bob").build()
        p2 = PacketBuilder(origin_id="alice").to("bob").build()
        p3 = PacketBuilder(origin_id="charlie").to("bob").build()
        for i, p in enumerate([p1, p2, p3]):
            (tmp_path / "in" / f"p{i}.uscp.json").write_text(p.to_json())

        results = list(t.poll(origin_id="alice"))
        assert len(results) == 2
        assert all(r.header.origin_id == "alice" for r in results)
