"""Tests for USCP packet building, parsing, and signatures."""

import json

import pytest

from cns_bridge import Body, Header, Intent, Packet, PacketBuilder, Priority, Signature


def test_header_roundtrip() -> None:
    header = Header(
        origin_id="lucineer",
        intent=Intent.QUERY,
        priority=Priority.HIGH,
        destination_id="hermes",
        correlation_id="abc-123",
    )
    restored = Header.from_dict(header.to_dict())
    assert restored.origin_id == header.origin_id
    assert restored.intent == header.intent
    assert restored.priority == header.priority
    assert restored.destination_id == header.destination_id
    assert restored.correlation_id == header.correlation_id
    assert restored.packet_id


def test_body_roundtrip() -> None:
    body = Body(data={"score": 0.94}, message="ok", schema="test/v1")
    restored = Body.from_dict(body.to_dict())
    assert restored.data == body.data
    assert restored.message == body.message
    assert restored.schema == body.schema


def test_signature_roundtrip() -> None:
    sig = Signature(value="xyz", algorithm="HMAC-SHA256", key_id="hermes")
    restored = Signature.from_dict(sig.to_dict())
    assert restored.value == sig.value
    assert restored.algorithm == sig.algorithm
    assert restored.key_id == sig.key_id


def test_packet_serialization() -> None:
    packet = (
        PacketBuilder(origin_id="wesley")
        .to("hermes")
        .with_intent(Intent.SENSE)
        .with_priority(Priority.NORMAL)
        .with_data(score=0.94)
        .with_message("report")
        .build()
    )
    raw = packet.to_json()
    data = json.loads(raw)
    assert data["header"]["origin_id"] == "wesley"
    assert data["header"]["destination_id"] == "hermes"
    assert data["header"]["intent"] == "sense"
    assert data["body"]["data"]["score"] == 0.94


def test_packet_roundtrip() -> None:
    packet = (
        PacketBuilder(origin_id="lucineer")
        .to("hermes")
        .with_intent(Intent.QUERY)
        .with_data(question="status")
        .build()
    )
    restored = Packet.from_json(packet.to_json())
    assert restored.header.origin_id == "lucineer"
    assert restored.body.data["question"] == "status"


def test_sign_and_verify() -> None:
    packet = (
        PacketBuilder(origin_id="lucineer")
        .with_intent(Intent.COMMAND)
        .with_data(action="ping")
        .signed_with("shared-secret")
        .build()
    )
    assert packet.signature.value
    assert packet.verify("shared-secret")


def test_verify_fails_with_wrong_secret() -> None:
    packet = (
        PacketBuilder(origin_id="lucineer")
        .with_data(action="ping")
        .signed_with("right-secret")
        .build()
    )
    assert not packet.verify("wrong-secret")
    assert packet.signature.verified is False


def test_verify_empty_signature() -> None:
    packet = PacketBuilder(origin_id="lucineer").build()
    assert not packet.verify("any-secret")


def test_packet_from_json_missing_signature() -> None:
    raw = json.dumps(
        {
            "header": {"origin_id": "x"},
            "body": {"data": {}},
        }
    )
    packet = Packet.from_json(raw)
    assert packet.signature.value == ""


def test_malformed_json_raises() -> None:
    with pytest.raises(json.JSONDecodeError):
        Packet.from_json("not json")


def test_packet_from_dict_missing_header_raises() -> None:
    with pytest.raises(KeyError):
        Packet.from_dict({"body": {"data": {}}})
