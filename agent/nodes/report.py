"""report node — render a markdown RCA from the full state.

Does NOT post to Slack in Phase 3 (no real toolkit calls). The CLI prints the
final_report; Phase 4 will pass it to toolkit.post_slack().
"""

from __future__ import annotations

from typing import Any

from agent.nodes._logging import node_span
from agent.state import AgentState


def _action_line(a) -> str:
    return f"- [{a.timestamp.isoformat(timespec='seconds')}] **{a.node}**: {a.action} → {a.result}"


def _render_report(state: AgentState) -> str:
    fix_block = "No fix proposed."
    if state.proposed_fix:
        fix_block = (
            f"**Type:** `{state.proposed_fix.type}`\n\n"
            f"**Target:** `{state.proposed_fix.target}`\n\n"
            f"**Description:** {state.proposed_fix.description}\n\n"
            f"**Command / Diff:**\n```\n{state.proposed_fix.command_or_diff}\n```"
        )

    runbook_block = "(no runbooks retrieved)"
    if state.retrieved_runbooks:
        runbook_block = "\n".join(
            f"- **{r.title}** (similarity {r.similarity:.2f}) — `{r.source_file}`"
            for r in state.retrieved_runbooks
        )

    action_block = "\n".join(_action_line(a) for a in state.actions_taken)

    decision = "auto-remediation applied" if any(
        a.action == "apply_remediation" for a in state.actions_taken
    ) else (
        "escalated to human" if any(
            a.action == "request_human_approval" for a in state.actions_taken
        ) else "no action"
    )

    return f"""# Incident RCA: {state.alert.name}

**Severity:** {state.alert.severity}  |  **Namespace:** {state.alert.namespace}  |  **Pod:** {state.alert.pod or "—"}

**Iterations:** {state.iteration + 1}  |  **Confidence:** {state.confidence:.2f}  |  **Decision:** {decision}

---

## Diagnosis

{state.diagnosis or "(no diagnosis produced)"}

## Proposed Fix

{fix_block}

## Relevant Runbooks

{runbook_block}

## Action Trail

{action_block}
"""


def report(state: AgentState) -> dict[str, Any]:
    with node_span("report", iteration=state.iteration) as span:
        rendered = _render_report(state)
        span["fields_updated"] = ["final_report", "status"]
        return {
            "final_report": rendered,
            "status": "done",
        }
