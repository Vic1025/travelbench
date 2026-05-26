## Prerequisite: Per-city SQLite databases

**Done alongside E5 implementation.**

Current state: all cities share one `data/travelbench.db`. This is fragile
(one corrupt file loses all cities) and makes it impossible to isolate city data.

**New structure:**
```
data/
  cities/
    london/
      travelbench.db
    tokyo/
      travelbench.db
    istanbul/
      travelbench.db
  data1/         ← existing task JSON output, unchanged
  sources/       ← existing Paris JSON sources, unchanged
  ground_truth/  ← existing Paris JSON ground truth, unchanged
```

**Key changes:**
- `db.py`: add `get_city_db_path(city: str) -> Path` returning
  `data/cities/{city}/travelbench.db`. Keep `DB_PATH` constant for
  backwards compatibility in tests.
- All functions with `(city, db_path=DB_PATH)` signature: default changes
  to `db_path = get_city_db_path(city)` derived inside the function when
  `db_path` is None.
- `generate_city_venues.py`: `init_db(get_city_db_path(city))` at startup,
  creates `data/cities/{city}/` automatically.
- `mock_tools.py`: per-lookup `get_city_db_path(city)` replaces hardcoded
  `_DB_PATH`. Paris still reads from JSON files (`_JSON_CITIES` list unchanged).
- All 16 affected files updated. `--db` override flag added to CLIs that
  were missing it (generate_city_venues.py, research_city.py, validate_city.py,
  calibrate_constraints.py).

---

# E5 — Per-city DB structure + venue/task diversity analysis
**Status: ✅ complete**

---

## Part A — Per-city database restructure

### Problem

All cities currently share a single `data/travelbench.db`. If that file is lost or
corrupted, all city data is gone. There is also no `--db` flag on the main pipeline
entry point (`generate_city_venues.py`), making it impossible to use a test DB
without code changes.

### Design

**New directory structure:**
```
data/
  cities/
    london/
      travelbench.db
    tokyo/
      travelbench.db
    istanbul/
      travelbench.db
  data1/          ← existing task JSON output, unchanged
  sources/        ← existing Paris JSON sources, unchanged
  ground_truth/   ← existing Paris JSON ground truth, unchanged
```

**New function in `db.py`:**
```python
def get_city_db_path(city: str) -> Path:
    return Path(__file__).parent.parent.parent / "data" / "cities" / city / "travelbench.db"
```

**Default parameter pattern:** Every function that takes both `city` and `db_path`
defaults to `get_city_db_path(city)` when `db_path is None`. The old `DB_PATH`
constant stays for backwards compatibility with tests that pass custom paths.

**`mock_tools.py`:** `_DB_PATH` becomes a per-city lookup via `get_city_db_path(city)`.
Paris still reads from legacy JSON files (`_JSON_CITIES` list unchanged).

**`generate_city_venues.py` CLI:** calls `init_db(get_city_db_path(city))` automatically
from `--city` arg. `--db` flag stays as an override for testing.

### Files to update (16)
`db.py`, `generate_city_venues.py`, `generate_venue.py`, `research_city.py`,
`validate_city.py`, `test_generate_tasks.py`, `generate_multi_venue_docs.py`,
`generate_task.py`, `task_agent.py`, `pool_utils.py`, `compute_task_difficulty.py`,
`compute_venue_difficulty.py`, `evaluator.py`, `server/mock_tools.py`,
`calibrate_constraints.py`, `populate_seasonal_windows.py`

---

## Part B — Venue and task diversity saturation analysis

 questions before scaling to new cities:

1. **How many venues is enough?** At what point does adding more venues produce
   redundant entries that share the same category/district/tier profile as existing
   ones — adding corpus bulk without adding benchmark value?

2. **How many tasks per window is enough?** At what point do generated tasks start
   repeating constraint patterns and venue combinations, giving diminishing diversity
   returns?

Both questions need empirical answers, not guesses. The pipeline has fixed targets
(50 venues, ~20 tasks/window) that need validation against real data.

---

## Design

Two standalone analysis scripts — manual invocation after generation runs.
No pipeline wiring. Results inform future generation targets.

---

## Experiment 1 — Venue saturation curve

**Script:** `scripts/analysis/venue_diversity_score.py --city <city> --db <path>`

**What it measures:**
For a given city's venue pool, compute how much genuine diversity each additional
venue adds. "Genuine diversity" = introduces a new (category, district, traffic_tier)
combination not already present in the pool.

**Algorithm:**
1. Load all venues for the city, ordered by generation order (row insertion order)
2. Walk through venues one by one, tracking the set of known combinations
3. For each venue N, record:
   - `n_combinations` — unique (category, district, traffic_tier) triples so far
   - `marginal_new` — 1 if this venue introduced a new combination, 0 if not
   - `tag_diversity` — number of distinct tags seen so far
4. Compute rolling marginal_new_rate over windows of 10 venues
5. Print saturation report + CSV output

**Saturation threshold:** when the rolling marginal_new_rate drops below 30% and
stays there for 10+ consecutive venues, the pool is saturated.

**Output:**
```
Venue diversity — London (50 venues)
  Unique (cat, district, tier) combinations: 38 / 50 venues
  Marginal new rate by decile:
    Venues  1-10: 100% new combinations
    Venues 11-20:  80% new combinations
    Venues 21-30:  60% new combinations
    Venues 31-40:  40% new combinations
    Venues 41-50:  20% new combinations  ← saturation zone
  Saturation point: ~venue 38
  Tag diversity: 24 distinct tags at 50 venues
  Recommendation: 40-45 venues sufficient for this city profile
```

---

## Experiment 2 — Task diversity saturation

**Script:** `scripts/analysis/task_diversity_score.py --city <city> --window <window_id>`

**What it measures:**
For a given city+window task set, compute how much genuine diversity each additional
task adds. Three metrics, each computed after adding each new task:

**Metric 1 — Pairwise constraint Jaccard similarity:**
For each pair of tasks, compute Jaccard similarity on their constraint pattern sets
(e.g. {label_required, time_threshold, pace_relaxed}). Mean across all pairs.
Higher = more repetition. Target: mean Jaccard < 0.4 across the whole set.

**Metric 2 — Venue coverage:**
Fraction of pool venues appearing in at least one task's `required_venue_ids` or
surviving `_apply_pool_filters`. Higher = better coverage of the pool.
Plateaus when all interesting venue combinations have been covered.

**Metric 3 — Structural type entropy:**
Shannon entropy of the structural type distribution. Max entropy (all 6 types
equally represented) = log2(6) ≈ 2.58. Target: entropy > 2.0.

**Algorithm:**
1. Load task JSONs for city+window from `data/data1/tasks/unfiltered/<model>/`
2. Sort by filename (proxy for generation order)
3. For N = 1..len(tasks), compute all three metrics on the first N tasks
4. Print curve + marginal diversity gain per task added

**Output:**
```
Task diversity — London / lon_carnival_2026 (24 tasks, 3 models)

After N tasks:
  N= 6: Jaccard=0.18 | venue_coverage=24% | type_entropy=2.58 (all types)
  N=12: Jaccard=0.24 | venue_coverage=38% | type_entropy=2.52
  N=18: Jaccard=0.31 | venue_coverage=49% | type_entropy=2.48
  N=24: Jaccard=0.38 | venue_coverage=55% | type_entropy=2.41
  N=30: Jaccard=0.44 | venue_coverage=58% | type_entropy=2.35  ← diminishing returns

Marginal diversity gain per 6-task batch:
  Tasks  1- 6: high (new types introduced, low overlap)
  Tasks  7-12: high (constraint patterns diverging)
  Tasks 13-18: medium (some pattern reuse)
  Tasks 19-24: medium (venue combinations thinning)
  Tasks 25-30: low (Jaccard rising, coverage plateauing)

Recommendation: ~20-24 tasks/window for a 50-venue pool
```

---

## When to run

Run both scripts after:
- First London generation run with new E2.5 agent-loop pipeline
- Any new city's first generation run
- After significantly changing venue generation parameters

**Practical workflow:**
1. Generate city at target venue count (50)
2. Run `venue_diversity_score.py` — if saturation before 45, reduce target
3. Generate 30+ tasks across 2 windows
4. Run `task_diversity_score.py` — find the knee in the Jaccard curve
5. Set per-window task target accordingly

---

## Tasks

- [x] `scripts/analysis/venue_diversity_score.py` — combination uniqueness curve
- [x] `scripts/analysis/task_diversity_score.py` — Jaccard + coverage + entropy
- [x] Run both on London after first agent-loop generation run
- [x] Document recommended targets in DESIGN_DECISIONS.md

---

## Tasks

### Part A — Per-city DB restructure
- [x] `get_city_db_path(city)` in `db.py`
- [x] Update `db.py`: `init_db` creates `data/cities/{city}/` directory automatically
- [x] Update all 16 files: import `get_city_db_path`, update defaults
- [x] `mock_tools.py`: per-city DB lookup, Paris legacy unchanged
- [x] All existing tests still pass after migration
- [x] Export and confirm with user

### Part B — Diversity analysis scripts
- [x] `scripts/analysis/venue_diversity_score.py`
- [x] `scripts/analysis/task_diversity_score.py`
- [x] Run on London after first agent-loop generation run
- [x] Document recommended targets in DESIGN_DECISIONS.md
