"""search_history node — query the Phase 2 RAG for relevant runbooks.

Builds a query string from the alert name + symptoms (top log line, top event
reason) and asks the retriever for the top-k matches.
"""

from __future__ import annotations

from typing import Any

from agent.nodes._logging import node_span
from agent.rag.retriever import RunbookRetriever
from agent.state import ActionLog, AgentState, _utcnow

K_RUNBOOKS = 3


def _build_query(state: AgentState) -> str:
    parts: list[str] = [state.alert.name]
    if state.alert.summary:
        parts.append(state.alert.summary)
    # First log line and first event reason are usually the most diagnostic.
    if state.pod_logs:
        parts.append(state.pod_logs[0])
    if state.pod_events:
        first = state.pod_events[0]
        reason = first.get("reason") or first.get("message", "")
        if reason:
            parts.append(str(reason))
    return " | ".join(parts)


def search_history(
    state: AgentState,
    *,
    retriever: RunbookRetriever,
) -> dict[str, Any]:
    query = _build_query(state)
    with node_span("search_history", iteration=state.iteration, alert=state.alert.name) as span:
        runbooks = retriever.retrieve(query, k=K_RUNBOOKS)
        action = ActionLog(
            timestamp=_utcnow(),
            node="search_history",
            action="retrieve_runbooks",
            result=f"{len(runbooks)} runbooks retrieved",
            metadata={"query": query, "k": K_RUNBOOKS},
        )
        span["fields_updated"] = ["retrieved_runbooks", "actions_taken"]
        return {
            "retrieved_runbooks": runbooks,
            "actions_taken": [*state.actions_taken, action],
        }
