"""
Alertmanager webhook receiver.

Phase 1 behavior: validate the incoming payload and log it.
Phase 3 behavior: if AGENT_AUTOTRIGGER=true, convert each alert into an
AlertPayload and dispatch into the LangGraph agent synchronously (mock toolkit).
Phase 4 behavior: AGENT_AUTOTRIGGER=true dispatches via FastAPI BackgroundTasks
so Alertmanager gets an immediate 200 response. The graph runs asynchronously in
the background, using get_default_toolkit() (mock or real based on settings).
AGENT_AUTOTRIGGER=false retains Phase 3 behavior: just logs and returns 200.
"""

import logging
from datetime import datetime
from typing import Any

import structlog
import uvicorn
from fastapi import BackgroundTasks, FastAPI, Request
from pydantic import BaseModel, Field

from agent.settings import settings
from agent.state import AlertPayload

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

log = structlog.get_logger()

app = FastAPI(title="KubeSentinel Webhook Receiver", version="0.3.0")


# ── Pydantic models matching Alertmanager webhook payload schema ────────────────


class Alert(BaseModel):
    status: str
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    startsAt: datetime
    endsAt: datetime
    generatorURL: str = ""
    fingerprint: str = ""


class AlertmanagerWebhookPayload(BaseModel):
    version: str
    groupKey: str
    truncatedAlerts: int = 0
    status: str
    receiver: str
    groupLabels: dict[str, Any] = Field(default_factory=dict)
    commonLabels: dict[str, Any] = Field(default_factory=dict)
    commonAnnotations: dict[str, Any] = Field(default_factory=dict)
    externalURL: str = ""
    alerts: list[Alert] = Field(default_factory=list)


# ── Translation: Alertmanager envelope -> agent AlertPayload ─────────────────


def _to_agent_alert(alert: Alert) -> AlertPayload:
    labels = alert.labels or {}
    annotations = alert.annotations or {}
    return AlertPayload(
        name=labels.get("alertname", "Unknown"),
        severity=labels.get("severity", "warning"),
        namespace=labels.get("namespace", "default"),
        pod=labels.get("pod"),
        summary=annotations.get("summary", ""),
        labels=labels,
        annotations=annotations,
        starts_at=alert.startsAt,
    )


# ── Routes ─────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "agent_autotrigger": settings.agent_autotrigger}


def _run_agent_for_alert(alert_payload: AlertPayload) -> None:
    """Run the full agent graph for a single alert in a background task.

    Imports are deferred so the webhook process doesn't pull in the full
    graph + LLM + retriever stack unless autotrigger is actually enabled.
    Exceptions are caught and logged so one failing alert doesn't kill
    background tasks for subsequent alerts.
    """
    from agent.graph import build_graph, get_default_toolkit  # noqa: PLC0415
    from agent.llm.factory import get_reasoning_llm  # noqa: PLC0415
    from agent.rag.retriever import get_retriever  # noqa: PLC0415
    from agent.state import AgentState  # noqa: PLC0415

    try:
        toolkit = get_default_toolkit(alert_name=alert_payload.name)
        graph = build_graph(
            toolkit=toolkit,
            llm=get_reasoning_llm(),
            retriever=get_retriever(),
        )
        final = graph.invoke(AgentState(alert=alert_payload))
        log.info(
            "agent.background_run.done",
            alert=alert_payload.name,
            status=final.get("status"),
        )
    except Exception as exc:
        log.error(
            "agent.background_run.error",
            alert=alert_payload.name,
            error=str(exc),
            exc_info=True,
        )


@app.post("/webhook/alert")
async def receive_alert(
    payload: AlertmanagerWebhookPayload,
    request: Request,
    background_tasks: BackgroundTasks,
):
    log.info(
        "alert_received",
        status=payload.status,
        receiver=payload.receiver,
        group_key=payload.groupKey,
        alert_count=len(payload.alerts),
        autotrigger=settings.agent_autotrigger,
        alerts=[
            {
                "name": a.labels.get("alertname"),
                "severity": a.labels.get("severity"),
                "namespace": a.labels.get("namespace"),
                "status": a.status,
            }
            for a in payload.alerts
        ],
    )

    if not settings.agent_autotrigger:
        return {"status": "received", "alert_count": len(payload.alerts)}

    # Autotrigger path: dispatch each alert into a background task so
    # Alertmanager gets an immediate 200 and doesn't retry on timeout.
    dispatched = []
    for a in payload.alerts:
        ap = _to_agent_alert(a)
        background_tasks.add_task(_run_agent_for_alert, ap)
        dispatched.append({"alert": ap.name, "queued": True})
        log.info("agent.background_run.queued", alert=ap.name)

    return {
        "status": "received",
        "alert_count": len(payload.alerts),
        "dispatched": dispatched,
    }


if __name__ == "__main__":
    uvicorn.run("webhook:app", host="0.0.0.0", port=8000, reload=True)
