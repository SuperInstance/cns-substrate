"""
NMEA → SWMIDI Bridge

Converts standard NMEA 0183 marine sensor sentences into SWMIDI-8 events
on the shared BeatClock. This bridges the two nervous systems:
  - SWMIDI carries agent events (builds, model outputs, flow state)
  - NMEA carries boat data (GPS, depth, heading)

Now they speak the same language.

## NMEA Sentences Supported

- $GPGGA: Global Positioning System Fix Data (lat, lon, satellites, fix quality)
- $GPRMC: Recommended Minimum Navigation Information (lat, lon, sog, cog, heading)
- $SDDBT: Depth Below Transducer (feet, meters, fathoms)
- $HCHDT: Heading True (degrees)

## SWMIDI-8 Format

  byte 0     status:     type(4 bits) | channel(4 bits)
  byte 1     pitch:      NMEA event type (0-127)
  byte 2     velocity:   confidence/quality (0-127)
  byte 3     error_mask  sensor health (8 friction bits)
  bytes 4-7  tick:       uint32, 96 PPQ on the shared BeatClock

## Pitch Map (NMEA-specific)

  10 = GPS position (lat/lon encoded in CC pairs)
  11 = GPS speed over ground
  12 = GPS course over ground
  20 = Depth reading
  21 = Heading true
  30 = Fix quality indicator
  31 = Satellite count

Usage:
    from cns_bridge.nmea_swmidi_bridge import NmeaToSwmidi

    bridge = NmeaToSwmidi()
    events = bridge.parse("$SDDBT,45.7,f,13.9,M,7.6,F*71")
    for event in events:
        print(event)
"""

from __future__ import annotations

import math
import re
import struct
import time
from dataclasses import dataclass, field
from typing import Optional


# ── NaN/Inf Safety ───────────────────────────────────────────────────

def _safe_float(value, default: float | None = None) -> float | None:
    """
    Parse a string to float, returning default for empty/invalid/NaN/Inf.

    NMEA sentences from real marine sensors can be corrupted by electrical
    noise, water ingress, or firmware bugs. A sentence field containing
    'nan', 'inf', '-inf', or garbage will produce a Python float that is
    NaN or Inf — which then propagates silently through every downstream
    calculation (velocity scaling, depth warnings, position encoding).

    This function is the firewall. Every float() call on NMEA field data
    goes through here.
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        if not value or not value.strip():
            return default
        try:
            result = float(value)
        except (ValueError, TypeError):
            return default
    else:
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


# ── Pitch codes for NMEA event types ────────────────────────────────

PITCH_GPS_POSITION = 10
PITCH_GPS_SOG = 11       # Speed over ground
PITCH_GPS_COG = 12       # Course over ground
PITCH_DEPTH = 20
PITCH_HEADING_TRUE = 21
PITCH_FIX_QUALITY = 30
PITCH_SATELLITE_COUNT = 31

# ── Error mask bits (same as flux-core error_mask.rs) ────────────────

MASK_SPATIAL = 0x01      # position collision
MASK_TEMPORAL = 0x02     # timing violation
MASK_SEMANTIC = 0x04     # nonsensical output
MASK_SAFETY = 0x08       # content safety flag
MASK_RESOURCE = 0x10     # resource unavailable
MASK_TOPOLOGY = 0x20     # connectivity issue
MASK_AUTHORITY = 0x40    # permission denied
MASK_CONSISTENCY = 0x80  # state inconsistency

# ── SWMIDI-8 packed event ───────────────────────────────────────────

@dataclass(frozen=True)
class SwmidiEvent:
    """A single 8-byte SWMIDI event."""
    status: int       # (type_nibble << 4) | channel
    pitch: int        # event type code (0-127)
    velocity: int     # confidence (0-127)
    error_mask: int   # friction bitmask
    tick: int         # BeatClock position (96 PPQ)

    # Optional metadata (not packed in the 8 bytes)
    cc_pairs: tuple[tuple[int, int], ...] = ()
    source: str = ""          # NMEA sentence type
    raw_data: str = ""        # original NMEA sentence

    NOTE_ON = 0x90   # status nibble for NoteOn
    CC = 0xB0        # status nibble for ControlChange

    def pack(self) -> bytes:
        """Pack into exactly 8 bytes (little-endian)."""
        return struct.pack(
            "<BBBBI",
            self.status,
            self.pitch,
            self.velocity,
            self.error_mask,
            self.tick,
        )

    @classmethod
    def unpack(cls, data: bytes) -> "SwmidiEvent":
        """Unpack 8 bytes into an event."""
        if len(data) < 8:
            raise ValueError(f"Need 8 bytes, got {len(data)}")
        status, pitch, velocity, error_mask, tick = struct.unpack("<BBBBI", data[:8])
        return cls(
            status=status,
            pitch=pitch,
            velocity=velocity,
            error_mask=error_mask,
            tick=tick,
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "pitch": self.pitch,
            "velocity": self.velocity,
            "error_mask": self.error_mask,
            "tick": self.tick,
            "cc_pairs": list(self.cc_pairs),
            "source": self.source,
            "raw_data": self.raw_data,
        }


# ── NMEA sentence parsers ───────────────────────────────────────────

def _parse_nmea_checksum(sentence: str) -> tuple[str, bool]:
    """
    Validate and strip NMEA checksum.
    Returns (body_without_checksum, checksum_valid).
    """
    if '*' not in sentence:
        return sentence.rstrip('\r\n'), True  # no checksum = assume valid

    body, _, checksum = sentence.partition('*')
    body = body.lstrip('$')
    checksum = checksum.strip()[:2]

    calculated = 0
    for char in body:
        calculated ^= ord(char)

    try:
        expected = int(checksum, 16)
        return body, calculated == expected
    except ValueError:
        return body, False


def parse_gga(fields: list[str]) -> dict:
    """
    Parse GGA sentence fields.
    $GPGGA,hhmmss.ss,llll.ll,a,yyyyy.yy,a,x,xx,x.x,x.x,M,x.x,M,x.x,xxxx*hh

    Returns dict with: time, lat, lon, fix_quality, satellites, altitude
    """
    result = {
        'time': fields[0] if len(fields) > 0 else '',
        'lat': _parse_lat_lon(fields[1], fields[2]) if len(fields) > 2 else None,
        'lon': _parse_lat_lon(fields[3], fields[4]) if len(fields) > 4 else None,
        'fix_quality': int(fields[5]) if len(fields) > 5 and fields[5] else 0,
        'satellites': int(fields[6]) if len(fields) > 6 and fields[6] else 0,
        'altitude': _safe_float(fields[8]) if len(fields) > 8 else None,
    }
    return result


def parse_rmc(fields: list[str]) -> dict:
    """
    Parse RMC sentence fields.
    $GPRMC,hhmmss.ss,A,llll.ll,a,yyyyy.yy,a,x.x,x.x,ddmmyy,x.x,a*hh

    Returns dict with: time, status, lat, lon, sog, cog, date, magnetic_variation
    """
    return {
        'time': fields[0] if len(fields) > 0 else '',
        'status': fields[1] if len(fields) > 1 else '',
        'lat': _parse_lat_lon(fields[2], fields[3]) if len(fields) > 3 else None,
        'lon': _parse_lat_lon(fields[4], fields[5]) if len(fields) > 5 else None,
        'sog': _safe_float(fields[6], 0.0) if len(fields) > 6 else 0.0,  # knots
        'cog': _safe_float(fields[7], 0.0) if len(fields) > 7 else 0.0,  # degrees
        'date': fields[8] if len(fields) > 8 else '',
    }


def parse_dbt(fields: list[str]) -> dict:
    """
    Parse DBT sentence fields.
    $SDDBT,depth_feet,f,depth_meters,M,depth_fathoms,F*hh

    Returns dict with: depth_meters
    """
    depth_m = None
    if len(fields) > 2 and fields[2]:
        depth_m = _safe_float(fields[2])
    depth_ft = None
    if len(fields) > 0 and fields[0]:
        depth_ft = _safe_float(fields[0])
    return {
        'depth_meters': depth_m,
        'depth_feet': depth_ft,
    }


def parse_hdt(fields: list[str]) -> dict:
    """
    Parse HDT sentence fields.
    $HCHDT,heading_true,T*hh

    Returns dict with: heading_true (degrees)
    """
    heading = None
    if len(fields) > 0 and fields[0]:
        heading = _safe_float(fields[0])
    return {'heading_true': heading}


def _parse_lat_lon(value: str, direction: str) -> Optional[float]:
    """Parse NMEA lat/lon format to decimal degrees."""
    if not value or not direction:
        return None
    try:
        # Latitude: ddmm.mmmm, Longitude: dddmm.mmmm
        if direction in ('N', 'S'):
            deg = int(value[:2])
            minutes = _safe_float(value[2:])
        else:  # E, W
            deg = int(value[:3])
            minutes = _safe_float(value[3:])

        if minutes is None:
            return None

        decimal = deg + minutes / 60.0
        if direction in ('S', 'W'):
            decimal = -decimal
        # Guard against NaN/Inf from corrupted data
        if math.isnan(decimal) or math.isinf(decimal):
            return None
        return round(decimal, 6)
    except (ValueError, IndexError):
        return None


# ── The bridge ───────────────────────────────────────────────────────

class NmeaToSwmidi:
    """
    Convert NMEA 0183 sentences to SWMIDI-8 events.

    Each NMEA sentence becomes one or more SWMIDI events on the BeatClock.
    Sensor health (fix quality, satellite count) maps to velocity and error_mask.
    """

    # NMEA sentence type → parser function
    PARSERS = {
        'GGA': parse_gga,
        'GPRMC': parse_rmc,
        'RMC': parse_rmc,
        'GPGGA': parse_gga,
        'SDDBT': parse_dbt,
        'DBT': parse_dbt,
        'HCHDT': parse_hdt,
        'HDT': parse_hdt,
    }

    def __init__(self, channel: int = 5, start_tick: int = 0):
        """
        Args:
            channel: SWMIDI channel for marine data (5 = sensor bus).
            start_tick: Initial BeatClock tick.
        """
        self.channel = channel & 0x0F
        self.tick = start_tick
        self._tick_increment = 12  # ~1/8 beat at 96 PPQ

    def _next_tick(self) -> int:
        tick = self.tick
        self.tick += self._tick_increment
        return tick

    def parse(self, sentence: str) -> list[SwmidiEvent]:
        """
        Parse a single NMEA sentence into SWMIDI events.

        Args:
            sentence: Raw NMEA sentence (e.g., "$SDDBT,45.7,f,13.9,M,7.6,F*71")

        Returns:
            List of SwmidiEvent objects (usually 1, sometimes more).
        """
        sentence = sentence.strip()
        if not sentence or not sentence.startswith('$'):
            return []

        body, checksum_valid = _parse_nmea_checksum(sentence)

        if not checksum_valid:
            return [SwmidiEvent(
                status=SwmidiEvent.CC | self.channel,
                pitch=0,
                velocity=0,
                error_mask=MASK_CONSISTENCY,
                tick=self._next_tick(),
                source='INVALID',
                raw_data=sentence,
            )]

        fields = body.split(',')
        sentence_type = fields[0].lstrip('$')

        # Find the parser
        parser = None
        for prefix, func in self.PARSERS.items():
            if sentence_type.endswith(prefix) or sentence_type == prefix:
                parser = func
                break

        if parser is None:
            return []

        try:
            data = parser(fields[1:])
        except Exception:
            return [SwmidiEvent(
                status=SwmidiEvent.CC | self.channel,
                pitch=0,
                velocity=0,
                error_mask=MASK_SEMANTIC,
                tick=self._next_tick(),
                source=sentence_type,
                raw_data=sentence,
            )]

        return self._encode(sentence_type, data, sentence)

    def _encode(self, sentence_type: str, data: dict, raw: str) -> list[SwmidiEvent]:
        """Encode parsed NMEA data as SWMIDI events."""
        events: list[SwmidiEvent] = []

        if 'fix_quality' in data:
            # GGA: GPS position + fix quality + satellites
            fix = data.get('fix_quality', 0)
            sats = data.get('satellites', 0)

            # Error mask from fix quality
            error_mask = 0
            if fix == 0:
                error_mask |= MASK_TOPOLOGY  # no fix = connectivity issue
            if fix < 2:
                error_mask |= MASK_RESOURCE   # poor fix

            # Velocity from satellite count (capped at 127)
            velocity = min(sats * 8, 127)

            # Position event (with CC pairs for lat/lon)
            cc_pairs = []
            lat = data.get('lat')
            lon = data.get('lon')
            if lat is not None:
                # Encode lat as two CC values (high byte, low byte)
                lat_scaled = int((lat + 90) * 1000)  # offset to positive, scale
                cc_pairs.append((16, (lat_scaled >> 7) & 0x7F))  # CC#16 = lat high
                cc_pairs.append((17, lat_scaled & 0x7F))          # CC#17 = lat low
            if lon is not None:
                lon_scaled = int((lon + 180) * 1000)
                cc_pairs.append((18, (lon_scaled >> 7) & 0x7F))  # CC#18 = lon high
                cc_pairs.append((19, lon_scaled & 0x7F))          # CC#19 = lon low

            events.append(SwmidiEvent(
                status=SwmidiEvent.NOTE_ON | self.channel,
                pitch=PITCH_GPS_POSITION,
                velocity=velocity,
                error_mask=error_mask,
                tick=self._next_tick(),
                cc_pairs=tuple(cc_pairs),
                source=sentence_type,
                raw_data=raw,
            ))

            # Fix quality event
            events.append(SwmidiEvent(
                status=SwmidiEvent.NOTE_ON | self.channel,
                pitch=PITCH_FIX_QUALITY,
                velocity=min(fix * 32, 127),
                error_mask=0,
                tick=self._next_tick(),
                source=sentence_type,
                raw_data=raw,
            ))

            # Satellite count event
            events.append(SwmidiEvent(
                status=SwmidiEvent.NOTE_ON | self.channel,
                pitch=PITCH_SATELLITE_COUNT,
                velocity=min(sats * 8, 127),
                error_mask=0,
                tick=self._next_tick(),
                source=sentence_type,
                raw_data=raw,
            ))

        elif 'sog' in data:
            # RMC: speed/course
            sog = data.get('sog', 0) or 0.0  # safe default for NaN/None
            cog = data.get('cog', 0) or 0.0
            status = data.get('status', '')

            # Extra guard: if somehow NaN slipped through, replace with 0
            if math.isnan(sog) or math.isinf(sog):
                sog = 0.0
            if math.isnan(cog) or math.isinf(cog):
                cog = 0.0

            error_mask = 0
            if status == 'V':  # V = warning, data invalid
                error_mask |= MASK_SEMANTIC

            # Speed over ground (knots → velocity, 0-127)
            events.append(SwmidiEvent(
                status=SwmidiEvent.NOTE_ON | self.channel,
                pitch=PITCH_GPS_SOG,
                velocity=min(int(sog * 5), 127),
                error_mask=error_mask,
                tick=self._next_tick(),
                source=sentence_type,
                raw_data=raw,
            ))

            # Course over ground (degrees → CC pair)
            cog_scaled = int(cog * 1000) % 360000
            events.append(SwmidiEvent(
                status=SwmidiEvent.NOTE_ON | self.channel,
                pitch=PITCH_GPS_COG,
                velocity=min(int(sog * 5), 127),
                error_mask=error_mask,
                tick=self._next_tick(),
                cc_pairs=((20, (cog_scaled >> 7) & 0x7F), (21, cog_scaled & 0x7F)),
                source=sentence_type,
                raw_data=raw,
            ))

        elif 'depth_meters' in data:
            # DBT: depth
            depth = data.get('depth_meters')
            if depth is not None and not (math.isnan(depth) or math.isinf(depth)):
                error_mask = 0
                if depth < 0:
                    error_mask |= MASK_SEMANTIC  # negative depth = sensor error
                if depth < 2:
                    error_mask |= MASK_SAFETY  # shallow water warning

                # Velocity inversely scaled: deeper = quieter signal
                velocity = min(int(127 / (1 + depth / 10)), 127)

                # Depth in CC pairs (high/low bytes, in centimeters)
                depth_cm = int(abs(depth) * 100)
                cc_pairs = (
                    (22, (depth_cm >> 14) & 0x7F),
                    (23, (depth_cm >> 7) & 0x7F),
                    (24, depth_cm & 0x7F),
                )

                events.append(SwmidiEvent(
                    status=SwmidiEvent.NOTE_ON | self.channel,
                    pitch=PITCH_DEPTH,
                    velocity=velocity,
                    error_mask=error_mask,
                    tick=self._next_tick(),
                    cc_pairs=cc_pairs,
                    source=sentence_type,
                    raw_data=raw,
                ))

        elif 'heading_true' in data:
            # HDT: heading
            heading = data.get('heading_true')
            if heading is not None:
                heading_scaled = int(heading * 1000) % 360000
                events.append(SwmidiEvent(
                    status=SwmidiEvent.NOTE_ON | self.channel,
                    pitch=PITCH_HEADING_TRUE,
                    velocity=127,  # heading is always reliable
                    error_mask=0,
                    tick=self._next_tick(),
                    cc_pairs=(
                        (25, (heading_scaled >> 7) & 0x7F),
                        (26, heading_scaled & 0x7F),
                    ),
                    source=sentence_type,
                    raw_data=raw,
                ))

        return events

    def parse_stream(self, sentences: list[str]) -> list[SwmidiEvent]:
        """Parse multiple NMEA sentences in sequence."""
        results = []
        for sentence in sentences:
            results.extend(self.parse(sentence))
        return results

    def pack_events(self, events: list[SwmidiEvent]) -> bytes:
        """Pack a list of events into binary (4-byte count + N×8 bytes)."""
        buf = struct.pack("<I", len(events))
        for event in events:
            buf += event.pack()
        return buf
