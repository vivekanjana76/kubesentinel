"""End-to-end ImagePullBackOff scenario."""

from __future__ import annotations

from agent.graph import build_graph
from agent.state import AgentState, AlertPayload, ProposedFix, ReasoningOutput
from agent.tools.mocks import MockToolkit


def test_imagepullbackoff_diagnosis_targets_deployment_or_image(
    make_runbook,
    make_fake_llm,
    make_fake_retriever,
):
    output = ReasoningOutput(
        diagnosis=(
            "ImagePullBackOff: registry returned 'manifest not found' for image "
            "myregistry.io/myapp:v2.3.1. Recent commit ghi9012 bumped the image "
            "tag but the image was never pushed."
        ),
        proposed_fix=ProposedFix(
            type="config_update",
            namespace="kubesentinel",
            target="deployment/sacrificial",
            description="Roll back the image tag in the deployment to the last known good version.",
            command_or_diff="kubectl set image deployment/sacrificial app=myregistry.io/myapp:v2.3.0",
        ),
        confidence=0.82,
    )
    runbook = make_runbook("ImagePullBackOff", "imagepullbackoff.md", similarity=0.88)

    toolkit = MockToolkit("ImagePullBackOff")
    llm = make_fake_llm([output])
    retriever = make_fake_retriever([runbook])
    graph = build_graph(toolkit=toolkit, llm=llm, retriever=retriever)

    initial = AgentState(
        alert=AlertPayload(
            name="ImagePullBackOff",
            severity="critical",
            namespace="kubesentinel",
            pod="sacrificial-0",
        )
    )
    final = AgentState(**graph.invoke(initial))

    assert final.status == "done"
    assert final.proposed_fix is not None
    fix_text = (
        final.proposed_fix.target
        + " "
        + final.proposed_fix.description
        + " "
        + final.proposed_fix.command_or_diff
    ).lower()
    # The fix should reference either the deployment, the image, or the tag.
    assert any(token in fix_text for token in ("deployment", "image", "myapp", "v2.3."))
    # Diagnosis should mention image / registry.
    assert any(token in (final.diagnosis or "").lower() for token in ("image", "registry"))
