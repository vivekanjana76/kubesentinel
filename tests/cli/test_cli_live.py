"""Unit tests for _resolve_live_pod in agent/cli.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.cli import _resolve_live_pod

# kubernetes is imported inside _resolve_live_pod via `import kubernetes`, so
# the correct patch targets are the kubernetes module itself, not agent.cli.kubernetes.
_LOAD_CFG = "kubernetes.config.load_kube_config"
_CORE_V1 = "kubernetes.client.CoreV1Api"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_pod(name: str, phase: str, terminating: bool = False) -> MagicMock:
    """Build a minimal mock pod matching the kubernetes client response shape."""
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.deletion_timestamp = MagicMock() if terminating else None
    pod.status.phase = phase
    return pod


def _mock_api(pods: list[MagicMock]) -> MagicMock:
    api = MagicMock()
    api.list_namespaced_pod.return_value = MagicMock(items=pods)
    return api


# ── Happy-path tests ──────────────────────────────────────────────────────────


def test_resolve_returns_first_running_pod():
    pods = [_make_pod("sacrificial-abc-111", "Running")]
    with patch(_LOAD_CFG), patch(_CORE_V1, return_value=_mock_api(pods)):
        result = _resolve_live_pod("kubesentinel")
    assert result == "sacrificial-abc-111"


def test_resolve_skips_pending_picks_running():
    pods = [
        _make_pod("sacrificial-abc-111", "Pending"),
        _make_pod("sacrificial-abc-222", "Running"),
    ]
    with patch(_LOAD_CFG), patch(_CORE_V1, return_value=_mock_api(pods)):
        result = _resolve_live_pod("kubesentinel")
    assert result == "sacrificial-abc-222"


def test_resolve_skips_terminating_picks_running():
    pods = [
        _make_pod("sacrificial-abc-old", "Running", terminating=True),
        _make_pod("sacrificial-abc-new", "Running", terminating=False),
    ]
    with patch(_LOAD_CFG), patch(_CORE_V1, return_value=_mock_api(pods)):
        result = _resolve_live_pod("kubesentinel")
    assert result == "sacrificial-abc-new"


def test_resolve_picks_first_of_multiple_running():
    pods = [
        _make_pod("sacrificial-abc-111", "Running"),
        _make_pod("sacrificial-abc-222", "Running"),
    ]
    with patch(_LOAD_CFG), patch(_CORE_V1, return_value=_mock_api(pods)):
        result = _resolve_live_pod("kubesentinel")
    assert result == "sacrificial-abc-111"


def test_resolve_passes_label_selector_to_api():
    pods = [_make_pod("sacrificial-abc-111", "Running")]
    mock_api = _mock_api(pods)
    with patch(_LOAD_CFG), patch(_CORE_V1, return_value=mock_api):
        _resolve_live_pod("kubesentinel", label_selector="app=myapp")
    mock_api.list_namespaced_pod.assert_called_once_with(
        "kubesentinel", label_selector="app=myapp"
    )


def test_resolve_uses_kubeconfig_path_when_set():
    pods = [_make_pod("sacrificial-abc-111", "Running")]
    with patch(_LOAD_CFG) as mock_load, \
         patch(_CORE_V1, return_value=_mock_api(pods)), \
         patch("agent.cli.settings") as mock_settings:
        mock_settings.kubeconfig_path = "/custom/kube/config"
        _resolve_live_pod("kubesentinel")
    mock_load.assert_called_once_with(config_file="/custom/kube/config")


def test_resolve_uses_default_kubeconfig_when_path_not_set():
    pods = [_make_pod("sacrificial-abc-111", "Running")]
    with patch(_LOAD_CFG) as mock_load, \
         patch(_CORE_V1, return_value=_mock_api(pods)), \
         patch("agent.cli.settings") as mock_settings:
        mock_settings.kubeconfig_path = None
        _resolve_live_pod("kubesentinel")
    mock_load.assert_called_once_with()


# ── Failure-path tests ────────────────────────────────────────────────────────


def test_resolve_raises_when_no_pods():
    with patch(_LOAD_CFG), patch(_CORE_V1, return_value=_mock_api([])):
        with pytest.raises(RuntimeError, match="Could not find a running pod"):
            _resolve_live_pod("kubesentinel")


def test_resolve_raises_when_only_pending_pods():
    pods = [_make_pod("sacrificial-abc-111", "Pending")]
    with patch(_LOAD_CFG), patch(_CORE_V1, return_value=_mock_api(pods)):
        with pytest.raises(RuntimeError, match="Could not find a running pod"):
            _resolve_live_pod("kubesentinel")


def test_resolve_raises_when_only_terminating_pods():
    pods = [_make_pod("sacrificial-abc-111", "Running", terminating=True)]
    with patch(_LOAD_CFG), patch(_CORE_V1, return_value=_mock_api(pods)):
        with pytest.raises(RuntimeError, match="Could not find a running pod"):
            _resolve_live_pod("kubesentinel")


def test_resolve_error_message_includes_namespace_and_selector():
    with patch(_LOAD_CFG), patch(_CORE_V1, return_value=_mock_api([])):
        with pytest.raises(RuntimeError) as exc_info:
            _resolve_live_pod("kubesentinel-demo", label_selector="app=other")
    msg = str(exc_info.value)
    assert "kubesentinel-demo" in msg
    assert "app=other" in msg
    assert "make.ps1 status" in msg
