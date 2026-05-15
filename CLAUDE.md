# KubeSentinel — Project Instructions for Claude Code

## Project Mission
KubeSentinel is an autonomous AI-powered SRE platform. It detects Kubernetes failures via Prometheus + Alertmanager, investigates them using a LangGraph agent with tool access (Kubernetes API, GitHub, Slack), retrieves historical context from a pgvector RAG store, and either auto-remediates or opens a Pull Request with a proposed fix and Root Cause Analysis.

This is a portfolio project intended to demonstrate production-grade agentic AI + DevOps skills to recruiters. Quality, clarity, and clean commit history matter as much as functionality.

## Tech Stack (Locked — do not substitute)
- **Language:** Python 3.12 (NEVER 3.13 or 3.14 — ecosystem support is incomplete)
- **Package manager:** uv (preferred) or pip with venv
- **Agent framework:** LangGraph + LangChain (Python)
- **LLM provider:** OpenRouter (primary), Google AI Studio Gemini (long-context log analysis only)
- **Backend:** FastAPI + uvicorn
- **Cluster:** Kind (Kubernetes in Docker)
- **Monitoring:** Prometheus + Alertmanager (installed via Helm)
- **Vector DB:** Supabase with pgvector extension
- **K8s client:** `kubernetes` (official Python SDK)
- **GitHub client:** PyGithub
- **Slack client:** `slack_sdk`
- **Lint/format:** ruff
- **Tests:** pytest

## Folder Structure
/
├── .claude/              # Claude Code config and subagents
├── agent/                # LangGraph agent code (nodes, state, graph)
├── app/sacrificial/      # The intentionally-broken FastAPI app we monitor
├── infra/k8s/            # Kubernetes manifests
├── infra/helm/           # Helm values files for Prometheus stack
├── docs/                 # Architecture diagrams, design docs
├── docs/runbooks/        # Markdown runbooks (seed data for RAG)
├── tests/                # pytest suite
├── CLAUDE.md             # This file
├── README.md             # Public-facing project README
└── pyproject.toml        # Python project config

## Hard Rules (Non-Negotiable)

1. **NEVER push directly to `main`.** Always create a feature branch, commit, push, and open a PR into `develop`.
2. **NEVER auto-merge your own PRs.** The user reviews and merges.
3. **NEVER commit secrets, API keys, or `.env` files.** Anything sensitive goes in `.env` (gitignored). Use `.env.example` with placeholders only.
4. **NEVER use `python` — always use `py -3.12`** on Windows to invoke the correct interpreter.
5. **NEVER install global packages.** All Python work happens inside `.venv` in the project root.
6. **NEVER fabricate API calls or imports.** If unsure about a library's current API, use the context7 MCP to fetch live docs.

## Branching & Commit Conventions

- **Branches:** `feat/phase-N-short-description`, `fix/short-description`, `docs/short-description`, `chore/short-description`
- **Commits:** Conventional Commits format — `feat:`, `fix:`, `docs:`, `chore:`, `test:`, `refactor:`
- Example: `feat(infra): add Kind cluster bootstrap script`
- Keep commits atomic. One logical change per commit.

## Workflow

For each phase task:
1. Read this CLAUDE.md and any relevant files first.
2. Create or check out the appropriate feature branch.
3. Implement the change.
4. Run lint (`ruff check .`) and tests (`pytest`) before committing.
5. Commit with a Conventional Commit message.
6. Push the branch.
7. Open a PR into `develop` using the GitHub MCP with a clear description: what changed, why, how to test.
8. STOP. Wait for the user to review.

## When Unsure
- For library APIs: use the context7 MCP first.
- For architectural decisions: ask the user before implementing.
- For anything destructive (deleting files, force-pushing): ask first.

## Tone in PR Descriptions and Docs
Professional, concise, technical. No emojis in code or commits. README and docs can use sparing emojis for visual hierarchy.