"""
Stub webhook receiver for Alertmanager notifications.
Validates the payload and logs it — no agent logic yet (Phase 3).
"""

import logging
from datetime import datetime
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

log = structlog.get_logger()

app = FastAPI(title="KubeSentinel Webhook Receiver", version="0.1.0")


# ── Pydantic models matching Alertmanager webhook payload schema ────────────────

class AlertLabel(BaseModel):
    model_config = {"extra": "allow"}


class AlertAnnotation(BaseModel):
    model_config = {"extra": "allow"}


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


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/webhook/alert")
async def receive_alert(payload: AlertmanagerWebhookPayload, request: Request):
    log.info(
        "alert_received",
        status=payload.status,
        receiver=payload.receiver,
        group_key=payload.groupKey,
        alert_count=len(payload.alerts),
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
    return {"status": "received", "alert_count": len(payload.alerts)}


if __name__ == "__main__":
    uvicorn.run("webhook:app", host="0.0.0.0", port=8000, reload=True)
