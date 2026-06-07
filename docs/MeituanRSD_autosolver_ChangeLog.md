# MeituanRSD_autosolver ChangeLog

## V3 interactive-train-rollback

- 项目名称从 RSD-Marvis AutoSolver Studio 统一改为 MeituanRSD_autosolver。
- 新增一键训练配置与 `tools/one_key_train.py`。
- 一键训练前自动备份上一轮状态。
- 新增 Cockpit 原生 Canvas 可视化：柱状图、热力图、雷达图、折线图、地图式战况图。
- Agent Office 支持点击 Agent 展开本轮关键操作、数据、日志。
- Flow 支持点击节点展开代码内容与流程状态。
- DataLab 支持保存场景参数、生成 `seed_config_large_seed301.json`。
- 错误归因模块展示错误原因、错误代码与修正代码。
- 新增训练日志模块，聚合 Notes、Handover、Agent logs、LLM chat、trials。
- 用户配置台所有卡片可执行动作，并显示结果。
- 新增一键回滚模块，回滚前强制二次确认并自动创建 pre_restore 备份。

## V3.3 · Score Feedback Agent / Online Screenshot Loop

- 新增“分数反馈”模块：用户可上传官方线上提交截图，系统自动保存截图、OCR 分数、解析 10 个算例、覆盖率与耗时。
- 新增 `tools/score_feedback_agent.py`：将线上反馈与上一轮反馈/当前 Cockpit 状态做 delta 对比，生成 `memory/score_feedback_latest.json`、`memory/score_feedback_history.jsonl` 与 `config/next_round_plan.json`。
- Cockpit 图表升级为线上反馈驱动：柱状图显示当前分数与上一轮 marker，热力图显示改善/退化风险，雷达图反映 coverage/improve/protected/runtime，折线图显示线上平均分历史，地图显示 case 战况。
- 新增 `/api/feedback/upload` 与 `/api/feedback/text`：支持截图上传，也支持无 OCR 环境时粘贴文本。
- 反馈分析会写入 `Notes.md`、`Handover.md`、Agent 日志，并更新 Evaluator / Strategy / Reflector Agent 的本轮状态。
- 如配置 `DEEPSEEK_API_KEY`，Score Feedback Agent 会请求 DeepSeek 补充下一轮策略；未配置时使用本地 deterministic analysis，保证系统可离线跑通。
