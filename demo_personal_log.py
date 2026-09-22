#!/usr/bin/env python3
"""PersonalLOG.AI — proof-of-concept demo.

Simulates 12 fleet decisions across agents (GLM-5.2, Claude, DeepSeek,
KimiCode, human) and demonstrates:

1. Full decision trail from a build command back to the player request.
2. Escalation pattern (mechanical → small LM → big LM → human).
3. Daily summary: "What did the fleet decide today?"
"""

from cns_bridge.personal_log import PersonalLog
from cns_bridge.log_graph import DecisionNode


def main() -> None:
    log = PersonalLog()

    # ==================================================================
    # CHAIN 1 — Player wants a castle (4 decisions)
    # ==================================================================

    d1 = log.record(
        "human", "request",
        input="Player clicked: 'Build me a castle'",
        output="Intent: construct a medieval castle structure",
        confidence=1.0,
    )

    d2 = log.record(
        "glm-5.2", "planning",
        input="Build a castle → decompose into components",
        output="Plan: stone walls (8x8), tower x4, gate, interior rooms",
        confidence=0.92,
        parent_id=d1.node_id,
    )

    d3 = log.record(
        "deepseek-v4-pro", "spatial_analysis",
        input="Castle plan with 4 towers and walls",
        output="Footprint: 64x64 studs, tower height 40, wall height 24",
        confidence=0.88,
        parent_id=d2.node_id,
    )

    d4 = log.record(
        "kimicode", "build_command",
        input="Castle spatial spec: walls, towers, gate",
        output="Place Part{Size=Vector3.new(64,24,4)...} x40 parts",
        confidence=0.85,
        parent_id=d3.node_id,
    )

    # ==================================================================
    # CHAIN 2 — Player wants a forest (3 decisions)
    # ==================================================================

    d5 = log.record(
        "human", "request",
        input="Player typed: 'make it look alive — add trees'",
        output="Intent: decorate terrain with forest",
        confidence=1.0,
    )

    d6 = log.record(
        "glm-5.2", "planning",
        input="Add forest around castle",
        output="Plan: scatter 30 pine trees, random rotation, terrain paint",
        confidence=0.90,
        parent_id=d5.node_id,
    )

    d7 = log.record(
        "deepseek-v4-flash", "build_command",
        input="Forest plan: 30 pine trees",
        output="for i=1,30 do Place Tree{position=randomize...} end",
        confidence=0.91,
        parent_id=d6.node_id,
    )

    # ==================================================================
    # CHAIN 3 — Escalation: ambiguous request (3 decisions)
    # ==================================================================

    d8 = log.record(
        "human", "request",
        input="Player said: 'make it cooler'",
        output="Intent: AMBIGUOUS — 'cooler' could mean lighting, style, weather",
        confidence=1.0,
    )

    d9 = log.record(
        "mechanical-bot", "classification",
        input="'make it cooler' — keyword analysis",
        output="No keyword match (not build/color/terrain)",
        confidence=0.15,
        parent_id=d8.node_id,
    )

    d10 = log.record(
        "deepseek-v4-flash", "interpretation",
        input="'make it cooler' — context: castle scene",
        output="Likely: add atmospheric lighting + particle effects",
        confidence=0.55,
        parent_id=d9.node_id,
    )

    d11 = log.record(
        "claude-sonnet-5", "escalation",
        input="Low-confidence interpretation forwarded",
        output="Apply dynamic lighting (TimeOfDay sunset) + fire particle accents",
        confidence=0.93,
        parent_id=d10.node_id,
        metadata={"escalated_from": "deepseek-v4-flash", "tier": "big_lm"},
    )

    # ==================================================================
    # CHAIN 4 — Independent fleet activity (2 decisions)
    # ==================================================================

    d12 = log.record(
        "glm-5.2", "optimization",
        input="Scene has 70 parts — check performance",
        output="Merged 12 static parts into unions, reduced render cost 18%",
        confidence=0.96,
    )

    # ==================================================================
    # DEMO OUTPUT
    # ==================================================================

    print("=" * 72)
    print("  PersonalLOG.AI — Proof of Concept Demo")
    print("=" * 72)

    # --- Decision Trail ---
    print("\n📋 DECISION TRAIL: Build command → Player request")
    print("-" * 72)
    trail = log.decision_trail_readable(d4.node_id)
    for i, step in enumerate(trail):
        indent = "  " * i
        arrow = "← " if i > 0 else "→ "
        print(f"  {arrow}{indent}[{step['agent']}] {step['type']}")
        print(f"     {indent}  output: {step['output']}")
        print(f"     {indent}  confidence: {step['confidence']:.0%}")
        print()

    # --- Escalation ---
    print("\n🔥 ESCALATION PATTERN: Ambiguous request")
    print("-" * 72)
    esc_trail = log.decision_trail_readable(d11.node_id)
    for i, step in enumerate(esc_trail):
        bar = "█" * int(step["confidence"] * 20)
        print(f"  Tier {i}: [{step['agent']}] {step['type']}")
        print(f"          confidence: {step['confidence']:.0%} {bar}")
        print(f"          output: {step['output']}")
        print()

    # --- Daily Summary ---
    print("\n📊 DAILY SUMMARY: What did the fleet decide today?")
    print("-" * 72)
    summary = log.daily_summary()
    print(f"  Date: {summary['date']}")
    print(f"  Total decisions: {summary['total_decisions']}")
    print(f"  Average confidence: {summary['avg_confidence']:.1%}")
    print(f"  Escalations: {summary['escalations']}")
    print()
    print("  By agent:")
    for agent, count in sorted(summary["by_agent"].items()):
        print(f"    {agent:25s} → {count} decisions")
    print()
    print("  By type:")
    for dtype, count in sorted(summary["by_type"].items()):
        print(f"    {dtype:25s} → {count} decisions")
    print()
    print("  Longest decision chain:")
    for i, step in enumerate(summary["top_chain"]):
        print(f"    {i+1}. [{step['agent']}] {step['output'][:60]}")

    # --- Export ---
    print("\n📦 JSON EXPORT (first 500 chars):")
    print("-" * 72)
    exported = log.export_json()
    print(exported[:500] + "...")

    print(f"\n✅ PersonalLOG demo complete — {len(log)} decisions recorded.")


if __name__ == "__main__":
    main()
