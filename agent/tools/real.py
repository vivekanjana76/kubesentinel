"""
RealToolkit — Toolkit implementation backed by live K8s, GitHub, and Slack clients.

Authentication:
  K8s:    ~/.kube/config via kubernetes.config.load_kube_config() (configurable via
          KUBECONFIG_PATH env). Production would use load_incluster_config() with a
          ServiceAccount scoped to the allowed namespaces.
  GitHub: PAT via GITHUB_TOKEN env var. Uses PyGithub's Auth.Token pattern.
  Slack:  Bot token via SLACK_BOT_TOKEN env var. Uses slack_sdk.WebClient.

DRY_RUN mode (default True):
  Every write method (apply_remediation, open_pr, post_slack) becomes a no-op that
  logs "would have done X" and returns an ActionLog with metadata.dry_run=True.
  Safe to run anywhere — no external mutations occur.

DRY_RUN=false:
  apply_remediation for kubectl_patch goes through a Slack approval gate when
  require_slack_approval_for_patches=True. open_pr creates a real PR in the demo
  repo. post_slack sends a real message to the configured incidents channel.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from agent.settings import AgentSettings
from agent.state import ActionLog, ProposedFix
from agent.tools.base import Toolkit
from agent.tools.safety import (
    validate_command_safe,
    validate_namespace,
    validate_pr_target,
    validate_resource_action,
    validate_slack_channel,
)

log = structlog.get_logger()

# ── Exceptions ────────────────────────────────────────────────────────────────


class ApprovalDeniedError(Exception):
    """Raised when the Slack approval gate is rejected or times out."""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _kebab(text: str) -> str:
    """Convert an alert name to a kebab-case slug safe for branch names."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _branch_name(alert_name: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"agent/fix-{_kebab(alert_name)}-{ts}"


def _parse_target(target: str) -> tuple[str, str]:
    """Parse 'kind/name' into (kind, name). Falls back to ('deployment', target)."""
    if "/" in target:
        kind, name = target.split("/", 1)
        return kind.lower(), name
    return "deployment", target


def _extract_patch_body(command_or_diff: str) -> dict[str, Any]:
    """Extract a JSON patch body from a kubectl command or raw JSON string.

    Handles two forms:
      - Raw JSON: {"spec": ...}  or  [{"op": ...}]
      - kubectl command: kubectl patch ... -p '{"spec": ...}'
    """
    stripped = command_or_diff.strip()
    if stripped.startswith(("{", "[")):
        return json.loads(stripped)

    # Try to extract the -p argument value
    match = re.search(r"-p\s+'([^']+)'", command_or_diff)
    if not match:
        match = re.search(r'-p\s+"([^"]+)"', command_or_diff)
    if not match:
        match = re.search(r"-p\s+(\S+)", command_or_diff)
    if match:
        return json.loads(match.group(1))

    raise ValueError(
        f"Cannot extract patch body from command_or_diff: {command_or_diff[:120]!r}. "
        "For kubectl_patch type, command_or_diff must be valid JSON or a kubectl "
        "command with -p '<json>'."
    )


# ── RealToolkit ───────────────────────────────────────────────────────────────


class RealToolkit(Toolkit):
    """Toolkit backed by live Kubernetes, GitHub, and Slack clients.

    Parameters
    ----------
    k8s_core_v1 : kubernetes.client.CoreV1Api
    k8s_apps_v1 : kubernetes.client.AppsV1Api
    github_repo  : github.Repository.Repository  (the demo-app repo)
    slack_client : slack_sdk.WebClient
    settings     : AgentSettings
    alert_name   : str  — used to generate unique branch names per run
    """

    def __init__(
        self,
        k8s_core_v1: Any,
        k8s_apps_v1: Any,
        github_repo: Any,
        slack_client: Any,
        settings: AgentSettings,
        alert_name: str = "unknown",
    ) -> None:
        self._core_v1 = k8s_core_v1
        self._apps_v1 = k8s_apps_v1
        self._github_repo = github_repo
        self._slack = slack_client
        self._settings = settings
        self._alert_name = alert_name

    # ── Read ─────────────────────────────────────────────────────────────────

    def fetch_logs(self, namespace: str, pod_name: str) -> list[str]:
        log.info("real_toolkit.fetch_logs", namespace=namespace, pod=pod_name)
        try:
            raw: str = self._core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                tail_lines=100,
            )
            lines = [line for line in raw.splitlines() if line.strip()]
            log.info("real_toolkit.fetch_logs.done", line_count=len(lines))
            return lines
        except Exception as exc:
            log.error("real_toolkit.fetch_logs.error", error=str(exc), exc_info=True)
            raise RuntimeError(f"fetch_logs failed for {namespace}/{pod_name}: {exc}") from exc

    def fetch_events(self, namespace: str, pod_name: str) -> list[dict]:
        log.info("real_toolkit.fetch_events", namespace=namespace, pod=pod_name)
        try:
            cutoff = datetime.now(UTC) - timedelta(hours=1)
            resp = self._core_v1.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={pod_name}",
            )
            events = []
            for item in resp.items:
                ts = item.last_timestamp or item.event_time
                if ts is not None:
                    ts_aware = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
                    if ts_aware < cutoff:
                        continue
                events.append(
                    {
                        "reason": item.reason or "",
                        "message": item.message or "",
                        "timestamp": str(ts) if ts else "",
                        "type": item.type or "Normal",
                    }
                )
            log.info("real_toolkit.fetch_events.done", event_count=len(events))
            return events
        except Exception as exc:
            log.error("real_toolkit.fetch_events.error", error=str(exc), exc_info=True)
            raise RuntimeError(f"fetch_events failed for {namespace}/{pod_name}: {exc}") from exc

    def fetch_recent_commits(self, hours: int = 2) -> list[dict]:
        log.info("real_toolkit.fetch_recent_commits", hours=hours)
        try:
            since = datetime.now(UTC) - timedelta(hours=hours)
            commits = self._github_repo.get_commits(since=since)
            result = []
            for c in commits:
                result.append(
                    {
                        "sha": c.sha,
                        "message": c.commit.message,
                        "author": c.commit.author.name if c.commit.author else "unknown",
                        "changed_files": [f.filename for f in c.files],
                        "timestamp": str(c.commit.author.date) if c.commit.author else "",
                    }
                )
            log.info("real_toolkit.fetch_recent_commits.done", commit_count=len(result))
            return result
        except Exception as exc:
            log.error("real_toolkit.fetch_recent_commits.error", error=str(exc), exc_info=True)
            raise RuntimeError(f"fetch_recent_commits failed: {exc}") from exc

    # ── Write ────────────────────────────────────────────────────────────────

    def apply_remediation(self, fix: ProposedFix) -> ActionLog:
        log.info(
            "real_toolkit.apply_remediation",
            fix_type=fix.type,
            namespace=fix.namespace,
            target=fix.target,
            dry_run=self._settings.dry_run,
        )

        # Safety guards always run, even in dry_run mode.
        validate_namespace(fix.namespace, self._settings.allowed_namespaces)
        kind, _name = _parse_target(fix.target)
        validate_resource_action(kind, "patch" if fix.type == "kubectl_patch" else "apply")
        if fix.type == "kubectl_patch":
            validate_command_safe(fix.command_or_diff)

        if self._settings.dry_run:
            log.info(
                "real_toolkit.apply_remediation.dry_run",
                would_apply=fix.command_or_diff[:200],
            )
            return ActionLog(
                node="remediate",
                action="apply_remediation",
                result=f"[DRY RUN] would have applied {fix.type} to {fix.namespace}/{fix.target}",
                metadata={
                    "dry_run": True,
                    "fix_type": fix.type,
                    "namespace": fix.namespace,
                    "target": fix.target,
                    "command_or_diff": fix.command_or_diff,
                },
            )

        # Non-kubectl fix types open a PR instead of touching the cluster.
        if fix.type in ("code_change", "config_update"):
            return self.open_pr(fix, rca=fix.description)

        # kubectl_patch: optionally gate on Slack approval.
        if self._settings.require_slack_approval_for_patches:
            result = self._run_slack_approval_gate(fix)
            if result != "approved":
                raise ApprovalDeniedError(
                    f"Slack approval gate returned '{result}' for {fix.target}. "
                    "Remediation aborted."
                )

        return self._execute_k8s_patch(fix)

    def _run_slack_approval_gate(self, fix: ProposedFix) -> str:
        """Post approval request to Slack and poll for emoji-reaction response."""
        from agent.tools.slack_approval import (  # noqa: PLC0415
            post_approval_request,
            wait_for_approval,
        )

        channel = self._settings.slack_incidents_channel
        message_ts = post_approval_request(
            client=self._slack,
            channel=channel,
            fix=fix,
            rca=fix.description,
        )
        log.info(
            "real_toolkit.approval_gate.waiting",
            channel=channel,
            message_ts=message_ts,
            timeout=self._settings.slack_approval_timeout_seconds,
        )
        outcome = wait_for_approval(
            client=self._slack,
            channel=channel,
            message_ts=message_ts,
            timeout_seconds=self._settings.slack_approval_timeout_seconds,
        )
        log.info("real_toolkit.approval_gate.result", outcome=outcome)
        return outcome

    def _execute_k8s_patch(self, fix: ProposedFix) -> ActionLog:
        """Apply a kubectl_patch fix via the Kubernetes API."""
        kind, name = _parse_target(fix.target)
        namespace = fix.namespace

        try:
            patch_body = _extract_patch_body(fix.command_or_diff)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot parse patch body: {exc}") from exc

        log.info(
            "real_toolkit.k8s_patch.executing",
            kind=kind,
            name=name,
            namespace=namespace,
        )
        try:
            if kind in ("deployment",):
                self._apps_v1.patch_namespaced_deployment(name, namespace, patch_body)
            elif kind in ("statefulset",):
                self._apps_v1.patch_namespaced_stateful_set(name, namespace, patch_body)
            elif kind in ("daemonset",):
                self._apps_v1.patch_namespaced_daemon_set(name, namespace, patch_body)
            else:
                # Fall back to CoreV1 for ConfigMap, etc.
                self._core_v1.patch_namespaced_config_map(name, namespace, patch_body)
        except Exception as exc:
            log.error("real_toolkit.k8s_patch.error", error=str(exc), exc_info=True)
            raise RuntimeError(f"K8s patch failed for {namespace}/{kind}/{name}: {exc}") from exc

        log.info("real_toolkit.k8s_patch.done", kind=kind, name=name)
        return ActionLog(
            node="remediate",
            action="apply_remediation",
            result=f"applied {fix.type} to {fix.namespace}/{fix.target}",
            metadata={
                "dry_run": False,
                "fix_type": fix.type,
                "namespace": fix.namespace,
                "target": fix.target,
            },
        )

    def open_pr(self, fix: ProposedFix, rca: str) -> ActionLog:
        log.info(
            "real_toolkit.open_pr",
            namespace=fix.namespace,
            target=fix.target,
            dry_run=self._settings.dry_run,
        )

        validate_namespace(fix.namespace, self._settings.allowed_namespaces)
        validate_pr_target(self._settings.pr_target_branch, self._settings.pr_target_branch)

        branch = _branch_name(self._alert_name)

        if self._settings.dry_run:
            log.info("real_toolkit.open_pr.dry_run", branch=branch)
            return ActionLog(
                node="remediate",
                action="open_pr",
                result=f"[DRY RUN] would have opened PR on branch {branch} for {fix.target}",
                metadata={
                    "dry_run": True,
                    "branch": branch,
                    "target": fix.target,
                    "pr_target_branch": self._settings.pr_target_branch,
                },
            )

        ts_str = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        base_path = f"fixes/{ts_str}-{_kebab(self._alert_name)}"

        try:
            # Get the SHA of the target branch to create the new branch from.
            base_ref = self._github_repo.get_branch(self._settings.pr_target_branch)
            base_sha = base_ref.commit.sha

            self._github_repo.create_git_ref(
                ref=f"refs/heads/{branch}",
                sha=base_sha,
            )

            # Always commit rca.md.
            self._github_repo.create_file(
                path=f"{base_path}/rca.md",
                message=f"fix: RCA for {self._alert_name} — {fix.description[:60]}",
                content=rca.encode(),
                branch=branch,
            )

            # Type-specific artifact.
            if fix.type == "kubectl_patch":
                artifact_path = f"{base_path}/patch.sh"
                artifact_content = (
                    f"#!/usr/bin/env bash\n# KubeSentinel auto-generated patch\n"
                    f"# Alert: {self._alert_name}\n# Target: {fix.namespace}/{fix.target}\n\n"
                    f"{fix.command_or_diff}\n"
                ).encode()
            elif fix.type == "code_change":
                artifact_path = f"{base_path}/proposed.diff"
                artifact_content = fix.command_or_diff.encode()
            else:  # config_update
                artifact_path = f"{base_path}/config-patch.yaml"
                artifact_content = fix.command_or_diff.encode()

            self._github_repo.create_file(
                path=artifact_path,
                message=f"fix: {fix.type} artifact for {self._alert_name}",
                content=artifact_content,
                branch=branch,
            )

            pr = self._github_repo.create_pull(
                title=f"fix({fix.namespace}): {fix.description[:72]}",
                body=rca,
                head=branch,
                base=self._settings.pr_target_branch,
            )

            log.info("real_toolkit.open_pr.done", pr_url=pr.html_url, branch=branch)
            return ActionLog(
                node="remediate",
                action="open_pr",
                result=f"opened PR #{pr.number}: {pr.html_url}",
                metadata={
                    "dry_run": False,
                    "pr_number": pr.number,
                    "pr_url": pr.html_url,
                    "branch": branch,
                    "target": fix.target,
                },
            )
        except Exception as exc:
            log.error("real_toolkit.open_pr.error", error=str(exc), exc_info=True)
            raise RuntimeError(f"open_pr failed for {fix.target}: {exc}") from exc

    def post_slack(self, channel: str, message: str) -> ActionLog:
        log.info(
            "real_toolkit.post_slack",
            channel=channel,
            message_length=len(message),
            dry_run=self._settings.dry_run,
        )

        validate_slack_channel(channel, self._settings.slack_incidents_channel)

        if self._settings.dry_run:
            log.info("real_toolkit.post_slack.dry_run", channel=channel)
            return ActionLog(
                node="report",
                action="post_slack",
                result=f"[DRY RUN] would have posted to {channel}",
                metadata={
                    "dry_run": True,
                    "channel": channel,
                    "message_length": len(message),
                },
            )

        try:
            resp = self._slack.chat_postMessage(
                channel=channel,
                text=message,
                mrkdwn=True,
            )
            message_ts = resp["ts"]
            log.info("real_toolkit.post_slack.done", channel=channel, ts=message_ts)
            return ActionLog(
                node="report",
                action="post_slack",
                result=f"posted to {channel} (ts={message_ts})",
                metadata={
                    "dry_run": False,
                    "channel": channel,
                    "message_ts": message_ts,
                },
            )
        except Exception as exc:
            log.error("real_toolkit.post_slack.error", error=str(exc), exc_info=True)
            raise RuntimeError(f"post_slack failed for channel {channel}: {exc}") from exc


# ── Factory ───────────────────────────────────────────────────────────────────


def build_real_toolkit(settings: AgentSettings, alert_name: str = "unknown") -> RealToolkit:
    """Construct a RealToolkit by loading credentials from settings.

    Raises RuntimeError with actionable message if any service fails to
    authenticate. Run `python -m agent.cli verify-tools` to diagnose issues.
    """
    import kubernetes  # noqa: PLC0415
    from github import Auth, Github  # noqa: PLC0415
    from slack_sdk import WebClient  # noqa: PLC0415

    # ── K8s ──
    try:
        if settings.kubeconfig_path:
            kubernetes.config.load_kube_config(config_file=settings.kubeconfig_path)
        else:
            kubernetes.config.load_kube_config()
        k8s_core_v1 = kubernetes.client.CoreV1Api()
        k8s_apps_v1 = kubernetes.client.AppsV1Api()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load Kubernetes config: {exc}. "
            "Check KUBECONFIG_PATH in .env or ensure ~/.kube/config exists. "
            "Run: python -m agent.cli verify-tools"
        ) from exc

    # ── GitHub ──
    if not settings.github_token:
        raise RuntimeError(
            "GITHUB_TOKEN is not set. Add it to .env (requires repo + workflow scopes). "
            "Run: python -m agent.cli verify-tools"
        )
    try:
        gh = Github(auth=Auth.Token(settings.github_token))
        repo_full = f"{settings.github_agent_repo_owner}/{settings.github_agent_repo_name}"
        github_repo = gh.get_repo(repo_full)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to access GitHub repo '{settings.github_agent_repo_owner}/"
            f"{settings.github_agent_repo_name}': {exc}. "
            "Check GITHUB_TOKEN scope (needs full repo) and GITHUB_AGENT_REPO_OWNER/NAME. "
            "Run: python -m agent.cli verify-tools"
        ) from exc

    # ── Slack ──
    if not settings.slack_bot_token:
        raise RuntimeError(
            "SLACK_BOT_TOKEN is not set. Add it to .env (xoxb-... from OAuth & Permissions). "
            "Run: python -m agent.cli verify-tools"
        )
    slack_client = WebClient(token=settings.slack_bot_token)

    return RealToolkit(
        k8s_core_v1=k8s_core_v1,
        k8s_apps_v1=k8s_apps_v1,
        github_repo=github_repo,
        slack_client=slack_client,
        settings=settings,
        alert_name=alert_name,
    )
