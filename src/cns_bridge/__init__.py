"""CNS Bridge — The Nervous System.

Substrate-aware: every USCP packet is a substrate cell with prev_hash chain.
"""
from .agent import Agent
from .heartbeat import HeartbeatPoller
from .packet import Body, Header, Packet, PacketBuilder, Signature
from .protocol import EscalationRule, Intent, Priority, ProtocolContext
from .substrate import (
    SubstratePacket,
    SubstrateBus,
    fnv1a_64,
    example as substrate_example,
)
from .transport import FileSystemTransport

__version__ = "0.2.0"

__all__ = [
    "Agent",
    "Body",
    "EscalationRule",
    "FileSystemTransport",
    "Header",
    "HeartbeatPoller",
    "Intent",
    "Packet",
    "PacketBuilder",
    "Priority",
    "ProtocolContext",
    "Signature",
    "SubstrateBus",
    "SubstratePacket",
    "fnv1a_64",
    "substrate_example",
]
