"""
MockToolkit — Toolkit implementation backed by static YAML fixtures.

Used by Phase 3 CLI demos and the agent test suite so the graph can be
exercised end-to-end without touching a real cluster, GitHub, or Slack.

A `force_low_confidence_first_pass` flag lets tests deterministically
exercise the re-investigation loop: on the first fetch_* call, only a
slice of the fixture data is returned; subsequent calls return the
full data. Combined with a fake LLM whose first answer is low-confidence,
this guarantees one loop iteration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml

from agent.state import ActionLog, ProposedFix, _utcnow
from agent.tools.base import Toolkit

log = structlog.get_logger()

DEFAULT_FIXTURES = Path(__file__).parent / "fixtures" / "scenarios.yaml"


class MockToolkit(Toolkit):
    def __init__(
        self,
        scenario: str,
        fixtures_path: Path | str = DEFAULT_FIXTURES,
        *,
        force_low_confidence_first_pass: bool = False,
    ) -> None:
        self.scenario = scenario
        self._fixtures_path = Path(fixtures_path)
        self._force_partial = force_low_confidence_first_pass
        self._call_count = 0
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        with self._fixtures_path.open("r", encoding="utf-8") as fh:
            all_scenarios = yaml.safe_load(fh) or {}
        if self.scenario not in all_scenarios:
            raise KeyError(
                f"Scenario '{self.scenario}' not found in {self._fixtures_path}. "
                f"Available: {sorted(all_scenarios.keys())}"
            )
        return all_scenarios[self.scenario]

    def _maybe_partial(self, values: list[Any]) -> list[Any]:
        """If forcing low-confidence first pass, return only first element on
        the very first fetch. After that, return full data."""
        self._call_count += 1
        if self._force_partial and self._call_count <= 1:
            return values[:1]
        return values

    # ── Read ─────────────────────────────────────────────────────────────────

    def fetch_logs(self, namespace: str, pod_name: str) -> list[str]:
        logs = list(self._data.get("pod_logs", []))
        return self._maybe_partial(logs)

    def fetch_events(self, namespace: str, pod_name: str) -> list[dict]:
        return list(self._data.get("pod_events", []))

    def fetch_recent_commits(self, hours: int = 2) -> list[dict]:
        return list(self._data.get("recent_commits", []))

    # ── Write (recorded only — no side effects) ──────────────────────────────

    def apply_remediation(self, fix: ProposedFix) -> ActionLog:
        log.info("mock.apply_remediation", target=fix.target, type=fix.type)
        return ActionLog(
            timestamp=_utcnow(),
            node="remediate",
            action="apply_remediation",
            result=f"would have applied {fix.type} to {fix.target}",
            metadata={
                "fix_type": fix.type,
                "target": fix.target,
                "command_or_diff": fix.command_or_diff,
            },
        )

    def open_pr(self, fix: ProposedFix, rca: str) -> ActionLog:
        log.info("mock.open_pr", target=fix.target)
        return ActionLog(
            timestamp=_utcnow(),
            node="remediate",
            action="open_pr",
            result=f"would have opened PR for {fix.target}",
            metadata={"target": fix.target, "rca_length": len(rca)},
        )

    def post_slack(self, channel: str, message: str) -> ActionLog:
        log.info("mock.post_slack", channel=channel, length=len(message))
        return ActionLog(
            timestamp=_utcnow(),
            node="report",
            action="post_slack",
            result=f"would have posted to {channel}",
            metadata={"channel": channel, "message_length": len(message)},
        )
