"""Shared fixtures for agent tests.

Provides factory fixtures (`make_fake_llm`, `make_fake_retriever`) so test
files don't need to import classes from this module directly. Combined with
pre-built `ReasoningOutput` fixtures (high / mid / low confidence), this lets
us exercise the full graph deterministically without any network calls.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agent.rag.retriever import Runbook
from agent.state import ProposedFix, ReasoningOutput


class _FakeStructuredRunnable:
    def __init__(self, outputs: list[ReasoningOutput]):
        self._outputs = list(outputs)
        self.invoke_count = 0

    def invoke(self, _prompt: Any) -> ReasoningOutput:
        if not self._outputs:
            raise AssertionError("_FakeStructuredRunnable: outputs exhausted")
        self.invoke_count += 1
        return self._outputs.pop(0)


class _FakeLLM:
    """Drop-in replacement for a LangChain BaseChatModel.

    Only `with_structured_output` is wired — that's all the agent uses.
    """

    def __init__(self, outputs: list[ReasoningOutput]):
        self._runnable = _FakeStructuredRunnable(outputs)

    def with_structured_output(self, _schema, **_kw) -> _FakeStructuredRunnable:
        return self._runnable


class _FakeRetriever:
    """Returns pre-canned runbooks regardless of the query."""

    def __init__(self, runbooks: list[Runbook] | None = None):
        self._runbooks = list(runbooks or [])

    def retrieve(self, _query: str, k: int = 3) -> list[Runbook]:
        return self._runbooks[:k]


def _fix(
    fix_type: str = "kubectl_patch",
    target: str = "deployment/sacrificial -n kubesentinel",
    description: str = "raise memory limit",
    command_or_diff: str = "kubectl set resources deployment/sacrificial --limits=memory=512Mi",
) -> ProposedFix:
    return ProposedFix(
        type=fix_type,  # type: ignore[arg-type]
        target=target,
        description=description,
        command_or_diff=command_or_diff,
    )


def _runbook(title: str, source_file: str, similarity: float = 0.9, content: str = "...") -> Runbook:
    return Runbook(
        id=uuid.uuid4(),
        title=title,
        source_file=source_file,
        content=content,
        similarity=similarity,
        metadata={},
    )


@pytest.fixture
def make_fake_llm():
    def _factory(outputs: list[ReasoningOutput]) -> _FakeLLM:
        return _FakeLLM(outputs)

    return _factory


@pytest.fixture
def make_fake_retriever():
    def _factory(runbooks: list[Runbook] | None = None) -> _FakeRetriever:
        return _FakeRetriever(runbooks)

    return _factory


@pytest.fixture
def reasoning_output_high() -> ReasoningOutput:
    return ReasoningOutput(
        diagnosis="Pod was OOMKilled — memory limit (128Mi) exceeded by image batch processing.",
        proposed_fix=_fix(),
        confidence=0.85,
    )


@pytest.fixture
def reasoning_output_low() -> ReasoningOutput:
    return ReasoningOutput(
        diagnosis="Insufficient data — need recent logs.",
        proposed_fix=_fix(description="gather more data"),
        confidence=0.25,
    )


@pytest.fixture
def reasoning_output_mid() -> ReasoningOutput:
    return ReasoningOutput(
        diagnosis="Suggestive but not definitive.",
        proposed_fix=_fix(),
        confidence=0.55,
    )


@pytest.fixture
def make_runbook():
    return _runbook
