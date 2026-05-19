"""prepare_retry node — bump iteration counter, reset transient findings, loop back.

Sits between the `reason` conditional ("low confidence, retries left") and a
re-entry into `investigate`. Centralizing the counter bump here keeps
`investigate` idempotent (it just gathers data; doesn't track loop state)
and keeps `route_after_reason` a pure function of state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.nodes._logging import node_span
from agent.state import ActionLog, AgentState


def prepare_retry(state: AgentState) -> dict[str, Any]:
    new_iteration = state.iteration + 1
    with node_span("prepare_retry", from_iteration=state.iteration, to_iteration=new_iteration) as span:
        action = ActionLog(
            timestamp=datetime.utcnow(),
            node="prepare_retry",
            action="loop_back",
            result=(
                f"confidence={state.confidence:.2f} below threshold; "
                f"iteration {state.iteration} -> {new_iteration}"
            ),
            metadata={
                "prior_confidence": state.confidence,
                "iteration": new_iteration,
            },
        )
        span["fields_updated"] = ["iteration", "confidence", "diagnosis", "proposed_fix", "actions_taken"]
        # Reset reasoning output so the next pass starts clean.
        return {
            "iteration": new_iteration,
            "confidence": 0.0,
            "diagnosis": None,
            "proposed_fix": None,
            "actions_taken": [*state.actions_taken, action],
        }
