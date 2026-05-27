"""Unit tests for agent/tools/slack_approval.py.

All Slack API calls are mocked. Tests cover:
  - post_approval_request: verifies Block Kit message structure and returns ts
  - wait_for_approval: approved, rejected, timeout, poll error recovery
  - polling terminates promptly on first matching reaction
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent.state import ProposedFix
from agent.tools.slack_approval import (
    APPROVE_EMOJI,
    REJECT_EMOJI,
    post_approval_request,
    wait_for_approval,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _fix(
    fix_type: str = "kubectl_patch",
    namespace: str = "kubesentinel",
    target: str = "deployment/sacrificial",
) -> ProposedFix:
    return ProposedFix(
        type=fix_type,  # type: ignore[arg-type]
        namespace=namespace,
        target=target,
        description="raise memory limit to 512Mi",
        command_or_diff='{"spec": {"containers": [{"resources": {"limits": {"memory": "512Mi"}}}]}}',
    )


def _reactions_resp(*names: str) -> dict:
    """Build a fake reactions.get response containing the given emoji names."""
    return {
        "message": {
            "reactions": [{"name": n, "count": 1, "users": ["U123"]} for n in names]
        }
    }


# ── post_approval_request ─────────────────────────────────────────────────────


def test_post_approval_request_returns_ts():
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1234567890.000001", "ok": True}
    ts = post_approval_request(client, "#incidents", _fix(), rca="OOM diagnosis")
    assert ts == "1234567890.000001"


def test_post_approval_request_calls_chat_postmessage():
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1111.0", "ok": True}
    post_approval_request(client, "#incidents", _fix(), rca="some rca")
    client.chat_postMessage.assert_called_once()
    kwargs = client.chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == "#incidents"
    assert "blocks" in kwargs
    assert len(kwargs["blocks"]) >= 4


def test_post_approval_request_blocks_contain_namespace_and_target():
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1111.0", "ok": True}
    fix = _fix(namespace="kubesentinel-demo", target="deployment/app")
    post_approval_request(client, "#incidents", fix, rca="")
    kwargs = client.chat_postMessage.call_args.kwargs
    blocks_text = str(kwargs["blocks"])
    assert "kubesentinel-demo" in blocks_text
    assert "deployment/app" in blocks_text


def test_post_approval_request_includes_approve_reject_instructions():
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1111.0", "ok": True}
    post_approval_request(client, "#incidents", _fix(), rca="")
    kwargs = client.chat_postMessage.call_args.kwargs
    blocks_text = str(kwargs["blocks"])
    assert "APPROVE" in blocks_text
    assert "REJECT" in blocks_text


def test_post_approval_request_omits_rca_block_when_empty():
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1111.0", "ok": True}
    post_approval_request(client, "#incidents", _fix(), rca="")
    kwargs = client.chat_postMessage.call_args.kwargs
    blocks_text = str(kwargs["blocks"])
    assert "Diagnosis / RCA" not in blocks_text


def test_post_approval_request_includes_rca_when_provided():
    client = MagicMock()
    client.chat_postMessage.return_value = {"ts": "1111.0", "ok": True}
    post_approval_request(client, "#incidents", _fix(), rca="Memory limit exceeded.")
    blocks_text = str(client.chat_postMessage.call_args.kwargs["blocks"])
    assert "Memory limit exceeded" in blocks_text


# ── wait_for_approval — approved ──────────────────────────────────────────────


def test_wait_for_approval_approved_on_first_poll():
    client = MagicMock()
    client.reactions_get.return_value = _reactions_resp(APPROVE_EMOJI)
    with patch("agent.tools.slack_approval.time.sleep"):
        result = wait_for_approval(client, "#incidents", "ts1", timeout_seconds=30)
    assert result == "approved"
    client.reactions_get.assert_called_once_with(channel="#incidents", timestamp="ts1")


def test_wait_for_approval_approved_on_second_poll():
    client = MagicMock()
    client.reactions_get.side_effect = [
        _reactions_resp(),
        _reactions_resp(APPROVE_EMOJI),
    ]
    with patch("agent.tools.slack_approval.time.sleep"):
        with patch("agent.tools.slack_approval.time.monotonic") as mock_mono:
            mock_mono.side_effect = [0, 1, 2, 3, 40]  # deadline=30, never hit
            result = wait_for_approval(client, "#incidents", "ts1", timeout_seconds=30)
    assert result == "approved"
    assert client.reactions_get.call_count == 2


# ── wait_for_approval — rejected ──────────────────────────────────────────────


def test_wait_for_approval_rejected_on_first_poll():
    client = MagicMock()
    client.reactions_get.return_value = _reactions_resp(REJECT_EMOJI)
    with patch("agent.tools.slack_approval.time.sleep"):
        result = wait_for_approval(client, "#incidents", "ts1", timeout_seconds=30)
    assert result == "rejected"


def test_wait_for_approval_rejected_after_empty_polls():
    client = MagicMock()
    client.reactions_get.side_effect = [
        _reactions_resp(),
        _reactions_resp(),
        _reactions_resp(REJECT_EMOJI),
    ]
    with patch("agent.tools.slack_approval.time.sleep"):
        with patch("agent.tools.slack_approval.time.monotonic") as mock_mono:
            mock_mono.side_effect = [0, 1, 2, 3, 4, 5, 6, 40]
            result = wait_for_approval(client, "#incidents", "ts1", timeout_seconds=30)
    assert result == "rejected"


# ── wait_for_approval — timeout ───────────────────────────────────────────────


def test_wait_for_approval_times_out():
    client = MagicMock()
    client.reactions_get.return_value = _reactions_resp()
    with patch("agent.tools.slack_approval.time.sleep"):
        with patch("agent.tools.slack_approval.time.monotonic") as mock_mono:
            # deadline = 0 + 5 = 5; immediately past deadline
            mock_mono.side_effect = [0, 6]
            result = wait_for_approval(client, "#incidents", "ts1", timeout_seconds=5)
    assert result == "timeout"


# ── wait_for_approval — poll error recovery ───────────────────────────────────


def test_wait_for_approval_recovers_from_poll_error():
    client = MagicMock()
    client.reactions_get.side_effect = [
        Exception("temporary network error"),
        _reactions_resp(APPROVE_EMOJI),
    ]
    with patch("agent.tools.slack_approval.time.sleep"):
        with patch("agent.tools.slack_approval.time.monotonic") as mock_mono:
            mock_mono.side_effect = [0, 1, 2, 3, 40]
            result = wait_for_approval(client, "#incidents", "ts1", timeout_seconds=30)
    assert result == "approved"


def test_wait_for_approval_approve_beats_reject_if_both_present():
    client = MagicMock()
    # Both emojis present — approve should win (checked first).
    client.reactions_get.return_value = _reactions_resp(APPROVE_EMOJI, REJECT_EMOJI)
    with patch("agent.tools.slack_approval.time.sleep"):
        result = wait_for_approval(client, "#incidents", "ts1", timeout_seconds=30)
    assert result == "approved"
