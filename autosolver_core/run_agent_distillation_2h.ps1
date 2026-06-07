$ErrorActionPreference = "Stop"

$env:MEITUAN_ENABLE_MEMORY = "0"
$env:MEITUAN_AGENT_EXPLORE = "0"
$env:MEITUAN_AGENT_MAX_ATTEMPTS = "3"
$env:MEITUAN_AGENT_BUDGET_MS = "9200"

python training/run_agent_distillation_2h.py `
  --duration-seconds 7200 `
  --data-root "agent/meituan_1500_training_samples_by_scene/case_bank/train" `
  --memory "memory/experiments.sqlite" `
  --model-out "models/strategy_selector.json" `
  --outputs-out "models/agent_solver_outputs_1500.jsonl" `
  --outputs-limit 10 `
  --standalone-out "solver_submission_standalone.py" `
  --solver-out "solver.py"
