"""Tool integrations for the LangGraph agent.

Phase 3 ships a mock toolkit only — see `mocks.MockToolkit`. Phase 4 adds
`real.RealToolkit` that talks to the live cluster, GitHub, and Slack. Both
implement the `Toolkit` ABC in `base.py` so the graph code is provider-agnostic.
"""
