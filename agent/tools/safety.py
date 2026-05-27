"""
Safety guards for RealToolkit write operations.

Every guard raises SafetyViolationError when a constraint is violated. These are
hard checks — they cannot be disabled via settings or flags. The *inputs* to the
guards (allowed_namespaces, pr_target_branch) come from Settings, but the guard
logic itself is non-negotiable.

Why hard guards instead of configurable bypasses?
The agent executes LLM-generated actions against real infrastructure. If the LLM
is confused, compromised, or produces a hallucinated command, these guards are the
last line of defence before a destructive action reaches the cluster or GitHub.
"""

from __future__ import annotations

import re


class SafetyViolationError(Exception):
    """Raised when a RealToolkit operation would violate a safety boundary."""


def validate_namespace(namespace: str, allowed: list[str]) -> None:
    """Reject any operation targeting a namespace outside the configured allowlist.

    Prevents the agent from accidentally modifying production or system namespaces
    (kube-system, default, monitoring, etc.). In production this enforcement would
    be backed by a RBAC-scoped ServiceAccount that can only touch the listed
    namespaces — this guard is defence-in-depth for the local dev case.
    """
    if namespace not in allowed:
        raise SafetyViolationError(
            f"Namespace '{namespace}' is not in the allowed list {allowed}. "
            "Update ALLOWED_NAMESPACES in .env to permit this namespace."
        )


_PROTECTED_KINDS = frozenset(
    {"namespace", "persistentvolume", "persistentvolumeclaim", "pv", "pvc"}
)


def validate_resource_action(kind: str, verb: str) -> None:
    """Reject delete operations on Namespace, PersistentVolume, and PVC resources.

    Deleting these resource types is almost always catastrophic and irreversible:
    - Namespace deletion cascades to every resource inside it.
    - PV/PVC deletion can permanently destroy stateful data.
    The agent may patch or update these resources, but never delete them.
    Production would additionally use finalizer policies and admission webhooks.
    """
    if verb.lower() == "delete" and kind.lower() in _PROTECTED_KINDS:
        raise SafetyViolationError(
            f"Verb 'delete' is forbidden on resource kind '{kind}'. "
            "Deleting Namespaces, PersistentVolumes, and PVCs is irreversible. "
            "Update the fix type to a patch or config_update instead."
        )


def validate_pr_target(target_branch: str, configured: str) -> None:
    """Reject PRs targeting 'main' or any branch other than the configured target.

    Agent-created PRs always target a review branch (default: 'develop') so a
    human must approve before anything reaches the protected main branch.
    Production would enforce this via branch protection rules on the demo repo;
    this guard catches the mistake before the API call is even made.
    """
    if target_branch == "main":
        raise SafetyViolationError(
            "PRs may not target 'main' directly. "
            f"Use '{configured}' or set PR_TARGET_BRANCH in .env."
        )
    if target_branch != configured:
        raise SafetyViolationError(
            f"PR target branch '{target_branch}' does not match the configured "
            f"branch '{configured}'. Update PR_TARGET_BRANCH in .env to allow it."
        )


def validate_slack_channel(channel: str, configured: str) -> None:
    """Reject messages to any channel other than the configured incidents channel.

    Prevents the agent from spamming arbitrary channels or leaking incident
    context to unintended audiences. Both 'incidents' and '#incidents' forms
    are normalized before comparison.
    """
    normalized_channel = channel.lstrip("#")
    normalized_configured = configured.lstrip("#")
    if normalized_channel != normalized_configured:
        raise SafetyViolationError(
            f"Slack channel '{channel}' is not the configured incidents channel "
            f"'{configured}'. Update SLACK_INCIDENTS_CHANNEL in .env."
        )


# Matches shell injection vectors: semicolons, pipes, ampersands, backticks.
# Note: single & is excluded (valid in some kubectl flag values); && is caught.
_SHELL_INJECTION_RE = re.compile(r"[;`]|\|+|&&")


def validate_command_safe(command: str) -> None:
    """Reject commands containing shell injection patterns.

    The agent constructs kubectl commands from LLM output. This guard catches
    the most common injection vectors: semicolons, pipe characters, double
    ampersands, and backticks. In production, commands should be submitted
    directly to the Kubernetes API (never via shell=True), making this a
    defence-in-depth backstop against prompt-injection attacks on the LLM.
    """
    match = _SHELL_INJECTION_RE.search(command)
    if match:
        raise SafetyViolationError(
            f"Command contains shell injection pattern '{match.group()}' at "
            f"position {match.start()}. "
            "Commands must not contain ; | & backticks or similar shell operators."
        )
