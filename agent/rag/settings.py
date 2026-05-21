"""
Phase 2 entry point for settings. Consolidated into `agent.settings` in
Phase 3 — this module re-exports the unified `settings` instance plus a
typed alias so existing imports (`from agent.rag.settings import settings`)
continue to work.
"""

from agent.settings import AgentSettings as RagSettings
from agent.settings import settings

__all__ = ["RagSettings", "settings"]
