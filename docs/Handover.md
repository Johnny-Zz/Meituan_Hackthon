# Handover.md

用于交接 MeituanRSD_autosolver 当前 champion、candidate、风险分支、训练轮次、备份点与下一轮建议。

## 当前状态

- 项目名称：MeituanRSD_autosolver
- 当前 champion：716.74（仅作为 UI 内置参考值；真实成绩以本地/官方反馈为准）
- 保护场景：small_seed100、tiny_seed42、scarce_couriers_seed401
- 一键训练：已配置为训练前自动备份，随后写入 `memory/trials.jsonl`、`logs/training_rounds.jsonl`、`memory/agent_logs.jsonl`、`docs/Notes.md`、`docs/Handover.md`
- 回滚：在“回滚”模块选择备份版本，二次确认输入 `ROLLBACK` 后恢复

## 下一轮建议

1. V6 anchor-preserving：任何新分支不能丢掉 V4-A 已有收益。
2. high_noise：主拓扑多轮不动，下一步只改 backup list / backup order。
3. medium202：固定 champion 解后做小范围 output-level swap。
4. large302：暂缓，等待 hard lock 与 anchor fallback 框架稳定。


## Handover · Round 1 · 2026-06-06 05:11:04

- 项目：MeituanRSD_autosolver
- 本轮改动：initial validation round after implementing 10 requested UI/training/rollback changes
- 一键训练模式：dry_run
- 备份：backups/pre_train_r1_20260606_051104.zip
- 当前建议：继续保持 V6 anchor-preserving；high_noise 只改 backup_order，medium202 只做 output-level swap，large302 暂缓。
- 风险提醒：如果 protected case 触发退化，直接在“回滚”页选择上一轮 pre_train 备份并输入 ROLLBACK。


## Handover · Round 2 · 2026-06-06 05:30:55

- 项目：MeituanRSD_autosolver
- 本轮改动：integrate local_test, large_seed301 and original single-agent solver
- 一键训练模式：local_test
- 备份：backups/pre_train_r2_20260606_053044.zip
- 当前建议：继续保持 V6 anchor-preserving；high_noise 只改 backup_order，medium202 只做 output-level swap，large302 暂缓。
- 风险提醒：如果 protected case 触发退化，直接在“回滚”页选择上一轮 pre_train 备份并输入 ROLLBACK。

## Handover · Autonomous Patch Round 3 · 2026-06-06 05:51:55
- 本轮目标：test autonomous patch integration
- 结论：已接受 patch
- 决策原因：patch passed static audit, compile audit, local benchmark, and no-regression gate
- Solver hash：before=208ece63c2f6ed77 after=4c0da7e3835c221e
- 下一步：如需接入线上 judge，先在官方平台手动提交当前 `submission/solver.py`，把反馈截图或分数填回 DataLab。

## Handover · Autonomous Patch Round 4 · 2026-06-06 05:52:29
- 本轮目标：test one click train with autonomous patch
- 结论：已接受 patch
- 决策原因：patch passed static audit, compile audit, local benchmark, and no-regression gate
- Solver hash：before=4c0da7e3835c221e after=936cb41e2dd0cf09
- 下一步：如需接入线上 judge，先在官方平台手动提交当前 `submission/solver.py`，把反馈截图或分数填回 DataLab。

## Handover · Autonomous Patch Round 5 · 2026-06-06 05:53:47
- 本轮目标：test one click train after reuse patch benchmark
- 结论：已接受 patch
- 决策原因：patch passed static audit, compile audit, local benchmark, and no-regression gate
- Solver hash：before=936cb41e2dd0cf09 after=6a4659ff50df8479
- 下一步：如需接入线上 judge，先在官方平台手动提交当前 `submission/solver.py`，把反馈截图或分数填回 DataLab。


## Handover · Round 5 · 2026-06-06 05:53:47

- 项目：MeituanRSD_autosolver
- 本轮改动：test one click train after reuse patch benchmark
- 一键训练模式：local_test
- 备份：backups/pre_train_r5_20260606_055322.zip
- 当前建议：继续保持 V6 anchor-preserving；high_noise 只改 backup_order，medium202 只做 output-level swap，large302 暂缓。
- 风险提醒：如果 protected case 触发退化，直接在“回滚”页选择上一轮 pre_train 备份并输入 ROLLBACK。


## Handover · 线上截图反馈 2026-06-06 06:07:07

本轮官方反馈已被 Score Feedback Agent 接收。平均分 714.115，与上一轮相比 Δ=-2.625。

下一轮优先级：
- high_noise_seed601: stalled，下一轮优先做场景专属轻量校准。
- medium_seed202: stalled，下一轮优先做场景专属轻量校准。

保护规则：small_seed100、tiny_seed42、scarce_couriers_seed401 仍保持 hard lock；任何自动 patch 必须先通过 no-regression gate。


## Handover · 线上截图反馈 2026-06-06 06:08:20

本轮官方反馈已被 Score Feedback Agent 接收。平均分 714.115，与上一轮相比 Δ=0.0。

下一轮优先级：
- high_noise_seed601: stalled，下一轮优先做场景专属轻量校准。
- medium_seed202: stalled，下一轮优先做场景专属轻量校准。
- large_seed302: stalled，下一轮优先做场景专属轻量校准。
- low_willingness_seed501: stalled，下一轮优先做场景专属轻量校准。
- large_seed301: stalled，下一轮优先做场景专属轻量校准。

保护规则：small_seed100、tiny_seed42、scarce_couriers_seed401 仍保持 hard lock；任何自动 patch 必须先通过 no-regression gate。


## Handover · 线上截图反馈 2026-06-06 06:08:59

本轮官方反馈已被 Score Feedback Agent 接收。平均分 714.115，与上一轮相比 Δ=-0.035。

下一轮优先级：
- high_noise_seed601: stalled，下一轮优先做场景专属轻量校准。
- medium_seed202: stalled，下一轮优先做场景专属轻量校准。
- large_seed302: stalled，下一轮优先做场景专属轻量校准。
- low_willingness_seed501: stalled，下一轮优先做场景专属轻量校准。
- large_seed301: stalled，下一轮优先做场景专属轻量校准。

保护规则：small_seed100、tiny_seed42、scarce_couriers_seed401 仍保持 hard lock；任何自动 patch 必须先通过 no-regression gate。


## Handover · 线上截图反馈 2026-06-06 06:09:21

本轮官方反馈已被 Score Feedback Agent 接收。平均分 714.115，与上一轮相比 Δ=-0.035。

下一轮优先级：
- high_noise_seed601: stalled，下一轮优先做场景专属轻量校准。
- medium_seed202: stalled，下一轮优先做场景专属轻量校准。

保护规则：small_seed100、tiny_seed42、scarce_couriers_seed401 仍保持 hard lock；任何自动 patch 必须先通过 no-regression gate。


## Handover · 线上截图反馈 2026-06-06 16:37:27

本轮官方反馈已被 Score Feedback Agent 接收。平均分 1140.73，与上一轮相比 Δ=426.615。

下一轮优先级：
- 立即恢复保护门禁：small/tiny/scarce 出现退化时禁用本轮 patch。
- low_willingness_seed501: regressed，下一轮优先做场景专属轻量校准。

保护规则：small_seed100、tiny_seed42、scarce_couriers_seed401 仍保持 hard lock；任何自动 patch 必须先通过 no-regression gate。


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

## Handover · Autonomous Patch Round 1 · 2026-06-06 18:06:07
- 本轮目标：Small/Tiny check tiny_small_backup_polish
- 结论：已拒绝/回滚 patch
- 决策原因：all patch attempts failed or regressed; solver.py restored to pre-patch content
- Solver hash：before=028bd2e5c230c5e5 after=028bd2e5c230c5e5
- 下一步：如需接入线上 judge，先在官方平台手动提交当前 `submission/solver.py`，把反馈截图或分数填回 DataLab。

## Handover · Autonomous Patch Round 2 · 2026-06-07 15:14:04
- 本轮目标：保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。
- 结论：已拒绝/回滚 patch
- 决策原因：all patch attempts failed or regressed; solver.py restored to pre-patch content
- Solver hash：before=028bd2e5c230c5e5 after=028bd2e5c230c5e5
- 下一步：如需接入线上 judge，先在官方平台手动提交当前 `submission/solver.py`，把反馈截图或分数填回 DataLab。

## Handover · Autonomous Patch Round 2 · 2026-06-07 15:14:12
- 本轮目标：保持 champion anchor，优先压缩耗时和 backup order，不动 protected case。
- 结论：已拒绝/回滚 patch
- 决策原因：all patch attempts failed or regressed; solver.py restored to pre-patch content
- Solver hash：before=028bd2e5c230c5e5 after=028bd2e5c230c5e5
- 下一步：如需接入线上 judge，先在官方平台手动提交当前 `submission/solver.py`，把反馈截图或分数填回 DataLab。


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
