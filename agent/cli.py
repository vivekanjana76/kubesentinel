"""
Agent CLI.

Usage:
    python -m agent.cli demo --scenario OOMKilled
    python -m agent.cli demo --scenario HighErrorRate
    python -m agent.cli demo --scenario ImagePullBackOff
    python -m agent.cli demo --scenario HighLatency
    python -m agent.cli demo --all

    python -m agent.cli live [--scenario OOMKilled]
    python -m agent.cli verify-tools
    python -m agent.cli demo-reset

`demo`          — runs end-to-end with MockToolkit (no external services needed).
`live`          — runs with RealToolkit against a synthetic alert. Requires
                  AGENT_USE_REAL_TOOLS=true and all credentials configured.
                  Set DRY_RUN=true (default) to preview actions without side effects.
`verify-tools`  — pings K8s, GitHub, and Slack; prints a connectivity report.
                  Run this after first-time setup to confirm credentials work.
`demo-reset`    — cleans up after a live demo: closes agent-created PRs,
                  deletes agent/fix-* branches, posts a reset notice to Slack,
                  and restores the sacrificial deployment to its healthy state.
"""

from __future__ import annotations

import subprocess
import sys

import structlog
import typer

# Windows consoles default to cp1252; reconfigure stdout/stderr to UTF-8 so
# the markdown RCA (which contains arrows, em dashes, box drawing) renders.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agent.graph import build_graph, get_default_toolkit
from agent.llm.factory import get_reasoning_llm
from agent.rag.retriever import get_retriever
from agent.settings import settings
from agent.state import AgentState, AlertPayload
from agent.tools.mocks import MockToolkit

log = structlog.get_logger()
app = typer.Typer(add_completion=False, help="KubeSentinel agent CLI")


@app.callback()
def _root() -> None:
    """KubeSentinel agent CLI. Run with `demo --scenario NAME` or `demo --all`."""
    # Forces Typer into multi-command mode so `demo` appears as a subcommand.

SCENARIOS: list[str] = ["OOMKilled", "HighErrorRate", "ImagePullBackOff", "HighLatency"]


def _resolve_live_pod(namespace: str, label_selector: str = "app=sacrificial") -> str:
    """Return the name of the first Running pod matching label_selector in namespace.

    Loads kubeconfig via KUBECONFIG_PATH (settings) or the default ~/.kube/config.
    Skips pods in Pending or Terminating (deletionTimestamp set) states.

    Raises RuntimeError with a remediation hint if no Running pod is found.
    """
    import kubernetes  # noqa: PLC0415

    if settings.kubeconfig_path:
        kubernetes.config.load_kube_config(config_file=settings.kubeconfig_path)
    else:
        kubernetes.config.load_kube_config()

    core_v1 = kubernetes.client.CoreV1Api()
    resp = core_v1.list_namespaced_pod(namespace, label_selector=label_selector)

    for pod in resp.items:
        # Skip pods being deleted (Terminating).
        if pod.metadata.deletion_timestamp is not None:
            continue
        phase = pod.status.phase if pod.status else None
        if phase == "Running":
            return pod.metadata.name

    raise RuntimeError(
        f"Could not find a running pod in '{namespace}' matching '{label_selector}'. "
        "Is the sacrificial app deployed? Run: .\\make.ps1 status"
    )

# Default alert payloads per scenario. Matches the fixture data semantically
# so the LLM has consistent context (alert + findings + runbooks all agree).
DEFAULT_ALERTS: dict[str, AlertPayload] = {
    "OOMKilled": AlertPayload(
        name="OOMKilled",
        severity="critical",
        namespace="kubesentinel",
        pod="sacrificial-0",
        summary="Pod sacrificial-0 was OOMKilled (exit code 137).",
    ),
    "HighErrorRate": AlertPayload(
        name="HighErrorRate",
        severity="warning",
        namespace="kubesentinel",
        pod="sacrificial-0",
        summary="5xx error rate exceeded 10% over 1 minute.",
    ),
    "ImagePullBackOff": AlertPayload(
        name="ImagePullBackOff",
        severity="critical",
        namespace="kubesentinel",
        pod="sacrificial-0",
        summary="Pod stuck in ImagePullBackOff: image manifest not found.",
    ),
    "HighLatency": AlertPayload(
        name="HighLatency",
        severity="warning",
        namespace="kubesentinel",
        pod="sacrificial-0",
        summary="p95 request latency exceeded 1s over 5 minutes.",
    ),
}


def _run_scenario(scenario: str) -> AgentState:
    if scenario not in SCENARIOS:
        raise typer.BadParameter(
            f"Unknown scenario '{scenario}'. Choose from: {', '.join(SCENARIOS)}"
        )

    typer.echo(f"\n{'=' * 70}\n  Scenario: {scenario}\n{'=' * 70}")

    toolkit = MockToolkit(scenario)
    llm = get_reasoning_llm()
    retriever = get_retriever()
    graph = build_graph(toolkit=toolkit, llm=llm, retriever=retriever)

    initial = AgentState(alert=DEFAULT_ALERTS[scenario])
    final_dict = graph.invoke(initial)

    # LangGraph returns a dict matching the BaseModel field set.
    final = AgentState(**final_dict)

    typer.echo("\n--- Final RCA Report ---\n")
    typer.echo(final.final_report or "(no report produced)")
    typer.echo("")
    return final


@app.command()
def demo(
    scenario: str | None = typer.Option(
        None,
        "--scenario",
        "-s",
        help=f"One of: {', '.join(SCENARIOS)}",
    ),
    all_: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Run every scenario and print a summary table.",
    ),
) -> None:
    """Run the agent end-to-end against the mock toolkit."""
    if not scenario and not all_:
        typer.echo(
            "Specify --scenario NAME or --all. See --help for available scenarios.",
            err=True,
        )
        raise typer.Exit(code=2)

    if scenario and all_:
        typer.echo("--scenario and --all are mutually exclusive.", err=True)
        raise typer.Exit(code=2)

    if scenario:
        _run_scenario(scenario)
        return

    # --all: run each scenario, then print a summary table.
    results: list[tuple[str, AgentState]] = []
    for name in SCENARIOS:
        try:
            final = _run_scenario(name)
            results.append((name, final))
        except Exception as exc:
            log.error("scenario.failed", scenario=name, error=str(exc))
            typer.echo(f"  ! {name}: failed — {exc}", err=True)

    typer.echo(f"\n{'=' * 70}\n  Summary\n{'=' * 70}")
    typer.echo(f"{'Scenario':<22}{'Status':<14}{'Conf':<8}{'Iter':<6}{'Fix':<22}")
    typer.echo("-" * 70)
    for name, final in results:
        fix_type = final.proposed_fix.type if final.proposed_fix else "—"
        typer.echo(
            f"{name:<22}{final.status:<14}{final.confidence:<8.2f}"
            f"{final.iteration:<6}{fix_type:<22}"
        )


@app.command()
def live(
    scenario: str = typer.Option(
        "OOMKilled",
        "--scenario",
        "-s",
        help=f"Synthetic alert to inject. One of: {', '.join(SCENARIOS)}",
    ),
) -> None:
    """Run the agent end-to-end with RealToolkit against a synthetic alert.

    Requires AGENT_USE_REAL_TOOLS=true and valid credentials in .env.
    With DRY_RUN=true (default), all write operations are logged but no
    external mutations are made. Run `verify-tools` first to confirm connectivity.
    """
    if not settings.agent_use_real_tools:
        typer.echo(
            "AGENT_USE_REAL_TOOLS is false — set it to true in .env to use RealToolkit.",
            err=True,
        )
        typer.echo(
            "Tip: run `python -m agent.cli verify-tools` to confirm credentials first.",
            err=True,
        )
        raise typer.Exit(code=1)

    if scenario not in SCENARIOS:
        typer.echo(
            f"Unknown scenario '{scenario}'. Choose from: {', '.join(SCENARIOS)}", err=True
        )
        raise typer.Exit(code=2)

    mode = "DRY RUN" if settings.dry_run else "LIVE"
    typer.echo(f"\n{'=' * 70}\n  Live run — scenario: {scenario}  [{mode}]\n{'=' * 70}")
    if not settings.dry_run:
        typer.echo(
            "WARNING: DRY_RUN=false — real PRs will be created and cluster patches may apply.",
            err=True,
        )

    try:
        toolkit = get_default_toolkit(alert_name=scenario)
    except RuntimeError as exc:
        typer.echo(f"Failed to initialise RealToolkit: {exc}", err=True)
        raise typer.Exit(code=1)

    # Resolve the actual pod name from the cluster — synthetic alerts use a
    # placeholder name that won't match generated pod names like sacrificial-d67755488-899ds.
    alert = DEFAULT_ALERTS[scenario]
    namespace = settings.allowed_namespaces[0]
    try:
        actual_pod = _resolve_live_pod(namespace, label_selector="app=sacrificial")
        log.info(
            "cli.live.resolved_pod",
            alert=scenario,
            synthetic_pod=alert.pod,
            actual_pod=actual_pod,
        )
        alert = alert.model_copy(update={"pod": actual_pod})
    except RuntimeError as exc:
        typer.echo(f"Pod resolution failed: {exc}", err=True)
        raise typer.Exit(code=1)

    llm = get_reasoning_llm()
    retriever = get_retriever()
    graph = build_graph(toolkit=toolkit, llm=llm, retriever=retriever)
    initial = AgentState(alert=alert)
    final_dict = graph.invoke(initial)
    final = AgentState(**final_dict)

    typer.echo("\n--- Final RCA Report ---\n")
    typer.echo(final.final_report or "(no report produced)")
    typer.echo("")


@app.command(name="verify-tools")
def verify_tools() -> None:
    """Ping K8s, GitHub, and Slack and report connectivity.

    Run this after first-time setup to confirm all credentials and scopes are
    correct before attempting a live demo. Each service is tested independently
    so partial failures are clearly identified.
    """
    typer.echo(f"\n{'=' * 60}")
    typer.echo("  KubeSentinel — Tool Connectivity Check")
    typer.echo(f"{'=' * 60}\n")

    all_ok = True

    # ── Kubernetes ────────────────────────────────────────────────────────────
    typer.echo("[ K8s ]  Checking Kubernetes API...")
    try:
        import kubernetes  # noqa: PLC0415

        if settings.kubeconfig_path:
            kubernetes.config.load_kube_config(config_file=settings.kubeconfig_path)
        else:
            kubernetes.config.load_kube_config()
        core_v1 = kubernetes.client.CoreV1Api()
        ns_list = core_v1.list_namespace(limit=1)
        ns_names = [ns.metadata.name for ns in ns_list.items]
        typer.echo(f"  OK  — connected (sample namespaces: {ns_names})\n")
    except Exception as exc:
        typer.echo(f"  FAIL — {exc}", err=True)
        typer.echo(
            "  Fix: check KUBECONFIG_PATH in .env or ensure ~/.kube/config exists.\n",
            err=True,
        )
        all_ok = False

    # ── GitHub ────────────────────────────────────────────────────────────────
    typer.echo("[ GitHub ] Checking GitHub API...")
    if not settings.github_token:
        typer.echo("  FAIL — GITHUB_TOKEN is not set in .env.", err=True)
        all_ok = False
    else:
        try:
            from github import Auth, Github  # noqa: PLC0415

            gh = Github(auth=Auth.Token(settings.github_token))
            user = gh.get_user()
            repo_full = f"{settings.github_agent_repo_owner}/{settings.github_agent_repo_name}"
            repo = gh.get_repo(repo_full)
            typer.echo(f"  OK  — authenticated as {user.login}")
            typer.echo(f"  OK  — demo repo accessible: {repo.full_name}\n")
        except Exception as exc:
            typer.echo(f"  FAIL — {exc}", err=True)
            typer.echo(
                "  Fix: check GITHUB_TOKEN scope (needs repo + workflow) and "
                "GITHUB_AGENT_REPO_OWNER / GITHUB_AGENT_REPO_NAME in .env.\n",
                err=True,
            )
            all_ok = False

    # ── Slack ─────────────────────────────────────────────────────────────────
    typer.echo("[ Slack ] Checking Slack API...")
    if not settings.slack_bot_token:
        typer.echo("  FAIL — SLACK_BOT_TOKEN is not set in .env.", err=True)
        all_ok = False
    else:
        try:
            from slack_sdk import WebClient  # noqa: PLC0415
            from slack_sdk.errors import SlackApiError  # noqa: PLC0415

            slack = WebClient(token=settings.slack_bot_token)
            auth_resp = slack.auth_test()
            bot_name = auth_resp.get("bot_id", "unknown")
            team = auth_resp.get("team", "unknown")
            typer.echo(f"  OK  — authenticated (bot={bot_name}, team={team})")

            # Check that the channel is accessible.
            channel = settings.slack_incidents_channel.lstrip("#")
            try:
                # Try to join / verify channel exists.
                slack.conversations_list(limit=200)
                typer.echo(f"  OK  — channel check passed for #{channel}\n")
            except SlackApiError as ch_exc:
                typer.echo(
                    f"  WARN — cannot verify #{channel}: {ch_exc.response['error']}. "
                    "Ensure the bot is invited: /invite @KubeSentinel\n",
                    err=True,
                )
        except Exception as exc:
            err_detail = str(exc)
            if "not_authed" in err_detail or "invalid_auth" in err_detail:
                typer.echo(
                    "  FAIL — Slack auth failed (not_authed). "
                    "Check SLACK_BOT_TOKEN starts with xoxb- and the app is installed.",
                    err=True,
                )
            elif "missing_scope" in err_detail:
                scope = err_detail.split("missing_scope:")[-1].strip() if "missing_scope:" in err_detail else "unknown"
                typer.echo(
                    f"  FAIL — Missing Slack scope: {scope}. "
                    "Required scopes: chat:write, chat:write.public, channels:read, "
                    "channels:history, reactions:read, files:write. "
                    "Add scopes and reinstall the app.",
                    err=True,
                )
            else:
                typer.echo(f"  FAIL — {exc}", err=True)
            all_ok = False

    # ── Summary ───────────────────────────────────────────────────────────────
    typer.echo(f"{'=' * 60}")
    if all_ok:
        typer.echo("  All services reachable. Ready for live demo.")
    else:
        typer.echo("  One or more services failed. Fix issues above before running live.")
        raise typer.Exit(code=1)
    typer.echo(f"{'=' * 60}\n")


@app.command(name="demo-reset")
def demo_reset() -> None:
    """Clean up after a live demo.

    1. Closes open PRs created by the agent in the demo repo.
    2. Deletes branches matching agent/fix-* in the demo repo.
    3. Posts a "demo reset" notice to the configured Slack incidents channel.
    4. Restores the sacrificial app deployment to its healthy default state.

    Requires valid credentials (GITHUB_TOKEN, SLACK_BOT_TOKEN) in .env.
    Safe to run multiple times — each step is idempotent.
    """
    typer.echo(f"\n{'=' * 60}\n  KubeSentinel — Demo Reset\n{'=' * 60}\n")

    if not settings.github_token:
        typer.echo("GITHUB_TOKEN not set — cannot clean up GitHub. Skipping.", err=True)
    else:
        try:
            from github import Auth, Github  # noqa: PLC0415

            gh = Github(auth=Auth.Token(settings.github_token))
            me = gh.get_user().login
            repo_full = f"{settings.github_agent_repo_owner}/{settings.github_agent_repo_name}"
            repo = gh.get_repo(repo_full)

            # Close open agent PRs.
            typer.echo("Closing open agent PRs...")
            closed = 0
            for pr in repo.get_pulls(state="open"):
                if pr.user.login == me and pr.head.ref.startswith("agent/fix-"):
                    pr.edit(state="closed")
                    closed += 1
                    typer.echo(f"  Closed PR #{pr.number}: {pr.title}")
            typer.echo(f"  {closed} PR(s) closed.\n")

            # Delete agent/fix-* branches.
            typer.echo("Deleting agent/fix-* branches...")
            deleted = 0
            for ref in repo.get_git_refs():
                if ref.ref.startswith("refs/heads/agent/fix-"):
                    ref.delete()
                    deleted += 1
                    typer.echo(f"  Deleted branch: {ref.ref.removeprefix('refs/heads/')}")
            typer.echo(f"  {deleted} branch(es) deleted.\n")

        except Exception as exc:
            typer.echo(f"GitHub cleanup failed: {exc}", err=True)

    # Post Slack notice.
    if not settings.slack_bot_token:
        typer.echo("SLACK_BOT_TOKEN not set — skipping Slack notification.")
    else:
        try:
            from slack_sdk import WebClient  # noqa: PLC0415

            slack = WebClient(token=settings.slack_bot_token)
            slack.chat_postMessage(
                channel=settings.slack_incidents_channel,
                text=(
                    ":recycle: *KubeSentinel Demo Reset* — "
                    "All agent-created PRs and branches have been cleaned up. "
                    "The sacrificial app is being restored to a healthy state."
                ),
                mrkdwn=True,
            )
            typer.echo(f"Slack notice posted to {settings.slack_incidents_channel}.\n")
        except Exception as exc:
            typer.echo(f"Slack notification failed: {exc}", err=True)

    # Restore sacrificial deployment.
    typer.echo("Restoring sacrificial deployment to healthy state...")
    try:
        result = subprocess.run(
            ["kubectl", "apply", "-f", "infra/k8s/sacrificial-deployment.yaml"],
            capture_output=True,
            text=True,
            check=True,
        )
        typer.echo(f"  {result.stdout.strip()}")
        typer.echo("  Deployment restored.\n")
    except subprocess.CalledProcessError as exc:
        typer.echo(f"kubectl apply failed: {exc.stderr}", err=True)
    except FileNotFoundError:
        typer.echo("kubectl not found in PATH — skipping deployment restore.", err=True)

    typer.echo(f"{'=' * 60}\n  Demo reset complete.\n{'=' * 60}\n")


if __name__ == "__main__":
    app()
