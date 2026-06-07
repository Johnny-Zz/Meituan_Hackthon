"""Patch solver.py v2: ILP for small cases, SA CONFIG search, full time budget."""
import sys

with open('solver.py', 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# =====================================================================
# 1. Scenario-aware willingness discount
# =====================================================================
old = 'return c.score * c.willingness + penalty * c.task_count * (1.0 - c.willingness)'
new = '''base = c.score * c.willingness + penalty * c.task_count * (1.0 - c.willingness)
    ct = CONFIG.get("_runtime_case_type", "normal")
    if ct in ("low_willingness", "scarce_couriers"):
        return base - penalty * c.task_count * c.willingness * (1.0 - c.willingness) * 0.15
    return base'''
if old in content:
    content = content.replace(old, new)
    changes += 1
    print("  [1] scenario-aware willingness discount")

# =====================================================================
# 2. Insert ILP exact solver + SA + random search before format_solution
# =====================================================================
new_functions = '''

# =====================================================================
# Priority 1: ILP exact solver for small cases (tasks <= 20)
# =====================================================================

def _ilp_exact_solve(candidates, task_to_idx, courier_to_idx, deadline_ms):
    """Solve small instances exactly via scipy MILP.

    Variables: x_i in {0,1} for each candidate i
    Objective: minimize sum(x_i * penalty_cost_i)
    Constraints:
      - Each task covered at most once
      - Each courier used at most once
    """
    try:
        from scipy.optimize import milp, LinearConstraint, Bounds
        from scipy.sparse import lil_matrix
    except ImportError:
        return None

    n_cands = len(candidates)
    n_tasks = len(task_to_idx)
    n_couriers = len(courier_to_idx)
    if n_cands == 0 or n_tasks == 0:
        return None

    penalty = float(CONFIG.get("acceptance_penalty", 100.0))
    # Objective: minimize sum of penalty costs
    costs = [c.score * c.willingness + penalty * c.task_count * (1.0 - c.willingness)
             for c in candidates]

    # Build constraint matrix: A @ x <= 1
    # Rows: n_tasks (task coverage) + n_couriers (courier usage)
    n_rows = n_tasks + n_couriers
    A = lil_matrix((n_rows, n_cands))
    for i, c in enumerate(candidates):
        for task_name in c.task_ids:
            if task_name in task_to_idx:
                A[task_to_idx[task_name], i] = 1
        courier_row = n_tasks + courier_to_idx.get(c.courier_id, 0)
        A[courier_row, i] = 1

    constraints = LinearConstraint(A.tocsr(), ub=[1.0] * n_rows)
    bounds = Bounds(lb=0, ub=1)
    integrality = [1] * n_cands

    timeout_s = min(8.0, (deadline_ms - _now_ms()) / 1000.0)
    if timeout_s < 0.1:
        return None

    try:
        result = milp(
            costs,
            constraints=constraints,
            bounds=bounds,
            integrality=integrality,
            options={"time_limit": timeout_s, "disp": False},
        )
        if result.success or result.status == 9:  # 9 = time limit, may still have good solution
            selected = [candidates[i] for i in range(n_cands) if result.x[i] > 0.5]
            # Verify coverage
            covered = set()
            for c in selected:
                covered.update(c.task_ids)
            if len(covered) == n_tasks:
                return selected
    except Exception:
        pass
    return None


# =====================================================================
# Priority 2: SA CONFIG space search
# =====================================================================

def _sa_config_search(candidates, task_to_idx, courier_to_idx, base_config, n_iterations, deadline_ms):
    """Simulated annealing in CONFIG space.

    For each CONFIG, run core_solver.solve() and keep the best.
    """
    import copy
    import random as _random
    import math as _math

    rng = _random.Random(42)
    best_config = copy.deepcopy(base_config)
    best_penalty = _eval_config_quick(candidates, task_to_idx, courier_to_idx, best_config, deadline_ms)
    if best_penalty is None:
        return None

    current_config = best_config
    current_penalty = best_penalty
    T = best_penalty * 0.03  # initial temperature
    T_min = 0.5

    alpha = _math.exp(_math.log(T_min / max(T, 0.01)) / max(1, n_iterations))

    for it in range(n_iterations):
        if not _has_time(deadline_ms, 500.0):
            break
        # Perturb config
        new_config = _perturb_config(current_config, rng)
        new_penalty = _eval_config_quick(candidates, task_to_idx, courier_to_idx, new_config, deadline_ms)
        if new_penalty is None:
            T *= alpha
            continue

        delta = new_penalty - current_penalty
        if delta < 0 or rng.random() < _math.exp(-delta / max(T, 0.01)):
            current_config = new_config
            current_penalty = new_penalty
            if current_penalty < best_penalty - 1e-6:
                best_config = copy.deepcopy(current_config)
                best_penalty = current_penalty

        T *= alpha

    return best_config


def _perturb_config(config, rng):
    """Randomly perturb a few CONFIG parameters."""
    import copy
    c = copy.deepcopy(config)

    # Perturb strategies
    strats = list(c.get("strategies", []))
    if strats and rng.random() < 0.6:
        idx = rng.randrange(len(strats))
        s = list(strats[idx])
        for j in range(6):
            if rng.random() < 0.5:
                s[j] = max(0.0, s[j] + rng.gauss(0, 0.06))
        strats[idx] = tuple(round(v, 4) if isinstance(v, float) else v for v in s)
        c["strategies"] = strats

    # Perturb search params
    if rng.random() < 0.4:
        c["local_search_budget_ms"] = max(500, min(6000,
            c.get("local_search_budget_ms", 2800) + rng.randint(-800, 800)))
    if rng.random() < 0.3:
        c["max_generated_strategies"] = max(8, min(32,
            c.get("max_generated_strategies", 16) + rng.choice([-4, -2, 0, 2, 4])))
    if rng.random() < 0.3:
        c["pair_top_k"] = max(12, min(48,
            c.get("pair_top_k", 28) + rng.choice([-4, 0, 4])))
    if rng.random() < 0.2:
        c["triple_top_k"] = max(8, min(32,
            c.get("triple_top_k", 20) + rng.choice([-4, 0, 4])))

    return c


def _eval_config_quick(candidates, task_to_idx, courier_to_idx, config, deadline_ms):
    """Quickly evaluate a CONFIG by running greedy + limited local search."""
    import copy
    saved = {k: CONFIG.get(k) for k in config}
    CONFIG.update(config)
    try:
        if not _has_time(deadline_ms, 300.0):
            return None
        ctx = build_context(candidates, task_to_idx, courier_to_idx)
        total_tasks = _count_bits(ctx.all_task_mask)
        penalty_val = float(CONFIG.get("acceptance_penalty", 100.0))

        # Quick greedy with top 3 base orders
        best_sel = None
        best_p = float('inf')
        for name, ordered in base_orders(ctx)[:5]:
            sel = greedy_select(ordered)
            p = sum(c.score * c.willingness + penalty_val * c.task_count * (1.0 - c.willingness) for c in sel)
            covered = sum(_count_bits(c.task_mask) for c in sel)
            if covered == total_tasks and p < best_p:
                best_p = p
                best_sel = sel

        if best_sel is None:
            return None
        return best_p
    except Exception:
        return None
    finally:
        for k, v in saved.items():
            if v is None:
                CONFIG.pop(k, None)
            else:
                CONFIG[k] = v


# =====================================================================
# Priority 3: Aggressive random restart search
# =====================================================================

def _random_restart_search(candidates, task_to_idx, base_selected, n_attempts, deadline_ms):
    """Random shuffle + greedy, keep best. Use full time budget."""
    import random as _random
    best = list(base_selected)
    rng = _random.Random(123)
    penalty = float(CONFIG.get("acceptance_penalty", 100.0))
    n_needed = len(task_to_idx)

    # Compute base penalty
    best_p = sum(c.score * c.willingness + penalty * c.task_count * (1.0 - c.willingness)
                 for c in best) if best else float('inf')

    all_cands = list(candidates)
    for attempt in range(n_attempts):
        if not _has_time(deadline_ms, 80.0):
            break
        # Random shuffle with varying amplitude
        amplitude = rng.uniform(3.0, 50.0)
        noise = [(c, rng.gauss(0, amplitude)) for c in all_cands]
        noise.sort(key=lambda x: (
            x[0].score * x[0].willingness + penalty * x[0].task_count * (1.0 - x[0].willingness) + x[1],
            x[0].score,
        ))
        sel, used_t, used_c = [], 0, 0
        for c, _ in noise:
            if c.task_mask & used_t or c.courier_bit & used_c:
                continue
            sel.append(c)
            used_t |= c.task_mask
            used_c |= c.courier_bit
            if _count_bits(used_t) == n_needed:
                break
        if _count_bits(used_t) < n_needed:
            continue
        p = sum(c.score * c.willingness + penalty * c.task_count * (1.0 - c.willingness) for c in sel)
        if p < best_p - 1e-9:
            best_p = p
            best = sel
    return best

'''

# Insert before format_solution
marker = 'def format_solution('
pos = content.find(marker)
if pos < 0:
    print("ERROR: format_solution not found")
    sys.exit(1)
content = content[:pos] + new_functions + content[pos:]
changes += 1
print("  [2] inserted ILP + SA + random search functions")

# =====================================================================
# 3. Modify solve() to use ILP for small cases and SA for medium/large
# =====================================================================
old_solve = '''        deadline_ms = _now_ms() + CONFIG["time_budget_ms"] - CONFIG["safety_margin_ms"]
        primary_deadline_ms = deadline_ms
        primary_budget_ms = float(CONFIG.get("multi_primary_time_budget_ms", 0.0))
        if primary_budget_ms > 0.0:
            primary_deadline_ms = min(deadline_ms, _now_ms() + primary_budget_ms)
        selected = choose_solution(candidates, task_to_idx, courier_to_idx, primary_deadline_ms)
        if not CONFIG.get("enable_multi_courier_output", False):'''

new_solve = '''        deadline_ms = _now_ms() + CONFIG["time_budget_ms"] - CONFIG["safety_margin_ms"]
        primary_deadline_ms = deadline_ms
        primary_budget_ms = float(CONFIG.get("multi_primary_time_budget_ms", 0.0))
        if primary_budget_ms > 0.0:
            primary_deadline_ms = min(deadline_ms, _now_ms() + primary_budget_ms)

        n_tasks = len(task_to_idx)
        selected = None

        # Priority 1: ILP exact solve for small cases (tasks <= 20)
        if n_tasks <= 20:
            selected = _ilp_exact_solve(candidates, task_to_idx, courier_to_idx, deadline_ms)

        # Fallback: greedy search
        if selected is None:
            selected = choose_solution(candidates, task_to_idx, courier_to_idx, primary_deadline_ms)

        # Priority 2: SA CONFIG search for medium/large (use remaining time)
        if n_tasks > 20 and _has_time(deadline_ms, 2000.0):
            remaining_ms = deadline_ms - _now_ms()
            n_sa = max(5, min(30, int(remaining_ms / 500)))
            sa_config = _sa_config_search(candidates, task_to_idx, courier_to_idx, CONFIG, n_sa, deadline_ms)
            if sa_config is not None:
                # Re-run with best SA config
                saved = {k: CONFIG.get(k) for k in sa_config}
                CONFIG.update(sa_config)
                try:
                    sa_selected = choose_solution(candidates, task_to_idx, courier_to_idx, deadline_ms)
                    if sa_selected:
                        penalty_val = float(CONFIG.get("acceptance_penalty", 100.0))
                        sa_p = sum(c.score * c.willingness + penalty_val * c.task_count * (1.0 - c.willingness) for c in sa_selected)
                        cur_p = sum(c.score * c.willingness + penalty_val * c.task_count * (1.0 - c.willingness) for c in selected)
                        if sa_p < cur_p - 1e-6:
                            selected = sa_selected
                finally:
                    for k, v in saved.items():
                        if v is None: CONFIG.pop(k, None)
                        else: CONFIG[k] = v

        # Priority 3: Random restart search with remaining time
        if _has_time(deadline_ms, 1000.0):
            n_restarts = max(20, min(500, 30000 // max(1, n_tasks)))
            improved = _random_restart_search(candidates, task_to_idx, selected, n_restarts, deadline_ms)
            if improved:
                selected = improved

        if not CONFIG.get("enable_multi_courier_output", False):'''

if old_solve in content:
    content = content.replace(old_solve, new_solve)
    changes += 1
    print("  [3] modified solve() with ILP/SA/random restart")
else:
    print("  ERROR: solve() pattern not found")
    sys.exit(1)

# Write
with open('solver.py', 'w', encoding='utf-8') as f:
    f.write(content)
print(f"  Total changes: {changes}, file: {len(content.splitlines())} lines")
