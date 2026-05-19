"""receive_alert node — entry point.

Validates the incoming alert and seeds the audit trail. Doesn't actually
*receive* anything from the network in Phase 3 — the graph is invoked from the
CLI with an `AlertPayload` already in state.
"""

from __future__ import annotations

from typing import Any

from agent.nodes._logging import node_span
from agent.state import ActionLog, AgentState, _utcnow


def receive_alert(state: AgentState) -> dict[str, Any]:
    with node_span("receive_alert", alert=state.alert.name) as span:
        entry = ActionLog(
            timestamp=_utcnow(),
            node="receive_alert",
            action="ingest_alert",
            result=f"received alert {state.alert.name} severity={state.alert.severity}",
            metadata={
                "alert_name": state.alert.name,
                "namespace": state.alert.namespace,
                "pod": state.alert.pod,
            },
        )
        span["fields_updated"] = ["actions_taken", "status"]
        return {
            "actions_taken": [*state.actions_taken, entry],
            "status": "investigating",
        }
