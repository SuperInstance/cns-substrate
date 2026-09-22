#!/usr/bin/env python3
"""Wesley sends night-school training results to Hermes.

This example demonstrates a report-style packet with structured data in the
body. It runs against a temporary inbox/outbox pair for portability.
"""

from __future__ import annotations

import tempfile

from cns_bridge import Agent, FileSystemTransport, Intent, Priority


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        transport = FileSystemTransport(
            inbox_path=f"{tmpdir}/inbox",
            outbox_path=f"{tmpdir}/outbox",
        )

        wesley = Agent(
            agent_id="wesley",
            transport=transport,
            secret="wesley-shared-secret",
        )

        packet = wesley.send(
            intent=Intent.SENSE,
            data={
                "session": "night-school-07",
                "subject": "tactical-communication",
                "score": 0.94,
                "iterations": 120,
                "notes": ["improved clarity", "reduced hedging"],
            },
            message="Night school session complete. Performance exceeded threshold.",
            priority=Priority.HIGH,
            schema="wesley/training-report/v1",
        )

        print(f"[Wesley] sent report {packet.header.packet_id}")
        print(packet.to_json(indent=2))


if __name__ == "__main__":
    main()
