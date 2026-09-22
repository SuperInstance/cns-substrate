"""CNS Bridge — The Nervous System.

Substrate-aware: every USCP packet is a substrate cell with prev_hash chain.
"""
from .packet import Header, Packet, PacketBuilder
from .protocol import Intent, Priority
from .substrate import (
    SubstratePacket,
    SubstrateBus,
    fnv1a_64,
    example as substrate_example,
)

__version__ = "0.2.0"
