# Source — The Nervous Tissue

This directory contains the Python implementation of CNS Bridge.

## Structure

```
src/
└── cns_bridge/
    ├── __init__.py           — Public API surface (29 exports)
    ├── packet.py             — USCP packet model + PacketBuilder
    ├── transport.py          — FileSystemTransport (atomic inbox/outbox)
    ├── agent.py              — Agent base class (send/receive/heartbeat)
    ├── heartbeat.py          — HeartbeatPoller (background inbox watcher)
    ├── protocol.py           — Intent, Priority, EscalationRule, ProtocolContext
    ├── escalation.py         — EscalationEngine (Mechanical→SmallLM→BigLM→Human)
    ├── compaction_guardian.py — Token pressure monitor + creative break capture
    ├── log_graph.py          — LedgerGraph (decision-consequence DAG)
    ├── token_estimator.py    — Heuristic token counting + context health
    ├── personal_log.py       — PersonalLog (fleet memory layer)
    └── nmea_swmidi_bridge.py — NMEA 0183 → SWMIDI-8 bridge (boat data to BeatClock)
```

→ See **[cns_bridge/](cns_bridge/)** for the module reference.

← Back to **[CNS Bridge](../README.md)**
