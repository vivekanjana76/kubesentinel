"""investigate node — calls the toolkit's read methods to gather context.

Idempotent: when re-entered after a low-confidence loop, it overwrites the
prior findings (which were insufficient). The toolkit can return richer data
on subsequent calls (real cluster: more recent logs; mock: configurable).
"""

from __future__ import annotations

from typing import Any

from agent.nodes._logging import node_span
from agent.state import ActionLog, AgentState, _utcnow
from agent.tools.base import Toolkit


def investigate(state: AgentState, *, toolkit: Toolkit) -> dict[str, Any]:
    namespace = state.alert.namespace
    pod = state.alert.pod or "unknown"

    with node_span("investigate", iteration=state.iteration, alert=state.alert.name) as span:
        logs = toolkit.fetch_logs(namespace, pod)
        events = toolkit.fetch_events(namespace, pod)
        commits = toolkit.fetch_recent_commits(hours=2)

        actions = [
            ActionLog(
                timestamp=_utcnow(),
                node="investigate",
                action="fetch_logs",
                result=f"{len(logs)} log lines",
                metadata={"namespace": namespace, "pod": pod},
            ),
            ActionLog(
                timestamp=_utcnow(),
                node="investigate",
                action="fetch_events",
                result=f"{len(events)} events",
                metadata={"namespace": namespace, "pod": pod},
            ),
            ActionLog(
                timestamp=_utcnow(),
                node="investigate",
                action="fetch_recent_commits",
                result=f"{len(commits)} commits in last 2h",
                metadata={"hours": 2},
            ),
        ]
        span["fields_updated"] = [
            "pod_logs",
            "pod_events",
            "recent_commits",
            "actions_taken",
        ]
        return {
            "pod_logs": logs,
            "pod_events": events,
            "recent_commits": commits,
            "actions_taken": [*state.actions_taken, *actions],
        }
