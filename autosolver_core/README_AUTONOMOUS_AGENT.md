# 完整自主学习型 AutoSolver Agent 说明

本版本将原始高性能启发式求解器保留为 `core_solver.py`，并新增一层可训练、可记忆、可归因的 AutoSolver Agent。提交入口仍然是 `solver.py` 中的 `solve(input_text)`。

本次改造已经按反馈补齐“接近高性能 solver.py”的训练闭环：**高性能 solver 作为 teacher → 采集 teacher baseline → teacher-relative reward → 策略/预算联合学习 → 参数搜索回灌 → holdout 验证**。

## 1. 核心思想

不要让 Agent 在线重新发明算法，而是让它学习：

```text
当前实例特征 + 当前时间预算 → 哪个 core_solver 策略/参数最接近 teacher 表现
```

其中：

- `core_solver.py` 是高性能 teacher；
- `solver.py` 是 Agent 包装入口；
- `agent/strategy_registry.py` 内置 teacher 策略与预算变体；
- `training/collect_experiments.py` 会记录每个策略相对 teacher 的差距；
- `training/train_selector.py` 会把经验库导出为线上策略选择表。

## 2. 新增/强化能力

### 2.1 Teacher 策略

`agent/strategy_registry.py` 已新增：

- `core_single_teacher`：高性能 core_solver + 严格单骑手输出；
- `core_single_teacher@9200ms`：显式 9.2s teacher 预算；
- `core_default_teacher`：高性能 core_solver 默认行为；
- `core_default_teacher@9200ms`：显式 9.2s 默认 teacher；
- `core_parallel_teacher`：允许多骑手备选的 teacher，仅当规则允许时使用。

默认不允许多骑手并行指派，符合“一个订单只能指派给一个骑手”的描述。若赛题评测允许多骑手同时指派，可设置：

```bash
MEITUAN_ALLOW_PARALLEL=1 python benchmark.py training_cases --module solver
```

### 2.2 Teacher-relative reward

`agent/reward.py` 新增：

```python
compute_teacher_relative_reward(result, teacher_result, runtime_ms, hard_time_limit_ms)
```

训练时不再只看绝对 penalty，而是学习：

```text
coverage_gap = teacher_covered - strategy_covered
penalty_gap  = strategy_penalty - teacher_penalty
```

少接一个订单会被巨大惩罚；覆盖数一致时才比较 penalty gap。这样训练目标会稳定逼近高性能 `core_solver.py`。

### 2.3 策略/预算联合学习

策略库中已加入显式预算变体，例如：

```text
single_balanced_search@2500ms
single_balanced_search@5000ms
single_scarce_bundle_repair@5000ms
single_scarce_bundle_repair@7000ms
core_single_teacher@9200ms
```

这样模型能区分“同一策略短预算”和“同一策略长预算”的效果。

### 2.4 参数搜索回灌

`training/tune_params.py` 会搜索 `core_solver.CONFIG` 的邻域，并把最优参数导出到：

```text
models/best_config_candidates.json
```

然后可以用：

```bash
python training/export_strategy_candidates.py models/best_config_candidates.json
```

把候选配置转成 `Strategy(...)` 代码片段，再选择稳定通过验证集的配置复制进 `agent/strategy_registry.py`。

### 2.5 Holdout 验证

新增：

```text
training/validate_against_teacher.py
```

用于比较 Agent 与 teacher 的差距，输出：

- coverage match rate；
- average penalty gap；
- median penalty gap；
- p95 penalty gap；
- invalid count；
- runtime p95。

## 3. 文件结构

```text
solver.py                         # 最终提交入口，自主 Agent 包装器
core_solver.py                    # 原始高性能确定性优化器 / teacher
agent/
  feature_extractor.py            # 特征提取与场景识别
  strategy_registry.py            # teacher 策略、预算变体、安全参数化执行
  meta_controller.py              # 在线策略调度器
  evaluator.py                    # 官方式快速评估器
  failure_analyzer.py             # 失败归因
  memory.py                       # SQLite 经验记忆
  reward.py                       # 绝对 reward + teacher-relative reward
training/
  collect_experiments.py          # 离线采集实验，支持 teacher baseline
  train_selector.py               # 训练/导出策略价值模型
  tune_params.py                  # teacher-relative 参数变异搜索
  export_strategy_candidates.py   # 把搜索结果转成 Strategy 片段
  validate_against_teacher.py     # holdout 验证 Agent-teacher gap
  analyze_failures.py             # 失败归因统计
models/
  strategy_selector.json          # 冷启动 teacher-prior 策略表
memory/
  .gitkeep                        # 运行后生成 experiments.sqlite
```

## 4. 推荐训练流程

### 4.1 清理旧经验

```bash
rm -f memory/experiments.sqlite
```

### 4.2 采集 teacher-relative 实验

严格单骑手规则：

```bash
python training/collect_experiments.py training_cases \
  --memory memory/experiments.sqlite \
  --teacher single \
  --teacher-budget-ms 9200
```

如果允许多骑手备选：

```bash
python training/collect_experiments.py training_cases \
  --memory memory/experiments.sqlite \
  --teacher best \
  --teacher-budget-ms 9200 \
  --allow-parallel
```

### 4.3 训练策略选择器

```bash
python training/train_selector.py \
  --memory memory/experiments.sqlite \
  --out models/strategy_selector.json
```

### 4.4 场景定向参数搜索

普通全局搜索：

```bash
python training/tune_params.py training_cases \
  --rounds 300 \
  --budget-ms 3500 \
  --teacher single \
  --memory memory/experiments.sqlite
```

骑手稀缺场景：

```bash
python training/tune_params.py case_bank/train/scarce \
  --rounds 500 \
  --budget-ms 7000 \
  --teacher single \
  --memory memory/experiments.sqlite
```

低意愿场景：

```bash
python training/tune_params.py case_bank/train/low_willingness \
  --rounds 500 \
  --budget-ms 7000 \
  --teacher single \
  --memory memory/experiments.sqlite
```

### 4.5 导出策略片段并回灌

```bash
python training/export_strategy_candidates.py models/best_config_candidates.json
```

选择在验证集上稳定的配置，复制到 `agent/strategy_registry.py` 的 `_BASE_STRATEGIES` 中。

### 4.6 Holdout 验证

```bash
MEITUAN_DISABLE_MEMORY=1 \
MEITUAN_AGENT_EXPLORE=0 \
MEITUAN_AGENT_MAX_ATTEMPTS=3 \
MEITUAN_AGENT_BUDGET_MS=9200 \
python training/validate_against_teacher.py training_cases --teacher single
```

关注目标：

| 指标 | 理想目标 |
|---|---:|
| coverage_match_rate | 1.0000 |
| avg_penalty_gap | 尽量接近 0 |
| p95_penalty_gap | 尽量接近 0 |
| invalid_count | 0 |
| p95_runtime_ms | < 9500 |

## 5. 正式提交建议

```bash
MEITUAN_DISABLE_MEMORY=1
MEITUAN_AGENT_EXPLORE=0
MEITUAN_AGENT_MAX_ATTEMPTS=2
MEITUAN_AGENT_BUDGET_MS=9200
```

含义：

- 禁用在线写 SQLite，降低不稳定性；
- 关闭探索，只使用训练好的策略；
- 保留 teacher 作为性能底线；
- 9.2 秒主动截止，避免 10 秒超时。

## 6. 常用命令

### 在线评估

```bash
python benchmark.py training_cases --module solver
```

### 直接评估 teacher/core_solver

```bash
python benchmark.py training_cases --module core_solver
```

### 查看单个样例 Agent 轨迹

```bash
python main.py training_cases/synthetic_medium_30_seed201.txt --json
```

### 失败原因统计

```bash
python training/analyze_failures.py --memory memory/experiments.sqlite
```

## 7. 设计原则

正式 10 秒求解时不调用 LLM，不在线生成任意代码。LLM 只适合离线分析失败日志、提出新策略、辅助生成策略参数。线上 Agent 采用“离线学习 + 在线快速调度 + teacher 性能底线”的方式，稳定性更高。
