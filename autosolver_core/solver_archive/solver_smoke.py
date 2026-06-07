"""Submission entry point for the trained AutoSolver Agent."""
from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("MEITUAN_ENABLE_MEMORY", "0")
os.environ.setdefault("MEITUAN_AGENT_EXPLORE", "0")
os.environ.setdefault("MEITUAN_AGENT_MAX_ATTEMPTS", "3")
os.environ.setdefault("MEITUAN_AGENT_BUDGET_MS", "9200")

import core_solver
from agent.meta_controller import AutoSolverAgent

CONFIG = core_solver.CONFIG
_AGENT: AutoSolverAgent | None = None


def _get_agent() -> AutoSolverAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = AutoSolverAgent()
    return _AGENT


def solve(input_text: str) -> list[Any]:
    return _get_agent().solve(input_text, core_solver=core_solver)


def solve_core(input_text: str) -> list[Any]:
    return core_solver.solve(input_text)


def last_trace() -> dict[str, Any]:
    return dict(_get_agent().last_trace)
