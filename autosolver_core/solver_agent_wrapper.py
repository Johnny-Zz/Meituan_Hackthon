"""Submission entry point: self-learning AutoSolver Agent.

This wrapper keeps the original deterministic optimizer in ``core_solver.py``
and adds the autonomous Agent layer described in the project report:
feature extraction, experience memory, strategy-value ranking, failure
attribution, and offline-trainable policy selection.

The judge only needs ``solve(input_text)``.
"""
from __future__ import annotations

from typing import Any

import core_solver
from agent.meta_controller import AutoSolverAgent

# Backward-compatible access for old scripts that tune CONFIG directly.
CONFIG = core_solver.CONFIG

_AGENT: AutoSolverAgent | None = None


def _get_agent() -> AutoSolverAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = AutoSolverAgent()
    return _AGENT


def solve(input_text: str) -> list:
    return _get_agent().solve(input_text, core_solver=core_solver)


def solve_core(input_text: str) -> list[Any]:
    """Bypass the Agent and call the original optimizer."""
    return core_solver.solve(input_text)


def last_trace() -> dict[str, Any]:
    """Return the latest Agent decision trace for debugging/local training."""
    return dict(_get_agent().last_trace)
