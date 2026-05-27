"""
Core Pydantic models for the LangGraph agent.

All shared types — incoming alert payload, agent working state, proposed fixes,
audit-trail entries, and the structured LLM reasoning output — live here so
nodes, tools, and the webhook all reference one source of truth.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.rag.retriever import Runbook


def _utcnow() -> datetime:
    return datetime.now(UTC)

# ── Alert input ──────────────────────────────────────────────────────────────


class AlertPayload(BaseModel):
    """Normalized representation of a single Alertmanager alert.

    Decoupled from the raw webhook envelope so callers (webhook, CLI fixtures,
    tests) can construct alerts directly without reconstructing the full
    Alertmanager schema.
    """

    name: str
    severity: str = "warning"
    namespace: str = "default"
    pod: str | None = None
    summary: str = ""
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    starts_at: datetime | None = None


# ── Audit + remediation ──────────────────────────────────────────────────────


FixType = Literal["kubectl_patch", "code_change", "config_update"]


class ProposedFix(BaseModel):
    """A single concrete remediation proposed by the reasoning node."""

    type: FixType
    namespace: str = Field(description="Kubernetes namespace where the resource lives, e.g. 'kubesentinel'.")
    target: str = Field(description="Resource being changed, e.g. 'deployment/sacrificial'.")
    description: str = Field(description="One-paragraph human-readable rationale.")
    command_or_diff: str = Field(
        description="Exact kubectl command, code diff, or config snippet to apply."
    )


class ActionLog(BaseModel):
    """An entry in the agent's audit trail.

    One ActionLog is appended per tool call, per LLM call, and per
    remediate/escalate decision. Provides the full timeline for the RCA report.
    """

    timestamp: datetime = Field(default_factory=_utcnow)
    node: str = Field(description="The graph node that produced this entry.")
    action: str = Field(description="Short verb-phrase: 'fetch_logs', 'llm_reason', etc.")
    result: str = Field(description="Outcome summary — 'ok', 'error: …', or a brief note.")
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Reasoning output (structured LLM schema) ─────────────────────────────────


class ReasoningOutput(BaseModel):
    """Schema the reasoning LLM is forced to return.

    Used with `llm.with_structured_output(ReasoningOutput)`. The wrapper in
    agent/llm/factory.py auto-falls-back from function-calling to json_mode
    when a model doesn't support tool calling.
    """

    diagnosis: str = Field(
        description="2-4 sentence root cause analysis grounded in the findings. "
        "Cite log lines, commit SHAs, and runbook sections."
    )
    proposed_fix: ProposedFix
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="0.0-1.0 — how certain you are this is the right diagnosis AND fix.",
    )


# ── Agent state (the LangGraph state) ────────────────────────────────────────


AgentStatus = Literal["investigating", "remediating", "reporting", "done", "failed"]


class AgentState(BaseModel):
    """The single object passed between graph nodes.

    Nodes return *partial* dict updates that LangGraph merges into this model.
    Treat as immutable from a node's perspective — never mutate fields in place.
    """

    # ── Input ──
    alert: AlertPayload

    # ── Investigation findings ──
    pod_logs: list[str] = Field(default_factory=list)
    pod_events: list[dict] = Field(default_factory=list)
    recent_commits: list[dict] = Field(default_factory=list)

    # ── RAG context ──
    retrieved_runbooks: list[Runbook] = Field(default_factory=list)

    # ── Reasoning output ──
    diagnosis: str | None = None
    proposed_fix: ProposedFix | None = None
    confidence: float = 0.0

    # ── Execution ──
    actions_taken: list[ActionLog] = Field(default_factory=list)
    final_report: str | None = None

    # ── Routing / control ──
    iteration: int = 0
    max_iterations: int = 3
    status: AgentStatus = "investigating"
    error: str | None = None

    model_config = {
        # Runbook contains a uuid.UUID — allow it through validation.
        "arbitrary_types_allowed": True,
    }
