# Demo Flow — KubeSentinel Live Demo Guide

This document is the step-by-step guide for recording a live KubeSentinel demo.
It covers first-time setup, Slack app configuration (including required scopes),
the recording script, and cleanup.

---

## Prerequisites

### Tools

| Tool | Version | Check |
|---|---|---|
| Docker Desktop | Any with WSL2 | `docker info` |
| Kind | v0.31+ | `kind version` |
| kubectl | Any | `kubectl version --client` |
| Helm | v3 | `helm version` |
| Python | 3.12 | `py -3.12 --version` |
| uv | latest | `uv --version` |
| gh (GitHub CLI) | v2+ | `gh --version` |

### Credentials

All credentials go in `.env` (see `.env.example`). Never commit `.env`.

```ini
# Required for real-tools mode
OPENROUTER_API_KEY=sk-or-v1-...
GITHUB_TOKEN=ghp_...
GITHUB_AGENT_REPO_OWNER=your-github-username
GITHUB_AGENT_REPO_NAME=kubesentinel-demo-app
SLACK_BOT_TOKEN=xoxb-...
SLACK_INCIDENTS_CHANNEL=#incidents

# Safety / mode switches
DRY_RUN=true           # set to false when recording real PR creation
AGENT_USE_REAL_TOOLS=true
AGENT_AUTOTRIGGER=true
```

---

## Slack App Setup

### 1. Create the app

1. Go to https://api.slack.com/apps and click **Create New App → From scratch**.
2. Name it `KubeSentinel`; select your workspace.

### 2. Required OAuth scopes

Under **OAuth & Permissions → Scopes → Bot Token Scopes**, add all of these:

| Scope | Purpose |
|---|---|
| `chat:write` | Post messages to channels the bot is a member of |
| `chat:write.public` | Post to channels without being a member (approval requests) |
| `channels:read` | List channels (used by `verify-tools` to confirm channel access) |
| `channels:history` | Read message history (not used today, useful for future polling) |
| `reactions:read` | Read emoji reactions on messages (core approval-gate mechanism) |
| `files:write` | Attach RCA files to messages (reserved for future attachment support) |

> **Important:** Adding scopes to an already-installed app requires reinstalling
> the app to the workspace. After adding scopes, click **Install to Workspace**
> again. The token will change — copy the new `xoxb-...` value into `.env`.

### 3. Install and copy the token

After installing, go to **OAuth & Permissions → OAuth Tokens** and copy the
**Bot User OAuth Token** (starts with `xoxb-`). Set `SLACK_BOT_TOKEN` in `.env`.

### 4. Invite the bot to your incidents channel

In Slack, open `#incidents` (or whatever channel you configured) and type:

```
/invite @KubeSentinel
```

### 5. Verify all scopes are working

```powershell
.\make.ps1 verify-tools
# or:
py -3.12 -m agent.cli verify-tools
```

Expected output:

```
[ K8s ]  Checking Kubernetes API...
  OK  — connected (sample namespaces: ['default', 'kubesentinel', ...])

[ GitHub ] Checking GitHub API...
  OK  — authenticated as your-username
  OK  — demo repo accessible: your-username/kubesentinel-demo-app

[ Slack ] Checking Slack API...
  OK  — authenticated (bot=BXXXXXXXX, team=Your Workspace)
  OK  — channel check passed for #incidents

==============================
  All services reachable. Ready for live demo.
==============================
```

If you see `FAIL — Missing Slack scope: reactions:read`, add the scope and
reinstall the app. The scope list above is the minimum required set.

---

## Demo Script (Screen Recording)

### Option A: Automated (`live-demo` target)

The `live-demo` make target runs the full sequence with stage markers:

```powershell
.\make.ps1 live-demo
```

Stage markers printed:

1. `[ Stage 1 ] Cluster up`
2. `[ Stage 2 ] Webhook running`
3. `[ Stage 3 ] Verifying credentials`
4. `[ Stage 4 ] Triggering failure`
5. `[ Stage 5 ] Alert fired`
6. `[ Stage 6 ] Agent responded`
7. `[ Stage 7 ] PR opened`

### Option B: Manual (step-by-step)

More control over timing and narration. Recommended for first-time recordings.

#### Step 1 — Bring up the cluster

```powershell
.\make.ps1 up
```

Expected: Kind cluster created, Prometheus/Alertmanager/sacrificial app running.

```powershell
.\make.ps1 status
```

All pods should be `Running`.

#### Step 2 — Start the webhook receiver

```powershell
.\make.ps1 webhook-dev
```

Open a second terminal. The receiver starts on `http://localhost:8000`.

Health check:
```powershell
Invoke-WebRequest http://localhost:8000/health | Select-Object -ExpandProperty Content
# {"status":"ok","agent_autotrigger":true}
```

#### Step 3 — Verify credentials

```powershell
.\make.ps1 verify-tools
```

All three services must show `OK` before proceeding.

#### Step 4 — Open Prometheus and Alertmanager (browser tabs)

- Prometheus alerts: http://localhost:9090/alerts
- Alertmanager: http://localhost:9093

#### Step 5 — Break the sacrificial app

```powershell
.\make.ps1 break-app
```

This hits `/crash` (20×), `/slow` (3×), and `/memory-leak` (15×). Within
1–2 minutes `HighErrorRate`, `HighLatency`, and `HighMemoryUsage` should
appear as `FIRING` in Prometheus.

#### Step 6 — Watch Alertmanager dispatch the webhook

In the webhook receiver terminal, you should see structured log lines like:

```
alert_received  status=firing  receiver=kubesentinel  alert_count=1
agent.background_run.queued  alert=OOMKilled
```

#### Step 7 — Watch the agent run in the background

The webhook returns `200` immediately; the agent runs in a background task.
Watch the log output for the full reasoning + RCA.

To trigger the agent synchronously (easier to record):

```powershell
py -3.12 -m agent.cli live --scenario OOMKilled
```

#### Step 8 — Show the Slack approval request

When `DRY_RUN=false` and a `kubectl_patch` is proposed, the agent posts a
Block Kit message to `#incidents` asking for `✅` (approve) or `❌` (reject).

React to the message within the approval window (default: 5 minutes). The agent
polls `reactions.get` every 5 seconds and proceeds once a reaction is detected.

#### Step 9 — Show the GitHub PR

If a PR was created (`DRY_RUN=false`), find it with:

```powershell
gh pr list --repo <owner>/<repo> --head agent/fix-
```

The PR contains two committed files under `fixes/{timestamp}-{alert-name}/`:
- `rca.md` — full Root Cause Analysis
- `patch.sh` / `proposed.diff` / `config-patch.yaml` — type-specific artifact

#### Step 10 — Wrap up

```powershell
.\make.ps1 demo-reset
```

This closes open agent PRs, deletes `agent/fix-*` branches, posts a Slack
reset notice, and re-applies the sacrificial deployment to its healthy state.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `not_authed` in verify-tools | Wrong token or app not installed | Copy the `xoxb-` token again after reinstalling |
| `missing_scope: reactions:read` | Scope not added or app not reinstalled | Add scope, reinstall app, copy new token |
| No alerts firing after `break-app` | Prometheus scrape interval / rule group interval | Wait up to 2 min; check http://localhost:9090/alerts |
| Agent escalates every run | Free OpenRouter model rate-limited | Rotate to another free model in `.env` or use a paid key |
| `SafetyViolationError: namespace not allowed` | LLM proposed wrong namespace | Check `ALLOWED_NAMESPACES` in `.env` |
| PR not created | `DRY_RUN=true` (default) | Set `DRY_RUN=false` in `.env` for real PRs |
| Webhook health check fails in `live-demo` | Port 8000 already in use | `netstat -ano | findstr :8000`; kill the process |

---

## Cleanup

```powershell
.\make.ps1 demo-reset   # Close PRs, delete branches, post Slack notice, restore deployment
.\make.ps1 down         # Delete the Kind cluster
```

Both operations are idempotent — safe to run multiple times.
