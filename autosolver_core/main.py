"""CLI for the self-learning Meituan AutoSolver Agent.

Usage:
    python main.py path/to/case.txt
    python main.py < path/to/case.txt
"""
from __future__ import annotations

import argparse
import json
import sys

from agent import run_agent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", help="Case file path. If omitted, stdin is used.")
    parser.add_argument("--json", action="store_true", help="Print full JSON trace.")
    args = parser.parse_args()

    if args.path:
        query = args.path
    else:
        query = sys.stdin.read()

    result = run_agent(query)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"data_file: {result.get('data_file')}")
    print(f"scenario_type: {result.get('features', {}).get('scenario_type')}")
    print(f"valid: {result.get('valid')}")
    print(f"covered_tasks: {result.get('covered_tasks')}/{result.get('total_tasks')}")
    print(f"penalty_score: {result.get('penalty_score')}")
    print(f"parallel_penalty_score: {result.get('parallel_penalty_score')}")
    print(f"total_score: {result.get('total_score')}")
    print(f"expected_accepted_tasks: {result.get('expected_accepted_tasks')}")
    print(f"elapsed_ms: {result.get('elapsed_ms')}")
    trace = result.get("trace", {})
    print(f"ranked_strategies: {trace.get('ranked_strategies')}")
    print(f"failure_suggestions: {trace.get('failure_suggestions')}")
    print(f"solution: {result.get('solution')}")


if __name__ == "__main__":
    main()
