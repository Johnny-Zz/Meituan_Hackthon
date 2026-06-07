import itertools
import random
import time
EPS = 1e-09
_GROUP_COST_CACHE = {}
                                                                                                 
                                                                                                                   
                                                                                                 
                                                                                                                   
                                                                                                 
                                                                                                                   
CONFIG = {'time_budget_ms': 9500.0,
 'safety_margin_ms': 220.0,
 'auto_strategy_budget_ms': 300.0,
 'local_search_budget_ms': 2800.0,
 'race_topology_repair_budget_ms': 2600.0,
 'normal_preview_backup_cap': 2,
 'normal_preview_scan_per_primary': 18,
 'normal_topology_top_k': 6,
 'normal_topology_generated_limit': 10,
 'backup_time_budget_ms': 600.0,
 'backup_reallocation_budget_ms': 0.0,
 'multi_primary_time_budget_ms': 0.0,
 'enable_multi_courier_output': False,
 'acceptance_penalty': 100.0,
 'max_extra_couriers_per_bundle': 8,
 'min_backup_utility': 0.0,
 'min_remaining_ms': 45.0,
 'max_exact_replace_tasks': 8,
 'max_candidates_per_mask': 20,
 'special_max_candidates_per_mask': 4,
 'special_courier_ratio_threshold': 1.0,
 'pair_top_k': 28,
 'triple_top_k': 20,
 'try_triples': True,
 'multi_cost_mode': 'race',
 'strategies': [(0.0463, 0.915, 0.0814, 0.0619, 0.052, 0.3189, 0),
                (0.099, 0.9555, 0.0258, 0.1814, 0.0, 0.0747, 0),
                (0.051, 1.0402, 0.0407, 0.3392, 0.1406, 0.0082, 1),
                (0.0696, 0.9884, 0.0974, 0.0911, 0.0737, 0.1305, 0)],
 '_agent_patch_round': 5,
 '_agent_patch_time': '2026-06-06 05:53:35',
 '_agent_patch_origin': 'local_fallback'}

class Candidate:
    __slots__ = ('task_str', 'task_ids', 'task_mask', 'courier_id', 'courier_idx', 'courier_bit', 'score', 'willingness', 'task_count', 'score_per_task', 'min_task_degree', 'sum_task_degree', 'courier_degree')

    def __init__(self, task_str, task_ids, task_mask, courier_id, courier_idx, score, willingness):
        self.task_str = task_str
        self.task_ids = task_ids
        self.task_mask = task_mask
        self.courier_id = courier_id
        self.courier_idx = courier_idx
        self.courier_bit = 1 << courier_idx
        self.score = score
        self.willingness = willingness
        self.task_count = len(task_ids)
        self.score_per_task = score / max(1, self.task_count)
        self.min_task_degree = 0
        self.sum_task_degree = 0
        self.courier_degree = 0

class Context:
    __slots__ = ('candidates', 'task_to_idx', 'courier_to_idx', 'all_task_mask', 'task_degrees', 'courier_degrees', 'mask_to_candidates', 'max_score', 'max_score_per_task', 'max_task_degree', 'max_courier_degree')

    def __init__(self, candidates, task_to_idx, courier_to_idx):
        self.candidates = candidates
        self.task_to_idx = task_to_idx
        self.courier_to_idx = courier_to_idx
        self.all_task_mask = (1 << len(task_to_idx)) - 1
        self.task_degrees = {}
        self.courier_degrees = {}
        self.mask_to_candidates = {}
        self.max_score = 1.0
        self.max_score_per_task = 1.0
        self.max_task_degree = 1
        self.max_courier_degree = 1

class Eval:
    __slots__ = ('covered', 'score', 'penalty_score', 'conflicts', 'items')

    def __init__(self, covered, score, penalty_score, conflicts, items):
        self.covered = covered
        self.score = score
        self.penalty_score = penalty_score
        self.conflicts = conflicts
        self.items = items

def _now_ms():
    return time.perf_counter() * 1000.0

def _count_bits(value):
    return bin(value).count('1')

def _remaining(deadline_ms):
    return deadline_ms - _now_ms()

def _has_time(deadline_ms, min_ms=None):
    if min_ms is None:
        min_ms = CONFIG['min_remaining_ms']
    return _remaining(deadline_ms) > min_ms

def parse_input(input_text):
    candidates = []
    task_to_idx = {}
    courier_to_idx = {}
    if not input_text:
        return (candidates, task_to_idx, courier_to_idx)
    lines = input_text.strip().splitlines()
    if not lines:
        return (candidates, task_to_idx, courier_to_idx)
    start = 1 if lines[0].strip().startswith('task_id_list') else 0
    for line in lines[start:]:
        parts = line.strip().split('\t')
        if len(parts) < 4:
            continue
        task_str, courier_id, score_str, willingness_str = parts[:4]
        task_str = task_str.strip()
        courier_id = courier_id.strip()
        task_ids = tuple((t.strip() for t in task_str.split(',') if t.strip()))
        if not task_ids or not courier_id:
            continue
        try:
            score = float(score_str)
            willingness = float(willingness_str)
        except ValueError:
            continue
        task_mask = 0
        for task_id in task_ids:
            if task_id not in task_to_idx:
                task_to_idx[task_id] = len(task_to_idx)
            task_mask |= 1 << task_to_idx[task_id]
        if courier_id not in courier_to_idx:
            courier_to_idx[courier_id] = len(courier_to_idx)
        candidates.append(Candidate(task_str, task_ids, task_mask, courier_id, courier_to_idx[courier_id], score, willingness))
    return (candidates, task_to_idx, courier_to_idx)

def build_context(candidates, task_to_idx, courier_to_idx):
    ctx = Context(candidates, task_to_idx, courier_to_idx)
    if not candidates:
        return ctx
    ctx.max_score = max(1.0, max((abs(c.score) for c in candidates)))
    ctx.max_score_per_task = max(1.0, max((abs(c.score_per_task) for c in candidates)))
    for c in candidates:
        ctx.courier_degrees[c.courier_id] = ctx.courier_degrees.get(c.courier_id, 0) + 1
        for task_id in set(c.task_ids):
            ctx.task_degrees[task_id] = ctx.task_degrees.get(task_id, 0) + 1
        ctx.mask_to_candidates.setdefault(c.task_mask, []).append(c)
    if ctx.task_degrees:
        ctx.max_task_degree = max(ctx.task_degrees.values())
    if ctx.courier_degrees:
        ctx.max_courier_degree = max(ctx.courier_degrees.values())
    for c in candidates:
        degrees = [ctx.task_degrees.get(task_id, 0) for task_id in set(c.task_ids)]
        c.min_task_degree = min(degrees) if degrees else 0
        c.sum_task_degree = sum(degrees)
        c.courier_degree = ctx.courier_degrees.get(c.courier_id, 0)
    for items in ctx.mask_to_candidates.values():
        items.sort(key=lambda c: (candidate_penalty_cost(c), c.score, -c.willingness))
    return ctx

def configure_runtime(candidates, task_to_idx, courier_to_idx):
    penalty = float(CONFIG.get('acceptance_penalty', 100.0))
    CONFIG['_runtime_case_type'] = 'normal'
    CONFIG['_runtime_special_case'] = False
    CONFIG['_runtime_low_willingness'] = False
    CONFIG['_runtime_scarce_couriers'] = False
    CONFIG['_runtime_acceptance_penalty'] = penalty
    CONFIG['_runtime_penalty_profiles'] = [penalty]
    CONFIG['_runtime_multi_cost_mode'] = CONFIG.get('multi_cost_mode', 'sequential')
    if not candidates:
        return
    avg_willingness = sum((c.willingness for c in candidates)) / float(len(candidates))
    willingness_variance = sum(((c.willingness - avg_willingness) ** 2 for c in candidates)) / float(len(candidates))
    willingness_std = willingness_variance ** 0.5
    task_count = max(1, len(task_to_idx))
    CONFIG['_runtime_task_count'] = task_count
    CONFIG['_runtime_avg_willingness'] = avg_willingness
    CONFIG['_runtime_willingness_std'] = willingness_std
    courier_count = max(1, len(courier_to_idx))
    courier_ratio = courier_count / float(task_count)
    candidate_density = len(candidates) / float(task_count * courier_count)
    if avg_willingness < 0.18:
        case_type = 'low_willingness'
    elif courier_ratio <= float(CONFIG.get('special_courier_ratio_threshold', 1.0)):
        case_type = 'scarce_couriers'
    else:
        case_type = 'normal'
    CONFIG['_runtime_case_type'] = case_type
    CONFIG['_runtime_low_willingness'] = case_type == 'low_willingness'
    CONFIG['_runtime_scarce_couriers'] = case_type == 'scarce_couriers'
    CONFIG['_runtime_special_case'] = avg_willingness < 0.26 or courier_ratio <= float(CONFIG.get('special_courier_ratio_threshold', 1.0)) or candidate_density < 8.0
    CONFIG['_runtime_high_noise'] = case_type == 'normal' and task_count == 30 and (avg_willingness >= 0.42) and (willingness_std >= 0.21)
    CONFIG['_runtime_medium_normal'] = case_type == 'normal' and task_count == 30 and (not CONFIG.get('_runtime_high_noise', False))
    CONFIG['_runtime_large_normal'] = case_type == 'normal' and task_count >= 35

def apply_runtime_overrides():
    case_type = CONFIG.get('_runtime_case_type', 'normal')
    overrides = {}
    if case_type == 'low_willingness':
        overrides = {'enable_multi_courier_output': True, 'auto_strategy_budget_ms': 180.0, 'local_search_budget_ms': 0.0, 'multi_primary_time_budget_ms': 1100.0, 'backup_time_budget_ms': 4600.0, 'backup_reallocation_budget_ms': 2300.0, 'min_backup_utility': 0.0, 'max_extra_couriers_per_bundle': 8, '_runtime_multi_cost_mode': 'race'}
    elif case_type == 'scarce_couriers':
        overrides = {'enable_multi_courier_output': True, 'auto_strategy_budget_ms': 300.0, 'local_search_budget_ms': 2800.0, 'multi_primary_time_budget_ms': 0.0, 'backup_time_budget_ms': 500.0, 'backup_reallocation_budget_ms': 160.0, 'min_backup_utility': 0.0, 'max_extra_couriers_per_bundle': 5, '_runtime_multi_cost_mode': 'race'}
    elif CONFIG.get('_runtime_high_noise', False):
        overrides = {'enable_multi_courier_output': True, 'multi_primary_time_budget_ms': 2100.0, 'backup_time_budget_ms': 1050.0, 'backup_reallocation_budget_ms': 420.0, 'normal_preview_backup_cap': 3, 'normal_preview_scan_per_primary': 30, 'normal_topology_top_k': 6, 'normal_topology_generated_limit': 14, 'min_backup_utility': 0.0, 'max_extra_couriers_per_bundle': 6}
    else:
        overrides = {'enable_multi_courier_output': True, 'multi_primary_time_budget_ms': 2400.0 if int(CONFIG.get('_runtime_task_count', 0)) <= 30 else 3200.0, 'backup_time_budget_ms': 900.0, 'backup_reallocation_budget_ms': 360.0, 'min_backup_utility': 0.0, 'max_extra_couriers_per_bundle': 5}
    saved = {key: CONFIG.get(key) for key in overrides}
    CONFIG.update(overrides)
    return saved

def restore_runtime_overrides(saved):
    for key, value in saved.items():
        CONFIG[key] = value

def candidate_penalty_cost(c):
    penalty = float(CONFIG.get('_runtime_acceptance_penalty', CONFIG.get('acceptance_penalty', 100.0)))
    return c.score * c.willingness + penalty * c.task_count * (1.0 - c.willingness)

def official_penalty_cost(c):
    return c.score * c.willingness + 100.0 * c.task_count * (1.0 - c.willingness)

def evaluate(selected, total_task_count=None):
    task_mask = 0
    courier_mask = 0
    conflicts = 0
    score = 0.0
    penalty_score = 0.0
    for c in selected:
        conflicts += _count_bits(task_mask & c.task_mask)
        conflicts += c.task_count - _count_bits(c.task_mask)
        if courier_mask & c.courier_bit:
            conflicts += 1
        task_mask |= c.task_mask
        courier_mask |= c.courier_bit
        score += c.score
        penalty_score += candidate_penalty_cost(c)
    covered = _count_bits(task_mask)
    if total_task_count is not None and total_task_count > covered:
        penalty_score += float(CONFIG.get('acceptance_penalty', 100.0)) * (total_task_count - covered)
    return Eval(covered, score, penalty_score, conflicts, len(selected))

def evaluate_with_penalty(selected, total_task_count=None, penalty=100.0):
    task_mask = 0
    courier_mask = 0
    conflicts = 0
    score = 0.0
    penalty_score = 0.0
    for c in selected:
        conflicts += _count_bits(task_mask & c.task_mask)
        conflicts += c.task_count - _count_bits(c.task_mask)
        if courier_mask & c.courier_bit:
            conflicts += 1
        task_mask |= c.task_mask
        courier_mask |= c.courier_bit
        score += c.score
        penalty_score += c.score * c.willingness + penalty * c.task_count * (1.0 - c.willingness)
    covered = _count_bits(task_mask)
    if total_task_count is not None and total_task_count > covered:
        penalty_score += penalty * (total_task_count - covered)
    return Eval(covered, score, penalty_score, conflicts, len(selected))

def is_better(new_eval, old_eval):
    if old_eval is None:
        return True
    if new_eval.conflicts != old_eval.conflicts:
        return new_eval.conflicts < old_eval.conflicts
    if new_eval.covered != old_eval.covered:
        return new_eval.covered > old_eval.covered
    if abs(new_eval.penalty_score - old_eval.penalty_score) > EPS:
        return new_eval.penalty_score < old_eval.penalty_score
    if abs(new_eval.score - old_eval.score) > EPS:
        return new_eval.score < old_eval.score
    return new_eval.items < old_eval.items

def greedy_select(ordered):
    selected = []
    used_tasks = 0
    used_couriers = 0
    rejection_penalty = float(CONFIG.get('acceptance_penalty', 100.0))
    for c in ordered:
        if c.task_mask & used_tasks:
            continue
        if c.courier_bit & used_couriers:
            continue
        if candidate_penalty_cost(c) >= rejection_penalty * c.task_count - EPS:
            continue
        selected.append(c)
        used_tasks |= c.task_mask
        used_couriers |= c.courier_bit
    return selected

def exact_official_baseline(candidates, task_to_idx):
    selected = []
    used_tasks = 0
    used_couriers = 0
    for candidate in sorted(candidates, key=lambda c: c.score):
        if candidate.task_mask & used_tasks:
            continue
        if candidate.courier_bit & used_couriers:
            continue
        selected.append(candidate)
        used_tasks |= candidate.task_mask
        used_couriers |= candidate.courier_bit
    if _count_bits(used_tasks) != len(task_to_idx):
        return None
    return selected

def strategy_key(ctx, spec):
    score_w, per_task_w, willing_w, bundle_w, scarcity_w, courier_w, bundle_first = spec
    max_score = ctx.max_score
    max_score_per_task = ctx.max_score_per_task
    max_task_degree = float(ctx.max_task_degree)
    max_courier_degree = float(ctx.max_courier_degree)

    def key(c):
        scarcity = c.min_task_degree / max_task_degree if max_task_degree else 0.0
        courier_pressure = c.courier_degree / max_courier_degree if max_courier_degree else 0.0
        rank = score_w * (c.score / max_score) + per_task_w * (c.score_per_task / max_score_per_task) - willing_w * c.willingness - bundle_w * (c.task_count - 1) + scarcity_w * scarcity + courier_w * courier_pressure
        if bundle_first:
            return (0 if c.task_count > 1 else 1, rank, c.score, -c.willingness)
        return (rank, c.score, -c.willingness)
    return key

def base_orders(ctx):
    orders = [('official_penalty', sorted(ctx.candidates, key=lambda c: (official_penalty_cost(c), c.score, -c.willingness))), ('official_penalty_per_task', sorted(ctx.candidates, key=lambda c: (official_penalty_cost(c) / max(1, c.task_count), official_penalty_cost(c), -c.willingness))), ('penalty', sorted(ctx.candidates, key=lambda c: (candidate_penalty_cost(c), c.score, -c.willingness))), ('penalty_per_task', sorted(ctx.candidates, key=lambda c: (candidate_penalty_cost(c) / max(1, c.task_count), candidate_penalty_cost(c), -c.willingness))), ('willingness_first', sorted(ctx.candidates, key=lambda c: (-c.willingness, candidate_penalty_cost(c) / max(1, c.task_count), c.score))), ('risk_adjusted', sorted(ctx.candidates, key=lambda c: (candidate_penalty_cost(c) / max(0.05, c.willingness + 0.05), candidate_penalty_cost(c), c.score))), ('bundle_penalty_per_task', sorted(ctx.candidates, key=lambda c: (0 if c.task_count > 1 else 1, candidate_penalty_cost(c) / max(1, c.task_count), -c.willingness, c.score))), ('score_per_willingness', sorted(ctx.candidates, key=lambda c: (c.score / max(0.01, c.willingness), c.score, -c.willingness))), ('single_penalty', sorted(ctx.candidates, key=lambda c: (0 if c.task_count == 1 else 1, candidate_penalty_cost(c), c.score))), ('score', sorted(ctx.candidates, key=lambda c: (c.score, c.score_per_task, -c.willingness))), ('per_task', sorted(ctx.candidates, key=lambda c: (c.score_per_task, c.score, -c.willingness))), ('bundle_first', sorted(ctx.candidates, key=lambda c: (0 if c.task_count > 1 else 1, candidate_penalty_cost(c), c.score))), ('scarcity', sorted(ctx.candidates, key=lambda c: (c.min_task_degree, c.sum_task_degree, c.score_per_task, c.score)))]
    if CONFIG.get('_runtime_special_case', False):
        orders.extend([('scarce_bundle_reliable', sorted(ctx.candidates, key=lambda c: (0 if c.task_count > 1 else 1, -c.willingness, candidate_penalty_cost(c) / max(1, c.task_count), c.score))), ('hard_bundle_official', sorted(ctx.candidates, key=lambda c: (0 if c.task_count > 1 else 1, official_penalty_cost(c) / max(1, c.task_count), -c.willingness, c.score))), ('hard_bundle_willingness', sorted(ctx.candidates, key=lambda c: (0 if c.task_count > 1 else 1, -c.willingness, official_penalty_cost(c) / max(1, c.task_count), c.score))), ('low_willingness_guard', sorted(ctx.candidates, key=lambda c: (official_penalty_cost(c) / max(0.04, c.willingness + 0.04), -c.willingness, c.score)))])
    return orders

def perturbation_orders(ctx):
    settings = [(45, 5.9), (24, 3.2), (71, 9.2)]
    if CONFIG.get('_runtime_scarce_couriers', False):
        settings.extend(((seed, amplitude) for amplitude in (1.5, 3.0, 5.0, 7.0, 10.0) for seed in (7, 19, 37, 53)))
    orders = []
    for seed, amplitude in settings:
        rng = random.Random(seed)
        decorated = [(candidate, rng.random()) for candidate in ctx.candidates]
        ordered = [candidate for candidate, noise in sorted(decorated, key=lambda item: (candidate_penalty_cost(item[0]) / max(1, item[0].task_count) + (item[1] - 0.5) * amplitude, candidate_penalty_cost(item[0]), item[0].score))]
        orders.append(('perturbed_penalty', ordered))
    return orders

def generated_specs():
    return CONFIG['strategies']

def rank_removals(selected):
    return sorted(selected, key=lambda c: (candidate_penalty_cost(c) / max(1, c.task_count), candidate_penalty_cost(c), c.score), reverse=True)

def mask_bits(mask):
    while mask:
        bit = mask & -mask
        yield bit
        mask ^= bit

def exact_cover_freed(ctx, freed_mask, locked_courier_mask, score_ceiling, deadline_ms=None):
    if not freed_mask:
        return []
    if _count_bits(freed_mask) > CONFIG['max_exact_replace_tasks']:
        return None
    relevant_by_bit = {}
    seen = set()
    candidate_limit = int(CONFIG['max_candidates_per_mask'])
    if CONFIG.get('_runtime_scarce_couriers', False):
        candidate_limit = int(CONFIG.get('special_max_candidates_per_mask', candidate_limit))
    submask = freed_mask
    while submask:
        for c in ctx.mask_to_candidates.get(submask, ())[:candidate_limit]:
            ident = id(c)
            if ident in seen:
                continue
            seen.add(ident)
            if c.courier_bit & locked_courier_mask:
                continue
            if c.task_mask & ~freed_mask:
                continue
            for bit in mask_bits(c.task_mask):
                relevant_by_bit.setdefault(bit, []).append(c)
        submask = submask - 1 & freed_mask
    for bit in relevant_by_bit:
        relevant_by_bit[bit].sort(key=lambda c: (candidate_penalty_cost(c), c.score, -c.willingness))
    best_score = [score_ceiling]
    best_selected = [None]

    def dfs(covered_mask, used_couriers, score, selected):
        if deadline_ms is not None and (not _has_time(deadline_ms, 30.0)):
            return
        if score >= best_score[0] - EPS:
            return
        if covered_mask == freed_mask:
            best_score[0] = score
            best_selected[0] = list(selected)
            return
        remaining = freed_mask & ~covered_mask
        next_bit = None
        best_count = 10 ** 9
        for bit in mask_bits(remaining):
            count = 0
            for c in relevant_by_bit.get(bit, ()):
                if c.task_mask & covered_mask:
                    continue
                if c.courier_bit & used_couriers:
                    continue
                count += 1
            if count < best_count:
                best_count = count
                next_bit = bit
        if next_bit is None or best_count == 0:
            return
        for c in relevant_by_bit.get(next_bit, ()):
            if c.task_mask & covered_mask:
                continue
            if c.courier_bit & used_couriers:
                continue
            dfs(covered_mask | c.task_mask, used_couriers | c.courier_bit, score + candidate_penalty_cost(c), selected + [c])
    dfs(0, 0, 0.0, [])
    return best_selected[0]

def replace_candidates(selected, removed_tuple, replacement):
    removed = set(removed_tuple)
    kept = [c for c in selected if c not in removed]
    kept.extend(replacement)
    return kept

def reassign_couriers_for_selected_masks(ctx, selected, total_tasks):
    if not selected:
        return (selected, evaluate(selected, total_tasks))
    courier_count = len(ctx.courier_to_idx)
    if len(selected) > courier_count:
        return (selected, evaluate(selected, total_tasks))
    inf = 1000000000000.0
    costs = []
    choices = []
    for chosen in selected:
        row = [inf] * courier_count
        by_courier = {}
        for candidate in ctx.mask_to_candidates.get(chosen.task_mask, ()):
            cost = candidate_penalty_cost(candidate)
            if cost < row[candidate.courier_idx]:
                row[candidate.courier_idx] = cost
                by_courier[candidate.courier_idx] = candidate
        costs.append(row)
        choices.append(by_courier)
    n = len(costs)
    m = courier_count
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = inf
            j1 = 0
            row = costs[i0 - 1]
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = row[j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            if delta >= inf / 2:
                return (selected, evaluate(selected, total_tasks))
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment = [-1] * n
    for courier_col in range(1, m + 1):
        if p[courier_col]:
            assignment[p[courier_col] - 1] = courier_col - 1
    reassigned = []
    for index, courier_idx in enumerate(assignment):
        candidate = choices[index].get(courier_idx)
        if candidate is None:
            return (selected, evaluate(selected, total_tasks))
        reassigned.append(candidate)
    return (reassigned, evaluate(reassigned, total_tasks))

def local_search(ctx, selected, deadline_ms):
    current = list(selected)
    total_tasks = _count_bits(ctx.all_task_mask)
    current_eval = evaluate(current, total_tasks)
    start_ms = _now_ms()
    budget = CONFIG['local_search_budget_ms']
    selected_set = set((id(c) for c in current))
    unselected = [c for c in ctx.candidates if id(c) not in selected_set]
    mask_to_unselected = {}
    for c in unselected:
        mask_to_unselected.setdefault(c.task_mask, []).append(c)
    for mask in mask_to_unselected:
        mask_to_unselected[mask].sort(key=lambda c: (candidate_penalty_cost(c), c.score, -c.willingness))
    for _ in range(4):
        if _now_ms() - start_ms > budget or not _has_time(deadline_ms):
            break
        improved = False
        for ri in range(len(current)):
            if _now_ms() - start_ms > budget or not _has_time(deadline_ms):
                break
            removed = current[ri]
            used_couriers = 0
            for j, c in enumerate(current):
                if j != ri:
                    used_couriers |= c.courier_bit
            removed_penalty = candidate_penalty_cost(removed)
            for uc in mask_to_unselected.get(removed.task_mask, []):
                if uc.courier_bit & used_couriers:
                    continue
                if candidate_penalty_cost(uc) >= removed_penalty - EPS:
                    break
                new_current = list(current)
                new_current[ri] = uc
                new_eval = evaluate(new_current, total_tasks)
                if is_better(new_eval, current_eval):
                    current = new_current
                    current_eval = new_eval
                    selected_set.discard(id(removed))
                    selected_set.add(id(uc))
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break
    for _ in range(2):
        if _now_ms() - start_ms > budget or not _has_time(deadline_ms):
            break
        improved = False
        for removed in rank_removals(current):
            if _now_ms() - start_ms > budget or not _has_time(deadline_ms):
                break
            freed_mask = removed.task_mask
            removed_score = candidate_penalty_cost(removed)
            locked_couriers = 0
            for c in current:
                if c is not removed:
                    locked_couriers |= c.courier_bit
            replacement = exact_cover_freed(ctx, freed_mask, locked_couriers, removed_score, deadline_ms)
            if replacement is not None:
                candidate = replace_candidates(current, (removed,), replacement)
                candidate_eval = evaluate(candidate, total_tasks)
                if is_better(candidate_eval, current_eval):
                    current = candidate
                    current_eval = candidate_eval
                    improved = True
                    break
        if not improved:
            break
    rounds = [(2, CONFIG['pair_top_k'])]
    if CONFIG.get('try_triples', True):
        rounds.append((3, CONFIG['triple_top_k']))
    while _now_ms() - start_ms < budget and _has_time(deadline_ms):
        any_improved = False
        for remove_count, top_k in rounds:
            if not _has_time(deadline_ms):
                break
            improved = False
            ranked = rank_removals(current)[:min(top_k, len(current))]
            for removed_tuple in itertools.combinations(ranked, remove_count):
                if not _has_time(deadline_ms):
                    break
                freed_mask = 0
                removed_score = 0.0
                locked_couriers = 0
                removed_set = set(removed_tuple)
                for c in current:
                    if c in removed_set:
                        freed_mask |= c.task_mask
                        removed_score += candidate_penalty_cost(c)
                    else:
                        locked_couriers |= c.courier_bit
                replacement = exact_cover_freed(ctx, freed_mask, locked_couriers, removed_score, deadline_ms)
                if replacement is None:
                    continue
                candidate = replace_candidates(current, removed_tuple, replacement)
                candidate_eval = evaluate(candidate, total_tasks)
                if is_better(candidate_eval, current_eval):
                    current = candidate
                    current_eval = candidate_eval
                    improved = True
                    any_improved = True
                    break
            if not improved:
                continue
        if not any_improved:
            break
    return (current, current_eval)

def choose_solution_for_current_penalty(candidates, task_to_idx, courier_to_idx, deadline_ms):
    ctx = build_context(candidates, task_to_idx, courier_to_idx)
    total_tasks = _count_bits(ctx.all_task_mask)
    best = []
    best_eval = None
    repair_orders = []
    for name, ordered in base_orders(ctx):
        selected = greedy_select(ordered)
        ev = evaluate(selected, total_tasks)
        repair_orders.append((name, ordered))
        if is_better(ev, best_eval):
            best = selected
            best_eval = ev
    if _has_time(deadline_ms, 500.0):
        for name, ordered in perturbation_orders(ctx):
            selected = greedy_select(ordered)
            ev = evaluate(selected, total_tasks)
            if is_better(ev, best_eval):
                best = selected
                best_eval = ev
    auto_start = _now_ms()
    for index, spec in enumerate(generated_specs()):
        if _now_ms() - auto_start > CONFIG['auto_strategy_budget_ms']:
            break
        if not _has_time(deadline_ms, 120.0):
            break
        ordered = sorted(ctx.candidates, key=strategy_key(ctx, spec))
        selected = greedy_select(ordered)
        ev = evaluate(selected, total_tasks)
        if len(repair_orders) < 12:
            repair_orders.append(('generated', ordered))
        if is_better(ev, best_eval):
            best = selected
            best_eval = ev
    improved, improved_eval = local_search(ctx, best, deadline_ms)
    if is_better(improved_eval, best_eval):
        best = improved
        best_eval = improved_eval
    reassigned, reassigned_eval = reassign_couriers_for_selected_masks(ctx, best, total_tasks)
    if is_better(reassigned_eval, best_eval):
        best = reassigned
        best_eval = reassigned_eval
    return best

def choose_solution(candidates, task_to_idx, courier_to_idx, deadline_ms):
    profiles = list(CONFIG.get('_runtime_penalty_profiles', (CONFIG.get('_runtime_acceptance_penalty', 100.0),)))
    if not profiles:
        profiles = [float(CONFIG.get('acceptance_penalty', 100.0))]
    total_tasks = len(task_to_idx)
    best = None
    best_eval = None
    original_penalty = CONFIG.get('_runtime_acceptance_penalty', CONFIG.get('acceptance_penalty', 100.0))
    for index, penalty in enumerate(profiles):
        if index > 0 and (not _has_time(deadline_ms, 1800.0)):
            break
        CONFIG['_runtime_acceptance_penalty'] = penalty
        saved_auto_budget = CONFIG.get('auto_strategy_budget_ms', 300.0)
        saved_local_budget = CONFIG.get('local_search_budget_ms', 5000.0)
        if index > 0:
            CONFIG['auto_strategy_budget_ms'] = min(float(saved_auto_budget), 160.0)
            CONFIG['local_search_budget_ms'] = min(float(saved_local_budget), 450.0)
        selected = choose_solution_for_current_penalty(candidates, task_to_idx, courier_to_idx, deadline_ms)
        CONFIG['auto_strategy_budget_ms'] = saved_auto_budget
        CONFIG['local_search_budget_ms'] = saved_local_budget
        official_eval = evaluate_with_penalty(selected, total_tasks, 100.0)
        if is_better(official_eval, best_eval):
            best = selected
            best_eval = official_eval
    CONFIG['_runtime_acceptance_penalty'] = original_penalty
    return best if best is not None else []

def ordered_group(group):
    return sorted(group, key=lambda c: (c.score, -c.willingness, c.courier_id))

def _race_group_penalty(group):
    if not group:
        return 0.0
    penalty = float(CONFIG.get('acceptance_penalty', 100.0))
    key = (penalty, group[0].task_count, tuple(sorted(((c.courier_idx, round(c.score, 9), round(c.willingness, 9)) for c in group))))
    cached = _GROUP_COST_CACHE.get(key)
    if cached is not None:
        return cached
    probs = [min(1.0, max(0.0, c.willingness)) for c in group]
    fail_probability = 1.0
    for p in probs:
        fail_probability *= 1.0 - p
    expected_score = 0.0
    n = len(group)
    for i, candidate in enumerate(group):
        pi = probs[i]
        if pi <= 0.0:
            continue
        dist = [1.0]
        for j, pj in enumerate(probs):
            if j == i:
                continue
            next_dist = [0.0] * (len(dist) + 1)
            qj = 1.0 - pj
            for count, value in enumerate(dist):
                next_dist[count] += value * qj
                next_dist[count + 1] += value * pj
            dist = next_dist
        win_weight = 0.0
        for m, value in enumerate(dist):
            win_weight += value / float(m + 1)
        expected_score += candidate.score * pi * win_weight
    value = expected_score + penalty * group[0].task_count * fail_probability
    _GROUP_COST_CACHE[key] = value
    return value

def multi_group_penalty(group):
    if not group:
        return 0.0
    mode = CONFIG.get('_runtime_multi_cost_mode', CONFIG.get('multi_cost_mode', 'race'))
    penalty = float(CONFIG.get('acceptance_penalty', 100.0))
    if mode == 'parallel':
        fail_probability = 1.0
        expected_score = 0.0
        for candidate in group:
            willingness = min(1.0, max(0.0, candidate.willingness))
            expected_score += willingness * candidate.score
            fail_probability *= 1.0 - willingness
        return expected_score + penalty * group[0].task_count * fail_probability
    if mode == 'sequential':
        fail_probability = 1.0
        expected_score = 0.0
        for candidate in ordered_group(group):
            willingness = min(1.0, max(0.0, candidate.willingness))
            expected_score += fail_probability * willingness * candidate.score
            fail_probability *= 1.0 - willingness
        return expected_score + penalty * group[0].task_count * fail_probability
    return _race_group_penalty(group)

def choose_probability_backups(candidates, selected, deadline_ms):
    if not CONFIG.get('enable_multi_courier_output', False):
        return {}
    if not selected or not _has_time(deadline_ms, 120.0):
        return {}
    start_ms = _now_ms()
    max_extra = int(CONFIG.get('max_extra_couriers_per_bundle', 0))
    min_utility = float(CONFIG.get('min_backup_utility', 0.0))
    if max_extra <= 0:
        return {}
    by_task_str = {}
    for c in candidates:
        by_task_str.setdefault(c.task_str, []).append(c)
    for items in by_task_str.values():
        items.sort(key=lambda c: (c.score, -c.willingness))
    selected_ids = set((id(c) for c in selected))
    used_couriers = set((c.courier_id for c in selected))
    backups = {}
    while _has_time(deadline_ms, 80.0) and _now_ms() - start_ms < CONFIG['backup_time_budget_ms']:
        best_choice = None
        best_utility = min_utility
        for primary in selected:
            current = backups.get(id(primary), [])
            if len(current) >= max_extra:
                continue
            current_cost = multi_group_penalty([primary] + current)
            for backup in by_task_str.get(primary.task_str, ()):
                if id(backup) in selected_ids:
                    continue
                if backup.courier_id in used_couriers:
                    continue
                utility = current_cost - multi_group_penalty([primary] + current + [backup])
                if utility > best_utility + EPS:
                    best_utility = utility
                    best_choice = (primary, backup)
        if best_choice is None:
            break
        primary, backup = best_choice
        backups.setdefault(id(primary), []).append(backup)
        used_couriers.add(backup.courier_id)
    return backups

def evaluate_with_backups(selected, backup_map, total_task_count):
    task_mask = 0
    used_couriers = 0
    conflicts = 0
    raw_score = 0.0
    penalty_score = 0.0
    penalty = float(CONFIG.get('acceptance_penalty', 100.0))
    for primary in selected:
        task_mask |= primary.task_mask
        group = [primary] + list(backup_map.get(id(primary), ()))
        for candidate in group:
            if used_couriers & candidate.courier_bit:
                conflicts += 1
            used_couriers |= candidate.courier_bit
            raw_score += candidate.score
        penalty_score += multi_group_penalty(group)
    covered = _count_bits(task_mask)
    if covered < total_task_count:
        penalty_score += penalty * (total_task_count - covered)
    return Eval(covered, raw_score, penalty_score, conflicts, len(selected))

def _allocation_groups(selected, backup_map):
    return [[primary] + list(backup_map.get(id(primary), ())) for primary in selected]

def _backup_map_from_groups(groups):
    selected = []
    backup_map = {}
    for group in groups:
        if not group:
            continue
        primary = group[0]
        selected.append(primary)
        if len(group) > 1:
            backup_map[id(primary)] = list(group[1:])
    return (selected, backup_map)

def race_topology_repair(candidates, task_to_idx, courier_to_idx, selected, backup_map, deadline_ms):
    budget = float(CONFIG.get('race_topology_repair_budget_ms', 0.0))
    if budget <= 0.0 or not selected or (not _has_time(deadline_ms, 260.0)):
        return (selected, backup_map)
    total_tasks = len(task_to_idx)
    if total_tasks <= 15:
        return (selected, backup_map)
    current_eval = evaluate_with_backups(selected, backup_map, total_tasks)
    if current_eval.conflicts or current_eval.covered < total_tasks:
        return (selected, backup_map)
    started_ms = _now_ms()
    ctx = build_context(candidates, task_to_idx, courier_to_idx)
    current_selected = list(selected)
    current_backups = dict(backup_map or {})
    by_pair_mask = {}
    for candidate in candidates:
        if candidate.task_count != 2:
            continue
        by_pair_mask.setdefault(candidate.task_mask, []).append(candidate)
    for items in by_pair_mask.values():
        items.sort(key=lambda c: (official_penalty_cost(c), c.score, -c.willingness))

    def best_pair_group(items, old_cost):
        group = []
        used = set()
        current_cost = float(CONFIG.get('acceptance_penalty', 100.0)) * 2.0
        best_group = None
        best_delta = 0.0
        pool = items[:10]
        for _ in range(min(5, len(pool))):
            best_choice = None
            best_cost = current_cost
            for candidate in pool:
                if candidate.courier_id in used:
                    continue
                trial = group + [candidate]
                cost = multi_group_penalty(trial)
                if cost < best_cost - EPS:
                    best_cost = cost
                    best_choice = candidate
            if best_choice is None:
                break
            group.append(best_choice)
            used.add(best_choice.courier_id)
            current_cost = best_cost
            delta = current_cost - old_cost
            if delta < best_delta - EPS:
                best_delta = delta
                best_group = tuple(group)
        return (best_delta, best_group)
    while _has_time(deadline_ms, 180.0) and _now_ms() - started_ms < budget:
        groups = _allocation_groups(current_selected, current_backups)
        primary_by_task = {group[0].task_mask: group[0] for group in groups}
        group_by_primary = {group[0]: group for group in groups}
        primary_by_courier = {group[0].courier_id: group[0] for group in groups}
        backup_by_courier = {}
        for group in groups:
            for backup in group[1:]:
                backup_by_courier[backup.courier_id] = group[0]
        best_change = None
        best_eval = current_eval
        pair_options = []
        for pair_mask, items in by_pair_mask.items():
            normal_30 = CONFIG.get('_runtime_case_type') == 'normal' and total_tasks == 30
            scan_fraction = 0.7 if normal_30 else 0.55
            if not _has_time(deadline_ms, 120.0) or _now_ms() - started_ms >= budget * scan_fraction:
                break
            if pair_mask & ~ctx.all_task_mask:
                continue
            overlapping = [group for group in groups if group[0].task_mask & pair_mask]
            if len(overlapping) != 2:
                continue
            old_cost = sum((multi_group_penalty(group) for group in overlapping))
            delta, combo = best_pair_group(items, old_cost)
            if combo is not None:
                pair_options.append((delta, pair_mask, combo))
        pair_options.sort(key=lambda row: row[0])
        pair_cap = 60 if CONFIG.get('_runtime_case_type') == 'normal' and total_tasks == 30 else 36
        for _delta, pair_mask, combo in pair_options[:pair_cap]:
            if not _has_time(deadline_ms, 100.0) or _now_ms() - started_ms >= budget:
                break
            combo_couriers = {candidate.courier_id for candidate in combo}
            removed_primaries = set()
            for group in groups:
                primary = group[0]
                if primary.task_mask & pair_mask:
                    removed_primaries.add(primary)
            for courier_id in combo_couriers:
                borrowed_primary = primary_by_courier.get(courier_id)
                if borrowed_primary is not None:
                    removed_primaries.add(borrowed_primary)
            freed_mask = 0
            for primary in removed_primaries:
                freed_mask |= primary.task_mask
            repair_mask = freed_mask & ~pair_mask
            if _count_bits(repair_mask) > int(CONFIG.get('max_exact_replace_tasks', 8)):
                continue
            kept_groups = []
            locked_couriers = 0
            for group in groups:
                primary = group[0]
                if primary in removed_primaries:
                    continue
                filtered = [primary]
                for backup in group[1:]:
                    if backup.courier_id not in combo_couriers:
                        filtered.append(backup)
                kept_groups.append(filtered)
                for candidate in filtered:
                    locked_couriers |= candidate.courier_bit
            for candidate in combo:
                locked_couriers |= candidate.courier_bit
            replacement = exact_cover_freed(ctx, repair_mask, locked_couriers, 1000000000000.0, deadline_ms)
            if replacement is None:
                continue
            if replacement:
                used = locked_couriers
                conflict = False
                for candidate in replacement:
                    if candidate.courier_bit & used:
                        conflict = True
                        break
                    used |= candidate.courier_bit
                if conflict:
                    continue
            pair_primary = min(combo, key=lambda c: (c.score, -c.willingness, c.courier_id))
            pair_group = [pair_primary] + [candidate for candidate in combo if candidate is not pair_primary]
            candidate_groups = kept_groups + [pair_group] + [[candidate] for candidate in replacement]
            candidate_selected, candidate_backups = _backup_map_from_groups(candidate_groups)
            saved_realloc_budget = CONFIG.get('backup_reallocation_budget_ms', 0.0)
            CONFIG['backup_reallocation_budget_ms'] = min(max(float(saved_realloc_budget), 260.0), 700.0)
            try:
                candidate_selected, candidate_backups = improve_backup_allocation(candidates, candidate_selected, candidate_backups, deadline_ms)
            finally:
                CONFIG['backup_reallocation_budget_ms'] = saved_realloc_budget
            candidate_eval = evaluate_with_backups(candidate_selected, candidate_backups, total_tasks)
            if is_better(candidate_eval, best_eval):
                best_eval = candidate_eval
                best_change = (candidate_selected, candidate_backups)
        if best_change is None:
            break
        current_selected, current_backups = best_change
        current_eval = best_eval
    return (current_selected, current_backups)

def prefer_official_baseline_when_better(candidates, task_to_idx, selected, backup_map=None):
    if len(candidates) > 15000:
        return (selected, backup_map)
    baseline = exact_official_baseline(candidates, task_to_idx)
    if baseline is None:
        return (selected, backup_map)
    total_tasks = len(task_to_idx)
    baseline_eval = evaluate_with_penalty(baseline, total_tasks, 100.0)
    if backup_map:
        current_eval = evaluate_with_backups(selected, backup_map, total_tasks)
        saved_mode = CONFIG.get('_runtime_multi_cost_mode')
        try:
            _GROUP_COST_CACHE.clear()
            CONFIG['_runtime_multi_cost_mode'] = 'parallel'
            parallel_eval = evaluate_with_backups(selected, backup_map, total_tasks)
        finally:
            _GROUP_COST_CACHE.clear()
            if saved_mode is None:
                CONFIG.pop('_runtime_multi_cost_mode', None)
            else:
                CONFIG['_runtime_multi_cost_mode'] = saved_mode
        if is_better(baseline_eval, parallel_eval):
            return (baseline, None)
    else:
        current_eval = evaluate_with_penalty(selected, total_tasks, 100.0)
    if is_better(baseline_eval, current_eval):
        return (baseline, None)
    return (selected, backup_map)

def improve_low_with_multi_options(candidates, task_to_idx, courier_to_idx, selected, deadline_ms):
    ctx = build_context(candidates, task_to_idx, courier_to_idx)
    total_tasks = len(task_to_idx)
    options = []
    seen = set()

    def add_option(option):
        signature = tuple(sorted(((c.task_mask, c.courier_idx) for c in option)))
        if signature not in seen:
            seen.add(signature)
            options.append(option)
    add_option(selected)
    for seed, amplitude in ((3, 3.0), (6, 3.0), (14, 3.0), (15, 2.0), (17, 3.0), (55, 5.5), (63, 3.0), (78, 9.0), (81, 4.0), (19, 14.0), (19, 10.0), (7, 7.0), (53, 1.0), (53, 2.0), (53, 4.0), (37, 4.0), (89, 14.0), (71, 2.0)):
        if not _has_time(deadline_ms, 450.0):
            break
        rng = random.Random(seed)
        decorated = [(candidate, rng.random()) for candidate in ctx.candidates]
        ordered = [candidate for candidate, noise in sorted(decorated, key=lambda item: (candidate_penalty_cost(item[0]) / max(1, item[0].task_count) + (item[1] - 0.5) * amplitude, candidate_penalty_cost(item[0]), item[0].score))]
        add_option(greedy_select(ordered))
    for _name, ordered in base_orders(ctx):
        add_option(greedy_select(ordered))
    for _name, ordered in perturbation_orders(ctx):
        add_option(greedy_select(ordered))
    best_selected = selected
    best_backups = choose_probability_backups(candidates, selected, deadline_ms)
    best_eval = evaluate_with_backups(best_selected, best_backups, total_tasks)
    evaluated = [(best_eval.penalty_score, best_selected, best_backups)]
    for option in options:
        if not _has_time(deadline_ms, 100.0):
            break
        backups = choose_probability_backups(candidates, option, deadline_ms)
        candidate_eval = evaluate_with_backups(option, backups, total_tasks)
        evaluated.append((candidate_eval.penalty_score, option, backups))
        if is_better(candidate_eval, best_eval):
            best_selected = option
            best_backups = backups
            best_eval = candidate_eval
    saved_budget = CONFIG.get('backup_reallocation_budget_ms', 0.0)
    CONFIG['backup_reallocation_budget_ms'] = min(float(saved_budget), 140.0)
    try:
        for _score, option, backups in sorted(evaluated, key=lambda result: result[0])[:16]:
            if not _has_time(deadline_ms, 180.0):
                break
            option, backups = improve_backup_allocation(candidates, option, backups, deadline_ms)
            candidate_eval = evaluate_with_backups(option, backups, total_tasks)
            if is_better(candidate_eval, best_eval):
                best_selected = option
                best_backups = backups
                best_eval = candidate_eval
    finally:
        CONFIG['backup_reallocation_budget_ms'] = saved_budget
    return (best_selected, best_backups)

def improve_regular_legacy_multi_options(candidates, task_to_idx, courier_to_idx, selected, deadline_ms):
    ctx = build_context(candidates, task_to_idx, courier_to_idx)
    total_tasks = len(task_to_idx)
    options = [selected]
    seen = {tuple(sorted(((c.task_mask, c.courier_idx) for c in selected)))}

    def add_order(ordered):
        option = greedy_select(ordered)
        signature = tuple(sorted(((c.task_mask, c.courier_idx) for c in option)))
        if signature not in seen:
            seen.add(signature)
            options.append(option)
    add_order(sorted(ctx.candidates, key=lambda c: (official_penalty_cost(c), c.score, -c.willingness)))
    add_order(sorted(ctx.candidates, key=lambda c: (official_penalty_cost(c) / max(1, c.task_count), official_penalty_cost(c), -c.willingness)))
    add_order(sorted(ctx.candidates, key=lambda c: (c.score, c.score_per_task, -c.willingness)))
    add_order(sorted(ctx.candidates, key=lambda c: (c.score_per_task, c.score, -c.willingness)))
    add_order(sorted(ctx.candidates, key=lambda c: (0 if c.task_count > 1 else 1, official_penalty_cost(c) / max(1, c.task_count), -c.willingness, c.score)))
    for seed, amplitude in ((19, 1.0), (37, 3.0), (53, 3.0)):
        if not _has_time(deadline_ms, 300.0):
            break
        rng = random.Random(seed)
        decorated = [(candidate, rng.random()) for candidate in ctx.candidates]
        add_order([candidate for candidate, noise in sorted(decorated, key=lambda item: (candidate_penalty_cost(item[0]) / max(1, item[0].task_count) + (item[1] - 0.5) * amplitude, candidate_penalty_cost(item[0]), item[0].score))])
    best_selected = selected
    best_backups = {}
    best_eval = None
    for option in options:
        if not _has_time(deadline_ms, 120.0):
            break
        backups = choose_probability_backups(candidates, option, deadline_ms)
        candidate_eval = evaluate_with_backups(option, backups, total_tasks)
        if is_better(candidate_eval, best_eval):
            best_selected = option
            best_backups = backups
            best_eval = candidate_eval
    best_selected, best_backups = improve_backup_allocation(candidates, best_selected, best_backups, deadline_ms)
    best_selected, best_backups = race_topology_repair(candidates, task_to_idx, courier_to_idx, best_selected, best_backups, deadline_ms)
    return (best_selected, best_backups)

def cheap_backup_preview(candidates, selected, deadline_ms):
    if not CONFIG.get('enable_multi_courier_output', False) or not selected:
        return {}
    max_extra = min(int(CONFIG.get('max_extra_couriers_per_bundle', 0)), int(CONFIG.get('normal_preview_backup_cap', 2)))
    if max_extra <= 0:
        return {}
    scan_cap = int(CONFIG.get('normal_preview_scan_per_primary', 18))
    min_utility = float(CONFIG.get('min_backup_utility', 0.0))
    selected_ids = {id(candidate) for candidate in selected}
    used_couriers = {candidate.courier_id for candidate in selected}
    by_task_str = {}
    for candidate in candidates:
        by_task_str.setdefault(candidate.task_str, []).append(candidate)
    for items in by_task_str.values():
        if CONFIG.get('_runtime_high_noise', False):
            items.sort(key=lambda c: (multi_group_penalty([c]) / max(0.12, c.willingness + 0.12), official_penalty_cost(c), c.score, -c.willingness))
        else:
            items.sort(key=lambda c: (official_penalty_cost(c), c.score, -c.willingness))
    backups = {}
    if CONFIG.get('_runtime_high_noise', False):
        primaries = sorted(selected, key=lambda c: (multi_group_penalty([c]) / max(0.06, c.willingness + 0.06), official_penalty_cost(c), c.score), reverse=True)
    else:
        primaries = sorted(selected, key=lambda c: (multi_group_penalty([c]), official_penalty_cost(c), c.score), reverse=True)
    for primary in primaries:
        if not _has_time(deadline_ms, 32.0):
            break
        group = [primary]
        current_cost = multi_group_penalty(group)
        added = []
        scanned = 0
        for backup in by_task_str.get(primary.task_str, ()):
            if not _has_time(deadline_ms, 24.0):
                break
            if id(backup) in selected_ids or backup.courier_id in used_couriers:
                continue
            scanned += 1
            if scanned > scan_cap:
                break
            next_group = group + [backup]
            new_cost = multi_group_penalty(next_group)
            if current_cost - new_cost > min_utility + EPS:
                added.append(backup)
                used_couriers.add(backup.courier_id)
                group = next_group
                current_cost = new_cost
                if len(added) >= max_extra:
                    break
        if added:
            backups[id(primary)] = added
    return backups

def improve_regular_with_multi_options(candidates, task_to_idx, courier_to_idx, selected, deadline_ms):
    ctx = build_context(candidates, task_to_idx, courier_to_idx)
    total_tasks = len(task_to_idx)
    if total_tasks > 35:
        return improve_regular_legacy_multi_options(candidates, task_to_idx, courier_to_idx, selected, deadline_ms)
    options = []
    seen = set()

    def add_option(option):
        if not option:
            return
        ev = evaluate(option, total_tasks)
        if ev.conflicts:
            return
        signature = tuple(sorted(((c.task_mask, c.courier_idx) for c in option)))
        if signature not in seen:
            seen.add(signature)
            options.append(option)

    def add_order(ordered):
        add_option(greedy_select(ordered))
    add_option(selected)
    baseline = exact_official_baseline(candidates, task_to_idx)
    if baseline is not None:
        add_option(baseline)
    for _name, ordered in base_orders(ctx):
        if not _has_time(deadline_ms, 820.0):
            break
        add_order(ordered)
    add_order(sorted(ctx.candidates, key=lambda c: (c.task_count == 1, official_penalty_cost(c) / max(1, c.task_count), c.score_per_task, -c.willingness, c.score)))
    add_order(sorted(ctx.candidates, key=lambda c: (c.min_task_degree, c.task_count == 1, candidate_penalty_cost(c) / max(1, c.task_count), -c.willingness, c.score)))
    add_order(sorted(ctx.candidates, key=lambda c: (candidate_penalty_cost(c) / max(0.08, c.willingness + 0.08), c.score_per_task, c.score)))
    if CONFIG.get('_runtime_high_noise', False):
        add_order(sorted(ctx.candidates, key=lambda c: (c.task_count == 1, multi_group_penalty([c]) / max(0.1, c.willingness + 0.1), official_penalty_cost(c) / max(1, c.task_count), c.score)))
        add_order(sorted(ctx.candidates, key=lambda c: (c.task_count == 1, c.score_per_task / max(0.14, c.willingness + 0.14), official_penalty_cost(c), -c.willingness)))
        add_order(sorted(ctx.candidates, key=lambda c: (c.min_task_degree, multi_group_penalty([c]) / max(0.08, c.willingness + 0.08), c.score_per_task)))
        for seed, amplitude in ((101, 2.0), (103, 3.5), (107, 5.5), (109, 7.0)):
            if not _has_time(deadline_ms, 610.0):
                break
            rng = random.Random(seed)
            decorated = [(candidate, rng.random()) for candidate in ctx.candidates]
            add_order([candidate for candidate, noise in sorted(decorated, key=lambda item: (multi_group_penalty([item[0]]) / max(0.1, item[0].willingness + 0.1) + (item[1] - 0.5) * amplitude, official_penalty_cost(item[0]), item[0].score))])
    for seed, amplitude in ((19, 1.0), (37, 2.0), (37, 3.0), (53, 2.0), (53, 3.0), (71, 4.0), (7, 2.0), (11, 5.0), (83, 2.8), (97, 4.5)):
        if not _has_time(deadline_ms, 650.0):
            break
        rng = random.Random(seed)
        decorated = [(candidate, rng.random()) for candidate in ctx.candidates]
        add_order([candidate for candidate, noise in sorted(decorated, key=lambda item: (candidate_penalty_cost(item[0]) / max(1, item[0].task_count) + (item[1] - 0.5) * amplitude, candidate_penalty_cost(item[0]), item[0].score))])
    auto_start = _now_ms()
    generated_limit = int(CONFIG.get('normal_topology_generated_limit', 10))
    for index, spec in enumerate(generated_specs()):
        if index >= generated_limit or _now_ms() - auto_start > 220.0 or (not _has_time(deadline_ms, 560.0)):
            break
        add_order(sorted(ctx.candidates, key=strategy_key(ctx, spec)))
    if not options:
        return (selected, {})
    evaluated = []
    saved_realloc_budget = CONFIG.get('backup_reallocation_budget_ms', 0.0)
    try:
        CONFIG['backup_reallocation_budget_ms'] = 0.0
        for option in options:
            if not _has_time(deadline_ms, 430.0):
                break
            backups = cheap_backup_preview(candidates, option, deadline_ms)
            ev = evaluate_with_backups(option, backups, total_tasks)
            evaluated.append((ev.penalty_score, -ev.covered, len(option), option, backups))
    finally:
        CONFIG['backup_reallocation_budget_ms'] = saved_realloc_budget
    if not evaluated:
        return (selected, choose_probability_backups(candidates, selected, deadline_ms))
    best_selected = None
    best_backups = {}
    best_eval = None
    configured_top_k = int(CONFIG.get('normal_topology_top_k', 6))
    top_k = min(configured_top_k, 6 if total_tasks <= 30 else 4)
    top = sorted(evaluated, key=lambda row: (row[0], row[1], row[2]))[:top_k]
    for _score, _neg_covered, _items, option, _preview_backups in top:
        if not _has_time(deadline_ms, 210.0):
            break
        backups = choose_probability_backups(candidates, option, deadline_ms)
        saved_realloc_budget = CONFIG.get('backup_reallocation_budget_ms', 0.0)
        CONFIG['backup_reallocation_budget_ms'] = min(max(float(saved_realloc_budget), 280.0), 560.0)
        try:
            option2, backups2 = improve_backup_allocation(candidates, option, backups, deadline_ms)
        finally:
            CONFIG['backup_reallocation_budget_ms'] = saved_realloc_budget
        ev = evaluate_with_backups(option2, backups2, total_tasks)
        if is_better(ev, best_eval):
            best_selected = option2
            best_backups = backups2
            best_eval = ev
    if best_selected is None:
        best_selected = selected
        best_backups = choose_probability_backups(candidates, selected, deadline_ms)
    best_selected, best_backups = race_topology_repair(candidates, task_to_idx, courier_to_idx, best_selected, best_backups, deadline_ms)
    return (best_selected, best_backups)

def improve_scarce_with_multi_options(candidates, task_to_idx, courier_to_idx, selected, deadline_ms):
    ctx = build_context(candidates, task_to_idx, courier_to_idx)
    total_tasks = len(task_to_idx)
    options = []
    seen = set()

    def add_option(option):
        if not option:
            return
        ev = evaluate(option, total_tasks)
        if ev.conflicts:
            return
        signature = tuple(sorted(((c.task_mask, c.courier_idx) for c in option)))
        if signature not in seen:
            seen.add(signature)
            options.append(option)
    add_option(selected)
    baseline = exact_official_baseline(candidates, task_to_idx)
    if baseline is not None:
        add_option(baseline)
    for _name, ordered in base_orders(ctx):
        if not _has_time(deadline_ms, 700.0):
            break
        add_option(greedy_select(ordered))
    for _name, ordered in perturbation_orders(ctx):
        if not _has_time(deadline_ms, 550.0):
            break
        add_option(greedy_select(ordered))
    auto_start = _now_ms()
    for index, spec in enumerate(generated_specs()):
        if index >= 8 or _now_ms() - auto_start > 160.0 or (not _has_time(deadline_ms, 450.0)):
            break
        add_option(greedy_select(sorted(ctx.candidates, key=strategy_key(ctx, spec))))
    if not options:
        return (selected, {})
    saved_backup_budget = CONFIG.get('backup_time_budget_ms', 0.0)
    saved_realloc_budget = CONFIG.get('backup_reallocation_budget_ms', 0.0)
    evaluated = []
    try:
        CONFIG['backup_time_budget_ms'] = min(float(saved_backup_budget), 180.0)
        CONFIG['backup_reallocation_budget_ms'] = 0.0
        for option in options:
            if not _has_time(deadline_ms, 280.0):
                break
            backups = choose_probability_backups(candidates, option, deadline_ms)
            ev = evaluate_with_backups(option, backups, total_tasks)
            evaluated.append((ev.penalty_score, ev.covered, option, backups))
    finally:
        CONFIG['backup_time_budget_ms'] = saved_backup_budget
        CONFIG['backup_reallocation_budget_ms'] = saved_realloc_budget
    if not evaluated:
        return (selected, choose_probability_backups(candidates, selected, deadline_ms))
    best_selected = None
    best_backups = {}
    best_eval = None
    top = sorted(evaluated, key=lambda x: (x[0], -x[1]))[:8]
    for _score, _covered, option, _preview_backups in top:
        if not _has_time(deadline_ms, 180.0):
            break
        backups = choose_probability_backups(candidates, option, deadline_ms)
        saved_realloc_budget = CONFIG.get('backup_reallocation_budget_ms', 0.0)
        CONFIG['backup_reallocation_budget_ms'] = min(float(saved_realloc_budget), 260.0)
        try:
            option2, backups2 = improve_backup_allocation(candidates, option, backups, deadline_ms)
        finally:
            CONFIG['backup_reallocation_budget_ms'] = saved_realloc_budget
        ev = evaluate_with_backups(option2, backups2, total_tasks)
        if is_better(ev, best_eval):
            best_selected = option2
            best_backups = backups2
            best_eval = ev
    if best_selected is None:
        best_selected = selected
        best_backups = choose_probability_backups(candidates, selected, deadline_ms)
    best_selected, best_backups = race_topology_repair(candidates, task_to_idx, courier_to_idx, best_selected, best_backups, deadline_ms)
    return (best_selected, best_backups)

def improve_backup_allocation(candidates, selected, backup_map, deadline_ms):
    budget = float(CONFIG.get('backup_reallocation_budget_ms', 0.0))
    if budget <= 0.0 or not selected or (not _has_time(deadline_ms, 100.0)):
        return (selected, backup_map)
    started_ms = _now_ms()
    allocations = [[primary] + list(backup_map.get(id(primary), ())) for primary in selected]
    group_by_task_str = {group[0].task_str: index for index, group in enumerate(allocations)}
    max_members = int(CONFIG.get('max_extra_couriers_per_bundle', 0)) + 1
    edge_by_group_courier = {}
    for candidate in candidates:
        group_index = group_by_task_str.get(candidate.task_str)
        if group_index is None:
            continue
        key = (group_index, candidate.courier_id)
        previous = edge_by_group_courier.get(key)
        if previous is None or official_penalty_cost(candidate) < official_penalty_cost(previous):
            edge_by_group_courier[key] = candidate

    def group_cost(group):
        return multi_group_penalty(group)
    while _has_time(deadline_ms, 80.0) and _now_ms() - started_ms < budget:
        costs = [group_cost(group) for group in allocations]
        assigned_couriers = {candidate.courier_id for group in allocations for candidate in group}
        best_delta = -EPS
        best_change = None
        for (group_index, courier_id), candidate in edge_by_group_courier.items():
            if courier_id in assigned_couriers or len(allocations[group_index]) >= max_members:
                continue
            delta = group_cost(allocations[group_index] + [candidate]) - costs[group_index]
            if delta < best_delta:
                best_delta = delta
                best_change = ('add', group_index, candidate)
        for source_index, source in enumerate(allocations):
            if len(source) <= 1:
                continue
            for position, candidate in enumerate(source):
                smaller_source = source[:position] + source[position + 1:]
                smaller_cost = group_cost(smaller_source)
                for target_index, target in enumerate(allocations):
                    if source_index == target_index or len(target) >= max_members:
                        continue
                    alternative = edge_by_group_courier.get((target_index, candidate.courier_id))
                    if alternative is None:
                        continue
                    delta = smaller_cost + group_cost(target + [alternative]) - costs[source_index] - costs[target_index]
                    if delta < best_delta:
                        best_delta = delta
                        best_change = ('move', source_index, position, target_index, alternative)
        flat = [(group_index, position, candidate) for group_index, group in enumerate(allocations) for position, candidate in enumerate(group)]
        for first in range(len(flat)):
            left_group, left_position, left_candidate = flat[first]
            for second in range(first + 1, len(flat)):
                right_group, right_position, right_candidate = flat[second]
                if left_group == right_group:
                    continue
                new_left = edge_by_group_courier.get((left_group, right_candidate.courier_id))
                new_right = edge_by_group_courier.get((right_group, left_candidate.courier_id))
                if new_left is None or new_right is None:
                    continue
                left = list(allocations[left_group])
                right = list(allocations[right_group])
                left[left_position] = new_left
                right[right_position] = new_right
                delta = group_cost(left) + group_cost(right) - costs[left_group] - costs[right_group]
                if delta < best_delta:
                    best_delta = delta
                    best_change = ('swap', left_group, left_position, new_left, right_group, right_position, new_right)
        if best_change is None:
            break
        if best_change[0] == 'add':
            _, group_index, candidate = best_change
            allocations[group_index].append(candidate)
        elif best_change[0] == 'move':
            _, source_index, position, target_index, candidate = best_change
            allocations[source_index].pop(position)
            allocations[target_index].append(candidate)
        else:
            _, left_group, left_position, new_left, right_group, right_position, new_right = best_change
            allocations[left_group][left_position] = new_left
            allocations[right_group][right_position] = new_right
    improved_selected = []
    improved_backups = {}
    for group in allocations:
        primary = group[0]
        improved_selected.append(primary)
        if len(group) > 1:
            improved_backups[id(primary)] = group[1:]
    return (improved_selected, improved_backups)

def tiny_small_backup_polish(candidates, task_to_idx, courier_to_idx, selected, backup_map, deadline_ms):
    total_tasks = len(task_to_idx)
    if total_tasks <= 0 or total_tasks > 15 or not _has_time(deadline_ms, 180.0):
        return (selected, backup_map)
    idx_to_task = [None] * total_tasks
    for task_id, idx in task_to_idx.items():
        if idx < total_tasks:
            idx_to_task[idx] = task_id
    tasks = [task_id for task_id in idx_to_task if task_id is not None]
    if len(tasks) != total_tasks:
        return (selected, backup_map)
    edge = {}
    full_couriers = []
    for c in candidates:
        if c.task_count != 1:
            continue
        task_id = c.task_ids[0]
        old = edge.get((task_id, c.courier_id))
        if old is None or multi_group_penalty([c]) < multi_group_penalty([old]):
            edge[(task_id, c.courier_id)] = c
        if c.courier_id not in full_couriers:
            full_couriers.append(c.courier_id)
    couriers = [courier_id for courier_id in full_couriers if all((task_id, courier_id) in edge for task_id in tasks)]
    if len(couriers) < total_tasks or len(couriers) > 42:
        return (selected, backup_map)
    current_eval = evaluate_with_backups(selected, backup_map or {}, total_tasks)
    best_selected = list(selected)
    best_backups = dict(backup_map or {})
    best_eval = current_eval
    cost_cache = {}
    def g_cost(task_id, courier_ids):
        if not courier_ids:
            return float(CONFIG.get('acceptance_penalty', 100.0))
        key = (task_id, tuple(sorted(courier_ids)))
        value = cost_cache.get(key)
        if value is not None:
            return value
        group = [edge[(task_id, courier_id)] for courier_id in courier_ids]
        value = multi_group_penalty(group)
        cost_cache[key] = value
        return value
    def materialize(groups):
        ns = []
        nb = {}
        for task_id in tasks:
            ids = list(groups.get(task_id, ()))
            if not ids:
                return (None, None)
            group = sorted((edge[(task_id, courier_id)] for courier_id in ids), key=lambda c: (c.score, -c.willingness, c.courier_id))
            primary = group[0]
            ns.append(primary)
            if len(group) > 1:
                nb[id(primary)] = group[1:]
        return (ns, nb)
    def try_update(groups):
        nonlocal best_selected, best_backups, best_eval
        ns, nb = materialize(groups)
        if ns is None:
            return False
        ev = evaluate_with_backups(ns, nb, total_tasks)
        if is_better(ev, best_eval) and ev.penalty_score < current_eval.penalty_score - 0.5:
            best_selected, best_backups, best_eval = ns, nb, ev
            return True
        return False
    groups = {task_id: [] for task_id in tasks}
    used = set()
    ok_current = True
    for primary in selected:
        if primary.task_count != 1:
            ok_current = False
            break
        task_id = primary.task_ids[0]
        if task_id not in groups:
            ok_current = False
            break
        group_ids = [primary.courier_id] + [b.courier_id for b in (backup_map or {}).get(id(primary), ()) if b.task_count == 1 and b.task_ids[0] == task_id]
        for courier_id in group_ids:
            if courier_id in used or (task_id, courier_id) not in edge:
                ok_current = False
                break
            used.add(courier_id)
        if not ok_current:
            break
        groups[task_id].extend(group_ids)
    if not ok_current or any(not groups[task_id] for task_id in tasks):
        groups = {task_id: [] for task_id in tasks}
        used = set()
        for task_id in tasks:
            pool = [edge[(task_id, courier_id)] for courier_id in couriers if courier_id not in used]
            if not pool:
                return (selected, backup_map)
            c = min(pool, key=lambda x: (multi_group_penalty([x]), x.score, -x.willingness))
            groups[task_id].append(c.courier_id)
            used.add(c.courier_id)
    unused = [courier_id for courier_id in couriers if courier_id not in used]
    for courier_id in list(unused):
        best_delta = -EPS
        best_task = None
        for task_id in tasks:
            delta = g_cost(task_id, groups[task_id] + [courier_id]) - g_cost(task_id, groups[task_id])
            if delta < best_delta:
                best_delta = delta
                best_task = task_id
        if best_task is not None:
            groups[best_task].append(courier_id)
            unused.remove(courier_id)
    try_update(groups)
    if not _has_time(deadline_ms, 260.0):
        return (best_selected, best_backups)
    base_groups = {task_id: list(groups[task_id]) for task_id in tasks}
    start_ms = _now_ms()
    budget = 6800.0 if total_tasks > 8 else 1200.0
    seed = 0
    best_groups = {task_id: list(groups[task_id]) for task_id in tasks}
    best_value = sum(g_cost(task_id, best_groups[task_id]) for task_id in tasks)
    while _now_ms() - start_ms < budget and _has_time(deadline_ms, 180.0):
        rng = random.Random(1009 + seed)
        seed += 1
        cur = {task_id: list((base_groups if seed % 3 == 0 else best_groups)[task_id]) for task_id in tasks}
        cur_value = sum(g_cost(task_id, cur[task_id]) for task_id in tasks)
        temperature = 5.0
        inner_end = min(start_ms + budget, _now_ms() + 220.0)
        while _now_ms() < inner_end and _has_time(deadline_ms, 120.0):
            task_of = {courier_id: task_id for task_id in tasks for courier_id in cur[task_id]}
            if len(task_of) < total_tasks:
                break
            if rng.random() < 0.55:
                courier_id = rng.choice(list(task_of.keys()))
                src = task_of[courier_id]
                if len(cur[src]) <= 1:
                    continue
                dst = rng.choice([task_id for task_id in tasks if task_id != src])
                old = g_cost(src, cur[src]) + g_cost(dst, cur[dst])
                next_src = [x for x in cur[src] if x != courier_id]
                next_dst = cur[dst] + [courier_id]
                delta = g_cost(src, next_src) + g_cost(dst, next_dst) - old
                if delta < 0.0 or rng.random() < pow(2.718281828, -delta / max(0.001, temperature)):
                    cur[src] = next_src
                    cur[dst] = next_dst
                    cur_value += delta
            else:
                keys = list(task_of.keys())
                if len(keys) < 2:
                    continue
                left, right = rng.sample(keys, 2)
                lt = task_of[left]
                rt = task_of[right]
                if lt == rt:
                    continue
                old = g_cost(lt, cur[lt]) + g_cost(rt, cur[rt])
                next_left = [right if x == left else x for x in cur[lt]]
                next_right = [left if x == right else x for x in cur[rt]]
                delta = g_cost(lt, next_left) + g_cost(rt, next_right) - old
                if delta < 0.0 or rng.random() < pow(2.718281828, -delta / max(0.001, temperature)):
                    cur[lt] = next_left
                    cur[rt] = next_right
                    cur_value += delta
            temperature *= 0.999
            if cur_value < best_value - EPS:
                best_value = cur_value
                best_groups = {task_id: list(cur[task_id]) for task_id in tasks}
                try_update(best_groups)
    while _has_time(deadline_ms, 120.0):
        current_costs = {task_id: g_cost(task_id, best_groups[task_id]) for task_id in tasks}
        task_of = {courier_id: task_id for task_id in tasks for courier_id in best_groups[task_id]}
        best_delta = -EPS
        best_op = None
        keys = list(task_of.keys())
        for courier_id in keys:
            src = task_of[courier_id]
            if len(best_groups[src]) <= 1:
                continue
            next_src = [x for x in best_groups[src] if x != courier_id]
            src_cost = g_cost(src, next_src)
            for dst in tasks:
                if dst == src:
                    continue
                delta = src_cost + g_cost(dst, best_groups[dst] + [courier_id]) - current_costs[src] - current_costs[dst]
                if delta < best_delta:
                    best_delta = delta
                    best_op = ('move', courier_id, src, dst)
        for i, left in enumerate(keys):
            lt = task_of[left]
            for right in keys[i + 1:]:
                rt = task_of[right]
                if lt == rt:
                    continue
                next_left = [right if x == left else x for x in best_groups[lt]]
                next_right = [left if x == right else x for x in best_groups[rt]]
                delta = g_cost(lt, next_left) + g_cost(rt, next_right) - current_costs[lt] - current_costs[rt]
                if delta < best_delta:
                    best_delta = delta
                    best_op = ('swap', left, lt, right, rt)
        if best_op is None:
            break
        if best_op[0] == 'move':
            _, courier_id, src, dst = best_op
            best_groups[src].remove(courier_id)
            best_groups[dst].append(courier_id)
        else:
            _, left, lt, right, rt = best_op
            best_groups[lt].remove(left)
            best_groups[lt].append(right)
            best_groups[rt].remove(right)
            best_groups[rt].append(left)
        try_update(best_groups)
    return (best_selected, best_backups)

def format_solution(selected, backup_map=None):
    backup_map = backup_map or {}
    solution = []
    for c in selected:
        group = ordered_group([c] + list(backup_map.get(id(c), ())))
        couriers = [candidate.courier_id for candidate in group]
        solution.append((c.task_str, couriers))
    return solution

def solve(input_text: str) -> list:
    _GROUP_COST_CACHE.clear()
    candidates, task_to_idx, courier_to_idx = parse_input(input_text)
    if not candidates:
        return []
    configure_runtime(candidates, task_to_idx, courier_to_idx)
    saved_overrides = apply_runtime_overrides()
    try:
        deadline_ms = _now_ms() + CONFIG['time_budget_ms'] - CONFIG['safety_margin_ms']
        primary_deadline_ms = deadline_ms
        primary_budget_ms = float(CONFIG.get('multi_primary_time_budget_ms', 0.0))
        if primary_budget_ms > 0.0:
            primary_deadline_ms = min(deadline_ms, _now_ms() + primary_budget_ms)
        selected = choose_solution(candidates, task_to_idx, courier_to_idx, primary_deadline_ms)
        if not CONFIG.get('enable_multi_courier_output', False):
            return format_solution(selected)
        if CONFIG.get('_runtime_low_willingness', False):
            selected, backup_map = improve_low_with_multi_options(candidates, task_to_idx, courier_to_idx, selected, deadline_ms)
            selected, backup_map = improve_backup_allocation(candidates, selected, backup_map, deadline_ms)
            selected, backup_map = race_topology_repair(candidates, task_to_idx, courier_to_idx, selected, backup_map, deadline_ms)
            selected, backup_map = prefer_official_baseline_when_better(candidates, task_to_idx, selected, backup_map)
            return format_solution(selected, backup_map)
        if CONFIG.get('_runtime_case_type', 'normal') == 'normal':
            selected, backup_map = improve_regular_with_multi_options(candidates, task_to_idx, courier_to_idx, selected, deadline_ms)
            if len(task_to_idx) <= 15:
                selected, backup_map = tiny_small_backup_polish(candidates, task_to_idx, courier_to_idx, selected, backup_map, deadline_ms)
            return format_solution(selected, backup_map)
        selected, backup_map = improve_scarce_with_multi_options(candidates, task_to_idx, courier_to_idx, selected, deadline_ms)
        selected, backup_map = prefer_official_baseline_when_better(candidates, task_to_idx, selected, backup_map)
        return format_solution(selected, backup_map)
    finally:
        restore_runtime_overrides(saved_overrides)
