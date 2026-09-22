"""Tests for the Ledger-Organizing Graph (LOG) module."""

import json

import pytest

from cns_bridge.log_graph import ConsequenceEdge, DecisionNode, LedgerGraph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_node(**kw) -> DecisionNode:
    defaults: dict = {"agent_id": "alpha", "decision_type": "inference"}
    defaults.update(kw)
    return DecisionNode(**defaults)


@pytest.fixture
def linear_chain() -> LedgerGraph:
    """A → B → C linear parent chain."""
    g = LedgerGraph()
    a = g.add_decision(_make_node(agent_id="A", decision_type="root"))
    b = g.add_decision(_make_node(agent_id="B", parent_id=a.node_id))
    c = g.add_decision(_make_node(agent_id="C", parent_id=b.node_id))
    return g


@pytest.fixture
def branched_graph() -> LedgerGraph:
    """Root with two children; each child has one grandchild."""
    g = LedgerGraph()
    root = g.add_decision(_make_node(agent_id="root"))
    c1 = g.add_decision(_make_node(agent_id="c1", parent_id=root.node_id))
    c2 = g.add_decision(_make_node(agent_id="c2", parent_id=root.node_id))
    g.add_decision(_make_node(agent_id="g1", parent_id=c1.node_id))
    g.add_decision(_make_node(agent_id="g2", parent_id=c2.node_id))
    return g


# ---------------------------------------------------------------------------
# DecisionNode
# ---------------------------------------------------------------------------


class TestDecisionNode:
    def test_auto_ids_unique(self) -> None:
        n1 = _make_node()
        n2 = _make_node()
        assert n1.node_id != n2.node_id

    def test_auto_timestamp(self) -> None:
        n = _make_node()
        assert n.timestamp  # non-empty
        # ISO-8601 sanity check
        assert "T" in n.timestamp

    def test_defaults(self) -> None:
        n = _make_node()
        assert n.confidence == 1.0
        assert n.parent_id is None
        assert n.input_hash == ""
        assert n.output_hash == ""
        assert n.metadata == {}

    def test_roundtrip(self) -> None:
        n = _make_node(
            agent_id="x",
            decision_type="escalation",
            input_hash="abc",
            output_hash="def",
            confidence=0.42,
            metadata={"layer": 2},
        )
        restored = DecisionNode.from_dict(n.to_dict())
        assert restored.agent_id == n.agent_id
        assert restored.decision_type == n.decision_type
        assert restored.input_hash == n.input_hash
        assert restored.output_hash == n.output_hash
        assert restored.confidence == n.confidence
        assert restored.metadata == n.metadata
        assert restored.node_id == n.node_id

    def test_frozen(self) -> None:
        n = _make_node()
        with pytest.raises(Exception):  # FrozenInstanceError
            n.agent_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ConsequenceEdge
# ---------------------------------------------------------------------------


class TestConsequenceEdge:
    def test_defaults(self) -> None:
        e = ConsequenceEdge(source_node="s", target_node="t")
        assert e.edge_type == "caused"
        assert e.weight == 1.0

    def test_roundtrip(self) -> None:
        e = ConsequenceEdge(
            source_node="s",
            target_node="t",
            edge_type="escalated_to",
            weight=0.7,
        )
        r = ConsequenceEdge.from_dict(e.to_dict())
        assert r.source_node == e.source_node
        assert r.target_node == e.target_node
        assert r.edge_type == e.edge_type
        assert r.weight == e.weight

    def test_frozen(self) -> None:
        e = ConsequenceEdge(source_node="s", target_node="t")
        with pytest.raises(Exception):  # FrozenInstanceError
            e.weight = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LedgerGraph — add_decision
# ---------------------------------------------------------------------------


class TestAddDecision:
    def test_single_node(self) -> None:
        g = LedgerGraph()
        node = g.add_decision(_make_node())
        assert node.node_id in g
        assert len(g) == 1

    def test_parent_link_created(self) -> None:
        g = LedgerGraph()
        parent = g.add_decision(_make_node())
        child = g.add_decision(_make_node(parent_id=parent.node_id))
        assert child.node_id in g.descendants(parent.node_id)
        assert parent.node_id in g.ancestors(child.node_id)

    def test_parent_unknown_does_not_crash(self) -> None:
        """A parent_id pointing to a non-existent node is silently ignored."""
        g = LedgerGraph()
        node = g.add_decision(_make_node(parent_id="nonexistent"))
        assert node.node_id in g
        assert g.ancestors(node.node_id) == []

    def test_add_decision_returns_same_node(self) -> None:
        g = LedgerGraph()
        node = _make_node()
        assert g.add_decision(node) is node


# ---------------------------------------------------------------------------
# LedgerGraph — add_consequence
# ---------------------------------------------------------------------------


class TestAddConsequence:
    def test_happy_path(self) -> None:
        g = LedgerGraph()
        a = g.add_decision(_make_node())
        b = g.add_decision(_make_node())
        edge = g.add_consequence(
            ConsequenceEdge(source_node=a.node_id, target_node=b.node_id)
        )
        assert edge.edge_id in g.edges

    def test_unknown_source_raises(self) -> None:
        g = LedgerGraph()
        b = g.add_decision(_make_node())
        with pytest.raises(ValueError, match="source_node"):
            g.add_consequence(
                ConsequenceEdge(source_node="ghost", target_node=b.node_id)
            )

    def test_unknown_target_raises(self) -> None:
        g = LedgerGraph()
        a = g.add_decision(_make_node())
        with pytest.raises(ValueError, match="target_node"):
            g.add_consequence(
                ConsequenceEdge(source_node=a.node_id, target_node="ghost")
            )


# ---------------------------------------------------------------------------
# LedgerGraph — ancestors / descendants
# ---------------------------------------------------------------------------


class TestTraversal:
    def test_ancestors_linear(self, linear_chain: LedgerGraph) -> None:
        nodes = list(linear_chain.nodes.values())
        c = nodes[2]
        anc = linear_chain.ancestors(c.node_id)
        assert len(anc) == 2
        assert nodes[0].node_id in anc
        assert nodes[1].node_id in anc

    def test_descendants_linear(self, linear_chain: LedgerGraph) -> None:
        nodes = list(linear_chain.nodes.values())
        a = nodes[0]
        desc = linear_chain.descendants(a.node_id)
        assert len(desc) == 2
        assert nodes[1].node_id in desc
        assert nodes[2].node_id in desc

    def test_descendants_branched(self, branched_graph: LedgerGraph) -> None:
        root_id = next(
            nid for nid, n in branched_graph.nodes.items()
            if n.agent_id == "root"
        )
        desc = branched_graph.descendants(root_id)
        assert len(desc) == 4  # two children + two grandchildren

    def test_ancestors_root_is_empty(self, linear_chain: LedgerGraph) -> None:
        nodes = list(linear_chain.nodes.values())
        assert linear_chain.ancestors(nodes[0].node_id) == []

    def test_unknown_node_raises_keyerror(self) -> None:
        g = LedgerGraph()
        with pytest.raises(KeyError):
            g.ancestors("ghost")
        with pytest.raises(KeyError):
            g.descendants("ghost")


# ---------------------------------------------------------------------------
# LedgerGraph — trace
# ---------------------------------------------------------------------------


class TestTrace:
    def test_trace_linear(self, linear_chain: LedgerGraph) -> None:
        nodes = list(linear_chain.nodes.values())
        path = linear_chain.trace(nodes[2].node_id)
        assert path[0] == nodes[2].node_id   # start at the outcome
        assert path[-1] == nodes[0].node_id  # end at the root
        assert len(path) == 3

    def test_trace_root_returns_self(self) -> None:
        g = LedgerGraph()
        root = g.add_decision(_make_node())
        path = g.trace(root.node_id)
        assert path == [root.node_id]

    def test_trace_with_consequence_edge(self) -> None:
        """trace should follow consequence edges as well as parent links."""
        g = LedgerGraph()
        a = g.add_decision(_make_node(agent_id="A"))
        b = g.add_decision(_make_node(agent_id="B", parent_id=a.node_id))
        c = g.add_decision(_make_node(agent_id="C", parent_id=b.node_id))
        # add a consequence edge from a → c (shortcut)
        g.add_consequence(
            ConsequenceEdge(source_node=a.node_id, target_node=c.node_id)
        )
        path = g.trace(c.node_id)
        # c now has two parents (b and a); trace should still reach a root
        assert path[0] == c.node_id
        assert path[-1] == a.node_id


# ---------------------------------------------------------------------------
# LedgerGraph — stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_empty_graph(self) -> None:
        s = LedgerGraph().stats()
        assert s["node_count"] == 0
        assert s["edge_count"] == 0
        assert s["density"] == 0.0
        assert s["avg_depth"] == 0.0
        assert s["orphan_rate"] == 0.0

    def test_single_node(self) -> None:
        g = LedgerGraph()
        g.add_decision(_make_node())
        s = g.stats()
        assert s["node_count"] == 1
        assert s["orphan_rate"] == 1.0  # no parent → orphan
        assert s["avg_depth"] == 0.0

    def test_linear_chain(self, linear_chain: LedgerGraph) -> None:
        s = linear_chain.stats()
        assert s["node_count"] == 3
        assert s["orphan_rate"] == pytest.approx(1 / 3)  # only root is orphan

    def test_density_with_edges(self) -> None:
        g = LedgerGraph()
        a = g.add_decision(_make_node())
        b = g.add_decision(_make_node())
        c = g.add_decision(_make_node())
        g.add_consequence(ConsequenceEdge(source_node=a.node_id, target_node=b.node_id))
        g.add_consequence(ConsequenceEdge(source_node=b.node_id, target_node=c.node_id))
        s = g.stats()
        assert s["edge_count"] == 2
        # density = 2 / (3*2) = 0.333...
        assert s["density"] == pytest.approx(2 / 6, abs=1e-4)


# ---------------------------------------------------------------------------
# LedgerGraph — serialisation
# ---------------------------------------------------------------------------


class TestSerialisation:
    def test_roundtrip(self, branched_graph: LedgerGraph) -> None:
        raw = branched_graph.to_json()
        restored = LedgerGraph.from_json(raw)
        assert len(restored) == len(branched_graph)
        assert restored.stats() == branched_graph.stats()

    def test_roundtrip_with_edges(self) -> None:
        g = LedgerGraph()
        a = g.add_decision(_make_node(agent_id="A"))
        b = g.add_decision(_make_node(agent_id="B"))
        g.add_consequence(
            ConsequenceEdge(source_node=a.node_id, target_node=b.node_id, weight=0.5)
        )
        raw = g.to_json()
        restored = LedgerGraph.from_json(raw)
        assert len(restored.edges) == 1
        edge = list(restored.edges.values())[0]
        assert edge.weight == 0.5

    def test_to_json_is_valid_json(self) -> None:
        g = LedgerGraph()
        g.add_decision(_make_node())
        data = json.loads(g.to_json())
        assert "nodes" in data
        assert "edges" in data

    def test_from_json_preserves_metadata(self) -> None:
        g = LedgerGraph()
        g.add_decision(_make_node(metadata={"layer": 3, "model": "glm-5.2"}))
        raw = g.to_json()
        restored = LedgerGraph.from_json(raw)
        node = list(restored.nodes.values())[0]
        assert node.metadata["layer"] == 3
        assert node.metadata["model"] == "glm-5.2"


# ---------------------------------------------------------------------------
# Integration: replay scenario
# ---------------------------------------------------------------------------


class TestReplayScenario:
    def test_full_agent_session_replay(self) -> None:
        """Simulate a multi-step agent session and verify the ledger
        captures every decision traceably."""
        g = LedgerGraph()

        # Step 1: router decides which model to use
        routing = g.add_decision(
            _make_node(
                agent_id="router",
                decision_type="routing",
                input_hash="q1hash",
                output_hash="glm52",
                confidence=0.9,
                metadata={"query": "translate", "chosen_model": "glm-5.2"},
            )
        )

        # Step 2: GLM-5.2 produces output
        inference = g.add_decision(
            _make_node(
                agent_id="glm-5.2",
                decision_type="inference",
                input_hash="q1hash",
                output_hash="translated",
                parent_id=routing.node_id,
                confidence=0.95,
                metadata={"tokens": 42},
            )
        )

        # Step 3: safety check on the output
        safety = g.add_decision(
            _make_node(
                agent_id="safety-filter",
                decision_type="safety_check",
                input_hash="translated",
                output_hash="pass",
                parent_id=inference.node_id,
                confidence=1.0,
            )
        )

        # Step 4: consequence — routing decision influenced safety directly
        g.add_consequence(
            ConsequenceEdge(
                source_node=routing.node_id,
                target_node=safety.node_id,
                edge_type="influenced",
                weight=0.3,
            )
        )

        # Trace safety → root
        path = g.trace(safety.node_id)
        assert path[0] == safety.node_id
        assert routing.node_id in path

        # Stats
        s = g.stats()
        assert s["node_count"] == 3
        assert s["edge_count"] == 1
        assert s["orphan_rate"] == pytest.approx(1 / 3)

        # Full roundtrip
        restored = LedgerGraph.from_json(g.to_json())
        assert restored.stats() == s
        restored_path = restored.trace(safety.node_id)
        assert restored_path == path
