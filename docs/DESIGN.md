# TravelBench — Feature Implementation Design

*Written after session 5 brainstorm. This doc is the reference before touching code.*

---

## Part 1 — What "within walking distance" means here

### The geographic tool question

The user asked about uploading a geographic tool. We already have lat/lng on every
venue (inside `location.lat` / `location.lng`) and the existing `get_travel_time`
tool does haversine distance → walking minutes. So we don't need a new tool for
simple proximity.

What we DO need for `dependency_chain` and `walking_distance` constraints is a
**new mock tool: `find_nearby_venues(venue_id, city, max_walk_minutes, category?)`**
This exposes geographic proximity as something the *agent* can actively query,
rather than only the evaluator knowing about it. Without this tool, an agent can't
reason about "is there a restaurant within 10min of Sainte-Chapelle?" — it would
have to guess or call get_travel_time for every pair.

The tool would:
- Accept a venue_id and max_walk_minutes threshold
- Return a ranked list of venues within that radius
- Optionally filter by category (restaurant, park, museum...)
- Source: computed from travel_matrix at load time (no new ground truth needed)

C-score implication: for dependency_chain constraints, we add a check that the
agent called `find_nearby_venues` at least once near the anchor venue.

---

## Part 2 — All brainstormed features, grouped by implementation complexity

### TIER 1 — Low coupling, can be added independently

**source_required** (most important P-score fix)
- Add `"source_required": "official_site" | "blogs_and_forums" | null` to rubric constraints
- Evaluator checks: did tool_call_log contain a call that retrieved a doc/site
  mentioning the constraint label for that venue?
- Implementation: index which doc_ids contain which venue+label pairs at eval load time
- Risk: none — fully additive, doesn't change existing scoring for constraints without this field

**noise_preference**
- New pattern: `noise_level_max`
- Venue ground truth already has `regulations.noise_level` (quiet/moderate/loud)
- params: `max_noise_level: "quiet" | "moderate"`
- Evaluator: check all venues in plan against max threshold
- No new tool needed

**district_avoidance**
- New pattern: `district_excluded`
- `venue.location.district` already exists
- params: `excluded_districts: ["1st arrondissement", ...]`
- Evaluator: any venue in plan from excluded district → deduction
- No new tool needed

**visit_count_minimum / cuisine diversity**
- New pattern: `category_count_minimum`
- params: `min_count: int, field: "category" | "district" | "label"`
- Evaluator: count distinct values of field across all activities

**opening_time_required**
- Extend existing `time_threshold` pattern to support `open_before` constraint
- params: `open_before: "HH:MM"` on visit-type activities
- Check venue.hours[day_of_week][0] <= open_before threshold
- Combines naturally with "I always start at 9am" type profiles

**max_single_activity_cost**
- Already in TODO as a numeric_aggregate variant
- params: `max_cost_usd`, `activity_type: "meal" | "visit" | null (all)`
- Evaluator: check each individual activity's estimated_cost_local

---

### TIER 2 — Requires new tool or new ground truth fields

**Weather system**
New tool: `get_weather_forecast(city, date) → {condition, temp_c, wind_kph, precipitation_mm}`

Ground truth addition: `weather_forecast` dict keyed by date in each city's data dir:
```json
{
  "2025-03-07": {"condition": "sunny",  "temp_c": 12, "wind_kph": 8,  "precipitation_mm": 0},
  "2025-03-08": {"condition": "rainy",  "temp_c": 9,  "wind_kph": 22, "precipitation_mm": 6}
}
```

Venue addition: `outdoor_sensitivity: "none" | "partial" | "outdoor_only"`
- outdoor_only: parks, open markets, rooftop bars → hard fail if rainy
- partial: canal-side terraces, street-food stalls → soft deduction
- none: museums, restaurants, indoor venues → unaffected

F-score changes:
- Hard fail: outdoor_only venue on rainy/stormy day
- Soft (0.5 partial): outdoor_only on cloudy day; partial-outdoor on rainy

C-score addition:
- Deduct if plan contains ≥2 outdoor_only venues and agent never called get_weather_forecast

New constraint pattern: `weather_aware`
- In rubric as a semantic/code hybrid: "I want to spend time outdoors" → plan should
  show evidence of weather-checking and contingency for rain

**find_nearby_venues tool**  (needed for dependency_chain, walking_distance)
```python
def tool_find_nearby_venues(anchor_venue_id, city, max_walk_minutes, category=None)
  → {results: [{venue_id, name, walk_minutes, category}]}
```
- Built from travel_matrix at load time (no new ground truth files)
- Returns venues sorted by walk_minutes ascending
- category filter uses venue.category field

**dependency_chain pattern**
- Requires find_nearby_venues to be callable
- Rubric format:
  ```json
  {
    "pattern": "dependency_chain",
    "anchor_venue_id": "par_s08",
    "requirement": "meal_within_walk",
    "max_walk_minutes": 15,
    "timing": "before"
  }
  ```
- Evaluator checks: is there a meal activity ending before par_s08 starts,
  and is that restaurant within 15min walk of par_s08?
- C-score: did agent call find_nearby_venues with anchor=par_s08?

**temporal_cross_day**
- New pattern: enforces cross-day uniqueness rules
- Requires evaluator to group activities by day before checking
- params: `field: "category" | "district" | "label"`, `max_per_day: int`
- Example: `{"field": "category", "value": "museum", "max_per_day": 1}`
- Evaluator: for each day, count activities where venue[field] matches value

**social_context**  (group size, occasion)
- New venue ground truth field: `max_group_size` and `suitable_occasions`
- New rubric params: `group_size: int`, `occasion: "anniversary" | "family" | "business"`
- Complex because it's partly semantic (romantic = anniversary) and partly
  structural (max_group_size = 2 for intimate restaurant)

---

### TIER 3 — Significant architecture changes

**travel_mode support**
- Needs travel_matrix expanded: currently single walking time per pair
  New structure: `{"walking": 22, "transit": 12, "taxi": 8, "bike": 14}`
- get_travel_time gets a `mode` param
- F-score buffer checks use mode from agent's actual call
- Budget: taxi/transit adds to estimated_cost_local
- New pattern: `transport_mode_required`

**price_tier + per-meal-type costs**
- Venue ground truth splits avg_cost_local into lunch/dinner
- New field: price_tier (budget/mid/upscale/fine-dining)
- Existing numeric_aggregate pattern gets a `per_meal_type` option
- New pattern: `price_tier_required` (cleaner than raw thresholds)

**multi-city expansion**
- generate_sources.py already abstracted per-city
- Real blocker: incorrect hours, blog content, official sites all need content
- LLM generation pipeline is the right approach (1 CLI command = 1 new city)

---

## Part 3 — Implementation order recommendation

Given the goal of meaningfully improving benchmark difficulty first:

**Sprint 1 — P-score integrity (source_required)**
1. Add source indexing at evaluator load: which doc_ids contain which venue+label
2. Add `source_required` field to a subset of existing task constraints
3. Verify P-scores drop for agents that don't consult the right source

**Sprint 2 — Weather system**
1. Add weather_forecast to ground truth (hardcoded, 2 dates)
2. Add outdoor_sensitivity to venue ground truth
3. Implement get_weather_forecast tool in mock_tools.py
4. F-score: outdoor_only hard fail, partial outdoor soft deduction
5. C-score: deduct for missing weather call on outdoor plans
6. Add 1 new weather-conditional task

**Sprint 3 — find_nearby_venues + dependency_chain**
1. Build find_nearby_venues from existing travel_matrix
2. Add dependency_chain pattern + handler
3. Add 1 task with dependency_chain constraint (Sainte-Chapelle meal-before)

**Sprint 4 — Tier 1 patterns**
noise_level_max, district_excluded, category_count_minimum, max_single_activity_cost
These are fast to implement and significantly expand constraint diversity.

**Sprint 5 — Travel modes**
Highest architectural impact — touch travel_matrix, mock_tools, F-score, C-score, budget.
Do last to avoid breaking in-progress work.

---

## Part 4 — How each new feature affects scoring tiers

| Feature                  | C-score impact              | F-score impact              | P-score impact           |
|--------------------------|-----------------------------|-----------------------------|--------------------------|
| source_required          | —                           | —                           | Lowers pass rate; tests reasoning quality |
| weather system           | New check: weather call     | New hard/soft fail class    | New weather_aware pattern |
| find_nearby_venues       | New check: used for anchor  | —                           | Enables dependency_chain |
| dependency_chain         | Must call find_nearby       | Timing + proximity check    | New pattern handler      |
| temporal_cross_day       | —                           | —                           | New pattern handler      |
| noise_level_max          | —                           | —                           | New pattern handler      |
| district_avoidance       | —                           | —                           | New pattern handler      |
| travel_mode              | New check: mode ignored     | Buffer uses actual mode     | New pattern handler      |
| max_single_activity_cost | Cross-check cost sum        | —                           | New pattern handler      |
| social_context           | —                           | Capacity check              | New hybrid pattern       |

---

## Part 5 — Open questions before implementation

1. **source_required strictness**: should it be a hard fail (0 credit if source not consulted)
   or a soft deduction (partial credit)? Recommendation: soft for now — a lucky guess
   is still worth something, just less than a verified finding.

2. **Weather conflicting forecasts**: if get_weather_forecast is called twice and returns
   slightly different conditions, which counts for F-score? Recommendation: use the
   ground truth condition regardless of what the tool returned — but log whether the
   agent got consistent results.

3. **find_nearby_venues in C-score**: should the C-score check fire only for
   dependency_chain tasks, or whenever the agent builds a plan with a sequence
   that requires proximity reasoning? Recommendation: only for dependency_chain rubric
   items — avoids penalising agents on tasks where the constraint doesn't exist.

4. **travel_matrix expansion for modes**: the haversine-based matrix is a stub.
   For transit, we'd want real or realistic metro-adjusted times. Recommendation:
   use multipliers for now (transit = 0.55x walking, taxi = 0.35x, bike = 0.65x)
   and note this as an approximation in the data README.

5. **outdoor_sensitivity scope**: should weather affect leisure activities differently
   from meals? A rainy day might cancel a canal walk but not affect a rooftop bar
   (which is covered). Recommendation: add `covered` boolean to venue ground truth
   as a sub-field of outdoor_sensitivity.

