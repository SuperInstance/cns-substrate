"""Background poller that watches the CNS inbox for new responses.

Delivery semantics (read this before relying on the poller):

* **At-most-once per process.** A packet whose ID has been delivered is
  recorded in an in-memory ``_seen`` set and is never delivered again for
  the lifetime of the poller. The set has no TTL and grows without bound;
  restarting the process clears it. Packets that are still sitting in the
  inbox after a restart are therefore delivered again — dedup is per-run,
  not durable.
* **Handled packets are removed from the inbox.** The transport deletes a
  packet file as it is polled, before the callback runs. If the callback
  raises, the packet is gone from the inbox: it is written to the dead
  letter directory (preserved, not lost) but it will not be re-delivered.
  Senders that need guaranteed delivery must implement their own retry
  (e.g. resend on timeout, correlated via ``correlation_id``).
* **Handlers must not crash the poller.** Any exception raised by the
  callback is logged and dead-lettered; the polling thread keeps running.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .packet import Packet
from .transport import FileSystemTransport


PacketHandler = Callable[[Packet], Any]

logger = logging.getLogger(__name__)


class HeartbeatPoller:
    """Poll the filesystem inbox and invoke a callback for new packets.

    The poller keeps track of packet IDs it has already delivered so each
    packet is only handled once. It runs in a background thread and can be
    started and stopped cleanly.

    Args:
        transport: FileSystemTransport instance to poll.
        agent_id: Only packets addressed to this agent (or originating from
            it, depending on filter) are delivered.
        callback: Function called with each new Packet.
        interval: Seconds between polls.
        filter_origin: If True, deliver packets whose ``origin_id`` matches
            ``agent_id``. If False, deliver packets whose ``destination_id``
            matches ``agent_id``.
        dead_letter_path: Directory where packets whose handler raised are
            preserved before they are lost from the inbox. Defaults to
            ``<outbox parent>/cns_dead_letter``; created lazily on first
            failure. Pass ``False`` to disable dead-lettering entirely
            (not recommended — packets would vanish silently).
    """

    def __init__(
        self,
        transport: FileSystemTransport,
        agent_id: str,
        callback: PacketHandler,
        interval: float = 1.0,
        filter_origin: bool = False,
        dead_letter_path: Path | str | bool | None = None,
    ) -> None:
        self.transport = transport
        self.agent_id = agent_id
        self.callback = callback
        self.interval = interval
        self.filter_origin = filter_origin
        if dead_letter_path is False:
            self._dead_letter_path: Path | None = None
        elif dead_letter_path is None:
            self._dead_letter_path = (
                Path(transport.outbox_path).parent / "cns_dead_letter"
            )
        else:
            self._dead_letter_path = Path(dead_letter_path)
        self._seen: set[str] = set()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def _match(self, packet: Packet) -> bool:
        if self.filter_origin:
            return packet.header.origin_id == self.agent_id
        return packet.header.destination_id == self.agent_id

    def _poll_once(self) -> None:
        for packet in list(self.transport.poll()):
            if not self._match(packet):
                continue
            pid = packet.header.packet_id
            with self._lock:
                if pid in self._seen:
                    continue
                self._seen.add(pid)
            try:
                self.callback(packet)
            except Exception:
                # A handler must not crash the poller. The packet is already
                # gone from the inbox (the transport removed it), so preserve
                # it in the dead letter directory instead of losing it. The
                # ID stays in _seen so a buggy handler is not retried
                # endlessly.
                logger.exception(
                    "HeartbeatPoller handler failed for packet %s from %s; "
                    "dead-lettering",
                    pid,
                    packet.header.origin_id,
                )
                self._write_dead_letter(packet)
                continue

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                # Transport errors should not terminate the background thread;
                # the next poll will retry.
                pass
            self._stop_event.wait(self.interval)

    def start(self) -> "HeartbeatPoller":
        """Start the background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float | None = None) -> None:
        """Signal the poller to stop and wait for the thread to finish."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout)
        self._thread = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def reset_seen(self) -> None:
        """Clear the set of delivered packet IDs.

        Call this after fixing a handler that previously raised: any packets
        that are still present in the inbox (e.g. duplicates re-written by
        a retrying sender, or packets that arrived after a restart) will be
        delivered again on the next poll.
        """
        with self._lock:
            self._seen.clear()

    def _write_dead_letter(self, packet: Packet) -> None:
        """Persist a packet whose handler failed, so it is not lost.

        Writes are atomic (temp file + rename). Failures to dead-letter are
        logged but never raised — the poller must keep running.
        """
        if self._dead_letter_path is None:
            return
        try:
            self._dead_letter_path.mkdir(parents=True, exist_ok=True)
            filename = (
                f"{packet.header.origin_id}_{packet.header.packet_id}_"
                f"{uuid.uuid4().hex[:8]}{self.transport.extension}"
            )
            target = self._dead_letter_path / filename
            temp_fd, temp_path = tempfile.mkstemp(
                suffix=self.transport.extension,
                dir=self._dead_letter_path,
                prefix=".tmp_",
            )
            try:
                with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                    f.write(packet.to_json())
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, target)
            except Exception:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
                raise
        except Exception:
            logger.exception(
                "Failed to dead-letter packet %s from %s",
                packet.header.packet_id,
                packet.header.origin_id,
            )
