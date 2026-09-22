"""Base Agent class for participating in the Hermes CNS bus."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .heartbeat import HeartbeatPoller, PacketHandler
from .packet import Packet, PacketBuilder
from .protocol import Intent, Priority, ProtocolContext
from .transport import FileSystemTransport


class Agent:
    """Base class for any agent that sends and receives USCP packets.

    Subclasses typically override ``handle`` to process incoming packets and
    may call ``start_heartbeat`` to receive responses asynchronously.

    Args:
        agent_id: Unique identity used as ``origin_id`` in outgoing packets.
        transport: FileSystemTransport instance.
        context: Optional protocol policy bundle.
        secret: Optional shared secret used to sign outgoing packets and
            verify incoming ones.
    """

    def __init__(
        self,
        agent_id: str,
        transport: FileSystemTransport,
        context: ProtocolContext | None = None,
        secret: str | bytes | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.transport = transport
        self.context = context or ProtocolContext()
        self.secret = secret
        self._poller: HeartbeatPoller | None = None
        self._response_handlers: list[PacketHandler] = []

    def builder(self) -> PacketBuilder:
        """Return a fresh PacketBuilder seeded with this agent's identity."""
        return PacketBuilder(origin_id=self.agent_id)

    def send(
        self,
        intent: Intent | None = None,
        data: dict[str, Any] | None = None,
        message: str = "",
        priority: Priority | None = None,
        destination_id: str = "hermes",
        correlation_id: str | None = None,
        schema: str | None = None,
    ) -> Packet:
        """Build, optionally sign, and send a packet to the CNS outbox."""
        intent = intent or self.context.default_intent
        priority = priority or self.context.default_priority

        builder = (
            self.builder()
            .to(destination_id)
            .with_intent(intent)
            .with_priority(priority)
        )
        if data:
            builder.with_data(**data)
        if message:
            builder.with_message(message)
        if correlation_id:
            builder.with_correlation_id(correlation_id)
        if schema:
            builder.with_schema(schema)
        if self.secret is not None:
            builder.signed_with(self.secret, key_id=self.agent_id)

        packet = builder.build()
        self.transport.send(packet)
        return packet

    def receive(self) -> Packet | None:
        """Read and remove the next packet addressed to this agent."""
        packet = self.transport.receive(origin_id=None)
        if packet is None:
            return None
        if self.secret is not None and packet.signature.value:
            packet.verify(self.secret)
        return packet

    def handle(self, packet: Packet) -> Any:
        """Process a packet. Subclasses should override this method."""
        raise NotImplementedError(
            f"Agent {self.agent_id} does not implement handle(packet)"
        )

    def on_response(self, handler: PacketHandler) -> PacketHandler:
        """Register a callback for responses delivered by the heartbeat poller.

        Can be used as a decorator.
        """
        self._response_handlers.append(handler)
        return handler

    def _dispatch_response(self, packet: Packet) -> None:
        for handler in self._response_handlers:
            handler(packet)
        try:
            self.handle(packet)
        except NotImplementedError:
            pass

    def start_heartbeat(self, interval: float = 1.0) -> HeartbeatPoller:
        """Start a background thread that watches the inbox for responses."""
        self._poller = HeartbeatPoller(
            transport=self.transport,
            agent_id=self.agent_id,
            callback=self._dispatch_response,
            interval=interval,
            filter_origin=False,
        )
        self._poller.start()
        return self._poller

    def stop_heartbeat(self, timeout: float | None = None) -> None:
        """Stop the background heartbeat poller if it is running."""
        if self._poller is not None:
            self._poller.stop(timeout)
            self._poller = None

    def escalate_if_needed(
        self, packet: Packet, has_response: bool = False
    ) -> Priority:
        """Apply protocol escalation rules to a previously sent packet."""
        sent_at = datetime.fromisoformat(packet.header.timestamp)
        return self.context.check_escalation(
            sent_at=sent_at,
            priority=packet.header.priority,
            has_response=has_response,
        )
