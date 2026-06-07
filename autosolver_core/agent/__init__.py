"""AutoSolver Agent package."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import time

from agent.evaluator import evaluate_output
from agent.feature_extractor import extract_features
from agent.meta_controller import AutoSolverAgent


def run_agent(query_or_path: str) -> dict[str, Any]:
    """Run the autonomous solver from a file path or raw input text.

    This replaces the broken legacy LangGraph entry point while retaining a
    simple CLI-friendly API.
    """
    path = Path(query_or_path.strip().strip('"'))
    if path.exists() and path.is_file():
        input_text = path.read_text(encoding="utf-8")
        data_file = str(path)
    else:
        input_text = query_or_path
        data_file = None

    agent = AutoSolverAgent()
    start = time.perf_counter()
    solution = agent.solve(input_text)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    result = evaluate_output(input_text, solution)
    features = extract_features(input_text).to_dict()
    return {
        "data_file": data_file,
        "features": features,
        "solution": solution,
        "elapsed_ms": round(elapsed_ms, 3),
        "trace": agent.last_trace,
        **result,
    }


def run_agent_json(query_or_path: str) -> str:
    return json.dumps(run_agent(query_or_path), ensure_ascii=False, indent=2)
