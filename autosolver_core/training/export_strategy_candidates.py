"""Convert best_config_candidates.json into Strategy snippets.

Use after ``tune_params.py``.  The printed snippets can be pasted into
``agent/strategy_registry.py`` when a mutated configuration consistently wins on
holdout cases.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", default="models/best_config_candidates.json")
    parser.add_argument("--prefix", default="distilled")
    args = parser.parse_args()
    payload = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    for idx, item in enumerate(payload.get("candidates", []), 1):
        name = f"{args.prefix}_v{idx}@{int(item.get('budget_ms', 0))}ms"
        config = item.get("config_overrides", {})
        print("Strategy(")
        print(f"    name={name!r},")
        print("    family='hybrid',")
        print(f"    description='Distilled config from {Path(args.json_path).name}; avg_reward={item.get('avg_reward'):.3f}.',")
        print(f"    config_overrides={json.dumps(config, ensure_ascii=False, sort_keys=True)},")
        print("    scenario_prior={'normal': 0.15, 'scarce_couriers': 0.35, 'bundle_heavy': 0.25},")
        print(f"    min_budget_ms={float(item.get('budget_ms', 2500.0)) * 0.75:.1f},")
        print(f"    preferred_budget_ms={float(item.get('budget_ms', 2500.0)):.1f},")
        print("),\n")


if __name__ == "__main__":
    main()
