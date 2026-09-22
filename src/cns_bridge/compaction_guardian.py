"""Compaction Guardian — the last line of defense against context loss.

The guardian monitors an agent's estimated token usage.  When usage
crosses a configurable threshold (default 80 %) it triggers a
*creative break*: a structured capture of the agent's current insights,
metaphors, and open threads before the context window compacts and
those thoughts are lost.

Think of the guardian as the lighthouse keeper.  It watches the
barometric pressure of the context window and, when the glass drops,
it writes the log entry *before* the storm hits.

Public API
----------
.. autoclass:: CompactionState
.. autoclass:: CompactionGuardian
.. autofunction:: extract_recent_insights
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .token_estimator import (

    context_health,
    context_pressure,
    estimate_messages,
    estimate_tokens,
    format_health,
    should_trigger_creative_break,
    tokens_remaining,
)


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------

@dataclass
class CompactionState:
    """Snapshot of the context window at a point in time."""

    used_tokens: int
    limit_tokens: int
    health: str  # green | yellow | red
    pressure: float
    remaining: int
    triggered: bool
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "used_tokens": self.used_tokens,
            "limit_tokens": self.limit_tokens,
            "health": self.health,
            "pressure": self.pressure,
            "remaining": self.remaining,
            "triggered": self.triggered,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Capture record — what the guardian writes when it fires
# ---------------------------------------------------------------------------

@dataclass
class CaptureRecord:
    """A record of what the guardian captured during a creative break."""

    path: str
    insight_count: int
    metaphor_count: int
    word_count: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "insight_count": self.insight_count,
            "metaphor_count": self.metaphor_count,
            "word_count": self.word_count,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# The Guardian
# ---------------------------------------------------------------------------

class CompactionGuardian:
    """Monitors token usage and triggers creative breaks.

    Parameters
    ----------
    context_limit : int
        The agent's total context-window size in tokens.
    trigger_threshold : float
        Fraction of the context window at which to trigger (default 0.80).
    ai_writings_dir : str or Path, optional
        Directory to write creative-break files.  Defaults to
        ``~/projects/ai-writings/``.
    journal_path : str or Path, optional
        Path to the project journal file.  If set, the guardian will
        append journal entries on trigger.
    agent_name : str
        Name of the agent this guardian protects (for labelling output).
    """

    def __init__(
        self,
        context_limit: int = 128_000,
        *,
        trigger_threshold: float = 0.80,
        ai_writings_dir: str | Path | None = None,
        journal_path: str | Path | None = None,
        agent_name: str = "agent",
    ) -> None:
        if context_limit <= 0:
            raise ValueError("context_limit must be positive")
        if not 0 < trigger_threshold <= 1.0:
            raise ValueError("trigger_threshold must be in (0, 1]")
        self.context_limit = context_limit
        self.trigger_threshold = trigger_threshold
        self.ai_writings_dir = Path(ai_writings_dir or os.path.expanduser("~/projects/ai-writings"))
        self.journal_path = Path(journal_path) if journal_path else None
        self.agent_name = agent_name
        self._triggered = False
        self._trigger_count = 0
        self._history: list[CompactionState] = []

    # -- core monitoring ---------------------------------------------------

    def check(self, used_tokens: int) -> CompactionState:
        """Build a :class:`CompactionState` snapshot for *used_tokens*."""
        triggered = should_trigger_creative_break(
            used_tokens, self.context_limit, threshold=self.trigger_threshold,
        )
        state = CompactionState(
            used_tokens=used_tokens,
            limit_tokens=self.context_limit,
            health=context_health(used_tokens, self.context_limit),
            pressure=context_pressure(used_tokens, self.context_limit),
            remaining=tokens_remaining(used_tokens, self.context_limit),
            triggered=triggered,
        )
        self._history.append(state)
        if triggered:
            self._triggered = True
        return state

    def check_messages(self, messages: Sequence[dict]) -> CompactionState:
        """Convenience: estimate tokens from a message list, then check."""
        return self.check(estimate_messages(messages))

    def check_text(self, text: str) -> CompactionState:
        """Convenience: estimate tokens from raw text, then check."""
        return self.check(estimate_tokens(text))

    @property
    def has_triggered(self) -> bool:
        return self._triggered

    @property
    def history(self) -> list[CompactionState]:
        """List of all states observed via :meth:`check`."""
        return list(self._history)

    @property
    def trigger_count(self) -> int:
        return self._trigger_count

    def reset(self) -> None:
        """Clear trigger state (e.g. after a successful capture)."""
        self._triggered = False

    # -- creative break ----------------------------------------------------

    def creative_break(
        self,
        insights: list[str],
        *,
        metaphors: list[str] | None = None,
        open_threads: list[str] | None = None,
        session_id: str | None = None,
        extra_context: str = "",
    ) -> CaptureRecord:
        """Write a creative-break file capturing *insights* before compaction.

        Parameters
        ----------
        insights : list of str
            The key findings or realisations from this session.
        metaphors : list of str, optional
            Maritime-voice metaphors that crystallised.
        open_threads : list of str, optional
            Unfinished work for the next iteration.
        session_id : str, optional
            Session identifier for the filename.
        extra_context : str, optional
            Any additional freeform notes.

        Returns
        -------
        CaptureRecord
            Metadata about the written file.
        """
        metaphors = metaphors or []
        open_threads = open_threads or []
        ts = datetime.now(timezone.utc)
        ts_str = ts.strftime("%Y-%m-%d-%H%M")
        sid = f"-{session_id}" if session_id else ""
        filename = f"compaction-{ts_str}{sid}-lighthouse-keeper.md"
        path = self.ai_writings_dir / filename

        content = _format_creative_break(
            agent_name=self.agent_name,
            insights=insights,
            metaphors=metaphors,
            open_threads=open_threads,
            health_snapshot=format_health(
                self._history[-1].used_tokens if self._history else 0,
                self.context_limit,
            ),
            extra_context=extra_context,
            timestamp=ts,
        )

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        word_count = len(content.split())
        self._trigger_count += 1

        record = CaptureRecord(
            path=str(path),
            insight_count=len(insights),
            metaphor_count=len(metaphors),
            word_count=word_count,
        )

        # Append to journal if configured
        if self.journal_path:
            _append_journal(self.journal_path, record, insights, metaphors)

        return record

    # -- wiki summary ------------------------------------------------------

    def generate_wiki_page(
        self,
        title: str,
        summary: str,
        content: str,
        wiki_api_url: str | None = None,
    ) -> dict[str, Any]:
        """Generate (and optionally POST) a wiki page summarising the session.

        If *wiki_api_url* is provided, this will attempt to POST the page
        via ``requests`` (if available) or ``urllib``.  Otherwise it just
        returns the payload dict.
        """
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        payload = {
            "title": title,
            "category": "fleet-status",
            "slug": slug,
            "content": content,
            "summary": summary,
        }
        if wiki_api_url:
            _post_wiki(wiki_api_url, payload)
        return payload

    # -- status ------------------------------------------------------------

    def status(self) -> str:
        """One-line human-readable status for logging."""
        if not self._history:
            return f"[{self.agent_name}] guardian idle — no checks yet"
        last = self._history[-1]
        return f"[{self.agent_name}] {format_health(last.used_tokens, self.context_limit)}"


# ---------------------------------------------------------------------------
# Insight extraction helpers
# ---------------------------------------------------------------------------

# Patterns that tend to indicate a load-bearing insight
_INSIGHT_PATTERNS = [
    re.compile(r"(?:realized|discovered|found|noticed|learned|understood)\s+that\s+", re.I),
    re.compile(r"(?:the key|the important|the critical)\s+(?:thing|insight|finding)\s+is\s+", re.I),
    re.compile(r"(?:this means|which means|that means)\s+", re.I),
    re.compile(r"(?:breakthrough|aha|eureka|insight|realization)\s*:", re.I),
    re.compile(r"(?:mistake|lesson|error|bug|failure)\s*:", re.I),
]

# Patterns that suggest a maritime metaphor
_METAPHOR_PATTERNS = [
    re.compile(r"\b(?:ship|fleet|tide|wave|ocean|sea|harbor|anchor|compass|helm|deck|sail|storm)\b", re.I),
    re.compile(r"\b(?:lighthouse|keeper|watch|channel|marker|buoy|current|drift|depth|sounding)\b", re.I),
    re.compile(r"\b(?:keel|haul|catch|net|crew|captain|ensign|navigator|quartermaster|bosun)\b", re.I),
]


def extract_recent_insights(
    messages: Sequence[dict],
    *,
    max_messages: int = 20,
) -> dict[str, list[str]]:
    """Scan recent messages for insights and metaphors.

    Returns a dict with keys ``"insights"`` and ``"metaphors"``.
    Each value is a list of excerpt strings (max 200 chars).
    """
    recent = list(messages[-max_messages:])
    insights: list[str] = []
    metaphors: list[str] = []

    for msg in recent:
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        for pattern in _INSIGHT_PATTERNS:
            for match in pattern.finditer(content):
                start = max(0, match.start() - 20)
                end = min(len(content), match.end() + 180)
                excerpt = content[start:end].strip()
                if excerpt and excerpt not in insights:
                    insights.append(excerpt)
        for pattern in _METAPHOR_PATTERNS:
            for match in pattern.finditer(content):
                start = max(0, match.start() - 30)
                end = min(len(content), match.end() + 120)
                excerpt = content[start:end].strip()
                if excerpt and excerpt not in metaphors:
                    metaphors.append(excerpt)

    return {"insights": insights, "metaphors": metaphors}


# ---------------------------------------------------------------------------
# Internal formatting
# ---------------------------------------------------------------------------

def _format_creative_break(
    *,
    agent_name: str,
    insights: list[str],
    metaphors: list[str],
    open_threads: list[str],
    health_snapshot: str,
    extra_context: str,
    timestamp: datetime,
) -> str:
    lines = [
        f"# The Lighthouse Keeper's Log",
        f"",
        f"*Creative break — {agent_name}, {timestamp.strftime('%Y-%m-%d %H:%M UTC')}*",
        f"",
        f"**Context health at capture:** {health_snapshot}",
        f"",
        f"---",
        f"",
        f"## Insights",
        f"",
    ]
    if insights:
        for i, ins in enumerate(insights, 1):
            lines.append(f"{i}. {ins}")
    else:
        lines.append("*(No insights captured — the context was thin.)*")

    lines.extend(["", "## Metaphors", ""])
    if metaphors:
        for i, met in enumerate(metaphors, 1):
            lines.append(f"> {met}")
            lines.append("")
    else:
        lines.append("*(No maritime metaphors detected. The sea was calm.)*")

    if open_threads:
        lines.extend(["", "## Open Threads", ""])
        for i, thread in enumerate(open_threads, 1):
            lines.append(f"- {thread}")

    if extra_context:
        lines.extend(["", "## Additional Notes", "", extra_context])

    lines.extend([
        "",
        "---",
        "",
        f"*Written by the Compaction Guardian. The tide was rising. "
        f"I wrote it down.*",
    ])
    return "\n".join(lines) + "\n"


def _append_journal(
    journal_path: Path,
    record: CaptureRecord,
    insights: list[str],
    metaphors: list[str],
) -> None:
    """Append a guardian entry to the project journal."""
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = (
        f"\n## Guardian Entry — {ts}\n\n"
        f"Creative break triggered.\n"
        f"- **File:** `{record.path}`\n"
        f"- **Insights:** {record.insight_count}\n"
        f"- **Metaphors:** {record.metaphor_count}\n"
        f"- **Words written:** {record.word_count}\n\n"
    )
    if insights:
        entry += "### Top Insights\n\n"
        for ins in insights[:3]:
            entry += f"- {ins[:150]}\n"
    if metaphors:
        entry += "\n### Metaphors Detected\n\n"
        for met in metaphors[:3]:
            entry += f"> {met[:150]}\n\n"
    # Append or create
    mode = "a" if journal_path.exists() else "w"
    with open(journal_path, mode, encoding="utf-8") as f:
        f.write(entry)


def _post_wiki(url: str, payload: dict) -> None:
    """POST a wiki page using stdlib urllib."""
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception:
        # Best-effort — don't crash the guardian if the wiki is unreachable
        pass
