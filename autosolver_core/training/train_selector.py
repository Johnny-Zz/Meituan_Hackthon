"""Train/export the strategy selector model from the experience memory.

The default model is a robust contextual-bandit policy table stored as JSON.
If scikit-learn is installed and enough data exists, this script also stores a
small RandomForest classifier with joblib, but the online solver does not depend
on sklearn.

Usage:
    python training/train_selector.py --memory memory/experiments.sqlite --out models/strategy_selector.json
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

DEFAULT_MEMORY = str(Path(__file__).resolve().parents[2] / "memory" / "training" / "experiments.sqlite")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def build_policy_table(memory_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(memory_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT scenario_type, strategy_name,
               COUNT(*) AS n,
               AVG(reward) AS avg_reward,
               MAX(reward) AS best_reward,
               AVG(runtime_ms) AS avg_runtime_ms,
               SUM(CASE WHEN accepted_orders=total_tasks AND total_tasks>0 THEN 1 ELSE 0 END) AS success_count
        FROM experiments
        GROUP BY scenario_type, strategy_name
        """
    ).fetchall()
    policy: dict[str, Any] = {"type": "contextual_bandit_table", "scenarios": {}}
    for row in rows:
        scenario = row["scenario_type"]
        n = int(row["n"])
        policy["scenarios"].setdefault(scenario, {})[row["strategy_name"]] = {
            "trial_count": n,
            "avg_reward": float(row["avg_reward"] or 0.0),
            "best_reward": float(row["best_reward"] or 0.0),
            "avg_runtime_ms": float(row["avg_runtime_ms"] or 0.0),
            "success_count": int(row["success_count"] or 0),
            "success_rate": (int(row["success_count"] or 0) / max(1, n)),
        }
    conn.close()
    return policy


def maybe_train_sklearn(memory_path: Path, out_dir: Path) -> None:
    try:
        from importlib.metadata import version

        numpy_version = tuple(int(part) for part in version("numpy").split(".")[:2])
        if numpy_version >= (2, 3):
            return
        import joblib  # type: ignore
        from sklearn.ensemble import RandomForestClassifier  # type: ignore
    except Exception:
        return

    conn = sqlite3.connect(memory_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM experiments").fetchall()
    conn.close()
    by_instance: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_instance[row["feature_hash"]].append(row)

    X = []
    y = []
    feature_names: list[str] | None = None
    for _fid, items in by_instance.items():
        best = max(items, key=lambda r: float(r["reward"] or -1e18))
        features = json.loads(best["feature_json"])
        numeric = {k: v for k, v in features.items() if isinstance(v, (int, float))}
        if feature_names is None:
            feature_names = sorted(numeric)
        X.append([float(numeric.get(name, 0.0)) for name in feature_names])
        y.append(best["strategy_name"])

    if len(set(y)) < 2 or len(y) < 8 or feature_names is None:
        return
    model = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)
    model.fit(X, y)
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "feature_names": feature_names}, out_dir / "strategy_selector_rf.joblib")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory", default=DEFAULT_MEMORY)
    parser.add_argument("--out", default="models/strategy_selector.json")
    args = parser.parse_args()

    memory_path = Path(args.memory)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    policy = build_policy_table(memory_path)
    out_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    maybe_train_sklearn(memory_path, out_path.parent)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
