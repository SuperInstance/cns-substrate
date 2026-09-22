# Tests — Sea Trials

270 tests across 12 modules. Every component of the nervous system is exercised.

## Module Map

| File | Tests | What it verifies |
|------|-------|-----------------|
| [test_packet.py](test_packet.py) | Packet construction, serialization, signing, verification, edge cases |
| [test_transport.py](test_transport.py) | Atomic file writes, inbox/outbox round-trips, partial reads |
| [test_agent.py](test_agent.py) | Agent send/receive, heartbeat lifecycle, escalation |
| [test_heartbeat.py](test_heartbeat.py) | Background polling, deduplication, stop/start cycles |
| [test_protocol.py](test_protocol.py) | Intent/Priority enums, escalation rule evaluation |
| [test_escalation.py](test_escalation.py) | Tiered handler chain, budget enforcement, metrics |
| [test_compaction_guardian.py](test_compaction_guardian.py) | Token thresholds, creative break capture, insight extraction |
| [test_log_graph.py](test_log_graph.py) | DAG operations, trace, ancestors, descendants, serialization |
| [test_graph_invariants.py](test_graph_invariants.py) | Graph structure invariants — cycle detection, depth bounds |
| [test_token_estimator.py](test_token_estimator.py) | Heuristic estimation accuracy, health thresholds |
| [test_personal_log.py](test_personal_log.py) | Daily summaries, decision trails, JSON export |
| [test_coverage_gaps.py](test_coverage_gaps.py) | Edge cases and integration paths across all modules |

## Running

```bash
cd /home/eileen/projects/cns-bridge
pytest
```

All 270 tests must pass before any fleet deployment. The hull is checked before the ship sails.

← Back to **[CNS Bridge](../README.md)**
