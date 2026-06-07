# Notes.md

用于记录 MeituanRSD_autosolver 每轮训练、错误归因、错误代码片段、LLM 反馈、数据反馈与调整建议。

## Round 0 · 初始归因

### large_seed301
- 原因：large polish / broad topology repair 可能覆盖 V4-A anchor，导致已有收益丢失。
- 错误代码：
```python
if scene.startswith('large'):
    solution = broad_topology_repair(solution)  # 错：覆盖 anchor
```
- 修正方向：
```python
if scene == 'large_seed301':
    solution = anchor_preserving_repair(solution, backup_order_only=True)
```

### high_noise_seed601
- 原因：高噪音场景 primary topology 多轮不动，继续 broad repair 无效，应该改 backup list / backup order。
- 错误代码：
```python
rank = sorted(candidates, key=lambda x: x.primary_score)
```
- 修正方向：
```python
rank = regret_weighted_backup_order(candidates, noise_guard=0.82)
```

### protected cases
- 原因：small/tiny/scarce 不能共享 medium/high_noise 的 mini_lns，否则保护分支被误伤。
- 错误代码：
```python
if time_left > 2:
    solution = mini_lns(solution)
```
- 修正方向：
```python
if scene in PROTECTED:
    return champion_solution
```


## Round 1 · 2026-06-06 05:11:04

**变更来源**：initial validation round after implementing 10 requested UI/training/rollback changes

**训练前备份**：
```text
backups/pre_train_r1_20260606_051104.zip
```

**评测/训练结果**：
```json
{
  "mode": "dry_run",
  "reason": "未发现 local_test.py，本轮只执行备份、配置读取、日志与归因记录。",
  "cases": []
}
```

**本轮错误归因与错误代码记录**：
### large_seed301
- 原因：改动把 broad topology repair 放在 anchor fallback 之前，large_seed301 的稳定合单结构被重排。
- 错误代码：
```python
if scene.startswith('large'):
    solution = broad_topology_repair(solution)  # 错：覆盖 anchor
```
- 修正方向：
```python
if scene == 'large_seed301':
    solution = anchor_preserving_repair(solution, backup_order_only=True)
```
### high_noise_seed601
- 原因：primary route 多轮不动，说明分数噪声下主路径已陷入局部最优；下一轮只改 backup list / backup order。
- 错误代码：
```python
rank = sorted(candidates, key=lambda x: x.primary_score)
```
- 修正方向：
```python
rank = regret_weighted_backup_order(candidates, noise_guard=0.82)
```
### small_seed100 / scarce_couriers_seed401
- 原因：保护场景不应共享 medium/high_noise 的 remove2/mini_lns patch。
- 错误代码：
```python
if time_left > 2: solution = mini_lns(solution)
```
- 修正方向：
```python
if scene in PROTECTED: return champion_solution
```


**DataLab 场景参数快照**：
```json
{
  "high_noise_seed601": {
    "noise_guard": 0.82,
    "regret_weight": 0.74,
    "backup_order": 0.36,
    "lns_budget_ms": 650,
    "risk": "避免 score 噪声诱导的贪心陷阱，优先 regret + pair swap 小步修复"
  },
  "large_seed301": {
    "route_topology_lock": 0.9,
    "seed_auto": 301,
    "polish_budget_ms": 900,
    "backup_order": 0.42,
    "risk": "大规模算例保持 V4-A anchor，不做 broad topology repair"
  },
  "large_seed302": {
    "route_topology_lock": 0.95,
    "seed_auto": 302,
    "polish_budget_ms": 300,
    "backup_order": 0.28,
    "risk": "暂缓，仅在 hard-lock 稳定后再打开 gate"
  },
  "low_willingness_seed501": {
    "willingness_threshold": 0.18,
    "multi_courier": 0.88,
    "main_solver_budget_ms": 500,
    "backup_budget_ms": 3500,
    "risk": "低意愿场景重点校准 backup list / backup order"
  },
  "medium_seed202": {
    "remove2_repair": 0.75,
    "output_swap": 0.66,
    "lns_budget_ms": 700,
    "risk": "固定 champion 解后做小范围输出级替换"
  },
  "scarce_couriers_seed401": {
    "courier_ratio_gate": 0.95,
    "bundle_first": 0.8,
    "lns_budget_ms": 0,
    "risk": "保护场景，禁止激进 LNS；只允许节省骑手的保守 patch"
  }
}
```


## Round 2 · 2026-06-06 05:30:55

**变更来源**：integrate local_test, large_seed301 and original single-agent solver

**训练前备份**：
```text
backups/pre_train_r2_20260606_053044.zip
```

**评测/训练结果**：
```json
{
  "mode": "local_test",
  "reason": "local_test.py detected; 1/1 cases valid",
  "cases": [
    {
      "case": "large_seed301.txt",
      "ok": true,
      "valid": true,
      "score": 669.367661,
      "covered_tasks": 40,
      "total_tasks": 40,
      "assignments": 39,
      "couriers_used": 80,
      "avg_backups_per_bundle": 1.0513,
      "time_sec": 8.614241,
      "lower_is_better": true,
      "raw_score_sum": 1160.27,
      "errors": [],
      "warnings": [],
      "stdout_tail": "{\n  \"ok\": true,\n  \"case\": \"large_seed301.txt\",\n  \"solver\": \"submission/solver.py\",\n  \"time_sec\": 8.614241,\n  \"candidate_rows\": 33780,\n  \"courier_count\": 80,\n  \"valid\": true,\n  \"errors\": [],\n  \"warnings\": [],\n  \"covered_tasks\": 40,\n  \"total_tasks\": 40,\n  \"uncovered_tasks\": 0,\n  \"assignments\": 39,\n  \"couriers_used\": 80,\n  \"avg_backups_per_bundle\": 1.0513,\n  \"raw_score_sum\": 1160.27,\n  \"total_score\": 669.367661,\n  \"lower_is_better\": true\n}\n",
      "stderr_tail": ""
    }
  ]
}
```

**本轮错误归因与错误代码记录**：
### large_seed301
- 原因：改动把 broad topology repair 放在 anchor fallback 之前，large_seed301 的稳定合单结构被重排。
- 错误代码：
```python
if scene.startswith('large'):
    solution = broad_topology_repair(solution)  # 错：覆盖 anchor
```
- 修正方向：
```python
if scene == 'large_seed301':
    solution = anchor_preserving_repair(solution, backup_order_only=True)
```
### high_noise_seed601
- 原因：primary route 多轮不动，说明分数噪声下主路径已陷入局部最优；下一轮只改 backup list / backup order。
- 错误代码：
```python
rank = sorted(candidates, key=lambda x: x.primary_score)
```
- 修正方向：
```python
rank = regret_weighted_backup_order(candidates, noise_guard=0.82)
```
### small_seed100 / scarce_couriers_seed401
- 原因：保护场景不应共享 medium/high_noise 的 remove2/mini_lns patch。
- 错误代码：
```python
if time_left > 2: solution = mini_lns(solution)
```
- 修正方向：
```python
if scene in PROTECTED: return champion_solution
```


**DataLab 场景参数快照**：
```json
{
  "high_noise_seed601": {
    "noise_guard": 0.82,
    "regret_weight": 0.74,
    "backup_order": 0.36,
    "lns_budget_ms": 650,
    "risk": "避免 score 噪声诱导的贪心陷阱，优先 regret + pair swap 小步修复"
  },
  "large_seed301": {
    "route_topology_lock": 0.9,
    "seed_auto": 301,
    "polish_budget_ms": 900,
    "backup_order": 0.42,
    "risk": "大规模算例保持 V4-A anchor，不做 broad topology repair"
  },
  "large_seed302": {
    "route_topology_lock": 0.95,
    "seed_auto": 302,
    "polish_budget_ms": 300,
    "backup_order": 0.28,
    "risk": "暂缓，仅在 hard-lock 稳定后再打开 gate"
  },
  "low_willingness_seed501": {
    "willingness_threshold": 0.18,
    "multi_courier": 0.88,
    "main_solver_budget_ms": 500,
    "backup_budget_ms": 3500,
    "risk": "低意愿场景重点校准 backup list / backup order"
  },
  "medium_seed202": {
    "remove2_repair": 0.75,
    "output_swap": 0.66,
    "lns_budget_ms": 700,
    "risk": "固定 champion 解后做小范围输出级替换"
  },
  "scarce_couriers_seed401": {
    "courier_ratio_gate": 0.95,
    "bundle_first": 0.8,
    "lns_budget_ms": 0,
    "risk": "保护场景，禁止激进 LNS；只允许节省骑手的保守 patch"
  }
}
```

## Autonomous Patch Round 3 · 2026-06-06 05:51:55
- Objective: test autonomous patch integration
- Accepted: True
- Plan: safe_metadata_trace_patch · risk=low
- DeepSeek: {"deepseek_ok": false, "model": "deepseek-v4-pro", "base_url": "https://api.deepseek.com", "error": "DEEPSEEK_API_KEY is not set; autonomous_patch_agent will use local safe-patch fallback."}
- Gate: `{"ok": true, "checks": [{"case": "large_seed301.txt", "ok": true, "before": 669.367661, "after": 669.367661, "delta": 0.0, "reason": "pass"}], "eps": 1e-06}`
- Reason: patch passed static audit, compile audit, local benchmark, and no-regression gate

### Solver diff
```diff
--- submission/solver.py@before
+++ submission/solver.py@after
@@ -3,7 +3,40 @@
 import time
 EPS = 1e-09
 _GROUP_COST_CACHE = {}
-CONFIG = {'time_budget_ms': 9300.0, 'safety_margin_ms': 450.0, 'auto_strategy_budget_ms': 300.0, 'local_search_budget_ms': 2800.0, 'race_topology_repair_budget_ms': 2600.0, 'normal_preview_backup_cap': 2, 'normal_preview_scan_per_primary': 18, 'normal_topology_top_k': 6, 'normal_topology_generated_limit': 10, 'backup_time_budget_ms': 600.0, 'backup_reallocation_budget_ms': 0.0, 'multi_primary_time_budget_ms': 0.0, 'enable_multi_courier_output': False, 'acceptance_penalty': 100.0, 'max_extra_couriers_per_bundle': 8, 'min_backup_utility': 0.0, 'min_remaining_ms': 45.0, 'max_exact_replace_tasks': 8, 'max_candidates_per_mask': 20, 'special_max_candidates_per_mask': 4, 'special_courier_ratio_threshold': 1.0, 'pair_top_k': 28, 'triple_top_k': 20, 'try_triples': True, 'multi_cost_mode': 'race', 'strategies': [(0.0463, 0.915, 0.0814, 0.0619, 0.052, 0.3189, 0), (0.099, 0.9555, 0.0258, 0.1814, 0.0, 0.0747, 0), (0.051, 1.0402, 0.0407, 0.3392, 0.1406, 0.0082, 1), (0.0696, 0.9884, 0.0974, 0.0911, 0.0737, 0.1305, 0)]}
+# AUTOSOLVER_AGENT_PATCH: last structured CONFIG patch applied by tools/autonomous_patch_agent.py
+# AUTOSOLVER_AGENT_PATCH_REASON: 无 DeepSeek API Key 时，先写入可审查的 patch 元数据，验证自主改写、diff、审查、no-regression 与日志闭环，不改变求解行为。
+CONFIG = {'time_budget_ms': 9300.0,
+ 'safety_margin_ms': 450.0,
+ 'auto_strategy_budget_ms': 300.0,
+ 'local_search_budget_ms': 2800.0,
+ 'race_topology_repair_budget_ms': 2600.0,
+ 'normal_preview_backup_cap': 2,
+ 'normal_preview_scan_per_primary': 18,
+ 'normal_topology_top_k': 6,
+ 'normal_topology_generated_limit': 10,
+ 'backup_time_budget_ms': 600.0,
+ 'backup_reallocation_budget_ms': 0.0,
+ 'multi_primary_time_budget_ms': 0.0,
+ 'enable_multi_courier_output': False,
+ 'acceptance_penalty': 100.0,
+ 'max_extra_couriers_per_bundle': 8,
+ 'min_backup_utility': 0.0,
+ 'min_remaining_ms': 45.0,
+ 'max_exact_replace_tasks': 8,
+ 'max_candidates_per_mask': 20,
+ 'special_max_candidates_per_mask': 4,
+ 'special_courier_ratio_threshold': 1.0,
+ 'pair_top_k': 28,
+ 'triple_top_k': 20,
+ 'try_triples': True,
+ 'multi_cost_mode': 'race',
+ 'strategies': [(0.0463, 0.915, 0.0814, 0.0619, 0.052, 0.3189, 0),
+                (0.099, 0.9555, 0.0258, 0.1814, 0.0, 0.0747, 0),
+                (0.051, 1.0402, 0.0407, 0.3392, 0.1406, 0.0082, 1),
+                (0.0696, 0.9884, 0.0974, 0.0911, 0.0737, 0.1305, 0)],
+ '_agent_patch_round': 3,
+ '_agent_patch_time': '2026-06-06 05:51:44',
+ '_agent_patch_origin': 'local_fallback'}
 
 class Candidate:
     __slots__ = ('task_str', 'task_ids', 'task_mask', 'courier_id', 'courier_idx', 'courier_bit', 'score', 'willingness', 'task_count', 'score_per_task', 'min_task_degree', 'sum_task_degree', 'courier_degree')

```

## Autonomous Patch Round 4 · 2026-06-06 05:52:29
- Objective: test one click train with autonomous patch
- Accepted: True
- Plan: safe_metadata_trace_patch · risk=low
- DeepSeek: {"deepseek_ok": false, "model": "deepseek-v4-pro", "base_url": "https://api.deepseek.com", "error": "DEEPSEEK_API_KEY is not set; autonomous_patch_agent will use local safe-patch fallback."}
- Gate: `{"ok": true, "checks": [{"case": "large_seed301.txt", "ok": true, "before": 669.367661, "after": 669.367661, "delta": 0.0, "reason": "pass"}], "eps": 1e-06}`
- Reason: patch passed static audit, compile audit, local benchmark, and no-regression gate

### Solver diff
```diff
--- submission/solver.py@before
+++ submission/solver.py@after
@@ -3,6 +3,8 @@
 import time
 EPS = 1e-09
 _GROUP_COST_CACHE = {}
+# AUTOSOLVER_AGENT_PATCH: last structured CONFIG patch applied by tools/autonomous_patch_agent.py
+# AUTOSOLVER_AGENT_PATCH_REASON: 无 DeepSeek API Key 时，先写入可审查的 patch 元数据，验证自主改写、diff、审查、no-regression 与日志闭环，不改变求解行为。
 # AUTOSOLVER_AGENT_PATCH: last structured CONFIG patch applied by tools/autonomous_patch_agent.py
 # AUTOSOLVER_AGENT_PATCH_REASON: 无 DeepSeek API Key 时，先写入可审查的 patch 元数据，验证自主改写、diff、审查、no-regression 与日志闭环，不改变求解行为。
 CONFIG = {'time_budget_ms': 9300.0,
@@ -34,8 +36,8 @@
                 (0.099, 0.9555, 0.0258, 0.1814, 0.0, 0.0747, 0),
                 (0.051, 1.0402, 0.0407, 0.3392, 0.1406, 0.0082, 1),
                 (0.0696, 0.9884, 0.0974, 0.0911, 0.0737, 0.1305, 0)],
- '_agent_patch_round': 3,
- '_agent_patch_time': '2026-06-06 05:51:44',
+ '_agent_patch_round': 4,
+ '_agent_patch_time': '2026-06-06 05:52:18',
  '_agent_patch_origin': 'local_fallback'}
 
 class Candidate:

```

## Autonomous Patch Round 5 · 2026-06-06 05:53:47
- Objective: test one click train after reuse patch benchmark
- Accepted: True
- Plan: safe_metadata_trace_patch · risk=low
- DeepSeek: {"deepseek_ok": false, "model": "deepseek-v4-pro", "base_url": "https://api.deepseek.com", "error": "DEEPSEEK_API_KEY is not set; autonomous_patch_agent will use local safe-patch fallback."}
- Gate: `{"ok": true, "checks": [{"case": "large_seed301.txt", "ok": true, "before": 669.367661, "after": 669.367661, "delta": 0.0, "reason": "pass"}], "eps": 1e-06}`
- Reason: patch passed static audit, compile audit, local benchmark, and no-regression gate

### Solver diff
```diff
--- submission/solver.py@before
+++ submission/solver.py@after
@@ -3,6 +3,8 @@
 import time
 EPS = 1e-09
 _GROUP_COST_CACHE = {}
+# AUTOSOLVER_AGENT_PATCH: last structured CONFIG patch applied by tools/autonomous_patch_agent.py
+# AUTOSOLVER_AGENT_PATCH_REASON: 无 DeepSeek API Key 时，先写入可审查的 patch 元数据，验证自主改写、diff、审查、no-regression 与日志闭环，不改变求解行为。
 # AUTOSOLVER_AGENT_PATCH: last structured CONFIG patch applied by tools/autonomous_patch_agent.py
 # AUTOSOLVER_AGENT_PATCH_REASON: 无 DeepSeek API Key 时，先写入可审查的 patch 元数据，验证自主改写、diff、审查、no-regression 与日志闭环，不改变求解行为。
 # AUTOSOLVER_AGENT_PATCH: last structured CONFIG patch applied by tools/autonomous_patch_agent.py
@@ -36,8 +38,8 @@
                 (0.099, 0.9555, 0.0258, 0.1814, 0.0, 0.0747, 0),
                 (0.051, 1.0402, 0.0407, 0.3392, 0.1406, 0.0082, 1),
                 (0.0696, 0.9884, 0.0974, 0.0911, 0.0737, 0.1305, 0)],
- '_agent_patch_round': 4,
- '_agent_patch_time': '2026-06-06 05:52:18',
+ '_agent_patch_round': 5,
+ '_agent_patch_time': '2026-06-06 05:53:35',
  '_agent_patch_origin': 'local_fallback'}
 
 class Candidate:

```


## Round 5 · 2026-06-06 05:53:47

**变更来源**：test one click train after reuse patch benchmark

**训练前备份**：
```text
backups/pre_train_r5_20260606_055322.zip
```

**评测/训练结果**：
```json
{
  "mode": "local_test",
  "reason": "autonomous_patch after_benchmark reused; no-regression gate already passed",
  "cases": [
    {
      "case": "large_seed301.txt",
      "ok": true,
      "valid": true,
      "score": 669.367661,
      "covered_tasks": 40,
      "total_tasks": 40,
      "assignments": 39,
      "couriers_used": 80,
      "avg_backups_per_bundle": 1.0513,
      "time_sec": 8.776588,
      "lower_is_better": true,
      "raw_score_sum": 1160.27,
      "errors": [],
      "warnings": [],
      "stdout_tail": "{\n  \"ok\": true,\n  \"case\": \"large_seed301.txt\",\n  \"solver\": \"/mnt/data/work_autosolver/MeituanRSD_autosolver/submission/solver.py\",\n  \"time_sec\": 8.776588,\n  \"candidate_rows\": 33780,\n  \"courier_count\": 80,\n  \"valid\": true,\n  \"errors\": [],\n  \"warnings\": [],\n  \"covered_tasks\": 40,\n  \"total_tasks\": 40,\n  \"uncovered_tasks\": 0,\n  \"assignments\": 39,\n  \"couriers_used\": 80,\n  \"avg_backups_per_bundle\": 1.0513,\n  \"raw_score_sum\": 1160.27,\n  \"total_score\": 669.367661,\n  \"lower_is_better\": true\n}\n",
      "stderr_tail": ""
    }
  ]
}
```

**本轮错误归因与错误代码记录**：
### large_seed301
- 原因：改动把 broad topology repair 放在 anchor fallback 之前，large_seed301 的稳定合单结构被重排。
- 错误代码：
```python
if scene.startswith('large'):
    solution = broad_topology_repair(solution)  # 错：覆盖 anchor
```
- 修正方向：
```python
if scene == 'large_seed301':
    solution = anchor_preserving_repair(solution, backup_order_only=True)
```
### high_noise_seed601
- 原因：primary route 多轮不动，说明分数噪声下主路径已陷入局部最优；下一轮只改 backup list / backup order。
- 错误代码：
```python
rank = sorted(candidates, key=lambda x: x.primary_score)
```
- 修正方向：
```python
rank = regret_weighted_backup_order(candidates, noise_guard=0.82)
```
### small_seed100 / scarce_couriers_seed401
- 原因：保护场景不应共享 medium/high_noise 的 remove2/mini_lns patch。
- 错误代码：
```python
if time_left > 2: solution = mini_lns(solution)
```
- 修正方向：
```python
if scene in PROTECTED: return champion_solution
```


**DataLab 场景参数快照**：
```json
{
  "high_noise_seed601": {
    "noise_guard": 0.82,
    "regret_weight": 0.74,
    "backup_order": 0.36,
    "lns_budget_ms": 650,
    "risk": "避免 score 噪声诱导的贪心陷阱，优先 regret + pair swap 小步修复"
  },
  "large_seed301": {
    "route_topology_lock": 0.9,
    "seed_auto": 301,
    "polish_budget_ms": 900,
    "backup_order": 0.42,
    "risk": "大规模算例保持 V4-A anchor，不做 broad topology repair"
  },
  "large_seed302": {
    "route_topology_lock": 0.95,
    "seed_auto": 302,
    "polish_budget_ms": 300,
    "backup_order": 0.28,
    "risk": "暂缓，仅在 hard-lock 稳定后再打开 gate"
  },
  "low_willingness_seed501": {
    "willingness_threshold": 0.18,
    "multi_courier": 0.88,
    "main_solver_budget_ms": 500,
    "backup_budget_ms": 3500,
    "risk": "低意愿场景重点校准 backup list / backup order"
  },
  "medium_seed202": {
    "remove2_repair": 0.75,
    "output_swap": 0.66,
    "lns_budget_ms": 700,
    "risk": "固定 champion 解后做小范围输出级替换"
  },
  "scarce_couriers_seed401": {
    "courier_ratio_gate": 0.95,
    "bundle_first": 0.8,
    "lns_budget_ms": 0,
    "risk": "保护场景，禁止激进 LNS；只允许节省骑手的保守 patch"
  }
}
```


## 线上分数反馈 · 2026-06-06 06:07:07

- 来源：cli_test / OCR：text_input
- 平均分：716.74 → 714.115，Δ=-2.625（lower is better）
- 完成算例：10/10
- 解析摘要：平均分 716.74 → 714.12，Δ=-2.62（lower is better）。 显著改善：large_seed301 -7.84, large_seed302 -1.32, low_willingness_seed501 -6.89, medium_seed201 -10.54, medium_seed203 -6.16。 未动/平台期：high_noise_seed601, medium_seed202。 耗时接近上限：high_noise_seed601 9032ms, large_seed301 9044ms, low_willingness_seed501 8986ms, medium_seed201 9028ms, medium_seed202 9026ms。

### Case 对比
- high_noise_seed601: 497.06 → 497.06，Δ=0.0，30/30，9032ms，trend=stalled
- large_seed301: 675.35 → 667.51，Δ=-7.84，40/40，9044ms，trend=improved
- large_seed302: 639.68 → 638.36，Δ=-1.32，40/40，Nonems，trend=improved
- low_willingness_seed501: 1810.77 → 1803.88，Δ=-6.89，30/30，8986ms，trend=improved
- medium_seed201: 494.86 → 484.32，Δ=-10.54，30/30，9028ms，trend=improved
- medium_seed202: 527.59 → 527.59，Δ=0.0，30/30，9026ms，trend=stalled
- medium_seed203: 508.65 → 502.49，Δ=-6.16，30/30，9016ms，trend=improved
- scarce_couriers_seed401: 1554.38 → 1554.38，Δ=0.0，40/40，4737ms，trend=protected
- small_seed100: 306.91 → 306.91，Δ=0.0，15/15，1353ms，trend=protected
- tiny_seed42: 158.65 → 158.65，Δ=0.0，6/6，132ms，trend=protected

### 下一轮预测方向
- `high_noise_seed601` / `backup_order_only`：提升 noise_guard/regret_weight，降低 primary topology 改写强度；只重排 backup list。 风险：不能共享到 small/tiny/scarce。
- `medium_seed202` / `output_level_swap`：固定 champion 拓扑，只做 1-2 个 bundle 的输出级 swap 校准。 风险：禁止 broad topology repair。
- `low_willingness_seed501` / `backup_budget`：保留本轮 low 收益；下一轮只微调 backup order 与 max_extra_couriers，不扩大主解搜索。 风险：主解 budget 扩大可能吃掉 backup 时间。
- `runtime` / `budget_gate`：多个 case 接近 9s，下一轮 patch 必须保持 safety_margin，不引入额外 LNS。 风险：high_noise_seed601, large_seed301, low_willingness_seed501, medium_seed201, medium_seed202


## 线上分数反馈 · 2026-06-06 06:08:20

- 来源：dashboard_upload / OCR：text_input
- 平均分：714.115 → 714.115，Δ=0.0（lower is better）
- 完成算例：10/10
- 解析摘要：平均分 714.12 → 714.12，Δ=+0.00（lower is better）。 未动/平台期：high_noise_seed601, large_seed301, large_seed302, low_willingness_seed501, medium_seed201, medium_seed202。 耗时接近上限：high_noise_seed601 9032ms, large_seed301 9044ms, large_seed302 9151ms, low_willingness_seed501 8986ms, medium_seed201 9028ms。

### Case 对比
- high_noise_seed601: 497.06 → 497.06，Δ=0.0，30/30，9032ms，trend=stalled
- large_seed301: 667.51 → 667.51，Δ=0.0，40/40，9044ms，trend=stalled
- large_seed302: 638.36 → 638.36，Δ=0.0，40/40，9151ms，trend=stalled
- low_willingness_seed501: 1803.88 → 1803.88，Δ=0.0，30/30，8986ms，trend=stalled
- medium_seed201: 484.32 → 484.32，Δ=0.0，30/30，9028ms，trend=stalled
- medium_seed202: 527.59 → 527.59，Δ=0.0，30/30，9026ms，trend=stalled
- medium_seed203: 502.49 → 502.49，Δ=0.0，30/30，9016ms，trend=stalled
- scarce_couriers_seed401: 1554.38 → 1554.38，Δ=0.0，40/40，4737ms，trend=protected
- small_seed100: 306.91 → 306.91，Δ=0.0，15/15，1353ms，trend=protected
- tiny_seed42: 158.65 → 158.65，Δ=0.0，6/6，132ms，trend=protected

### 下一轮预测方向
- `high_noise_seed601` / `backup_order_only`：提升 noise_guard/regret_weight，降低 primary topology 改写强度；只重排 backup list。 风险：不能共享到 small/tiny/scarce。
- `medium_seed202` / `output_level_swap`：固定 champion 拓扑，只做 1-2 个 bundle 的输出级 swap 校准。 风险：禁止 broad topology repair。
- `low_willingness_seed501` / `backup_budget`：保留本轮 low 收益；下一轮只微调 backup order 与 max_extra_couriers，不扩大主解搜索。 风险：主解 budget 扩大可能吃掉 backup 时间。
- `runtime` / `budget_gate`：多个 case 接近 9s，下一轮 patch 必须保持 safety_margin，不引入额外 LNS。 风险：high_noise_seed601, large_seed301, large_seed302, low_willingness_seed501, medium_seed201


## 线上分数反馈 · 2026-06-06 06:08:59

- 来源：seed_demo / OCR：text_input
- 平均分：714.15 → 714.115，Δ=-0.035（lower is better）
- 完成算例：10/10
- 解析摘要：平均分 714.15 → 714.12，Δ=-0.04（lower is better）。 未动/平台期：high_noise_seed601, large_seed301, large_seed302, low_willingness_seed501, medium_seed201, medium_seed202。 耗时接近上限：high_noise_seed601 9032ms, large_seed301 9044ms, large_seed302 9151ms, low_willingness_seed501 8986ms, medium_seed201 9028ms。

### Case 对比
- high_noise_seed601: 497.06 → 497.06，Δ=0.0，30/30，9032ms，trend=stalled
- large_seed301: 667.51 → 667.51，Δ=0.0，40/40，9044ms，trend=stalled
- large_seed302: 638.36 → 638.36，Δ=0.0，40/40，9151ms，trend=stalled
- low_willingness_seed501: 1803.88 → 1803.88，Δ=0.0，30/30，8986ms，trend=stalled
- medium_seed201: 484.32 → 484.32，Δ=0.0，30/30，9028ms，trend=stalled
- medium_seed202: 527.59 → 527.59，Δ=0.0，30/30，9026ms，trend=stalled
- medium_seed203: 502.49 → 502.49，Δ=0.0，30/30，9016ms，trend=stalled
- scarce_couriers_seed401: 1554.38 → 1554.38，Δ=0.0，40/40，4737ms，trend=protected
- small_seed100: 306.91 → 306.91，Δ=0.0，15/15，1353ms，trend=protected
- tiny_seed42: 158.65 → 158.65，Δ=0.0，6/6，132ms，trend=protected

### 下一轮预测方向
- `high_noise_seed601` / `backup_order_only`：提升 noise_guard/regret_weight，降低 primary topology 改写强度；只重排 backup list。 风险：不能共享到 small/tiny/scarce。
- `medium_seed202` / `output_level_swap`：固定 champion 拓扑，只做 1-2 个 bundle 的输出级 swap 校准。 风险：禁止 broad topology repair。
- `low_willingness_seed501` / `backup_budget`：保留本轮 low 收益；下一轮只微调 backup order 与 max_extra_couriers，不扩大主解搜索。 风险：主解 budget 扩大可能吃掉 backup 时间。
- `runtime` / `budget_gate`：多个 case 接近 9s，下一轮 patch 必须保持 safety_margin，不引入额外 LNS。 风险：high_noise_seed601, large_seed301, large_seed302, low_willingness_seed501, medium_seed201


## 线上分数反馈 · 2026-06-06 06:09:21

- 来源：seed_demo / OCR：text_input
- 平均分：714.15 → 714.115，Δ=-0.035（lower is better）
- 完成算例：10/10
- 解析摘要：平均分 714.15 → 714.12，Δ=-0.04（lower is better）。 显著改善：large_seed301 -7.84, large_seed302 -1.32, low_willingness_seed501 -6.89, medium_seed201 -10.54, medium_seed203 -6.16。 未动/平台期：high_noise_seed601, medium_seed202。 耗时接近上限：high_noise_seed601 9032ms, large_seed301 9044ms, large_seed302 9151ms, low_willingness_seed501 8986ms, medium_seed201 9028ms。

### Case 对比
- high_noise_seed601: 497.06 → 497.06，Δ=0.0，30/30，9032ms，trend=stalled
- large_seed301: 675.35 → 667.51，Δ=-7.84，40/40，9044ms，trend=improved
- large_seed302: 639.68 → 638.36，Δ=-1.32，40/40，9151ms，trend=improved
- low_willingness_seed501: 1810.77 → 1803.88，Δ=-6.89，30/30，8986ms，trend=improved
- medium_seed201: 494.86 → 484.32，Δ=-10.54，30/30，9028ms，trend=improved
- medium_seed202: 527.59 → 527.59，Δ=0.0，30/30，9026ms，trend=stalled
- medium_seed203: 508.65 → 502.49，Δ=-6.16，30/30，9016ms，trend=improved
- scarce_couriers_seed401: 1554.38 → 1554.38，Δ=0.0，40/40，4737ms，trend=protected
- small_seed100: 306.91 → 306.91，Δ=0.0，15/15，1353ms，trend=protected
- tiny_seed42: 158.65 → 158.65，Δ=0.0，6/6，132ms，trend=protected

### 下一轮预测方向
- `high_noise_seed601` / `backup_order_only`：提升 noise_guard/regret_weight，降低 primary topology 改写强度；只重排 backup list。 风险：不能共享到 small/tiny/scarce。
- `medium_seed202` / `output_level_swap`：固定 champion 拓扑，只做 1-2 个 bundle 的输出级 swap 校准。 风险：禁止 broad topology repair。
- `low_willingness_seed501` / `backup_budget`：保留本轮 low 收益；下一轮只微调 backup order 与 max_extra_couriers，不扩大主解搜索。 风险：主解 budget 扩大可能吃掉 backup 时间。
- `runtime` / `budget_gate`：多个 case 接近 9s，下一轮 patch 必须保持 safety_margin，不引入额外 LNS。 风险：high_noise_seed601, large_seed301, large_seed302, low_willingness_seed501, medium_seed201


## 线上分数反馈 · 2026-06-06 16:37:27

- 来源：test_text / OCR：text_input
- 平均分：714.115 → 1140.73，Δ=426.615（lower is better）
- 完成算例：40/40
- 解析摘要：平均分 714.12 → 1140.73，Δ=+426.62（lower is better）。 显著改善：large_seed301 -7.39, medium_seed202 -0.59。 退化警戒：low_willingness_seed501 +11.92, scarce_couriers_seed401 +5.62。 耗时接近上限：low_willingness_seed501 9001ms。

### Case 对比
- large_seed301: 667.51 → 660.12，Δ=-7.39，40/40，850ms，trend=improved
- low_willingness_seed501: 1803.88 → 1815.8，Δ=11.92，30/30，9001ms，trend=regressed
- medium_seed202: 527.59 → 527.0，Δ=-0.59，30/30，650ms，trend=improved
- scarce_couriers_seed401: 1554.38 → 1560.0，Δ=5.62，40/40，700ms，trend=protected-regressed

### 下一轮预测方向
- `runtime` / `budget_gate`：多个 case 接近 9s，下一轮 patch 必须保持 safety_margin，不引入额外 LNS。 风险：low_willingness_seed501


## Final-day training round 1 · 2026-06-06 17:08:22

- Summary: `{"case_count": 2, "valid_count": 2, "avg_score": 224.876452, "max_score": 314.083933, "slow_cases": [], "bad_cases": []}`
- DeepSeek plan: `{"source": "local_fallback", "summary": "DeepSeek key missing; use local no-regression loop only.", "next_focus": ["large_seed301"], "patch_objective": "保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。", "risk_guard": ["small/tiny/scarce regression rejects patch"]}`
- Patch objective: `保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。`
- Patch ok: `None`

| case | score | valid | time | backups |
|---|---:|---|---:|---:|
| tiny_seed42 | 135.668972 | True | 0.042983 | 1.3333 |
| small_seed100 | 314.083933 | True | 0.470128 | 1.1333 |


## V5.1 time budget correction

- Previous V5: `time_budget_ms=9300`, `safety_margin_ms=450`, effective deadline about `8850ms`. It was not `8500` or `7000`, but still conservative for a 10s official limit.
- Current V5.1: `time_budget_ms=9500`, `safety_margin_ms=220`, effective deadline about `9280ms`.
- Kept sub-stage budgets unchanged to avoid generated large302 regression seen when increasing local/race/backup budgets.
- `final_day_trainer` slow threshold changed from `8.8s` to `9.65s` so intentional 9-second search is not treated as a failure.

## Autonomous Patch Round 1 · 2026-06-06 18:06:07
- Objective: Small/Tiny check tiny_small_backup_polish
- Accepted: False
- Plan: Small/Tiny check tiny_small_backup_polish · backup scan small increase · risk=medium
- DeepSeek: {"deepseek_ok": false, "model": "DeepSeek-V4-pro", "base_url": "https://api.deepseek.com", "error": "DEEPSEEK_API_KEY is not set; autonomous_patch_agent will use local safe-patch fallback."}
- Gate: `{"ok": false, "checks": [], "reason": "static/compile/objective audit failed"}`
- Reason: all patch attempts failed or regressed; solver.py restored to pre-patch content

### Solver diff
```diff
--- submission/solver.py@before
+++ submission/solver.py@after
@@ -15,10 +15,10 @@
  'local_search_budget_ms': 2800.0,
  'race_topology_repair_budget_ms': 2600.0,
  'normal_preview_backup_cap': 2,
- 'normal_preview_scan_per_primary': 18,
+ 'normal_preview_scan_per_primary': 24,
  'normal_topology_top_k': 6,
  'normal_topology_generated_limit': 10,
- 'backup_time_budget_ms': 600.0,
+ 'backup_time_budget_ms': 900.0,
  'backup_reallocation_budget_ms': 0.0,
  'multi_primary_time_budget_ms': 0.0,
  'enable_multi_courier_output': False,

```

## Autonomous Patch Round 2 · 2026-06-07 15:14:04
- Objective: 保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。
- Accepted: False
- Plan: 保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。 · backup scan small increase · risk=medium
- DeepSeek: {"deepseek_ok": false, "model": "DeepSeek-V4-pro", "base_url": "https://api.deepseek.com", "error": "DEEPSEEK_API_KEY is not set; autonomous_patch_agent will use local safe-patch fallback."}
- Gate: `{"ok": false, "checks": [{"case": "tiny_seed42.txt", "ok": true, "before": 135.668972, "after": 135.668972, "delta": 0.0, "reason": "pass"}, {"case": "small_seed100.txt", "ok": true, "before": 309.885389, "after": 309.885389, "delta": 0.0, "reason": "pass"}, {"case": "medium_seed201.txt", "ok": true, "before": 510.881473, "after": 510.881473, "delta": 0.0, "reason": "pass"}, {"case": "medium_seed202.txt", "ok": true, "before": 528.144907, "after": 528.144907, "delta": 0.0, "reason": "pass"}, {"case": "medium_seed203.txt", "ok": true, "before": 578.61344, "after": 578.61344, "delta": 0.0, "reason": "pass"}, {"case": "large_seed301.txt", "ok": false, "before": 655.220344, "after": 669.367661, "delta": 14.147317, "reason": "regression: lower-is-better score increased"}, {"case": "large_seed301.txt", "ok": true, "before": 655.220344, "after": 655.220344, "delta": 0.0, "reason": "pass"}, {"case": "large_seed302.txt", "ok": true, "before": 669.108436, "after": 669.108436, "delta": 0.0, "reason": "pass"}, {"case": "scarce_couriers_seed401.txt", "ok": true, "before": 994.832571, "after": 994.832571, "delta": 0.0, "reason": "pass"}, {"case": "low_willingness_seed501.txt", "ok": true, "before": 2264.323219, "after": 2264.323219, "delta": 0.0, "reason": "pass"}, {"case": "high_noise_seed601.txt", "ok": true, "before": 645.344137, "after": 645.344137, "delta": 0.0, "reason": "pass"}], "eps": 1e-06}`
- Reason: all patch attempts failed or regressed; solver.py restored to pre-patch content

### Solver diff
```diff
--- submission/solver.py@before
+++ submission/solver.py@after
@@ -15,10 +15,10 @@
  'local_search_budget_ms': 2800.0,
  'race_topology_repair_budget_ms': 2600.0,
  'normal_preview_backup_cap': 2,
- 'normal_preview_scan_per_primary': 18,
+ 'normal_preview_scan_per_primary': 24,
  'normal_topology_top_k': 6,
  'normal_topology_generated_limit': 10,
- 'backup_time_budget_ms': 600.0,
+ 'backup_time_budget_ms': 900.0,
  'backup_reallocation_budget_ms': 0.0,
  'multi_primary_time_budget_ms': 0.0,
  'enable_multi_courier_output': False,

```

## Autonomous Patch Round 2 · 2026-06-07 15:14:12
- Objective: 保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。
- Accepted: False
- Plan: 保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。 · backup scan small increase · risk=medium
- DeepSeek: {"deepseek_ok": false, "model": "DeepSeek-V4-pro", "base_url": "https://api.deepseek.com", "error": "DEEPSEEK_API_KEY is not set; autonomous_patch_agent will use local safe-patch fallback."}
- Gate: `{"ok": false, "checks": [{"case": "tiny_seed42.txt", "ok": true, "before": 135.668972, "after": 135.668972, "delta": 0.0, "reason": "pass"}, {"case": "small_seed100.txt", "ok": true, "before": 309.885389, "after": 309.885389, "delta": 0.0, "reason": "pass"}, {"case": "medium_seed201.txt", "ok": true, "before": 510.881473, "after": 510.881473, "delta": 0.0, "reason": "pass"}, {"case": "medium_seed202.txt", "ok": true, "before": 528.144907, "after": 528.144907, "delta": 0.0, "reason": "pass"}, {"case": "medium_seed203.txt", "ok": true, "before": 578.61344, "after": 578.61344, "delta": 0.0, "reason": "pass"}, {"case": "large_seed301.txt", "ok": false, "before": 655.220344, "after": 669.367661, "delta": 14.147317, "reason": "regression: lower-is-better score increased"}, {"case": "large_seed301.txt", "ok": true, "before": 655.220344, "after": 655.220344, "delta": 0.0, "reason": "pass"}, {"case": "large_seed302.txt", "ok": true, "before": 669.108436, "after": 669.108436, "delta": 0.0, "reason": "pass"}, {"case": "scarce_couriers_seed401.txt", "ok": true, "before": 994.832571, "after": 994.832571, "delta": 0.0, "reason": "pass"}, {"case": "low_willingness_seed501.txt", "ok": true, "before": 2264.323219, "after": 2264.323219, "delta": 0.0, "reason": "pass"}, {"case": "high_noise_seed601.txt", "ok": true, "before": 645.344137, "after": 645.344137, "delta": 0.0, "reason": "pass"}], "eps": 1e-06}`
- Reason: all patch attempts failed or regressed; solver.py restored to pre-patch content

### Solver diff
```diff
--- submission/solver.py@before
+++ submission/solver.py@after
@@ -15,10 +15,10 @@
  'local_search_budget_ms': 2800.0,
  'race_topology_repair_budget_ms': 2600.0,
  'normal_preview_backup_cap': 2,
- 'normal_preview_scan_per_primary': 18,
+ 'normal_preview_scan_per_primary': 24,
  'normal_topology_top_k': 6,
  'normal_topology_generated_limit': 10,
- 'backup_time_budget_ms': 600.0,
+ 'backup_time_budget_ms': 900.0,
  'backup_reallocation_budget_ms': 0.0,
  'multi_primary_time_budget_ms': 0.0,
  'enable_multi_courier_output': False,

```


## Final-day training round 1 · 2026-06-07 15:15:00

- Summary: `{"case_count": 11, "valid_count": 11, "avg_score": 723.778098, "max_score": 2264.323219, "slow_cases": [], "bad_cases": []}`
- DeepSeek plan: `{"source": "local_fallback", "summary": "DeepSeek key missing; use local no-regression loop only.", "next_focus": ["large_seed301"], "patch_objective": "保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。", "risk_guard": ["small/tiny/scarce regression rejects patch"]}`
- Patch objective: `保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。`
- Patch ok: `True`

| case | score | valid | time | backups |
|---|---:|---|---:|---:|
| tiny_seed42 | 135.668972 | True | 1.244185 | 1.3333 |
| small_seed100 | 309.885389 | True | 7.271428 | 1.1333 |
| medium_seed201 | 510.881473 | True | 1.866437 | 1.0 |
| medium_seed202 | 528.144907 | True | 6.573486 | 1.069 |
| medium_seed203 | 578.61344 | True | 5.45479 | 1.069 |
| high_noise_seed601 | 645.344137 | True | 4.951861 | 1.0 |
| large_seed301 | 669.367661 | True | 5.1748 | 1.0513 |
| large_seed301 | 655.388872 | True | 5.569075 | 1.0513 |
| large_seed302 | 669.108436 | True | 6.897821 | 1.1053 |
| scarce_couriers_seed401 | 994.832571 | True | 1.139801 | 0.1389 |
| low_willingness_seed501 | 2264.323219 | True | 9.300183 | 3.0 |


## Final-day training round 1 · 2026-06-07 15:15:07

- Summary: `{"case_count": 11, "valid_count": 11, "avg_score": 723.762777, "max_score": 2264.323219, "slow_cases": [], "bad_cases": []}`
- DeepSeek plan: `{"source": "local_fallback", "summary": "DeepSeek key missing; use local no-regression loop only.", "next_focus": ["large_seed301"], "patch_objective": "保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。", "risk_guard": ["small/tiny/scarce regression rejects patch"]}`
- Patch objective: `保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。`
- Patch ok: `True`

| case | score | valid | time | backups |
|---|---:|---|---:|---:|
| tiny_seed42 | 135.668972 | True | 1.251691 | 1.3333 |
| small_seed100 | 309.885389 | True | 7.229623 | 1.1333 |
| medium_seed201 | 510.881473 | True | 1.847051 | 1.0 |
| medium_seed202 | 528.144907 | True | 5.545684 | 1.069 |
| medium_seed203 | 578.61344 | True | 5.502731 | 1.069 |
| high_noise_seed601 | 645.344137 | True | 4.926876 | 1.0 |
| large_seed301 | 669.367661 | True | 4.789679 | 1.0513 |
| large_seed301 | 655.220344 | True | 5.127626 | 1.1053 |
| large_seed302 | 669.108436 | True | 6.925791 | 1.1053 |
| scarce_couriers_seed401 | 994.832571 | True | 1.353334 | 0.1389 |
| low_willingness_seed501 | 2264.323219 | True | 9.266434 | 3.0 |
