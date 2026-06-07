# MeituanRSD_autosolver V2：可视化 Multi-Agent 递归策略蒸馏平台方案

> 目标：把 AutoSolver 从“单 Agent + 大 solver.py”升级为“离线 Multi-Agent 策略蒸馏工作室 + 小型静态 solver.py”。  
> 核心原则：**离线复杂、线上极简；用户最高权限；所有 candidate 必须经过 no-regression；最终 solver.py 不嵌入 LLM、不联网、不超过 100KB。**

---

## 1. 设计灵感

本平台借鉴两类界面：

1. **SciClaw 风格**：深色科研工作台、左侧项目导航、中央任务流、底部对话/技能入口。
2. **Marvis 风格**：本地办公桌视角，每个 Agent 像“办公室里的专家”一样拥有具体席位、身份、状态和职责。

Marvis 官方页面强调“本地模式文件 0 上传”、端云协同与本地模式切换、文件智能整理搜索、电脑设置和文档处理能力。这对 AutoSolver 很有启发：训练平台要尽量本地化、可审计、可接管，关键动作必须用户确认。

---

## 2. 为什么不是纯 Multi-Agent，也不是单 Agent

### 单 Agent 的问题

单 Agent 很容易出现：

```text
训练、归因、改代码、提交、回滚全部混在一起
好版本被坏版本覆盖
solver.py 越来越大
错误分支无法定位
```

### 散养式 Multi-Agent 的问题

每个 Agent 都自由发挥会导致：

```text
策略污染
日志过多
决策链混乱
多个 Agent 同时改 solver.py
```

### 最优选择：Leader-Centric Multi-Agent

```text
Leader Agent 统一调度
各专家 Agent 只负责自己的阶段
Auditor Agent 拥有否决权
用户拥有最高权限
所有 Agent 通过 blackboard 共享结构化状态
```

---

## 3. Agent 角色设计

| Agent | 形态 | 职责 |
|---|---|---|
| Leader Agent | 红色核心指挥官 | 统筹训练轮次、决定下一步动作 |
| Data Seed Agent | 绿色种子培育师 | 生成 mid-training 数据、hard cases |
| Strategy Agent | 紫色策略师 | 维护策略池、提出场景策略 |
| Trainer Agent | 蓝色训练工程师 | 执行本地训练、记录运行状态 |
| HyperParam Agent | 橙色调参师 | 调整 budget、topK、backup 阈值 |
| Evaluator Agent | 青色评测员 | 计算覆盖、成本、耗时、可靠性 |
| LLM Reflector | 粉色研究员 | 和 DeepSeek 对话，做错误归因 |
| Auditor Agent | 银色审核员 | 检查 solver.py 大小、依赖、风险分支 |
| Distiller Agent | 青绿色蒸馏师 | 把有效策略压缩成 compact config |
| Submit Agent | 黄色提交员 | 准备提交包，但必须用户确认 |

---

## 4. Harness 思想的接入

平台把 Plan-and-Execute 与 ReAct 结合：

```text
Leader Agent：先 Plan，制定本轮目标
各专家 Agent：执行时 ReAct，观察 → 行动 → 记录 → 修正
Harness：在每一步加清洗、校正、过滤、重试、回滚
```

### Harness 护栏

```text
输入清洗：检查数据格式、任务覆盖、willingness 合法性
输出过滤：LLM 只能输出 DSL，不能直接写 solver.py
错误重试：失败策略可降级重试，不能中断整轮训练
no-regression：protected cases 退化立刻回滚
代码审计：检测 solver.py 大小、危险依赖、复杂度
用户确认：线上提交、promote、rollback 必须可视化确认
```

---

## 5. 场景训练逻辑

```text
low_willingness：骑手不愿接，要提高可靠性
scarce_couriers：骑手少，要节省骑手
high_noise：分数噪声大，要避免贪心陷阱
medium：局部组合优化
large：规模大，不能全局暴力搜
small/tiny：应该接近精确最优，但必须冻结保护
```

LangGraph 路径：

```text
开始
 ↓
读取输入，判断场景
 ↓
tiny/small：精确搜索或稳定基线
 ↓
scarce：合单优先 + 保守局部搜索
 ↓
low_willingness：备选骑手策略
 ↓
medium/large/high_noise：贪心 + 局部搜索 + LNS
 ↓
检查时间
 ↓
还有2秒：继续 polish
快超时：返回当前最优解
```

---

## 6. 可视化控制台模块

V2 HTML 控制台包含：

1. **Cockpit 总览**：当前 champion、candidate、solver 大小、no-regression 状态。
2. **Agent Office**：Marvis 式办公室，每个 Agent 有席位、状态、能量和任务。
3. **Training Flow**：LangGraph 状态机可视化。
4. **Data Lab**：本轮数据种子生成策略、场景分布、样本质量。
5. **Score Board**：case 级别分数差异、protected case 红线。
6. **Forensics**：错误归因、成功归因、下一轮建议。
7. **Code Auditor**：solver.py 大小、危险关键词、函数数量、diff 风险。
8. **LLM Dialogue**：用户和 DeepSeek 对话，反馈给 Leader Agent。
9. **Control Center**：用户拥有最高权限，可暂停、回滚、冻结场景、禁止策略、导出 solver。

---

## 7. DeepSeek 接入原则

不要把 API Key 写进代码和 zip 包。使用环境变量：

```powershell
$env:DEEPSEEK_API_KEY="你的新 key"
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com"
$env:DEEPSEEK_MODEL="deepseek-v4-pro"
```

> 注意：如果 key 曾经发在聊天或截图中，建议立即去平台后台停用旧 key 并重新生成。

---

## 8. 最终目标

```text
离线 Multi-Agent 越来越强
线上 solver.py 越来越小
每轮变动可解释
每个 candidate 可回滚
每个 Agent 可审计
用户随时可介入
```

最终导出的 solver.py 应该：

```text
小于 100KB
无 LLM
无网络
无数据库
无复杂训练逻辑
只包含 compact config + 少量核心策略
