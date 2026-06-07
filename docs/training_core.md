# Training Core Notes

The original algorithm core was copied into `autosolver_core/` and its large data was moved out of the code path:

- `datasets_archive/training_cases/`
- `datasets_archive/training_cases_evolution/`
- `datasets_archive/meituan_1500_training_samples_by_scene/`
- `datasets_archive/source_duplicates/`

Historical solver variants were moved to `autosolver_core/solver_archive/`.

## Recommended Commands

Run these from the unified project root unless noted otherwise.

```bash
python3 local_test.py submission/solver.py cases/large_seed301.txt --json
python3 local_test.py submission/solver.py generated_cases/tiny_seed42/tiny_seed42.txt --json
```

For deep offline training:

```bash
cd autosolver_core
python3 training/collect_experiments.py ../datasets_archive/training_cases
python3 training/train_selector.py
python3 training/validate_against_teacher.py ../datasets_archive/training_cases --teacher single
```

The default training memory path is now:

```text
memory/training/experiments.sqlite
```

Set `MEITUAN_AGENT_MEMORY` to override it.

