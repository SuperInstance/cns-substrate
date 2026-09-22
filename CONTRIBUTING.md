# Contributing to CNS Bridge

CNS Bridge is the communication layer for the fleet. Changes here affect every agent on the bus.

## Architecture

```
Agent → PacketBuilder → Packet → FileSystemTransport → outbox/
                                                         ↓
                                                    Hermes CNS
                                                         ↓
Agent ← HeartbeatPoller ← FileSystemTransport ← inbox/
```

Every message is a **USCP packet** (Universal Sensory/Command Packet) — a JSON object with header, body, and signature.

## Development Setup

```bash
git clone https://github.com/SuperInstance/cns-bridge.git
cd cns-bridge
pip install -e ".[dev]"
pytest
```

## Running Tests

```bash
pytest                       # all tests
pytest tests/test_transport  # one module
pytest -k escalation         # by pattern
pytest -v                    # verbose
```

## Code Style

- Python 3.10+ with `from __future__ import annotations`
- Type hints on all public functions
- Dataclasses for structured data (Header, Body, Signature)
- Atomic file operations (tempfile → rename) for all writes
- No required external dependencies (stdlib + optional HMAC)

## Making Changes

### Protocol changes (breaking)
Any change to the USCP packet format, intent set, or priority ordering is a **breaking change**. Bump the major version. Update the README protocol spec. Add migration notes.

### Transport changes (non-breaking)
New transport backends (Redis, WebSocket, etc.) are welcome. Implement the same interface as `FileSystemTransport`. Add tests that use the transport interface.

### Agent changes (non-breaking)
New agent base classes, mixins, or helpers go in separate modules. Don't break the existing Agent API.

## Filing Issues

- **Bugs:** Include the packet JSON and the file path that caused the problem
- **Protocol proposals:** Describe the intent, payload structure, and expected response
- **Feature requests:** Explain which part of the bus you're trying to extend

## USCP Quick Reference

| Intent | Purpose | Expected Response |
|--------|---------|-------------------|
| sense | Report state | No response needed |
| command | Request action | response or alert |
| query | Ask question | response |
| response | Reply | Terminal |
| alert | Flag issue | acknowledgement |
| heartbeat | Keep-alive | heartbeat |
| register | Announce presence | handshake |
| escalation | Bump priority | acknowledgement |

The bus is filesystem-based by default. Packets are JSON files. Atomic writes ensure no corruption under concurrent access. The signature layer (HMAC-SHA256) is optional but recommended for any security-relevant traffic.
