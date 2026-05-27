# KubeSentinel — Resume Content

All numbers in this document are sourced from [docs/metrics.md](metrics.md). Do not edit numbers here without updating metrics.md first.

---

## Resume Bullet Points (AI Engineer Angle)

- Designed and built an autonomous SRE agent using LangGraph that detects Kubernetes failures via Prometheus/Alertmanager, investigates using live cluster data and RAG-retrieved runbooks, and opens GitHub PRs with structured Root Cause Analyses
- Architected an eight-node LangGraph state machine with three conditional routing branches (remediate, retry, escalate), achieving 236.6s median time-to-resolution across four failure scenarios
- Built a RAG pipeline with local BGE-small-en-v1.5 embeddings (384 dimensions, zero API cost) and Supabase pgvector, achieving 0.69–0.82 cosine similarity across eight canonical runbook queries
- Implemented six non-bypassable safety guards (namespace allowlist, resource-kind protection, PR target lock, Slack channel lock, shell injection filter, dry-run gate) to constrain LLM-generated actions
- Engineered a dependency-injection seam (`MockToolkit` / `RealToolkit`) enabling full-graph testing without external services — 64% overall coverage (93% core logic), 133 tests, all mocked
- Integrated three external services (Kubernetes API, GitHub, Slack) with a Slack emoji-reaction approval gate for kubectl patches, eliminating the need for publicly reachable callback URLs
- Achieved structured LLM output with automatic JSON-mode fallback for models lacking function-calling support, enabling zero-code model swaps across OpenRouter's free tier

---

## LinkedIn Project Entry

**KubeSentinel — Autonomous AI-Powered SRE Platform**

Built an end-to-end autonomous SRE system that monitors a Kubernetes cluster, detects failures through Prometheus alerting, and remediates them using an AI agent. The core is a LangGraph state machine that investigates incidents by pulling live pod logs and events, retrieves relevant runbooks from a pgvector RAG store, and uses an LLM to diagnose root causes with structured output. The agent either applies a fix directly (with Slack approval) or opens a GitHub Pull Request containing a full Root Cause Analysis.

The system includes six safety guards that constrain every LLM-generated action, a dependency-injection architecture that enables full testing without external services (64% overall, 93% core logic coverage across 133 tests), and a RAG pipeline using local embeddings at zero API cost. Median time-to-resolution is 236.6s from alert to remediation PR.

Tech: Python, LangGraph, LangChain, FastAPI, Kubernetes (Kind), Prometheus, Alertmanager, Supabase (pgvector), sentence-transformers, OpenRouter, GitHub Actions

---

## Elevator Pitches

**AI Engineer angle:**
KubeSentinel is a LangGraph agent that autonomously diagnoses Kubernetes failures using RAG-retrieved runbooks and structured LLM output, then opens GitHub PRs with Root Cause Analyses — with six safety guards constraining every action.

**DevOps angle:**
KubeSentinel closes the loop from Prometheus alert to remediation PR: it watches a K8s cluster, investigates failures using live cluster data and historical runbooks, and either auto-fixes or opens a PR with a structured RCA — all with dry-run safety by default.

**Agentic AI angle:**
KubeSentinel demonstrates production-grade agentic AI: an eight-node LangGraph state machine with conditional routing, RAG memory, real tool integrations (K8s, GitHub, Slack), and non-bypassable safety guards — built over five iterative phases with 133 tests and CI.
