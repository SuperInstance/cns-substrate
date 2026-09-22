"""Tests for PersonalLOG.AI — PersonalLog module."""

import json
from datetime import datetime, timezone

import pytest

from cns_bridge.personal_log import PersonalLog
from cns_bridge.log_graph import DecisionNode, LedgerGraph


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_log() -> PersonalLog:
    return PersonalLog()


@pytest.fixture
def populated_log() -> PersonalLog:
    """A log with a 3-node chain and one standalone node."""
    log = PersonalLog()
    a = log.record("human", "request", input="build", output="want build", confidence=1.0)
    b = log.record("glm-5.2", "planning", parent_id=a.node_id,
                   input="build", output="plan", confidence=0.9)
    c = log.record("kimicode", "build_command", parent_id=b.node_id,
                   input="plan", output="parts", confidence=0.85)
    log.record("glm-5.2", "optimization", input="check perf", output="ok")
    return log


@pytest.fixture
def escalation_log() -> PersonalLog:
    """A log with a 4-tier escalation chain."""
    log = PersonalLog()
    r = log.record("human", "request", input="ambiguous", output="??", confidence=1.0)
    m = log.record("bot", "classification", parent_id=r.node_id,
                   input="ambiguous", output="no match", confidence=0.15)
    f = log.record("deepseek-flash", "interpretation", parent_id=m.node_id,
                   input="ambiguous", output="guess", confidence=0.55)
    log.record("claude", "escalation", parent_id=f.node_id,
               input="guess", output="final answer", confidence=0.93,
               metadata={"tier": "big_lm"})
    return log


# ---------------------------------------------------------------------------
# 1. Recording
# ---------------------------------------------------------------------------


class TestRecord:
    def test_basic_record(self, empty_log):
        node = empty_log.record("glm-5.2", "planning", input="x", output="y")
        assert node.agent_id == "glm-5.2"
        assert node.decision_type == "planning"
        assert node.node_id in empty_log
        assert len(empty_log) == 1

    def test_record_returns_decision_node(self, empty_log):
        node = empty_log.record("human", "request")
        assert isinstance(node, DecisionNode)

    def test_record_stores_input_output_in_metadata(self, empty_log):
        node = empty_log.record("glm", "plan", input="hello", output="world")
        assert node.metadata["input"] == "hello"
        assert node.metadata["output"] == "world"

    def test_record_with_parent_creates_link(self, empty_log):
        parent = empty_log.record("human", "request")
        child = empty_log.record("glm", "plan", parent_id=parent.node_id)
        # Parent should appear in ancestors of child
        assert parent.node_id in empty_log.graph.ancestors(child.node_id)

    def test_record_generates_hashes(self, empty_log):
        node = empty_log.record("glm", "plan", input="test input", output="test output")
        assert node.input_hash != ""
        assert node.output_hash != ""
        assert len(node.input_hash) == 12

    def test_record_auto_timestamp(self, empty_log):
        node = empty_log.record("glm", "plan")
        # Should be an ISO timestamp
        datetime.fromisoformat(node.timestamp)


# ---------------------------------------------------------------------------
# 2. Linking
# ---------------------------------------------------------------------------


class TestLink:
    def test_link_creates_consequence_edge(self, populated_log):
        nodes = list(populated_log.graph.nodes.values())
        edge = populated_log.link(nodes[0].node_id, nodes[-1].node_id,
                                  edge_type="influenced")
        assert edge.edge_type == "influenced"
        assert len(populated_log.graph.edges) >= 1

    def test_link_unknown_source_raises(self, empty_log):
        empty_log.record("glm", "plan")
        with pytest.raises(ValueError):
            empty_log.link("nonexistent", "alsodoesntexist")


# ---------------------------------------------------------------------------
# 3. Decision Trail
# ---------------------------------------------------------------------------


class TestDecisionTrail:
    def test_trail_three_node_chain(self, populated_log):
        nodes = list(populated_log.graph.nodes.values())
        build_cmd = [n for n in nodes if n.decision_type == "build_command"][0]
        trail = populated_log.decision_trail(build_cmd.node_id)
        assert len(trail) == 3
        # Trail goes from outcome → root
        assert trail[0] == build_cmd.node_id
        # Root should be last
        root = populated_log.graph.nodes[trail[-1]]
        assert root.decision_type == "request"

    def test_trail_readable_returns_dicts(self, populated_log):
        nodes = list(populated_log.graph.nodes.values())
        build_cmd = [n for n in nodes if n.decision_type == "build_command"][0]
        trail = populated_log.decision_trail_readable(build_cmd.node_id)
        assert len(trail) == 3
        assert all("agent" in t for t in trail)
        assert all("output" in t for t in trail)

    def test_trail_standalone_node(self, populated_log):
        opt = [n for n in populated_log.graph.nodes.values()
               if n.decision_type == "optimization"][0]
        trail = populated_log.decision_trail(opt.node_id)
        assert len(trail) == 1

    def test_trail_unknown_node_raises(self, empty_log):
        with pytest.raises(KeyError):
            empty_log.decision_trail("nonexistent")


# ---------------------------------------------------------------------------
# 4. Daily Summary
# ---------------------------------------------------------------------------


class TestDailySummary:
    def test_summary_has_expected_keys(self, populated_log):
        s = populated_log.daily_summary()
        expected_keys = {"date", "total_decisions", "by_agent", "by_type",
                         "avg_confidence", "escalations", "top_chain"}
        assert expected_keys.issubset(s.keys())

    def test_summary_counts_correctly(self, populated_log):
        s = populated_log.daily_summary()
        assert s["total_decisions"] == 4
        assert s["by_agent"]["glm-5.2"] == 2
        assert s["by_agent"]["human"] == 1
        assert s["by_type"]["planning"] == 1

    def test_summary_empty_log(self, empty_log):
        s = empty_log.daily_summary()
        assert s["total_decisions"] == 0
        assert s["by_agent"] == {}
        assert s["avg_confidence"] == 0.0

    def test_summary_avg_confidence(self, populated_log):
        s = populated_log.daily_summary()
        # confidences: 1.0, 0.9, 0.85, 1.0 = avg 0.9375
        assert abs(s["avg_confidence"] - 0.9375) < 0.01

    def test_summary_escalation_count(self, escalation_log):
        s = escalation_log.daily_summary()
        assert s["escalations"] == 1

    def test_summary_top_chain_is_longest(self, escalation_log):
        s = escalation_log.daily_summary()
        # Escalation chain is 4 nodes deep
        assert len(s["top_chain"]) == 4


# ---------------------------------------------------------------------------
# 5. Filtering
# ---------------------------------------------------------------------------


class TestFiltering:
    def test_filter_by_agent(self, populated_log):
        glm_nodes = populated_log.filter_by_agent("glm-5.2")
        assert len(glm_nodes) == 2

    def test_filter_by_type(self, populated_log):
        plans = populated_log.filter_by_type("planning")
        assert len(plans) == 1

    def test_filter_no_results(self, populated_log):
        assert populated_log.filter_by_agent("nobody") == []
        assert populated_log.filter_by_type("nothing") == []


# ---------------------------------------------------------------------------
# 6. Export
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_json_is_valid_json(self, populated_log):
        raw = populated_log.export_json()
        data = json.loads(raw)
        assert "nodes" in data
        assert "edges" in data
        assert "summary" in data
        assert "stats" in data

    def test_export_json_node_count(self, populated_log):
        data = populated_log.to_dict()
        assert len(data["nodes"]) == 4

    def test_export_json_preserves_metadata(self, populated_log):
        data = populated_log.to_dict()
        request_nodes = [n for n in data["nodes"] if n["decision_type"] == "request"]
        assert request_nodes[0]["metadata"]["input"] == "build"

    def test_export_json_empty_log(self, empty_log):
        raw = empty_log.export_json()
        data = json.loads(raw)
        assert data["nodes"] == []
        assert data["summary"]["total_decisions"] == 0


# ---------------------------------------------------------------------------
# 7. Get Node
# ---------------------------------------------------------------------------


class TestGetNode:
    def test_get_node_returns_decision_node(self, populated_log):
        nodes = list(populated_log.graph.nodes.values())
        node = populated_log.get_node(nodes[0].node_id)
        assert isinstance(node, DecisionNode)

    def test_get_node_unknown_raises(self, empty_log):
        with pytest.raises(KeyError):
            empty_log.get_node("nonexistent")
