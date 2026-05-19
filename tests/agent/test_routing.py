"""Unit tests for route_after_reason."""

from __future__ import annotations

from agent.nodes.routing import route_after_reason
from agent.state import AgentState, AlertPayload


def _state(confidence: float, iteration: int = 0, max_iterations: int = 3) -> AgentState:
    return AgentState(
        alert=AlertPayload(name="OOMKilled"),
        confidence=confidence,
        iteration=iteration,
        max_iterations=max_iterations,
    )


def test_high_confidence_routes_to_remediate():
    assert route_after_reason(_state(0.85)) == "remediate"
    assert route_after_reason(_state(0.7)) == "remediate"


def test_low_confidence_with_retries_left_routes_to_prepare_retry():
    # max_iterations=3, iteration=0 — 2 retries left after this pass.
    assert route_after_reason(_state(0.25, iteration=0)) == "prepare_retry"
    assert route_after_reason(_state(0.3, iteration=1)) == "prepare_retry"


def test_low_confidence_exhausted_routes_to_escalate():
    # iteration=2 with max_iterations=3 means 'iteration < max - 1' is False.
    assert route_after_reason(_state(0.2, iteration=2)) == "escalate"


def test_mid_confidence_routes_to_escalate():
    # 0.4 <= confidence < 0.7 always escalates regardless of retries.
    assert route_after_reason(_state(0.5, iteration=0)) == "escalate"
    assert route_after_reason(_state(0.65, iteration=0)) == "escalate"
