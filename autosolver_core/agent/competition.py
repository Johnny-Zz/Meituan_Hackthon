"""Compatibility layer for the legacy competition Agent entry point.

The previous version imported stale solver symbols.  This file now delegates to
the new autonomous MetaController-based Agent while keeping the old import path
``from agent.competition import run_agent`` usable.
"""
from __future__ import annotations

from typing import Any

from agent import run_agent as _run_agent


def run_agent(query_or_path: str) -> dict[str, Any]:
    return _run_agent(query_or_path)


agent_graph = None
