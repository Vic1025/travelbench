# E3 — Schema validation improvements + B5 difficulty scores
**Status: ✅ complete**
**Blocks: E5**

---

## Overview

Two concerns, primarily in `test_generate_tasks.py`, `generate_task.py`,
`compute_task_difficulty.py`, and `generate_city_venues.py`:

1. **Schema validation gaps** — `validate_task_schema()` and `_verify_task_solvable()`
   don't catch several real problems
2. **B5 difficulty scores not written** — saved tasks have LLM-assigned label only,
   no arithmetic scores; difficulty scoring formula needs redesign

**Note on gate enforcement:** The E2.5 agent loop already enforces validation via SUBMIT
before any task is produced. No separate save gate is needed in `test_generate_tasks.py`
— that design has been removed. E3 focuses on improving the validation logic that SUBMIT
calls, and wiring difficulty scoring.

---

## Part A — Schema validation improvements (`validate_task_schema` + `_verify_task_solvable`)

### A1 — `source_in_profile` tracing check

Every hop-2 P-constraint must trace back to a real signal in the query.

**Two-tier check, no LLM call needed:**

Tier A (hard fail): `source_in_profile` is empty, or is a placeholder string like
`"N/A"`, `"inferred"`, `"implicit"`, `"from context"`. LLM invented the constraint
without a query signal — hop-2 rule violated.

Tier B (soft warning `~ `): tokenise `source_in_profile` into content words (strip
stopwords), check ≥50% appear in `public_input.query`. Paraphrasing is legitimate —
the LLM shouldn't be penalised for summarising well. But if the signal words have
nothing to do with the query, it's a warning.

### A2 — Python script dry-run execution

`python_script` B-score constraints checked for syntax markers only. Need to actually
execute against stubs to catch wrong dict keys, wrong structure assumptions, etc.

**Stubs:**
```python
_stub_plan   = {"city": city, "days": [{"day": 1, "activities": [
    {"venue_id": "v1", "activity_type": "visit",
     "time_start": "10:00", "time_end": "12:00"}
]}]}
_stub_task   = {"days": 1, "rubric": {}}
_stub_venues = {"v1": {"name": "Test", "category": "museum", "price_tier": "mid"}}
```
Hard fail if: exception raised, return value not a float, or value outside [0.0, 1.0].

### A3 — `required_venue_ids` verified against pool

Currently printed as warning in `print_task_summary` but not a hard issue in
`validate_task_schema`. Move to hard issue: if any `required_venue_ids` entry is not
in the pool, fail with the missing ID named.

### A4 — Shared constraint engine module

`_evaluate_generic_constraint`, `_activity_matches_scope`, `_activity_satisfies_condition`,
and the three ordering constants (`TRAFFIC_TIER_ORDER`, `PRICE_TIER_ORDER`, `PACE_ORDER`)
currently live in `eval/evaluator.py`. They need to be callable from `test_generate_tasks.py`
and `generate_task.py` for pool-level checks.

**Decision:** Extract to `scripts/generation/constraint_engine.py`. Update
`eval/evaluator.py` to import from there (no functional change to evaluator).

### A5 — Pool-level constraint satisfiability via generic engine

**Insight:** The generic constraint engine treats activities as carriers for venue_id
and looks up venue data from the venues dict. Running it against the venue pool
(each venue wrapped as `{"venue_id": vid, "activity_type": "visit"}`) checks whether
any venue satisfies a constraint — without a separate implementation.

```python
def _pool_as_activities(venue_pool: list[dict]) -> tuple[list, dict]:
    activities = [{"venue_id": v["venue_id"], "activity_type": "visit"} for v in venue_pool]
    venues     = {v["venue_id"]: v for v in venue_pool}
    return activities, venues
```

For each P-score constraint with `consequence: "p_score_full"` or `"f_score_hard"`:
if pool-level result score == 0.0 → hard fail (no venue can satisfy this constraint).

**Scope translation (`_translate_scope_for_pool`):**
- `activity_type=meal` → `category=restaurant` (and cafe, bar)
- `time_window=...` → skip (scheduling, not existence)
- `per_day` → skip
- `category=...`, `has_tag=...`, `all` → works as-is

**Consequence mapping:** Only `p_score_full` and `f_score_hard` cause hard fails.
`b_score_bonus` failing at pool level is not a hard fail — B-score is additive,
not required.

**Type-specific additions beyond generic engine:**

*Type 3:* The tension condition requires comparing two constraint results.
After running both sides:
- `len(iconic_pool) >= days` AND `len(hidden_gem_pool) >= days`
- Jaccard overlap < 0.4: `len(iconic ∩ hidden_gem) / len(iconic ∪ hidden_gem) < 0.4`
- Minimum exclusive: `len(iconic - hidden_gem) >= 1` AND `len(hidden_gem - iconic) >= 1`

*Type 4:* Budget floor arithmetic (not expressible in generic engine):
`budget >= 2 × min_meal_cost + 2 × min_site_cost + days × 2 × transit_est`

*Type 2:* Time ceiling geometry already handled in E2 Part C.

### A6 — Filtered pool size hard fail

After applying all hard F-score + P-score constraint filters (the same filter used for
pool_size_difficulty in Part C), if the resulting pool has **> 25 venues**, the task
lacks meaningful difficulty — the agent has too many options.

Hard fail in `_verify_task_solvable` (called by SUBMIT):
```
"Filtered pool too large (N venues after constraints) — task lacks meaningful
difficulty. Tighten constraints to narrow below 25."
```

The agent sees this as a SUBMIT error and must add more constraints before retrying.

**Shared filter function:** `_apply_pool_filters(pool, task)` applies hard F-score +
P-score constraints and returns the filtered list. Used by:
- `_verify_task_solvable` (for > 25 check and existing checks)
- `compute_avg_venue_difficulty` (replacing old random sampling)
- `_pool_size_difficulty_score` (for difficulty axis)

---

## Part B — F-score evaluator: Hard fail #3 (Type 6)

Enabled by E2 Part E providing ticket_availability in `load_ground_truth()`.

**Hard fail #3:** Agent books a venue on a sold-out anchor date without having called
`get_official_site(venue_id, anchor_date)` first.

Check: for each activity on an anchor date, if `ticket_availability[date]["sold_out"]`
is True, verify `official_site_called` (from C-score tracking) includes a call with
that specific `(venue_id, date)` pair. If not → hard fail.

This is the core Type 6 trap mechanic.

---

## Part C — B5 difficulty scoring redesign

### Three axes

**Axis 1: `constraint_complexity`** (pure arithmetic from task JSON, unchanged):
- Hop depth distribution across P+B constraints
- Constraint count (up to 6 → max score)
- Structural type weight (Type 5=0.7, Type 6/3=0.6, Type 2/4=0.5, Type 1=0.4)
- B-score bonus if any B-score constraints present
- Secondary structural type +0.1 if present

**Axis 2: `avg_venue_difficulty`** (redesigned — no more random sampling):
Apply `_apply_pool_filters(pool, task)` to get the P-score-filtered pool.
Average `venue_difficulty_score` across all venues in that filtered pool.
Returns None if no scores computed yet (falls back to constraint_complexity alone).

**Why filtered pool (not full pool):** A task with halal + wheelchair constraints should
average difficulty over the halal+wheelchair venues, not all 50. The constraint context
is what makes a venue relevant.

**Axis 3: `pool_size_difficulty`** (new):
After `_apply_pool_filters`, score based on how many venues remain:

| Pool size | Score |
|-----------|-------|
| ≤ 3       | 1.0   |
| 4–8       | 0.75  |
| 9–15      | 0.5   |
| 16–25     | 0.25  |
| > 25      | hard fail (SUBMIT rejects — see A6) |

Narrow pool = harder for agent to find valid options.
Wide pool = task constraints are too loose.

### Combined score

```
difficulty_combined = 0.50 × constraint_complexity
                    + 0.25 × avg_venue_difficulty   (or 0 if None)
                    + 0.25 × pool_size_difficulty
```

If `avg_venue_difficulty` is None: `difficulty_combined = 0.50 × cc + 0.50 × pool_size`

Label thresholds (unchanged): < 0.35 → easy, < 0.60 → medium, ≥ 0.60 → hard

### Orchestrator step 9: compute_venue_difficulty

`compute_venue_difficulty.py` is not currently called from `generate_city_venues.py`.
It must run before task generation since `avg_venue_difficulty` reads
`venue_difficulty_score` from the DB.

Add as **step 9** after ticket availability (step 8):
```python
from scripts.generation.compute_venue_difficulty import compute_all_venue_difficulties
scores = compute_all_venue_difficulties(city=city_key, dry_run=dry_run, db_path=db_path)
```

All inputs (source docs, doc_venue_refs, wrong_info rows, truth carriers) are
committed by the end of step 5 (venue generation). Step 9 is pure arithmetic, no API.

---

## Tasks

### Part A — Schema validation
- [x] A1: Tier A `source_in_profile` placeholder check (hard fail)
- [x] A1: Tier B `source_in_profile` word-overlap check (soft `~ ` warning)
- [x] A2: Python script dry-run against stubs — hard fail on exception or bad return
- [x] A3: `required_venue_ids` pool membership — move from print to hard issue
- [x] A4: Create `scripts/generation/constraint_engine.py` with
      `_evaluate_generic_constraint`, `_activity_matches_scope`,
      `_activity_satisfies_condition`, `TRAFFIC_TIER_ORDER`, `PRICE_TIER_ORDER`,
      `PACE_ORDER`; update `eval/evaluator.py` imports
- [x] A5: `_pool_as_activities()` helper in `generate_task.py` / `test_generate_tasks.py`
- [x] A5: `_translate_scope_for_pool()` — scope translation helper
- [x] A5: Run all generic P/F constraints through pool engine in `validate_task_schema`
- [x] A5: Type 3 Jaccard overlap check (< 0.4) + minimum exclusive per side
- [x] A5: Type 4 budget floor check (explicit arithmetic)
- [x] A6: `_apply_pool_filters(pool, task)` shared helper — hard F-score + P-score filters
- [x] A6: Filtered pool > 25 hard fail in `_verify_task_solvable`

### Part B — F-score evaluator
- [x] Hard fail #3 (Type 6): sold-out anchor booked without site check
      (depends on E2 Part E ticket_availability in load_ground_truth)

### Part C — Difficulty scoring
- [x] Step 9 in `generate_city_venues.py` — call `compute_all_venue_difficulties`
- [x] `_apply_pool_filters(pool, task)` — reuse in `compute_avg_venue_difficulty`
      (replaces random sampling with mean over filtered pool)
- [x] `_pool_size_difficulty_score(n)` — curve function
- [x] Update `annotate_task_difficulty` — add pool_size_difficulty axis,
      new weights 50/25/25
- [x] Update `compute_avg_venue_difficulty` — filtered pool mean, no sampling
- [x] Call `annotate_task_difficulty` at task save time in `test_generate_tasks.py`
      (after agent loop produces accepted task)
- [x] Summary output: show constraint_complexity + avg_venue_difficulty +
      pool_size_difficulty + difficulty_combined per task
- [x] Test: difficulty fields populated on saved tasks
- [x] Test: pool > 25 triggers hard fail
- [x] Test: pool_size_difficulty curve correct at boundaries
