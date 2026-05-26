# Pattern → Generic Schema Classification Table

> ⚠ **Older design doc.** Preserved as historical reference for design rationale.
> **Current implementation status:** see [docs/TODO_PHASE6.md](TODO_PHASE6.md) for the
> live work tracker, including any items in this doc that have been built, modified,
> or superseded.

**Purpose:** authoritative mapping from each of the 28 legacy `_handle_*` handlers to either (a) a generic-schema form the engine evaluates directly, or (b) Bucket C (stay as handler). Used by the Phase 3a migration script.

**Principle (per user directive):** no silent fallbacks. If a pattern's params don't match the expected shape, the translator fails loud with the task id and constraint id. Better to stop migration and fix the schema than to let a malformed constraint through.

**Binary scoring:** all migrated patterns score 0.0 or 1.0 via the engine (plus 0.5 when `at_least` is partially met — pre-existing engine behavior). Fractional per-venue averaging and flag-based partial credit from the old handlers are intentionally dropped — accepted as scoring drift.

---

## Bucket A — direct engine translation (binary pass/fail)

### 1. `time_threshold`
Intent: every target-type activity's time field satisfies an operator vs. a threshold.
```
params:              → generic:
  activity_type: X     scope: "activity_type=X"
  field: "time_start"  condition: {field, operator, value}
  operator: ">="       aggregation: "all"
  value: "15:30"
```
Required params: `field`, `operator`, `value`. Optional `activity_type` (default `"meal"`).

### 2. `numeric_aggregate`
Intent: aggregate a numeric field across activities, compare sum/max/count vs. threshold.
```
params:                → generic:
  field: cost            condition: {}   (sum aggregation reads field itself)
  activity_type: X       scope: "activity_type=X" + "per_day" if group_by="day"
  aggregate: "sum"       aggregation: {sum: field, operator, value}
  group_by: "day"|"trip"
  operator: "<="
  value: 80
```
Required: `field`, `aggregate`, `operator`, `value`. For `aggregate: "count"` → `{exactly: value}` or `{at_most/at_least: value}` depending on operator. For `aggregate: "max"` → translator emits a composite: `scope: ..., condition: {field <= value}, aggregation: "all"` (every activity ≤ value = max ≤ value). For `aggregate: "sum"` → `{sum: field, operator, value}` directly.

### 3. `label_required`
Intent: target activities have a required tag, universally OR at least K of them.
```
params:                    → generic:
  required_label: X          condition: {has_tag: X}
  activity_type: Y (def meal) scope: "activity_type=Y"
  min_count: N (optional)    aggregation: {at_least: N}  if min_count set
                                        | "all"          otherwise
```
Required: `required_label`. Optional `activity_type` (default `"meal"`), `min_count`.

### 4. `label_excluded`
Intent: target activities do NOT have an excluded tag.
```
params:                 → generic:
  excluded_label: X       condition: {has_tag: X}
  activity_type: Y        scope: "activity_type=Y"
                          aggregation: "none"
```
Required: `excluded_label`. Optional `activity_type` (default `"any"`).
Note: the `none` aggregation is semantically "no activity in scope may satisfy the condition."

### 5. `regulation_required`
Intent: target activities' regulation is truthy (or a specific value), universally OR at least one.
```
params:                      → generic:
  regulation_key: X            condition: {field: X, operator: "==", value: required_value}
  required_value: true (def)   scope: "activity_type=Y"
  activity_type: Y             aggregation: {at_least: 1}  if at_least_one=True
  at_least_one: false (def)                 | "all"          otherwise
```
Required: `regulation_key`. Field lookup goes through the engine's `_get_field_value` which handles both flat and nested regulations dict.
Required value semantics: if `required_value` param is missing, defaults to `True`.

### 6. `hidden_gem_required`
Intent: at least N activities are at low-traffic or hidden-gem / locals-favourite tagged venues.
```
params:            → generic:
  min_count: N (def 1) scope: "all"
                       condition: {any_of: [   # composite OR condition
                         {field: "traffic_tier", operator: "==", value: "low"},
                         {has_tag: "hidden-gem"},
                         {has_tag: "locals-favourite"},
                       ]}
                       aggregation: {at_least: N}
```
Required: none. `min_count` defaults to 1.
Note: this is a compound condition — engine already supports `{any: [...]}` composite.

### 7. `category_required`
Intent: at least one activity exists in each of the required categories.
```
params:                         → generic:
  required_categories: [A,B,C]    scope: "all"
                                  condition: {field: "category", operator: "in", value: [A,B,C]}
                                  aggregation: {count_distinct: 3, field: "category"}
```
Required: `required_categories` (non-empty list). N = `len(required_categories)` → `count_distinct: N`.

### 8. `noise_level_max`
Intent: every target activity's venue noise level ≤ threshold.
```
params:                     → generic:
  max_noise_level: X          condition: {field: "noise_level", operator: "<=", value: X}
  activity_type: Y (def any)  scope: "activity_type=Y"
                              aggregation: "all"
```
Required: `max_noise_level`.
Note: noise_level lives in nested regulations dict; `_get_field_value` handles it.

### 9. `district_excluded`
Intent: no activity at venues in the excluded districts.
```
params:                  → generic:
  excluded_districts: [X,Y] condition: {field: "district", operator: "in", value: [X,Y]}
                            scope: "all"
                            aggregation: "none"
```
Required: `excluded_districts` (non-empty list).

### 10. `district_count_max`
Intent: across the plan, at most N distinct districts are used.
```
params:         → generic:
  max_districts: N  scope: "all"
                    condition: {}
                    aggregation: {count_distinct: N, field: "district"}
```
Wait — `count_distinct` currently uses `>=` semantics (line 267: `passed = len(vals) >= agg["count_distinct"]`). For "max N distinct," we want `<=`. **Engine change needed**: add a new `{at_most_distinct: N, field: X}` aggregation, OR extend `count_distinct` to accept an operator.
Required: `max_districts`.
**FLAGGED FOR ENGINE ADDITION** during Phase 3a.

### 11. `category_count_minimum`
Intent (per agent-facing docs in `task_agent.py:475`): "at least N venues of a given category X."

Handler at line 1848 has a latent bug: it ignores `params["category"]` and reads `params["field"]` (default `"category"`), then counts distinct values. A plan with [restaurant, museum] has distinct-count=2, passing "museum min 2" trivially — without any museums.

**Migration policy (per user direction): translate based on agent intent, not handler behavior.** Tasks that relied on the buggy pass will re-score differently after migration. That's accepted — such tasks were invalid constraints anyway.

```
params:             → generic:
  category: X         scope: "category=X"   OR scope: "has_tag=X" if field signals a tag
  min_count: N        condition: {}
  field: "category"   aggregation: {at_least: N}
         (default)
```

Required: `min_count`. Optional `category`. `field` param is respected:
- `field: "category"` (default) + `category: X` → `scope: "category=X"`, aggregation `{at_least: N}`
- `field: "cuisine_label"` → this is the one case where the handler's distinct-count behavior was probably intended (diverse cuisines). Migrate as `scope: "activity_type=meal"`, `condition: {}`, `aggregation: {count_distinct: N, field: "cuisine_label"}` — preserving distinct-count.
- If `category` param is missing and `field=category` → **fail loud** (can't know which category agent meant).

### 12. `max_single_activity_cost`
Intent: every target activity's cost ≤ max.
```
params:             → generic:
  max_usd: N          condition: {field: "estimated_cost_local", operator: "<=", value: N}
  activity_type: X    scope: "activity_type=X"
                      aggregation: "all"
```
Required: `max_usd`.

### 13. `price_tier_required`
Intent: every target activity's venue price_tier satisfies the tier band.
```
params:                  → generic:
  min_tier: X (optional)   condition: {field: "price_tier", operator: ">=", value: min_tier}
  max_tier: Y (optional)       | {field: "price_tier", operator: "<=", value: max_tier}
  activity_type: Z             | composite {all: [>=min, <=max]}
                           scope: "activity_type=Z" (default meal)
                           aggregation: "all"
```
Required: at least one of `min_tier` / `max_tier`. If both → composite `all` condition.

**No silent fallback (per user direction):** if a constraint has `pattern: "price_tier_required"` with neither `min_tier` nor `max_tier`, the translator raises `ValueError("Task {tid} constraint {cid}: price_tier_required requires at least one of min_tier/max_tier — the constraint is meaningless without bounds")` and migration aborts. The legacy handler silently treated this as "always passes" (max_idx=top, min_idx=bottom → every venue qualifies), which is a bug that hid invalid tasks. Migration surfaces these rather than inheriting the bug.

Confirmed by audit of 8 real-task usages: all have `min_tier` set, none hit this case.

### 14. `min_age`
Intent: every target venue permits the group's min age.
```
params:               → generic:
  group_min_age: N      condition: {any_of: [
  activity_type: X        {field: "min_age", operator: "==", value: null},  # no restriction
                          {field: "min_age", operator: "<=", value: N},     # restriction ≤ group min
                        ]}
                        scope: "activity_type=X"
                        aggregation: "all"
```
Required: `group_min_age`.
Note: `min_age` (aka `age_restriction`) lives in nested regulations. The `== null` check here represents "field absent or explicitly null" — the engine's `==` with `value: null` requires careful handling (see ENGINE ADDITION below).
**FLAGGED FOR ENGINE ADDITION:** `operator: "=="` with `value: null` should match venues where the field is None/absent. Current engine's `_get_field_value` returns None for absent fields, and `_activity_satisfies_condition` returns False when actual is None (line ~101). Needs adjustment so explicit `value: null` matches absent.

### 15. `dress_code_required`
Intent: every target venue's dress_code ≥ required level (ordered enum).
```
params:                → generic:
  required_dress_code: X condition: {field: "dress_code", operator: ">=", value: X}
  activity_type: Y       scope: "activity_type=Y"
                         aggregation: "all"
```
Required: `required_dress_code`.
Engine ordered-enum comparison already supports `price_tier`, `traffic_tier`, `recommended_pace`. Add `dress_code` to the ordered-enum list (DRESS_CODE_ORDER = ["none", "casual", "smart_casual", "formal"]).
**FLAGGED FOR ENGINE ADDITION:** dress_code ordered-enum comparison.

### 16. `pace_relaxed`
Intent: every target venue's recommended_pace ≤ threshold (ordered enum: relaxed < moderate < intense).
```
params:              → generic:
  max_pace: X (def moderate)  condition: {field: "recommended_pace", operator: "<=", value: X}
  activity_type: Y     scope: "activity_type=Y"
                       aggregation: "all"
```
Required: none (max_pace defaults to "moderate").
Engine already supports pace ordering.

### 17. `max_visit_duration`
Intent: every target activity's duration ≤ max minutes.
```
params:                    → generic:
  max_minutes: N              condition: {field: "recommended_visit_minutes", operator: "<=", value: N}
  activity_type: X            scope: "activity_type=X"
                              aggregation: "all"
```
Required: `max_minutes`.
Note: `recommended_visit_minutes` is a venue field, flat — no regulation nesting.

### 18. `local_cuisine_preference` — **Bucket C**
Intent: at least X fraction of meals are at venues tagged with the city's local cuisine.
```python
ratio = params.get("min_ratio", 0.5)
# counts meals where venue has tag matching city's local_cuisine_label
# passes iff local_cuisine_count / meal_count >= ratio
```
Translation requires a city-specific cuisine label injected at scoring time. The handler reads this from a city config. The engine doesn't have city context. **Stays as handler.**

### 19. `cuisine_diversity_minimum` — **Bucket C**
Intent: at least N distinct cuisines across meals.

Handler at line 2338 reads cuisine_label from venue tags (matches cuisine name against an allowlist). `cuisine_label` isn't a venue field directly — it's derived from tags filtered by an allowlist. The engine's `count_distinct` reads `field` from venue or activity, so it can't derive cuisine_from-tags. **Stays as handler.**

### 20. `must_visit`
Intent: at least N activities match a specific venue / label / category / name.
```
match_type="venue_id"   → scope: "venue_id=X"
match_type="label"      → scope: "has_tag=X"
match_type="category"   → scope: "category=X"
match_type="venue_name" → scope: "venue_name~X"   (new primitive, substring match)

All variants:          condition: {}
                       aggregation: {at_least: min_count}
```
**FLAGGED FOR ENGINE ADDITION:** two new scope primitives:
- `"venue_id=X"` — activity's venue_id equals X
- `"venue_name~X"` — activity's venue_name contains substring X (case-insensitive)

Required: `match_type`, `value`. Optional `min_count` (default 1), `activity_type` (default "any").

---

## Bucket C — stay as handlers (do NOT migrate)

### 21. `weather_aware` — Bucket C
External state: reads `weather_forecast.json`, joins by date, compares to venue outdoor_sensitivity. City + date context required. Engine has no notion of external state.

### 22. `temporal_cross_day` — Bucket C
Cross-day aggregation with "at most N activities per day matching X." The engine has per_day scope but this handler has a special structure (day index + cross-day condition) that doesn't fit cleanly.

### 23. `dependency_chain` — Bucket C
For each activity with a dependency requirement, checks that a supporting activity exists before/after within a time window. Cross-activity relational logic.

### 24. `consecutive_pairs` — Bucket C
Checks ordered consecutive-activity pairs satisfy a relational predicate. Cross-activity relational logic.

### 25. `opening_time_required` — Bucket C
Checks venue hours on the specific day-of-week the activity is scheduled. Requires day-of-week + hours dict lookup the engine doesn't have.

### 26. `opening_time_window` — Bucket A
Single-venue constraint: "if this venue_id is in plan, it must start ≤ latest_start." Once `venue_id=X` scope primitive is added to the engine, this translates cleanly. Engine already returns 1.0 for empty matching set (matches handler's "venue not in plan → not applicable" behavior).
```
params:              → generic:
  venue_id: X          scope: "venue_id=X"
  latest_start: HH:MM  condition: {field: "time_start", operator: "<=", value: HH:MM}
                       aggregation: "all"
```
Required: `venue_id`, `latest_start`.

### 27. `transport_mode_required` — Bucket A
Intent: target activities use a required travel_mode, universally OR at least one.
```
params:                → generic:
  required_mode: X       condition: {field: "travel_mode", operator: "==", value: X}
  at_least_one: bool     scope: "activity_type=Y" (default any)
  activity_type: Y       aggregation: {at_least: 1} if at_least_one=True
                                    | "all"           otherwise
```
Required: `required_mode`. Optional `at_least_one` (default False), `activity_type` (default "any").

### 28. `social_match` — Bucket A (needs `contains` operator)
Intent: target activities' venues have the occasion in `suitable_occasions` list AND `max_group_size ≥ group_size`.
```
params:                → generic:
  occasion: X            condition: {all: [
  group_size: N            {field: "suitable_occasions", operator: "contains", value: X},
  activity_type: Y         {field: "max_group_size", operator: ">=", value: N},
                         ]}
                         scope: "activity_type=Y" (default any)
                         aggregation: "all"
```
Required: `occasion`, `group_size`. Optional `activity_type` (default "any").

**FLAGGED FOR ENGINE ADDITION:** `"contains"` operator — see Engine additions section below.

---

## Final bucket assignment

**Bucket A — migrate to engine (21 patterns):**
1. time_threshold
2. numeric_aggregate
3. label_required
4. label_excluded
5. regulation_required
6. hidden_gem_required
7. category_required
8. noise_level_max
9. district_excluded
10. district_count_max — *engine addition: at_most_distinct aggregation*
11. category_count_minimum — *intent-based migration; buggy old behavior not preserved*
12. max_single_activity_cost
13. price_tier_required — *fail-loud if neither min nor max*
14. min_age — *engine addition: null-match for equality-to-absent*
15. dress_code_required — *engine addition: dress_code ordered enum*
16. pace_relaxed
17. max_visit_duration
18. must_visit — *engine addition: venue_id=X and venue_name~X scope primitives*
19. opening_time_window — *uses the new venue_id=X primitive (shared with #18)*
20. transport_mode_required
21. social_match — *engine addition: "contains" operator*

**Bucket C — keep as handlers (7 patterns):**
1. weather_aware
2. temporal_cross_day
3. dependency_chain
4. consecutive_pairs
5. opening_time_required
6. local_cuisine_preference
7. cuisine_diversity_minimum

---

## Engine additions required for Bucket A migration

These must land BEFORE running the migration script. No silent fallbacks — if a pattern's translation fails, migration stops.

1. **`venue_id=X` scope primitive** (for `must_visit`, `opening_time_window`)
   - `_activity_matches_scope` recognizes scope starting with `"venue_id="` and checks `act.venue_id == X`.

2. **`venue_name~X` scope primitive** (for `must_visit` with `match_type=venue_name`)
   - `_activity_matches_scope` recognizes scope starting with `"venue_name~"` and checks `X.lower() in act.venue_name.lower()`.
   - `~` rather than `=` signals substring-match semantics.

3. **`at_most_distinct` aggregation** (for `district_count_max`)
   - Sibling of existing `count_distinct`. `passed = len(vals) <= agg["at_most_distinct"]`.
   - New aggregation key rather than extending `count_distinct` with an operator — keeps each aggregation key's semantics unambiguous.

4. **Null-match operator** (for `min_age`)
   - Currently `actual is None → return False`. For explicit `value: null` equality, we want matching when the field is absent/None.
   - Fix: before the `if actual is None: return False` guard, check if `value is None` and `op in ("==", "!=")`. Handle equality-to-null explicitly.

5. **`dress_code` ordered enum** (for `dress_code_required`)
   - Add a `DRESS_CODE_ORDER = ["none", "casual", "smart_casual", "formal"]` constant.
   - Add an ordered-enum branch in `_activity_satisfies_condition` mirroring the existing price_tier / traffic_tier / recommended_pace blocks.

6. **`"contains"` operator** (for `social_match`)

   Direction matters — `contains` and `in` are **mirror operators**:

   | Operator | What LHS is | What RHS is | Check |
   |---|---|---|---|
   | `in` (already exists) | field value (usually scalar) | list | `actual in value` → is this venue's district one of [A, B, C]? |
   | `contains` (new) | field value (must be a list) | scalar | `value in actual` → does this venue's suitable_occasions list contain "anniversary"? |

   Same type of question, opposite data shapes. `has_tag` is already a special case of `contains` hardcoded to the `tags` field; `contains` generalizes this to any venue-side list field (`suitable_occasions`, or future list-valued fields).

   Implementation in `_activity_satisfies_condition`:
   ```python
   if op == "contains":
       # actual is expected to be a list
       return value in (actual or [])
   ```
   ~3 lines, symmetric with existing `in` branch. One unit test verifying direction.

**Estimated engine additions:** ~45 lines across `_activity_matches_scope`, `_activity_satisfies_condition`, and the ordering constants block. Plus ~12 new unit tests.

---

## Migration script plan

Input: all task JSONs in `data/data1/tasks/unfiltered/*/`.

For each task:
  For each constraint in `rubric.personal_constraints`:
    - Read `pattern` and `params`
    - If pattern in Bucket A (20 patterns):
      - Apply the pattern-specific translation rule from this table
      - Replace `pattern`/`params` with `scope`/`condition`/`aggregation`/`consequence`
      - Preserve: `id`, `score_tier`, `hop`, `source_in_profile`, `description`, `check_method`
      - Add `consequence: "p_score_full"` (score_tier is always "P" on personal_constraints)
    - If pattern in Bucket C (8 patterns):
      - Leave constraint shape unchanged
    - If pattern unknown (not in either bucket):
      - **Fail loud**: print task_id, constraint id, unknown pattern, and abort migration.

  Write migrated task back in place.

**No `.bak` files** (user has duplicate repo for rollback).
**Fail loud** on any unknown pattern or invalid params.

After successful migration on all tasks, delete the migration script itself.

---

## Decisions locked (from Phase 3a step 1 review)

1. **`opening_time_window` and `transport_mode_required` → Bucket A.** Both express cleanly in generic form once the engine additions land. Done.

2. **`category_count_minimum` not renamed.** The pattern name dies at migration — all references either get rewritten in task files or updated in Phase 3b handbook. No rename step needed.

3. **`category_count_minimum` migrated by agent intent, not by buggy handler behavior.** Tasks that relied on the old "any 2 distinct categories pass" bug will re-score. Such tasks were invalid constraints; losing them is acceptable.

4. **`price_tier_required` with neither min nor max fails loud.** Migration aborts with task+constraint id. No silent "always passes." Confirmed zero real tasks hit this today.

5. **Engine additions happen first.** Prerequisites for the migration script. Phase 3a substeps: (a) engine additions, (b) migration script, (c) evaluator dispatch rewire + handler deletion, (d) test fixture updates, (e) full suite + real-task smoke check.

6. **`consequence: "p_score_full"` added uniformly on migrated P-constraints.** Legacy agents don't write this field; translator fills it in. Matches the generic-schema design doc.

7. **Error logging for invalid constraints** (e.g. price_tier with no bounds): translator raises Python exceptions that surface in the migration script's output. Phase 3b handbook rewrite will teach future agents not to produce these shapes.

---

## Summary counts

- **28 handlers total**
- **21 → Bucket A** (migrate, delete handler)
- **7 → Bucket C** (keep as handler)
- **6 small engine additions** needed before migration runs
- Estimated Phase 3a effort: engine additions (1h) + migration script (1h) + evaluator dispatch rewire (1h) + test-fixture updates (1h) = **~4h total**
