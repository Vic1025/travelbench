# TravelBench Validation Design — current state + proposed unification

> ⚠ **Older design doc.** Preserved as historical reference for design rationale.
> **Current implementation status:** see [docs/TODO_PHASE6.md](TODO_PHASE6.md) for the
> live work tracker, including any items in this doc that have been built, modified,
> or superseded.

**Status: design document, reconciling completed Phase 3/4 work with P9/P10/P12/P13 in flight**
**Last updated:** this session
**Read before:** implementing P13 or any Phase 4 subtask that touches validation

---

## Purpose

The validation pipeline has grown organically across Phases 2, 3, and 4. Each structural type has accumulated its own checks; the P-constraint handling has muddled through several versions. The work planned for P13/P9/P10/P12 will be the first unification of the whole picture. This document is the "how do all the pieces fit together" reference — what checks apply when, why, and what the data flow looks like through the whole validation.

Not a spec. This is the conceptual map. Specific implementation details live in individual subtask docs.

---

## Structural types and their difficulty sources

From `docs/task_structural_types.md`, each of the six structural types has a DIFFERENT source of difficulty. The validator must enforce each type's specific difficulty mechanism without applying checks that don't belong.

| Type | Name | Difficulty source | Validator checks for |
|---|---|---|---|
| 1 | Cascading Requirements | Hidden implicit constraints inferred from query | Hop-2 P-constraints present; universal checks |
| 2 | Subset Selection Under Ceiling | Time ceiling in query text (not P-filters) | Time feasibility (P7 algorithm) |
| 3 | Competing Requirements | P-P constraint tension (two competing pulls) | Tension detection (P9 redesign) |
| 4 | Precision Allocation | Budget ceiling in query text (not P-filters) | Budget feasibility (P7 algorithm) |
| 5 | Hard Feasibility Reduction | Pool-narrowing stack (narrowness IS the task) | Viable schedule exists (P10 redesign) |
| 6 | Context-Window Tension | Seasonal-window event vs. persona requirement | Doc consultation ("did agent call get_official_site") |

The critical insight that drove the P6 audit: not every check applies to every type. Applying "constraint tension required" to type 2 (where difficulty comes from the time ceiling, not P-P interaction) produces false rejections. P6 fixed the exemption tables; subsequent subtasks fill in the type-specific checks that were missing or wrong.

---

## The data flow through validation

Each task goes through two layers:

```
SUBMIT → validate_task_schema → _verify_task_solvable → save
         (structural checks)     (pool-sensitive checks)
```

### Layer 1: `validate_task_schema(task, pool)` in `test_generate_tasks.py:1440`

Schema and structural checks that don't need to walk the pool. Runs on every type. Reports a list of issues.

Current checks (as of P6 + P7):
- **Top-level schema**: `task_id`, `city`, `days`, `start_date`, `public_input`, `rubric` all present
- **Query sanity**: `public_input.query` non-empty, 20-600 chars, no constraint jargon leakage
- **Hop-2 rule**: at least one P-constraint with `hop == 2`
- **Source-in-profile (A1)**: every hop-2+ constraint must have `source_in_profile` pointing to a verbatim phrase from the query (not "inferred" / "N/A")
- **Type vs pattern confusion**: hard_constraints use `type`, P-constraints use `pattern`
- **B-score shape**: required fields, script_code validity if `python_script`
- **Character trait limits**: max 2 Cat-2 signals per task (4 for type5)
- **query_resources validation (P7)**: `time_ceiling_minutes` required for type2, `budget_per_day` required for type4
- **P-P constraint tension (current, to be replaced by P9)**: for type 3 and type 5, at least one pair of P-constraints must create tension. Currently uses `_SEMANTIC_TENSION_PAIRS` whitelist + a narrowing-threshold heuristic (both flawed — P9 replaces).

### Layer 2: `_verify_task_solvable(task, venue_pool)` in `generate_task.py:788`

Pool-sensitive checks. Runs on every type but with per-type exemptions. Returns `(bool, reason)`.

Current stages:
1. `filtered = _apply_pool_filters(venue_pool, task)` — applies hard constraints + every P-constraint universally (**BUG: P13 will fix this**)
2. If `filtered == []` → reject "Constraints eliminate all venues"
3. Pool-size-max (for type3/type5 only, per P6): `len(filtered) > 25` → reject "pool too large"
4. Per-pattern solvability checks:
   - `category_count_minimum`: ≥ min_count venues of named category
   - `hidden_gem_required`: ≥ min_count hidden-gem venues
   - `label_required`: at least one venue with the label
   - `regulation_required`: at least one venue with the regulation true
5. Type 2: time-ceiling feasibility (P7 algorithm)
6. Type 4: budget-ceiling feasibility (P7 algorithm)

---

## Per-type validation in detail

### Type 1 — Cascading Requirements

**Difficulty source:** Agent must INFER hidden constraints from the query. E.g., "my grandmother is visiting from India" implies Indian food + slower pace + accessible + maybe dietary. The rubric captures these inferences; validator checks they exist.

**Always satisfiable** per spec — cascaded constraints make the plan harder but never impossible.

**Layer 1 checks (specific to type 1):**
- Hop-2 rule applies (need at least one inference)
- Source-in-profile tracing (A1) catches fake inferences
- NO tension requirement (P6: correctly exempted — cascading is compounding, not competing)
- NO pool-size-max (P6: correctly exempted — narrowness isn't the point)

**Layer 2 checks (specific to type 1):**
- Pool must support a viable schedule (universal-pool check — becomes explicit in P13)
- No per-type-1-specific check beyond general solvability

**Open items:**
- Once P13 lands, the universal-pool viable-schedule check becomes explicit for type 1 (currently it's implicit via "not empty + per-pattern checks").

---

### Type 2 — Subset Selection Under Ceiling

**Difficulty source:** A TIME CEILING in the query text (e.g., "5 hours available") is tight relative to the minimum time for the required venues. Agent must select a subset that fits.

**Validity guarantee:** at least one viable subset must fit the ceiling.

**Layer 1 checks:**
- `query_resources.time_ceiling_minutes` must be present (P7 added)
- NO tension requirement (P6 exempted)
- NO pool-size-max (P6 exempted — narrowness isn't the point)

**Layer 2 checks:**
- `_verify_task_solvable` runs the P7 time-ceiling algorithm:
  - Determine K (min venues to visit): `max(days × 2, category_count_minimum)`
  - Fetch K nearest venues to pool centroid (with coords)
  - Compute min_time = `Σ(0.5 × recommended_visit_minutes) + nearest-neighbor-travel`
  - Apply ratio band: `1.1 × min_time ≤ stated_ceiling ≤ 1.3 × min_time`
  - Cap check: `stated_ceiling ≤ 10 hours × days`
  - Fallback (no coords): check against flat 15-min travel assumption, floor-only

**User's memory of this algorithm vs. actual:**
- User recalled "3 nearest venues × 1.1 bottom, 1.3 top" — almost right. K scales with `category_count_minimum`, not fixed at 3.
- User recalled "include logic for assigning a specific venue" — this is the `required_venue_ids` concept from Type 4. Not currently in Type 2. Could be added if we want "must visit X + fit in Y hours" tasks.

**What's complete:**
- P7 algorithm fully implemented
- 25 E3 tests passing

**What still depends on P13:**
- The current algorithm runs on `filtered` which is produced by the buggy universal-everything pool filter. After P13, `filtered` will be replaced by `universal_pool` (only-universals applied). Scoped/inclusion filters don't narrow the universal pool, so they don't distort the time feasibility check incorrectly. Net effect: time feasibility becomes more accurate post-P13.

---

### Type 4 — Precision Allocation

**Difficulty source:** A BUDGET CEILING in the query text (e.g., "£40/day") is tight relative to the cheapest viable plan cost. Agent must allocate intelligently (shift spend toward high-value uses, exploit free entries).

**Validity guarantee:** at least one viable venue combo must fit the budget.

**Layer 1 checks:**
- `query_resources.budget_per_day` required (P7, in local currency per P8)
- NO tension requirement (P6 exempted — allocation pressure comes from budget not P-P)
- NO pool-size-max (P6 exempted)

**Layer 2 checks:**
- `_verify_task_solvable` runs the P7 budget-ceiling algorithm:
  - Split filtered pool into restaurants vs. sites (by category)
  - Sort each by `avg_cost_local` ascending
  - `k_r = RESTAURANTS_PER_DAY × days` = 2 × days; `k_s = SITES_PER_DAY × days` = 2 × days
  - Reject if insufficient restaurants or sites
  - `base_min = Σ(cheapest k_r restaurants) + Σ(cheapest k_s sites)`
  - `premium_add = Σ(cost of required_venue_ids that exceed 1.5 × category median)`
  - `min_budget_per_day = (base_min + premium_add) / days`
  - Apply ratio band: `1.1 × min_budget_per_day ≤ stated_budget ≤ 1.3 × min_budget_per_day`

**User's memory of this algorithm vs. actual:**
- User recalled "cheapest per day trip which is one day 2+2, sum up price *number of members" — the 2+2 and sum is right. Number of members is NOT in the current algorithm. Currently assumes a single person. This may be a gap.
- User recalled "possibly assigning a venue here" — yes, `required_venue_ids` + premium add IS in the algorithm.
- User recalled "1.1 bottom, 1.3 top" — correct.

**Gap identified in user's comment:**
- If a task specifies a group (e.g., family of 4), cost per day = `(base_min + premium_add) × num_people`. Not currently scaled. Worth adding to the algorithm IF group size is carried in the task schema.

**What's complete:**
- P7 algorithm for single-person budget
- Premium-add for required_venue_ids
- E3 tests passing

**What still depends on P13:**
- Same as Type 2 — `filtered` replaced by `universal_pool`. Scoped filters (like "upscale dinner") don't narrow the whole pool post-P13, so the cheapest-2-restaurants-per-day computation remains accurate (not distorted by universal-upscale bug).

---

### Type 3 — Competing Requirements

**Difficulty source:** Two P-constraints pull at DIFFERENT parts of the pool. Agent must find a non-obvious tradeoff — can't satisfy both with the same venue.

**Validity guarantee:** both "sides" must exist in the pool in reasonable quantity.

**Layer 1 checks (current state, all being replaced):**
- Hop-2 rule applies
- Constraint tension required (current broken logic uses `_SEMANTIC_TENSION_PAIRS` whitelist + narrowing-threshold heuristic)
- Pool-size-max = 25 applies (current; correct in spirit but P13 changes what "pool" means)

**Layer 1 checks (post P9 + P13):**
- Hop-2 rule still applies
- **Tension detection via scope-aware pool comparisons**:
  - `universal × universal`: small-overlap metric on universal_pool venue sets
    - Both sides ≥ 3 venues, overlap ≤ 3 OR overlap ≤ 25% of smaller set
  - `scoped × scoped (same scope)`: small-overlap on the shared slot candidate sets
  - `inclusion × inclusion`: small-overlap on inclusion_pools
  - `universal × inclusion`: tension if universal_pool starves the inclusion (inclusion_pool in universal_pool < min_count)
  - Other pair combinations are NOT tension
- **B×B plan-space narrowing** (for pairs involving plan-level patterns like `cuisine_diversity_minimum`, `district_count_max`):
  - Stacked narrowing product ≤ 5% of baseline AND ≤ 2000 × days absolute

**Layer 2 checks:**
- Viable schedule check (becomes stage-1 universal-pool check post-P13)
- Per-pattern solvability checks appropriate to each filter mode:
  - Universal: universal_pool supports 2r + 1s × days (viable schedule)
  - Scoped: each scoped_pool has ≥ 2 venues (for tension use) or ≥ 1 (feasibility only)
  - Inclusion: each inclusion_pool has ≥ min_count venues

**Scoping considerations for type 3:**
Type 3 tension requires real competition, which means scope must match. Two examples:
- `wheelchair_accessible (universal) × outdoor (universal, scoped to sites)`: legitimate tension IF the wheelchair venues and outdoor site venues have small overlap.
- `upscale dinner (scoped to meals) × quiet (universal)`: both apply, but they're not competing — compounding. NOT type 3 tension.

The tension detection rules above reflect this.

**What still depends on P9, P13:**
- Everything. Current type 3 logic is fundamentally wrong for filter-style patterns and broken for scoping. P13 + P9 together replace the entire type-3 validator.

---

### Type 5 — Hard Feasibility Reduction

**Difficulty source:** Constraint STACKING narrows the pool to a tiny viable set (1-5 venues). The task is about discovering WHICH venues even exist, not optimizing among many.

**Validity guarantee (per spec):** intersection ≥ 1 viable venue.

**Layer 1 checks:**
- Character trait cap relaxed (max 4 instead of 2 — type 5 may stack)
- Same-cluster stacking allowed (type 5 exemption)
- Tension required (current)

**Layer 2 checks (current, being replaced by P10):**
- `_verify_task_solvable` runs the standard per-pattern checks — which are wrong for type 5 because type 5's narrowness IS the point. Current check rejects type 5 for producing the narrow pool it's supposed to produce.

**Layer 2 checks (post-P10):**
- Pool-size-max exempted (type 5's narrowness is valid)
- Viable schedule check: universal_pool has ≥ `2 × days` restaurants AND ≥ `1 × days` sites
  - NOT per-category_count_minimum — stacking may leave few-of-specific-categories, but as long as a viable schedule can be built, pass
- Per-pattern checks still run but are scope-aware (post-P13): only universals narrow the universal_pool

**Scoping considerations for type 5:**
Type 5 is the most likely type to hit the universal-vs-scoped bug. Agents stack 3+ regulation_required filters (universal). They might also add scoped filters like "upscale dinner." In the current code, these all apply universally and wipe out the pool. Post-P13:
- Universals stack → universal_pool narrows to 3-5 venues (correct, that's the point)
- Scoped filters → narrow the slot pools (correct, doesn't over-filter)
- Viable-schedule check passes as long as 2r + 1s × days can be built from universal_pool

**What still depends on P10, P13:**
- Same pipeline refactor as type 3. Type 5's solvability redesign is smaller but critical.

---

### Type 6 — Context-Window Tension

**Difficulty source:** A seasonal event creates a specific context (sold-out dates, modified hours, closure zones). The agent's persona wants something that conflicts with this context. Agent must consult the docs (`get_official_site`) to discover the conflict.

**Validity guarantee:** always satisfiable — tension is about quality of adaptation, not existence.

**Layer 1 checks:**
- NO tension requirement (P6 exempted — seasonal tension isn't P-P)
- NO pool-size-max (P6 exempted)
- Special: B-score may include `doc_appeared` pattern as evidence agent consulted docs

**Layer 2 checks:**
- Standard solvability (viable schedule)
- No type-6-specific pool check

**What still depends on anything:**
- Nothing urgent. Type 6 is stable. P13 tightens what "filtered" means but behavior unchanged.

---

## The P-constraint scope model (P13)

The single biggest architectural change across all types. Today every P-constraint is applied universally by `_apply_pool_filters`. This breaks most real-world intent. P13 introduces **three filter modes**, each with distinct pool narrowing and distinct bars.

### The three modes — conceptually

**Universal** — "every venue in the plan must satisfy this"
- Example: "wheelchair accessible," "quiet venues," "dog-friendly trip"
- Effect: narrows the whole-plan pool (called `universal_pool`)
- Filter strength expected: strong (50%+ narrowing on the category it applies to)

**Scoped** — "the venues of this type/time must satisfy this"
- Example: "upscale dinner" (scope: activity_type=meal OR category=restaurant + time_window=evening)
- Effect: narrows the scoped-slot candidate set, NOT the whole pool
- Filter strength expected: strong (50%+ narrowing within the scope)

**Inclusion** — "at least K venues matching this must appear in the plan"
- Example: "include 2 museums," "include one hidden gem"
- Effect: narrows the inclusion candidate set (candidates meeting the criterion), NOT the whole pool
- Filter strength expected: very strong (75%+ of pool must FAIL the criterion)

### Food vs. Site partitioning (user-raised insight)

All venues fall into one of two groups by category (already in DB as `category` field — no new tag needed):

**FOOD** = `restaurant`, `cafe`, `bar`
**SITE** = `museum`, `attraction`, `park`, `neighbourhood`

These groups are the natural measurement scale for pool narrowing:
- Viable schedule = `2 FOOD + 1 SITE per day` (minimum)
- Upper-bar checks compare narrowed pool to size of food/site group, not whole pool

A constraint that narrows "food" meaningfully but doesn't affect "site" is legitimate (it's a food-specific filter). Scoping to meals makes this explicit.

### The five-stage solvability check (post-P13)

```
Stage 1: Build universal_pool
  universal_pool = venue_pool
  for pc in personal_constraints:
    if pc.scope_mode == "universal":
      universal_pool = apply_filter(universal_pool, pc)

Stage 2: Build scoped sub-pools
  for pc in personal_constraints:
    if pc.scope_mode == "scoped":
      slot_venues = filter_by_scope(universal_pool, pc.scope)
      scoped_pools[pc.id] = apply_filter(slot_venues, pc)

Stage 3: Build inclusion candidate sets
  for pc in personal_constraints:
    if pc.scope_mode == "inclusion":
      inclusion_pools[pc.id] = apply_filter(universal_pool, pc)

Stage 4: Lower bounds (feasibility)
  - universal_pool supports 2r + 1s × days (viable schedule)
  - each scoped_pool ≥ 1 venue (≥ 2 if this pc participates in tension)
  - each inclusion_pool ≥ min_count

Stage 5: Upper bounds (meaningfulness — CUMULATIVE narrowing, not per-filter)
  - After ALL universal filters AND-ed together: universal_pool ≤ 50% of FOOD
    group AND ≤ 50% of SITE group (checks the STACK's cumulative narrowing)
  - Scoped filters grouped by scope target, AND-ed within each scope:
    each group's final narrowed pool ≤ 50% of that scope's FOOD or SITE share
  - Each inclusion checks INDEPENDENTLY: inclusion_pool ≤ 25% of FOOD or
    SITE (whichever the filter targets)
```

**Important: universals and scoped filters STACK cumulatively.** The upper bar checks whether the agent's full stack narrows each group meaningfully, not whether each individual filter narrows on its own. This means:

- A single weak filter like `regulation_required: family_friendly` (60% FOOD, 92% SITE) fails alone.
- But paired with `noise_level_max: moderate` that also narrows both groups, the cumulative stack might pass.
- The rejection message identifies which GROUP (FOOD or SITE) is under-narrowed, so the agent can add targeted filters rather than guessing.

Inclusions don't stack — each "include K of this" is evaluated independently against the 25% bar. Two inclusions on the same category (e.g., "include 2 museums" + "include 1 hidden-gem museum") don't narrow each other; they specify two separate plan constraints.

**Threshold rationale (user-proposed, confirmed sensible):**
- Universal/scoped at 50% per-group: both apply across many activities, so moderate narrowing suffices to shape the plan.
- Inclusion at 25%: applies to just K slots, so the criterion must exclude most candidates to meaningfully shape those slots. 25% is strict because inclusion with 40% candidates is weak — any plan trivially satisfies.

**Lower bar rationale:**
- Viable schedule is the universal lower bar: if `universal_pool` can't support `2r + 1s × days`, no plan is possible regardless of other constraints.
- Scoped lower bar of ≥ 1 is feasibility; ≥ 2 is for real choice (used when tension detection needs the pool to be meaningful).
- Inclusion lower bar of `≥ min_count` is obvious and already implemented.

---

## Tag-category affinity (auto-derived per city)

When an agent writes `label_required: vegetarian`, the validator needs to know that "vegetarian" is a food tag (applies to `restaurant`/`cafe`, not `museum`/`park`). Currently no such check exists; `_apply_pool_filters` applies the filter universally and wipes out non-food venues (which don't have the "vegetarian" tag).

**Proposed (user-suggested, pool-derived):** compute per-city tag affinity from real pool data.

```
For each tag t in pool:
  cats = [venue.category for venue in pool if t in venue.tags]
  cat_fractions = {cat: count / venues_of_category_count[cat] for cat in cats}
  
  if max(cat_fractions) ≥ 0.8 and second_highest < 0.1:
    affinity[t] = {category_with_max_fraction}   # concentrated in one category
  elif ≥ 2 categories have cat_fraction ≥ 0.3:
    affinity[t] = {those categories}             # multi-category tag
  else:
    affinity[t] = None                            # universal (too spread to restrict)
```

Auto-recomputed when pool changes. New cities get their own table. New tags auto-classified.

**Validator rule:**
When agent writes `label_required` or `label_excluded` with tag `t`:
- If `affinity[t] == None` → any scope accepted
- If `affinity[t] == {cat1, cat2}`:
  - `scope_mode: universal` → REJECT ("tag applies only to these categories, scope accordingly")
  - `scope_mode: scoped` with `scope: {category: ...}` → categories must be a subset of `affinity[t]`
  - `scope_mode: inclusion` → accepted (inclusion narrowing math handles the category scoping naturally — the inclusion_pool will be inside the affinity categories)

This makes category-scoping automatic where the data supports it, and fails fast when an agent mixes categories incorrectly (e.g., "vegetarian museum").

---

## What this unifies

Cross-cutting view: what each subtask covers in this design.

| Subtask | Structural types touched | Scope-model stage touched |
|---|---|---|
| P6 ✅ | all (exemption audit) | — |
| P7 ✅ | 2, 4 | — (uses filtered pool, becomes universal_pool post-P13) |
| P8 ✅ | 4 (currency) | — |
| P13 📝 | all (pipeline) | stages 1-5 introduced |
| P9 📝 | 3 (+ 5) | tension detection uses stages 1-3 |
| P10 📝 | 5 | viable schedule = stage-4 lower bar |
| P12 📝 | all | category_venue_count uses inclusion mode |
| P11 (hold) | 3, 4, 6 mostly | B-score pipeline separate |

---

## The bigger open questions

**Q1: does category_venue_count need scope_mode?**
**Resolved:** Always inclusion mode, handbook spells this out explicitly. The pool_filter expression IS the scope.

**Q2: does the viable-schedule check need to account for tasks with days × (more than 2r+2s)?**
**Resolved:** 2+2 is the VALIDATOR's "cheapest viable plan" definition (for P7 budget math). Viable schedule lower bar is 2r+1s×days (minimum feasibility). Real agent schedules are not constrained to 2+2 — they can be any shape.

**Q3: should the upper bars be per-type tunable?**
**Resolved:** NO. Single 50/50/25 constants. Upper bars check per-filter MEANINGFULNESS; that's type-invariant. Types are limited in other ways (exemptions from pool-size-max etc.).

**Q4: should there be a pre-validation "is this city's pool even reasonable" check?**
**Resolved:** NO — the inclusion and viable-schedule lower bars already catch this ("need 5 museums, pool has 2 → fail"). No additional pre-flight check needed.

**Q5 (added from real data analysis):** What happens when common regulations are over-represented in the pool?
This came up testing upper bars against real London data. `wheelchair_accessible` is 92%/100% in the London pool — the 50% upper bar correctly flags this as cosmetic. But a real wheelchair user's task SHOULD include that constraint.

**Resolved:** upper bar correctly rejects. The problem is pool-side (over-representation of accessible venues) and belongs to P2 (pool composition). Validator tells the truth — "this filter doesn't narrow meaningfully in this pool" — and points to alternatives. Pool-generation is the right layer to fix the over-representation so constraints become meaningful.

---

## Reality check: what the real London pool tells us

Before committing to threshold numbers, I ran them against the actual `test_50` London pool. The results are sobering and validate the design:

| regulation | FOOD coverage | SITE coverage | Upper-bar verdict (50%) |
|---|---|---|---|
| wheelchair_accessible | 92% | 100% | ✗ FAIL — cosmetic in this pool |
| photography_allowed | 100% | 96% | ✗ FAIL — cosmetic in this pool |
| family_friendly | 60% | 92% | ✗ FAIL — cosmetic in this pool |
| pet_friendly | 4% | 36% | ✓ OK — genuinely narrows |
| reservation_required | 28% | 12% | ✓ OK — genuinely narrows |

Three of five regulations would be rejected as universal filters. This reveals two things:

1. **The validation is correct to reject.** If 92% of the pool is wheelchair-accessible, the wheelchair constraint adds no selection pressure — the task trivially passes without the agent doing interesting work. A benchmark that validates such tasks isn't measuring constraint-handling ability; it's measuring whether the agent can output any venues at all.

2. **The pool is biased toward "safe" venues.** This is a P2 concern (low-tier and bar underrepresentation) extended to regulations. The pool-generation prompt tells the LLM "sample diverse venues" but in practice the LLM picks well-known mainstream spots which tend to be accessible, photo-friendly, and family-friendly.

The validation layer will tell the truth about each task; fixing the pool is P2's job. The validator's error message points agents to regulations that ARE discriminating in the current pool (`pet_friendly`, `reservation_required`) so they can build meaningful tasks even with a biased pool.

---

## Summary

The validation pipeline today is six separate story threads (one per structural type) plus the P-constraint scoping confusion that cuts across all of them. P13 is the unifying refactor that makes scope explicit and splits the pool into universal/scoped/inclusion sub-pools. P9 and P10 plug into this framework cleanly — they stop being type-specific hacks and become type-specific uses of the same scope-aware primitives. P12 adds new patterns that naturally fit the inclusion mode.

After all of P13 + P9 + P10 + P12 land, the validation story becomes:
1. Schema checks (structural) — flagged per type by exemption
2. Scope stages 1-3 build three pools (universal, scoped, inclusion)
3. Lower-bound checks across all three (feasibility)
4. Upper-bound checks across all three (meaningfulness) using food/site partition
5. Type-specific additional checks (time feasibility for 2, budget for 4, tension for 3/5, doc consultation for 6)
6. Per-pattern legacy checks (label_required pool membership etc.) — simplified post-P13

Clean, layered, type-aware. The current mess becomes a coherent pipeline.

---

## Clarifications and corrections (added post-Phase 3)

### 1. Concern 3 bar is a lower bound, not upper

The viable-schedule check (`≥ 2 FOOD + 1 SITE per day in universal_pool`) is a **lower bound**. It is the floor below which no schedule is constructable at all. The "lower bar" / "upper bar" naming refers to feasibility floors (lower) vs meaningfulness ceilings (upper). The viable-schedule check is the feasibility floor.

---

### 2. A×A and B×B — corrected definitions

**A×A = venue-pool-level narrowing**

Applies to any constraint whose effect can be measured as a change in the set of eligible venues. Both the 50/50/25 stacking bars AND the small-overlap tension metric are A×A — they both operate on venue sets returned by `venues_matching`. The two parts of A×A are:

- **A×A stacking (upper bars):** how much does the cumulative stack of constraints narrow the FOOD/SITE groups? Measures the full stack together. Applies to all types except 2 and 4.
- **A×A overlap (tension detection):** do two specific constraints pull at *different* venue subsets? Measures a specific pair. Applies to type 3 only.

Both use `venues_matching` as the primitive. Both are the same conceptual framework.

**B×B = plan-space-level narrowing**

Applies to constraints whose effect cannot be reduced to "which venues are eliminated from the pool" — instead they restrict which *combinations of venues* constitute a valid plan. A valid plan must satisfy these constraints over its entire schedule.

**The key split by aggregation type:**

| Aggregation | Level | Logic |
|---|---|---|
| `"all"` | **A×A** | removes non-satisfying venues from pool |
| `"none"` | **A×A** | removes satisfying venues from pool (exclusion) |
| `{at_least: N}` | **B×B** | plan must contain ≥N qualifying activities |
| `{at_most: N}` | **B×B** | plan can contain ≤N qualifying activities |
| `{count_distinct: N, field}` | **B×B** | plan must visit N distinct field values |
| `{at_most_distinct: N, field}` | **B×B** | plan must stay within N distinct field values |
| `{ratio: R}` | **B×B** | ≥R fraction of scoped slot type must qualify |
| `{at_least_days: N}` | **B×B** | N complete days must satisfy condition |
| `{sum: ..., operator, value}` | **B×B** | aggregate over plan activities must satisfy |

Real task distribution (171 constraints across 66 tasks): 89 A×A (52%), 82 B×B (48%). B×B is not a corner case.

**Why this matters for validation:** A×A constraints are the ones that narrow the venue pool and should be measured against the 50/50/25 upper bars. B×B constraints do NOT narrow the venue pool and are NOT measured by upper bars — they instead narrows the set of valid plans drawn from the (A×A-filtered) pool.

**Plan-space intuition:** pool of 25 venues, 3-day trip with 2+2 per day = 6 slots. Baseline plan space ≈ P(25, 6) ≈ 127M ordered selections. An A×A universal constraint reducing pool to 15 changes this to P(15, 6) ≈ 3M — a 97% reduction. A B×B `{at_least: 2, scope: category=museum}` with 3 museums in pool: only plans that include ≥2 museum activities are valid — fraction ≈ [C(3,2)×C(22,4) + C(3,3)×C(22,3)] / C(25,6) ≈ 18% of baseline. Neither reduces the venue set; both reduce the plan set.

---

### 3. Per-type bars — corrected table

Types 2 and 4 are exempt from ALL venue pool upper/lower bars. Their difficulty mechanism is the resource ceiling (time or budget in the query), not pool narrowing. P-constraints in types 2/4 are descriptive context — they help shape the FORM of the plan but are not supposed to create selection pressure by themselves.

Type 5's upper bars must be stricter because extreme pool narrowing IS the point. A type 5 task where 60% of venues survive is not hard enough.

| Type | A×A upper bars | Lower bars | A×A overlap tension | P7 |
|---|---|---|---|---|
| 1 cascading | ✅ 50/50/25 | ✅ | ❌ exempt | ❌ |
| 2 subset | ❌ **exempt** | ❌ **exempt** | ❌ exempt | ✅ time |
| 3 competing | ✅ 50/50/25 | ✅ | ✅ **required** | ❌ |
| 4 allocation | ❌ **exempt** | ❌ **exempt** | ❌ exempt | ✅ budget |
| 5 feasibility | ✅ **25/25/10** | ✅ | ❌ exempt | ❌ |
| 6 context | ✅ 50/50/25 | ✅ | ❌ exempt | ❌ |

Type 5 upper bar rationale: universal stack must eliminate ≥75% of FOOD and ≥75% of SITE; each inclusion must exclude ≥90% of its group. Same FOOD/SITE/group measurement, stricter thresholds.

Pool-size-max (≤25 venues in universal_pool) applies to type 3 only — where a large surviving pool means the agent has too much choice and the tension isn't real.

---

### 4. B×B tension — revised scope

The original design documented B×B as relevant only for "plan-level B patterns like `district_count_max`, `cuisine_diversity_minimum`." This was an incomplete framing tied to the old pattern+params system.

**Correct framing:** B×B applies to any constraint with a counting aggregation (`at_least`, `at_most`, `count_distinct`, `at_most_distinct`, `ratio`, `at_least_days`, `sum`). These constraints operate at the plan-space level. B×B tension means: two such constraints together eliminate a much larger fraction of valid plans than either eliminates alone.

**B×B narrowing factor (per constraint):** estimate the fraction of baseline plans that satisfy the constraint, given the pool composition and schedule shape. Examples:
- `{at_least: 2, scope: category=museum}` — fraction of 6-slot plans that include ≥2 of the N museums in pool
- `{at_most_distinct: 2, field: district}` — fraction of plans drawn entirely from ≤2 districts
- `{ratio: 0.6, scope: activity_type=meal}` — fraction of meal-slot arrangements where ≥60% are from local-cuisine venues

**Stacked B×B narrowing:** multiply individual narrowing factors (with correlation adjustment if constraints target the same slots). If stacked product ≤ 5% of baseline AND ≤ 2000 × days absolute plans remain → genuine B×B tension.

**B×B tension in type 3:** a valid type 3 task may be built on two B×B constraints (e.g., `{at_most_distinct: 2, field: district}` + `{count_distinct: 3, field: cuisine_label}`) that have small overlap at the plan-space level even if both accept all venues at the pool level. The A×A overlap metric would find no tension (both sets = full pool), but B×B would detect that satisfying both simultaneously is hard.

**Implementation approach:** compute per-constraint plan-space narrowing factor using simple combinatorial approximations (not exact). Flag as tension if stacked product is sufficiently small. The approximations don't need to be exact — the goal is to distinguish trivially satisfiable pairs from genuinely competing ones.

**Status:** B×B tension detection is the most complex part of Phase 4. Since 82 of 171 real constraints are B×B aggregation types, it is not optional for correctness.

