"""Tests for filesystem inbox/outbox transport."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from cns_bridge import FileSystemTransport, Packet, PacketBuilder


@pytest.fixture
def transport(tmp_path: Path) -> FileSystemTransport:
    return FileSystemTransport(
        inbox_path=tmp_path / "inbox",
        outbox_path=tmp_path / "outbox",
    )


def test_paths_default_to_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CNS_INBOX", str(tmp_path / "env_inbox"))
    monkeypatch.setenv("CNS_OUTBOX", str(tmp_path / "env_outbox"))
    t = FileSystemTransport()
    assert t.inbox_path == tmp_path / "env_inbox"
    assert t.outbox_path == tmp_path / "env_outbox"


def test_send_creates_outbox_file(transport: FileSystemTransport) -> None:
    packet = PacketBuilder(origin_id="lucineer").with_data(a=1).build()
    path = transport.send(packet)
    assert path.exists()
    assert path.parent == transport.outbox_path
    data = json.loads(path.read_text())
    assert data["header"]["origin_id"] == "lucineer"


def test_receive_returns_oldest_packet(transport: FileSystemTransport) -> None:
    packet = PacketBuilder(origin_id="hermes").with_data(n=1).build()
    transport.send(packet)
    # Send to outbox so it can be moved to inbox for the test.
    inbox_file = transport.inbox_path / "hermes_test.uscp.json"
    inbox_file.write_text(packet.to_json())

    received = transport.receive()
    assert received is not None
    assert received.header.origin_id == "hermes"
    assert not inbox_file.exists()


def test_receive_filters_by_origin_id(transport: FileSystemTransport) -> None:
    a = PacketBuilder(origin_id="lucineer").build()
    b = PacketBuilder(origin_id="wesley").build()
    (transport.inbox_path / "lucineer_1.uscp.json").write_text(a.to_json())
    (transport.inbox_path / "wesley_1.uscp.json").write_text(b.to_json())

    received = transport.receive(origin_id="wesley")
    assert received is not None
    assert received.header.origin_id == "wesley"
    assert len(transport.list_inbox()) == 1


def test_receive_empty_returns_none(transport: FileSystemTransport) -> None:
    assert transport.receive() is None


def test_peek_does_not_remove(transport: FileSystemTransport) -> None:
    packet = PacketBuilder(origin_id="hermes").build()
    (transport.inbox_path / "hermes_1.uscp.json").write_text(packet.to_json())

    first = transport.peek()
    second = transport.peek()
    assert first is not None
    assert second is not None
    assert first.header.packet_id == second.header.packet_id
    assert len(transport.list_inbox()) == 1


def test_poll_yields_packets(transport: FileSystemTransport) -> None:
    a = PacketBuilder(origin_id="lucineer").build()
    b = PacketBuilder(origin_id="hermes").build()
    (transport.inbox_path / "lucineer_1.uscp.json").write_text(a.to_json())
    (transport.inbox_path / "hermes_1.uscp.json").write_text(b.to_json())

    packets = list(transport.poll())
    assert len(packets) == 2
    assert len(transport.list_inbox()) == 0


def test_poll_can_filter_origin(transport: FileSystemTransport) -> None:
    a = PacketBuilder(origin_id="lucineer").build()
    b = PacketBuilder(origin_id="hermes").build()
    (transport.inbox_path / "lucineer_1.uscp.json").write_text(a.to_json())
    (transport.inbox_path / "hermes_1.uscp.json").write_text(b.to_json())

    packets = list(transport.poll(origin_id="lucineer"))
    assert len(packets) == 1
    assert packets[0].header.origin_id == "lucineer"
    assert len(transport.list_inbox()) == 1


def test_corrupt_file_is_skipped(transport: FileSystemTransport) -> None:
    (transport.inbox_path / "bad.uscp.json").write_text("not valid json")
    packet = PacketBuilder(origin_id="hermes").build()
    (transport.inbox_path / "good.uscp.json").write_text(packet.to_json())

    received = transport.receive()
    assert received is not None
    assert received.header.origin_id == "hermes"


def test_poll_tolerates_concurrent_removal(
    transport: FileSystemTransport, monkeypatch
) -> None:
    """If another consumer deletes the file first, the packet still survives.

    The unlink is best-effort: the data matters more than the removal.
    """
    packet = PacketBuilder(origin_id="hermes").to("lucineer").build()
    path = transport.inbox_path / f"{packet.header.origin_id}_{packet.header.packet_id}.uscp.json"
    path.write_text(packet.to_json(), encoding="utf-8")

    real_unlink = type(path).unlink

    def flaky_unlink(self, *args, **kwargs):
        raise FileNotFoundError  # someone else removed it first

    monkeypatch.setattr(type(path), "unlink", flaky_unlink)

    results = list(transport.poll())
    monkeypatch.setattr(type(path), "unlink", real_unlink)

    assert len(results) == 1
    assert results[0].header.packet_id == packet.header.packet_id


def test_poll_reads_each_file_once(transport: FileSystemTransport, monkeypatch) -> None:
    """Origin filtering must not read the same bytes twice."""
    packet = PacketBuilder(origin_id="hermes").to("lucineer").build()
    path = transport.inbox_path / f"{packet.header.origin_id}_{packet.header.packet_id}.uscp.json"
    path.write_text(packet.to_json(), encoding="utf-8")

    reads = {"count": 0}
    real_read = Path.read_text

    def counting_read(self, *args, **kwargs):
        reads["count"] += 1
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read)

    results = list(transport.poll(origin_id="hermes"))
    assert len(results) == 1
    assert reads["count"] == 1
