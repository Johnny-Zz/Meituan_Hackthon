# Meituan_Hackthon

美团配送调度赛题 AutoSolver 统一工程。这个目录把两套项目整合到一个可运行、可训练、可回滚、可提交的工作区中：

- `MeituanRSD_autosolver`：本地可视化工作台、提交 solver、评测器、回滚、线上分数反馈和安全 patch 流程。
- `Meituan-Hackathon-main/meituan_agent_build`：算法核心、Teacher 求解器、Agent 策略调度、离线训练和策略蒸馏资产。

整合后的原则是：**提交入口唯一、训练能力归档清晰、评测口径统一、运行状态和训练记忆分开存放**。

## 快速开始

进入项目根目录：

```bash
cd /Users/johnny/Desktop/大二/美团Agent大赛/Meituan_Hackthon
```

运行当前提交 solver 的本地评测：

```bash
python3 local_test.py submission/solver.py cases/large_seed301.txt --json
python3 local_test.py submission/solver.py generated_cases/tiny_seed42/tiny_seed42.txt --json
```

启动可视化控制台：

```bash
python3 app.py
```

浏览器访问：

```text
http://127.0.0.1:8765
```

常用 API 自检：

```bash
curl http://127.0.0.1:8765/api/state
curl http://127.0.0.1:8765/api/audit
curl http://127.0.0.1:8765/api/solver
```

## 当前验证基线

本整合目录已按 `local_test.py` 口径验证过当前 `submission/solver.py`：

| Case | 结果 | 覆盖 | total_score | 说明 |
|---|---:|---:|---:|---|
| `cases/large_seed301.txt` | valid | 40/40 | 669.367661 | 当前主样例 |
| `generated_cases/tiny_seed42/tiny_seed42.txt` | valid | 6/6 | 135.668972 | 小样本冒烟测试 |

`local_test.py` 是统一工程的主评测口径。它支持单骑手和多备选骑手输出，会使用 race expected cost 计算 `total_score`，分数越低越好。

## 关键入口

| 入口 | 作用 |
|---|---|
| `submission/solver.py` | 唯一比赛提交入口，必须提供 `solve(input_text: str) -> list` |
| `local_test.py` | 本地主评测器，检查合法性、覆盖率、重复任务/骑手、期望成本 |
| `app.py` | 本地可视化工作台 HTTP 服务 |
| `dashboard/` | Cockpit、Agent Office、训练日志、回滚、分数反馈等前端页面 |
| `tools/` | 备份、自动 patch、最终日训练、分数反馈、数据生成等自动化脚本 |
| `autosolver_core/core_solver.py` | 原算法核心 Teacher 求解器 |
| `autosolver_core/agent/` | 特征提取、策略注册、Meta Controller、记忆、reward、失败归因 |
| `autosolver_core/training/` | 实验采集、策略选择器训练、参数搜索、Teacher 验证 |

重要：不要用 `autosolver_core/solver.py` 或 `autosolver_core/solver_archive/*.py` 覆盖 `submission/solver.py`。前者是训练/研究资产，后者才是当前线上提交入口。

## 目录结构

```text
Meituan_Hackthon/
├── app.py                         # 本地工作台服务
├── dashboard/                     # 可视化控制台前端
├── submission/
│   └── solver.py                  # 唯一提交入口
├── local_test.py                  # 主评测器
├── autosolver_core/               # 算法核心和离线训练系统
│   ├── core_solver.py             # Teacher / baseline 求解器
│   ├── agent/                     # 多 Agent 决策模块
│   ├── training/                  # 离线训练和蒸馏脚本
│   ├── models/                    # 策略选择器和训练产物
│   └── solver_archive/            # 历史 solver 快照
├── cases/                         # 当前主评测 case
├── generated_cases/               # 工作台生成的多场景 case
├── datasets_archive/              # 大型训练数据和原始重复数据归档
├── tools/                         # 本地自动化工具链
├── config/                        # 训练、场景和反馈配置
├── memory/
│   ├── studio/                    # 工作台状态、patch、聊天、线上反馈
│   └── training/                  # 离线训练 SQLite 记忆库
├── logs/
│   ├── studio/                    # 工作台和最终日训练日志
│   └── training/                  # 蒸馏、自进化训练日志
├── backups/
│   └── legacy/                    # 历史 zip 备份和新备份
└── docs/                          # 交接、架构、训练核心和更新记录
```

当前目录共包含约 1776 个文件，其中 `datasets_archive/` 约 1600 个训练数据文件，`autosolver_core/solver_archive/` 保留 14 个历史 solver 快照。

## 输入输出格式

输入是制表符分隔文本，通常包含表头：

```text
task_id_list	courier_id	total_score	willingness
T0037,T0039	C028	52.016	0.582
T0012	C073	49.233	0.1485
```

`solve(input_text)` 返回列表：

```python
[
    ("T0005,T0018", ["C045"]),
    ("T0030", ["C031", "C052"]),
]
```

约束：

- 同一任务不能在多个分配项中重复出现。
- 同一骑手不能在多个分配项中重复出现。
- 每个返回项必须能在输入候选边中找到对应任务包和骑手。
- 如果一个任务包返回多个骑手，`local_test.py` 会按 race expected cost 评估。

## 可视化工作台

启动后，`app.py` 会在本地提供这些主要接口：

| 接口 | 说明 |
|---|---|
| `GET /api/state` | 返回完整工作台状态、日志摘要、训练配置、反馈信息 |
| `GET /api/audit` | 审计 `submission/solver.py` 大小、危险关键词、哈希和函数数量 |
| `GET /api/backups` | 列出备份 |
| `GET /api/logs` | 查看 Notes、Handover、训练、patch、聊天日志摘要 |
| `GET /api/patches` | 查看 patch 报告和最近 diff |
| `GET /api/solver` | 读取当前提交 solver 源码 |
| `POST /api/action` | 触发审计、备份、一键训练、自动 patch、回滚、生成 case 等动作 |
| `POST /api/feedback/upload` | 上传线上分数截图 |
| `POST /api/feedback/text` | 粘贴线上分数文本 |
| `POST /api/chat` | 与 DeepSeek Reflector 对话 |

工作台不会自动提交到官方平台。提交相关动作只做准备、记录和检查，最终提交仍由用户手动确认。

## 常用脚本

```bash
# 审计提交 solver
python3 tools/champion_guard.py backup --tag manual_snapshot --note "before experiment"

# 生成多场景训练样本
python3 tools/generate_midtrain_cases.py --base cases/large_seed301.txt --target all --seed 301

# 一键本地训练/评估/记录
python3 tools/final_day_trainer.py --rounds 1 --submission-budget 18

# 安全自动 patch：只允许白名单 CONFIG 改动，并经过 no-regression gate
python3 tools/autonomous_patch_agent.py --objective "保持 champion anchor，优化 large_seed301"

# 粘贴线上分数文本做反馈归因
python3 tools/score_feedback_agent.py --raw-text "..."
```

## 离线训练核心

进入算法核心目录：

```bash
cd autosolver_core
```

采集策略实验：

```bash
python3 training/collect_experiments.py ../datasets_archive/training_cases
```

训练策略选择器：

```bash
python3 training/train_selector.py
```

Teacher 对比验证：

```bash
python3 training/validate_against_teacher.py ../datasets_archive/training_cases --teacher single
```

训练默认写入：

```text
memory/training/experiments.sqlite
```

如需覆盖，可设置：

```bash
export MEITUAN_AGENT_MEMORY=/path/to/experiments.sqlite
```

## 环境变量

| 变量 | 作用 |
|---|---|
| `RSD_STUDIO_PORT` | 控制台端口，默认 `8765` |
| `DEEPSEEK_API_KEY` | DeepSeek LLM 归因和 patch 计划 |
| `DEEPSEEK_BASE_URL` | DeepSeek OpenAI-compatible endpoint |
| `DEEPSEEK_MODEL` | 默认 `DeepSeek-V4-pro` |
| `QWEN_API_KEY` | Qwen OCR，用于分数截图识别 |
| `QWEN_BASE_URL` | Qwen OpenAI-compatible endpoint |
| `QWEN_OCR_MODEL` | OCR 模型名 |
| `MEITUAN_AGENT_MEMORY` | 离线训练记忆库路径 |
| `MEITUAN_AGENT_MODEL` | 策略选择器 JSON 路径 |
| `MEITUAN_ALLOW_PARALLEL` | 是否允许 core agent 使用多骑手策略 |

配置示例在：

```text
config/.env.example
setup_deepseek_env.example.sh
setup_qwen_env.example.sh
```

## 文档索引

| 文档 | 内容 |
|---|---|
| `技术说明文档.md` | 当前统一工程的详细技术说明 |
| `docs/architecture.md` | 整合架构说明 |
| `docs/training_core.md` | 离线训练核心说明 |
| `docs/Handover.md` | 历史交接、训练轮次和线上反馈记录 |
| `docs/Notes.md` | 训练、归因、patch 和实验备注 |
| `docs/MeituanRSD_autosolver_ChangeLog.md` | 工作台历史更新记录 |
| `docs/MeituanRSD_autosolver_V2_Plan.md` | 可视化多 Agent 工作台设计方案 |

