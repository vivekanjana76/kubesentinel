"""Validation of the Pydantic state models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.state import (
    ActionLog,
    AgentState,
    AlertPayload,
    ProposedFix,
    ReasoningOutput,
)


def test_alert_payload_defaults():
    ap = AlertPayload(name="OOMKilled")
    assert ap.severity == "warning"
    assert ap.namespace == "default"
    assert ap.pod is None
    assert ap.labels == {}


def test_proposed_fix_requires_valid_type():
    with pytest.raises(ValidationError):
        ProposedFix(
            type="not_a_real_type",  # type: ignore[arg-type]
            namespace="kubesentinel",
            target="x",
            description="y",
            command_or_diff="z",
        )


def test_reasoning_output_confidence_bounded():
    with pytest.raises(ValidationError):
        ReasoningOutput(
            diagnosis="x",
            proposed_fix=ProposedFix(
                type="kubectl_patch",
                namespace="kubesentinel",
                target="x",
                description="y",
                command_or_diff="z",
            ),
            confidence=1.5,
        )
    with pytest.raises(ValidationError):
        ReasoningOutput(
            diagnosis="x",
            proposed_fix=ProposedFix(
                type="kubectl_patch",
                namespace="kubesentinel",
                target="x",
                description="y",
                command_or_diff="z",
            ),
            confidence=-0.1,
        )


def test_agent_state_initial_values():
    state = AgentState(alert=AlertPayload(name="OOMKilled"))
    assert state.iteration == 0
    assert state.confidence == 0.0
    assert state.status == "investigating"
    assert state.pod_logs == []
    assert state.retrieved_runbooks == []
    assert state.diagnosis is None
    assert state.proposed_fix is None


def test_action_log_minimal():
    log = ActionLog(node="receive_alert", action="ingest", result="ok")
    assert log.timestamp is not None
    assert log.metadata == {}
