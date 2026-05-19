"""
LangGraph wiring for the KubeSentinel agent.

`build_graph(toolkit, llm, retriever)` constructs and compiles the state
machine. The CLI and the (future) webhook autotrigger both go through this
factory so DI of mock-vs-real components is one line.

Graph topology:

    START
      |
      v
    receive_alert
      |
      v
    investigate <----------------------+
      |                                |
      v                                |
    search_history                     |
      |                                |
      v                                |
    reason                             |
      |                                |
      +-- (high confidence) --> remediate -+
      |                                    |
      +-- (low conf + retries) --> prepare_retry
      |                                    |
      +-- (else) ----------------> escalate-+
                                           |
                                           v
                                         report
                                           |
                                           v
                                          END

Nodes are pure functions taking AgentState (+ injected deps via partial).
LangGraph merges dict updates into the BaseModel state automatically.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    escalate,
    investigate,
    prepare_retry,
    reason,
    receive_alert,
    remediate,
    report,
    route_after_reason,
    search_history,
)
from agent.state import AgentState
from agent.tools.base import Toolkit

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.graph.state import CompiledStateGraph

    from agent.rag.retriever import RunbookRetriever


def build_graph(
    *,
    toolkit: Toolkit,
    llm: BaseChatModel,
    retriever: RunbookRetriever,
) -> CompiledStateGraph:
    """Build and compile the agent state machine.

    All side-effecting collaborators (toolkit, llm, retriever) are injected
    here. Swap MockToolkit for RealToolkit in Phase 4 — graph code untouched.
    """
    builder: StateGraph = StateGraph(AgentState)

    # Bind dependencies into the node callables. LangGraph requires nodes
    # to be plain `(state) -> dict` callables; partial gives us that.
    builder.add_node("receive_alert", receive_alert)
    builder.add_node("investigate", partial(investigate, toolkit=toolkit))
    builder.add_node("search_history", partial(search_history, retriever=retriever))
    builder.add_node("reason", partial(reason, llm=llm))
    builder.add_node("prepare_retry", prepare_retry)
    builder.add_node("remediate", partial(remediate, toolkit=toolkit))
    builder.add_node("escalate", escalate)
    builder.add_node("report", report)

    builder.add_edge(START, "receive_alert")
    builder.add_edge("receive_alert", "investigate")
    builder.add_edge("investigate", "search_history")
    builder.add_edge("search_history", "reason")

    builder.add_conditional_edges(
        "reason",
        route_after_reason,
        {
            "remediate": "remediate",
            "prepare_retry": "prepare_retry",
            "escalate": "escalate",
        },
    )

    builder.add_edge("prepare_retry", "investigate")
    builder.add_edge("remediate", "report")
    builder.add_edge("escalate", "report")
    builder.add_edge("report", END)

    return builder.compile()
