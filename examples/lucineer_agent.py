#!/usr/bin/env python3
"""Lucineer checks in with Hermes over the CNS bus.

This example shows a creative agent sending a heartbeat/query packet and
waiting for a response. It uses a temporary inbox/outbox pair so it can run
anywhere without the Windows Hermes mount.
"""

from __future__ import annotations

import tempfile
import time

from cns_bridge import Agent, FileSystemTransport, Intent, Priority


def main() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        transport = FileSystemTransport(
            inbox_path=f"{tmpdir}/inbox",
            outbox_path=f"{tmpdir}/outbox",
        )

        class Lucineer(Agent):
            def handle(self, packet):
                print(
                    f"[Lucineer] received response from {packet.header.origin_id}: "
                    f"{packet.body.message}"
                )

        lucineer = Lucineer(
            agent_id="lucineer",
            transport=transport,
            secret="lucineer-shared-secret",
        )

        # Start watching for responses in the background.
        lucineer.start_heartbeat(interval=0.5)

        # Send a heartbeat/query to Hermes.
        packet = lucineer.send(
            intent=Intent.QUERY,
            message="Hermes, what is the current fleet status?",
            priority=Priority.NORMAL,
        )
        print(f"[Lucineer] sent packet {packet.header.packet_id}")

        # Simulate Hermes replying by dropping a packet into Lucineer's inbox.
        from cns_bridge import PacketBuilder

        response = (
            PacketBuilder(origin_id="hermes")
            .to("lucineer")
            .with_intent(Intent.RESPONSE)
            .with_priority(Priority.NORMAL)
            .with_correlation_id(packet.header.packet_id)
            .with_message("All systems nominal. Creative engines at 73%.")
            .signed_with("lucineer-shared-secret", key_id="hermes")
            .build()
        )
        # Hermes writes to the agent's inbox, not the outbox.
        response_path = (
            transport.inbox_path
            / f"hermes_{response.header.packet_id}.uscp.json"
        )
        response_path.write_text(response.to_json(), encoding="utf-8")
        print(f"[Hermes]  dropped response {response.header.packet_id} into inbox")

        time.sleep(1.5)
        lucineer.stop_heartbeat()


if __name__ == "__main__":
    main()
