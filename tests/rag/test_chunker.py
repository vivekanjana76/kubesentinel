"""Tests for the Markdown chunking logic in agent.rag.ingest."""

import pathlib
import tempfile

from agent.rag.ingest import _chunk_file

SAMPLE_MD = """\
# OOMKilled Pod

## Symptoms

Pod status shows OOMKilled. The container restart count climbs rapidly.
Alert KubePodOOMKilled fires.

## Root Cause

The Linux OOM killer terminated the container because it exceeded its
memory limit. Exit code 137 = 128 + 9 (SIGKILL).

## Investigation Steps

1. Run kubectl describe pod to confirm OOMKilled reason.
2. Check container_memory_working_set_bytes in Prometheus.
3. Inspect previous container logs with --previous flag.

## Resolution

Raise the memory limit in the Deployment manifest and redeploy.
Consider adding a VPA in recommendation mode to observe actual usage.

## Prevention

Set both requests and limits for every container. Alert at 85% of limit.
"""


def _write_temp_md(content: str) -> pathlib.Path:
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    )
    tmp.write(content)
    tmp.flush()
    return pathlib.Path(tmp.name)


def test_chunk_count_is_positive():
    path = _write_temp_md(SAMPLE_MD)
    chunks = _chunk_file(path)
    assert len(chunks) > 0


def test_title_extracted_from_h1():
    path = _write_temp_md(SAMPLE_MD)
    chunks = _chunk_file(path)
    assert chunks[0].title == "OOMKilled Pod"


def test_source_file_is_filename():
    path = _write_temp_md(SAMPLE_MD)
    chunks = _chunk_file(path)
    assert all(c.source_file == path.name for c in chunks)


def test_chunk_indices_are_sequential():
    path = _write_temp_md(SAMPLE_MD)
    chunks = _chunk_file(path)
    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))


def test_each_chunk_has_content():
    path = _write_temp_md(SAMPLE_MD)
    chunks = _chunk_file(path)
    assert all(c.content.strip() for c in chunks)


def test_each_chunk_has_unique_hash():
    path = _write_temp_md(SAMPLE_MD)
    chunks = _chunk_file(path)
    hashes = [c.content_hash for c in chunks]
    assert len(set(hashes)) == len(hashes)


def test_large_section_is_split():
    # A section well over 1000 chars should produce more than one chunk.
    long_section = "## Long Section\n\n" + ("word " * 300)
    path = _write_temp_md(f"# Title\n\n{long_section}")
    chunks = _chunk_file(path)
    assert len(chunks) > 1


def test_frontmatter_title_takes_precedence():
    md_with_fm = "---\ntitle: Custom Title\n---\n# H1 Title\n\n## Section\n\nContent here.\n"
    path = _write_temp_md(md_with_fm)
    chunks = _chunk_file(path)
    assert chunks[0].title == "Custom Title"


def test_hash_is_deterministic():
    path = _write_temp_md(SAMPLE_MD)
    chunks_a = _chunk_file(path)
    chunks_b = _chunk_file(path)
    assert [c.content_hash for c in chunks_a] == [c.content_hash for c in chunks_b]
