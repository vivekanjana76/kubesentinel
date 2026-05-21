"""Shared structlog helpers for node entry/exit instrumentation."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

import structlog

log = structlog.get_logger()


@contextmanager
def node_span(node: str, **fields: Any):
    """Emit `node.entry` and `node.exit` log records with a timing in ms.

    Usage:
        with node_span("reason", iteration=state.iteration) as span:
            ...
            span["fields_updated"] = ["diagnosis", "confidence"]
    """
    extras: dict[str, Any] = {}
    start = time.perf_counter()
    log.info("node.entry", node=node, **fields)
    try:
        yield extras
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.info(
            "node.exit",
            node=node,
            duration_ms=duration_ms,
            **fields,
            **extras,
        )
