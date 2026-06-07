"""Patch solver.py to add bitmask DP and random search for small cases."""
import sys

with open('solver.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add scenario-aware willingness discount
old_penalty = 'return c.score * c.willingness + penalty * c.task_count * (1.0 - c.willingness)'
new_penalty = '''base = c.score * c.willingness + penalty * c.task_count * (1.0 - c.willingness)
    case_type = CONFIG.get("_runtime_case_type", "normal")
    if case_type in ("low_willingness", "scarce_couriers"):
        return base - penalty * c.task_count * c.willingness * (1.0 - c.willingness) * 0.15
    return base'''

if old_penalty in content:
    content = content.replace(old_penalty, new_penalty)
    print("  [OK] Applied scenario-aware willingness discount")
else:
    print("  [WARN] penalty pattern not found")

# 2. Insert bitmask DP function before format_solution
dp_code = '''

def _bitmask_dp_solve(candidates, task_to_idx, courier_to_idx, deadline_ms):
    """Optimal assignment via bitmask DP for small task sets (n <= 20).

    dp[mask] = best non-conflicting candidate set covering exactly `mask` tasks.
    Complexity: O(3^n) worst case via submask enumeration.
    """
    n_tasks = len(task_to_idx)
    if n_tasks == 0 or n_tasks > 20:
        return None

    task_names = sorted(task_to_idx.keys())
    task_bit = {name: i for i, name in enumerate(task_names)}
    all_mask = (1 << n_tasks) - 1
    penalty = float(CONFIG.get("acceptance_penalty", 100.0))

    # Remap candidates to contiguous bit positions
    from collections import defaultdict
    by_mask = defaultdict(list)
    for c in candidates:
        new_mask = 0
        for tname in c.task_ids:
            if tname in task_bit:
                new_mask |= (1 << task_bit[tname])
        if new_mask == 0:
            continue
        cost = c.score * c.willingness + penalty * c.task_count * (1.0 - c.willingness)
        by_mask[new_mask].append((c.courier_bit, cost, c))

    for m in by_mask:
        by_mask[m].sort(key=lambda x: x[1])

    INF = float('inf')
    dp_cost = [INF] * (1 << n_tasks)
    dp_courier = [0] * (1 << n_tasks)
    dp_solution = [None] * (1 << n_tasks)
    dp_cost[0] = 0.0
    dp_courier[0] = 0
    dp_solution[0] = []

    for mask in range(1, 1 << n_tasks):
        if not _has_time(deadline_ms, 50.0):
            return None
        sub = mask
        while sub:
            for courier_bits, cost, c in by_mask.get(sub, []):
                prev = mask ^ sub
                if dp_cost[prev] >= INF:
                    continue
                if courier_bits & dp_courier[prev]:
                    continue
                new_cost = dp_cost[prev] + cost
                if new_cost < dp_cost[mask] - 1e-9:
                    dp_cost[mask] = new_cost
                    dp_courier[mask] = dp_courier[prev] | courier_bits
                    dp_solution[mask] = dp_solution[prev] + [c]
            sub = (sub - 1) & mask

    if dp_cost[all_mask] >= INF:
        return None
    return dp_solution[all_mask]


def _random_perturb_search(candidates, task_to_idx, base_selected, base_penalty, n_attempts, deadline_ms):
    """Random perturbation: shuffle + greedy, keep best."""
    import random as _random
    best = list(base_selected)
    best_p = base_penalty
    rng = _random.Random(42)
    penalty = float(CONFIG.get("acceptance_penalty", 100.0))
    all_cands = list(candidates)
    n_needed = len(task_to_idx)

    for _ in range(n_attempts):
        if not _has_time(deadline_ms, 80.0):
            break
        shuffled = [(c, rng.random()) for c in all_cands]
        amplitude = rng.uniform(5.0, 40.0)
        shuffled.sort(key=lambda x: (
            x[0].score * x[0].willingness + penalty * x[0].task_count * (1.0 - x[0].willingness)
            + (x[1] - 0.5) * amplitude,
            x[0].score,
        ))
        sel, used_t, used_c = [], 0, 0
        for c, _ in shuffled:
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

format_marker = 'def format_solution(selected, backup_map=None):'
if format_marker in content:
    pos = content.index(format_marker)
    content = content[:pos] + dp_code + content[pos:]
    print("  [OK] Inserted DP and random search functions")
else:
    print("  [ERROR] format_solution not found")
    sys.exit(1)

# 3. Modify solve() to call DP for small cases
old_solve_line = '        selected = choose_solution(candidates, task_to_idx, courier_to_idx, primary_deadline_ms)'
new_solve_block = '''        # Bitmask DP for small task sets
        total_tasks = len(task_to_idx)
        selected = None
        if total_tasks <= 16:
            selected = _bitmask_dp_solve(candidates, task_to_idx, courier_to_idx, deadline_ms)
        if selected is None:
            selected = choose_solution(candidates, task_to_idx, courier_to_idx, primary_deadline_ms)

        # Random perturbation search with remaining time
        if _has_time(deadline_ms, 1500.0):
            penalty_val = float(CONFIG.get("acceptance_penalty", 100.0))
            base_p = sum(c.score * c.willingness + penalty_val * c.task_count * (1.0 - c.willingness) for c in selected)
            n_attempts = max(50, min(500, 20000 // max(1, total_tasks)))
            improved = _random_perturb_search(candidates, task_to_idx, selected, base_p, n_attempts, deadline_ms)
            if improved:
                selected = improved'''

if old_solve_line in content:
    content = content.replace(old_solve_line, new_solve_block)
    print("  [OK] Modified solve() to use DP + random search")
else:
    print("  [ERROR] solve() line not found")
    sys.exit(1)

with open('solver.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"  solver.py: {len(content.splitlines())} lines")
