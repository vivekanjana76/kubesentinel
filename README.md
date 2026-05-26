# KubeSentinel

> Autonomous AI SRE platform that detects, diagnoses, and remediates Kubernetes failures using LangGraph.

KubeSentinel watches a Kubernetes cluster via Prometheus + Alertmanager. When an alert fires, a LangGraph agent investigates using live cluster data, retrieves historical runbooks from a pgvector RAG store, and either auto-remediates or opens a GitHub Pull Request with a Root Cause Analysis and proposed fix.

---

## Architecture Overview

```mermaid
flowchart TD
    subgraph Cluster["Kind Cluster"]
        SA[Sacrificial FastAPI App\n/crash /memory-leak /slow]
        PM[Prometheus\n:9090]
        AM[Alertmanager\n:9093]
        SA -->|/metrics scrape| PM
        PM -->|alert rules| AM
    end

    subgraph Host["Host Machine"]
        WH[Webhook Receiver\nlocalhost:8000]
        AG[LangGraph Agent\nPhase 2+]
        RAG[(pgvector RAG\nSupabase)]
        GH[GitHub PR]
        SL[Slack]
    end

    AM -->|POST /webhook/alert| WH
    WH --> AG
    AG -->|kubectl / k8s API| Cluster
    AG -->|runbook lookup| RAG
    AG -->|auto-fix or PR| GH
    AG -->|notification| SL
```

**Phase 1** delivers the Kind cluster, sacrificial app, Prometheus/Alertmanager stack, and webhook receiver.  
**Phase 2** adds the RAG memory: 8 seed runbooks, Supabase pgvector schema, local BGE embeddings, and the retriever the agent will call.  
**Phase 3** builds the LangGraph agent skeleton: state machine, mock tools, real OpenRouter reasoning, end-to-end CLI.  
**Phase 4** swaps in the real toolkit (Kubernetes / GitHub / Slack) and flips webhook autotrigger on.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| Package manager | uv |
| Agent framework | LangGraph + LangChain |
| LLM provider | OpenRouter (primary), Gemini (long-context) |
| Backend | FastAPI + uvicorn |
| Cluster | Kind (Kubernetes in Docker) |
| Monitoring | Prometheus + Alertmanager (kube-prometheus-stack Helm chart) |
| Vector DB | Supabase + pgvector |
| K8s client | `kubernetes` Python SDK |

---

## Phase 1 Quick-Start

### Prerequisites

- Docker Desktop (Windows) with WSL2 backend
- [Kind v0.31+](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [Helm v3](https://helm.sh/docs/intro/install/)
- [uv](https://docs.astral.sh/uv/)
- Python 3.12 (`py -3.12 --version` should work)

### 1. Bring up the full stack

**Windows (PowerShell):**
```powershell
.\make.ps1 up
```

**Linux/macOS:**
```bash
make up
```

This will:
1. Create a Kind cluster named `kubesentinel` using `kindest/node:v1.33.0`
2. Build the sacrificial app Docker image (`kubesentinel/sacrificial:0.1.0`)
3. Load the image into Kind (no registry needed)
4. Install `kube-prometheus-stack` via Helm into the `monitoring` namespace
5. Apply all Kubernetes manifests into the `kubesentinel` namespace

> **Note:** The Helm install step (`--wait`) can take 5–8 minutes on first run.

### 2. Verify everything is running

```bash
kubectl get pods -A
```

Expected output includes pods in `Running` state for:
- `monitoring/kube-prometheus-stack-prometheus-*`
- `monitoring/kube-prometheus-stack-alertmanager-*`
- `kubesentinel/sacrificial-*` (two replicas)

Open the Prometheus UI: http://localhost:9090  
Open the Alertmanager UI: http://localhost:9093

### 3. Start the webhook receiver

```powershell
# Windows
.\make.ps1 webhook-dev

# Linux/macOS
make webhook-dev
```

This starts the FastAPI webhook receiver on `http://localhost:8000`. It will receive Alertmanager notifications.

> **Windows / host.docker.internal:** Alertmanager is configured to POST to `http://host.docker.internal:8000/webhook/alert`. On Windows with Docker Desktop, `host.docker.internal` resolves automatically. If it does not, find your LAN IP with `ipconfig` and update the `url` in `infra/helm/values.yaml`, then run `.\make.ps1 helm-install` again.

### 4. Trigger test alerts

```powershell
# Windows
.\make.ps1 break-app

# Linux/macOS
make break-app
```

This hits `/crash` (20×), `/slow` (3×), and `/memory-leak` (15×) on the sacrificial app in sequence. Within 1–2 minutes you should see `HighErrorRate`, `HighLatency`, and `HighMemoryUsage` alerts firing in Prometheus and arriving at your webhook receiver logs.

Access the sacrificial app directly:
```bash
kubectl port-forward -n kubesentinel svc/sacrificial 8080:80
curl http://localhost:8080/
curl http://localhost:8080/crash       # → 500
curl http://localhost:8080/memory-leak # → allocates 10 MiB
curl "http://localhost:8080/slow?duration=3"
```

### 5. Tear down

```powershell
.\make.ps1 down   # Windows
make down         # Linux/macOS
```

---

## Project Structure

```
KubeSentinel/
├── agent/
│   ├── webhook.py          # Phase 1+3: Alertmanager receiver (autotrigger gated)
│   ├── settings.py         # Phase 3: consolidated pydantic-settings
│   ├── state.py            # Phase 3: AgentState, ProposedFix, ActionLog, AlertPayload
│   ├── graph.py            # Phase 3: build_graph(toolkit, llm, retriever)
│   ├── cli.py              # Phase 3: py -3.12 -m agent.cli demo --scenario ...
│   ├── nodes/              # Phase 3: 7 node implementations + routing
│   ├── llm/factory.py      # Phase 3: OpenRouter / Gemini + structured-output fallback
│   ├── tools/
│   │   ├── base.py             #   Toolkit ABC
│   │   ├── mocks.py            #   MockToolkit (Phase 3 / CI)
│   │   ├── real.py             #   RealToolkit (Phase 4 — K8s/GitHub/Slack)
│   │   ├── safety.py           #   Phase 4 safety guards
│   │   ├── slack_approval.py   #   Phase 4 Block Kit + emoji-reaction polling
│   │   └── fixtures/scenarios.yaml
│   └── rag/                # Phase 2: RAG memory layer
│       ├── migrations/001_create_runbooks.sql
│       ├── migrate.py      #   py -3.12 -m agent.rag.migrate
│       ├── ingest.py       #   py -3.12 -m agent.rag.ingest [--file F] [--dry-run]
│       ├── retriever.py    #   RunbookRetriever, get_retriever()
│       └── cli.py          #   py -3.12 -m agent.rag.cli query "..."
├── app/sacrificial/        # Deliberately broken FastAPI app
├── docs/
│   ├── runbooks/           # 8 seed SRE runbooks (RAG source data)
│   ├── architecture.md
│   ├── rag-architecture.md     # Phase 2 RAG design doc
│   ├── agent-architecture.md   # Phase 3+4 agent state machine, DI seam, sequence diagram
│   ├── safety-boundaries.md    # Phase 4 safety guards + production equivalents
│   └── demo-flow.md            # Phase 4 demo recording guide + Slack scope checklist
├── infra/
│   ├── kind/cluster.yaml
│   ├── k8s/
│   └── helm/values.yaml
├── tests/
│   ├── rag/                # 23 tests: chunker, retriever, ingest
│   ├── agent/              # 21 tests: state, mocks, routing, two scenario E2Es
│   └── tools/              # 78 tests: safety guards, RealToolkit, Slack approval
├── Makefile
├── make.ps1
├── pyproject.toml
└── requirements.txt
```

---

## Phase 2: RAG Memory

### Prerequisites

In addition to Phase 1 prerequisites:
- A [Supabase](https://supabase.com) project with the `pgvector` extension enabled
- `.env` populated with `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `DATABASE_URL`
  (see `.env.example` for format; `DATABASE_URL` is the direct Postgres URI from
  Supabase Dashboard > Project Settings > Database > Connection string)

### 1. Run the schema migration

```powershell
py -3.12 -m agent.rag.migrate
```

Creates the `runbooks` table, HNSW cosine index, and `match_runbooks()` function. Idempotent — safe to re-run.

### 2. Ingest the seed runbooks

```powershell
py -3.12 -m agent.rag.ingest
```

Chunks all 8 runbooks in `docs/runbooks/`, generates embeddings locally using `BAAI/bge-small-en-v1.5`
(~130MB download on first run — expected), and upserts into Supabase. Idempotent: re-running skips unchanged chunks.

Ingest a single file:
```powershell
py -3.12 -m agent.rag.ingest --file oomkilled-pod.md
```

Preview chunks without writing:
```powershell
py -3.12 -m agent.rag.ingest --dry-run
```

### 3. Test retrieval

```powershell
py -3.12 -m agent.rag.cli query "OOMKilled with memory limit 128Mi"
py -3.12 -m agent.rag.cli query "image not found registry pull error" --k 5
```

### 4. Run tests

```powershell
py -3.12 -m pytest tests/rag/ -v
```

All 23 tests pass. Supabase and the embedding model are fully mocked — no network or GPU needed.

See [docs/rag-architecture.md](docs/rag-architecture.md) for schema design, model rationale, chunking strategy, and a retrieval sequence diagram.

---

## Phase 3: Agent Skeleton

A LangGraph state machine that turns an Alertmanager webhook into a diagnosed
fix or escalation, ending with a markdown RCA. Phase 3 ships with **mock tools
only** — real Kubernetes / GitHub / Slack integrations arrive in Phase 4.

### Architecture

```mermaid
graph LR
    A[receive_alert] --> B[investigate]
    B --> C[search_history]
    C --> D[reason]
    D -- "conf ≥ 0.7" --> E[remediate]
    D -- "conf < 0.4 + retries" --> P[prepare_retry]
    D -- "else" --> F[escalate]
    P --> B
    E --> R[report]
    F --> R
```

Full architecture, decision table, and an end-to-end sequence diagram live in
[docs/agent-architecture.md](docs/agent-architecture.md).

### Prerequisites (additional to Phase 2)

- `.env` must include `OPENROUTER_API_KEY`. Free-tier signup at
  [openrouter.ai](https://openrouter.ai).

### Run a scenario end-to-end

```powershell
py -3.12 -m agent.cli demo --scenario OOMKilled
py -3.12 -m agent.cli demo --scenario HighErrorRate
py -3.12 -m agent.cli demo --scenario ImagePullBackOff
py -3.12 -m agent.cli demo --scenario HighLatency

# Run every scenario and print a summary table
py -3.12 -m agent.cli demo --all
```

The CLI uses the `MockToolkit` (no cluster needed) and calls OpenRouter for
real reasoning. Each run prints the full structlog trace and the final
markdown RCA.

### Choosing a different LLM

Default is `meta-llama/llama-3.3-70b-instruct:free` — most consistent
tool-calling on the free tier. Every LLM call is bounded by a 90-second
timeout (`OPENROUTER_REQUEST_TIMEOUT`); when the upstream provider hangs
or rate-limits, the reason node captures the error and the iteration loop
retries / escalates as designed.

Override per `.env`:

```ini
# Rotation alternates — confirmed available on the free tier:
OPENROUTER_REASONING_MODEL=qwen/qwen3-next-80b-a3b-instruct:free
# OPENROUTER_REASONING_MODEL=z-ai/glm-4.5-air:free
# OPENROUTER_REASONING_MODEL=openai/gpt-oss-120b:free
```

Free models on OpenRouter are aggressively rate-limited upstream (HTTP 429
"temporarily rate-limited"). The agent captures the error in an `ActionLog`,
loops, and ultimately escalates if every attempt fails — that flow is by
design. For long demo sessions, set `OPENROUTER_API_KEY` to a paid key or
swap to a different free model.

The factory in `agent/llm/factory.py:get_structured_llm()` auto-falls back
from function-calling to `method="json_mode"` if a model doesn't support
tool calling, so model swaps don't require code changes.

### Adding a new scenario

1. Append YAML to `agent/tools/fixtures/scenarios.yaml`
2. Append a runbook to `docs/runbooks/`, re-run `agent.rag.ingest`
3. Add the alert name to `SCENARIOS` + `DEFAULT_ALERTS` in `agent/cli.py`

### LangSmith tracing (optional)

```ini
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=ls-...
```

LangChain auto-routes traces to LangSmith when these are set — no code
changes needed.

### Tests

```powershell
py -3.12 -m pytest tests/agent -v
```

21 agent tests + 23 RAG tests = 44 total. No real LLM, Supabase, or cluster
calls happen in CI — the LLM is replaced with a `FakeStructuredRunnable`
that returns pre-built `ReasoningOutput` instances.

---

## Phase 4: Real Tools + Webhook Autotrigger

Phase 4 swaps `MockToolkit` for `RealToolkit` (live K8s API, GitHub PR
creation, Slack notifications) and enables the Alertmanager webhook to trigger
the agent automatically via FastAPI `BackgroundTasks`.

### New `.env` variables

```ini
# External services
GITHUB_TOKEN=ghp_...                      # PAT with repo + workflow scopes
GITHUB_AGENT_REPO_OWNER=your-gh-username
GITHUB_AGENT_REPO_NAME=kubesentinel-demo-app
SLACK_BOT_TOKEN=xoxb-...                  # Bot token from OAuth & Permissions
SLACK_INCIDENTS_CHANNEL=#incidents

# Safety / mode
DRY_RUN=true                              # false → real PRs + K8s patches
AGENT_USE_REAL_TOOLS=true                 # false → MockToolkit (default)
AGENT_AUTOTRIGGER=true                    # false → webhook just logs, no agent
REQUIRE_SLACK_APPROVAL_FOR_PATCHES=true   # false → skip approval gate
ALLOWED_NAMESPACES=kubesentinel,kubesentinel-demo
PR_TARGET_BRANCH=develop
```

See `.env.example` for the full list with descriptions.

### Setup checklist

1. **Cluster up:** `.\make.ps1 up`
2. **Slack app:** Create app, add required scopes (see below), install,
   invite bot to `#incidents`.
3. **GitHub repo:** Create a demo repo; generate a PAT with `repo` + `workflow`
   scopes.
4. **`.env`:** Populate all variables above. Keep `DRY_RUN=true` until you
   have verified everything works.
5. **Verify:** `.\make.ps1 verify-tools` — all three services must show `OK`.

### Required Slack scopes

| Scope | Why |
|---|---|
| `chat:write` | Post messages to member channels |
| `chat:write.public` | Post to channels without being a member |
| `channels:read` | List channels (connectivity check) |
| `channels:history` | Read message history |
| `reactions:read` | Poll emoji reactions for the approval gate |
| `files:write` | Attach files to messages |

> After adding scopes to an already-installed app, you must reinstall it to the
> workspace. The token changes — copy the new `xoxb-` value into `.env`.

### Dry-run vs live mode

| `DRY_RUN` | Effect |
|---|---|
| `true` (default) | All write operations are no-ops; safety guards still run and log violations |
| `false` | Real K8s patches, real GitHub PRs, real Slack messages |

Start with `DRY_RUN=true`. Flip to `false` only when recording a full demo
with real cluster access and a valid GitHub repo.

### Slack approval gate

When `REQUIRE_SLACK_APPROVAL_FOR_PATCHES=true` and a `kubectl_patch` fix is
proposed, the agent posts a Block Kit message to `#incidents` and polls for
emoji reactions:

- ✅ `:white_check_mark:` → **APPROVE** — agent applies the patch
- ❌ `:x:` → **REJECT** — agent escalates instead
- No reaction within 5 minutes → **TIMEOUT** — agent escalates

This avoids requiring a publicly reachable callback URL. See
[docs/demo-flow.md](docs/demo-flow.md) for the full demo recording guide.

### Safety boundaries

Six hard guards protect every write operation (see
[docs/safety-boundaries.md](docs/safety-boundaries.md)):

1. Namespace allowlist — blocks ops outside `ALLOWED_NAMESPACES`
2. Protected resource kinds — blocks `delete` on Namespace / PV / PVC
3. PR target branch lock — blocks PRs targeting `main`
4. Slack channel lock — blocks messages to non-incident channels
5. Shell injection filter — blocks `;`, `|`, `&&`, `` ` `` in commands
6. DRY_RUN gate — short-circuits all external writes

### CLI commands (Phase 4)

```powershell
# Run with RealToolkit (requires AGENT_USE_REAL_TOOLS=true)
py -3.12 -m agent.cli live --scenario OOMKilled

# Verify K8s / GitHub / Slack connectivity
py -3.12 -m agent.cli verify-tools

# Clean up after a demo
py -3.12 -m agent.cli demo-reset
```

PowerShell equivalents:
```powershell
.\make.ps1 verify-tools
.\make.ps1 live-demo     # full end-to-end with stage markers
.\make.ps1 demo-reset
```

### Project structure (Phase 4 additions)

```
agent/tools/
├── base.py             # Toolkit ABC (unchanged)
├── mocks.py            # MockToolkit (unchanged)
├── real.py             # RealToolkit — K8s / GitHub / Slack
├── safety.py           # Six non-bypassable write guards
└── slack_approval.py   # Block Kit posting + emoji-reaction polling
docs/
├── safety-boundaries.md  # Guard details + production equivalents
└── demo-flow.md          # Step-by-step demo recording guide
```

### Tests

```powershell
py -3.12 -m pytest tests/ -v
```

122 tests total (44 Phase 3 + 78 Phase 4). No live credentials needed — all
external calls are mocked.

---

## Alerts Defined

| Alert | Condition | Severity |
|-------|-----------|----------|
| `HighErrorRate` | 5xx rate > 10% over 1 min | warning |
| `PodCrashLooping` | Pod restarts > 3 in 5 min | critical |
| `HighMemoryUsage` | Memory > 90% of 128Mi limit | warning |
| `HighLatency` | p95 latency > 1s over 5 min | warning |

---

## Development

```powershell
# Create venv and install all dependencies (Windows)
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Lint
.venv\Scripts\ruff check .

# Tests
.venv\Scripts\pytest
```
