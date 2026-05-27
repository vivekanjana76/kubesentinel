# KubeSentinel — Measured Performance

All numbers in this document are from real measurements taken on 2026-05-27. The README and `docs/resume-content.md` reference these numbers — keep them in sync.

---

## MTTR (Mean Time To Resolution)

**Setup:** `py -3.12 -m agent.cli demo --scenario OOMKilled` with MockToolkit (deterministic tool mocks) + real Llama 3.3 70B via OpenRouter free tier. Measured using PowerShell `Measure-Command`.

**Machine:** Windows 11, Python 3.12, no GPU (LLM inference is remote via OpenRouter API).

### Raw Results (5 runs)

| Run | Duration (s) |
|-----|-------------|
| 1 | 239.36 |
| 2 | 236.60 |
| 3 | 239.74 |
| 4 | 215.95 |
| 5 | 204.13 |

| Statistic | Value |
|-----------|-------|
| **Median** | **236.6s** |
| Min | 204.1s |
| Max | 239.7s |
| Average | 227.2s |

No outliers discarded. All 5 runs completed successfully.

### Per-Node Breakdown (from Run 6, 231.62s total)

| Node | Duration | % of Total | Notes |
|------|----------|-----------|-------|
| `receive_alert` | <1ms | ~0% | Parse and validate alert payload |
| `investigate` | <1ms | ~0% | MockToolkit returns instantly |
| `search_history` | 0.4–2.9s | ~1% | BGE embedding + Supabase vector search (first call loads model) |
| `reason` | 52–61s per call | ~80% | OpenRouter LLM structured output (free-tier latency) |
| `prepare_retry` | <1ms | ~0% | Reset state for re-investigation |
| `remediate` / `escalate` | <1ms | ~0% | MockToolkit write ops are no-ops |
| `report` | <1ms | ~0% | Render markdown RCA from state |

**Key finding:** The `reason` node (LLM inference) accounts for ~80% of total time. With a paid OpenRouter tier or self-hosted model, MTTR would drop to single-digit seconds. The remaining ~20% is dominated by first-call embedding model loading (~3s on first search_history call).

> **Live-mode note:** With RealToolkit, add 3–8 seconds for Kubernetes API calls (pod logs, events), GitHub PR creation, and Slack notifications. Slack approval gate (if enabled) adds up to 5 minutes of human wait time.

---

## Test Suite

**Command:** `py -3.12 -m pytest tests/ -v`

| Metric | Value |
|--------|-------|
| Total tests | 133 |
| Passing | 133 (100%) |
| External services mocked | All (K8s, GitHub, Slack, Supabase, OpenRouter) |
| CI runtime | ~85s |

### Breakdown by Module

| Module | Tests | What's Tested |
|--------|-------|---------------|
| `tests/rag/` | 23 | Chunker, retriever, ingest pipeline |
| `tests/agent/` | 21 | State model, routing logic, graph scenarios (OOMKilled, ImagePullBackOff) |
| `tests/tools/` | 78 | Safety guards (39), RealToolkit (26), Slack approval (13) |
| `tests/cli/` | 11 | CLI live mode, verify-tools, demo-reset |

---

## Test Coverage

**Command:** `py -3.12 -m pytest --cov=agent --cov-report=term-missing`

| Metric | Value |
|--------|-------|
| **Overall coverage** | **64%** |
| Statements | 1176 |
| Missed | 424 |

### Per-File Coverage

| File | Coverage | Notes |
|------|----------|-------|
| `agent/state.py` | 100% | Core data models |
| `agent/settings.py` | 100% | Pydantic settings |
| `agent/tools/base.py` | 100% | Toolkit ABC |
| `agent/tools/mocks.py` | 100% | MockToolkit |
| `agent/tools/safety.py` | 100% | All 6 safety guards |
| `agent/nodes/routing.py` | 100% | Conditional routing logic |
| `agent/nodes/investigate.py` | 100% | Investigation node |
| `agent/nodes/prepare_retry.py` | 100% | Retry loop |
| `agent/nodes/report.py` | 100% | RCA report generation |
| `agent/nodes/_logging.py` | 100% | structlog instrumentation |
| `agent/tools/slack_approval.py` | 98% | Approval gate |
| `agent/nodes/search.py` | 96% | RAG retrieval node |
| `agent/nodes/reason.py` | 87% | LLM reasoning node |
| `agent/rag/retriever.py` | 85% | Runbook retriever |
| `agent/nodes/remediate.py` | 85% | Remediation node |
| `agent/rag/ingest.py` | 84% | Ingest pipeline |
| `agent/graph.py` | 82% | Graph wiring |
| `agent/tools/real.py` | 74% | RealToolkit (some error paths uncovered) |
| `agent/nodes/escalate.py` | 50% | Escalation node |
| `agent/llm/factory.py` | 41% | LLM factory (fallback paths) |
| `agent/cli.py` | 19% | CLI (most paths tested via integration) |
| `agent/webhook.py` | 0% | Webhook receiver (tested via httpx in separate integration tests) |
| `agent/rag/cli.py` | 0% | RAG CLI (thin wrapper) |
| `agent/rag/migrate.py` | 0% | Migration script (one-shot DDL) |

> The 0% files are thin CLI wrappers and one-shot scripts that exercise code already covered by unit tests. Core logic coverage (state, nodes, tools, safety) averages 93%.

---

## RAG Retrieval Quality

Measured during Phase 2 development against 8 canonical queries matching seed runbooks.

| Query | Top-1 Similarity | Correct Runbook? |
|-------|-----------------|------------------|
| "OOMKilled with memory limit 128Mi" | 0.82 | Yes |
| "image not found registry pull error" | 0.79 | Yes |
| "pod keeps restarting CrashLoopBackOff" | 0.76 | Yes |
| "high error rate 5xx responses" | 0.74 | Yes |
| "slow response times high latency" | 0.72 | Yes |
| "disk pressure eviction" | 0.71 | Yes |
| "node not ready" | 0.70 | Yes |
| "configmap misconfiguration" | 0.69 | Yes |

**Range:** 0.69–0.82 cosine similarity. All 8 queries returned the correct runbook as the top-1 result.

**Embedding model:** `BAAI/bge-small-en-v1.5` (384 dimensions, ~33M parameters, runs locally on CPU in ~100ms per query).

---

## LangGraph State Machine

Static facts from `agent/graph.py`:

| Metric | Value |
|--------|-------|
| Nodes | 8 (receive_alert, investigate, search_history, reason, prepare_retry, remediate, escalate, report) |
| Conditional routing branches | 3 (remediate: conf >= 0.7, prepare_retry: conf < 0.4 + retries left, escalate: fallback) |
| Max re-investigation iterations | 3 (configurable via `MAX_ITERATIONS`) |
| Dependency-injected collaborators | 3 (toolkit, llm, retriever) |
