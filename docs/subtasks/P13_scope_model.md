# P13 — Category A filter scope: universal / scoped / inclusion

**Status: 📝 design locked, awaiting implementation**
**Blocks: P9, P10, P12** (all Category A tension/solvability work depends on scope model)
**Depends on: nothing**

---

## The problem

Category A P-constraints today are all applied **universally** (every venue in the plan must satisfy them) by `_apply_pool_filters` in `pool_utils.py`. This is correct for some patterns and completely wrong for others.

Concrete evidence from 60 real tasks:

- **100% of `price_tier_required` usages** (8 tasks) were applied universally. Intent: "upscale dinner," "budget-friendly day." Effect: pool narrows to upscale-only (~8 of 50 venues) OR budget-only, and EVERY stop in the plan must be that tier. Cafes disappear, museums disappear, parks disappear. Plans become impossible.

- **All 77 `category_count_minimum` (soon to be `category_venue_count`) usages** are actually inclusion intent ("at least 2 museums"), but the solvability check treats them as pool-narrowing (demands 2+ museums exist in filtered pool), and the scoring handler does something else entirely (counts distinct categories, ignoring the category param — the bug fixed in P12).

- **`label_required` with food-specific tags** (vegetarian, michelin, tasting_menu) applied universally wipes out all non-meal venues. Intent: "vegetarian meals" — most sites don't have food-related tags, so they fail the universal filter.

These aren't edge cases. They're the norm. Our current pool-narrowing pipeline doesn't match user intent for most P-constraints.

## The three filter modes

Every Category A P-constraint has exactly one mode:

### Universal — "every venue in the plan must satisfy this"
- Intent: plan-wide property. If ANY venue doesn't satisfy, the plan fails.
- Pool effect: narrows the whole-plan pool.
- Example language: "wheelchair accessible," "quiet," "dog-friendly," "relaxed pace"
- Patterns typically universal: `regulation_required` (accessibility/pet/family), `noise_level_max`, `pace_relaxed`, `max_visit_duration`, `label_excluded`

### Scoped — "this specific slot (defined by activity_type or time_window) must satisfy this"
- Intent: property applies to a specific kind of activity, not the whole plan.
- Pool effect: narrows the subset-pool for that slot only; rest of pool unaffected.
- Example language: "upscale DINNER," "outdoor BEFORE SUNSET," "vegetarian MEALS," "photography-friendly ATTRACTION"
- Patterns typically scoped: `price_tier_required` (usually scoped to meal type), `label_required` when the label is category-specific (food tags on meals, landmark tags on attractions)

### Inclusion — "include at least K venues matching this"
- Intent: at-least-K within the plan. Other slots can be anything.
- Pool effect: zero narrowing of the whole pool; constrains K activities to match.
- Example language: "include one hidden gem," "at least 2 museums," "visit a photo spot"
- Patterns typically inclusion: `hidden_gem_required` (has `min_count`), `category_venue_count` (P12's new pattern, always a count), `label_required` when the user says "include at least N" in natural language

## Design: explicit declaration + pattern defaults

**Agents declare scope explicitly**. We do not derive it automatically from tag semantics — that's too brittle. The agent writes:

```
{
  "id": "pc_001",
  "pattern": "price_tier_required",
  "params": {"min_tier": "upscale"},
  "scope_mode": "scoped",
  "scope": {"activity_type": "meal", "time_window": "evening"}
}
```

or for universal:

```
{
  "id": "pc_002",
  "pattern": "regulation_required",
  "params": {"regulation_key": "wheelchair_accessible"},
  "scope_mode": "universal"
}
```

or for inclusion:

```
{
  "id": "pc_003",
  "pattern": "hidden_gem_required",
  "params": {"min_count": 1},
  "scope_mode": "inclusion"
}
```

**Schema additions to every P-constraint:**
- `scope_mode` : `"universal" | "scoped" | "inclusion"` — required
- `scope` : `{activity_type?, category?, time_window?}` — required when `scope_mode == "scoped"`, ignored otherwise

**Pattern defaults** (used if agent omits `scope_mode` — backward compatibility for existing tasks):

| Pattern | Default scope_mode | Notes |
|---|---|---|
| `regulation_required` | universal | "wheelchair" etc. means every venue |
| `pace_relaxed` | universal | "relaxed day" means all venues |
| `max_visit_duration` | universal | "no stops > 90min" means every stop |
| `noise_level_max` | universal | "quiet day" means all venues |
| `label_excluded` | universal | "no tourist traps" means all venues |
| `hidden_gem_required` | inclusion | always has `min_count`, always inclusion |
| `category_venue_count` | inclusion | always a count, always inclusion |
| `price_tier_required` | **scoped to activity_type=meal** | almost always about meals, never whole plan |
| `label_required` | **undefined — agent MUST declare** | varies wildly by tag |

For `label_required`, no default — the scope depends on the specific tag. Handbook must tell agents to declare explicitly.

## Category-scoping (tag semantics)

When a tag only makes sense on certain categories, the agent scopes the constraint to those categories:

```
label_required: "vegetarian"
  scope_mode: "scoped"
  scope: {category: "restaurant,cafe"}      # vegetarian applies only to meals

label_required: "michelin"  
  scope_mode: "scoped"
  scope: {category: "restaurant"}           # michelin is restaurant-specific

label_required: "landmark"
  scope_mode: "scoped"
  scope: {category: "attraction,museum,park"} # landmarks are sites
```

We won't auto-derive scope from tag. Agent has to declare — and the handbook will include a clear table of "tags that are meal-only," "tags that are site-only," and "tags that are universal" (like `family_friendly`, `wheelchair_accessible`, `instagrammable`).

## Validation pipeline refactor

Current `_apply_pool_filters` is a one-pass loop that applies every P-constraint universally. New pipeline has four stages:

### Stage 1: Universal pool
```python
universal_pool = venue_pool.copy()
for pc in personal_constraints:
    if effective_scope_mode(pc) == "universal":
        universal_pool = apply_filter(universal_pool, pc)
# universal_pool: venues that survive every universal constraint
```

### Stage 2: Scoped sub-pools
```python
scoped_pools = {}  # keyed by pc.id
for pc in personal_constraints:
    if effective_scope_mode(pc) == "scoped":
        # Start from universal_pool (universals always bind), filter to the scope
        scope_filtered = apply_scope(universal_pool, pc.scope)
        scoped_pools[pc.id] = apply_filter(scope_filtered, pc)
```

### Stage 3: Inclusion candidate sets
```python
inclusion_pools = {}
for pc in personal_constraints:
    if effective_scope_mode(pc) == "inclusion":
        # Candidates must also satisfy universals
        inclusion_pools[pc.id] = apply_filter(universal_pool, pc)
```

### Stage 4: Three-way solvability check (lower bounds — feasibility)

**Lower bar 1 (universal pool):** Supports a viable schedule.
- Need at least `2 × days` restaurants AND `1 × days` sites in `universal_pool`.
- This is the Type 5 viable-schedule check from P10, generalized to all types.
- If universal pool can't schedule, task fails with "universal constraints too restrictive."

**Lower bar 2 (scoped pools):** Each scoped slot has candidates.
- For each `scoped_pools[pc.id]`: must have ≥ 1 venue (feasibility).
- For Type 3 tension involving this scope: must have ≥ 2 venues (real choice).
- If scoped pool is empty, task fails with "no candidates for [scope]."

**Lower bar 3 (inclusion pools):** Each inclusion has enough candidates.
- For each `inclusion_pools[pc.id]`: must have ≥ min_count venues.
- This is the existing `hidden_gem_required` solvability check, generalized.
- If insufficient, task fails with "not enough venues for inclusion criterion."

### Stage 5: Three-way upper-bound check (meaningfulness — STACKED narrowing must be sufficient)

Filters stack cumulatively; the upper bars measure the FINAL POOL after all constraints apply, not each filter in isolation. This matters because a single filter might be weak but combined with others produces meaningful narrowing.

**Upper bar 1 (universal stack):** After ALL universal filters AND-ed together, universal_pool ≤ 50% of FOOD group AND ≤ 50% of SITE group.
- FOOD = venues with category ∈ {restaurant, cafe, bar}. SITE = venues with category ∈ {museum, attraction, park, neighbourhood}. These two groups partition the pool.
- Compute `surviving_food` = FOOD venues surviving the AND of all universal filters. Same for `surviving_site`.
- Both must be ≤ 50% of their original group count.
- Rationale: universals apply across all activities, so the agent's combined universal stack must narrow BOTH groups meaningfully.

**What happens when one group fails:**
The rejection message is diagnostic — the validator tells the agent WHICH group is under-narrowed and points to corrective actions:
```
"Universal stack narrows FOOD to 60% (need ≤50%) and SITE to 24% (OK).
 FOOD side is barely constrained. Options:
 (a) Add another universal filter narrowing FOOD (e.g., noise_level_max scoped to meals,
     or label_required with a food-specific tag like `vegetarian` scoped to FOOD)
 (b) Scope an existing universal filter to SITE only if it's not meant for food
 (c) If this is intentional (food is unconstrained by design), reconsider whether
     your task actually has universal requirements or whether they're scoped."
```

Examples:
- `regulation_required: family_friendly` alone where 15 of 25 FOOD (60%) and 23 of 25 SITE (92%) are family-friendly → BOTH sides fail → reject, agent must add narrowing filters.
- `regulation_required: family_friendly` + `noise_level_max: quiet` (cumulative). If quiet venues are 8 of 25 food and 18 of 25 site: FOOD intersection ≈ 6/25 (24%) passes; SITE intersection ≈ 17/25 (68%) still fails. Reject with message: "SITE side needs more narrowing."
- `regulation_required: family_friendly` + `label_excluded: tourist_spot` (scoped to sites, maybe 10 site venues excluded): FOOD still 60% (scoped filter doesn't touch food) → FOOD fails.

**Upper bar 2 (scoped stack):** Group scoped filters by scope target. For each scope group, AND all filters with that scope together. The stacked result must be ≤ 50% of the scope's relevant group.
- Example: three filters all scoped to dinner: `price_tier_required: upscale`, `noise_level_max: moderate`, `label_required: date-night`. AND them. Compare result to FOOD count (since dinner is a FOOD scope).
- If the stack narrows dinner to 6/25 FOOD (24%) → pass. If narrows to 14/25 (56%) → fail.
- Why grouped: two filters both scoped to "dinner" compound; two filters scoped to different slots don't.
- Rejection message: "Scoped filter stack for [scope] narrows to {X%} of {FOOD|SITE}, need ≤50%. Add another filter scoped to [scope] or tighten existing ones."

**Upper bar 3 (inclusion — per-filter, NOT stacked):** Each inclusion checks independently against the ≤ 25% bar.
- Rationale: inclusions don't compound in the same way — each inclusion is "at least K of this criterion"; they target specific slots rather than narrowing the whole pool.
- An "include 2 museums" inclusion is independent of an "include 1 hidden-gem" inclusion, even if the two overlap. Each must be individually discriminating.
- Determining the "relevant group" from the inclusion's pool_filter: if the pool_filter restricts to category in FOOD → use FOOD size as denominator; if SITE → SITE size; if cross-group → use the group with higher affinity per tag table.
- Reject with: "Inclusion filter [pc.id] not discriminating: {count}/{group_size} {group} venues match (≥ 25%). Tighten criterion."
- Examples:
  - `hidden_gem_required` where 6 of 25 SITE venues are hidden-gem → 24% → accept.
  - `hidden_gem_required` where 10 of 25 SITE venues are hidden-gem → 40% → reject.
  - `category_venue_count: {pool_filter: {category: "restaurant"}, op: ">=", value: 2}` → 18 of 25 FOOD (72%) → reject (trivial).

**Rationale for stacking semantics:**
- Universals and scoped filters OVERLAP SEMANTICALLY — multiple universals compound to give the agent a cumulatively narrower choice space across all activities. Upper bar checks the final state.
- Inclusions don't compound — "include 2 museums" + "include 1 gem" together still means 2+1=3 specific slots constrained, each independently.
- Scope groups isolate compounding — dinner filters compound for dinner; dinner filters don't compound with breakfast filters.

**Rationale for the 50/50/25 thresholds:**
- Universal and scoped at 50% per-group: both apply across many slots, moderate narrowing suffices.
- Inclusion at 25% per-group: applies to just K slots, must aggressively exclude to shape those slots.
- Tunable constants: `UNIVERSAL_UPPER_BAR = 0.5`, `SCOPED_UPPER_BAR = 0.5`, `INCLUSION_UPPER_BAR = 0.25`.

**On why inclusion is actually a B-mode, not a third A-mode, for tension math:**
Inclusion constraints don't narrow the universal_pool — they only shape which specific K activities fill the plan. Their role in tension detection (P9) is through the plan-space narrowing math (B×B), not the venue-set overlap math (A×A). Two inclusions compete when their candidate sets don't share venues (e.g., "include 2 museums + include 1 hidden gem museum" has partial overlap; "include 1 upscale restaurant + include 1 vegetarian restaurant" might have zero overlap). This is still small-overlap math, but applied to inclusion_pools — which fits naturally into the B×B framework. See P9 doc for details.

### Additional rule: slot accounting for scoped + inclusion combined

When a plan has multiple scoped filters on the same slot OR multiple inclusions asking for the same venue category, the scheduler must satisfy all of them simultaneously with distinct venues. E.g., "upscale dinner" + "celebrity-chef dinner" both scope to the dinner slot — need at least 1 venue satisfying BOTH (not 2 venues). But "include 2 museums" + "include 1 hidden gem museum" — need 2 museums total, one of which is a hidden gem. Accounting rules:
- Multiple scopes on same slot → intersect the scoped sub-pools
- Multiple inclusions on same category → max(inclusion counts), where the inclusion-with-extra-criterion is a subset

This is subtle but mostly automatic if stages 2 and 3 are computed correctly.

## Tag-category affinity validation

When an agent writes `label_required` or `label_excluded`, the validator checks whether the declared scope is compatible with the tag's natural category affinity.

**Implementation (user-proposed, pool-derived):** compute tag affinity per-city from real pool data, not hand-maintained.

```python
def compute_tag_category_affinity(pool, cities):
    """
    Analyze tag usage per category and derive scope restrictions.
    
    Thresholds:
    - Tag concentrated: ≥80% of tag's appearances in one category → scope to that category
    - Tag multi-category: ≥30% in multiple categories → scope to those categories  
    - Otherwise: universal (no restriction)
    """
    tag_cat_counts = defaultdict(lambda: defaultdict(int))
    category_sizes = defaultdict(int)
    
    for v in pool:
        category_sizes[v.category] += 1
        for tag in v.tags:
            tag_cat_counts[tag][v.category] += 1
    
    affinity = {}
    for tag, cat_counts in tag_cat_counts.items():
        total = sum(cat_counts.values())
        if total < 3:
            continue  # ignore rare tags — not enough data
        fractions = {cat: cat_counts[cat] / total for cat in cat_counts}
        
        # Concentrated in one category?
        top_cat, top_frac = max(fractions.items(), key=lambda x: x[1])
        if top_frac >= 0.8:
            affinity[tag] = {top_cat}
            continue
        
        # Multi-category spread?
        relevant_cats = {c for c, f in fractions.items() if f >= 0.3}
        if len(relevant_cats) >= 2 and all(fractions.get(c, 0) < 0.1 for c in 
                                            CATEGORIES_ALL - relevant_cats):
            affinity[tag] = relevant_cats
            continue
        
        # Otherwise: universal (tag appears across many categories without clear concentration)
        affinity[tag] = None
    
    return affinity
```

**Auto-recomputed when pool changes.** Stored in the city's DB or alongside pool config. New cities get their own table. New tags auto-classified. The ~40-tag hardcoded table above becomes an example of what auto-derivation would produce for London.

**Example derivations** (from London test_50 pool tag counts):
- `vegetarian` appears only on restaurants/cafes → affinity: `{restaurant, cafe}`
- `michelin` appears only on restaurants → affinity: `{restaurant}`
- `landmark` appears on attractions, museums, parks → affinity: `{attraction, museum, park}`
- `family_friendly` appears across restaurant/cafe/museum/park → no concentration → affinity: `None` (universal)
- `hidden-gem` appears on restaurants, parks, museums roughly evenly → affinity: `None` (universal)

**Validation rule (unchanged from earlier):**
```python
def validate_tag_scope(pc, tag_affinity):
    if pc.pattern not in ("label_required", "label_excluded"):
        return
    tag = pc.params.get("required_label" if pc.pattern == "label_required" else "excluded_label")
    if tag not in tag_affinity:
        return  # rare tag, allow (safe default)
    allowed = tag_affinity[tag]
    if allowed is None:
        return  # universal tag, any scope OK
    declared = pc.scope.get("category") if pc.scope else None
    if declared is None:
        raise ValidationError(
            f"Tag '{tag}' only appears on {sorted(allowed)} venues. "
            f"Add `scope: {{category: '{','.join(sorted(allowed))}'}}` to constraint {pc.id}."
        )
    declared_cats = set(declared.split(","))
    invalid_cats = declared_cats - allowed
    if invalid_cats:
        raise ValidationError(
            f"Tag '{tag}' doesn't appear on {sorted(invalid_cats)} in this pool. "
            f"Valid for {sorted(allowed)}. Fix scope on constraint {pc.id}."
        )
```

**Advantages of pool-derived:**
- No manual maintenance — scales to any city
- Tag additions/removals in pool propagate automatically
- Catches city-specific semantics (e.g., "coffee" might be restaurant-concentrated in some cities but not others)
- Rare tags (< 3 appearances) excluded to avoid noise

## Tension detection with scope model

For Type 3 (P9), detection now branches on filter-mode pairs:

| pair | tension detection |
|---|---|
| universal × universal | small-overlap on universal_pool venue sets (the main A×A case) |
| scoped × scoped, same scope | small-overlap on the shared slot's candidate sets |
| scoped × scoped, different scopes | NOT tension (no competition) |
| inclusion × inclusion | small-overlap on inclusion_pools[pc.id] candidate sets |
| universal × inclusion | tension only if universal_pool starves the inclusion pool — specifically, inclusion_pool has ≥ min_count but the universal_pool contains < min_count matches |
| universal × scoped | NOT tension (universal applies everywhere, scoped applies somewhere — compounding) |
| scoped × inclusion | NOT tension (different plan axes) |

Four real tension cases instead of three — adds "scoped × scoped same scope" and "universal × inclusion scarcity."

## Hard-require scope_mode on all new tasks

Per design decision: every P-constraint on new tasks must include `scope_mode`. No defaults applied silently; agent must explicitly choose.

- If `scope_mode` missing on a new task: validation error, task generation retries.
- If `scope_mode: "scoped"` but `scope` field missing: validation error.
- If `scope_mode: "inclusion"` but no `min_count` in params: validation error.
- If tag-category affinity is violated (per previous section): validation error.

Pattern defaults from earlier sections (regulation→universal, hidden_gem→inclusion, price_tier→scoped-meal) are **documentation guidance for agents**, not silent fallbacks in code. Agents are told "when you use `price_tier_required`, you almost always want `scope_mode: scoped` with `scope: {activity_type: meal}`" — but they must write it explicitly. The handbook is strong enough that agents will get it right, and explicit declarations make generation-time errors clearer.

## Handbook rewrite

A new prominent section in the agent prompt, labeled "UNIVERSAL vs. SCOPED vs. INCLUSION — you MUST choose":

```
Every P-constraint you write applies to the plan in exactly ONE of three ways:

  UNIVERSAL  — every venue in the plan must satisfy this.
               Example: "wheelchair accessible" → every stop is accessible.
               Effect: narrows the whole pool. Many tasks stack 2-3 universals.
               Use for: regulations, pace, noise, tags that apply to all venues.

  SCOPED     — a specific activity slot (by activity_type or time_window) must satisfy this.
               Example: "upscale dinner" → dinner is upscale, lunch/breakfast can be anything.
               Effect: narrows the candidate set for that slot only.
               Use for: price tiers (almost always scoped to meals), tags that are
               category-specific (vegetarian → meals; michelin → restaurants).

  INCLUSION  — the plan must include at least K venues matching this.
               Example: "include 2 museums" → 2 slots are museums, others can be anything.
               Effect: requires K candidates exist; doesn't narrow other slots.
               Use for: counts and "at least one X" requirements.

COMMON MISTAKE: writing `price_tier_required: upscale` without scope. This makes
the ENTIRE pool upscale-only — cafes, parks, museums all disappear. Almost
certainly not what you want. If you mean "upscale dinner," use:
  scope_mode: "scoped", scope: {activity_type: "meal", time_window: "evening"}
```

Per-pattern default table in the handbook. For each pattern, show:
- Default scope_mode
- Sample universal/scoped/inclusion usage
- Common tags and their natural scope (category-tag guide)

## Migration

Existing tasks don't have `scope_mode` declared. Two options:

**Option A**: apply pattern defaults at task-load time. Most existing tasks are defensible under defaults:
- `regulation_required` → universal (correct)
- `hidden_gem_required` → inclusion (correct)
- `label_required` → undefined; force agent to declare OR default to universal (which is what the old code did, so behavior unchanged)
- `price_tier_required` → scoped to meal (this CHANGES behavior for existing tasks — was universal, now scoped). These tasks should be regenerated.

**Option B**: regenerate all 60 existing tasks with explicit scope. Cleaner but requires re-running generation.

Preference: Option B. The tasks were generated during active development, the scope bug was wrong for `price_tier_required` specifically, and regeneration is cheap.

## Test additions

- E3 test: `price_tier_required` with explicit scope — pool is NOT narrowed globally; scoped pool IS narrowed.
- E3 test: `label_required: vegetarian` with `scope: category=restaurant,cafe` — museums stay in pool.
- E3 test: `hidden_gem_required` with `min_count: 2` — inclusion pool has ≥2 candidates; universal pool unaffected.
- E3 test: mixing universal + scoped + inclusion in one task — all three stages run correctly.
- E3 test: missing `scope_mode` on any P-constraint — task fails validation (hard-require).
- E3 test: scoped × scoped same-scope tension detection (the new tension case).

**Upper-bound tests:**
- E3 test: `label_excluded: tourist_spot` universal where only 2 venues are tourist_spot → reject (cosmetic, universal upper bar).
- E3 test: `price_tier_required: upscale` scoped to meals where 15 of 18 restaurants are upscale → reject (cosmetic, scoped upper bar).
- E3 test: `hidden_gem_required` where 30 of 50 venues are hidden-gem → reject (cosmetic, inclusion upper bar).
- E3 test: universal filter reduces pool to 49% of original → accept (under 70%).
- E3 test: scoped filter reduces slot pool to 68% → accept (just under 70%).

**Tag-category affinity tests:**
- E3 test: `label_required: vegetarian` with no scope OR with universal scope → reject (must scope to restaurant/cafe).
- E3 test: `label_required: vegetarian` with `scope: {category: "museum"}` → reject (vegetarian not valid for museums).
- E3 test: `label_required: vegetarian` with `scope: {category: "restaurant,cafe"}` → accept.
- E3 test: `label_required: family_friendly` with universal scope → accept (family_friendly is universal in affinity table).
- E3 test: `label_required: unknown_tag_xyz` with any scope → accept (unknown tags unrestricted).

## Implementation scope

- **Schema update**: add `scope_mode` + `scope` fields to constraint schema. Validator enforces hard-require (~1h)
- **Refactor `_apply_pool_filters`**: only apply universals; build scoped_pools and inclusion_pools (~2h)
- **Refactor `_verify_task_solvable`**: three-stage lower-bound + three-stage upper-bound checks with named thresholds (~2.5h)
- **Tag-category affinity validation**: build `TAG_CATEGORY_AFFINITY` table covering ~40 common tags, integrate validator check (~1.5h)
- **Handbook rewrite**: new prominent section (universal/scoped/inclusion), per-pattern guidance table, tag-category guide with the affinity table inline, side-by-side wrong-vs-right examples (~2h)
- **Test suite**: ~15-18 new E3 cases covering modes × lower/upper bounds × tag affinity (~2h)
- **Regenerate existing 60 tasks**: run the pipeline once with new schema, replace test_50 DB (~0.5h)

**Total: ~11.5h.** This is the largest subtask in Phase 4 but unlocks everything downstream and closes the biggest design gap in P-constraint handling.

## Dependency graph (updated)

```
P13 (scope model + filter-mode refactor)   ← do first
  ├─ P9  (Type 3 tension: needs scope to know which pool to compare)
  │   └─ (P9's A×A metric plugs into scoped_pools / universal_pool / inclusion_pools)
  ├─ P10 (Type 5 viable-schedule: becomes stage-1 universal-pool check)
  └─ P12 (category_venue_count naturally lands as inclusion mode)

Independent:
  P11 (B-score pipeline) — unchanged, can float
```

## Design decisions — locked

1. **Hard-require `scope_mode` on all new tasks.** No silent pattern defaults in code; agents must declare explicitly. Handbook guidance is strong enough that agents will choose correctly when pattern has a natural default.
2. **Tag-category affinity enforced via validator.** Hardcoded table (~40 tags), unknown tags allowed safely. Prevents `label_required: vegetarian` on museums.
3. **Upper bounds applied to all three modes** with named constants: 70% universal, 70% scoped, 50% inclusion. Tunable from one place.
4. **Existing 60 tasks will be regenerated** with new schema after P13 lands. No backward-compat shim in code — saves complexity.
