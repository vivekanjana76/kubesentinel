"""escalate node — record that a human needs to approve / investigate.

Mocked in Phase 3: appends an ActionLog. Phase 4 will post to Slack via the
Toolkit and potentially open a draft PR with the partial diagnosis attached.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.nodes._logging import node_span
from agent.state import ActionLog, AgentState


def escalate(state: AgentState) -> dict[str, Any]:
    with node_span(
        "escalate",
        iteration=state.iteration,
        confidence=state.confidence,
    ) as span:
        fix_desc = (
            f"{state.proposed_fix.type} on {state.proposed_fix.target}"
            if state.proposed_fix
            else "no fix proposed"
        )
        action = ActionLog(
            timestamp=datetime.utcnow(),
            node="escalate",
            action="request_human_approval",
            result=f"human approval required for {fix_desc} (confidence={state.confidence:.2f})",
            metadata={
                "confidence": state.confidence,
                "iteration": state.iteration,
            },
        )
        span["fields_updated"] = ["actions_taken", "status"]
        return {
            "actions_taken": [*state.actions_taken, action],
            "status": "reporting",
        }
