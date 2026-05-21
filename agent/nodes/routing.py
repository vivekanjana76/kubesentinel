"""Conditional routing for the post-reason branch.

Decision table:

| confidence              | iteration room left          | next                    |
|-------------------------|------------------------------|-------------------------|
| >= confidence_high      | (any)                        | "remediate"             |
| < confidence_low        | iteration < max_iterations-1 | "investigate" (re-loop) |
| otherwise                | -                            | "escalate"              |

When we re-loop, the iteration counter is bumped here (not in the
`investigate` node) so investigate stays idempotent and the decision logic
lives in one place.

The function returns *both* a routing key and a state update. LangGraph's
`add_conditional_edges` takes only a routing function, so we expose two
adapters: `route_after_reason` for the edge, and `bump_iteration_if_looping`
for a tiny pass-through node that bumps the counter before investigate runs.

Simpler approach: keep all loop-counter logic out of LangGraph's flow control
by mutating `iteration` inside the conditional function via a side-channel.
That would tie correctness to call order. Instead, we route to a dedicated
`prepare_retry` node when looping (added in graph.py) which bumps the counter,
then flows back into investigate.
"""

from __future__ import annotations

from typing import Literal

from agent.settings import settings
from agent.state import AgentState

RouteName = Literal["remediate", "prepare_retry", "escalate"]


def route_after_reason(state: AgentState) -> RouteName:
    if state.confidence >= settings.confidence_high:
        return "remediate"
    iterations_left = state.iteration < state.max_iterations - 1
    if state.confidence < settings.confidence_low and iterations_left:
        return "prepare_retry"
    return "escalate"
