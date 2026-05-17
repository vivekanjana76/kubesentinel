"""Tests for ingest idempotency logic — Supabase client is mocked."""

import pathlib
import tempfile

import numpy as np
import pytest

from agent.rag.ingest import _chunk_file, _fetch_existing_hashes, ingest_files

SAMPLE_MD = """\
# CrashLoopBackOff

## Symptoms

Pod shows CrashLoopBackOff status. Restart count is climbing.

## Root Cause

Container exits with non-zero exit code on every start.
"""


def _write_temp_md(content: str, name: str = "test-runbook.md") -> pathlib.Path:
    d = tempfile.mkdtemp()
    p = pathlib.Path(d) / name
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def mock_supabase(mocker):
    client = mocker.MagicMock()
    # .table().select().eq().execute() → empty by default
    client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    # .table().upsert().execute() → no-op
    client.table.return_value.upsert.return_value.execute.return_value = None
    return client


@pytest.fixture()
def mock_model(mocker):
    model = mocker.MagicMock()
    model.encode.return_value = np.zeros((1, 384), dtype=np.float32)
    return model


class TestFetchExistingHashes:
    def test_returns_empty_dict_when_no_rows(self, mock_supabase):
        result = _fetch_existing_hashes(mock_supabase, "oomkilled-pod.md")
        assert result == {}

    def test_returns_hash_map(self, mock_supabase):
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"chunk_index": 0, "metadata": {"hash": "aaa"}},
            {"chunk_index": 1, "metadata": {"hash": "bbb"}},
        ]
        result = _fetch_existing_hashes(mock_supabase, "oomkilled-pod.md")
        assert result == {0: "aaa", 1: "bbb"}


class TestIngestIdempotency:
    def test_first_run_upserts_all_chunks(self, mock_supabase, mock_model, mocker):
        mocker.patch("agent.rag.ingest.create_client", return_value=mock_supabase)
        mocker.patch("agent.rag.ingest.SentenceTransformer", return_value=mock_model)

        path = _write_temp_md(SAMPLE_MD)
        chunks = _chunk_file(path)
        # Patch encode to return the right shape
        mock_model.encode.return_value = np.zeros((len(chunks), 384), dtype=np.float32)

        ingest_files([path], dry_run=False)
        upsert_call_count = mock_supabase.table.return_value.upsert.call_count
        assert upsert_call_count == len(chunks)

    def test_second_run_skips_unchanged_chunks(self, mock_supabase, mock_model, mocker):
        mocker.patch("agent.rag.ingest.create_client", return_value=mock_supabase)
        mocker.patch("agent.rag.ingest.SentenceTransformer", return_value=mock_model)

        path = _write_temp_md(SAMPLE_MD)
        chunks = _chunk_file(path)

        # Simulate DB already containing all chunks with correct hashes
        existing_data = [
            {"chunk_index": c.chunk_index, "metadata": {"hash": c.content_hash}}
            for c in chunks
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = (
            existing_data
        )

        ingest_files([path], dry_run=False)
        # No upserts should have been called — all chunks are unchanged
        mock_supabase.table.return_value.upsert.assert_not_called()

    def test_dry_run_does_not_call_supabase(self, mock_supabase, mock_model, mocker):
        create_client_mock = mocker.patch(
            "agent.rag.ingest.create_client", return_value=mock_supabase
        )
        mocker.patch("agent.rag.ingest.SentenceTransformer", return_value=mock_model)

        path = _write_temp_md(SAMPLE_MD)
        ingest_files([path], dry_run=True)

        create_client_mock.assert_not_called()
        mock_supabase.table.assert_not_called()

    def test_changed_chunk_is_upserted(self, mock_supabase, mock_model, mocker):
        mocker.patch("agent.rag.ingest.create_client", return_value=mock_supabase)
        mocker.patch("agent.rag.ingest.SentenceTransformer", return_value=mock_model)

        path = _write_temp_md(SAMPLE_MD)
        chunks = _chunk_file(path)

        # Simulate chunk 0 existing but with a stale hash
        existing_data = [
            {"chunk_index": 0, "metadata": {"hash": "stale_hash_does_not_match"}},
        ]
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = (
            existing_data
        )
        mock_model.encode.return_value = np.zeros((len(chunks), 384), dtype=np.float32)

        ingest_files([path], dry_run=False)
        # At least one upsert should occur (for the changed + any new chunks)
        assert mock_supabase.table.return_value.upsert.call_count >= 1
