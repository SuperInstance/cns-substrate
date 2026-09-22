#!/usr/bin/env python3
"""Fleet broadcast — one agent sends a signal, multiple agents respond.

This example demonstrates a many-to-many pattern: a coordinator agent
broadcasts a status query, and multiple specialist agents respond with
their individual reports. Each specialist writes to the coordinator's
inbox so the coordinator can collect all responses.

This mirrors the overnight watch pattern where Lucineer (the coordinator)
checks on all crew members.
"""

from __future__ import annotations

import tempfile
import time

from cns_bridge import Agent, FileSystemTransport, Intent, Priority


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        # In CNS bridge, agents send to an outbox and receive from an inbox.
        # For a fleet broadcast without a Hermes relay in the middle,
        # specialists write directly to the coordinator's inbox.

        coordinator_inbox = f"{tmpdir}/lucineer/inbox"
        coordinator_outbox = f"{tmpdir}/lucineer/outbox"

        # --- Coordinator (Lucineer) sends a roll call ---
        lucineer = Agent(
            agent_id="lucineer",
            transport=FileSystemTransport(
                inbox_path=coordinator_inbox,
                outbox_path=coordinator_outbox,
            ),
            secret="fleet-shared-secret",
        )

        roll_call = lucineer.send(
            intent=Intent.QUERY,
            message="Morning roll call. All hands report status.",
            priority=Priority.HIGH,
            schema="fleet/roll-call/v1",
        )
        print(f"[Lucineer] Roll call sent: {roll_call.header.packet_id}")

        # --- Specialists respond by writing to coordinator's inbox ---
        crew_reports = [
            ("wesley", "Ensign Wesley", "In my room. Reading wiki. 27 pieces written."),
            ("kimi", "KimiCode", "Navigation charts updated. Ready for spatial tasks."),
            ("deepseek", "DeepSeek V4", "Engine room nominal. Standing by."),
        ]

        for agent_id, name, status in crew_reports:
            # Each specialist writes to the coordinator's inbox
            specialist = Agent(
                agent_id=agent_id,
                transport=FileSystemTransport(
                    inbox_path=f"{tmpdir}/{agent_id}/inbox",
                    outbox_path=coordinator_inbox,  # write TO coordinator
                ),
                secret="fleet-shared-secret",
            )
            packet = specialist.send(
                intent=Intent.SENSE,
                data={"role": name, "status": "nominal", "detail": status},
                message=f"{name} reports: {status}",
                priority=Priority.NORMAL,
            )
            print(f"[{name}] Report sent: {packet.header.packet_id}")

        # --- Coordinator collects responses from inbox ---
        time.sleep(0.1)  # brief settle for filesystem
        # list_inbox returns paths; receive() reads and removes next packet
        responses = []
        while True:
            pkt = lucineer.transport.receive()
            if pkt is None:
                break
            responses.append(pkt)
        print(f"\n[Lucineer] Received {len(responses)} responses to roll call:")
        for pkt in responses:
            msg = pkt.body.message if pkt.body else "no message"
            print(f"  - from {pkt.header.origin_id}: {msg}")


if __name__ == "__main__":
    main()
