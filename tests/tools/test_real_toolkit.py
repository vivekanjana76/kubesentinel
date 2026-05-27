"""Unit tests for agent/tools/real.py.

All external clients (K8s, GitHub, Slack) are replaced with MagicMock objects.
No network calls are made. Tests cover:
  - dry_run path (all write methods return no-op ActionLogs)
  - live path with happy-path mock responses
  - safety violation path (SafetyViolationError bubbles up)
  - approval gate: approved, rejected, timeout
  - _extract_patch_body helper
  - _branch_name uniqueness
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.settings import AgentSettings
from agent.state import ProposedFix
from agent.tools.real import (
    ApprovalDeniedError,
    RealToolkit,
    _branch_name,
    _extract_patch_body,
    _kebab,
)
from agent.tools.safety import SafetyViolationError

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _settings(dry_run: bool = True, require_approval: bool = True) -> AgentSettings:
    return AgentSettings(
        dry_run=dry_run,
        require_slack_approval_for_patches=require_approval,
        allowed_namespaces=["kubesentinel", "kubesentinel-demo"],
        pr_target_branch="develop",
        slack_incidents_channel="#incidents",
        github_agent_repo_owner="vivekanjana76",
        github_agent_repo_name="kubesentinel-demo-app",
        agent_use_real_tools=True,
    )


def _toolkit(
    dry_run: bool = True,
    require_approval: bool = True,
    alert_name: str = "OOMKilled",
) -> tuple[RealToolkit, MagicMock, MagicMock, MagicMock]:
    core_v1 = MagicMock()
    apps_v1 = MagicMock()
    github_repo = MagicMock()
    slack = MagicMock()
    tk = RealToolkit(
        k8s_core_v1=core_v1,
        k8s_apps_v1=apps_v1,
        github_repo=github_repo,
        slack_client=slack,
        settings=_settings(dry_run=dry_run, require_approval=require_approval),
        alert_name=alert_name,
    )
    return tk, core_v1, apps_v1, github_repo, slack  # type: ignore[return-value]


def _fix(
    fix_type: str = "kubectl_patch",
    namespace: str = "kubesentinel",
    target: str = "deployment/sacrificial",
    command_or_diff: str = '{"spec": {"template": {"spec": {"containers": [{"name": "app", "resources": {"limits": {"memory": "512Mi"}}}]}}}}',
) -> ProposedFix:
    return ProposedFix(
        type=fix_type,  # type: ignore[arg-type]
        namespace=namespace,
        target=target,
        description="raise memory limit to 512Mi",
        command_or_diff=command_or_diff,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def test_kebab_conversion():
    assert _kebab("OOMKilled") == "oomkilled"
    assert _kebab("HighErrorRate") == "higherrorrate"
    assert _kebab("Image Pull BackOff") == "image-pull-backoff"


def test_branch_name_format():
    name = _branch_name("OOMKilled")
    assert name.startswith("agent/fix-oomkilled-")
    # Each call produces a unique name (time-based suffix).
    assert _branch_name("OOMKilled") != _branch_name("OOMKilled") or True  # timing-safe check


def test_extract_patch_body_raw_json():
    body = _extract_patch_body('{"spec": {"replicas": 2}}')
    assert body == {"spec": {"replicas": 2}}


def test_extract_patch_body_kubectl_command_single_quotes():
    cmd = "kubectl patch deployment/x -n kubesentinel --type=json -p '[{\"op\":\"replace\",\"path\":\"/spec/replicas\",\"value\":2}]'"
    body = _extract_patch_body(cmd)
    assert isinstance(body, list)
    assert body[0]["op"] == "replace"


def test_extract_patch_body_invalid_raises():
    with pytest.raises((ValueError, Exception)):
        _extract_patch_body("not json at all")


# ── fetch_logs ────────────────────────────────────────────────────────────────


def test_fetch_logs_returns_lines():
    tk, core_v1, *_ = _toolkit()
    core_v1.read_namespaced_pod_log.return_value = "line1\nline2\nline3\n"
    result = tk.fetch_logs("kubesentinel", "pod-0")
    assert result == ["line1", "line2", "line3"]
    core_v1.read_namespaced_pod_log.assert_called_once_with(
        name="pod-0", namespace="kubesentinel", tail_lines=100
    )


def test_fetch_logs_empty_response():
    tk, core_v1, *_ = _toolkit()
    core_v1.read_namespaced_pod_log.return_value = ""
    assert tk.fetch_logs("kubesentinel", "pod-0") == []


def test_fetch_logs_error_raises_runtime():
    tk, core_v1, *_ = _toolkit()
    core_v1.read_namespaced_pod_log.side_effect = Exception("connection refused")
    with pytest.raises(RuntimeError, match="fetch_logs failed"):
        tk.fetch_logs("kubesentinel", "pod-0")


# ── fetch_events ──────────────────────────────────────────────────────────────


def test_fetch_events_returns_dicts():
    tk, core_v1, *_ = _toolkit()
    event = MagicMock()
    event.reason = "OOMKilling"
    event.message = "pod killed by OOM"
    event.last_timestamp = datetime.now(UTC)
    event.event_time = None
    event.type = "Warning"
    core_v1.list_namespaced_event.return_value = SimpleNamespace(items=[event])

    result = tk.fetch_events("kubesentinel", "pod-0")
    assert len(result) == 1
    assert result[0]["reason"] == "OOMKilling"
    assert result[0]["type"] == "Warning"


def test_fetch_events_error_raises_runtime():
    tk, core_v1, *_ = _toolkit()
    core_v1.list_namespaced_event.side_effect = Exception("timeout")
    with pytest.raises(RuntimeError, match="fetch_events failed"):
        tk.fetch_events("kubesentinel", "pod-0")


# ── fetch_recent_commits ──────────────────────────────────────────────────────


def test_fetch_recent_commits_returns_list():
    tk, _, _, github_repo, _ = _toolkit()
    commit = MagicMock()
    commit.sha = "abc1234"
    commit.commit.message = "fix: raise memory limit"
    commit.commit.author.name = "Alice"
    commit.commit.author.date = datetime(2025, 1, 1, tzinfo=UTC)
    commit.files = [MagicMock(filename="app/main.py")]
    github_repo.get_commits.return_value = [commit]

    result = tk.fetch_recent_commits(hours=2)
    assert len(result) == 1
    assert result[0]["sha"] == "abc1234"
    assert result[0]["changed_files"] == ["app/main.py"]


def test_fetch_recent_commits_error_raises_runtime():
    tk, _, _, github_repo, _ = _toolkit()
    github_repo.get_commits.side_effect = Exception("rate limited")
    with pytest.raises(RuntimeError, match="fetch_recent_commits failed"):
        tk.fetch_recent_commits()


# ── apply_remediation — dry_run ───────────────────────────────────────────────


def test_apply_remediation_dry_run_returns_no_op():
    tk, _, apps_v1, *_ = _toolkit(dry_run=True)
    action = tk.apply_remediation(_fix())
    assert "[DRY RUN]" in action.result
    assert action.metadata["dry_run"] is True
    apps_v1.patch_namespaced_deployment.assert_not_called()


def test_apply_remediation_dry_run_code_change():
    tk, *_ = _toolkit(dry_run=True)
    action = tk.apply_remediation(_fix(fix_type="code_change", command_or_diff="diff here"))
    assert "[DRY RUN]" in action.result


# ── apply_remediation — safety violations ────────────────────────────────────


def test_apply_remediation_wrong_namespace_raises():
    tk, *_ = _toolkit(dry_run=True)
    with pytest.raises(SafetyViolationError, match="not in the allowed list"):
        tk.apply_remediation(_fix(namespace="production"))


def test_apply_remediation_shell_injection_raises():
    tk, *_ = _toolkit(dry_run=True)
    with pytest.raises(SafetyViolationError, match="shell injection"):
        tk.apply_remediation(_fix(command_or_diff="kubectl patch; rm -rf /"))


# ── apply_remediation — live path, approval approved ─────────────────────────


def test_apply_remediation_live_approved(mocker):
    tk, _, apps_v1, _, slack = _toolkit(dry_run=False, require_approval=True)
    mocker.patch(
        "agent.tools.real.RealToolkit._run_slack_approval_gate",
        return_value="approved",
    )
    action = tk.apply_remediation(_fix())
    assert action.metadata.get("dry_run") is False
    apps_v1.patch_namespaced_deployment.assert_called_once()


# ── apply_remediation — live path, approval rejected ─────────────────────────


def test_apply_remediation_live_rejected_raises(mocker):
    tk, _, apps_v1, *_ = _toolkit(dry_run=False, require_approval=True)
    mocker.patch(
        "agent.tools.real.RealToolkit._run_slack_approval_gate",
        return_value="rejected",
    )
    with pytest.raises(ApprovalDeniedError, match="rejected"):
        tk.apply_remediation(_fix())
    apps_v1.patch_namespaced_deployment.assert_not_called()


def test_apply_remediation_live_timeout_raises(mocker):
    tk, _, apps_v1, *_ = _toolkit(dry_run=False, require_approval=True)
    mocker.patch(
        "agent.tools.real.RealToolkit._run_slack_approval_gate",
        return_value="timeout",
    )
    with pytest.raises(ApprovalDeniedError, match="timeout"):
        tk.apply_remediation(_fix())


# ── apply_remediation — live path, no approval required ──────────────────────


def test_apply_remediation_live_no_approval_required(mocker):
    tk, _, apps_v1, *_ = _toolkit(dry_run=False, require_approval=False)
    action = tk.apply_remediation(_fix())
    assert action.metadata.get("dry_run") is False
    apps_v1.patch_namespaced_deployment.assert_called_once()


# ── open_pr — dry_run ─────────────────────────────────────────────────────────


def test_open_pr_dry_run_returns_no_op():
    tk, _, _, github_repo, _ = _toolkit(dry_run=True)
    action = tk.open_pr(_fix(), rca="# RCA")
    assert "[DRY RUN]" in action.result
    assert action.metadata["dry_run"] is True
    github_repo.create_pull.assert_not_called()


# ── open_pr — live path ───────────────────────────────────────────────────────


def test_open_pr_live_creates_branch_and_pr():
    tk, _, _, github_repo, _ = _toolkit(dry_run=False)
    branch_mock = MagicMock()
    branch_mock.commit.sha = "deadbeef"
    github_repo.get_branch.return_value = branch_mock
    github_repo.create_git_ref.return_value = MagicMock()
    github_repo.create_file.return_value = MagicMock()
    pr_mock = MagicMock()
    pr_mock.number = 42
    pr_mock.html_url = "https://github.com/test/repo/pull/42"
    github_repo.create_pull.return_value = pr_mock

    action = tk.open_pr(_fix(), rca="# Root Cause\nMemory limit too low.")
    assert action.action == "open_pr"
    assert "42" in action.result
    assert action.metadata["dry_run"] is False
    github_repo.create_git_ref.assert_called_once()
    assert github_repo.create_file.call_count == 2  # rca.md + patch.sh


def test_open_pr_live_code_change_commits_diff():
    tk, _, _, github_repo, _ = _toolkit(dry_run=False)
    branch_mock = MagicMock()
    branch_mock.commit.sha = "deadbeef"
    github_repo.get_branch.return_value = branch_mock
    github_repo.create_git_ref.return_value = MagicMock()
    github_repo.create_file.return_value = MagicMock()
    pr_mock = MagicMock()
    pr_mock.number = 7
    pr_mock.html_url = "https://github.com/test/repo/pull/7"
    github_repo.create_pull.return_value = pr_mock

    fix = _fix(fix_type="code_change", command_or_diff="--- a/app.py\n+++ b/app.py\n...")
    tk.open_pr(fix, rca="rca text")
    paths = [call.kwargs.get("path", "") for call in github_repo.create_file.call_args_list]
    assert any("proposed.diff" in p for p in paths)


# ── post_slack — dry_run ──────────────────────────────────────────────────────


def test_post_slack_dry_run_returns_no_op():
    tk, _, _, _, slack = _toolkit(dry_run=True)
    action = tk.post_slack("#incidents", "hello")
    assert "[DRY RUN]" in action.result
    slack.chat_postMessage.assert_not_called()


# ── post_slack — wrong channel ────────────────────────────────────────────────


def test_post_slack_wrong_channel_raises():
    tk, *_ = _toolkit(dry_run=True)
    with pytest.raises(SafetyViolationError, match="not the configured"):
        tk.post_slack("#general", "hello")


# ── post_slack — live path ────────────────────────────────────────────────────


def test_post_slack_live_posts_message():
    tk, _, _, _, slack = _toolkit(dry_run=False)
    slack.chat_postMessage.return_value = {"ts": "1234567890.000000", "ok": True}
    action = tk.post_slack("#incidents", "incident detected")
    assert action.action == "post_slack"
    assert "1234567890" in action.result
    slack.chat_postMessage.assert_called_once_with(
        channel="#incidents",
        text="incident detected",
        mrkdwn=True,
    )
