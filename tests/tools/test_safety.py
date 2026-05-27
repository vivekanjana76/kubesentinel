"""Unit tests for agent/tools/safety.py.

Every guard is tested with at least one positive case (should pass) and
one negative case (should raise SafetyViolationError). Error message
content is also verified so that operational failures are easy to diagnose.
"""

from __future__ import annotations

import pytest

from agent.tools.safety import (
    SafetyViolationError,
    validate_command_safe,
    validate_namespace,
    validate_pr_target,
    validate_resource_action,
    validate_slack_channel,
)

ALLOWED = ["kubesentinel", "kubesentinel-demo"]


# ── validate_namespace ────────────────────────────────────────────────────────


def test_validate_namespace_allowed():
    validate_namespace("kubesentinel", ALLOWED)
    validate_namespace("kubesentinel-demo", ALLOWED)


def test_validate_namespace_rejects_unknown():
    with pytest.raises(SafetyViolationError, match="not in the allowed list"):
        validate_namespace("default", ALLOWED)


def test_validate_namespace_rejects_system():
    with pytest.raises(SafetyViolationError):
        validate_namespace("kube-system", ALLOWED)


def test_validate_namespace_rejects_production():
    with pytest.raises(SafetyViolationError):
        validate_namespace("production", ALLOWED)


def test_validate_namespace_empty_allowed_rejects_everything():
    with pytest.raises(SafetyViolationError):
        validate_namespace("kubesentinel", [])


# ── validate_resource_action ─────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["Namespace", "namespace", "NAMESPACE"])
def test_validate_resource_action_rejects_delete_namespace(kind):
    with pytest.raises(SafetyViolationError, match="irreversible"):
        validate_resource_action(kind, "delete")


@pytest.mark.parametrize("kind", ["PersistentVolume", "persistentvolume", "pv", "PV"])
def test_validate_resource_action_rejects_delete_pv(kind):
    with pytest.raises(SafetyViolationError):
        validate_resource_action(kind, "delete")


@pytest.mark.parametrize("kind", ["PersistentVolumeClaim", "persistentvolumeclaim", "pvc", "PVC"])
def test_validate_resource_action_rejects_delete_pvc(kind):
    with pytest.raises(SafetyViolationError):
        validate_resource_action(kind, "delete")


def test_validate_resource_action_allows_patch_on_protected():
    validate_resource_action("Namespace", "patch")
    validate_resource_action("PersistentVolume", "patch")
    validate_resource_action("PersistentVolumeClaim", "update")


def test_validate_resource_action_allows_delete_on_safe_kinds():
    validate_resource_action("Deployment", "delete")
    validate_resource_action("Pod", "delete")
    validate_resource_action("ConfigMap", "delete")


def test_validate_resource_action_verb_case_insensitive():
    with pytest.raises(SafetyViolationError):
        validate_resource_action("Namespace", "DELETE")
    with pytest.raises(SafetyViolationError):
        validate_resource_action("pv", "Delete")


# ── validate_pr_target ────────────────────────────────────────────────────────


def test_validate_pr_target_allows_configured():
    validate_pr_target("develop", "develop")


def test_validate_pr_target_rejects_main():
    with pytest.raises(SafetyViolationError, match="may not target 'main'"):
        validate_pr_target("main", "develop")


def test_validate_pr_target_rejects_main_regardless_of_configured():
    with pytest.raises(SafetyViolationError, match="may not target 'main'"):
        validate_pr_target("main", "main")


def test_validate_pr_target_rejects_mismatch():
    with pytest.raises(SafetyViolationError, match="does not match"):
        validate_pr_target("feature/something", "develop")


def test_validate_pr_target_allows_custom_configured_branch():
    validate_pr_target("staging", "staging")


# ── validate_slack_channel ────────────────────────────────────────────────────


def test_validate_slack_channel_allows_exact_match():
    validate_slack_channel("#incidents", "#incidents")


def test_validate_slack_channel_normalizes_hash_prefix():
    validate_slack_channel("incidents", "#incidents")
    validate_slack_channel("#incidents", "incidents")
    validate_slack_channel("incidents", "incidents")


def test_validate_slack_channel_rejects_wrong_channel():
    with pytest.raises(SafetyViolationError, match="not the configured incidents channel"):
        validate_slack_channel("#general", "#incidents")


def test_validate_slack_channel_rejects_random_channel():
    with pytest.raises(SafetyViolationError):
        validate_slack_channel("#random", "#incidents")


# ── validate_command_safe ─────────────────────────────────────────────────────


@pytest.mark.parametrize("cmd", [
    "kubectl patch deployment/sacrificial -n kubesentinel --type=json -p '[...]'",
    "kubectl set resources deployment/sacrificial --limits=memory=512Mi",
    "kubectl set image deployment/sacrificial app=myregistry.io/myapp:v2.3.0",
    "kubectl rollout restart deployment/sacrificial -n kubesentinel",
])
def test_validate_command_safe_allows_clean_commands(cmd):
    validate_command_safe(cmd)


@pytest.mark.parametrize("cmd,pattern", [
    ("kubectl get pods; rm -rf /", ";"),
    ("kubectl get pods && kubectl delete namespace kubesentinel", "&&"),
    ("kubectl get pods | xargs kubectl delete pod", "|"),
    ("kubectl exec pod -- `id`", "`"),
    ("kubectl apply -f file.yaml || true", "||"),
])
def test_validate_command_safe_rejects_injection(cmd, pattern):
    with pytest.raises(SafetyViolationError, match="shell injection"):
        validate_command_safe(cmd)


def test_validate_command_safe_empty_command_is_safe():
    validate_command_safe("")


def test_validate_command_safe_error_message_contains_position():
    with pytest.raises(SafetyViolationError, match="position"):
        validate_command_safe("kubectl get pods; echo pwned")
