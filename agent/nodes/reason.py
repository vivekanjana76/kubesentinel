"""reason node — call the reasoning LLM with the full investigation context.

Uses `get_structured_llm()` so the LLM is forced to return a valid
`ReasoningOutput` (diagnosis / proposed_fix / confidence). The fallback to
json_mode lives in the factory — this node just calls the runnable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agent.llm.factory import get_structured_llm
from agent.nodes._logging import node_span
from agent.state import ActionLog, AgentState, ReasoningOutput, _utcnow

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


PROMPT_TEMPLATE = """You are a senior Site Reliability Engineer investigating a Kubernetes alert.

# Alert
- Name: {alert_name}
- Severity: {alert_severity}
- Namespace: {alert_namespace}
- Pod: {alert_pod}
- Summary: {alert_summary}
- Labels: {alert_labels}

# Live Investigation Findings

## Pod Logs
{logs}

## Pod Events
{events}

## Recent Code Changes (last 2 hours)
{commits}

# Historical Runbooks (most relevant first)
{runbooks}

# Your Task
Analyze the above and produce a structured response with three fields:
- diagnosis: 2-4 sentence root cause analysis grounded in the findings.
  Cite specific log lines, commit SHAs, and runbook sections where relevant.
- proposed_fix: specific remediation.
  - type: one of "kubectl_patch", "code_change", "config_update"
  - target: resource being changed (e.g. "deployment/sacrificial -n kubesentinel")
  - description: one-paragraph rationale for *this* fix
  - command_or_diff: the exact kubectl command, code diff, or config snippet
- confidence: float in [0.0, 1.0] — how certain you are this is the right
  diagnosis AND fix. Below 0.7 means more investigation would help; below 0.4
  means findings are insufficient.

Be specific. If findings contradict the runbooks, explain. If you can't
identify a fix from the available data, set confidence below 0.4 and propose
a fix that gathers more information (e.g. a kubectl describe / kubectl logs
--previous command targeting the right resource).
"""


def _format_logs(logs: list[str]) -> str:
    if not logs:
        return "(no logs captured)"
    return "\n".join(f"  {i+1}. {line}" for i, line in enumerate(logs))


def _format_events(events: list[dict]) -> str:
    if not events:
        return "(no events)"
    parts = []
    for e in events:
        reason = e.get("reason", "")
        msg = e.get("message", "")
        ts = e.get("timestamp", "")
        parts.append(f"  - [{ts}] {reason}: {msg}")
    return "\n".join(parts)


def _format_commits(commits: list[dict]) -> str:
    if not commits:
        return "(no recent commits)"
    parts = []
    for c in commits:
        sha = c.get("sha", "?")[:7]
        msg = c.get("message", "")
        author = c.get("author", "?")
        files = c.get("changed_files", [])
        ts = c.get("timestamp", "")
        parts.append(
            f"  - {sha} [{ts}] by {author}: {msg}\n      files: {', '.join(files)}"
        )
    return "\n".join(parts)


def _format_runbooks(runbooks) -> str:
    if not runbooks:
        return "(no relevant runbooks)"
    parts = []
    for r in runbooks:
        parts.append(
            f"## {r.title}  (similarity={r.similarity:.2f}, source={r.source_file})\n"
            f"{r.content}"
        )
    return "\n\n".join(parts)


def _format_prompt(state: AgentState) -> str:
    return PROMPT_TEMPLATE.format(
        alert_name=state.alert.name,
        alert_severity=state.alert.severity,
        alert_namespace=state.alert.namespace,
        alert_pod=state.alert.pod or "(none)",
        alert_summary=state.alert.summary or "(none)",
        alert_labels=state.alert.labels or "{}",
        logs=_format_logs(state.pod_logs),
        events=_format_events(state.pod_events),
        commits=_format_commits(state.recent_commits),
        runbooks=_format_runbooks(state.retrieved_runbooks),
    )


def reason(state: AgentState, *, llm: BaseChatModel) -> dict[str, Any]:
    prompt = _format_prompt(state)

    with node_span("reason", iteration=state.iteration, alert=state.alert.name) as span:
        structured = get_structured_llm(llm, ReasoningOutput)
        try:
            output: ReasoningOutput = structured.invoke(prompt)
        except Exception as exc:
            error_log = ActionLog(
                timestamp=_utcnow(),
                node="reason",
                action="llm_reason",
                result=f"error: {exc}",
                metadata={"error_type": type(exc).__name__},
            )
            span["fields_updated"] = ["actions_taken", "status", "error"]
            return {
                "actions_taken": [*state.actions_taken, error_log],
                "status": "failed",
                "error": str(exc),
            }

        action = ActionLog(
            timestamp=_utcnow(),
            node="reason",
            action="llm_reason",
            result=f"diagnosis produced, confidence={output.confidence:.2f}",
            metadata={
                "confidence": output.confidence,
                "fix_type": output.proposed_fix.type,
                "fix_target": output.proposed_fix.target,
            },
        )
        span["fields_updated"] = [
            "diagnosis",
            "proposed_fix",
            "confidence",
            "actions_taken",
        ]
        return {
            "diagnosis": output.diagnosis,
            "proposed_fix": output.proposed_fix,
            "confidence": output.confidence,
            "actions_taken": [*state.actions_taken, action],
        }
