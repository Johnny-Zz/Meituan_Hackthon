"""Persistent experience memory for the AutoSolver Agent.

The memory is intentionally lightweight (SQLite) so it can be used during local
training, and safely disabled/fallback during judge execution if the filesystem
is read-only.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path(
    os.environ.get(
        "MEITUAN_AGENT_MEMORY",
        str(Path(__file__).resolve().parents[2] / "memory" / "training" / "experiments.sqlite"),
    )
)


class AgentMemory:
    def __init__(self, path: str | Path | None = None, readonly: bool = False):
        self.path = Path(path) if path is not None else DEFAULT_DB_PATH
        self.readonly = readonly
        self.available = False
        self.conn: sqlite3.Connection | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            uri = f"file:{self.path}?mode=ro" if readonly else str(self.path)
            self.conn = sqlite3.connect(uri, uri=readonly)
            self.conn.row_factory = sqlite3.Row
            if not readonly:
                self._init_schema()
            self.available = True
        except Exception:
            self.conn = None
            self.available = False

    def _init_schema(self) -> None:
        assert self.conn is not None
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT,
                feature_hash TEXT,
                scenario_type TEXT,
                feature_json TEXT,
                strategy_name TEXT,
                strategy_params TEXT,
                accepted_orders INTEGER,
                total_tasks INTEGER,
                total_score REAL,
                penalty_score REAL,
                parallel_penalty_score REAL,
                expected_accepted_tasks REAL,
                runtime_ms REAL,
                reward REAL,
                is_best INTEGER DEFAULT 0,
                failure_tags TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scenario_memory (
                scenario_type TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                trial_count INTEGER NOT NULL DEFAULT 0,
                avg_reward REAL NOT NULL DEFAULT 0,
                best_reward REAL NOT NULL DEFAULT -1000000000000,
                avg_runtime_ms REAL NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (scenario_type, strategy_name)
            );

            CREATE TABLE IF NOT EXISTS strategy_param_memory (
                strategy_name TEXT NOT NULL,
                param_json TEXT NOT NULL,
                scenario_type TEXT NOT NULL,
                avg_reward REAL NOT NULL DEFAULT 0,
                trial_count INTEGER NOT NULL DEFAULT 0,
                best_instance_id TEXT,
                PRIMARY KEY (strategy_name, param_json, scenario_type)
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def log_experiment(
        self,
        *,
        instance_id: str,
        features: dict[str, Any],
        strategy_name: str,
        strategy_params: dict[str, Any],
        result: dict[str, Any],
        reward: float,
        failure_tags: list[str],
        is_best: bool = False,
    ) -> None:
        if not self.available or self.readonly or self.conn is None:
            return
        try:
            scenario_type = str(features.get("scenario_type", "unknown"))
            self.conn.execute(
                """
                INSERT INTO experiments(
                    instance_id, feature_hash, scenario_type, feature_json,
                    strategy_name, strategy_params, accepted_orders, total_tasks,
                    total_score, penalty_score, parallel_penalty_score,
                    expected_accepted_tasks, runtime_ms, reward, is_best, failure_tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    str(features.get("instance_hash", instance_id)),
                    scenario_type,
                    json.dumps(features, ensure_ascii=False, sort_keys=True),
                    strategy_name,
                    json.dumps(strategy_params, ensure_ascii=False, sort_keys=True),
                    int(result.get("covered_tasks", 0)),
                    int(result.get("total_tasks", 0)),
                    float(result.get("total_score", math.inf)),
                    float(result.get("penalty_score", math.inf)),
                    float(result.get("parallel_penalty_score", result.get("penalty_score", math.inf))),
                    float(result.get("expected_accepted_tasks", 0.0)),
                    float(result.get("runtime_ms", 0.0)),
                    float(reward),
                    1 if is_best else 0,
                    json.dumps(failure_tags, ensure_ascii=False),
                ),
            )
            self._update_scenario_memory(scenario_type, strategy_name, reward, result)
            self._update_param_memory(scenario_type, strategy_name, strategy_params, instance_id, reward)
            self.conn.commit()
        except Exception:
            # Memory should never break online solving.
            try:
                self.conn.rollback()
            except Exception:
                pass

    def _update_scenario_memory(self, scenario: str, strategy: str, reward: float, result: dict[str, Any]) -> None:
        assert self.conn is not None
        row = self.conn.execute(
            "SELECT * FROM scenario_memory WHERE scenario_type=? AND strategy_name=?",
            (scenario, strategy),
        ).fetchone()
        success = 1 if result.get("valid") and result.get("missing_tasks", 1) == 0 else 0
        runtime = float(result.get("runtime_ms", 0.0))
        if row is None:
            self.conn.execute(
                """
                INSERT INTO scenario_memory(
                    scenario_type, strategy_name, trial_count, avg_reward, best_reward,
                    avg_runtime_ms, success_count, fail_count
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (scenario, strategy, reward, reward, runtime, success, 0 if success else 1),
            )
        else:
            n = int(row["trial_count"])
            new_n = n + 1
            avg_reward = (float(row["avg_reward"]) * n + reward) / new_n
            avg_runtime = (float(row["avg_runtime_ms"]) * n + runtime) / new_n
            self.conn.execute(
                """
                UPDATE scenario_memory
                SET trial_count=?, avg_reward=?, best_reward=?, avg_runtime_ms=?,
                    success_count=?, fail_count=?, last_updated=CURRENT_TIMESTAMP
                WHERE scenario_type=? AND strategy_name=?
                """,
                (
                    new_n,
                    avg_reward,
                    max(float(row["best_reward"]), reward),
                    avg_runtime,
                    int(row["success_count"]) + success,
                    int(row["fail_count"]) + (0 if success else 1),
                    scenario,
                    strategy,
                ),
            )

    def _update_param_memory(self, scenario: str, strategy: str, params: dict[str, Any], instance_id: str, reward: float) -> None:
        assert self.conn is not None
        param_json = json.dumps(params, ensure_ascii=False, sort_keys=True)
        row = self.conn.execute(
            """
            SELECT * FROM strategy_param_memory
            WHERE scenario_type=? AND strategy_name=? AND param_json=?
            """,
            (scenario, strategy, param_json),
        ).fetchone()
        if row is None:
            self.conn.execute(
                """
                INSERT INTO strategy_param_memory(strategy_name, param_json, scenario_type, avg_reward, trial_count, best_instance_id)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (strategy, param_json, scenario, reward, instance_id),
            )
        else:
            n = int(row["trial_count"])
            avg_reward = (float(row["avg_reward"]) * n + reward) / (n + 1)
            best_instance = instance_id if reward >= float(row["avg_reward"]) else row["best_instance_id"]
            self.conn.execute(
                """
                UPDATE strategy_param_memory
                SET avg_reward=?, trial_count=?, best_instance_id=?
                WHERE scenario_type=? AND strategy_name=? AND param_json=?
                """,
                (avg_reward, n + 1, best_instance, scenario, strategy, param_json),
            )

    def get_strategy_stats(self, scenario: str, strategy_name: str) -> dict[str, Any] | None:
        if not self.available or self.conn is None:
            return None
        try:
            row = self.conn.execute(
                "SELECT * FROM scenario_memory WHERE scenario_type=? AND strategy_name=?",
                (scenario, strategy_name),
            ).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def export_policy_table(self) -> dict[str, Any]:
        if not self.available or self.conn is None:
            return {"scenarios": {}}
        data: dict[str, Any] = {"scenarios": {}}
        try:
            rows = self.conn.execute("SELECT * FROM scenario_memory").fetchall()
            for row in rows:
                scenario = row["scenario_type"]
                data["scenarios"].setdefault(scenario, {})[row["strategy_name"]] = {
                    "trial_count": row["trial_count"],
                    "avg_reward": row["avg_reward"],
                    "best_reward": row["best_reward"],
                    "avg_runtime_ms": row["avg_runtime_ms"],
                    "success_count": row["success_count"],
                    "fail_count": row["fail_count"],
                }
        except Exception:
            pass
        return data
