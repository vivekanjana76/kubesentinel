"""LangGraph node implementations.

Each node is a pure function: `(state) -> dict` of partial updates. The graph
binds toolkit / llm / retriever via `functools.partial` in `agent/graph.py`,
so the underlying node signatures take those as keyword-only args.

Naming maps 1:1 with module file names:
    receive_alert     -> nodes/receive.py
    investigate       -> nodes/investigate.py
    search_history    -> nodes/search.py
    reason            -> nodes/reason.py
    remediate         -> nodes/remediate.py
    escalate          -> nodes/escalate.py
    report            -> nodes/report.py
    route_after_reason-> nodes/routing.py
"""

from agent.nodes.escalate import escalate
from agent.nodes.investigate import investigate
from agent.nodes.prepare_retry import prepare_retry
from agent.nodes.reason import reason
from agent.nodes.receive import receive_alert
from agent.nodes.remediate import remediate
from agent.nodes.report import report
from agent.nodes.routing import route_after_reason
from agent.nodes.search import search_history

__all__ = [
    "escalate",
    "investigate",
    "prepare_retry",
    "reason",
    "receive_alert",
    "remediate",
    "report",
    "route_after_reason",
    "search_history",
]
