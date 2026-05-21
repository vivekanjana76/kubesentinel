"""
Abstract toolkit interface. The graph holds a `Toolkit` reference and never
imports a concrete implementation — Phase 3 wires in `MockToolkit`, Phase 4
swaps in `RealToolkit` with zero changes to the graph code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent.state import ActionLog, ProposedFix


class Toolkit(ABC):
    """Read + write surface the agent uses to interact with the world.

    Read methods (fetch_*) are pure information retrieval. Write methods
    (apply_remediation, open_pr, post_slack) have side effects in the real
    implementation; mocks record what *would* have happened.
    """

    # ── Read ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def fetch_logs(self, namespace: str, pod_name: str) -> list[str]:
        """Return recent stdout/stderr lines for the given pod."""

    @abstractmethod
    def fetch_events(self, namespace: str, pod_name: str) -> list[dict]:
        """Return Kubernetes events scoped to the given pod."""

    @abstractmethod
    def fetch_recent_commits(self, hours: int = 2) -> list[dict]:
        """Return commits to the watched repo in the last `hours` hours."""

    # ── Write ────────────────────────────────────────────────────────────────

    @abstractmethod
    def apply_remediation(self, fix: ProposedFix) -> ActionLog:
        """Apply the proposed fix (kubectl patch / config update / etc.)."""

    @abstractmethod
    def open_pr(self, fix: ProposedFix, rca: str) -> ActionLog:
        """Open a GitHub PR carrying the proposed fix and RCA."""

    @abstractmethod
    def post_slack(self, channel: str, message: str) -> ActionLog:
        """Post a notification to Slack."""
