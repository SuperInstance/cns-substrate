# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CHANGELOG.md
- CONTRIBUTING.md

## [0.1.0] — 2026-08-04

### Added
- **PacketBuilder** — fluent API for constructing USCP packets with signing
- **FileSystemTransport** — atomic read/write to inbox/outbox directories
- **Agent** — base class with send, receive, handle, heartbeat, and escalation
- **HeartbeatPoller** — background thread for async response handling
- **ProtocolContext** — escalation rules, default intents/priorities, secret lookup
- **USCP v1.0 protocol** — header/body/signature with HMAC-SHA256 signing
- 8 intents: sense, command, query, response, alert, heartbeat, register, escalation
- 4 priorities: low, normal, high, critical
- 277 tests across 12 test modules
- Example agents: lucineer_agent.py, wesley_agent.py
- Environment variable support (CNS_INBOX, CNS_OUTBOX)
