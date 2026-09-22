"""Tests for the background heartbeat poller."""

import time
from pathlib import Path

import pytest

from cns_bridge import FileSystemTransport, HeartbeatPoller, Intent, Packet, PacketBuilder


def _drop_inbox(transport: FileSystemTransport, packet: Packet) -> None:
    """Place a packet directly into the agent's inbox (simulating Hermes)."""
    filename = f"{packet.header.origin_id}_{packet.header.packet_id}.uscp.json"
    path = transport.inbox_path / filename
    path.write_text(packet.to_json(), encoding="utf-8")


@pytest.fixture
def transport(tmp_path: Path) -> FileSystemTransport:
    return FileSystemTransport(
        inbox_path=tmp_path / "inbox",
        outbox_path=tmp_path / "outbox",
    )


def test_poller_delivers_matching_packet(transport: FileSystemTransport) -> None:
    received = []

    poller = HeartbeatPoller(
        transport=transport,
        agent_id="lucineer",
        callback=received.append,
        interval=0.1,
    )
    poller.start()

    response = PacketBuilder(origin_id="hermes").to("lucineer").build()
    _drop_inbox(transport, response)

    time.sleep(0.35)
    poller.stop()

    assert len(received) == 1
    assert received[0].header.packet_id == response.header.packet_id


def test_poller_ignores_non_matching_packet(transport: FileSystemTransport) -> None:
    received = []

    poller = HeartbeatPoller(
        transport=transport,
        agent_id="lucineer",
        callback=received.append,
        interval=0.1,
    )
    poller.start()

    other = PacketBuilder(origin_id="hermes").to("wesley").build()
    _drop_inbox(transport, other)

    time.sleep(0.35)
    poller.stop()

    assert len(received) == 0


def test_poller_dedupes_seen_packets(transport: FileSystemTransport) -> None:
    received = []

    poller = HeartbeatPoller(
        transport=transport,
        agent_id="lucineer",
        callback=received.append,
        interval=0.1,
    )
    poller.start()

    response = PacketBuilder(origin_id="hermes").to("lucineer").build()
    _drop_inbox(transport, response)

    time.sleep(0.35)
    # Send the same file again (simulating a duplicate write).
    _drop_inbox(transport, response)

    time.sleep(0.35)
    poller.stop()

    assert len(received) == 1


def test_poller_can_filter_origin(transport: FileSystemTransport) -> None:
    received = []

    poller = HeartbeatPoller(
        transport=transport,
        agent_id="lucineer",
        callback=received.append,
        interval=0.1,
        filter_origin=True,
    )
    poller.start()

    outgoing = PacketBuilder(origin_id="lucineer").to("hermes").build()
    _drop_inbox(transport, outgoing)

    time.sleep(0.35)
    poller.stop()

    assert len(received) == 1
    assert received[0].header.origin_id == "lucineer"


def test_poller_stop_is_idempotent(transport: FileSystemTransport) -> None:
    poller = HeartbeatPoller(
        transport=transport,
        agent_id="lucineer",
        callback=lambda p: None,
        interval=0.1,
    )
    poller.start()
    assert poller.is_running()
    poller.stop()
    assert not poller.is_running()
    poller.stop()  # should not raise


def _raising_callback(packet: Packet) -> None:
    raise RuntimeError("handler bug")


def test_poller_dead_letters_packet_on_handler_failure(
    transport: FileSystemTransport, tmp_path: Path
) -> None:
    """A raising handler must not lose the packet: it is preserved on disk."""
    dead_letter = tmp_path / "dead_letter"
    poller = HeartbeatPoller(
        transport=transport,
        agent_id="lucineer",
        callback=_raising_callback,
        interval=0.1,
        dead_letter_path=dead_letter,
    )
    poller.start()

    response = PacketBuilder(origin_id="hermes").to("lucineer").build()
    _drop_inbox(transport, response)

    time.sleep(0.35)
    poller.stop()

    files = list(dead_letter.glob("*.uscp.json"))
    assert len(files) == 1
    preserved = Packet.from_json(files[0].read_text(encoding="utf-8"))
    assert preserved.header.packet_id == response.header.packet_id


def test_poller_survives_handler_failure(transport: FileSystemTransport) -> None:
    """The polling thread must keep running after a handler raises."""
    received = []

    def flaky(packet: Packet) -> None:
        if packet.header.origin_id == "hermes":
            raise RuntimeError("handler bug")
        received.append(packet)

    poller = HeartbeatPoller(
        transport=transport,
        agent_id="lucineer",
        callback=flaky,
        interval=0.1,
        dead_letter_path=transport.outbox_path.parent / "dead_letter",
    )
    poller.start()

    bad = PacketBuilder(origin_id="hermes").to("lucineer").build()
    _drop_inbox(transport, bad)

    time.sleep(0.35)
    good = PacketBuilder(origin_id="wesley").to("lucineer").build()
    _drop_inbox(transport, good)

    time.sleep(0.35)
    poller.stop()

    assert poller.is_running() is False  # stopped cleanly
    assert len(received) == 1
    assert received[0].header.packet_id == good.header.packet_id


def test_poller_dead_letter_default_is_derived(
    transport: FileSystemTransport,
) -> None:
    """Without an explicit path, dead letters land next to the outbox."""
    poller = HeartbeatPoller(
        transport=transport,
        agent_id="lucineer",
        callback=_raising_callback,
        interval=0.1,
    )
    poller.start()

    response = PacketBuilder(origin_id="hermes").to("lucineer").build()
    _drop_inbox(transport, response)

    time.sleep(0.35)
    poller.stop()

    default_dir = transport.outbox_path.parent / "cns_dead_letter"
    assert len(list(default_dir.glob("*.uscp.json"))) == 1


def test_poller_can_disable_dead_letter(
    transport: FileSystemTransport, tmp_path: Path
) -> None:
    """dead_letter_path=False restores the old silent-drop behavior."""
    poller = HeartbeatPoller(
        transport=transport,
        agent_id="lucineer",
        callback=_raising_callback,
        interval=0.1,
        dead_letter_path=False,
    )
    poller.start()

    response = PacketBuilder(origin_id="hermes").to("lucineer").build()
    _drop_inbox(transport, response)

    time.sleep(0.35)
    poller.stop()

    # Packet consumed, nothing preserved, poller still healthy.
    assert len(list(transport.inbox_path.iterdir())) == 0
    assert not (transport.outbox_path.parent / "cns_dead_letter").exists()
