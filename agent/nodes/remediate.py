"""remediate node — mock execution of the proposed fix.

In Phase 3, the MockToolkit returns an ActionLog describing what *would* have
happened. Phase 4's RealToolkit will actually issue kubectl commands or open
PRs. The node code is identical in both cases — it only talks to the Toolkit.
"""

from __future__ import annotations

from typing import Any

from agent.nodes._logging import node_span
from agent.state import ActionLog, AgentState, _utcnow
from agent.tools.base import Toolkit


def remediate(state: AgentState, *, toolkit: Toolkit) -> dict[str, Any]:
    if state.proposed_fix is None:
        # Guard: route_after_reason should never send us here without a fix.
        err = ActionLog(
            timestamp=_utcnow(),
            node="remediate",
            action="apply_remediation",
            result="error: no proposed_fix in state",
        )
        return {
            "actions_taken": [*state.actions_taken, err],
            "status": "failed",
            "error": "remediate called with no proposed_fix",
        }

    with node_span(
        "remediate",
        iteration=state.iteration,
        confidence=state.confidence,
        fix_type=state.proposed_fix.type,
    ) as span:
        action = toolkit.apply_remediation(state.proposed_fix)
        span["fields_updated"] = ["actions_taken", "status"]
        return {
            "actions_taken": [*state.actions_taken, action],
            "status": "reporting",
        }
