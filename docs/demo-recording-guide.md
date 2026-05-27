# Demo Recording Guide

Step-by-step guide for recording a 90–120 second KubeSentinel demo video. The final artifact (`docs/assets/demo.gif`) is referenced from README.md.

---

## Pre-Flight Checklist

- [ ] Docker Desktop running (WSL2 backend)
- [ ] Kind cluster up: `.\make.ps1 up` (verify with `.\make.ps1 status`)
- [ ] `.env` populated with all credentials (OpenRouter, GitHub, Slack)
- [ ] `.\make.ps1 verify-tools` shows all three services `OK`
- [ ] `DRY_RUN=false` in `.env` (for real PRs and Slack messages)
- [ ] `AGENT_USE_REAL_TOOLS=true` in `.env`
- [ ] Slack `#incidents` channel open in a browser tab
- [ ] GitHub demo repo PR page open: `https://github.com/<owner>/<repo>/pulls`
- [ ] Prometheus alerts page open: `http://localhost:9090/alerts`
- [ ] Recording software ready (OBS Studio recommended)

---

## Screen Layout

```
+-------------------------------+-------------------------------+
|  Terminal (PowerShell)        |  Browser (split vertically)   |
|                               |  Top: Prometheus /alerts      |
|  $ py -3.12 -m agent.cli     |  Bottom: Slack #incidents     |
|    live --scenario OOMKilled  |  OR: GitHub PRs page          |
|                               |                               |
+-------------------------------+-------------------------------+
```

Use a screen resolution of 1920x1080. Font size 14+ in terminal for readability. Dark theme recommended for both terminal and browser.

---

## Recording Script

### Scene 1: Context (0:00–0:10)

Open the terminal and briefly show the cluster is running:

```powershell
kubectl get pods -n kubesentinel
```

**Text overlay:** "KubeSentinel — Autonomous AI SRE"

### Scene 2: Trigger the Agent (0:10–0:20)

Run the agent against a live scenario:

```powershell
py -3.12 -m agent.cli live --scenario OOMKilled
```

**Text overlay:** "Alert fires — agent investigates autonomously"

### Scene 3: Agent Reasoning (0:20–0:50)

Let the terminal scroll as the agent runs through its nodes. The structlog output shows:
- `receive_alert` — alert parsed
- `investigate` — pod logs and events fetched
- `search_history` — RAG retrieval (top-3 runbooks)
- `reason` — LLM diagnosis with structured output
- `remediate` or `escalate` — action taken

**Text overlay:** "Investigating with LangGraph + RAG + LLM"

### Scene 4: Slack Notification (0:50–1:00)

Switch to the Slack `#incidents` tab. Show the Block Kit message with the RCA summary and approval request (if `kubectl_patch`).

If approval gate is active, react with a checkmark emoji.

**Text overlay:** "Slack notification with approval gate"

### Scene 5: GitHub PR (1:00–1:15)

Switch to the GitHub PRs page. Click into the newly created PR. Show:
- PR title and description with RCA
- The committed fix file(s) under `fixes/`

**Text overlay:** "PR opened with Root Cause Analysis"

### Scene 6: Final RCA (1:15–1:30)

Switch back to the terminal. Scroll to the final RCA report output.

**Text overlay:** "Full RCA report generated"

### Outro (1:30–1:40)

Fade or cut. Optional text slide:
- "KubeSentinel — github.com/vivekanjana76/kubesentinel"
- "Built with LangGraph, Prometheus, pgvector"

---

## Post-Recording

### Editing

Use **Clipchamp** (built into Windows 11) or any editor that supports:
- Trimming dead time (cluster startup, long waits)
- Adding text overlays at the timestamps above
- Exporting as MP4 (1080p) and/or GIF

### Converting to GIF

For a README-embedded GIF (recommended max 15 seconds, looping highlights):

```powershell
# Using ffmpeg (install via winget: winget install ffmpeg)
ffmpeg -i demo.mp4 -vf "fps=10,scale=800:-1" -t 15 docs/assets/demo.gif
```

Or use [ezgif.com](https://ezgif.com/video-to-gif) for a browser-based conversion.

### Final Placement

1. Save the full video as `docs/assets/demo.mp4` (local only, gitignored)
2. Save the GIF as `docs/assets/demo.gif` (local only, gitignored)
3. To make the GIF visible in the README, upload it to a GitHub issue comment or use GitHub LFS, then update the image URL in README.md

---

## Cleanup After Recording

```powershell
.\make.ps1 demo-reset
.\make.ps1 down          # optional — tears down the Kind cluster
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| Agent escalates instead of remediating | Free-tier LLM rate-limited — wait 60s and retry, or switch model in `.env` |
| No Slack message appears | Check `SLACK_BOT_TOKEN`, verify bot is in `#incidents`: `/invite @KubeSentinel` |
| No PR created | Verify `DRY_RUN=false` and `GITHUB_TOKEN` has `repo` scope |
| OBS recording is black | Disable hardware acceleration in OBS settings |
| GIF too large (>10 MB) | Reduce fps to 8, scale to 640px width, trim to 10s |
