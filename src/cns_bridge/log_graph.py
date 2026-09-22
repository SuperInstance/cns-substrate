"""Ledger-Organizing Graph (LOG) — inspectable, traceable agent decision records.

A LOG stores every agent decision as a node in a directed graph.  Consequences
flow along typed edges.  Because the ledger never forgets, any outcome can be
traced back through its causal chain, replayed, and audited.

Core insight: memory is **structural** (connections between nodes) rather than
representational (facts in a database).  See SUPERINSTANCE_AI.md.

Public API
----------
``DecisionNode``      — an immutable record of a single decision.
``ConsequenceEdge``   — a typed, weighted link between two decisions.
``LedgerGraph``       — the graph container with query and serialisation helpers.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionNode:
    """One agent decision, immutable for its entire lifetime.

    Attributes
    ----------
    node_id :
        Globally unique identifier (auto-generated when omitted).
    timestamp :
        ISO-8601 UTC string; auto-generated when omitted.
    agent_id :
        Identifier of the agent that made the decision.
    decision_type :
        Free-form label categorising the decision (e.g. ``"routing"``,
        ``"inference"``, ``"escalation"``).
    input_hash :
        Short hash of the inputs that produced the decision.
    output_hash :
        Short hash of the decision output.
    parent_id :
        Node id of the parent decision, or ``None`` for a root.
    confidence :
        Agent-reported confidence in the decision (0.0–1.0).
    metadata :
        Arbitrary key/value payload for domain-specific enrichment.
    """

    agent_id: str
    decision_type: str
    input_hash: str = ""
    output_hash: str = ""
    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    parent_id: str | None = None
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionNode":
        return cls(
            agent_id=data["agent_id"],
            decision_type=data["decision_type"],
            input_hash=data.get("input_hash", ""),
            output_hash=data.get("output_hash", ""),
            node_id=data.get("node_id", str(uuid.uuid4())),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            parent_id=data.get("parent_id"),
            confidence=data.get("confidence", 1.0),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class ConsequenceEdge:
    """A typed, weighted edge linking two decisions.

    Attributes
    ----------
    edge_id :
        Globally unique identifier (auto-generated when omitted).
    source_node :
        Node id of the cause / parent side.
    target_node :
        Node id of the effect / child side.
    edge_type :
        Free-form label (e.g. ``"caused"``, ``"influenced"``,
        ``"escalated_to"``).
    weight :
        Strength of the relationship (0.0–1.0, default 1.0).
    timestamp :
        ISO-8601 UTC string; auto-generated when omitted.
    """

    source_node: str
    target_node: str
    edge_type: str = "caused"
    edge_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    weight: float = 1.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsequenceEdge":
        return cls(
            source_node=data["source_node"],
            target_node=data["target_node"],
            edge_type=data.get("edge_type", "caused"),
            edge_id=data.get("edge_id", str(uuid.uuid4())),
            weight=data.get("weight", 1.0),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )


# ---------------------------------------------------------------------------
# LedgerGraph
# ---------------------------------------------------------------------------


class LedgerGraph:
    """In-memory directed graph of agent decisions and their consequences.

    The graph supports two edge semantics:

    * **parent links** (via ``DecisionNode.parent_id``) — the primary
      hierarchy of decisions.
    * **consequence edges** (via ``add_consequence``) — cross-cutting or
      after-the-fact causal relationships.

    Both are unified in ``ancestors`` / ``descendants`` / ``trace``.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, DecisionNode] = {}
        self._edges: dict[str, ConsequenceEdge] = {}
        # Adjacency lists (include both parent links and consequence edges).
        self._children: dict[str, set[str]] = defaultdict(set)
        self._parents: dict[str, set[str]] = defaultdict(set)

    # -- properties ---------------------------------------------------------

    @property
    def nodes(self) -> dict[str, DecisionNode]:
        """Read-only mapping of node_id → DecisionNode."""
        return dict(self._nodes)

    @property
    def edges(self) -> dict[str, ConsequenceEdge]:
        """Read-only mapping of edge_id → ConsequenceEdge."""
        return dict(self._edges)

    # -- mutation -----------------------------------------------------------

    def add_decision(self, node: DecisionNode) -> DecisionNode:
        """Register *node* in the graph.

        If ``node.parent_id`` refers to an existing node, a parent→child
        adjacency is created automatically.

        Returns the node as stored (same object).
        """
        self._nodes[node.node_id] = node
        if node.parent_id and node.parent_id in self._nodes:
            self._link(node.parent_id, node.node_id)
        return node

    def add_consequence(self, edge: ConsequenceEdge) -> ConsequenceEdge:
        """Register a consequence edge.

        Both endpoints must already exist in the graph
        (``ValueError`` otherwise).
        """
        if edge.source_node not in self._nodes:
            raise ValueError(f"Unknown source_node: {edge.source_node!r}")
        if edge.target_node not in self._nodes:
            raise ValueError(f"Unknown target_node: {edge.target_node!r}")
        self._edges[edge.edge_id] = edge
        self._link(edge.source_node, edge.target_node)
        return edge

    def _link(self, parent: str, child: str) -> None:
        self._children[parent].add(child)
        self._parents[child].add(parent)

    # -- queries ------------------------------------------------------------

    def ancestors(self, node_id: str) -> list[str]:
        """Return all ancestor node ids (transitive parents), BFS order.

        Raises ``KeyError`` if *node_id* is unknown.
        """
        self._require(node_id)
        seen: set[str] = set()
        queue: deque[str] = deque(self._parents.get(node_id, set()))
        while queue:
            nid = queue.popleft()
            if nid in seen:
                continue
            seen.add(nid)
            queue.extend(self._parents.get(nid, set()))
        return sorted(seen)

    def descendants(self, node_id: str) -> list[str]:
        """Return all descendant node ids (transitive children), BFS order.

        Raises ``KeyError`` if *node_id* is unknown.
        """
        self._require(node_id)
        seen: set[str] = set()
        queue: deque[str] = deque(self._children.get(node_id, set()))
        while queue:
            nid = queue.popleft()
            if nid in seen:
                continue
            seen.add(nid)
            queue.extend(self._children.get(nid, set()))
        return sorted(seen)

    def trace(self, node_id: str) -> list[str]:
        """Trace an outcome back to its root cause(s).

        Returns a list of node ids ordered from *node_id* (the outcome)
        up through each ancestor until a root is reached.

        When multiple roots exist, the path through the earliest-registered
        ancestor is chosen (deterministic).

        Raises ``KeyError`` if *node_id* is unknown.
        """
        self._require(node_id)
        path: list[str] = [node_id]
        current = node_id
        while True:
            parents = self._parents.get(current, set())
            if not parents:
                break
            # Pick the parent that was registered first (smallest insertion
            # order).  Because _nodes is a regular dict, iteration order is
            # insertion order.
            next_parent = min(parents, key=lambda nid: list(self._nodes).index(nid))
            if next_parent in path:  # cycle guard
                break
            path.append(next_parent)
            current = next_parent
        return path

    # -- statistics ---------------------------------------------------------

    def stats(self) -> dict[str, float | int]:
        """Return summary statistics about the graph.

        Keys
        ----
        node_count : int
        edge_count : int  (consequence edges only)
        density : float  — edges / max_possible_edges
        avg_depth : float  — average distance from each node to its root(s)
        orphan_rate : float  — fraction of nodes with no parent (0.0–1.0)
        """
        n = len(self._nodes)
        edge_count = len(self._edges)
        if n <= 1:
            density = 0.0
        else:
            max_edges = n * (n - 1)  # directed, no self-loops
            density = edge_count / max_edges if max_edges else 0.0

        depths = [self._depth(nid) for nid in self._nodes]
        avg_depth = sum(depths) / n if n else 0.0

        orphans = sum(
            1
            for nid in self._nodes
            if not self._parents.get(nid) and self._nodes[nid].parent_id is None
        )
        orphan_rate = orphans / n if n else 0.0

        return {
            "node_count": n,
            "edge_count": edge_count,
            "density": round(density, 6),
            "avg_depth": round(avg_depth, 6),
            "orphan_rate": round(orphan_rate, 6),
        }

    def _depth(self, node_id: str) -> int:
        """Longest path from *node_id* to a root."""
        depth = 0
        current = node_id
        visited: set[str] = {current}
        while True:
            parents = self._parents.get(current, set())
            if not parents:
                break
            # advance to the parent that gives the longest path
            best = None
            best_depth = -1
            for p in parents:
                if p in visited:
                    continue
                d = self._depth(p)
                if d > best_depth:
                    best_depth = d
                    best = p
            if best is None:
                break
            visited.add(best)
            depth += 1 + best_depth
            break
        return depth

    # -- serialisation ------------------------------------------------------

    def to_json(self, indent: int | None = None) -> str:
        """Serialise the entire graph to JSON."""
        return json.dumps(self.to_dict(), indent=indent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LedgerGraph":
        graph = cls()
        for nd in data.get("nodes", []):
            graph.add_decision(DecisionNode.from_dict(nd))
        for ed in data.get("edges", []):
            graph.add_consequence(ConsequenceEdge.from_dict(ed))
        return graph

    @classmethod
    def from_json(cls, raw: str) -> "LedgerGraph":
        return cls.from_dict(json.loads(raw))

    # -- internal -----------------------------------------------------------

    def _require(self, node_id: str) -> None:
        if node_id not in self._nodes:
            raise KeyError(f"Unknown node: {node_id!r}")

    # -- dunder -------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self._nodes

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"LedgerGraph(nodes={len(self._nodes)}, "
            f"edges={len(self._edges)})"
        )
