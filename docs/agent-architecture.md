# Agent Architecture

The KubeSentinel agent is a LangGraph state machine that turns an Alertmanager
webhook into either an applied remediation, an escalation, or a request for
human review — always ending with a markdown RCA report.

This document covers:

1. The state model
2. The node graph and conditional routing
3. The dependency-injection seam (mock vs real)
4. The reasoning prompt + structured output
5. The re-investigation loop
6. Observability
7. A sequence diagram of an end-to-end OOMKilled run

---

## 1. State model

State is a Pydantic v2 `BaseModel` (`agent/state.py:AgentState`). LangGraph
accepts the model class as the graph schema and merges partial dict updates
returned by each node.

| Field | Purpose |
|---|---|
| `alert: AlertPayload` | The incoming, normalized alert (independent of Alertmanager schema). |
| `pod_logs`, `pod_events`, `recent_commits` | Investigation findings from the toolkit. |
| `retrieved_runbooks: list[Runbook]` | Top-k matches from the Phase 2 RAG. |
| `diagnosis`, `proposed_fix`, `confidence` | Structured LLM output. |
| `actions_taken: list[ActionLog]` | Audit trail — one entry per tool/LLM call. |
| `final_report: str \| None` | Markdown RCA emitted by the `report` node. |
| `iteration`, `max_iterations` | Re-investigation loop control. |
| `status: AgentStatus` | One of `investigating`, `remediating`, `reporting`, `done`, `failed`. |

Two helper models live alongside:

- `ProposedFix`: typed `kubectl_patch | code_change | config_update` plus
  target, description, and exact command-or-diff.
- `ActionLog`: timestamped audit entry — node, action verb, result, metadata.
- `ReasoningOutput`: the schema the LLM is forced to return via
  `with_structured_output(ReasoningOutput)`.

---

## 2. Node graph

```mermaid
graph TD
    __start__([START]):::edge
    receive_alert(receive_alert)
    investigate(investigate)
    search_history(search_history)
    reason(reason)
    prepare_retry(prepare_retry)
    remediate(remediate)
    escalate(escalate)
    report(report)
    __end__([END]):::edge

    __start__ --> receive_alert
    receive_alert --> investigate
    investigate --> search_history
    search_history --> reason
    reason -.->|conf >= 0.7| remediate
    reason -.->|conf < 0.4 & retries left| prepare_retry
    reason -.->|else| escalate
    prepare_retry --> investigate
    remediate --> report
    escalate --> report
    report --> __end__

    classDef edge fill:#f2f0ff
```

(Auto-generated equivalent available via `graph.get_graph().draw_mermaid()`.)

**Node responsibilities:**

| Node | Reads | Writes |
|---|---|---|
| `receive_alert` | `state.alert` | `actions_taken`, `status` |
| `investigate` | `state.alert`, toolkit | `pod_logs`, `pod_events`, `recent_commits`, `actions_taken` |
| `search_history` | findings, retriever | `retrieved_runbooks`, `actions_taken` |
| `reason` | full state, LLM | `diagnosis`, `proposed_fix`, `confidence`, `actions_taken` |
| `prepare_retry` | `iteration`, `confidence` | bumps `iteration`, resets reasoning output, `actions_taken` |
| `remediate` | `proposed_fix`, toolkit | `actions_taken`, `status` |
| `escalate` | `confidence`, `proposed_fix` | `actions_taken`, `status` |
| `report` | full state | `final_report`, `status` |

Each node is a pure function `(state) -> dict[str, Any]`. Side-effecting
collaborators (toolkit, LLM, retriever) are bound via `functools.partial` in
`agent/graph.py:build_graph()`.

---

## 3. Dependency injection seam

```python
build_graph(toolkit=MockToolkit("OOMKilled"), llm=get_reasoning_llm(), retriever=get_retriever())
```

Phase 4 will swap a single import:

```python
build_graph(toolkit=RealToolkit(), llm=get_reasoning_llm(), retriever=get_retriever())
```

The graph code never imports a concrete toolkit. The `Toolkit` ABC
(`agent/tools/base.py`) is the only contract the graph depends on.

The mock toolkit (`agent/tools/mocks.py`) reads from
`agent/tools/fixtures/scenarios.yaml`, which currently ships four scenarios
matching the Phase 2 seed runbooks: `OOMKilled`, `HighErrorRate`,
`ImagePullBackOff`, `HighLatency`.

---

## 4. Reasoning + structured output

The `reason` node formats a prompt containing the alert, findings, recent
commits, and top-k runbooks, then calls:

```python
structured = get_structured_llm(llm, ReasoningOutput)
output: ReasoningOutput = structured.invoke(prompt)
```

`get_structured_llm()` (in `agent/llm/factory.py`) wraps
`llm.with_structured_output(schema)` with an automatic fallback:

1. Try the default — function calling (what `langchain-openai` uses by default).
2. If the model raises `NotImplementedError` or an error that mentions
   "function calling not supported", retry with `method="json_mode"`.
3. Pydantic validation on the returned object catches malformed JSON.

A structured warning (`llm.structured_output.fallback`) is emitted when the
fallback fires, so the behavior is visible in logs and LangSmith traces.

**Why this matters:** OpenRouter free-tier models have inconsistent
function-calling support. Llama 3.3 70B handles tool calling reliably; some
DeepSeek variants do not. The fallback keeps `reason` working across model
swaps without touching node code.

---

## 5. Re-investigation loop

When `confidence < confidence_low` (default 0.4) *and* there are retries left
(`iteration < max_iterations - 1`), `route_after_reason` returns
`"prepare_retry"`. The `prepare_retry` node:

1. Bumps `iteration` by one.
2. Resets `confidence`, `diagnosis`, `proposed_fix` to defaults.
3. Appends a `loop_back` `ActionLog`.

The flow then re-enters `investigate`. With a real toolkit, the second pass
may surface newer/more detailed cluster state. With the mock, the
`force_low_confidence_first_pass=True` flag is the test hook — it returns
only a slice of the fixture data on the first call.

Loop termination is guaranteed by `iteration < max_iterations - 1`: at
`iteration == max_iterations - 1` the conditional falls through to
`escalate`, regardless of confidence.

---

## 6. Observability

- **structlog**: every node emits `node.entry` and `node.exit` with
  `duration_ms` and `fields_updated`. Implemented in
  `agent/nodes/_logging.py:node_span`.
- **Audit trail**: every tool call and LLM call appends an `ActionLog`. The
  `report` node renders the full trail into the final RCA.
- **LangSmith** (opt-in): set `LANGCHAIN_TRACING_V2=true` and
  `LANGCHAIN_API_KEY` in `.env`. LangChain auto-routes traces — no code
  changes needed.

---

## 7. Sequence diagram — OOMKilled end-to-end

```mermaid
sequenceDiagram
    actor Alert as Alertmanager
    participant WH as Webhook /webhook/alert
    participant G as LangGraph (compiled)
    participant T as MockToolkit
    participant R as RunbookRetriever
    participant L as OpenRouter LLM

    Alert->>WH: POST AlertmanagerWebhookPayload
    Note over WH: Phase 3 default: AGENT_AUTOTRIGGER=false<br/>just logs and returns 200<br/>(CLI exercises the graph instead)

    Note over G: build_graph(toolkit=Mock, llm=OpenRouter, retriever=RAG)

    G->>G: receive_alert  → status=investigating
    G->>T: fetch_logs / fetch_events / fetch_recent_commits
    T-->>G: pod_logs, pod_events, recent_commits
    G->>R: retrieve("OOMKilled | MemoryError | ...", k=3)
    R-->>G: top-3 runbooks (oomkilled-pod.md first)
    G->>L: with_structured_output(ReasoningOutput).invoke(prompt)
    L-->>G: ReasoningOutput(diagnosis, proposed_fix, confidence=0.85)
    Note over G: route_after_reason: 0.85 >= 0.7 → remediate
    G->>T: apply_remediation(fix)
    T-->>G: ActionLog("would have applied kubectl_patch ...")
    G->>G: report  → final_report (markdown RCA), status=done
    G-->>WH: (CLI prints final_report)
```

---

## Adding a new scenario

1. Append a new entry to `agent/tools/fixtures/scenarios.yaml` keyed by alert name.
2. Append a `Runbook` to `docs/runbooks/` and re-run `agent.rag.ingest` so the
   RAG can return it.
3. Add the alert to `SCENARIOS` and `DEFAULT_ALERTS` in `agent/cli.py`.
4. Optionally add a `tests/agent/test_graph_<scenario>.py` assertion.
