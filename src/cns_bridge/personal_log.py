"""PersonalLOG.AI — a personal memory layer for multi-agent fleets.

Wraps :class:`LedgerGraph` with a high-level API for recording agent decisions,
querying daily summaries, tracing causal trails, and exporting to JSON for
Cloudflare Worker consumption.

Design Principles
-----------------
1. **Every decision is a node** — no compaction, no summarising, no forgetting.
2. **The graph IS the memory** — structure over representation.
3. **Any agent can record** — GLM, Claude, DeepSeek, KimiCode, human, or a
   deterministic bot.  All are equal citizens in the ledger.
4. **Trails are bidirectional** — trace forward from request to outcome, or
   backward from outcome to root cause.

Example
-------
    from cns_bridge.personal_log import PersonalLog

    log = PersonalLog()
    req = log.record("human", "request", input="build a castle",
                     output="player wants a castle", confidence=1.0)
    plan = log.record("glm-5.2", "planning", parent_id=req.node_id,
                      input="build a castle", output="8x8 stone structure",
                      confidence=0.9)
    cmd = log.record("kimicode", "build_command", parent_id=plan.node_id,
                     input="8x8 stone structure", output="Place Part ...",
                     confidence=0.85)

    print(log.decision_trail(cmd.node_id))
    print(log.daily_summary())
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .log_graph import ConsequenceEdge, DecisionNode, LedgerGraph


# ---------------------------------------------------------------------------
# PersonalLog
# ---------------------------------------------------------------------------


class PersonalLog:
    """High-level wrapper around :class:`LedgerGraph` for fleet memory.

    Records decisions from any agent, organises them into a causal graph,
    and provides query methods for daily summaries, decision trails, and
    JSON export.
    """

    def __init__(self, graph: LedgerGraph | None = None) -> None:
        self.graph = graph or LedgerGraph()
        # Track insertion order for daily summary windowing.
        self._order: list[str] = []

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        agent_id: str,
        decision_type: str,
        *,
        input: str = "",
        output: str = "",
        confidence: float = 1.0,
        parent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DecisionNode:
        """Record a single decision in the ledger.

        Parameters
        ----------
        agent_id :
            Which agent made the decision (e.g. ``"glm-5.2"``, ``"human"``).
        decision_type :
            Category label — ``"request"``, ``"planning"``, ``"routing"``,
            ``"build_command"``, ``"escalation"``, etc.
        input :
            Human-readable description of the input to the decision.
        output :
            Human-readable description of the decision output.
        confidence :
            Agent-reported confidence in [0.0, 1.0].
        parent_id :
            Node id of the parent decision (creates a causal link).
        metadata :
            Arbitrary extra data to attach to the node.

        Returns
        -------
        DecisionNode
            The recorded node (with generated ``node_id`` and ``timestamp``).
        """
        node = DecisionNode(
            agent_id=agent_id,
            decision_type=decision_type,
            input_hash=_short_hash(input),
            output_hash=_short_hash(output),
            confidence=confidence,
            parent_id=parent_id,
            metadata={
                "input": input,
                "output": output,
                **(metadata or {}),
            },
        )
        self.graph.add_decision(node)
        self._order.append(node.node_id)
        return node

    def link(
        self,
        source_id: str,
        target_id: str,
        edge_type: str = "caused",
        weight: float = 1.0,
    ) -> ConsequenceEdge:
        """Create a consequence edge between two existing decisions."""
        edge = ConsequenceEdge(
            source_node=source_id,
            target_node=target_id,
            edge_type=edge_type,
            weight=weight,
        )
        self.graph.add_consequence(edge)
        return edge

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def daily_summary(self, date: str | None = None) -> dict[str, Any]:
        """Summarise what the fleet decided today (or on *date*).

        Parameters
        ----------
        date :
            ISO date string ``"YYYY-MM-DD"``.  Defaults to today (UTC).

        Returns
        -------
        dict with keys:
            - ``date`` — the date queried
            - ``total_decisions`` — count of decisions that day
            - ``by_agent`` — {agent_id: count}
            - ``by_type`` — {decision_type: count}
            - ``avg_confidence`` — mean confidence across decisions
            - ``escalations`` — count of escalation-type decisions
            - ``top_chain`` — longest decision trail of the day
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        day_nodes = [
            self.graph.nodes[nid]
            for nid in self._order
            if self.graph.nodes[nid].timestamp.startswith(date)
        ]

        if not day_nodes:
            return {
                "date": date,
                "total_decisions": 0,
                "by_agent": {},
                "by_type": {},
                "avg_confidence": 0.0,
                "escalations": 0,
                "top_chain": [],
            }

        by_agent = Counter(n.agent_id for n in day_nodes)
        by_type = Counter(n.decision_type for n in day_nodes)
        avg_conf = sum(n.confidence for n in day_nodes) / len(day_nodes)
        escalations = sum(1 for n in day_nodes if n.decision_type == "escalation")

        # Find the longest trail among today's nodes.
        top_chain: list[str] = []
        for n in day_nodes:
            trail = self.decision_trail(n.node_id)
            if len(trail) > len(top_chain):
                top_chain = trail

        # Resolve node ids to readable summaries for the chain.
        readable_chain = [
            {
                "agent": self.graph.nodes[nid].agent_id,
                "type": self.graph.nodes[nid].decision_type,
                "output": self.graph.nodes[nid].metadata.get("output", ""),
            }
            for nid in top_chain
            if nid in self.graph.nodes
        ]

        return {
            "date": date,
            "total_decisions": len(day_nodes),
            "by_agent": dict(by_agent),
            "by_type": dict(by_type),
            "avg_confidence": round(avg_conf, 4),
            "escalations": escalations,
            "top_chain": readable_chain,
        }

    def decision_trail(self, node_id: str) -> list[str]:
        """Trace from an outcome back to the original request.

        Returns a list of node ids ordered from *node_id* (the outcome)
        back through each ancestor to the root request.
        """
        return self.graph.trace(node_id)

    def decision_trail_readable(self, node_id: str) -> list[dict[str, Any]]:
        """Like :meth:`decision_trail` but returns rich dicts."""
        trail_ids = self.decision_trail(node_id)
        result = []
        for nid in trail_ids:
            node = self.graph.nodes[nid]
            result.append({
                "node_id": nid,
                "agent": node.agent_id,
                "type": node.decision_type,
                "input": node.metadata.get("input", ""),
                "output": node.metadata.get("output", ""),
                "confidence": node.confidence,
                "timestamp": node.timestamp,
            })
        return result

    def filter_by_agent(self, agent_id: str) -> list[DecisionNode]:
        """Return all decisions made by *agent_id*."""
        return [
            n for n in self.graph.nodes.values()
            if n.agent_id == agent_id
        ]

    def filter_by_type(self, decision_type: str) -> list[DecisionNode]:
        """Return all decisions of *decision_type*."""
        return [
            n for n in self.graph.nodes.values()
            if n.decision_type == decision_type
        ]

    def get_node(self, node_id: str) -> DecisionNode:
        """Retrieve a single node by id."""
        return self.graph.nodes[node_id]

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_json(self, indent: int | None = 2) -> str:
        """Serialise the entire ledger to JSON for Worker consumption.

        The output schema is intentionally flat and self-describing so a
        Cloudflare Worker can ingest it without Python-specific types::

            {
              "summary": {...},
              "nodes": [...],
              "edges": [...]
            }
        """
        payload = {
            "summary": self.daily_summary(),
            "stats": self.graph.stats(),
            "nodes": [n.to_dict() for n in self.graph.nodes.values()],
            "edges": [e.to_dict() for e in self.graph.edges.values()],
        }
        return json.dumps(payload, indent=indent, default=str)

    def to_dict(self) -> dict[str, Any]:
        """Return the ledger as a dictionary (alias for export structure)."""
        return json.loads(self.export_json())

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.graph)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self.graph

    def __repr__(self) -> str:
        return f"PersonalLog(decisions={len(self)}, edges={len(self.graph.edges)})"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_hash(text: str) -> str:
    """Generate a short deterministic hash for input/output text."""
    if not text:
        return ""
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:12]
