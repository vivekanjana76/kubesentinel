"""
Agent CLI.

Usage:
    python -m agent.cli demo --scenario OOMKilled
    python -m agent.cli demo --scenario HighErrorRate
    python -m agent.cli demo --scenario ImagePullBackOff
    python -m agent.cli demo --scenario HighLatency
    python -m agent.cli demo --all

Runs the LangGraph agent end-to-end with the MockToolkit and real OpenRouter
LLM. Prints node trace via structlog and the final markdown RCA. The `--all`
mode also prints a summary table at the end.

Phase 3 only — no real K8s / GitHub / Slack calls anywhere downstream.
"""

from __future__ import annotations

import sys
from typing import Optional

import structlog
import typer

from agent.graph import build_graph
from agent.llm.factory import get_reasoning_llm
from agent.rag.retriever import get_retriever
from agent.state import AgentState, AlertPayload
from agent.tools.mocks import MockToolkit

log = structlog.get_logger()
app = typer.Typer(add_completion=False, help="KubeSentinel agent CLI")


@app.callback()
def _root() -> None:
    """KubeSentinel agent CLI. Run with `demo --scenario NAME` or `demo --all`."""
    # Forces Typer into multi-command mode so `demo` appears as a subcommand.

SCENARIOS: list[str] = ["OOMKilled", "HighErrorRate", "ImagePullBackOff", "HighLatency"]

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
    scenario: Optional[str] = typer.Option(
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


if __name__ == "__main__":
    app()
