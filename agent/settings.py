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
    # Free-tier reasoning model. Llama 3.3 70B has reliable tool-calling on
    # OpenRouter's free tier. Alternatives that also work:
    #   - "qwen/qwen-2.5-72b-instruct:free"
    #   - "deepseek/deepseek-chat-v3.1"  (may degrade structured output)
    #   - "google/gemini-2.0-flash-exp:free"
    openrouter_reasoning_model: str = "meta-llama/llama-3.3-70b-instruct:free"

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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = AgentSettings()  # type: ignore[call-arg]
