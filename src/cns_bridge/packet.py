"""USCP packet builder, parser, and signature handling.

A USCP packet is a JSON object with three top-level keys:

    {
        "header": {...},
        "body": {...},
        "signature": {...}
    }

The header introduces the sender. The body carries the need. The signature
promotes integrity. See README.md for the full protocol specification.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .protocol import Intent, Priority


DEFAULT_ALGORITHM = "HMAC-SHA256"
USCP_VERSION = "1.0"


@dataclass
class Header:
    """Introduction metadata for a CNS packet.

    Attributes:
        origin_id: Unique identity of the sending agent.
        packet_id: UUID for this specific packet; auto-generated if omitted.
        intent: What kind of message this is.
        priority: Urgency level.
        destination_id: Optional target agent or "hermes" for the CNS.
        timestamp: ISO-8601 UTC timestamp; auto-generated if omitted.
        version: USCP protocol version.
        correlation_id: Optional ID linking this packet to a previous one.
    """

    origin_id: str
    packet_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    intent: Intent = Intent.QUERY
    priority: Priority = Priority.NORMAL
    destination_id: str = "hermes"
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    version: str = USCP_VERSION
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin_id": self.origin_id,
            "packet_id": self.packet_id,
            "intent": self.intent.value,
            "priority": self.priority.value,
            "destination_id": self.destination_id,
            "timestamp": self.timestamp,
            "version": self.version,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Header":
        return cls(
            origin_id=data["origin_id"],
            packet_id=data.get("packet_id", str(uuid.uuid4())),
            intent=Intent(data.get("intent", Intent.QUERY.value)),
            priority=Priority(data.get("priority", Priority.NORMAL.value)),
            destination_id=data.get("destination_id", "hermes"),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            version=data.get("version", USCP_VERSION),
            correlation_id=data.get("correlation_id"),
        )


@dataclass
class Body:
    """Payload container for a CNS packet.

    The body is intentionally permissive: the only required field is ``data``.
    Everything else is optional context that agents may attach.
    """

    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    mime_type: str = "application/json"
    encoding: str = "utf-8"
    schema: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "message": self.message,
            "mime_type": self.mime_type,
            "encoding": self.encoding,
            "schema": self.schema,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Body":
        return cls(
            data=data.get("data", {}),
            message=data.get("message", ""),
            mime_type=data.get("mime_type", "application/json"),
            encoding=data.get("encoding", "utf-8"),
            schema=data.get("schema"),
        )


@dataclass
class Signature:
    """Integrity metadata for a CNS packet.

    The signature object records how the packet was signed, but it does not
    contain the raw secret. Verification is performed with a shared secret.
    """

    value: str = ""
    algorithm: str = DEFAULT_ALGORITHM
    key_id: str = "default"
    verified: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Signature":
        return cls(
            value=data.get("value", ""),
            algorithm=data.get("algorithm", DEFAULT_ALGORITHM),
            key_id=data.get("key_id", "default"),
            verified=data.get("verified"),
        )


@dataclass
class Packet:
    """A complete USCP packet: header, body, and signature."""

    header: Header
    body: Body
    signature: Signature = field(default_factory=Signature)

    def to_dict(self) -> dict[str, Any]:
        return {
            "header": self.header.to_dict(),
            "body": self.body.to_dict(),
            "signature": self.signature.to_dict(),
        }

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Packet":
        return cls(
            header=Header.from_dict(data["header"]),
            body=Body.from_dict(data["body"]),
            signature=Signature.from_dict(data.get("signature", {})),
        )

    @classmethod
    def from_json(cls, raw: str) -> "Packet":
        return cls.from_dict(json.loads(raw))

    def signing_payload(self) -> bytes:
        """Return the canonical bytes that the signature covers.

        The signature covers the header and body only, ordered deterministically.
        """
        canonical = {
            "header": self.header.to_dict(),
            "body": self.body.to_dict(),
        }
        return json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

    def sign(self, secret: str | bytes, key_id: str = "default") -> "Packet":
        """Sign the packet in place and return self."""
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        self.signature.key_id = key_id
        self.signature.algorithm = DEFAULT_ALGORITHM
        self.signature.value = base64.b64encode(
            hmac.new(secret, self.signing_payload(), hashlib.sha256).digest()
        ).decode("ascii")
        self.signature.verified = True
        return self

    def verify(self, secret: str | bytes) -> bool:
        """Verify the packet signature against a shared secret."""
        if not self.signature.value:
            return False
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        expected = base64.b64encode(
            hmac.new(secret, self.signing_payload(), hashlib.sha256).digest()
        ).decode("ascii")
        is_valid = hmac.compare_digest(expected, self.signature.value)
        self.signature.verified = is_valid
        return is_valid


class PacketBuilder:
    """Fluent builder for USCP packets."""

    def __init__(self, origin_id: str) -> None:
        self._header = Header(origin_id=origin_id)
        self._body = Body()
        self._secret: str | bytes | None = None
        self._key_id: str = "default"

    def to(self, destination_id: str) -> "PacketBuilder":
        self._header.destination_id = destination_id
        return self

    def with_intent(self, intent: Intent) -> "PacketBuilder":
        self._header.intent = intent
        return self

    def with_priority(self, priority: Priority) -> "PacketBuilder":
        self._header.priority = priority
        return self

    def with_packet_id(self, packet_id: str) -> "PacketBuilder":
        self._header.packet_id = packet_id
        return self

    def with_correlation_id(self, correlation_id: str | None) -> "PacketBuilder":
        self._header.correlation_id = correlation_id
        return self

    def with_data(self, **kwargs: Any) -> "PacketBuilder":
        self._body.data.update(kwargs)
        return self

    def with_message(self, message: str) -> "PacketBuilder":
        self._body.message = message
        return self

    def with_schema(self, schema: str) -> "PacketBuilder":
        self._body.schema = schema
        return self

    def signed_with(self, secret: str | bytes, key_id: str = "default") -> "PacketBuilder":
        self._secret = secret
        self._key_id = key_id
        return self

    def build(self) -> Packet:
        packet = Packet(header=self._header, body=self._body)
        if self._secret is not None:
            packet.sign(self._secret, self._key_id)
        return packet
