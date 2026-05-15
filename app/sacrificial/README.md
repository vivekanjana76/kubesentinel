# KubeSentinel Sacrificial App

A deliberately broken FastAPI application used to generate realistic Kubernetes failure scenarios for KubeSentinel alert testing.

Image: `kubesentinel/sacrificial:0.1.0`

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | Returns service name and version |
| `GET` | `/health` | Liveness probe — always 200 |
| `GET` | `/ready` | Readiness probe — always 200 |
| `GET` | `/metrics` | Prometheus metrics (auto-instrumented) |
| `GET` | `/crash` | Raises `RuntimeError` → HTTP 500 |
| `GET` | `/memory-leak` | Appends 10 MiB to a global list per call |
| `GET` | `/slow` | Sleeps `?duration=N` seconds (default: 5) |

---

## Failure Modes

### `GET /crash` — High Error Rate

Raises an unhandled `RuntimeError`, which FastAPI converts to an HTTP 500. Repeat calls drive the 5xx rate above the `HighErrorRate` alert threshold (> 10% of requests over 1 minute).

```bash
# Trigger repeatedly to fire the HighErrorRate alert
for i in {1..20}; do curl -s http://localhost:8080/crash; done
```

### `GET /memory-leak` — OOMKilled

Each request allocates 10 MiB and never frees it. The Kubernetes Deployment sets a 128 MiB memory limit, so after approximately 12 calls the container is OOMKilled and restarts.

```bash
# Call until the pod is killed (watch with: kubectl get pods -n kubesentinel -w)
for i in {1..15}; do curl -s http://localhost:8080/memory-leak; done
```

### `GET /slow?duration=N` — High Latency

Sleeps for `N` seconds before responding. The `HighLatency` alert fires when p95 response time exceeds 1 second. Default duration is 5 seconds (overridable via `SLOW_DURATION_SECONDS` env var or query param).

```bash
curl "http://localhost:8080/slow?duration=10"
```

### Pod Crash Loop — PodCrashLooping

The memory-leak endpoint will cause OOMKilled restarts. After 3+ restarts within 5 minutes the `PodCrashLooping` alert fires. You can also manually kill a pod:

```bash
kubectl delete pod -n kubesentinel -l app=sacrificial --wait=false
```

---

## Running Locally

```bash
# With uv (from this directory)
uv venv && uv pip install -r requirements.txt
uvicorn main:app --reload --port 8080

# With Docker
docker build -t kubesentinel/sacrificial:0.1.0 .
docker run -p 8080:8080 kubesentinel/sacrificial:0.1.0
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SLOW_DURATION_SECONDS` | `5` | Default sleep duration for `/slow` |
