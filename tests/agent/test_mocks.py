"""MockToolkit fixture-loading and read/write methods."""

from __future__ import annotations

import pytest

from agent.state import ProposedFix
from agent.tools.mocks import MockToolkit


@pytest.fixture(params=["OOMKilled", "HighErrorRate", "ImagePullBackOff", "HighLatency"])
def scenario(request):
    return request.param


def test_each_scenario_loads(scenario):
    t = MockToolkit(scenario)
    logs = t.fetch_logs("ns", "pod")
    events = t.fetch_events("ns", "pod")
    commits = t.fetch_recent_commits()
    assert isinstance(logs, list)
    assert isinstance(events, list)
    assert isinstance(commits, list)
    # All four scenarios have at least one commit (recent change is the lever).
    assert len(commits) >= 1


def test_oomkilled_specific_fixture():
    t = MockToolkit("OOMKilled")
    logs = t.fetch_logs("ns", "pod")
    assert any("MemoryError" in line for line in logs)
    events = t.fetch_events("ns", "pod")
    assert events[0]["reason"] == "OOMKilling"


def test_imagepullbackoff_fixture():
    t = MockToolkit("ImagePullBackOff")
    events = t.fetch_events("ns", "pod")
    assert events
    assert "manifest" in events[0]["message"]
    commits = t.fetch_recent_commits()
    assert commits[0]["sha"] == "ghi9012"


def test_unknown_scenario_raises():
    with pytest.raises(KeyError):
        MockToolkit("NotARealScenario")


def test_force_low_confidence_first_pass_returns_partial_then_full():
    t = MockToolkit("OOMKilled", force_low_confidence_first_pass=True)
    first = t.fetch_logs("ns", "pod")
    second = t.fetch_logs("ns", "pod")
    assert len(first) == 1
    assert len(second) == 3
    assert len(second) > len(first)


def test_write_methods_return_action_logs():
    t = MockToolkit("OOMKilled")
    fix = ProposedFix(
        type="kubectl_patch",
        namespace="kubesentinel",
        target="deployment/sacrificial",
        description="raise limit",
        command_or_diff="kubectl set resources ...",
    )
    a1 = t.apply_remediation(fix)
    assert a1.action == "apply_remediation"
    assert "would have applied" in a1.result

    a2 = t.open_pr(fix, "rca text")
    assert a2.action == "open_pr"

    a3 = t.post_slack("#incidents", "hello")
    assert a3.action == "post_slack"
