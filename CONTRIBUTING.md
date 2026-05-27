# Contributing to KubeSentinel

## Prerequisites

- Python 3.12 (`py -3.12 --version`)
- Docker Desktop with WSL2 backend
- Kind, kubectl, Helm v3

## Setup

```powershell
git clone https://github.com/vivekanjana76/kubesentinel.git
cd kubesentinel
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e ".[dev]"
pre-commit install
```

## Running Tests

```powershell
py -3.12 -m pytest tests/ -v
```

All external services (Kubernetes, GitHub, Slack, Supabase, OpenRouter) are fully mocked in tests. No credentials or cluster needed.

## Linting

```powershell
py -3.12 -m ruff check .
```

Pre-commit hooks run ruff automatically on staged files.

## Branch Naming

- `feat/phase-N-short-description` or `feat/short-description`
- `fix/short-description`
- `docs/short-description`
- `chore/short-description`

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(agent): add new investigation tool
fix(webhook): handle empty alert payload
docs: update architecture diagram
chore: bump ruff to 0.5.0
test(rag): add chunker edge case coverage
```

Keep commits atomic: one logical change per commit.

## Pull Requests

- Always target `develop`, never `main`
- Include a clear description: what changed, why, how to test
- All tests must pass and `ruff check .` must be clean before requesting review
