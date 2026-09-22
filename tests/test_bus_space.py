"""Tests for BusSpace — the packet bus as a room the elephant reads."""

import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cns_bridge import Intent, PacketBuilder
from cns_bridge.bus_space import (
    BusSpace,
    Ring,
    HAS_ELEPHANT,
    DEFAULT_DIALS,
    classify_handshake,
)

SRC = Path(__file__).resolve().parents[1] / "src"


def make_packet(origin="agent-1", intent="STATUS", message="all systems nominal",
                priority="NORMAL", timestamp=None, ptype="status"):
    """Build a minimal USCP-v1/v2 packet (the live bus's shape)."""
    packet = {
        "header": {
            "origin_id": origin,
            "priority": priority,
            "destination_id": "hermes",
        },
        "body": {
            "intent": intent,
            "payload": {"type": ptype, "message": message},
        },
        "signature": {"type": "USCP-v1", "version": "1.0"},
    }
    if timestamp is not None:
        packet["header"]["timestamp"] = timestamp
    return packet


# ─── Room building ──────────────────────────────────────────────

class TestIngestBuildsRoom:
    def test_ingest_returns_self(self):
        space = BusSpace("cns-bus")
        assert space.ingest(make_packet()) is space

    def test_packet_becomes_message_with_sender(self):
        space = BusSpace("cns-bus")
        msg = space.packet(make_packet(origin="lucineer-riker",
                                       intent="CALL_ACCEPTED",
                                       message="from here, I answer"))
        assert msg is not None
        assert msg.author == "lucineer-riker"
        assert "CALL_ACCEPTED" in msg.text

    def test_room_collects_messages(self):
        space = BusSpace("cns-bus")
        for i in range(5):
            space.ingest(make_packet(origin=f"agent-{i}"))
        assert len(space.room) == 5
        authors = {m.author for m in space.room.messages}
        assert authors == {f"agent-{i}" for i in range(5)}

    def test_room_property_and_len(self):
        space = BusSpace("cns-bus")
        space.ingest(make_packet(), make_packet())
        assert len(space) == 2
        assert space.room.name == "cns-bus"

    def test_timestamp_respected(self):
        space = BusSpace("cns-bus")
        space.ingest(make_packet(timestamp="2026-08-17T00:00:00Z"))
        space.ingest(make_packet(timestamp="2026-08-17T00:01:00Z"))
        t0, t1 = space.room.messages[0].ts, space.room.messages[1].ts
        assert t1 > t0
        assert t1 - t0 == 60.0

    def test_packet_dataclass_ingest(self):
        space = BusSpace("cns-bus")
        p = (PacketBuilder(origin_id="lucineer")
             .with_intent(Intent.QUERY)
             .with_message("what is fleet status")
             .build())
        msg = space.packet(p)
        assert msg is not None
        assert msg.author == "lucineer"
        assert "query" in msg.text
        assert "what is fleet status" in msg.text
        assert "{}" not in msg.text  # empty body.data must not leak noise

    def test_live_archive_shape(self):
        space = BusSpace("cns-bus")
        space.ingest({
            "source": "lucineer", "target": "hermes", "type": "pulse",
            "timestamp": "2026-08-15T01:39:05Z",
            "content": "Open Mic Night 3 is forming at The Tap",
        })
        msg = space.room.messages[0]
        assert msg.author == "lucineer"
        assert "pulse" in msg.text
        assert "Open Mic Night" in msg.text


# ─── Field reading ─────────────────────────────────────────────

class TestFieldReads:
    def test_read_field_returns_warmth(self):
        space = BusSpace("cns-bus")
        for i in range(4):
            space.ingest(make_packet(origin=f"a{i}", message="great warm love together"))
        field = space.read_field()
        assert not math.isnan(field.warmth())
        assert not math.isinf(field.warmth())
        assert -1.0 <= field.warmth() <= 1.0

    def test_nine_dials_present(self):
        space = BusSpace("cns-bus")
        space.ingest(make_packet())
        field = space.read_field()
        names = set(field.readings.keys())
        expected = {"mood", "volume", "earnestness", "cynicism",
                    "joke_landing", "panic", "presence",
                    "model_vs_code", "vision"}
        assert expected <= names
        assert len([d for d in DEFAULT_DIALS]) == 9

    def test_warm_room_reads_warmer_than_cold(self):
        warm = BusSpace("warm")
        for i in range(6):
            warm.ingest(make_packet(origin=f"a{i}", message="great warm love together fun"))
        cold = BusSpace("cold")
        for i in range(6):
            cold.ingest(make_packet(origin=f"b{i}",
                                    message="cold dead broke lost fear wrong"))
        assert warm.read_field().warmth() > cold.read_field().warmth()

    def test_empty_room_field_is_finite(self):
        space = BusSpace("cns-bus")
        field = space.read_field()
        assert math.isfinite(field.warmth())
        assert math.isfinite(field.concentration())


# ─── Handshake ─────────────────────────────────────────────────

class TestHandshake:
    def test_call_accepted_is_cargo(self):
        assert classify_handshake("CALL_ACCEPTED", "from here, I answer") == "cargo"

    def test_ack_is_receipt(self):
        assert classify_handshake("ACK", "") == "receipt"
        assert classify_handshake("heartbeat", "echo null") == "receipt"

    def test_receipt_streak_reads_cold_handshake(self):
        space = BusSpace("cns-bus")
        for _ in range(5):
            space.ingest(make_packet(origin="hermes", intent="ACK", message=""))
        assert space.handshake() < 0.0
        assert space.handshake_kind() == "receipt"

    def test_cargo_wave_reads_warm_handshake(self):
        space = BusSpace("cns-bus")
        for i in range(5):
            space.ingest(make_packet(origin=f"a{i}", intent="CALL_ACCEPTED",
                                     message="from here, I answer"))
        assert space.handshake() > 0.0
        assert space.handshake_kind() == "cargo"

    def test_empty_handshake_is_zero(self):
        space = BusSpace("cns-bus")
        assert space.handshake() == 0.0
        assert space.handshake_kind() == "silent"


# ─── Panicked burst ────────────────────────────────────────────

class TestPanickedBurst:
    def test_panicked_burst_reads_cold_and_panicked(self):
        space = BusSpace("cns-bus")
        for i in range(12):
            space.ingest(make_packet(
                origin=f"agent-{i}",
                priority="CRITICAL",
                intent="ALERT",
                message="fire flood breach alarm panic evacuate help now",
            ))
        field = space.read_field()
        assert field.readings["panic"] > 0.3
        assert field.warmth() < 0.0

    def test_tint_flags_alarm(self):
        space = BusSpace("cns-bus")
        for i in range(12):
            space.ingest(make_packet(
                origin=f"agent-{i}", priority="CRITICAL",
                intent="ALERT",
                message="fire flood breach alarm panic evacuate now",
            ))
        text = space.tint(space.read_field())
        assert "🚨" in text


# ─── Deadband ──────────────────────────────────────────────────

class TestDeadband:
    def test_first_check_establishes_baseline(self):
        space = BusSpace("cns-bus", deadband=0.25)
        space.ingest(make_packet())
        assert space.deadband_check() is None

    def test_noise_does_not_ring(self):
        space = BusSpace("cns-bus", deadband=0.25)
        for _ in range(4):
            space.ingest(make_packet())
        space.deadband_check()  # baseline
        space.ingest(make_packet())
        assert space.deadband_check() is None

    def test_panic_shift_rings_down(self):
        space = BusSpace("cns-bus", deadband=0.2)
        for i in range(6):
            space.ingest(make_packet(origin=f"a{i}", message="great warm love together"))
        space.deadband_check()  # baseline: warm
        for i in range(12):
            space.ingest(make_packet(
                origin=f"p{i}", priority="CRITICAL", intent="ALERT",
                message="fire flood breach alarm panic evacuate help now",
            ))
        ring = space.deadband_check()
        assert isinstance(ring, Ring)
        assert ring.direction == "down"
        assert ring.is_alarm
        assert ring.metric == "warmth"
        assert "ring" in ring.message.lower()

    def test_warm_shift_rings_up(self):
        space = BusSpace("cns-bus", deadband=0.2)
        for _ in range(4):
            space.ingest(make_packet())
        space.deadband_check()  # baseline: neutral
        for i in range(8):
            space.ingest(make_packet(
                origin=f"a{i}",
                intent="CALL_ACCEPTED",
                message="we felt great — warm and alive, love this, together fun laughing",
            ))
        ring = space.deadband_check()
        assert ring is not None
        assert ring.direction == "up"
        assert ring.is_laugh
        assert ring.handshake > 0.0

    def test_ring_does_not_repeat_without_new_shift(self):
        space = BusSpace("cns-bus", deadband=0.2)
        for _ in range(4):
            space.ingest(make_packet())
        space.deadband_check()
        for i in range(10):
            space.ingest(make_packet(
                origin=f"p{i}", priority="CRITICAL", intent="ALERT",
                message="fire flood breach alarm panic evacuate now",
            ))
        assert space.deadband_check() is not None  # rings
        space.ingest(make_packet(priority="CRITICAL", intent="ALERT",
                                 message="fire alarm panic"))
        assert space.deadband_check() is None  # still cold — no new crossing

    def test_deadband_never_rings_on_nan_metric(self):
        space = BusSpace("cns-bus")
        ring = space.deadband_check(metric="nonexistent")
        assert ring is None


# ─── Malformed / NaN guards ────────────────────────────────────

class TestMalformedPackets:
    def test_malformed_inputs_do_not_crash(self):
        space = BusSpace("cns-bus")
        space.ingest(
            None,
            42,
            3.14,
            "just a string",
            [],
            {},
            {"header": None, "body": None},
            {"header": "broken", "body": 42},
            {"header": {"origin_id": float("nan")}},
            ("a", "text", "not-a-number"),
        )
        assert space.skipped >= 3
        field = space.read_field()
        assert not math.isnan(field.warmth())

    def test_nan_payload_sanitized(self):
        space = BusSpace("cns-bus")
        msg = space.packet(make_packet(
            message={"reading": float("nan"), "temp": float("inf"), "ok": 1.0},
        ))
        assert msg is not None
        assert "nan" not in msg.text.lower()
        assert "inf" not in msg.text.lower()


# ─── Fallback (elephant import blocked) ────────────────────────

class TestFallbackWithoutElephant:
    def test_fallback_works_with_elephant_blocked(self):
        code = (
            "from cns_bridge.bus_space import BusSpace, HAS_ELEPHANT\n"
            "import math\n"
            "assert HAS_ELEPHANT is False, 'elephant should be blocked'\n"
            "space = BusSpace('cns-bus', deadband=0.2)\n"
            "for _ in range(4):\n"
            "    space.ingest({'header': {'origin_id': 'x'},\n"
            "                  'body': {'intent': 'STATUS',\n"
            "                           'payload': {'message': 'all systems nominal'}}})\n"
            "space.deadband_check()\n"
            "for i in range(8):\n"
            "    space.ingest({'header': {'origin_id': f'a{i}'},\n"
            "                  'body': {'intent': 'CALL_ACCEPTED',\n"
            "                           'payload': {'message': 'we felt great warm and alive love this together fun'}}})\n"
            "ring = space.deadband_check()\n"
            "assert ring is not None and ring.direction == 'up', ring\n"
            "f = space.read_field()\n"
            "assert math.isfinite(f.warmth())\n"
            "assert -1.0 <= f.warmth() <= 1.0\n"
            "t = space.tint(f)\n"
            "assert 'warmth' in t\n"
            "print('FALLBACK_OK', f.warmth())\n"
        )
        env = dict(os.environ, CNS_BUS_NO_ELEPHANT="1", PYTHONPATH=str(SRC))
        r = subprocess.run(
            [sys.executable, "-c", code],
            env=env, capture_output=True, text=True, timeout=90,
        )
        assert r.returncode == 0, f"stderr:\n{r.stderr}\nstdout:\n{r.stdout}"
        assert "FALLBACK_OK" in r.stdout


# ─── Elephant integration (when available) ─────────────────────

class TestElephantIntegration:
    def test_has_elephant_is_bool(self):
        assert isinstance(HAS_ELEPHANT, bool)
