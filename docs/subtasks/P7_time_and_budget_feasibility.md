# P7 — Type 2 time-ceiling & Type 4 budget-ceiling solvability checks
**Status: ✅ DONE — schema + query_pool enrichment + both algorithms + protocol updates + 25 E3 tests + E2 update, 415/415 tests passing**
**Depends on:** P6 ✅, P8 ✅
**Blocks:** nothing

---

## Scope

Close the two gaps from the P6 audit: type 2 time-ceiling and type 4 budget-ceiling solvability checks are currently missing (budget entirely, time partially). Both depend on a number that lives in the natural-language query — so the schema needs to carry that number explicitly.

Four pieces of work:
1. **Schema addition**: `public_input.query_resources` block
2. **Tool enhancement**: `query_pool` gets an opt-in `include` parameter
3. **Type 2 feasibility algorithm** (enhanced — partial version exists)
4. **Type 4 feasibility algorithm** (new — none exists)
5. **Handbook + system prompt updates** for type 2 & type 4 protocols

---

## 1. Schema addition: `query_resources`

The agent fills this in alongside the query. The validator reads it. Required for type 2 / type 4, optional for everything else.

```json
"public_input": {
  "query": "I have £50/day for food and sightseeing, want one really nice meal...",
  "query_resources": {
    "time_ceiling_minutes": 330,      // required for type 2
    "budget_per_day": 40              // required for type 4 — in city's local currency
  }
}
```

**Schema validation rules added:**
- If `structural_type` matches `type2*` → `time_ceiling_minutes` is required and must be an integer
- If `structural_type` matches `type4*` → `budget_per_day` is required and must be a positive number
- `budget_per_day` is in the city's local currency (GBP for London, JPY for Tokyo, etc. — see P8). Validator uses `get_city_currency(city)` only for rendering error messages; comparisons against `avg_cost_local` are direct and currency-neutral.

**Why local currency (not USD):** the venue DB stores costs in local currency (see P8). User queries say "£40/day"; the agent should use 40 directly without juggling exchange rates. The validator compares `budget_per_day` against `avg_cost_local` sums — both in the same currency by construction.

---

## 2. `query_pool` — opt-in field enrichment

**Current behavior:** returns `[{venue_id, name}, ...]`

**Proposed:**
```python
query_pool(
  filters={"category": "restaurant"},
  include=["price", "duration", "coords"]   # optional, default []
)
```

| `include` key | Adds to each result |
|---|---|
| `price`    | `avg_cost_local`, `price_tier` |
| `duration` | `recommended_visit_minutes` |
| `coords`   | `lat`, `lng`, `district` |

Default (`include=[]` or missing) preserves current minimal output. Type 2 protocols should suggest `include=["duration", "coords"]`; type 4 protocols should suggest `include=["price"]`.

---

## 3. Type 2 feasibility algorithm (enhanced)

Builds on the existing k-nearest check in `_verify_task_solvable`. Changes:

**Inputs:**
- `filtered` (pool after applying all filters)
- `query_resources.time_ceiling_minutes` (from schema)
- K = `category_count_minimum` for the relevant category, else `days × 2`

**Constants (module-level, tunable):**
```python
VISIT_DURATION_FRACTION = 0.5   # use half of recommended_visit_minutes (quick visits)
TIME_RATIO_MIN = 1.1            # ceiling / min_time must be ≥ 1.1 (not impossibly tight)
TIME_RATIO_MAX = 1.3            # ceiling / min_time must be ≤ 1.3 (real selection pressure)
SINGLE_DAY_CAP_MINUTES = 10 * 60  # 1 day max (10 waking hours)
```

**Algorithm:**
1. Take filtered pool, drop venues missing coords
2. If coverage < 50% or `len(venues_with_coords) < K`: fall back to current 15-min flat-travel check (no enhancement)
3. Find centroid → sort by distance → pick nearest K
4. Compute `min_time = K × 0.5 × avg(recommended_visit_minutes) + nearest_neighbour_travel_between_K_venues`
5. Read `ceiling = query_resources.time_ceiling_minutes`
6. **Cap check**: if `ceiling > SINGLE_DAY_CAP_MINUTES × days` → fail ("time ceiling exceeds {days}-day cap")
7. **Floor check**: if `ceiling < min_time × TIME_RATIO_MIN` → fail ("time ceiling infeasible: need ≥{X}min, stated ceiling {Y}min")
8. **Ceiling check**: if `ceiling > min_time × TIME_RATIO_MAX` → fail ("time ceiling too loose: ratio {r}×; selection pressure requires 1.1-1.3×")

**What happens if agent didn't set query_resources:** hard fail at schema validation with clear message. Don't silently skip the check.

---

## 4. Type 4 feasibility algorithm (new)

**Inputs:**
- `filtered` (pool after filters)
- `query_resources.budget_per_day_usd` (from schema)
- `days` (from top-level)
- `required_venue_ids` (from rubric — any agent-specified must-visits)

**Constants:**
```python
RESTAURANTS_PER_DAY = 2
SITES_PER_DAY = 2          # sites = museum, attraction, park, neighbourhood
BUDGET_RATIO_MIN = 1.1
BUDGET_RATIO_MAX = 1.3
SITE_CATEGORIES = {"museum", "attraction", "park", "neighbourhood"}
```

**Algorithm:**
1. Split `filtered` into restaurants vs sites by `category`
2. Sort each by `avg_cost_local` ascending
3. `cheapest_K_restaurants = filtered_restaurants[: RESTAURANTS_PER_DAY × days]`
4. `cheapest_K_sites = filtered_sites[: SITES_PER_DAY × days]`
5. Fail hard if either list is shorter than its target (pool can't fill the quota — "pool gap: need K restaurants within filters, have N")
6. `base_min = sum(v.avg_cost_local for v in cheapest_K_restaurants) + sum(v.avg_cost_local for v in cheapest_K_sites)`
7. `premium_add = sum(venue.avg_cost_local for venue_id in required_venue_ids where venue.avg_cost_local > 1.5 × category_median)` — only venues that are "expensive for their category" count as premium. Rationale: if the agent required a £15 cafe, that doesn't push the budget floor up; if they required a £150 dinner, it does.
8. `min_budget = base_min + premium_add`
9. `min_budget_per_day = min_budget / days`
10. Compare `query_resources.budget_per_day` against `[min_budget_per_day × 1.1, min_budget_per_day × 1.3]`
11. **Floor check**: `< 1.1 × min_budget_per_day` → fail ("budget infeasible: cheapest viable plan is {currency}{X}/day, stated budget {currency}{Y}/day")
12. **Ceiling check**: `> 1.3 × min_budget_per_day` → fail ("budget too loose: ratio {r}×; real allocation pressure requires 1.1-1.3×")

**Premium-venue detection (step 7):** compare venue's `avg_cost_local` to median of its category in the pool. If above 1.5× median, count as premium. This avoids penalising the agent for requiring ordinary venues.

**Currency rendering:** Error messages use `get_city_currency(city)` (from P8) to format the appropriate symbol — `£47/day` for London, `¥4800/day` for Tokyo. Internal comparisons remain numeric.

---

## 5. Error messages

Error messages should be actionable for the agent — tell it what specifically to change:

**Type 2 violations:**
- `"Time ceiling infeasible: min schedule for {K} venues needs ~{X}min (visits={V}min, travel={T}min), stated ceiling {Y}min. Either increase time_ceiling_minutes or reduce category_count_minimum."`
- `"Time ceiling too loose ({r:.1f}×): meaningful selection pressure requires 1.1-1.3× the min schedule time. Tighten time_ceiling_minutes to {target_range}."`

**Type 4 violations:**
- `"Budget infeasible: cheapest {K_r} restaurants + {K_s} sites sums to {currency}{min}/day, stated budget {currency}{stated}/day. Increase budget_per_day or loosen P-filters to unlock cheaper venues."`
- `"Budget too loose ({r:.1f}×): real allocation pressure requires 1.1-1.3× the cheapest viable plan. Tighten budget_per_day to {target_range}."`

---

## 6. Handbook + prompt updates

### Handbook entry for `query_pool` (`-h tools`):
Document the `include` parameter:
```
query_pool(filters: dict, include: list[str] = []) → [{...}]
  filters: same as before
  include: list of extra fields per result. Valid values:
    - "price"    → adds avg_cost_local, price_tier
    - "duration" → adds recommended_visit_minutes
    - "coords"   → adds lat, lng, district
  Example (type 2): query_pool({"category":"restaurant"}, include=["duration","coords"])
  Example (type 4): query_pool({"category":"museum"}, include=["price"])
```

### Type 2 reasoning protocol update:
Add a step 0 explicitly about `query_resources`:
```
0. THINK: What's the time ceiling in minutes? State it now — you MUST include it
   as query_resources.time_ceiling_minutes in the SUBMIT. The validator will
   check that the cheapest K-venue plan fits in 1.1-1.3× this ceiling.
```
And update step 2 to recommend `include=["duration","coords"]` on `query_pool`.

### Type 4 reasoning protocol update:
Add a step 0 about `query_resources.budget_per_day`:
```
0. THINK: What's the per-day budget, in this city's local currency?
   State it now — you MUST include it as query_resources.budget_per_day in
   the SUBMIT. The validator will check that your budget is 1.1-1.3× the
   cost of the cheapest viable plan (cheapest 2 restaurants + 2 sites per
   day, plus any expensive required venues). Use the city's local currency
   (GBP for London, JPY for Tokyo, etc.) — do not convert to USD.
```
And update step 2 to recommend `include=["price"]` on `query_pool`.

---

## 7. E3 tests to add

**Type 2 positive cases:**
- Task with K=3 restaurants all in Soho cluster, ceiling=300 min → passes (ratio in band)
- Same but ceiling=600 min → fails (too loose, ratio 2×)
- Same but ceiling=120 min → fails (too tight)
- Task without `query_resources.time_ceiling_minutes` → fails schema validation

**Type 4 positive cases:**
- Pool with budget/mid/upscale venues, budget set to 1.2× cheapest combo → passes
- Budget set to 2× cheapest combo → fails (too loose)
- Budget set to 0.8× cheapest combo → fails (too tight)
- `required_venue_ids` with expensive venue, budget accounts for it → passes
- Same, budget ignores the premium add → fails
- Task without `query_resources.budget_per_day` → fails schema validation

**Enrichment tests:**
- `query_pool({}, include=["price"])` returns entries with `avg_cost_usd`
- `query_pool({}, include=["duration","coords"])` returns entries with duration + lat/lng
- `query_pool({})` (no include) returns minimal {venue_id, name} as before

---

## Implementation order

1. Schema: add `query_resources` field + validator's schema-presence check
2. `_query_pool` enrichment + handbook entry
3. Type 2 algorithm in `_verify_task_solvable`
4. Type 4 algorithm in `_verify_task_solvable`
5. System-prompt protocol updates for t2 and t4
6. E3 tests
7. Full test suite
8. Export + run live cycle

---

## Open questions / decisions made

- **Local currency as the standard unit** (superseding an earlier USD proposal — see P8 for the full rename). The DB stores venue costs in local currency (labelled `avg_cost_local` post-P8); the schema field `budget_per_day` is in the same currency. No conversion anywhere.
- **50% visit duration fraction** — constant `VISIT_DURATION_FRACTION = 0.5`, tuneable later
- **2 restaurants + 2 sites per day** — constants `RESTAURANTS_PER_DAY = 2`, `SITES_PER_DAY = 2`
- **1.1-1.3× ratio band** — same for both types, uniform difficulty signal
- **Premium detection via 1.5× category median** — prevents false positives on ordinary required venues
- **Missing `query_resources` → hard fail** (not silent skip) — forces agents to commit to a number
- **Depends on P8** — this doc references `avg_cost_local` and `get_city_currency()`; if P8 isn't landed first, implementation should use current `avg_cost_usd` and these references need updating

---

## Why this matters

Right now a type 4 task can pass validation with `query: "£40/day"` and `constraint: upscale min 1` even if the cheapest upscale meal in the pool is £70 — the task is literally unsolvable but we ship it. A type 2 task can pass with ceiling = 2 hours + 5 museums required even if the minimum schedule is 4 hours. Both are generating noise data that will pollute the benchmark.

With P7, both types get actual numeric feasibility guarantees and a meaningful-difficulty ratio band. Combined with P6 (correctly exempting types from wrong rules), we'll finally have validation that matches the spec end-to-end.
