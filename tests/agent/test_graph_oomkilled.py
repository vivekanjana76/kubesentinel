"""End-to-end OOMKilled scenario through the compiled graph."""

from __future__ import annotations

from agent.graph import build_graph
from agent.state import AgentState, AlertPayload
from agent.tools.mocks import MockToolkit


def test_oomkilled_happy_path(
    reasoning_output_high,
    make_runbook,
    make_fake_llm,
    make_fake_retriever,
):
    runbook = make_runbook(
        "OOMKilled Pod",
        "oomkilled-pod.md",
        similarity=0.92,
        content="Pod terminated by Linux OOM killer when memory exceeds cgroup limit.",
    )
    toolkit = MockToolkit("OOMKilled")
    llm = make_fake_llm([reasoning_output_high])
    retriever = make_fake_retriever([runbook])
    graph = build_graph(toolkit=toolkit, llm=llm, retriever=retriever)

    initial = AgentState(
        alert=AlertPayload(
            name="OOMKilled",
            severity="critical",
            namespace="kubesentinel",
            pod="sacrificial-0",
        )
    )
    final_dict = graph.invoke(initial)
    final = AgentState(**final_dict)

    assert final.status == "done"
    assert final.diagnosis
    assert final.confidence > 0.5
    assert final.proposed_fix is not None
    assert final.proposed_fix.type in {"kubectl_patch", "code_change"}
    # The retrieved runbook should make it into the final state.
    assert any(r.source_file == "oomkilled-pod.md" for r in final.retrieved_runbooks)
    # Audit trail should include the fetches, the LLM call, and the remediation.
    action_names = [a.action for a in final.actions_taken]
    assert "fetch_logs" in action_names
    assert "llm_reason" in action_names
    assert "apply_remediation" in action_names
    # Final report should be rendered and reference OOMKilled.
    assert final.final_report
    assert "OOMKilled" in final.final_report


def test_oomkilled_low_confidence_loops_back(
    reasoning_output_low,
    reasoning_output_high,
    make_runbook,
    make_fake_llm,
    make_fake_retriever,
):
    """First reason pass is low-confidence; second pass succeeds — should
    loop back through prepare_retry -> investigate exactly once and end up
    remediating on iteration 1."""
    runbook = make_runbook("OOMKilled Pod", "oomkilled-pod.md")
    # Force first investigate to return partial data; second pass gets full.
    toolkit = MockToolkit("OOMKilled", force_low_confidence_first_pass=True)
    llm = make_fake_llm([reasoning_output_low, reasoning_output_high])
    retriever = make_fake_retriever([runbook])
    graph = build_graph(toolkit=toolkit, llm=llm, retriever=retriever)

    initial = AgentState(
        alert=AlertPayload(
            name="OOMKilled",
            namespace="kubesentinel",
            pod="sacrificial-0",
        )
    )
    final = AgentState(**graph.invoke(initial))

    assert final.iteration == 1
    assert final.status == "done"
    assert final.confidence > 0.7
    action_names = [a.action for a in final.actions_taken]
    # prepare_retry should have fired once.
    assert action_names.count("loop_back") == 1
    # Two LLM passes.
    assert action_names.count("llm_reason") == 2
    # Two rounds of fetch_logs (the loop re-runs investigate).
    assert action_names.count("fetch_logs") == 2
    # Final state ends in remediation, not escalation.
    assert "apply_remediation" in action_names
    assert "request_human_approval" not in action_names
