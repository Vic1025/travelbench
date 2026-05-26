# E2 — Wire travel matrix + ticket availability into the orchestrator
**Status: ✅ complete**
**Blocks: E5**
**Depends on: E1 (for ticket availability to be meaningful)**

---

## Problem

Both scripts exist and all tests pass, but `generate_city_venues.py` never calls them:

| Script | Status | Called by orchestrator? |
|--------|--------|------------------------|
| `build_travel_matrix.py` | ✅ complete, tested | ❌ no |
| `populate_ticket_availability.py` | ✅ complete, tested | ❌ no |

Beyond orchestrator wiring, four downstream systems are broken or incomplete because of missing data. These are all in scope for E2.

---

## Part A — Orchestrator wiring (original E2 scope)

Add steps 7 and 8 to `generate_city_venues()` after the events write:

```python
# ── Step 7: Travel matrix ────────────────────────────────────────────────────
from scripts.generation.build_travel_matrix import build_travel_matrix
matrix_result = build_travel_matrix(city=city_key, api_key=None, db_path=db_path, dry_run=dry_run)

# ── Step 8: Ticket availability ──────────────────────────────────────────────
from scripts.generation.populate_ticket_availability import populate_ticket_availability
ta_result = populate_ticket_availability(city=city_key, dry_run=dry_run, db_path=db_path)
```

Both are idempotent. Travel matrix uses haversine by default (no ORS key needed).
Ticket availability requires windows — if none, returns `no_windows` and warns but doesn't fail.

CLI additions: `--ors-key`, `--skip-matrix`, `--skip-tickets`

---

## Part B — Travel time signal for task generation

**Problem:** Task generation LLM has no geographic signal. It writes time-ceiling
constraints (Type 2) without knowing whether the venues it picks are 10 min or 45 min
apart. Result: time ceilings are not meaningfully tight.

**Decision:** Add `lat`, `lng` to the venue pool listing in the task generation prompt,
plus a one-line distance formula. The LLM computes travel estimates inline when designing
time-bounded sequences — no tool loop, no pre-computed table, no context bloat.

**Why not a pre-computed matrix:** 50 venues × 50 venues = 2,450 pairs. Even compact,
that's ~37KB of context just for travel data. Injecting only the pairs the LLM needs
(via an agent tool loop) would work but requires converting task generation from
single-shot to an agent loop — meaningful architecture change. The formula approach
costs ~500 chars of context (two numbers per venue) and covers all the pairs the LLM
actually uses while designing a task.

**What to inject into the prompt:**

Each venue line in the pool listing gains two columns:
```
par_r01 | Le Comptoir | restaurant | Marais | high | relaxed | 48.857, 2.351 | tags: french
```

Plus a formula note in the system prompt:
```
TRAVEL TIME ESTIMATION:
Straight-line distance (km) ≈ sqrt((Δlat × 111)² + (Δlng × 75)²)
Walking: distance_km × 12 min  |  Transit: distance_km × 8 + 5 min
Use these when designing time-bounded sequences (Type 2 tasks).
The real travel time will be 10-30% higher — build in a buffer.
```

**What needs to change:**
1. `load_venue_pool()` in `generate_task.py` — add `lat`, `lng` to the SELECT
2. `load_london_pool()` in `test_generate_tasks.py` — add lat/lng
3. Remove `load_paris_pool()` — Paris data is retired; Paris tasks now use DB (Part E)
4. Add venue pool format: append `{lat:.3f}, {lng:.3f}` to each venue line
5. Add travel formula to system prompt in `test_generate_tasks.py` and `generate_task.py`

**Note on unmatched venues:** Low-traffic venues and Overpass-failed high/mid venues
have invented coordinates. Formula-based estimates on invented coords are still better
than flat 15 min for most cases, and low-traffic venues are rarely the pivot venues
for Type 2 time-ceiling tasks. Acceptable approximation.

**Solvability gate fallback (Part C):** When travel matrix exists in DB, use it.
When matrix is missing or incomplete, use the same haversine formula from
`build_travel_matrix.py` (already exported as `haversine_km` + `estimate_minutes`).

---

## Part C — Solvability gate: travel time for Type 2

**Problem:** `_verify_task_solvable()` uses flat 15-min travel for schedule feasibility.

**Decision:** Only Type 2 tasks with a clear venue subgroup need geometric checking.
Other types (1, 3, 4, 5, 6) don't use a time ceiling as their core mechanism.

**Approach:**
- Detect Type 2 by `structural_type` containing `"type2"`
- Identify the candidate venue set: venues passing all hard + P-score label/regulation filters
- Find the K nearest venues within that set using haversine (K = `min_count` or `category_count_minimum` param, default 3)
- Compute minimum visit time: K × avg_visit_minutes + sum of (K-1) nearest-neighbour travel times
- Compare against the task's time ceiling (from `time_threshold` param if present, else `days × 10hrs`)
- Fail if minimum time > ceiling; pass otherwise
- Fall back to flat 15-min if <50% of candidate venues have real coordinates

For non-Type-2 tasks, keep the existing avg_visit + 15min check as the rough floor.

---

## Part D — Ticket availability: task gen prompt + solvability gate

**Problem:** Task gen LLM doesn't know which dates are sold out. Solvability gate doesn't
check ticket availability. Evaluator can't check tickets for DB-generated cities.

**Format decision:** Standardise on DB dict format everywhere:
`{date_str: {"sold_out": bool, "slots_available": N}}`
Paris JSON string format (`{date_str: "sold_out"}`) is retired with Paris data.
Evaluator line 719 check needs updating from `== "sold_out"` to `.get("sold_out")`.

**In task generation prompt:** add `unavailable_dates: [...]` to each venue entry,
derived from `ticket_availability` table rows with `sold_out=1` within the window's
date range. The LLM can then write Type 6 constraints knowing a real sold-out trap
exists at a specific venue.

**In solvability gate (`_verify_task_solvable`):**
- If `required_venue_ids` is non-empty: verify each required venue has at least one
  non-sold-out date within the task's window dates. Hard fail if zero available dates.
- No other ticket check at generation time — availability traps are discovered by
  agents at run time.

**Hard fail conditions in F-score evaluator:**
1. Agent books a venue on a date with `sold_out=True` in ticket_availability
2. Agent books a required venue, and no date in the task window has availability

Note: Hard fail #3 (Type 6 anchor date booked without site check) is an evaluator
enforcement concern — implemented in E3 once E2 makes the ticket data available.

---

## Part E — Retire Paris JSON, standardise on DB for all cities

**Decision:** `load_ground_truth()` becomes a pure DB function. No JSON fallback.
Paris data is retired — new Paris runs use DB like every other city.

**Field mapping DB → evaluator format:**

```python
# Hours: flat columns → nested dict
venue["hours"] = {
    "mon": row["hours_mon"], "tue": row["hours_tue"], "wed": row["hours_wed"],
    "thu": row["hours_thu"], "fri": row["hours_fri"],
    "sat": row["hours_sat"], "sun": row["hours_sun"],
}

# Regulations: flat int columns → nested dict of bools
venue["regulations"] = {
    "pet_friendly":         bool(row["pet_friendly"]),
    "wheelchair_accessible":bool(row["wheelchair_accessible"]),
    "parking_nearby":       bool(row["parking_nearby"]),
    "age_restriction":      row["age_restriction"],   # int or None
    "dress_code":           row["dress_code"],
    "photography_allowed":  bool(row["photography_allowed"]),
    "noise_level":          row["noise_level"],
    "reservation_required": bool(row["reservation_required"]),
    "outside_food_allowed": bool(row["outside_food_allowed"]),
    "family_friendly":      bool(row["family_friendly"]),
}

# Tags: separate table → labels list
venue["labels"] = [r["tag"] for r in conn.execute(
    "SELECT tag FROM tags WHERE venue_id = ?", (vid,)
)]

# Location: flat column → nested dict (evaluator reads venue["location"]["district"])
venue["location"] = {"district": row["district"]}

# Wrong info: map to both names evaluator checks
venue["has_wrong_info"] = bool(row["has_wrong_info_planned"])
venue["has_stale_hours"] = bool(row["has_wrong_info_planned"])

# Ticket availability: attach from ticket_availability table
ticket_rows = conn.execute(
    "SELECT date, sold_out, slots_available FROM ticket_availability WHERE venue_id = ?",
    (vid,)
).fetchall()
venue["ticket_availability"] = {
    r["date"]: {"sold_out": bool(r["sold_out"]), "slots_available": r["slots_available"]}
    for r in ticket_rows
}
```

**Matrix format DB → evaluator:**
DB stores `walk_minutes`, `transit_minutes` per pair.
Evaluator expects `{v1_to_v2: {"walking": N, "transit": N}}` (handles legacy int too).
```python
matrix = {}
for row in conn.execute("SELECT * FROM travel_matrix WHERE city = ?", (city,)):
    key = f"{row['venue_id_a']}_to_{row['venue_id_b']}"
    matrix[key] = {"walking": row["walk_minutes"], "transit": row["transit_minutes"]}
```

If `travel_matrix` table is empty: return `{}` and emit a warning — visible gap,
not a silent skip.

---

## Tasks

### Part A — Orchestrator wiring
- [x] Add step 7 (travel matrix) to `generate_city_venues()` after step 8 events write
- [x] Add step 8 (ticket availability) after step 7
- [x]  Add `--ors-key`, `--skip-matrix`, `--skip-tickets` CLI flags
- [x]  Add `matrix_pairs`, `ticket_rows` to return dict
- [x]  Update `test_orchestrator.py` — verify steps 7+8 are called in dry-run

### Part B — Lat/lng + formula in task gen prompt
- [x]  Add `lat`, `lng` to `load_venue_pool()` SELECT in `generate_task.py`
- [x]  Add `lat`, `lng` to `load_london_pool()` in `test_generate_tasks.py`
- [x]  Remove `load_paris_pool()` from `test_generate_tasks.py` (Paris retired, uses DB)
- [x]  Add `{lat:.3f}, {lng:.3f}` column to venue pool listing format in prompts
- [x]  Add travel formula block to system prompt in both `generate_task.py` and `test_generate_tasks.py`

### Part C — Solvability gate Type 2 geometry
- [x]  Detect Type 2 in `_verify_task_solvable()`
- [x]  Extract candidate venue set and time ceiling for Type 2
- [x]  Nearest-K check: use `get_travel_time()` from DB matrix first, fall back to
      `haversine_km()` + `estimate_minutes()` from `build_travel_matrix.py` when missing
- [x]  Fall back to 15-min floor when <50% of candidate venues have real coordinates

### Part D — Ticket availability
- [x]  Add `unavailable_dates` to venue pool listing in task gen prompts
      (query `ticket_availability` table for sold_out rows within window dates)
- [x]  Add required-venue availability check to `_verify_task_solvable()`
- [x]  Update evaluator F-score sold-out check from `== "sold_out"` string to
      `.get("sold_out")` dict format (line 719)
- [x]  Hard fail #1: evaluator reads sold_out from DB-format ticket_availability dict
- [x]  Hard fail #2: no available date for required venue within window

### Part E — Retire Paris JSON, standardise on DB
- [x]  Rewrite `load_ground_truth()` as pure DB function — no JSON fallback
- [x]  Build `venue["hours"]` nested dict from flat `hours_mon...hours_sun` columns
- [x]  Build `venue["regulations"]` nested dict from flat regulation columns
- [x]  Build `venue["labels"]` list from `tags` table JOIN
- [x]  Build `venue["location"] = {"district": row["district"]}`
- [x]  Map `has_wrong_info_planned` → both `has_wrong_info` and `has_stale_hours`
- [x]  Attach `ticket_availability` dict to each venue from `ticket_availability` table
- [x]  Build matrix dict `{v1_to_v2: {"walking": N, "transit": N}}` from `travel_matrix` table
- [x]  Emit visible warning (not silent skip) when matrix table empty
