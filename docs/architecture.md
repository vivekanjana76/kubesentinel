# KubeSentinel — Architecture

## System Overview

KubeSentinel is an autonomous SRE platform built around a LangGraph agent loop. The platform observes a Kubernetes cluster, detects failures through Prometheus alerting, investigates them with AI-assisted tool calls, and either remediates automatically or produces a structured incident report as a GitHub Pull Request.

---

## Top-Level Flow

```mermaid
flowchart LR
    SA["Sacrificial App<br/>(FastAPI)"] -->|"/metrics"| PM["Prometheus"]
    PM -->|"alert rules"| AM["Alertmanager"]
    AM -->|"POST /webhook/alert"| WH["Webhook<br/>Receiver"]
    WH --> AG["LangGraph<br/>Agent"]
    AG -->|"kubectl API"| K8s["K8s Cluster"]
    AG -->|"embedding search"| RAG[("pgvector<br/>RAG Store")]
    AG -->|"PR + RCA"| GH["GitHub"]
    AG -->|"notification"| SL["Slack"]
```

---

## Detailed Component Diagram

```mermaid
flowchart LR
    subgraph K8s["Kind Cluster"]
        direction TB
        SA["Sacrificial App\nFastAPI :8080"]
        PM["Prometheus\n:9090"]
        AM["Alertmanager\n:9093"]
        KSM["kube-state-metrics"]
        NE["node-exporter"]

        SA -->|"scrape /metrics"| PM
        KSM -->|"pod/node metrics"| PM
        NE -->|"host metrics"| PM
        PM -->|"rule eval"| AM
    end

    subgraph Host["Host Machine"]
        direction TB
        WH["Webhook Receiver\nFastAPI :8000"]
        AG["LangGraph Agent"]
        subgraph Tools["Agent Tools"]
            K8C["K8s API Client"]
            GHC["GitHub Client"]
            SLC["Slack Client"]
            RAGC["RAG Retriever"]
        end
    end

    RAG[("pgvector RAG\nSupabase")]
    GH["GitHub"]
    SL["Slack"]

    AM -->|"POST /webhook/alert"| WH
    WH -->|"trigger"| AG
    AG --> Tools
    K8C -->|"kubectl API"| K8s
    RAGC -->|"embedding search"| RAG
    GHC -->|"PR + RCA"| GH
    SLC -->|"notification"| SL
```

---

## Data Flow — Full Alert-to-PR Lifecycle

```mermaid
sequenceDiagram
    participant App as Sacrificial App
    participant Prom as Prometheus
    participant AM as Alertmanager
    participant WH as Webhook Receiver
    participant Agent as LangGraph Agent
    participant K8s as K8s API
    participant RAG as pgvector RAG
    participant LLM as OpenRouter LLM
    participant SL as Slack
    participant GH as GitHub

    App->>Prom: /metrics (every 15s)
    Prom->>Prom: evaluate PrometheusRules
    Prom->>AM: alert: OOMKilled (firing)
    AM->>AM: group + wait (30s)
    AM->>WH: POST /webhook/alert
    WH->>WH: validate payload (Pydantic)

    Note over WH,Agent: AGENT_AUTOTRIGGER=true: background task

    WH->>Agent: trigger investigation
    Agent->>K8s: fetch pod logs + events
    K8s-->>Agent: logs, events, recent commits
    Agent->>RAG: embedding search (top-3 runbooks)
    RAG-->>Agent: oomkilled-pod.md (similarity: 0.82)
    Agent->>LLM: structured output (ReasoningOutput)
    LLM-->>Agent: diagnosis + proposed_fix + confidence=0.85

    Note over Agent: route: confidence >= 0.7 -> remediate

    alt kubectl_patch fix
        Agent->>SL: Block Kit approval request
        SL-->>Agent: emoji reaction (approve/reject)
        Agent->>K8s: apply kubectl patch
    else code_change fix
        Agent->>GH: create branch + commit fix + RCA
        GH-->>Agent: PR #N opened
    end

    Agent->>SL: post incident summary to #incidents
    Agent->>Agent: generate markdown RCA report
```

---

## Namespace Layout

| Namespace | Contents |
|-----------|----------|
| `kubesentinel` | Sacrificial app (Deployment, Service, ServiceMonitor, PrometheusRule) |
| `monitoring` | kube-prometheus-stack (Prometheus, Alertmanager, Grafana, kube-state-metrics, node-exporter) |

---

## Phase Roadmap

### Phase 1 — Infrastructure Foundation (complete)
- Kind cluster with NodePort mappings for Prometheus/Alertmanager UIs
- Sacrificial FastAPI app with deliberate failure endpoints
- Kubernetes manifests: Deployment, Service, ServiceMonitor, PrometheusRule
- kube-prometheus-stack via Helm (laptop-tuned resource limits)
- Stub webhook receiver (validates payload, logs, returns 200)
- Makefile + PowerShell automation

### Phase 2 — RAG Memory (complete)
- 8 seed runbooks covering common K8s failure modes
- Supabase pgvector store with HNSW cosine index
- Local BGE-small-en-v1.5 embeddings (384 dims, zero API cost)
- Runbook retriever with top-k cosine similarity search
- 23 tests (chunker, retriever, ingest — all mocked)

### Phase 3 — Agent Skeleton (complete)
- LangGraph 8-node state machine with conditional routing
- MockToolkit for offline demos (4 scenarios from YAML fixtures)
- OpenRouter reasoning with structured output + JSON fallback
- CLI: demo, --all summary table
- 44 tests (21 agent + 23 RAG)

### Phase 4 — Real Tools (complete)
- RealToolkit: live K8s API, GitHub PR creation, Slack notifications
- 6 non-bypassable safety guards (namespace, resource kind, PR target, Slack channel, shell injection, dry-run)
- Slack emoji-reaction approval gate for kubectl patches
- Webhook autotrigger (FastAPI BackgroundTasks)
- CLI: live, verify-tools, demo-reset
- 122 tests (all external calls mocked)

### Phase 5 — Polish (complete)
- README rewrite for recruiter-ready presentation
- GitHub Actions CI pipeline (ruff + pytest)
- Measured performance metrics with methodology
- Architecture diagram polish, demo recording guide
- Resume content, PROJECT_HANDOFF.md

---

## Key Design Decisions

### Why Kind over Minikube?
Kind runs Kubernetes nodes as Docker containers, which integrates cleanly with Docker Desktop on Windows without requiring Hyper-V. Port mappings are defined declaratively in the cluster config YAML.

### Why a "sacrificial" app?
Having a real workload that can be deliberately broken gives Prometheus realistic metrics to scrape. This is more representative than mock data and exercises the full alert pipeline from metric → rule evaluation → Alertmanager → webhook.

### Why `host.docker.internal` for the webhook URL?
The Alertmanager runs inside the cluster; the webhook receiver runs on the host. `host.docker.internal` is Docker Desktop's DNS name for the host machine, reachable from within any container on Windows and macOS. On Linux, a static IP or `--add-host` is needed instead.

### Why 128Mi memory limit on the sacrificial app?
A realistic OOMKilled scenario requires the container's memory limit to be reachable. 128Mi is low enough that 12–15 calls to `/memory-leak` (each allocating 10 MiB) will exhaust it and trigger a container restart, exercising the `PodCrashLooping` and `HighMemoryUsage` alert rules.

### Why structlog for the webhook receiver?
structlog produces structured JSON-friendly log lines with consistent key-value pairs. This makes it straightforward to later pipe webhook logs into an observability stack or parse them in tests.
