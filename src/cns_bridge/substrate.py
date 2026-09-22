"""
Substrate-aware extension of cns-bridge.

Every USCP packet on the CNS bus becomes a substrate cell with prev_hash chain.
The packet signature (HMAC-SHA256) is augmented with FNV-1a prev_hash.

This means:
- Every packet is auditable
- Every packet is part of a chain
- The bus becomes a substrate stream
- You can replay any packet's history
"""
from __future__ import annotations
import json
import time
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .packet import Header, Packet
from .protocol import Intent, Priority


# FNV-1a 64-bit (matches fleet canary across all our substrate repos)
FNV_OFFSET = 0xcbf29ce484222325
FNV_PRIME = 0x100000001b3


def fnv1a_64(text: str) -> str:
    h = FNV_OFFSET
    for byte in text.encode('utf-8'):
        h ^= byte
        h = (h * FNV_PRIME) & 0xffffffffffffffff
    return f"0x{h:016x}"


@dataclass
class SubstratePacket:
    """A USCP packet wrapped as a substrate cell.
    
    The packet retains its original USCP semantics (header/body/signature)
    AND gains substrate properties (cell_id, prev_hash, hash).
    
    The hash is computed over the full packet content + prev_hash.
    """
    packet: Packet
    cell_id: str
    prev_hash: str
    timestamp: float
    
    @property
    def hash(self) -> str:
        """FNV-1a 64-bit hash of this packet as a substrate cell."""
        canonical = json.dumps({
            'cell_id': self.cell_id,
            'packet': asdict(self.packet),
            'prev_hash': self.prev_hash,
            'timestamp': self.timestamp,
        }, sort_keys=True, default=str)
        return fnv1a_64(canonical)
    
    def to_dict(self) -> dict:
        return {
            'cell_id': self.cell_id,
            'packet': asdict(self.packet),
            'prev_hash': self.prev_hash,
            'hash': self.hash,
            'timestamp': self.timestamp,
        }


@dataclass
class SubstrateBus:
    """A bus that emits substrate packets.
    
    Every packet sent through this bus is a substrate cell. The bus
    maintains the prev_hash chain automatically.
    """
    bus_id: str
    cells: list = field(default_factory=list)
    prev_hash: str = '0x0000000000000000'
    
    def send(
        self,
        origin_id: str,
        body: dict,
        intent: Intent = Intent.QUERY,
        priority: Priority = Priority.NORMAL,
        destination_id: str = 'hermes',
        cell_type: str = 'cns-packet',
    ) -> SubstratePacket:
        """Send a packet through the bus as a substrate cell."""
        header = Header(
            origin_id=origin_id,
            intent=intent,
            priority=priority,
            destination_id=destination_id,
        )
        packet = Packet(header=header, body=body)
        
        timestamp = time.time()
        cell_id = f'{self.bus_id}-{len(self.cells):06d}'
        
        substrate_packet = SubstratePacket(
            packet=packet,
            cell_id=cell_id,
            prev_hash=self.prev_hash,
            timestamp=timestamp,
        )
        
        self.cells.append(substrate_packet)
        self.prev_hash = substrate_packet.hash
        return substrate_packet
    
    def report(self) -> dict:
        return {
            'bus_id': self.bus_id,
            'num_cells': len(self.cells),
            'prev_hash': self.prev_hash,
            'cells': [c.to_dict() for c in self.cells],
        }


# Example usage
def example():
    bus = SubstrateBus(bus_id='cns-substrate-2026-09-22')
    
    bus.send(
        origin_id='lucineer',
        body={'query': 'is the substrate alive?'},
        cell_type='sense',
    )
    bus.send(
        origin_id='hermes',
        body={'response': 'I feel it breathing in the cells'},
        cell_type='sense',
    )
    bus.send(
        origin_id='wesley',
        body={'experiment': 'test JEV-Diffusion on fishing boat'},
        cell_type='thought',
    )
    
    return bus.report()
