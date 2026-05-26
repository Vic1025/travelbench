# Engine migration TODO

> ⚠ **Older design doc.** Preserved as historical reference for design rationale.
> **Current implementation status:** see [docs/TODO_PHASE6.md](TODO_PHASE6.md) for the
> live work tracker, including any items in this doc that have been built, modified,
> or superseded.

**Goal:** make the generic constraint engine the single bottom wheel for BOTH scoring (plan → pass/fail) and validation (rule → pool support check). Delete redundant per-pattern handlers. Fill in validation logic on top of the unified engine.

**Status:** ✅ Phase 1 complete · ✅ Phase 2 complete · ✅ Phase 2.5 complete · ✅ Phase 3a complete · ✅ Phase 3b complete · ✅ Phase 4 complete

---

## The four phases

### Phase 1: Understand the validation logic ✅ COMPLETE

**Purpose:** before touching code, write down in one place what every validation question we want to answer looks like, translated into scope/condition/aggregation terms. This is the design artifact that tells us if the engine has gaps.

**Scope:**
- Walk each validation concern: scope_mode-style narrowing (universal/scoped/inclusion narrowing), the 50/50/25 upper bars, the viable-schedule lower bar, tag-category affinity, P7 time feasibility, P7 budget feasibility, tension detection (the A×A and B×B math).
- For each: express it as either (a) "a query against the engine's predicates" or (b) "pure math on top of engine outputs" or (c) "external to the engine, stays its own module."
- Identify what engine primitives are needed. Expected primitives: the two validation-facing helpers (`venues_matching`, `venues_in_scope`) plus the existing scoring path.

**Deliverable:** `docs/ENGINE_MIGRATION_DESIGN.md` — the map. ✅ written, exported

---

### Phase 2: Complete the engine ✅ COMPLETE

**Purpose:** add the missing engine features identified in Phase 1.

**Final set of additions** (what was actually made, vs. initial expectations):

| Addition | Status | Notes |
|---|---|---|
| Validation-facing helpers `venues_matching` + `venues_in_scope` | ✅ added | Core Phase 4 primitives |
| Nested `regulations` dict field access | ✅ added as `_get_field_value` helper | Flat venue → regulations dict → activity, in order |
| `{"any_of": [...]}` scope marker for explicit union | ✅ added | Needed after discovering `_translate_scope_for_pool` had a latent list-semantics bug (produced lists with OR intent, but `_activity_matches_scope` reads lists as AND) |
| Per-venue averaging for `aggregation: "all"` (fractional) | ❌ descoped | Strict binary pass/fail kept; we're not committing to fractional scoring yet |
| Flag-based partial credit | ❌ descoped | "Agent flagged a failure" is a half-written design; will revisit when we see real benchmark data |

**Key design decisions locked during Phase 2:**
- **Aggregation is boolean-producing.** Every aggregation mode (`all`, `none`, `at_least`, `at_most`, `count_distinct`, `ratio`, `sum`) produces a pass/fail verdict. Numeric aggregations do their own comparison internally before returning boolean. Engine score is 1.0 / 0.0 (plus 0.5 for `at_least` partial progress, pre-existing).
- **Empty condition defaults to pass.** `_activity_satisfies_condition(act, {}, venues)` returns True. Used by `venues_matching(pool, scope, {})` to mean "scope-filter only." **Phase 4 must add a validator rule: a constraint cannot have an empty condition** — submit-time schema check.
- **Scope has three shapes:** string specs like `"activity_type=meal"`, lists-as-AND, and dict `{"any_of": [...]}` for explicit unions. List-default-AND matches the design doc; any_of marker makes OR explicit where needed.
- **`_translate_scope_for_pool` now preserves AND/OR correctly.** `activity_type=meal` translates to an `any_of` dict of three food categories (union), not a raw list (which would have been AND'd incorrectly).

**Engine file state:** `scripts/generation/constraint_engine.py` went from 357 → 476 lines. ~120 net added across the two validation helpers, `_get_field_value`, scope-matcher `any_of` handling, and an improved `_translate_scope_for_pool`.

**Test coverage:** 40 new tests in `test_e3.py` (165 → 180 E3 tests). Full suite 502/502 passing.

---

### Phase 2.5: Unify tags/labels field name ✅ COMPLETE

**Purpose:** fix latent bug where evaluator stores venue tags under `v["labels"]` while engine reads `v["tags"]`. Without this fix, Phase 3 migration would produce silently-zero tag matches for every real-run task. Scope deliberately narrow: only rename the field; nested `regulations` shape stays as-is (too invasive to flatten, agent-facing API depends on it).

**Changes:**
- `eval/evaluator.py::load_ground_truth` — one line: write `v["tags"]` instead of `v["labels"]`
- `eval/evaluator.py` — 7 handler read sites: rename `venues[...].get("labels")` → `...get("tags")`
- `scripts/regression_test.py:96` — rename fixture `"labels": []` → `"tags": []`
- `scripts/generation/test_e3.py:990` — rename fixture for consistency

**NOT touched:**
- `scripts/generate_sources.py` — stub/legacy pipeline, self-consistent, writes fixtures used only by demo/regression modes; tech debt but doesn't impact live pipeline
- `scripts/generate_city.py:174` — LLM-prompt text describing venue shape; agents write to DB where tags live in a separate table, so the prompt wording doesn't affect data layout
- All `regulations: {...}` nested dict code — kept; engine's `_get_field_value` already reads nested regulations via fallback

**Checkpoint:**
- Full test suite 502/502 green
- `regression_test.py` passes

**Estimated effort:** ~30-45 min

---

### Phase 3: Migrate tasks to engine directly — no translator layer

**Purpose:** migrate all scoring to use the engine directly. Rewrite tasks on disk to the generic schema. Delete Bucket A+B handlers. Rewrite agent handbook to teach the generic schema. End state: one shape on disk, one engine, no translator, no Bucket A/B handlers.

The safety net is the user's duplicate repo — if anything breaks catastrophically, revert from there. No runtime translation layer needed.

---

#### Phase 3a: Migrate tasks on disk + delete legacy handlers ✅ COMPLETE

**What was done:**

1. **Pattern classification table** (`docs/PATTERN_TO_GENERIC.md`): all 28 handlers classified into Bucket A (21, migrate) and Bucket C (7, keep as handlers). Served as the authoritative migration spec.

2. **Engine additions** before migration:
   - `venue_id=X` and `venue_name~X` scope primitives (for `must_visit`, `opening_time_window`)
   - `at_most_distinct` aggregation (for `district_count_max`)
   - Null-match operator — `value: null` with `==`/`!=` matches absent fields (for `min_age`)
   - `DRESS_CODE_ORDER` ordered-enum comparison (for `dress_code_required`)
   - `"contains"` operator — mirror of `"in"`, checks list-field contains scalar (for `social_match`)
   - `at_least_days` aggregation — "at least N complete days where all scoped activities satisfy condition" (for `pace_relaxed`)
   - Fixed empty-matching short-circuit: `{at_least: N≥1}` with no scope-matches now scores 0.0, not N/A 1.0

3. **Prompt + task data fixes** (found during migration dry-run):
   - `generate_task.py:508` prompt corrected: `max_age_restriction` → `group_min_age` (handler had been reading the wrong key, silently mis-scoring every `min_age` constraint)
   - `par_gen_005.json` constraint `p_004` repaired to match
   - `price_tier_required`: time_window label → HH:MM range translation added (`TIME_WINDOW_LABELS` map)
   - `pace_relaxed` translation corrected to use `at_least_days` (old translation was wrong)
   - Full audit of prompt vs handler param names; all silent-fail mechanisms documented and eliminated at migration boundary

4. **Migration script** (`scripts/migration/migrate_tasks_to_generic.py`):
   - 21 per-pattern translator functions
   - `MigrationError` with task + constraint id context on any invalid params
   - Ran clean on 66 tasks (171 P-constraints: 134 → generic schema, 37 preserved as Bucket C)
   - Script deleted after successful run

5. **Evaluator dispatch rewired** (`eval/evaluator.py`):
   - Engine is default for constraints carrying `scope`/`condition`/`aggregation`
   - Bucket C patterns route through `P_SCORE_HANDLERS` (7 entries only)
   - 21 Bucket A handler functions deleted (~637 lines)
   - `OPERATORS`, `DRESS_CODE_ORDER` dead constants removed

6. **`_apply_pool_filters` rewritten** (`scripts/generation/pool_utils.py`):
   - Now uses `venues_matching` for all generic-schema constraints
   - `"all"` aggregation → filter pool (universal constraint)
   - `"none"` aggregation → exclude matching venues (exclusion constraint)
   - `{at_least: N}` and other counting aggregations → no pool filtering (reachability check only)

7. **`_verify_task_solvable` updated** (`scripts/generation/generate_task.py`):
   - Reachability checks now read generic schema (scope + condition + aggregation)
   - `k_target` extraction for type2 time-ceiling check reads `{at_least: N}` aggregation
   - Legacy `category_count_minimum`/`label_required` pattern-specific branches replaced

8. **Test fixtures hard-migrated** to generic schema across:
   - `scripts/generation/test_e2.py`, `test_e2_5.py`, `test_e3.py`, `test_b4.py`, `test_b5.py`
   - `test_generate_tasks.py` `validate_task_schema` updated to accept either generic schema OR Bucket C pattern+params shape
   - Pre-existing `test_b5.py` bugs fixed (tuple unpacking, stale `n_samples` kwarg, wrong expectation on pre-set difficulty)

**Checkpoint results:**
- Full test suite: **578/578 green** (E1:56 + E2:51 + E2.5:99 + E3:210 + E4:116 + B4:53 + B5:43)
- 66 authoritative tasks scored cleanly via new dispatch (4-task smoke check, no routing errors)
- Evaluator `eval/evaluator.py` cut by ~637 lines

**Bucket counts:**
- 21 Bucket A handlers deleted
- 7 Bucket C handlers kept: `weather_aware`, `temporal_cross_day`, `dependency_chain`, `consecutive_pairs`, `opening_time_required`, `local_cuisine_preference`, `cuisine_diversity_minimum`

---

#### Phase 3b: Rewrite handbook + smoke test ✅ COMPLETE

**What was done:**

1. **`task_agent.py` handbook rewritten** to teach generic schema:
   - Replaced `pattern`+`params` vocabulary section with full `scope`/`condition`/`aggregation`/`consequence` reference
   - Added scope primitives (all, activity_type=, category=, has_tag=, venue_id=, time_window=, list-AND)
   - Added condition forms (has_tag, not_tag, field comparisons, composite all/any)
   - Added all aggregation modes (all, none, at_least, at_most, at_least_days, count_distinct, at_most_distinct, sum)
   - Added 5 worked examples covering universal, regulation, inclusion, exclusion, scoped+time patterns
   - Updated HOW CONSTRAINTS APPLY section to explain universal vs inclusion semantics correctly
   - Updated all scattered pattern-name references in narrative sections

2. **7 Bucket C patterns documented** in a new SPECIAL-HANDLER section:
   - `local_cuisine_preference` — already used in 36 real tasks, documented with params
   - `cuisine_diversity_minimum` — used in 1 real task, documented
   - `weather_aware`, `opening_time_required`, `temporal_cross_day`, `dependency_chain`, `consecutive_pairs` — documented for the first time; were never in any handbook previously
   - Clear note: these use old `pattern`+`params` format, NOT scope/condition/aggregation

3. **Single-shot pipeline deleted** (per user: one-shot calls no longer used):
   - `generate_task.py`: deleted `SYSTEM_PROMPT` (475-line prompt), `_build_task_prompt`, `generate_task_llm`, `generate_tasks_all_windows` (~238 lines removed)
   - `test_generate_tasks.py`: deleted `generate_tasks_llm`, `_call_single_task_llm`, single_shot branches in `generate_all_types` (~255 lines removed)
   - All test files updated to remove imports/tests of deleted functions

4. **Architecture clarified**: `generate_task.py` is the orchestrator that calls `task_agent.py`'s `run_task_agent` for each task. The old one-shot path bypassed the agent loop entirely. Both were confirmed before deletion.

**Checkpoint results:**
- Full test suite: **611/611 green** (E1:56 + E2:45 + E2.5:95 + E3:210 + E4:116 + B4:47 + B5:43)
- Handbook teaches generic schema as the primary constraint format
- All 7 Bucket C patterns documented with their params
- Old single-shot prompt (which still taught pattern+params Bucket A vocabulary) is gone

**Smoke test (Phase 3b task generation):**
- `run_task_agent` requires a live API key + DB — not runnable in this session
- The SUBMIT dispatch and validation are confirmed working via Phase 3a smoke check (4 real tasks scored cleanly end-to-end)
- Handbook coherence confirmed by reading

---

#### Phase 3b dependency graph update

### Phase 4: Shift validation — use engine primitives, fill in missing logic

**Purpose:** rebuild validation on top of the engine's primitives. Fix the broken tension detection. Implement scope-aware pool splitting and the 50/50/25 upper bars. Add viable-schedule lower bar. Add tag-category affinity. Fix the `evaluate()` end-to-end wrapper.

**Key finding pre-Phase-4:** 3 of the 7 Bucket C patterns were misclassified — they ARE expressible in the engine. Migrate them first, reducing Bucket C from 7 to 4 before doing validation work. The remaining 4 are genuinely irreducible (external state, cross-activity relational, adjacent-pair).

---

#### Phase 4 pre-work: migrate 3 misclassified Bucket C patterns

**`local_cuisine_preference`** → `ratio` aggregation on `local_cuisine == 1` field. Zero engine changes needed. Migrate 36 real tasks.

**`cuisine_diversity_minimum`** → add `cuisine_label` derived field to `load_city_pool` (first tag matching CUISINE_TYPES set, ~5 lines). Then standard `count_distinct`. Migrate 1 real task.

**`temporal_cross_day`** → three sub-cases all expressible today:
- `scope=day` → `scope: "per_day"` + `condition: {field==value}` + `aggregation: {at_most: N}`
- `scope=trip` + specific value → `scope: "all"` + `condition: {field==value}` + `aggregation: {at_most: N}`
- `value=__any__` → `scope: "all"` + `condition: {}` + `aggregation: {at_most_distinct: N, field: X}`

Migrate 0 real tasks (none in authoritative set).

For all three: delete handler, remove from P_SCORE_HANDLERS, move from SPECIAL-HANDLER section to generic schema section in handbook, update generate_task.py prompt references.

---

#### Phase 4 Step 1: Fix tension detection (~2h)

Current tension logic uses `_SEMANTIC_TENSION_PAIRS` whitelist + `_venues_passing` function — both use old `pattern`+`params` format. Every migrated task will now wrongly fail or pass tension checks.

**Step 1a: A×A tension (overlap metric) — type 3 only**

For each pair (i, j) of constraints where both have a `"all"` or `"none"` aggregation (i.e., venue-pool-level):
- Compute `set_i = {v.venue_id for v in venues_matching(pool, scope_i, condition_i)}`
- Compute `set_j = {v.venue_id for v in venues_matching(pool, scope_j, condition_j)}`
- Both sets must have ≥ 3 venues
- Tension if `|set_i ∩ set_j| ≤ 3` absolute OR `≤ 25% of smaller set`

A×A only fires for type 3. Delete `_SEMANTIC_TENSION_PAIRS` and `_venues_passing`, replace with `venues_matching` calls.

**Step 1b: B×B tension (plan-space narrowing) — type 3 only**

For each pair (i, j) where one or both constraints have a counting aggregation (`at_least`, `at_most`, `count_distinct`, `at_most_distinct`, `ratio`, `at_least_days`, `sum`):
- Compute `f_i = _estimate_bxb_narrowing(constraint_i, pool, days)`
- Compute `f_j = _estimate_bxb_narrowing(constraint_j, pool, days)`
- Stacked product `f_i × f_j ≤ 0.05` (≤ 5% of baseline) → B×B tension detected

Per-aggregation narrowing factor estimates (combinatorial approximations):
- `{at_least: N}` with m qualifying venues out of P total, k slots: `1 - Σ(i<N) C(m,i)C(P-m,k-i)/C(P,k)`
- `{at_most: N}` per day: `1 - Σ(i>N) C(m,i)C(P-m,k-i)/C(P,k)` per day
- `{count_distinct: N, field}` with D distinct values, pool distribution per value: approximated as `1 - probability of drawing <N distinct values across k slots`
- `{at_most_distinct: N, field}` with D distinct values: `Σ(d≤N) C(D,d) × P(all k slots from those d values) / C(P,k)`
- `{ratio: R}` with m qualifying out of P total, k slots: `P(k-slot sample has ≥R fraction qualifying)`
- `{at_least_days: N}` with D days, per-day pass probability p: `Σ(d≥N) C(D,d) p^d (1-p)^(D-d)`
- `{sum: ≤ budget}` approximate as fraction of venue cost combinations within budget

These are approximations — correctness to ±20% is sufficient for the pass/fail threshold.

**Also in Step 1:**
- Wire in `_check_scope_declarations` (already written and tested but dormant)
- Add non-empty condition gate: generic-schema P-constraint with `condition == {}` requires non-trivial scope (not `"all"` and not `"activity_type=X"`)
- Remove the old `_SEMANTIC_TENSION_PAIRS` dict and `_venues_passing` function entirely

**Checkpoint:** run full suite, spot-check 3-4 real tasks through `validate_task_schema`.

---

#### Phase 4 Step 2: Scope-aware pool splitting (~1.5h)

The current `_apply_pool_filters` applies ALL constraints universally (the bug VALIDATION_DESIGN calls the single biggest architectural problem). Fix to the three-pool model:

```
Classify each P-constraint:
  universal:  agg == "all" (every activity in scope must satisfy)
  scoped:     agg == "all" with non-trivial scope (activity_type or category or time_window)
  inclusion:  agg == {at_least: N} or {count_distinct: N} or similar counting aggregation

Build pools:
  universal_pool  = apply all universal constraints cumulatively (engine venues_matching)
  scoped_pool[id] = venues_matching(universal_pool, scope, condition) per scoped constraint
  inclusion_pool[id] = venues_matching(universal_pool, scope, condition) per inclusion constraint
```

Update `_apply_pool_filters` in `generate_task.py` to return `universal_pool` (not all constraints applied). Update `_verify_task_solvable` to use the three pools.

**Note:** `aggregation: "none"` (exclusion) is treated as a universal negative filter — it removes from the universal_pool.

**Checkpoint:** run full suite. Type 5 tasks that were wrongly rejected by over-aggressive pool filtering should now pass.

---

#### Phase 4 Step 3: Lower-bar checks (~0.5h)

On top of the scope-aware pools from Step 2:

1. **Viable-schedule lower bar** (applied after building universal_pool):
   - FOOD venues = `universal_pool` where category in {restaurant, cafe, bar}
   - SITE venues = `universal_pool` where category in {museum, attraction, park, neighbourhood}
   - Require: `len(food) >= 2 × days` AND `len(site) >= 1 × days`
   - Failure message: "Universal constraints leave only N food venues — minimum viable schedule needs 2 × days"

2. **Scoped lower bar** (per scoped constraint):
   - Each `scoped_pool[id]` must have `≥ 1` venue (≥ 2 if this constraint participates in type 3 tension pair)

3. **Inclusion lower bar** (per inclusion constraint):
   - `len(inclusion_pool[id]) >= min_count` — already implemented, just reads from correct pool now

Replace the existing per-pattern hardcoded checks with these engine-driven checks.

**Checkpoint:** run full suite.

---

#### Phase 4 Step 4: Upper-bar checks (50/50/25, with type-specific thresholds) (~1h)

After building all pools, measure meaningfulness of each A×A constraint (aggregation `"all"` or `"none"` only — B×B counting constraints are NOT measured by upper bars).

**FOOD group** = venues where category in {restaurant, cafe, bar}
**SITE group** = venues where category in {museum, attraction, park, neighbourhood}

**Per-type thresholds:**

| Type | Universal upper bar | Scoped upper bar | Inclusion upper bar |
|---|---|---|---|
| 1, 3, 6 | ≤ 50% survives | ≤ 50% within scope | ≤ 25% qualifies |
| 2, 4 | **exempt** | **exempt** | **exempt** |
| 5 | **≤ 25% survives** | **≤ 25% within scope** | **≤ 10% qualifies** |

**Universal cumulative upper bar:**
After applying ALL universal constraints cumulatively: check `n_universal_food / n_original_food` and `n_universal_site / n_original_site`. Compare to threshold for the task's type. Check is cumulative stack, not per-filter.

**Scoped upper bar:**
For each scoped constraint: measure `n_scoped_pool[id] / n_scope_total` within universal_pool (where scope_total = venues in universal_pool matching the scope's activity_type/category).

**Inclusion upper bar:**
For each inclusion constraint: `n_inclusion_pool[id] / n_original_group`. Each inclusion evaluated independently.

Bucket C pattern handlers (4 remaining) skipped — engine cannot reason about their pool contribution.

**Checkpoint:** test against real London pool. Cross-reference against the VALIDATION_DESIGN real data table (wheelchair_accessible 92% → should FAIL, pet_friendly 4% → should PASS).

---

#### Phase 4 Step 5: Tag-category affinity (~1h)

Build affinity table from pool at validation time (once per SUBMIT, no caching):

```python
def compute_tag_affinity(pool: list[dict]) -> dict[str, set[str] | None]:
    """
    For each tag, determine which categories it concentrates in.
    Returns {tag: {cat1, cat2} | None} where None means universal.
    """
    from collections import Counter, defaultdict
    cat_counts = Counter(v["category"] for v in pool)
    tag_cats = defaultdict(Counter)
    for v in pool:
        for tag in v.get("tags", []):
            tag_cats[tag][v["category"]] += 1

    affinity = {}
    for tag, cat_counter in tag_cats.items():
        total = sum(cat_counter.values())
        fractions = {cat: count / cat_counts[cat]
                     for cat, count in cat_counter.items() if cat_counts[cat] > 0}
        concentrated = {cat for cat, frac in fractions.items() if frac >= 0.8}
        multi = {cat for cat, frac in fractions.items() if frac >= 0.3}
        if len(concentrated) == 1 and all(
            fractions.get(cat, 0) < 0.1 for cat in cat_counts if cat not in concentrated
        ):
            affinity[tag] = concentrated
        elif len(multi) >= 2:
            affinity[tag] = multi
        else:
            affinity[tag] = None  # universal
    return affinity
```

**Validator rule:** for each `has_tag=X` or `not_tag=X` condition with `aggregation: "all"` (universal scope):
- If `affinity[X]` is not None and scope is `"all"`: flag "Tag '{X}' applies only to {affinity categories} — add scope: activity_type=meal (or similar) to avoid filtering out unrelated venues"
- If scope is `"activity_type=meal"` or `"category=restaurant"` etc.: accept if scope ⊆ affinity categories

Applies only to generic-schema constraints. Bucket C patterns skipped.

**Checkpoint:** test with vegetarian/halal (should suggest meal scope), with hidden-gem (should be universal — appears across categories).

---

#### Phase 4 Step 6: Fix `evaluate()` end-to-end wrapper (~0.5h)

Current `evaluate()` does a disk lookup for the task by ID (`data/tasks/{task_id}.json` — wrong path) and loads venues without a db_path override.

**Fix:** add `task=` and `db_path=` optional params:
```python
def evaluate(result: dict, task: dict | None = None, db_path: Path | None = None, ...) -> dict:
    if task is None:
        task_id = result.get("task_id") or ...
        task = load_task(task_id)   # existing disk-lookup path
    city = task["city"]
    venues, matrix = load_ground_truth(city, db_path=db_path)
    ...
```

Backward-compatible: existing callers with no `task=` still work via disk lookup. New callers pass the task dict directly.

**Checkpoint:** run end-to-end: `evaluate(result, task=task, db_path=DB)` produces all score tiers.

---

#### Phase 4 Step 7: Full smoke test (~0.5h)

- Run full test suite (currently 611/611, should stay green throughout)
- Load 5 real tasks, construct a plausible plan for each, call `evaluate()`, verify all score tiers return
- Test 1 type-3 task with two competing constraints — verify tension detection accepts it
- Test 1 type-5 task with 3 stacked universals — verify narrow pool is accepted, not rejected
- Export final snapshot

---

**Bucket C final state after pre-work:** 4 handlers remain:
- `weather_aware` — external forecast state
- `opening_time_required` — day-keyed hours lookup
- `dependency_chain` — cross-activity relational + travel matrix
- `consecutive_pairs` — adjacent-pair ordering

**Estimated effort:**
| Sub-step | Effort |
|---|---|
| Pre-work: migrate 3 patterns | ~1h |
| Step 1: tension detection (A×A + B×B) | ~2.5h |
| Step 2: scope-aware pool splitting | ~1.5h |
| Step 3: lower bars | ~0.5h |
| Step 4: upper bars (50/50/25, type-specific) | ~1h |
| Step 5: tag-category affinity | ~1h |
| Step 6: evaluate() wrapper | ~0.5h |
| Step 7: smoke test | ~0.5h |
| **Total** | **~8.5h** |

---

## Dependency ordering

Strictly sequential:
```
Phase 1 (design) ✅
  → Phase 2 (engine) ✅
    → Phase 2.5 (tags/labels unification) ✅
      → Phase 3a (migrate tasks on disk + delete Bucket A handlers) ✅
        → Phase 3b (rewrite handbook + delete single-shot pipeline) ✅
          → Phase 4 (validation on engine primitives) ✅

---

## Phase 4 completion record

**Pre-work: 3 misclassified Bucket C patterns migrated to engine**
- `local_cuisine_preference` (36 real tasks) → `ratio` aggregation on `local_cuisine` field
- `cuisine_diversity_minimum` (1 real task) → `count_distinct` on new `cuisine_label` derived field in `pool_utils.load_city_pool`
- `temporal_cross_day` (0 real tasks) → `per_day` scope + `at_most` aggregation
- Bucket C reduced from 7 to 4 (weather_aware, opening_time_required, dependency_chain, consecutive_pairs)

**Step 1: Tension detection rewritten**
- Deleted `_SEMANTIC_TENSION_PAIRS`, `_venues_passing` (pattern+params based)
- A×A overlap metric: `venues_matching` for each `agg="all"/"none"` constraint pair, small-overlap check (≤3 absolute or ≤25% of smaller set), both sides ≥3 venues
- Type 3 only requires explicit tension; type 5 is exempt (stacking verified via lower bars)
- Bucket C pattern pair stub for type 3 (B×B deferred)
- Empty-condition gate added: `condition=={}` with trivial scope is a schema error
- `_check_scope_declarations` left dormant (checks old `scope_mode` field)

**Step 2: Scope-aware pool splitting**
- New `_build_scope_pools(venue_pool, task)` helper in `generate_task.py`
- Returns `{universal, scoped, inclusion}` three-pool dict
- universal: agg="all"/"none" constraints applied cumulatively via `venues_matching`
- scoped: agg="all" with non-trivial scope — candidate set within universal_pool
- inclusion: {at_least:N} constraints — NOT pool filters, reachability check only
- `_verify_task_solvable` fully rewritten to use three pools

**Step 3: Lower bars**
- Viable-schedule: universal_pool must have ≥2 FOOD/day AND ≥1 SITE/day
- Scoped: each scoped_pool ≥1 (≥2 for type 3 tension pairs)
- Inclusion: each inclusion_pool ≥ min_count
- Types 2 and 4 exempt from all bar checks (P7 handles their feasibility)

**Step 4: Upper bars (50/50/25 or 25/25/10 for type 5)**
- Universal upper bar: only fires when there ARE universal constraints; cumulative stack measured against original FOOD/SITE
- Scoped upper bar: per scoped constraint, measured within universal_pool scope
- Inclusion upper bar: uses actual qualifying venue categories (not scope string heuristic)
- Type 5 thresholds: 25/25/10 (universal/scoped/inclusion)
- Types 2 and 4 exempt

**Step 5: Tag-category affinity**
- `_tag_affinity(tag)` computed from pool at validation time: ≥80% food → "food", ≥80% site → "site", else None
- Warns (soft warning `~ `) when universal constraint uses a food-specific tag with scope="all"
- Warnings, not hard errors (may be intentional in some tasks)

**Step 6: evaluate() wrapper**
- Added `task=` and `db_path=` optional params — backward-compatible
- Existing callers with no overrides still use disk lookup + default city DB

**Step 7: Smoke test**
- 5 Paris tasks scored cleanly via individual scorers (no routing errors, valid composites)
- `evaluate()` wrapper tested with London task + London DB — all 4 score tiers returned

**Checkpoint results:**
- Full test suite: **611/611 green** (E1:56 + E2:45 + E2.5:95 + E3:210 + E4:116 + B4:47 + B5:43)
- End-to-end: `evaluate(result, task=task, db_path=DB)` works correctly

**Known deferred items:**
- B×B tension detection (plan-space narrowing for counting aggregations) — deferred pending confirmation on which structural types need it
- `_check_scope_declarations` wiring — still dormant, uses old `scope_mode` field not present in new schema
- `evaluate()` disk-lookup path still uses legacy flat path `data/tasks/{id}.json` — fine for now since callers pass `task=` directly
```

Each phase and substep produces a checkpoint. After each: full test suite green, zip exported for user review. If any step fails catastrophically, revert from the duplicate repo.

## Total estimate

| Phase | Effort |
|---|---|
| Phase 1 — design doc | 2h |
| Phase 2 — complete engine | 3-4h |
| Phase 2.5 — tags/labels unification | 0.5h |
| Phase 3a — migrate tasks on disk + delete handlers | 3-4h |
| Phase 3b — rewrite handbook + delete single-shot pipeline | 2-3h |
| Phase 4 — pre-work + 7 validation steps | ~7.5h |
| **Total** | **~18.5-21h** |

End state: one schema on disk, one engine, no translator layer. Bucket C reduced from 7 to 4 handlers (weather, opening_time_required, dependency_chain, consecutive_pairs). Validation is scope-aware with upper/lower bars and tag affinity. `evaluate()` runnable end-to-end.

## What's on hold / ignored for now

The existing subtask docs (`docs/subtasks/P9_*.md`, `P10_*.md`, `P12_*.md`, `P13_scope_model.md`, `P13_division.md`, `P13.1_structure.md`, `P13.2_structure.md`, `P14_*.md`, `P15_*.md`) are NOT being worked on. Their content may be pulled into this migration plan incrementally — the designed rules (upper bars, affinity, etc.) flow into Phase 4. But the subtasks as framed are superseded.

## Safe rollback

User has a duplicate of the repo. This migration happens in `/tmp/travelbench_phase4/`. If it breaks catastrophically, revert from the duplicate.

---

## Phase 5: B×B plan-space tension detection

**Goal:** implement plan-space narrowing factor estimation for counting-aggregation constraints (`at_least`, `at_most`, `count_distinct`, `at_most_distinct`, `ratio`, `at_least_days`, `sum`). Detect when stacked B×B constraints eliminate too large a fraction of valid plans (upper bound for type 1 — unintended difficulty) or confirm they eliminate enough (lower bound for type 3/5 — required tension/hardness).

**Status:** ✅ Complete — 651/651 tests passing (611 existing + 40 new B×B tests).

**Files created/modified:**
- `scripts/generation/constraint_engine_bxb.py` — new module, pure math
- `scripts/generation/test_bxb.py` — new, 40 tests
- `scripts/generation/generate_task.py` — `_verify_task_solvable` Steps 5+6 (B×B type 1 gate + type 5 OR check)
- `test_generate_tasks.py` — B×B tension detection wired into type 3 tension block
- `scripts/generation/task_agent.py` — handbook: B×B section added to `validate`
- `scripts/generation/test_e3.py` — [6b] test updated for B×B-aware expectations

**Real-task context:** 82 of 171 constraints (48%) across 66 tasks are B×B aggregation types. Distribution: `ratio` (36), `at_least` (17), `at_most_distinct` (12), `at_least_days` (11), `sum` (5), `count_distinct` (1). 14 tasks have 2+ B×B constraints.

---

### What needs to change, and where

#### 1. New module: `constraint_engine_bxb.py`

The B×B narrowing estimator is pure math on pool attributes + schedule shape. It doesn't fit cleanly into the existing engine (which operates on plans, not on plan-space combinatorics) and doesn't belong in generate_task.py (too long). New module alongside constraint_engine.py.

**Contents:**
- `_estimate_bxb_narrowing(constraint, pool, days, slots_per_day) → float` — per-constraint narrowing factor. Dispatches by aggregation type.
- Per-type factor functions (see Step 1 below for formulas).
- `_bxb_stacked_product(constraints, pool, days, slots_per_day) → float` — multiply individual factors (with correlation adjustment when two constraints target the same slot pool).
- `detect_bxb_tension(constraints, pool, days) → dict` — top-level entry point for validation. Returns `{found: bool, product: float, pairs: [...]}`.

**Why separate module:** the formulas are non-trivial (hypergeometric, binomial, inclusion-exclusion) and need their own tests. Keeping them out of the already-large constraint_engine.py and generate_task.py is cleaner.

---

#### 2. `generate_task.py` — `_verify_task_solvable`

Currently the only check in `_verify_task_solvable` that touches B×B constraints is the inclusion lower bar (3c) — "does the pool have enough qualifying venues?" That's necessary but not sufficient.

**New checks to add (after existing lower/upper bars):**

**For type 1** — B×B upper bound (accidental over-restriction):
- Collect all B×B constraints (counting aggregations in `inclusion` + any `at_most`, `count_distinct`, `at_most_distinct`, `ratio`, `at_least_days`, `sum`).
- If stacked product ≤ 2% of baseline plans → task is accidentally too hard → fail with message explaining which constraints combine to create the problem.
- Threshold choice: 2% for type 1 (unintended stacking should be caught early). Type 1 difficulty is supposed to come from cascading, not from combinatorial scarcity.

**For type 3** — B×B lower bound (required tension as alternative to A×A):
- If A×A tension already found → skip B×B check (either suffices).
- If no A×A tension → compute B×B stacked product. If product ≤ 5% → B×B tension detected → accept.
- If neither A×A nor B×B tension → fail with current tension error message.

**For type 5** — B×B tightness verification:
- Compute stacked product across ALL constraints (both A×A and B×B together — type 5 stacks everything).
- Product must be ≤ 10% of baseline plans to confirm the "extreme narrowing" claim.
- This replaces the current approach of relying solely on the A×A upper bar for type 5.

**Types 2, 4, 6** — exempt. No change.

---

#### 3. `test_generate_tasks.py` — tension detection block

The current tension block in `validate_task_schema` only runs A×A detection and has a Bucket C stub placeholder. After B×B is implemented:

- Wire in `detect_bxb_tension` from the new module.
- For type 3: tension is found if EITHER A×A overlap test OR B×B product test passes.
- Update the error message to describe both paths: "No tension detected — type 3 needs either (a) two A×A constraints with small venue-set overlap, or (b) two B×B constraints whose stacked plan-space product ≤ 5%."
- Remove the Bucket C two-pattern stub (which was a placeholder for B×B). Once real B×B is wired in, Bucket C patterns still don't get B×B analysis (their aggregations are opaque to the engine), but we no longer need to pretend their presence alone signals tension.

---

#### 4. `task_agent.py` — handbook

The handbook currently says nothing about B×B constraints interacting with each other. The agent needs to understand:
- That B×B constraints narrow the **plan space** (not the venue pool) — stacking two of them makes valid schedules rare, not venues absent.
- The threshold: two B×B constraints whose combined narrowing factor ≤ 5% create genuine tension for type 3.
- How to reason about it: "If I require ≥3 museum visits AND ≥2 districts, and the pool has only 4 museums across 3 districts, almost no plans satisfy both simultaneously — that's good type 3 tension."
- Antipattern: stacking a `ratio` and an `at_least_days` that target the same slots in the same direction — that's type 1 territory (accidental over-restriction), not type 3 tension.

**Handbook section to update:** the `validate` section (currently describes solvability checks) needs a new paragraph:

```
B×B PLAN-SPACE TENSION (type 3 only)
Two B×B constraints create genuine tension when their combined plan-space
narrowing factor ≤ 5% of baseline. The validator computes this automatically
from pool composition and schedule shape. Good B×B pairs:
  {at_most_distinct: 2, field: district} + {count_distinct: 3, field: cuisine_label}
  {at_least: 3, scope: category=museum} + {at_most_distinct: 2, field: district}
  {ratio: 0.6, scope: activity_type=meal} + {at_least_days: 2}
Bad pairs (same direction, type 1 territory):
  {at_least: 2, scope: has_tag=vegan} + {at_least: 1, scope: has_tag=halal}
  (both require specific food — stacking makes schedule rare, not tensioned)
```

---

#### 5. New test file: `test_bxb.py`

The B×B math is complex enough to warrant its own test file, separate from the existing E-series and B-series suites.

**Tests to write:**
- Per-type narrowing factor unit tests (known exact answers for small pools).
- Stacked product computation with independent and correlated constraint pairs.
- `detect_bxb_tension` integration tests: type 3 task that has only B×B tension (no A×A) → found. Type 1 task with innocuous B×B stacking → not flagged. Type 1 task with accidental over-restriction → flagged.
- Boundary: single B×B constraint → no tension (need ≥2). Empty constraint list → no tension.
- Pool edge cases: pool too small for hypergeometric (P < K) → graceful degradation.

---

### Subtask breakdown

#### Step 1 — Per-type narrowing factor functions (~3h)

Implement `_estimate_bxb_narrowing` dispatch in `constraint_engine_bxb.py`. Per-type formulas (all approximate to ±20%, sufficient for threshold detection):

**`{at_least: N}`** with m qualifying venues out of P total, K slots:
```
f = 1 - CDF(hypergeometric(K, m, P), N-1)
  = P(at least N successes in K draws without replacement from P items of which m qualify)
```
Use `scipy.stats.hypergeom.sf(N-1, P, m, K)` or a pure-Python approximation.

**`{at_most: N}`** same distribution, upper tail:
```
f = CDF(hypergeometric(K, m, P), N)
  = P(at most N successes)
```

**`{ratio: R}`** with m qualifying out of P total across K_scope scope-slots:
```
f = P(hypergeometric(K_scope, m_scope, P_scope) ≥ ceil(R * K_scope))
```
Where K_scope = K × (scope fraction of pool), m_scope and P_scope filtered to scope.

**`{at_least_days: N}`** with D days, each day has S slots:
Approximate per-day pass probability: p_day ≈ P(at least 1 qualifying in S draws) for "all in scope satisfy" or use the full per-day calculation.
```
f = P(Binomial(D, p_day) ≥ N)
  = sum_{k=N}^{D} C(D,k) * p_day^k * (1-p_day)^(D-k)
```

**`{count_distinct: N, field}`** with D distinct field values, pool distribution d_i per value, K slots:
Inclusion-exclusion approximation:
```
f ≈ 1 - P(draws cover < N distinct values)
  ≈ 1 - sum_{j=0}^{N-1} (-1)^j * C(D,D-j) * ((D-j)/D)^K  [uniform approximation]
```
For non-uniform distribution use actual pool distribution.

**`{at_most_distinct: N, field}`** with D distinct values:
```
f = sum_{j=0}^{N} C(D,j) * P(all K draws from those j values)
  = sum_{j=0}^{N} C(D,j) * (j/D)^K  [uniform approximation]
```

**`{sum: ..., operator: <=, value: V}`** with venue cost distribution {μ, σ} over K draws:
```
f ≈ Φ((V - K*μ) / (sqrt(K) * σ))   [central limit theorem approximation]
```
Use normal CDF. Requires computing mean and std of cost field across qualifying pool.

All formulas need a `max(0.001, min(0.999, result))` clamp to avoid log(0) in stacking.

**Checkpoint:** unit test each formula against a manually-calculated small case.

---

#### Step 2 — Joint computation respecting group structure (~2h)

**The fundamental structure: food group and site group are separate draws.**

A plan always consists of:
- `K_food = days × 2` slots drawn from the food pool (P_food venues: restaurant, cafe, bar)
- `K_site = days × 2` slots drawn from the site pool (P_site venues: museum, attraction, park, neighbourhood)

These are **independent draws from disjoint pools**. This isn't a coincidence — it's the definition of a valid travel plan. The correct baseline plan count is:

```
baseline = C(P_food, K_food) × C(P_site, K_site)
```

NOT `C(P_total, K_total)` — that formula treats food and site slots as interchangeable, which overcounts by mixing groups that are never mixed in practice.

**Scope → group mapping:**

Every B×B constraint targets one of three groups:

| Scope | Group | K | Pool |
|---|---|---|---|
| `activity_type=meal`, `category=restaurant/cafe/bar` | food | `days × 2` | P_food |
| `activity_type=visit`, `category=museum/attraction/park/neighbourhood` | site | `days × 2` | P_site |
| `all`, `activity_type=any`, `per_day`, or spans both | universal | `days × 4` | P_total |

For `has_tag=X` or other scopes, check which venues match: if all matching venues are in FOOD_CATS → food group; all in SITE_CATS → site group; mixed → universal.

**Real-task distribution** (66 tasks, 82 B×B constraints):
- food group: 38 (ratio×36, at_least×1, count_distinct×1)
- site group: 10 (at_least×10)
- universal: 34 (at_most_distinct×12, at_least_days×11, at_least×6, sum×5)

**Independence rules:**
- Food group constraints and site group constraints are **always independent** — exact, no overlap check needed. Multiply their probabilities directly.
- Within the same group, constraints may be dependent → use joint multivariate hypergeometric.
- Universal constraints span both groups; treat as a third independent factor (approximation — see below).

**Why treating universal as independent is conservative:**

A universal `{at_least: N}` constraint requires N qualifying venues across K_total slots. A food-group constraint requires something of the K_food food slots. These interact (the food slots that satisfy the universal constraint also help satisfy the food constraint). Treating them as independent **underestimates** the joint probability (makes the stacked f smaller than reality) — which means we overestimate tension. This errs in the right direction: false positives (detecting tension that isn't quite there) are preferable to false negatives (missing genuine tension). For a ±20% approximation target, this is acceptable.

**Joint computation formula for same-group constraints (at_least pairs):**

When two constraints A and B share the same group (same pool and same K):

```
P(A satisfied AND B satisfied) =

  Σ  C(|A_only|, k_A) · C(|B_only|, k_B) · C(|AB|, k_AB) · C(|neither|, k_n)
     ────────────────────────────────────────────────────────────────────────────
                              C(P_group, K_group)

where the sum is over all (k_A, k_B, k_AB, k_n) satisfying:
  k_A + k_B + k_AB + k_n = K_group
  [A satisfied]:  k_A + k_AB ≥ threshold_A    (for {at_least: N_A}: threshold_A = N_A)
  [B satisfied]:  k_B + k_AB ≥ threshold_B    (for {at_least: N_B}: threshold_B = N_B)

Bucket sizes computed via venues_matching:
  set_A  = venues_matching(pool, scope_A, cond_A)
  set_B  = venues_matching(pool, scope_B, cond_B)
  set_AB = venues_matching(pool, scope_AB, {"all": [cond_A, cond_B]})
  |AB|      = |set_AB|
  |A_only|  = |set_A|  - |set_AB|
  |B_only|  = |set_B|  - |set_AB|
  |neither| = P_group - |set_A| - |set_B| + |set_AB|
```

Enumeration cost: K_group^3 terms ≤ 8^3 = 512 for a 4-day trip (K_food=8). Fast.

**Which aggregation types support joint computation (within same group):**

| Aggregation | Joint computable? | Notes |
|---|---|---|
| `at_least` | ✅ | k_ab + k_a ≥ N in hypergeometric |
| `at_most` | ✅ | k_ab + k_a ≤ N |
| `ratio` | ✅ | k_ab + k_a ≥ ceil(R · K_group) |
| `at_least_days` | ✅ | per-day pass prob computed jointly, then binomial |
| `sum` | ✅ | joint cost distribution via CLT |
| `count_distinct` | ⚠️ | needs distinct-value tracking — use `min()` fallback |
| `at_most_distinct` | ⚠️ | same — use `min()` fallback |

**Algorithm for `_compute_bxb_joint(constraints, pool, days)`:**

```python
K_food = days * 2
K_site = days * 2
K_total = days * 4
food_pool = [v for v in pool if v.get("category") in FOOD_CATS]
site_pool = [v for v in pool if v.get("category") in SITE_CATS]

# 1. Assign each constraint to a group
food_pcs      = [c for c in constraints if _constraint_group(c, pool) == "food"]
site_pcs      = [c for c in constraints if _constraint_group(c, pool) == "site"]
universal_pcs = [c for c in constraints if _constraint_group(c, pool) == "universal"]

# 2. Compute per-group joint probabilities
f_food = _compute_group_joint(food_pcs, food_pool, K_food)        # hypergeometric over food group
f_site = _compute_group_joint(site_pcs, site_pool, K_site)        # hypergeometric over site group
f_univ = _compute_group_joint(universal_pcs, pool, K_total)       # hypergeometric over full pool

# 3. Multiply (food and site are independent; universal treated as independent approx)
f_joint = f_food * f_site * f_univ

# 4. Absolute count against the correct two-group baseline
baseline = C(len(food_pool), K_food) * C(len(site_pool), K_site)
valid_count = f_joint * baseline

return (f_joint, valid_count)
```

`_compute_group_joint(pcs, group_pool, K)` handles within-group clustering and joint hypergeometric as described above. Single-constraint groups skip directly to the per-type formula from Step 1.

The function returns **both** the probability and the absolute count — the absolute count is the primary threshold metric (see Threshold Summary below).



#### Step 3 — Wire into `_verify_task_solvable` (~1h)

Add B×B checks after the existing lower/upper bar section (step 3d). Primary metric is `absolute_valid_count`; fraction `f` is used as a gate to distinguish B×B interaction from legitimate A×A cascading.

```python
# 3d. B×B plan-space checks (types 1, 3, 5 only)
if not bars_exempt and not is_type6:
    from scripts.generation.constraint_engine_bxb import _compute_bxb_joint
    bxb_pcs = [c for c in pcs if "scope" in c and isinstance(c.get("aggregation"), dict)]
    if len(bxb_pcs) >= 2:
        K = days * 4   # 2 food + 2 site slots per day (fixed — see Step 2 rationale)
        f, valid_count = _compute_bxb_joint(bxb_pcs, universal, days)
        solvability_floor = max(K, days * 2)

        # ── Type 1: misclassification detector ────────────────────────────────
        # Fires ONLY when BOTH conditions hold:
        #   f ≤ 5%: scarcity comes from counting constraint interaction, not from
        #           legitimate A×A cascading through a narrow pool
        #   valid_count < floor: fewer valid plans than slots — agent has no room
        # If f > 5% but count is low, the pool is legitimately small from A×A
        # filtering — that's fine for type 1.
        if is_type1 and f <= 0.05 and valid_count < solvability_floor:
            return False, (
                f"B×B misclassification: stacked counting constraints leave "
                f"f={f:.1%} of plans, ~{valid_count:.0f} valid plans "
                f"(floor={solvability_floor}). This is plan-space competition — "
                f"type 3 behaviour in a type 1 task. "
                f"Either reclassify as type 3 or remove one counting constraint."
            )

        # ── Type 5: tightness verification (OR with A×A bars) ─────────────────
        # A×A bars (25/25/10) verify venue-pool narrowing.
        # B×B f ≤ 2% verifies plan-space narrowing.
        # Either one independently confirms extreme narrowing — OR logic.
        # The A×A check runs earlier in the pipeline (upper bars step 4).
        # Here we only need to track the B×B result; the OR is resolved after
        # both checks complete. Store f on local var for use after the bar checks.
        # If neither A×A nor B×B confirms tightness → fail at end of bar checks.
        # Implementation: set a flag here, check it after the upper bar block.
        if is_type5:
            _bxb_tight = (f <= 0.02)
            # _axa_tight is set by the upper bar checks (step 4a/4b/4c above)
            # Both are referenced in the type 5 final verdict below.
```

After the upper bar block, add the type 5 OR verdict:
```python
        # Type 5 OR check: A×A bars OR B×B tightness must confirm extreme narrowing
        if is_type5:
            _axa_tight = (  # did the A×A upper bars actually fire for this task?
                has_universal and (
                    (orig_food > 0 and len(food_in_uni)/orig_food <= 0.25) or
                    (orig_site > 0 and len(site_in_uni)/orig_site <= 0.25)
                )
            )
            if not _axa_tight and not _bxb_tight:
                return False, (
                    f"Type 5 insufficient narrowing: neither A×A bars "
                    f"(need ≤25% food/site surviving) nor B×B plan-space "
                    f"(need f≤2%, got f={f:.1%}) confirm extreme narrowing. "
                    f"Add a strong universal constraint or tighter counting constraints."
                )
```

Note: `_bxb_tight` and `_axa_tight` are only computed when `is_type5` is True, so no overhead for other types. The B×B computation (`_compute_bxb_joint`) is only called once even though its result is used in two places (type 1 check and type 5 check).

---

#### Step 4 — Wire into tension detection in `validate_task_schema` (~1h)

In the tension block, after A×A check. For type 3, B×B tension fires when `f ≤ 5%` — this is purely a fraction threshold, no absolute count ceiling needed. `f ≤ 5%` already means "hard to find a valid plan regardless of pool size."

```python
if tension_required and not tension_found and pool:
    from scripts.generation.constraint_engine_bxb import _compute_bxb_joint
    bxb_pcs = [c for c in pcs if "scope" in c and isinstance(c.get("aggregation"), dict)]
    if len(bxb_pcs) >= 2:
        f, valid_count = _compute_bxb_joint(bxb_pcs, pool, days=task.get("days", 1))
        if f <= 0.05:
            tension_found = True
            tension_detail.append(
                f"B×B: joint_prob={f:.1%} ≤ 5% "
                f"(~{valid_count:.0f} valid plans)"
            )
```

Remove the Bucket C two-pattern stub (replaced by real B×B logic).

Update tension error message to explain both paths:
```
"No tension detected — type 3 needs either:
  (a) Two A×A constraints with venue-set overlap ≤ 3 absolute or ≤ 25% of smaller set, OR
  (b) Two or more B×B counting constraints whose joint valid-plan fraction ≤ 5%.
 Good A×A pairs: outdoor-tag vs indoor-museum, pet-friendly vs quiet, upscale vs budget.
 Good B×B pairs: {at_most_distinct:2, district} + {count_distinct:3, cuisine_label};
                 {at_least:3, museum} + {at_most_distinct:2, district};
                 {ratio:0.6, meal} + {at_least_days:2}."
```

---

#### Step 5 — Handbook update (~0.5h)

Add the B×B tension paragraph to the `validate` section in `_build_task_handbook`. Keep it brief — the agent needs to know the concept and good/bad examples, not the math.

---

#### Step 6 — Tests in `test_bxb.py` (~2h)

New test file. Run separately as `python scripts/generation/test_bxb.py`. Add it to the suite summary.

Expected tests:
- 6 per-type unit tests (one per aggregation type, known small case)
- 3 stacked product tests (independent, correlated, mixed)
- 3 tension detection integration tests (type 3 B×B only, type 3 A×A+B×B, type 1 accidental over-restriction)
- 2 boundary tests (single constraint, empty list)
- 1 pool edge case (P < K → graceful clamp)

---

#### Step 7 — Full suite regression (~0.5h)

Run all existing suites (E1 through B5) + new test_bxb. Confirm 611 + N_bxb all green.

---

### Effort estimate

| Step | Work |
|---|---|
| Step 1: per-type narrowing factor functions | ~3h |
| Step 2: joint multivariate hypergeometric | ~2h |
| Step 3: wire into _verify_task_solvable | ~1h |
| Step 4: wire into tension detection | ~1h |
| Step 5: handbook update | ~0.5h |
| Step 6: test_bxb.py | ~2h |
| Step 7: regression suite | ~0.5h |
| **Total** | **~10h** |

---

### Threshold summary

**Why fraction (f) is the right primary metric**

`f` is the joint probability that a uniformly random plan (K_food draws from food pool × K_site draws from site pool) satisfies all B×B constraints. The correct baseline is:

```
baseline = C(P_food, K_food) × C(P_site, K_site)
         = C(P_food, days×2) × C(P_site, days×2)
```

NOT `C(P_total, K_total)` — that formula treats food and site slots as interchangeable (they're not). The group-separated baseline is the denominator that makes `f` meaningful across different pool sizes and trip lengths.

**Unified f thresholds (stepping down by type):**

| Check | Type | f threshold | Direction | Mechanism |
|---|---|---|---|---|
| A×A universal upper bar | 1/3/6 | ≤ 50% pool survives | venue pool narrows | _verify_task_solvable step 4a |
| A×A universal upper bar | 5 | ≤ 25% pool survives | venue pool narrows | _verify_task_solvable step 4a |
| B×B type 3 tension | 3 | f ≤ 5% | plan space is sparse | validate_task_schema tension block |
| B×B type 5 tightness | 5 | f ≤ 2% (OR A×A ≤25/25/10) | plan space is very sparse | _verify_task_solvable step 3d |
| B×B type 1 gate | 1 | f ≤ 5% AND count < floor | misclassification detector | _verify_task_solvable step 3d |

Thresholds step down predictably: 50% (A×A type 1/3/6) → 25% (A×A type 5) → 5% (B×B type 3) → 2% (B×B type 5). Each step is a tighter claim.

**Type-by-type rules:**

**Type 1 — misclassification detector (both conditions required):**
```
fires when: f ≤ 5%  AND  valid_count < max(K, days×2)
```
Both conditions must hold. `f ≤ 5%` gates on counting-constraint interaction (not A×A pool narrowing). `valid_count < floor` confirms there's genuinely no room for the agent. If `f > 5%` but count is low, the pool is legitimately small from A×A filtering — fine for type 1.

**Type 3 — B×B tension (alternative to A×A, OR logic):**
```
tension confirmed if: A×A overlap ≤ 3 absolute OR ≤ 25% of smaller set
                  OR: f ≤ 5%   (from B×B joint computation)
```
Either confirms genuine competition. f ≤ 5% alone is sufficient — no absolute count ceiling needed. The fraction already captures hardness regardless of pool size.

**Type 5 — tightness verification (OR logic across A×A and B×B):**
```
extreme narrowing confirmed if:
  A×A: universal constraints leave ≤ 25% of food AND ≤ 25% of sites
  OR
  B×B: f ≤ 2%
```
Both check different dimensions (pool scarcity vs plan scarcity). Either independently confirms the type 5 claim. The A×A check runs first; B×B is computed once and stored, then OR'd at the end of the bar checks. Additionally: `valid_count ≥ 1` as a hard solvability floor (if zero valid plans exist, the task is unsolvable regardless of type).

**Solvability floor (all non-exempt types):**
```
valid_count ≥ 1     [hard floor — task must be solvable]
```
The per-type checks above handle the upper/lower direction for difficulty. The absolute floor is just "at least one valid plan must exist" — which is already partially guaranteed by the inclusion lower bars (step 3c) but the B×B computation gives a more precise guarantee.

---

### Open question resolved

For type 3: B×B and A×A tension are **alternatives** — either one suffices. A task passes the tension check if ANY of the following holds:
- A pair of A×A constraints has venue-set overlap ≤ 3 absolute or ≤ 25% of smaller set
- The B×B joint computation gives f ≤ 5%

For type 5: A×A and B×B are **alternatives** — either confirms extreme narrowing:
- A×A universal upper bars: ≤ 25% food AND ≤ 25% site survive
- B×B joint computation: f ≤ 2%

For type 1: A×A and B×B both apply — A×A runs as normal bar checks, B×B adds a misclassification gate that fires only when counting constraints themselves (not pool narrowing) cause scarcity.



