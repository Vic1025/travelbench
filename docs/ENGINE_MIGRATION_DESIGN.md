# Engine Migration — Phase 1 Design Doc

> ⚠ **Older design doc.** Preserved as historical reference for design rationale.
> **Current implementation status:** see [docs/TODO_PHASE6.md](TODO_PHASE6.md) for the
> live work tracker, including any items in this doc that have been built, modified,
> or superseded.

**Purpose:** Before writing any code, map every validation concern we have onto the generic constraint engine. For each concern, answer: can it be expressed as (a) a direct query against engine predicates, (b) pure math on top of engine outputs, or (c) external logic that stays its own module?

**Output of Phase 1:** this doc plus an itemized list of engine additions needed for Phase 2.

**Not touched in Phase 1:** any code.

---

## The engine, as it stands today

Three existing pieces in `scripts/generation/constraint_engine.py`:

1. `_activity_matches_scope(act, scope, venues) → bool`
   Per-activity scope predicate. Supports `"all"`, `"activity_type=X"`, `"category=X"`, `"has_tag=X"`, `"time_window=H:M-H:M"`, and list-AND. Per_day is handled separately in the scoring wrapper.

2. `_activity_satisfies_condition(act, condition, venues) → bool`
   Per-activity condition predicate. Supports `has_tag`, `not_tag`, field comparison (`field + operator + value`, with ordered-enum support for price_tier/traffic_tier/recommended_pace), and composite `all` / `any`.

3. `_evaluate_generic_constraint(cid, constraint, days, all_activities, venues) → {id, score, reason}`
   Scoring wrapper. Walks activities, filters by scope, checks each against condition, aggregates (`all` / `none` / `at_least` / `at_most` / `exactly` / `count_distinct` / `ratio` / `sum`), returns 0.0/1.0 (plus 0.5 for "at_least with some progress").

4. Pool-facing helpers already written:
   - `pool_as_activities(pool) → (activities, venues_dict)` — wraps each venue as a single-activity plan
   - `_translate_scope_for_pool(scope)` — converts scope specs for pool-existence checks (e.g., `activity_type=meal` → `[category=restaurant, category=cafe, category=bar]`)
   - `check_constraint_pool_satisfiability(constraint, pool) → (ok, reason)` — currently only called from tests

The **bottom wheels** are the two predicates (`_activity_matches_scope`, `_activity_satisfies_condition`). Everything else composes them.

---

## The validation concerns we need to support

Collected from the frozen subtask docs (P6, P7, P9, P10, P13, P15):

1. **Scope-mode narrowing** — universal / scoped / inclusion differentiation on every P-constraint. What narrows the universal pool? What narrows a specific slot? What's an "include at least K" check?
2. **50/50/25 upper bars** — universal and scoped filters must cumulatively narrow the FOOD group AND SITE group each by at least 50%. Inclusion filters must exclude at least 75% of the relevant group.
3. **Viable-schedule lower bar** — universal_pool must support `2 restaurants + 1 site × days` (minimum feasible schedule).
4. **Per-constraint pool-sufficiency** — inclusion constraints must have ≥ min_count candidates in the pool.
5. **Tag-category affinity** — when a label is meal-specific (e.g. `vegetarian`), the constraint's scope must target meal categories; same for site-specific labels.
6. **P7 time feasibility (type 2)** — the query-stated time_ceiling must fall in a 1.1-1.3× band around the minimum schedule time.
7. **P7 budget feasibility (type 4)** — the query-stated budget_per_day must fall in a 1.1-1.3× band around the cheapest viable plan cost.
8. **Type 3 tension (A×A)** — two per-venue filters must identify mostly *different* venue sets — small overlap.
9. **Type 3 tension (B×B plan-level)** — two plan-level constraints must compound to collapse the plan space below 5% of baseline AND below 2000×days in absolute terms.
10. **Type 5 viable-schedule exemption** — type 5 is exempt from pool-size-max and per-category-count lower bar; it passes if the stacked pool can build a minimum schedule.
11. **Structural sanity** — schema validation of the constraint shape itself (valid fields, required hop-2 present, etc.). Pre-engine check.
12. **Per-type exemptions** — which rules apply to which type (e.g., tension check doesn't apply to type 1/2/4/6; pool-size-max doesn't apply to type 5 after P10).

---

## Classification of each concern

Legend:
- **(a)** direct engine query — one call to an engine primitive answers the question
- **(b)** math on top of engine outputs — use engine primitives to get counts, then arithmetic
- **(c)** external to engine — stays its own module, engine plays no role

### 1. Scope-mode narrowing → (a) direct engine query

The three "modes" are not new fields. They emerge from combinations of `scope` × `aggregation`:

- **Universal narrowing** = `scope: "all"` + `aggregation: "all"` → every activity must satisfy.
- **Scoped narrowing** = `scope: "activity_type=meal"` (or similar) + `aggregation: "all"` → every meal activity must satisfy; other activities unaffected.
- **Inclusion** = any scope + `aggregation: {at_least: K}` → pool must contain ≥K matching, K specific activities must satisfy in the plan.

Getting the surviving-pool list for a universal or scoped constraint is:
```python
surviving = [v for v in pool if scope_match(v) and condition_check(v)]
```
which composes the two existing predicates. No new engine primitive needed for this alone — it's the two validation-facing helpers (`venues_matching`, `venues_in_scope`).

**Engine need:** the two helper functions on top of existing predicates. ~10 lines.

### 2. 50/50/25 upper bars → (b) math on engine outputs

For each universal or scoped constraint:
- Call `venues_in_scope(pool, scope)` to get denominator count (scope-matching venues, partitioned by FOOD/SITE).
- Call `venues_matching(pool, scope, condition)` to get numerator count (scope-matching venues that also satisfy condition).
- Compute `n_satisfying / n_in_scope` per FOOD group and per SITE group.
- Compare to 0.5 (universal/scoped) or 0.25 (inclusion).

Stacking: for universal filters, apply them cumulatively then measure the FINAL surviving pool against original FOOD/SITE counts. For scoped filters, group by scope target and stack within each group.

**Engine need:** nothing beyond the two helpers. The arithmetic is caller-side.

### 3. Viable-schedule lower bar → (b) math on engine outputs

After cumulative universal filtering:
- `surviving_food = venues_matching(universal_pool, scope="activity_type=meal", condition=None)` — but wait, a "no-op condition" isn't in the engine yet. Check what `_activity_satisfies_condition` does with an empty dict: line 68-69, returns True. So `condition={}` gives us "no condition" and the helper returns everything in scope. Good.
- `surviving_site = venues_matching(universal_pool, scope="activity_type=visit", condition={})` — or explicitly scope to each SITE category.
- Check `len(surviving_food) >= 2 × days` and `len(surviving_site) >= 1 × days`.

**Engine need:** confirm empty-condition behavior is clean. It is. No changes.

### 4. Per-constraint pool-sufficiency (inclusion lower bar) → (a) direct engine query

For each inclusion constraint, call `venues_matching(pool, scope, condition)`. Check `len(result) >= min_count`. This is essentially what the existing `check_constraint_pool_satisfiability` does, generalized.

**Engine need:** nothing new — the existing helper covers this.

### 5. Tag-category affinity → (b) math on engine outputs, with auxiliary compute

Two parts:
- **Compute affinity** (once per city, at task-gen time): for each tag, count appearances per category from the pool. Apply the ≥80%-concentrated rule, ≥30%-in-N-cats rule, or universal fallback.
- **Validate a constraint's scope against affinity**: if a constraint's condition references a tag (via `has_tag=X`), look up the tag's affinity. If restricted to certain categories, the constraint's scope must be ⊆ those categories.

The compute step is pure pool analysis, no engine needed.
The validate step compares the constraint's declared scope to the affinity result — string equality / subset check. Nothing engine-specific.

**Engine need:** nothing. This logic lives in a validation helper (maybe `tag_affinity.py` or inside the validator module).

### 6. P7 time feasibility → (c) external to engine

Geographic computation: K-nearest neighbor travel from pool centroid, half-recommended visit minutes, nearest-neighbor travel time sum, compare stated ceiling to 1.1-1.3× minimum.

This uses raw venue geometry (lat/lng), `recommended_visit_minutes`, and travel matrix. None of it is scope/condition/aggregation shaped.

**Engine need:** nothing. P7 module stays independent.

### 7. P7 budget feasibility → (c) external to engine

Same character: split filtered pool by category, sort by `avg_cost_local`, pick cheapest `2r+2s×days`, sum, plus premium_add for required_venue_ids above category median, compare stated budget to 1.1-1.3× minimum.

Uses raw costs. No scope/condition/aggregation.

**Engine need:** nothing. P7 module stays independent.

**Note:** the `filtered` pool that P7 operates on is produced by today's `_apply_pool_filters`. Post-migration, this becomes `build_universal_pool(pool, universal_constraints)` → `venues_matching(universal_pool, "all", cumulative_conditions)`. P7 consumes the output; its internal math is unchanged.

### 8. Type 3 tension A×A small-overlap → (b) math on engine outputs

For each pair of universal or scoped constraints with per-venue conditions:
- Get `set_i = venues_matching(pool, scope_i, condition_i)` (venues satisfying constraint i alone)
- Get `set_j = venues_matching(pool, scope_j, condition_j)` (venues satisfying constraint j alone)
- Both must be ≥ `MIN_SIDE_COUNT` (e.g., 3)
- Overlap `len(set_i & set_j)` must be ≤ 3 absolute OR ≤ 25% of smaller set

This is the P9 small-overlap metric, expressed in engine terms. Two engine calls, set math, threshold comparison.

**Engine need:** nothing beyond the two helpers. The sets come directly from `venues_matching`.

### 9. Type 3 tension B×B plan-space-narrowing → (b)(c) hybrid

**Corrected scope (post-Phase 3):** B×B is NOT limited to old plan-level pattern handlers. It applies to any constraint with a counting aggregation — `at_least`, `at_most`, `count_distinct`, `at_most_distinct`, `ratio`, `at_least_days`, `sum`. These constraints operate on the plan space, not the venue pool: they restrict which *combinations of venues* form a valid plan, without removing any venue from the pool.

In real tasks: 82 of 171 constraints (48%) use counting aggregations and are therefore B×B. This is not a corner case.

**A×A vs B×B split by aggregation type:**
- A×A: `"all"`, `"none"` — directly narrows the venue pool
- B×B: everything else — narrows the set of valid plans drawn from the pool

**B×B plan-space narrowing factor:** for each counting-aggregation constraint, estimate what fraction of all possible plans (pool × schedule shape) satisfy it. For two B×B constraints in a type 3 task, they create tension when their combined narrowing factor is ≤ 5% of baseline AND ≤ 2000 × days absolute plans remain.

**Example:** `{at_most_distinct: 2, field: district}` + `{count_distinct: 3, field: cuisine_label}`. Neither removes any venue from the pool (A×A overlap = 0 tension). But satisfying both — all venues from ≤2 districts AND ≥3 distinct cuisines — may be hard if cuisine variety is geographically spread. B×B stacked product would be small.

**Why the original formulation was incomplete:** the design mentioned `district_count_max` and `cuisine_diversity_minimum` as examples. Both have since been migrated to engine aggregations (`at_most_distinct`, `count_distinct`). The B×B framework applies to ALL counting aggregations, not just the patterns that happened to illustrate it.

**Engine need:** nothing new. B×B is computed as arithmetic on pool attributes (fraction of venues per district, per cuisine, etc.) × schedule shape (slots per day, days). The narrowing factor for each aggregation type requires a per-type formula (combinatorial approximation, not exact). Implemented as a helper function `_estimate_bxb_narrowing(constraint, pool, days) → float`.

### 10. Type 5 viable-schedule exemption → (b) math, one engine query

Same as concern #3 above, applied to type 5 specifically. Type 5 skips the pool-size-max and per-category-count lower bars. Instead:
- Universal pool from engine queries
- Check viable schedule on it (2r + 1s × days)

**Engine need:** nothing new.

### 11. Structural sanity → (c) external to engine

Required top-level fields (task_id, city, days, start_date, etc.), hop-2 present, source_in_profile non-empty, etc. Pure schema shape check.

**Engine need:** nothing. Validator module.

### 12. Per-type exemptions → (c) external to engine

Dispatch logic: "if type 5, skip rules X and Y; apply rule Z instead." Pure control flow at the validator level.

**Engine need:** nothing.

---

## Summary — what the engine must support

**Engine additions needed (Phase 2):**

1. **Two validation-facing helpers** (~10 lines total):
   - `venues_matching(pool, scope, condition) → list[venue]`
   - `venues_in_scope(pool, scope) → list[venue]`

2. **Nested regulations dict access in `_activity_satisfies_condition`** (~8 lines):
   Currently the engine reads `v[field]` then falls through to `act[field]`. Some pools store regulations as `v["regulations"]["wheelchair_accessible"]` instead of flat `v["wheelchair_accessible"]`. Add a field lookup helper that checks flat → nested regulations → activity in order.

3. **Per-venue averaging for `aggregation: "all"` in the scoring wrapper** (~3 lines):
   Current engine returns 0/1 for `aggregation: "all"`. Scoring for label_required-style patterns expects fractional: `n_satisfying / n_matching`. Change the binary to fractional when `aggregation == "all"`.

4. **Flag-based partial credit modifier in `_activity_satisfies_condition`** (~10 lines):
   When an activity has `flags: ["keyword"]` and the condition fails, return a partial-satisfy marker rather than a binary false. Five handlers use this today. Probably add an optional param `allow_flag_credit=True` that returns a 3-way result (`True` / `"flagged"` / `False`), with the scoring wrapper converting `"flagged"` → 0.5.

5. **Field access via the nested regulations dict also in scope** (trivial):
   `_activity_matches_scope` doesn't access venue fields much, but `has_tag=` does. Same regulations-dict pattern.

**Engine additions NOT needed:**

- No new scope kinds (existing `all` / `activity_type=` / `category=` / `has_tag=` / `time_window=` + list-AND are enough)
- No new condition kinds beyond what exists
- No new aggregation modes — `all`, `none`, `at_least`, `at_most`, `exactly`, `count_distinct`, `ratio`, `sum` cover everything
- No plan-space-narrowing math inside the engine (it's B×B's own module)

**Total engine work:** ~30-40 lines across constraint_engine.py. Smaller than I expected going in.

---

## What Phase 4 builds on top of the engine

After Phase 2 lands, Phase 4 implements the validation rules using only engine primitives + arithmetic:

1. **Classification**: for each P-constraint, derive (universal / scoped / inclusion) from its (scope, aggregation) pair.
2. **Universal narrowing**: cumulatively call `venues_matching(pool, "all", condition)` for each universal constraint, producing `universal_pool`.
3. **Scoped narrowing**: for each scoped constraint, call `venues_matching(universal_pool, scope, condition)`.
4. **Inclusion checks**: for each inclusion constraint, call `venues_matching(universal_pool, scope, condition)`, verify `len(result) >= min_count`.
5. **Upper bars**: compute `n_satisfying_food / n_in_scope_food` and site equivalents, compare to 0.5 / 0.25 thresholds.
6. **Lower bars**: viable schedule check on `universal_pool` (2r + 1s × days).
7. **Tag affinity**: derive per-city affinity from pool (auxiliary compute), enforce on constraint declarations (subset check).
8. **A×A tension**: call `venues_matching` for each constraint separately, compute intersection size, compare to MIN_SIDE_COUNT + MAX_OVERLAP thresholds.
9. **B×B tension**: separate module (`plan_space_narrowing.py`) computing per-pattern narrowing factors. Not engine-based.
10. **P7 feasibility (type 2/4)**: stays external. Consumes `universal_pool` from the engine-based narrowing as its starting filtered pool.
11. **Per-type exemptions**: validator-level control flow.

Every concern is either engine-query, engine-math-on-top, or external module. Nothing is "engine needs to do something radically new."

---

## What we're NOT doing in Phase 2 or Phase 4

- **Not redefining patterns.** The 28 evaluator handlers are classified (Bucket A / B / C) in earlier sessions; Phase 3 migrates Bucket A and B to the engine via offline task-file rewrite, deletes their handlers.
- **Not changing the scoring semantics EXCEPT for** the per-venue averaging and flag partial credit gaps identified above. These are explicit, intentional changes that match what Bucket A/B handlers already do.
- **Not touching Bucket C handlers.** Weather, temporal_cross_day, dependency_chain, consecutive_pairs, must_visit — these have cross-activity or external-state logic the engine doesn't try to model.
- **Not adding new constraint patterns.** The generic schema CAN express new kinds of checks (condition + aggregation combinations we haven't written yet), but that's opportunistic. Phase 4 uses what's there.

---

## Risks flagged for Phase 2 / Phase 3

1. **Scoring drift on label_required and regulation_required** — current handlers give fractional via per-venue averaging. New engine path does the same after Phase 2 addition. Must verify on 60 real tasks during Phase 3a.

2. **Flag-based partial credit** — five handlers use `a.flags` to give 0.5 when the agent acknowledged a condition failure. Engine needs this addition to match. If not implemented exactly, tasks where agents flagged issues will lose partial credit they had before.

3. **`labels` vs `tags` field inconsistency** — some parts of the code read `v["labels"]`, some read `v["tags"]` or `v["category_tags"]`. The engine's `has_tag` helper reads `tags or category_tags`. Handlers read `labels`. Need to verify which field is actually populated at venue-creation time. This is a data-integrity question, not an engine-logic question. Flag for Phase 3a verification.

4. **`_translate_scope_for_pool`'s `activity_type=meal → [category=restaurant, category=cafe, category=bar]` translation** — this is correct at pool time but the same scope at schedule time uses `activity.activity_type` instead. The engine has an asymmetry between plan-scoring and pool-validation that we rely on. Document it but don't try to unify.

5. **Empty pools / empty constraints** — engine should return empty lists gracefully, not crash. Existing code mostly handles this; Phase 2 adds explicit tests for the validation helpers.

---

## Phase 1 conclusion

**The engine as a bottom wheel is sound.** Validation + scoring both compose its two predicates. The "dual question" structure (scoring asks yes/no on a plan; validation asks counts on a pool) resolves cleanly: validation uses the same predicates but wraps them in "list survivors" / "count survivors" helpers, then does arithmetic.

**Engine additions for Phase 2 are small** (~30-40 lines). No structural changes, just gap fills.

**Most validation logic lives in Phase 4, on top of the engine** — 50/50/25 bars, viable-schedule, tag affinity, tension detection, P7 integration — all as arithmetic on engine outputs + a few auxiliary modules for B×B and P7.

**Ready for Phase 2** — build the engine additions and their unit tests. Estimated 3-4 hours.
