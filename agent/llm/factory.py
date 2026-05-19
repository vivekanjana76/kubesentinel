"""
LLM factory.

Two model roles exist:

- `get_reasoning_llm()` — OpenRouter (OpenAI-API-compatible), default model
  `meta-llama/llama-3.3-70b-instruct:free`. Used by the `reason` node.
- `get_log_analysis_llm()` — Google Gemini, default `gemini-2.0-flash-exp`.
  Defined for Phase 4 long-context log analysis; not invoked in Phase 3.

`get_structured_llm(schema)` wraps `with_structured_output` and falls back from
function-calling to `json_mode` automatically when a model doesn't support tool
calling. Reason: free-tier OpenRouter models have inconsistent function-calling
support; we want the reasoning node to "just work" across model swaps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from langchain.chat_models import init_chat_model

from agent.settings import settings

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable
    from pydantic import BaseModel

log = structlog.get_logger()


def get_reasoning_llm() -> "BaseChatModel":
    """Return a chat model configured against OpenRouter free-tier.

    OpenRouter is OpenAI-API-compatible, so we route through `langchain-openai`
    by setting `model_provider="openai"` and overriding the base URL.
    """
    if not settings.openrouter_api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Add it to .env before running the "
            "agent (see .env.example)."
        )
    return init_chat_model(
        model=settings.openrouter_reasoning_model,
        model_provider="openai",
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key,
        temperature=0.2,
    )


def get_log_analysis_llm() -> "BaseChatModel":
    """Return a Gemini chat model for long-context log analysis.

    Defined for Phase 4 — not called in Phase 3.
    """
    if not settings.google_api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is not set. Add it to .env before using log analysis."
        )
    return init_chat_model(
        model=settings.google_gemini_model,
        model_provider="google_genai",
        api_key=settings.google_api_key,
        temperature=0.0,
    )


def get_structured_llm(
    llm: "BaseChatModel",
    schema: "type[BaseModel]",
) -> "Runnable":
    """Return a runnable that produces validated `schema` instances.

    Strategy:
    1. Try `llm.with_structured_output(schema)` — uses function calling.
    2. If the model rejects function calling, retry with `method="json_mode"`.
    3. If both fail, surface the error — Pydantic validation on the JSON output
       will catch malformed responses regardless.

    The fallback is detected by:
    - Catching `NotImplementedError` (some integrations raise this directly).
    - String-matching the exception against known "function calling not
      supported" phrases.

    A structured warning is emitted when the fallback fires so model behavior
    is visible in logs and LangSmith traces.
    """
    try:
        return llm.with_structured_output(schema)
    except NotImplementedError:
        log.warning(
            "llm.structured_output.fallback",
            reason="NotImplementedError on function-calling path",
            method="json_mode",
        )
        return llm.with_structured_output(schema, method="json_mode")
    except Exception as exc:
        msg = str(exc).lower()
        function_calling_unsupported = any(
            phrase in msg
            for phrase in (
                "function calling",
                "tool calling",
                "tools not supported",
                "does not support tools",
            )
        )
        if not function_calling_unsupported:
            raise
        log.warning(
            "llm.structured_output.fallback",
            reason=str(exc),
            method="json_mode",
        )
        return llm.with_structured_output(schema, method="json_mode")
