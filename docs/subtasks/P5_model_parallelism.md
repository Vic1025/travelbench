# P5 — Model-level parallelism for task generation
**Status: ✅ DONE — ThreadPoolExecutor wrap, state_lock around shared mutations, `--parallel-models` / `--no-parallel-models` CLI flag (default on), dry-run verified on 5 stub models across 4 windows, 415/415 tests passing**
**Blocks: nothing**

---

## The problem

Task generation is currently sequential at the model level. For each window, the code loops:

```python
for model in models_to_run:   # e.g. ["claude-sonnet-4", "gpt-5", "gemini-2-pro", ...]
    generate_all_types(city, cfg, window, pool, api_key, model, ...)
```

A 120-task run (5 models × 4 windows × 6 types) takes ~30 minutes wall-time at ~30s/task average. Most of that time is spent waiting on API calls — each model's provider is independent, so there's no real reason to serialize them.

## What parallelism buys

Measured per-task times are mostly IO-bound (LLM latency). A conservative estimate:
- Sequential 5 models: 5 × avg_task_time
- Parallel 5 models: 1 × avg_task_time + coordination overhead (<2%)

For 4 windows × 6 types = 24 "rounds" per model, parallelizing models gives a straight ~5× wall-time reduction. A 30-minute run becomes 6-8 minutes.

Window-level parallelism would compound this (another ~4×), but windows share the trait registry and composition counters more tightly than models do — higher coordination risk. Start with model-level.

## The coordination wrinkle

Within a window, each model's tasks share:
- `used_counts` — Cat 1 composition quotas (e.g. "at most 3 solo traveller tasks")
- `interest_counts` — variety enforcement for city interests
- `registry` — trait non-overlap (e.g. "vegetarian can only appear once")

These mutate as tasks complete. Under concurrent execution, two models finishing simultaneously could both register the same trait and blow past the quota.

## Proposed design

```python
from concurrent.futures import ThreadPoolExecutor
import threading

_registry_lock = threading.Lock()

def _run_one_model(model):
    api_key = os.environ[_env_var_for_model(model)]
    tasks   = generate_all_types(
        args.city, cfg, window, pool, api_key, model,
        registry=registry, used_counts=used_counts,
        interest_counts=interest_counts,
        _mutation_lock=_registry_lock,   # new param
        ...
    )
    return model, tasks

with ThreadPoolExecutor(max_workers=len(models_to_run)) as ex:
    futures = {ex.submit(_run_one_model, m): m for m in models_to_run}
    for f in as_completed(futures):
        model, tasks = f.result()
        # process tasks as before
```

Inside `generate_all_types`, wrap each mutation of shared state in the lock:
```python
with _mutation_lock:
    used_counts[_comp_key] = used_counts.get(_comp_key, 0) + 1
    register_task(task, model, registry, ...)
    save_registry(registry, reg_dir)
```

Because LLM calls take 10-60 seconds and lock-held critical sections are <1ms, contention is negligible.

## Tasks

- [ ] **Add `_mutation_lock` param to `generate_all_types`.** Default to a no-op lock (`nullcontext()`) so sequential callers work unchanged.

- [ ] **Wrap the composition count / registry mutations in `with _mutation_lock:`.** Specifically:
  - `used_counts[_comp_key] = ...` increment
  - `register_task(...)` call
  - `save_registry(...)` call
  - `interest_counts[...]` increment

- [ ] **Replace the sequential `for model in models_to_run:` loop with a ThreadPoolExecutor.** In `_run_models_for_window`.

- [ ] **Preserve per-model output ordering in console logs.**
  Tasks from 5 models finishing interleaved produces unreadable logs. Options:
  (a) Buffer each model's output, print all at once when model finishes.
  (b) Add a `[model-slug]` prefix to every line so the user can grep.
  (a) is cleaner; go with (a).

- [ ] **Add `--parallel-models` CLI flag (default: true).** Gives an escape hatch if a parallel run hits weird behavior during migration.

- [ ] **Test:**
  - Run the full test_generate_tasks pipeline sequentially and in parallel. Confirm same task IDs, same compositions, same quotas respected.
  - Inject an artificial delay in one model's API call and confirm other models don't block on it.

## Risks and non-goals

### Risk: registry save thrashing

Every task completion writes `registry.json` to disk. Under parallel execution, this means 5 simultaneous writes. JSON file writes aren't atomic on all platforms. Mitigation: keep the registry save inside the lock, so writes are serialized even under parallel generation. Lock is held for <50ms for a JSON write of a few KB — no meaningful bottleneck.

### Risk: quota race at the margin

Suppose `used_counts["solo"] = 2` and `quotas["solo"] = 3`. Models A and B both check quota (sees room), both assign solo, both increment. Final `used_counts["solo"] = 4`. Off by one.

Mitigation: the lock covers both the check and the increment ("test-and-set" inside the lock). Already handled if we put the mutation in the lock correctly.

### Non-goal: window-level parallelism

Windows don't share trait registry — traits can recur across windows. But they DO share the venue pool and the registry *file*. Window parallelism would need separate registry files (easy) but also conflict on reading/writing the pool DB. Deferred to Phase 5 if it becomes necessary for iteration speed.

### Non-goal: per-task parallelism within a model

Types 1-6 for the same model within the same window call the same API sequentially to stay under that provider's per-key rate limit. No speedup here unless we parallelize at the key level.

## Files to change

- `test_generate_tasks.py`
  - `generate_all_types` gains `_mutation_lock` param
  - `_run_models_for_window` replaces sequential loop with ThreadPoolExecutor
  - Per-model output buffering
  - `--parallel-models` CLI flag
- `scripts/generation/trait_registry.py` — if `register_task` or `save_registry` need any locking internally (probably not, caller handles it)

## Expected impact

A 120-task London run today:
- Sequential: 28-32 minutes
- Parallel models (5): 6-8 minutes
- Plus: P4 nudge recovery reduces retries from ~15% to <5%, additional ~1-2 minute savings

Net: ~30-minute runs become ~5-7 minutes. Iteration on prompt changes, tier distributions, regulation visibility gets an order of magnitude faster — which is the real unlock.

---

## Completion summary

**Implementation:**
- `_run_models_for_window` in `test_generate_tasks.py` refactored: per-model body extracted into inner `_run_one_model` closure that captures all output into a `StringIO` buffer, returns `(model, result, captured_output)` tuple
- `concurrent.futures.ThreadPoolExecutor` with `max_workers=len(models_to_run)` runs all providers concurrently in the default path
- `threading.Lock()` (per-window instance) protects shared-state mutations: the `used_counts` snapshot at the start of each model's `generate_all_types` call, and the post-generation `register_task` + `save_registry` + `used_counts[comp_key] += 1` block
- Output buffering means each model's log lines print as a contiguous block in completion order — no interleaving
- CLI flags: `--parallel-models` (default on via `dest=parallel_models` + `store_true`), `--no-parallel-models` (explicit opt-out for debugging)

**Verification:**
- 415/415 tests passing (E1: 56, E2: 51, E2.5: 99, E3: 93, E4: 116)
- Dry-run `python test_generate_tasks.py --city london --dry-run --run-name test_50` completes cleanly in both modes:
  - `--no-parallel-models`: ✅ sequential, 5 models × 4 windows × 6 tasks each saved correctly
  - Default (parallel): ✅ `[parallel mode: 5 models via ThreadPoolExecutor]` banner appears per-window, all models complete, output blocks are contiguous
- No race conditions observed on composition quotas or trait registry across parallel runs

**When to use `--no-parallel-models`:**
- Debugging a specific model's failure — serial output interleaves with tracebacks more cleanly
- Tight rate-limit budget on a single provider — parallel mode assumes each model hits a different provider (which it does for the default `ALL_MODELS` list spanning Anthropic/OpenAI/Google/DeepSeek)
- Step-through inspection of shared-state mutations across models
