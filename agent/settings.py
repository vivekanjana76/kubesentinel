"""
Consolidated settings for KubeSentinel. Extends the Phase 2 RAG settings with
LLM provider config (OpenRouter, Gemini) and agent runtime parameters.

Read once at import time; all settings come from `.env` (gitignored).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    # ── Supabase / RAG (Phase 2) ──
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    # Direct Postgres URI — used by migrate.py for DDL.
    database_url: str = ""

    # ── LLM providers (Phase 3) ──
    openrouter_api_key: str = ""
    # OpenRouter is OpenAI-API-compatible; reached via langchain-openai.
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Free-tier reasoning model. Pinned for determinism + reliability. Llama
    # 3.3 70B has the most consistent tool-calling on OpenRouter's free tier.
    # When rate-limited (HTTP 429), the agent's loop+escalate flow handles it
    # by design — see prepare_retry node and route_after_reason.
    # Rotation alternates (confirmed available on the free tier):
    #   - "qwen/qwen3-next-80b-a3b-instruct:free"
    #   - "z-ai/glm-4.5-air:free"
    #   - "openai/gpt-oss-120b:free"
    openrouter_reasoning_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    # Per-request LLM timeout in seconds. Prevents indefinite hangs when an
    # upstream provider accepts the connection but never returns a response.
    # On timeout, the reason node captures the error and the existing
    # iteration loop retries up to max_iterations.
    openrouter_request_timeout: int = 90

    google_api_key: str = ""
    # Defined for Phase 4 long-context log analysis. Override via .env.
    google_gemini_model: str = "gemini-2.0-flash-exp"

    # ── Agent runtime ──
    # Master switch: when False (Phase 3 default), the webhook just logs.
    # Phase 4 flips this to True to wire incoming alerts straight into the graph.
    agent_autotrigger: bool = False
    # Max re-investigation iterations before the graph gives up and escalates.
    max_iterations: int = 3
    # Confidence thresholds for `route_after_reason`.
    confidence_high: float = 0.7
    confidence_low: float = 0.4

    # ── Phase 4: External auth ──
    # K8s: path to kubeconfig. None → default (~/.kube/config / KUBECONFIG env).
    # Production deployments use load_incluster_config() with a ServiceAccount instead.
    kubeconfig_path: str | None = None
    # GitHub PAT — separate from GITHUB_MCP_TOKEN used by Claude Code.
    # Required scopes: repo (full), workflow.
    github_token: str = ""
    # Demo-app repo where agent-created PRs land. NEVER the kubesentinel repo itself.
    github_agent_repo_owner: str = ""
    github_agent_repo_name: str = ""
    # Slack bot token (xoxb-…). Required scopes: chat:write, chat:write.public,
    # channels:read, channels:history, reactions:read, files:write.
    slack_bot_token: str = ""
    slack_incidents_channel: str = "#incidents"

    # ── Phase 4: Behavior ──
    # DRY_RUN=true (default) — every destructive action is a no-op that logs
    # "would have done X". Safe to run anywhere. Set false only for live demos.
    dry_run: bool = True
    # When true, kubectl_patch remediations wait for Slack emoji-reaction approval
    # before executing. Ignored in dry_run mode.
    require_slack_approval_for_patches: bool = True
    # How long to poll for Slack approval before timing out (seconds).
    slack_approval_timeout_seconds: int = 300
    # When true, RealToolkit is used instead of MockToolkit.
    # Requires github_token, slack_bot_token, and a reachable kubeconfig.
    agent_use_real_tools: bool = False

    # ── Phase 4: Safety ──
    # Hard namespace allowlist — apply_remediation rejects any namespace not listed.
    allowed_namespaces: list[str] = ["kubesentinel", "kubesentinel-demo"]
    # Agent-created PRs always target this branch, never main.
    pr_target_branch: str = "develop"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = AgentSettings()  # type: ignore[call-arg]
