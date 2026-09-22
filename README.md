# cns-bridge (substrate edition)

> **The CNS bus, now with substrate cells.** Every USCP packet is a substrate cell with prev_hash chain.

## What's new vs the original

| Original | Substrate Edition |
|---|---|
| HMAC-SHA256 signatures only | HMAC-SHA256 + FNV-1a prev_hash |
| Independent packets | Chained cells |
| File-based bus | File-based bus + substrate stream |
| Static audit log | Replayable history |

## The substrate pattern

Every packet sent through the bus is wrapped as a `SubstratePacket`:
- `cell_id`: unique identifier
- `prev_hash`: chain link to previous cell
- `hash`: FNV-1a 64-bit of packet + prev_hash
- `timestamp`: when sent

The bus maintains the chain automatically. Every packet is auditable.

## Quick Start

```python
from cns_bridge.substrate import SubstrateBus
from cns_bridge.protocol import Intent, Priority

bus = SubstrateBus(bus_id='my-bus')

bus.send(
    origin_id='my-agent',
    body={'query': 'is the substrate alive?'},
    intent=Intent.QUERY,
)

# Inspect the chain
print(bus.report())
```

## Tests

```bash
python3 -m unittest tests.test_substrate  # 6/6 pass
```

## See also

- [SuperInstance/cns-bridge](https://github.com/SuperInstance/cns-bridge) — original
- [SuperInstance/jev-diffusion](https://github.com/SuperInstance/jev-diffusion) — substrate segmentation
- [SuperInstance/quilt-cowboy](https://github.com/SuperInstance/quilt-cowboy) — Quilt's rider
