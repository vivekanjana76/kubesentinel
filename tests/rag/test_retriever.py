"""Tests for RunbookRetriever — Supabase client is mocked."""

import uuid

import numpy as np
import pytest

from agent.rag.retriever import Runbook, RunbookRetriever


def _make_mock_row(**overrides) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "title": "OOMKilled Pod",
        "source_file": "oomkilled-pod.md",
        "content": "Pod status shows OOMKilled.",
        "similarity": 0.92,
        "metadata": {"hash": "abc123"},
        **overrides,
    }


@pytest.fixture()
def mock_model(mocker):
    model = mocker.MagicMock()
    model.encode.return_value = np.zeros(384, dtype=np.float32)
    return model


@pytest.fixture()
def mock_client(mocker):
    return mocker.MagicMock()


def _configure_rpc(mock_client, rows: list[dict]) -> None:
    rpc_result = mock_client.rpc.return_value
    rpc_result.execute.return_value.data = rows


class TestRunbookRetrieverRetrieve:
    def test_returns_runbook_list(self, mock_client, mock_model):
        _configure_rpc(mock_client, [_make_mock_row()])
        retriever = RunbookRetriever(client=mock_client, model=mock_model)
        results = retriever.retrieve("OOMKilled")
        assert len(results) == 1
        assert isinstance(results[0], Runbook)

    def test_empty_query_returns_empty(self, mock_client, mock_model):
        retriever = RunbookRetriever(client=mock_client, model=mock_model)
        assert retriever.retrieve("") == []
        assert retriever.retrieve("   ") == []
        mock_model.encode.assert_not_called()

    def test_k_out_of_range_raises(self, mock_client, mock_model):
        retriever = RunbookRetriever(client=mock_client, model=mock_model)
        with pytest.raises(ValueError, match="k must be between"):
            retriever.retrieve("query", k=0)
        with pytest.raises(ValueError, match="k must be between"):
            retriever.retrieve("query", k=21)

    def test_correct_rpc_called(self, mock_client, mock_model):
        _configure_rpc(mock_client, [])
        retriever = RunbookRetriever(client=mock_client, model=mock_model)
        retriever.retrieve("test query", k=5)
        mock_client.rpc.assert_called_once_with(
            "match_runbooks",
            {"query_embedding": mock_model.encode.return_value.tolist(), "match_count": 5},
        )

    def test_similarity_is_clamped(self, mock_client, mock_model):
        _configure_rpc(mock_client, [_make_mock_row(similarity=1.5)])
        retriever = RunbookRetriever(client=mock_client, model=mock_model)
        results = retriever.retrieve("query")
        assert results[0].similarity <= 1.0

    def test_invalid_row_is_skipped(self, mock_client, mock_model):
        bad_row = {"id": "not-a-uuid", "title": None}
        good_row = _make_mock_row()
        _configure_rpc(mock_client, [bad_row, good_row])
        retriever = RunbookRetriever(client=mock_client, model=mock_model)
        results = retriever.retrieve("query")
        assert len(results) == 1
        assert results[0].title == "OOMKilled Pod"

    def test_empty_db_response_returns_empty(self, mock_client, mock_model):
        _configure_rpc(mock_client, [])
        retriever = RunbookRetriever(client=mock_client, model=mock_model)
        assert retriever.retrieve("anything") == []

    def test_model_encode_called_with_query(self, mock_client, mock_model):
        _configure_rpc(mock_client, [])
        retriever = RunbookRetriever(client=mock_client, model=mock_model)
        retriever.retrieve("memory limit exceeded")
        mock_model.encode.assert_called_once_with(
            "memory limit exceeded", normalize_embeddings=True
        )
