import asyncio
import os

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

APP_VERSION = "0.1.0"

app = FastAPI(title="KubeSentinel Sacrificial App", version=APP_VERSION)

Instrumentator().instrument(app).expose(app)

# Module-level store for the memory-leak endpoint — never freed intentionally.
_leak_store: list[bytes] = []


@app.get("/")
async def root():
    return {"service": "kubesentinel-sacrificial", "version": APP_VERSION, "status": "running"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    return {"status": "ready"}


@app.get("/crash")
async def crash():
    raise RuntimeError("Deliberate crash triggered for failure-mode testing")


@app.get("/memory-leak")
async def memory_leak():
    chunk = b"x" * (10 * 1024 * 1024)  # 10 MiB per call, never released
    _leak_store.append(chunk)
    total_mib = len(_leak_store) * 10
    return {"leaked_mib": total_mib, "chunks": len(_leak_store)}


@app.get("/slow")
async def slow(duration: int = int(os.getenv("SLOW_DURATION_SECONDS", "5"))):
    await asyncio.sleep(duration)
    return {"slept_seconds": duration}
