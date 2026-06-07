"""LLM-driven analysis and strategy generation using Mimo-V2.5-pro.

This module replaces the manual human review step in the training pipeline.
Mimo analyzes experiment data, failure patterns, and performance gaps, then
generates new Strategy configurations that are automatically injected.

Key functions:
- analyze_training_results: Summarize experiment data, find weak spots
- generate_new_strategies: Create Strategy configs from analysis
- analyze_failure_patterns: Deep failure attribution beyond rule-based tags
- suggest_case_generation: Recommend next-round training case types
- reflect_on_benchmark: Post-benchmark reflection for next iteration
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agent.llm_client import chat, chat_json

# --- JSON Schemas for structured LLM output ---

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "weak_scenarios": {
            "type": "array",
            "description": "场景类型 where no strategy achieves good coverage",
            "items": {
                "type": "object",
                "properties": {
                    "scenario": {"type": "string"},
                    "best_coverage_rate": {"type": "number"},
                    "best_avg_reward": {"type": "number"},
                    "issue": {"type": "string"},
                },
            },
        },
        "parameter_insights": {
            "type": "array",
            "description": "Parameter combinations that seem suboptimal",
            "items": {"type": "string"},
        },
        "strategy_suggestions": {
            "type": "array",
            "description": "Specific new strategy ideas with reasoning",
            "items": {"type": "string"},
        },
        "case_generation_hints": {
            "type": "array",
            "description": "What types of training cases to generate next",
            "items": {"type": "string"},
        },
    },
}

STRATEGY_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "strategies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "family": {"type": "string", "enum": ["greedy", "hybrid", "teacher", "risk_aware", "exact"]},
                    "description": {"type": "string"},
                    "config_overrides": {"type": "object"},
                    "scenario_prior": {"type": "object"},
                    "preferred_budget_ms": {"type": "number"},
                    "min_budget_ms": {"type": "number"},
                },
                "required": ["name", "family", "description", "config_overrides"],
            },
        },
    },
}

CASE_SPECS_SCHEMA = {
    "type": "object",
    "properties": {
        "case_specs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["low_willingness", "scarce_couriers", "high_noise", "medium", "sparse", "bundle_heavy"]},
                    "num_tasks": {"type": "integer"},
                    "num_couriers": {"type": "integer"},
                    "sample_rate": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["mode", "num_tasks", "num_couriers", "sample_rate"],
            },
        },
    },
}

REFLECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_assessment": {"type": "string"},
        "top_issues": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                },
            },
        },
        "should_continue": {"type": "boolean"},
        "suggested_focus": {"type": "string"},
    },
}


# --- Valid parameter ranges for CONFIG safety ---
_VALID_PARAM_RANGES = {
    "auto_strategy_budget_ms": (50.0, 800.0),
    "local_search_budget_ms": (0.0, 8000.0),
    "max_generated_strategies": (4, 96),
    "max_candidates_per_mask": (10, 100),
    "pair_top_k": (8, 64),
    "triple_top_k": (4, 48),
    "backup_time_budget_ms": (50.0, 1200.0),
    "min_backup_utility": (-50.0, 100.0),
    "max_extra_couriers_per_bundle": (0, 6),
    "ilp_time_limit_seconds": (0.0, 3.0),
}


def _clamp_config(config: dict[str, Any]) -> dict[str, Any]:
    """Clamp generated config parameters to safe ranges."""
    clamped = dict(config)
    for key, (lo, hi) in _VALID_PARAM_RANGES.items():
        if key in clamped:
            try:
                val = float(clamped[key])
                clamped[key] = type(lo)(max(lo, min(hi, val)))
            except (TypeError, ValueError):
                del clamped[key]
    # Safety flags
    clamped["force_single_courier_output"] = True
    clamped["enable_multi_courier_output"] = False
    clamped["acceptance_penalty"] = 100.0
    clamped["dynamic_penalty"] = False
    return clamped


def analyze_training_results(
    memory_path: str | Path = "memory/experiments.sqlite",
    top_n: int = 30,
) -> dict[str, Any]:
    """Analyze experiment database and return structured analysis.

    Reads the SQLite experiment log, aggregates per-scenario per-strategy
    statistics, and sends a summary to Mimo for deep analysis.
    """
    memory_path = Path(memory_path)
    if not memory_path.exists():
        return {"error": "No experiment database found", "weak_scenarios": [], "strategy_suggestions": []}

    conn = sqlite3.connect(str(memory_path))
    conn.row_factory = sqlite3.Row

    # Aggregate stats
    rows = conn.execute("""
        SELECT scenario_type, strategy_name,
               COUNT(*) as n,
               AVG(reward) as avg_reward,
               MAX(reward) as best_reward,
               AVG(runtime_ms) as avg_runtime,
               SUM(CASE WHEN accepted_orders = total_tasks AND total_tasks > 0 THEN 1 ELSE 0 END) as full_coverage_count,
               AVG(CAST(accepted_orders AS FLOAT) / MAX(total_tasks, 1)) as avg_coverage_rate,
               AVG(penalty_score) as avg_penalty
        FROM experiments
        GROUP BY scenario_type, strategy_name
        ORDER BY scenario_type, avg_reward DESC
    """).fetchall()

    # Build summary for LLM
    scenario_data: dict[str, list[dict]] = {}
    for row in rows:
        scenario = row["scenario_type"] or "unknown"
        scenario_data.setdefault(scenario, []).append({
            "strategy": row["strategy_name"],
            "trials": row["n"],
            "avg_reward": round(row["avg_reward"] or 0, 2),
            "best_reward": round(row["best_reward"] or 0, 2),
            "avg_runtime_ms": round(row["avg_runtime"] or 0, 1),
            "full_coverage_rate": round((row["full_coverage_count"] or 0) / max(1, row["n"]), 3),
            "avg_coverage_rate": round(row["avg_coverage_rate"] or 0, 3),
            "avg_penalty": round(row["avg_penalty"] or 0, 2),
        })

    # Also get failure tag distribution
    failure_rows = conn.execute("""
        SELECT scenario_type, strategy_name, failure_tags
        FROM experiments
        WHERE failure_tags IS NOT NULL AND failure_tags != ''
    """).fetchall()

    failure_stats: dict[str, dict[str, int]] = {}
    for row in failure_rows:
        scenario = row["scenario_type"] or "unknown"
        try:
            tags = json.loads(row["failure_tags"])
        except (json.JSONDecodeError, TypeError):
            continue
        failure_stats.setdefault(scenario, {})
        for tag in tags:
            failure_stats[scenario][tag] = failure_stats[scenario].get(tag, 0) + 1

    conn.close()

    # Truncate for LLM context
    for scenario in scenario_data:
        scenario_data[scenario] = scenario_data[scenario][:top_n]

    prompt = f"""你是一个优化算法专家。以下是一个外卖调度系统的策略实验数据汇总，请分析：

## 实验数据 (按场景×策略)
{json.dumps(scenario_data, ensure_ascii=False, indent=2)}

## 失败标签分布
{json.dumps(failure_stats, ensure_ascii=False, indent=2)}

请分析：
1. 哪些场景类型下策略覆盖不足（avg_coverage_rate < 0.95 或 avg_reward < -10000）？
2. 哪些参数组合在哪些场景下表现异常好/差？
3. 有哪些可以尝试的新策略方向？（考虑config参数的组合空间）
4. 下一轮训练应该重点生成什么类型的case来补齐短板？

请给出具体、可操作的建议。"""

    system = "你是一个优化算法分析专家，擅长分析组合优化问题的策略表现数据。请用中文回答，输出JSON格式。"

    try:
        result = chat_json(prompt, schema=ANALYSIS_SCHEMA, system=system, temperature=0.2)
        result["_raw_data"] = {
            "scenario_count": len(scenario_data),
            "total_experiments": sum(len(v) for v in scenario_data.values()),
        }
        return result
    except Exception as exc:
        print(f"[llm_analyzer] analysis failed: {exc}")
        return {
            "error": str(exc),
            "weak_scenarios": [],
            "strategy_suggestions": [],
            "parameter_insights": [],
            "case_generation_hints": [],
        }


def generate_new_strategies(
    analysis: dict[str, Any],
    existing_strategy_names: list[str] | None = None,
    count: int = 5,
) -> list[dict[str, Any]]:
    """Generate new Strategy configurations from analysis report.

    Returns a list of strategy dicts that can be converted to Strategy objects.
    """
    existing = existing_strategy_names or []

    # Extract valid CONFIG keys from core_solver
    import core_solver
    config_keys = sorted(core_solver.CONFIG.keys())

    prompt = f"""你是一个优化算法工程师。根据以下分析报告，为外卖调度系统生成{count}个新的求解策略。

## 分析报告
{json.dumps(analysis, ensure_ascii=False, indent=2)}

## 已有策略 (不要重复)
{existing}

## 可用的CONFIG参数
{config_keys}

## 策略参数说明
- auto_strategy_budget_ms: 自动策略生成的预算(ms)，范围50-800
- local_search_budget_ms: 局部搜索预算(ms)，范围0-8000
- max_generated_strategies: 最大生成策略数，范围4-96
- max_candidates_per_mask: 每个掩码最大候选数，范围10-100
- pair_top_k: 双任务组的top-k，范围8-64
- triple_top_k: 三任务组的top-k，范围4-48
- try_triples: 是否尝试三任务组(true/false)
- ilp_time_limit_seconds: ILP求解时限(s)，0表示不使用
- backup_time_budget_ms: 备选骑手分配预算(ms)，范围50-1200
- min_backup_utility: 备选方案最小收益阈值，范围-50到100
- max_extra_couriers_per_bundle: 每个bundle额外骑手数，范围0-6
- strategies: 策略权重元组列表，每个元组7个浮点数

## 输出要求
每个策略需要：
- name: 策略名(英文snake_case)
- family: greedy/hybrid/teacher/risk_aware/exact之一
- description: 中文描述
- config_overrides: 参数字典(只包含要覆盖的参数)
- scenario_prior: 各场景类型的优先级乘数(可选)
- preferred_budget_ms: 推荐运行预算(ms)
- min_budget_ms: 最小预算(ms)

请针对分析报告中的弱场景生成针对性策略。"""

    system = "你是一个优化算法策略生成专家。请输出JSON格式的策略配置列表。确保参数值在合法范围内。"

    try:
        result = chat_json(prompt, schema=STRATEGY_LIST_SCHEMA, system=system, temperature=0.4)
        strategies = result.get("strategies", [])
        # Clamp all configs
        for s in strategies:
            s["config_overrides"] = _clamp_config(s.get("config_overrides", {}))
        return strategies[:count]
    except Exception as exc:
        print(f"[llm_analyzer] strategy generation failed: {exc}")
        return []


def analyze_failure_patterns(
    memory_path: str | Path = "memory/experiments.sqlite",
    scenario: str | None = None,
) -> dict[str, Any]:
    """Deep failure analysis using LLM, beyond rule-based tags.

    Reads failure tag distributions and associated performance data,
    then asks Mimo to identify root causes and suggest fixes.
    """
    memory_path = Path(memory_path)
    if not memory_path.exists():
        return {"error": "No experiment database"}

    conn = sqlite3.connect(str(memory_path))
    conn.row_factory = sqlite3.Row

    query = """
        SELECT scenario_type, strategy_name, failure_tags,
               reward, penalty_score, covered_tasks, total_tasks,
               runtime_ms, feature_json
        FROM experiments
        WHERE failure_tags IS NOT NULL AND failure_tags != '' AND failure_tags != '[]'
    """
    params: list = []
    if scenario:
        query += " AND scenario_type = ?"
        params.append(scenario)
    query += " ORDER BY reward ASC LIMIT 200"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        return {"message": "No failure data found"}

    # Aggregate failures
    failure_data = []
    for row in rows[:50]:  # Limit context size
        try:
            tags = json.loads(row["failure_tags"])
        except (json.JSONDecodeError, TypeError):
            continue
        failure_data.append({
            "scenario": row["scenario_type"],
            "strategy": row["strategy_name"],
            "tags": tags,
            "reward": row["reward"],
            "coverage": f"{row['accepted_orders']}/{row['total_tasks']}",
            "penalty": row["penalty_score"],
        })

    prompt = f"""以下是一个调度优化Agent的失败案例汇总。请分析失败模式的根本原因并给出改进建议。

## 失败案例 (最差的{len(failure_data)}个)
{json.dumps(failure_data, ensure_ascii=False, indent=2)}

## 失败标签说明
- invalid_solution: 解无效
- duplicate_tasks: 任务重复分配
- courier_scarcity: 骑手不足
- candidate_sparsity: 候选稀疏
- unmatched_orders: 未匹配订单
- underused_bundles: bundle利用不足
- overused_bad_bundles: bundle过度使用
- low_willingness_without_backups: 低接单率无备选
- low_acceptance_quality: 接受质量差

请分析：
1. 这些失败之间有没有共同的根本原因？
2. 哪些失败标签组合频繁出现？
3. 对于每种失败模式，最有效的参数调整是什么？
4. 是否需要全新的算法策略来解决某些失败模式？"""

    system = "你是一个组合优化问题的调试专家。请用中文分析并给出具体可操作的建议。"

    try:
        return chat_json(prompt, system=system, temperature=0.2)
    except Exception as exc:
        return {"error": str(exc), "raw_failure_count": len(failure_data)}


def suggest_case_generation(
    performance_summary: dict[str, Any],
    existing_case_types: list[str] | None = None,
    count: int = 6,
) -> list[dict[str, Any]]:
    """Suggest what types of training cases to generate next.

    Based on current performance gaps, recommend case parameters
    that would best improve the agent's weaknesses.
    """
    existing = existing_case_types or ["low_willingness", "scarce_couriers", "high_noise", "medium"]

    prompt = f"""你是一个训练数据生成专家。根据以下性能报告，建议下一轮应该生成什么类型的训练case。

## 当前性能摘要
{json.dumps(performance_summary, ensure_ascii=False, indent=2)}

## 已有的case类型
{existing}

## 可选的case模式
- low_willingness: 低接单率场景
- scarce_couriers: 骑手稀缺场景
- high_noise: 高噪声场景
- medium: 中等复杂度场景
- sparse: 稀疏候选场景
- bundle_heavy: bundle密集场景

请建议{count}个case规格，每个包含：
- mode: case模式
- num_tasks: 任务数(10-80)
- num_couriers: 骑手数(10-80)
- sample_rate: 采样率(0.3-1.0)
- reason: 为什么要生成这种case

重点针对当前覆盖不足的场景类型。"""

    system = "你是一个训练数据设计专家。请输出JSON格式的case规格列表。"

    try:
        result = chat_json(prompt, schema=CASE_SPECS_SCHEMA, system=system, temperature=0.3)
        return result.get("case_specs", [])[:count]
    except Exception as exc:
        print(f"[llm_analyzer] case generation suggestion failed: {exc}")
        return []


def reflect_on_benchmark(
    benchmark_results: list[dict[str, Any]],
    iteration: int,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Post-benchmark reflection: assess progress and decide next steps.

    Called after each full benchmark pass. The LLM evaluates whether
    the training loop is converging and suggests focus areas.
    """
    history = history or []

    # Summarize current results
    summary = {
        "iteration": iteration,
        "total_cases": len(benchmark_results),
        "valid_rate": sum(1 for r in benchmark_results if r.get("valid")) / max(1, len(benchmark_results)),
        "avg_penalty": sum(r.get("penalty_score", 0) for r in benchmark_results) / max(1, len(benchmark_results)),
        "avg_coverage": sum(r.get("covered_tasks", 0) / max(1, r.get("total_tasks", 1)) for r in benchmark_results) / max(1, len(benchmark_results)),
        "worst_cases": sorted(benchmark_results, key=lambda r: r.get("penalty_score", 0), reverse=True)[:5],
    }

    # Show trend from history
    trend = ""
    if len(history) >= 2:
        recent = history[-5:]
        trend = "\n## 历史趋势\n" + "\n".join(
            f"  轮次{h.get('iteration', '?')}: penalty={h.get('avg_penalty', '?'):.1f} coverage={h.get('avg_coverage', '?'):.3f}"
            for h in recent
        )

    prompt = f"""你是一个机器学习训练过程的监督者。请评估当前训练进展并给出建议。

## 当前轮次结果 (第{iteration}轮)
{json.dumps(summary, ensure_ascii=False, indent=2)}
{trend}

请评估：
1. 训练是否在有效收敛？（对比历史趋势）
2. 当前最突出的问题是什么？
3. 下一轮应该重点做什么？（继续变异搜索 / 调整case生成 / 引入新策略 / 什么都可以）
4. 是否应该停止训练？（如果已收敛或在发散）

请给出客观评估。"""

    system = "你是一个训练过程监督专家。请输出JSON格式的反思报告。用中文回答。"

    try:
        return chat_json(prompt, schema=REFLECTION_SCHEMA, system=system, temperature=0.2)
    except Exception as exc:
        print(f"[llm_analyzer] reflection failed: {exc}")
        return {
            "overall_assessment": "分析失败，继续训练",
            "should_continue": True,
            "top_issues": [],
            "action_items": [],
        }
