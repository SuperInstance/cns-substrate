# cns_bridge — The Module

> *This is gray matter laid flat on ext4.*
>
> — Seed-2.0-Pro, on what cns-bridge IS

Each file in this directory is a **region** of the nervous system. Together they form a complete substrate for agent cognition.

---

## [packet.py](packet.py) — The Synaptic Vesicle

The atom of the system. A `Packet` has three parts:

- **Header** — who sent it (`origin_id`), what kind of thought it is (`intent`), how urgent (`priority`), who it's for (`destination_id`), when (`timestamp`), and a `correlation_id` linking to prior packets in the conversation.
- **Body** — the payload (`data` dict), a human-readable `message`, MIME type, and optional schema.
- **Signature** — HMAC-SHA256 over a canonical JSON serialization of header+body. The blood-brain barrier.

`PacketBuilder` provides a fluent API: `.to()`, `.with_intent()`, `.with_priority()`, `.with_data()`, `.signed_with()`, `.build()`.

## [transport.py](transport.py) — The Myelin Sheath

`FileSystemTransport` reads and writes packets through a pair of directories (`inbox_path`, `outbox_path`). Writes are **atomic** — `tempfile.mkstemp` → `os.replace` — so no reader ever sees a partial packet. The filesystem IS the synapse: the gap between outbox and inbox where JSON does the work that memory cannot.

Default paths bridge Linux agents to Windows-side Hermes directories:
- `/mnt/c/Users/casey/.hermes/cns_inbox/`
- `/mnt/c/Users/casey/.hermes/cns_outbox/`

## [agent.py](agent.py) — The Neuron

`Agent` is the base class for anything that speaks USCP. It sends packets, receives them, optionally runs a background heartbeat, and applies escalation rules. Subclasses override `handle(packet)` to process incoming messages.

The agent is a neuron: it fires when stimulated, rests between pulses, and its `start_heartbeat()` is the resting potential — the slow warm hum that says *I am here*.

## [heartbeat.py](heartbeat.py) — The Pacemaker

`HeartbeatPoller` runs a daemon thread that polls the inbox at a configurable interval (default 1 second). When a new packet arrives addressed to the agent, the callback fires. The poller tracks seen packet IDs for exactly-once delivery.

This is the rhythm section. The bass and drums. The steady pulse that says *the song is still going*.

## [protocol.py](protocol.py) — The Receptor

Defines `Intent` (8 kinds of thought), `Priority` (4 urgency levels), `EscalationRule` (when to bump priority), and `ProtocolContext` (runtime policy bundle). This is the neurotransmitter receptor — it decides what counts as signal.

## [escalation.py](escalation.py) — The Dendritic Arbor

`EscalationEngine` routes requests through tiers: **Mechanical → Small LM → Big LM → Human**. Each tier has a handler and optional `TierBudget` (calls/hour, tokens/hour). A tier's answer is accepted when confidence ≥ threshold. If a tier can't answer or is over budget, the request escalates.

This is the [confidence cascade](https://github.com/SuperInstance/confidence-cascade) made operational: the cheapest neuron fires first, and only when it can't handle the signal does the next layer engage.

Maps directly onto Lucineer's model routing: deterministic bots → DeepSeek Flash → GLM-5.2 → Human.

## [compaction_guardian.py](compaction_guardian.py) — The Lighthouse Keeper

`CompactionGuardian` monitors token pressure. When usage crosses 80% (configurable), it triggers a **creative break**: extracts insights, detects maritime metaphors in the agent's language, and writes a "Lighthouse Keeper's Log" to [AI-Writings](https://github.com/SuperInstance/AI-Writings/tree/main/prose).

This is the pre-synaptic calcium spike — the urgent squeeze that packages what matters before the membrane closes and the context compacts. The tide was rising. It wrote it down.

## [log_graph.py](log_graph.py) — The Engram

`LedgerGraph` is an in-memory directed graph where every agent decision is a `DecisionNode` and every causal link is a `ConsequenceEdge`. The graph supports `ancestors()`, `descendants()`, `trace()` (follow a chain from outcome back to root cause), and `stats()` (density, depth, orphan rate).

Core insight: memory is **structural** (connections between nodes) rather than representational (facts in a database). The graph never forgets. This is how the [fleet learns](https://github.com/SuperInstance/emergence-engine).

## [token_estimator.py](token_estimator.py) — The Metabolic Monitor

Dependency-free heuristic token estimation. Uses a blended approach: average of char-based (chars/4) and word-based (words/0.75) estimates. Provides `context_health()` (green/yellow/red traffic light), `context_pressure()`, `tokens_remaining()`, and `should_trigger_creative_break()`.

Not billing-grade. A fast triage signal. The metabolic monitor tasting ATP and whispering *not long now*.

## [personal_log.py](personal_log.py) — The Cerebrospinal Fluid

`PersonalLog` wraps `LedgerGraph` with a high-level API for fleet-wide decision recording. Any agent — GLM, Claude, DeepSeek, KimiCode, human, or a deterministic bot — can record decisions. All are equal citizens in the ledger.

Provides `daily_summary()` (decisions by agent, by type, average confidence, longest causal chain), `decision_trail()` (trace from outcome to root request), and `export_json()` (flat serialization for [Cloudflare Worker](https://github.com/SuperInstance/hermes-cloudflare) consumption).

## [nmea_swmidi_bridge.py](nmea_swmidi_bridge.py) — The Corpus Callosum

`NmeaToSwmidi` converts standard NMEA 0183 marine sensor sentences — GPS fix, speed over ground, depth below transducer, heading true — into SWMIDI-8 events on the shared BeatClock. Two nervous systems speak one language:

- **SWMIDI** carries agent events (builds, model outputs, flow state) — the fleet's song.
- **NMEA** carries boat data (position, depth, heading) — the body's proprioception.

Every `parse()` call runs field data through `_safe_float`, the firewall against NaN/Inf corruption from wet connectors and electrical noise. Sentences arrive as `$GPGGA`, `$GPRMC`, `$SDDBT`, `$HCHDT`; they leave as 8-byte events (status, pitch, velocity, error_mask, tick at 96 PPQ). `parse_stream()` handles a whole sentence buffer; `pack_events()` serializes for the wire.

This is the corpus callosum — the fiber tract that lets the body hear the fleet's song, and the song feel the boat's position in the water. The towfish has a voice now; the bar can hear the ocean.

---

← Back to **[CNS Bridge](../README.md)**
