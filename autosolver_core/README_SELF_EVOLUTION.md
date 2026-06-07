# 端对端自迭代 Agent 说明 (Self-Evolution)

本版本在原有半自动Agent架构基础上，新增**端对端自迭代流水线**和**小米Mimo-V2.5-pro LLM集成**，实现了：

- **自动训练闭环**：无需人工干预，Agent 自动生成训练数据 → 收集实验 → 学习策略 → 验证迭代
- **LLM驱动分析**：Mimo-V2.5-pro 分析训练数据、生成新策略配置、反思优化方向
- **自迭代收敛**：自动检测收敛，动态调整训练方向

## 架构概览

```
self_evolution.py (主入口)
    │
    ├── 生成合成训练case (LLM可引导)
    ├── collect_experiments.py (所有策略×所有case)
    ├── train_selector.py (策略选择器)
    ├── tune_params.py (参数变异搜索)
    │
    ├── ★ LLM分析阶段 (每N轮) ★
    │   ├── llm_analyzer.analyze_training_results()
    │   ├── llm_analyzer.generate_new_strategies()
    │   ├── auto_strategy_injector.inject_strategies()
    │   └── llm_analyzer.suggest_case_generation()
    │
    ├── autosolver_agent.py (演化训练)
    ├── benchmark.py (验证)
    │
    └── ★ LLM反思 ★
        └── llm_analyzer.reflect_on_benchmark()
```

## 快速开始

### 安装依赖

```bash
pip install openai>=1.0.0
```

### 配置 Mimo API

复制 `.env.example` 为 `.env`，确认以下配置：

```env
MIMO_API_KEY=tp-c25756taquaw88yfnbjxfon4k40r13tr9z4gpen12dfmllx3
MIMO_BASE_URL=https://token-plan-cn.xiaomimimo.com/v1
MIMO_MODEL=mimo-v2.5-pro
```

### 运行自迭代

```bash
# 快速测试 (3轮, ~10分钟)
python self_evolution.py --max-rounds 3

# 标准训练 (2小时)
python self_evolution.py --hours 2

# 深度训练 (6小时, LLM每3轮分析一次)
python self_evolution.py --hours 6 --llm-every 3

# 从检查点恢复
python self_evolution.py --resume
```

### 运行原有训练

```bash
# 原有24小时训练 (现已集成LLM分析)
python long_train.py --hours 24 --llm-every 5

# 原有离线实验收集
python training/collect_experiments.py case_bank/train --teacher single

# 原有策略选择器训练
python training/train_selector.py --memory memory/experiments.sqlite
```

## 新增模块

### `agent/llm_client.py` — Mimo API客户端

```python
from agent.llm_client import chat, chat_json

# 普通对话
response = chat("分析这个优化问题的特点")

# JSON结构化输出
result = chat_json("生成3个策略配置", schema={...})
```

### `agent/llm_analyzer.py` — LLM分析器

| 函数 | 说明 |
|------|------|
| `analyze_training_results()` | 分析实验数据库，找出弱场景和改进方向 |
| `generate_new_strategies()` | 根据分析生成新的Strategy配置 |
| `analyze_failure_patterns()` | 深度失败模式分析 |
| `suggest_case_generation()` | 建议下一轮训练case类型 |
| `reflect_on_benchmark()` | 训练过程反思，判断是否继续 |

### `agent/auto_strategy_injector.py` — 自动策略注入

```python
from agent.auto_strategy_injector import inject_strategies

# 从LLM输出自动注入策略
injected = inject_strategies(
    strategy_dicts=llm_output,
    test_cases=holdout_pairs,  # 可选：验证
)
```

### `agent/strategy_registry.py` — 运行时策略管理 (新增API)

```python
from agent.strategy_registry import add_strategy, remove_strategy, Strategy

# 动态添加策略
add_strategy(Strategy(
    name="my_custom_strategy",
    family="hybrid",
    description="...",
    config_overrides={"local_search_budget_ms": 3000.0},
    preferred_budget_ms=5000.0,
))

# 移除策略
remove_strategy("my_custom_strategy")
```

## 核心流程详解

### 自迭代循环

每一轮包含：

1. **生成训练数据** — 使用默认规格 + LLM建议的case类型
2. **收集实验** — 所有16个策略在所有case上运行，记录teacher-relative reward
3. **训练选择器** — 从实验数据生成策略选择策略表
4. **参数变异** — 随机搜索core_solver CONFIG空间，找最优参数组合
5. **LLM分析** (每N轮) — Mimo分析弱场景、生成新策略、建议case类型
6. **自动注入** — LLM生成的策略自动验证并注入注册表
7. **演化训练** — 进化算法优化solver CONFIG
8. **基准测试** — 在holdout case上验证性能
9. **LLM反思** — 评估是否收敛、建议下一步方向
10. **保存检查点** — 支持中断恢复

### 收敛条件

- 连续6轮无改善 → 自动停止
- LLM建议停止 → 自动停止
- 达到时间/轮次上限 → 自动停止

### 输出文件

| 文件 | 说明 |
|------|------|
| `solver_best_evolved.py` | 演化得到的最佳solver快照 |
| `self_evolution_log.jsonl` | 每轮训练日志 |
| `self_evolution_summary.json` | 最终总结 |
| `self_evolution_checkpoint.json` | 检查点 (用于resume) |
| `models/strategy_selector.json` | 学到的策略选择表 |
| `memory/experiments.sqlite` | 实验数据库 |

## 与原有系统的兼容性

- `solver.py` — 提交入口不变
- `main.py` — CLI入口不变
- `agent/graph.py` — 原有LangGraph Agent保留，LLM后端切换为Mimo
- `training/` — 原有训练脚本全部保留可用
- `core_solver.py` — 高性能求解器不变

## LLM 成本控制

- 默认每3轮进行一次LLM分析（可通过 `--llm-every` 调整）
- LLM分析只发送聚合统计数据，不发送原始case数据
- 所有LLM调用都有重试和超时机制
- LLM失败时自动跳过，不影响确定性训练流程
