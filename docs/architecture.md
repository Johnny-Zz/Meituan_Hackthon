# Unified Architecture

`MeituanRSD_unified` keeps the second project as the user-facing shell and imports the first project as the offline algorithm core.

## Studio Layer

The studio layer comes from `MeituanRSD_autosolver`:

- `app.py` serves the dashboard and API.
- `dashboard/` renders cockpit, agent office, logs, rollback, audit, and score feedback views.
- `tools/` contains the safe local automation loop: backup, local testing, autonomous config-only patching, final-day training, score feedback, and case generation.
- `submission/solver.py` is the only competition-facing `solve(input_text)` implementation.

Runtime state is intentionally isolated under `memory/studio/` and `logs/studio/`.

## Algorithm Core Layer

The algorithm layer comes from `Meituan-Hackathon-main/meituan_agent_build`:

- `autosolver_core/core_solver.py` is the deterministic teacher solver.
- `autosolver_core/agent/` contains feature extraction, strategy registry, meta controller, memory, reward, and failure analysis.
- `autosolver_core/training/` contains experiment collection, selector training, parameter tuning, distillation, and validation scripts.
- `autosolver_core/models/strategy_selector.json` is the learned policy table used by the core agent.

Training memory is isolated under `memory/training/`; large data lives under `datasets_archive/`.

## Evaluation Policy

Use `local_test.py` as the canonical evaluator for the unified project because it supports the multi-courier race expected-cost output used by `submission/solver.py`.

The original `autosolver_core/evaluate.py` is kept for historical single-courier/core experiments, but it should not be used to compare final studio scores.

