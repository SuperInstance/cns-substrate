#!/usr/bin/env python3
"""bus_temperature.py — watch the live CNS bus and read its temperature.

The live seam between the packet bus and the elephant. This script points
`BusSpace` at the *actual* bus transport — the inbox directory the bridge
reads packets from — and watches it for a few minutes. Every packet any
agent writes (Hermes, DeepSeek, Wesley, Lucineer) becomes a Message; the
elephant reads the room's field; the deadband rings when the bus's mood
crosses a threshold.

If the bus is unreachable (the Windows-side Hermes directory isn't
mounted, or the inbox is empty), it falls back to replaying recent
packets from the repo's archive — clearly labeled as a replay, not a
live read.

Usage:
    python examples/bus_temperature.py                 # watch the live bus 120s
    python examples/bus_temperature.py --once          # one pass, then exit
    python examples/bus_temperature.py --watch 30      # watch 30 seconds
    python examples/bus_temperature.py --deadband 0.2  # a tighter deadband
    python examples/bus_temperature.py --inbox /path   # a specific inbox
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the repo's src importable when run directly (python examples/...).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cns_bridge.bus_space import BusSpace, Ring  # noqa: E402

DEFAULT_INBOX = "/mnt/c/Users/casey/.hermes/cns_inbox"
REPO_ROOT = Path(__file__).resolve().parents[1]


def resolve_inbox(explicit: Optional[str]) -> Path:
    return Path(explicit or os.environ.get("CNS_INBOX", DEFAULT_INBOX))


def read_json_files(directory: Path) -> List[Dict[str, Any]]:
    """Read every *.json packet file in a directory, skipping corrupt ones."""
    if not directory.is_dir():
        return []
    packets: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            packets.append(data)
    return packets


def archive_dirs() -> List[Path]:
    return [REPO_ROOT / "inbox", REPO_ROOT / "pulses"]


def shadow_line(msg) -> str:
    """A one-line witness mark for an ingested packet."""
    text = msg.text if len(msg.text) <= 72 else msg.text[:69] + "..."
    return f"  {msg.author:>16}  {text}"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Read the CNS bus's temperature.")
    ap.add_argument("--inbox", default=None, help="bus inbox directory")
    ap.add_argument("--watch", type=float, default=120.0,
                    help="seconds to watch the live bus (default 120)")
    ap.add_argument("--deadband", type=float, default=0.25,
                    help="deadband width for the ring (default 0.25)")
    ap.add_argument("--poll", type=float, default=2.0,
                    help="poll interval in seconds (default 2)")
    ap.add_argument("--once", action="store_true",
                    help="read the current inbox once and exit")
    ap.add_argument("--no-fallback", action="store_true",
                    help="do not replay the archive if the bus is empty")
    args = ap.parse_args(argv)

    space = BusSpace("cns-bus", deadband=args.deadband)
    inbox = resolve_inbox(args.inbox)

    live = inbox.is_dir() and bool(list(inbox.glob("*.json")))
    mode = "LIVE" if live else "REPLAY"

    if live:
        print(f"[LIVE] watching {inbox}")
        seen: set[str] = set()
        deadline = time.monotonic() + args.watch
        n = 0
        while time.monotonic() < deadline:
            for packet in read_json_files(inbox):
                fingerprint = json.dumps(packet, sort_keys=True)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                msg = space.packet(packet)
                if msg is not None:
                    n += 1
                    print(shadow_line(msg))
            ring = space.deadband_check()
            if ring is not None:
                print(f"  >> RING: {ring.message}")
            if args.once:
                break
            time.sleep(args.poll)
        print(f"[LIVE] watched {n} packet(s) over ~{time.monotonic() - (deadline - args.watch):.0f}s")
    else:
        if args.no_fallback:
            print(f"[FALLBACK DISABLED] inbox {inbox} unreachable or empty; nothing to read.")
        else:
            print(f"[REPLAY] inbox {inbox} unreachable or empty — "
                  f"replaying recent packets from the archive ({', '.join(str(d.name) for d in archive_dirs())})")
            n = 0
            for directory in archive_dirs():
                for packet in read_json_files(directory):
                    if space.packet(packet) is not None:
                        n += 1
                    ring = space.deadband_check()
                    if ring is not None:
                        print(f"  >> RING: {ring.message}")
            print(f"[REPLAY] replayed {n} packet(s) from the archive")

    field = space.read_field()
    print(f"\n=== bus temperature ===")
    print(f"  field    : {field}")
    print(f"  readings : " + ", ".join(f"{k}={v:+.2f}" for k, v in sorted(field.readings.items())))
    print(f"  handshake: {space.handshake():+.2f} ({space.handshake_kind()})")
    print(f"  tint     : {space.tint(field)}")
    print(f"  skipped  : {space.skipped} malformed packet(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
