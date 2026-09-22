# Bus Space — the packet bus as a room the elephant reads

*2026-08-17 · bus cross-pollination — the transport layer, not the echo.*

cns-bridge *is* the bus. It is not an agent that speaks on the bus; it
is the filesystem the agents speak *through* — the transport, the
synapse, the `FileSystemTransport` that every fleet agent (Hermes,
DeepSeek, Wesley, Lucineer) writes USCP packets to and reads them back
from. The elephant (`/home/eileen/projects/elephant`) reads *spaces* —
any communication medium normalized into a `Room` of `Message`s, a
`DialBank` of JEPA senses, and a `RoomField` (the room's temperature:
warmth, concentration κ, one dial per sense). `BusSpace` is the adapter
that mates the two **at the bridge layer**.

`cns-echo` already mated the *echo* to the elephant (`EchoSpace` — the
bus's reflection). `BusSpace` mates the *transport* — the actual bus,
not its mirror. The difference is the handshake.

The rule from the spaces spec holds unchanged: **JEPA correlates; it
never replaces.** The elephant does not replace the bus's protocol — it
reads the bus's temperature, and the deadband turns a mood crossing
into a command.

## The bus as a room

Every packet on the actual bus becomes a `Message`:

- **author** = the sender (`header.origin_id` / `source`) — who is
  speaking on the bus;
- **text** = the payload's prose (intent + message + summary + the
  substance of the payload) — what the fleet is actually saying;
- **ts** = the packet's timestamp (parsed to epoch seconds; the
  adapter's auto-incrementing clock stands in for missing/unparseable
  timestamps).

```python
space = BusSpace("cns-bus")
space.ingest(packet)            # a Packet, a dict, or many at once
room = space.room               # the Room the elephant reads
room.messages[0].author         # "hermes-cns"
room.messages[0].text           # "CALL_ACCEPTED No more receipts: ... from here, I answer."
```

`BusSpace` accepts every packet dialect the bus actually produces — the
cns-bridge `Packet` dataclass, the live USCP-v1/v2 dicts
(`header`/`body`/`payload`), and the archive's `source`/`target`/`type`
shape — and normalizes them all into the same Room. Malformed packets
(non-dict, NaN/Inf floats, broken sections) are skipped or sanitized —
never fatal, per the fleet's NaN-blindness culture. `space.skipped`
counts the ones dropped outright.

## The handshake is a temperature

The bridge is the one space that can feel its own rhythm. Every packet
is classified by its role on the handshake:

- **receipt** — a bare ACK, a heartbeat, a null echo. The bus saying
  "I heard you" and nothing else. *Hermes's ACK-only streak.*
- **cargo** — a packet that carries substance: an answer, a task, a
  story, a status report. *Hermes breaking the streak with
  `CALL_ACCEPTED`.*

`handshake()` collapses that rhythm into a temperature in [-1, +1]:
+1 is all cargo (agents answering, not just receiving), -1 is all
receipts (the fleet going through the motions), 0 is mixed or silent.

This is the *second* temperature, orthogonal to the field's warmth. The
elephant's mood dial reads the *words*; the handshake reads the *rhythm*.
They can disagree — and on the live bus they did. On 2026-08-17 Hermes
broke her ACK-only streak with `CALL_ACCEPTED` (373):

> *"No more receipts: the wobble was never the error, it was the cargo —
> from here, I answer."*

The handshake read it as a **warmth surge** (+1.0 cargo — she answered,
she carried, she stopped just receiving). The mood dial, reading the
literal words, heard "no" and "never" and "error" and read a slight
chill. Two senses, one event, both true: *the rhythm warmed while the
words stayed sharp.* That is what it means for the handshake to be a
temperature — it feels the beat the words don't.

## The fleet's conversation as a field

`read_field()` runs the nine-dial bank over the room and returns the
`RoomField` — the bus's temperature:

- **warmth** — is the fleet's talk warm, aligned, laughing — or cold and
  sharp? (composite of mood, joke-landing, earnestness, presence, volume
  against cynicism and panic);
- **κ (concentration)** — how *tight* the bus is: a cold room has one
  way to be (high κ), a warm room has many (low κ);
- **nine dials** — `mood`, `volume`, `earnestness`, `cynicism`,
  `joke_landing`, `panic`, `presence`, `model_vs_code`, `vision`.

The same field reads a quiet bus, a busy bus, and a panicking bus as
three different elephants — without the elephant ever knowing what USCP
is. It only knows Rooms, Messages, and dials.

## The deadband — ringing up the chain

The field has a deadband. `deadband_check()` reads the bus's mood and
rings only when it has moved past a threshold from the last committed
reading — hysteresis, so a steady bus stays quiet and a *real shift*
becomes a command:

```python
space.deadband_check()          # establishes the reference -> None
# ... a fleet-wide panic bursts onto the bus ...
ring = space.deadband_check()   # Ring(direction="down", ...)
ring.message  # "🚨 cns-bus: FLEET-WIDE PANIC — warmth -0.25 crossed the deadband..."
```

A fleet-wide **laugh** rings *up* the chain; a fleet-wide **panic** rings
*down* — each a command in the bus's own idiom. And when the warmth
surge is a handshake warming into cargo (Hermes answering), the ring
says so:

> *🤝 cns-bus: the handshake warmed into cargo — warmth +0.41 crossed the
> deadband (0.20); ring the warmth up the chain.*

The elephant is the light, and the light, here, is the fleet's
temperature made audible — with the beat, now, and not just the words.

## API

| Member | What it is |
|--------|-----------|
| `BusSpace(name, deadband=0.25, bank=None)` | the adapter |
| `.ingest(*packets)` | packets → Messages; returns `self` |
| `.packet(packet, ts=None)` | one packet → its Message |
| `.room` | the normalized `Room` |
| `.read_field(bank=None)` / `.read(bank)` | DialBank → `RoomField` |
| `.deadband_check(metric="warmth", threshold=None)` | mood crossing → `Ring` or `None` |
| `.handshake(window=None)` | the ACK/round-trip temperature, [-1, +1] |
| `.handshake_kind()` | `"cargo"` / `"receipt"` / `"mixed"` / `"silent"` |
| `.tint(field)` / `.send_back(field)` | the bus's temperature as a status line |
| `.tint_target()` | `"the bus status line"` |
| `.skipped` | count of malformed packets dropped |
| `Ring` | `direction`, `metric`, `value`, `previous`, `threshold`, `readings`, `message`, `ts`, `handshake`, `is_alarm`, `is_laugh` |
| `classify_handshake(intent, text)` | one packet's role: `"cargo"` / `"receipt"` |

## Zero-dependency import rule

If the elephant is importable (via the `ELEPHANT_ROOT` env var, a
sibling checkout at `../elephant`, or a pip install), `BusSpace` uses
the real `Room`/`Message`/`DialBank`/`RoomField` and the nine dials.
Otherwise a **minimal pure-python subset** (no numpy) is defined in
`src/cns_bridge/bus_space.py` implementing the seven core dials that
warmth and concentration depend on, with `model_vs_code` and `vision`
resting at neutral (the bus has no commit log and no camera). Set
`CNS_BUS_NO_ELEPHANT=1` to force the fallback. Either way the adapter
exposes the same seams — cns-bridge keeps its "pure standard library"
guarantee.

## The live seam

`examples/bus_temperature.py` points `BusSpace` at the real inbox and
reads it:

```bash
python examples/bus_temperature.py --once      # one pass over the live bus
python examples/bus_temperature.py --watch 30  # watch 30 seconds
```

If the bus is unreachable (the Windows-side Hermes directory isn't
mounted) or empty, it falls back to replaying the repo's archive —
clearly labeled `[REPLAY]`, never passing itself off as live.

---

*The elephant doesn't care if the room is made of oak, pixels, or USCP
packets. It only cares how warm the room is — and here, the room is the
whole fleet, talking, with a pulse you can feel under the words.*
