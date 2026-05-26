# TravelBench — Phase 5.5 TODO

> ⚠ **Archived phase TODO.** Content below preserved for historical record.
> **Current source of truth:** [docs/TODO_PHASE6.md](TODO_PHASE6.md) (Phase 6 work tracker).
> **Phase 7 candidates:** [docs/PHASE7_FUTURE_WORK.md](PHASE7_FUTURE_WORK.md).
*Decisions from April 28 analysis session. Focus: constraint diversity,
wrong-info scaling, and score differentiation.*

---

## Agreed Changes — Implementation Notes

**Pre-implementation findings (April 28):**
- V2: `_used_venue_ids` pattern in generate_task.py → task_agent.py is clean;
  replicate for `_used_tension_axes`.
- V6: Current check at test_generate_tasks.py:1720 accepts `time_window OR venue_id`.
  Tighten to require `time_window`; keep `venue_id` as optional additional.
- V7: Engine handles `sum` aggregation correctly for both `per_day` and `all` scope.
  Reads `estimated_cost_local` from plan activity, falls back to venue ground truth.
- W2: Pool listing reads `has_wrong_info` boolean from pool_utils.py:151.
  Need to join wrong_info table to get category string.
- D1: Only 1 venue in London has <3 docs (Okinawa Izakaya, WI venue, 2 docs).
  Doc count is emergent from per-venue agent — enforce via VERIFY check.
  Existing London pool is nearly compliant already.

### V1. P-score aggregation diversity enforcement
**Where:** `test_generate_tasks.py` → `validate_task_schema`
**What:** If task has ≥3 P-constraints, reject if ALL use `{at_least}`.
At least one must use a different aggregation type: `all`, `none`, `ratio`,
`count_distinct`, `sum`, `at_most`, `at_most_distinct`, `at_least_days`.
**Also:** Add matching guidance line to task_agent.py handbook section
under CHARACTER TRAIT RULES:
"Aggregation diversity: if the task has ≥3 P-constraints, at most 2 may
use `{at_least}`. At least one must use a different aggregation type."
**Rationale:** Current 23-task run has 92% `at_least` aggregation. All
constraint types are documented with examples — agent just defaults to
the easiest one without enforcement.

### V2. Type 3 tension axis variety across tasks
**Where:** `task_agent.py` → `_dispatch_task_tool` SUBMIT validation
**What:** Track the tension field/tag axis for each Type 3 task in the run.
On SUBMIT, extract the `field` or `has_tag` from the two competing
P-constraints. If the same axis pair was already used by another Type 3
task in this run → reject with:
"This tension axis ({axis_pair}) already exists in this run. Use a
different pair — see the tension patterns in the Type 3 protocol."
Pass existing tension axes into the agent's context (same pattern as
`_used_venue_ids` for venue diversity).
**Rationale:** All four current Type 3 tasks use `traffic_tier high vs low`.
The protocol lists 5+ alternatives (tag-based, scoped, cross-category)
but nothing enforces variety.

### V3. Minimum P-constraint count
**Where:** `test_generate_tasks.py` → `validate_task_schema`
**What:** Minimum 3 P-constraints per task. For multi-day tasks (days ≥ 3),
minimum 4.
**Rationale:** Current average is 2.9 per task. Some tasks have only 2,
which doesn't create enough constraint interaction.

### V4. Increase hop-2 minimum to 2 for hard tasks
**Where:** `test_generate_tasks.py` → `validate_task_schema`
**What:** Current rule: at least 1 hop-2 P-constraint. New rule: at least
2 hop-2 for tasks with `difficulty == "hard"`.
**Also:** Update hop-2 prompt wording to clarify: hop-2 constraints should
be *hinted at* in the query through natural signals, not claimed explicitly.
The query provides raw material for reasoning — the constraint is what the
agent derives from that material.
Current example in prompt:
  `"anniversary dinner" → upscale restaurant`
Better framing:
  `"celebrating something special together" → upscale evening dinner`
  (The query hints at the occasion without naming the constraint directly.
   The solving agent must infer "special celebration → upscale dinner".)
**Rationale:** hop-1 pass rate is 87%, hop-2 pass rate is 71%. Only 16%
spread. Making hop-2 constraints genuinely require inference (not just
synonym matching) and requiring more of them for hard tasks would increase
the reasoning gap.

### V5. Scope diversity enforcement
**Where:** `test_generate_tasks.py` → `validate_task_schema`
**What:** At least one P-constraint must use a scope other than `"all"` or
`"activity_type=meal"`. Acceptable alternatives: `per_day`, `time_window=*`,
`category=*`, `venue_id=*`, `[list]` compound scope, `has_tag=*`.
**Rationale:** `all` and `activity_type=meal` cover 80%+ of current
constraints. Diverse scopes force more specific reasoning.

### V6. Type 6 time_window grounding requirement
**Where:** `test_generate_tasks.py` → `validate_task_schema` (tighten
existing "type 6 not grounded" check)
**What:** Type 6 tasks must include at least one P-constraint with
`time_window` scope (e.g. `time_window=18:00-23:59` for evening
constraints, `time_window=09:00-12:00` for morning activities during
seasonal events). `venue_id=` alone is insufficient for Type 6 grounding
— it says "visit this venue" without tying the constraint to the
window's temporal character.
**Rationale:** Current Type 6 tasks ground via `venue_id=specific_restaurant`
which is just "must visit X" — not temporal reasoning. The window's
specific timing (Christmas closures, Carnival schedule) should drive at
least one scheduling constraint.

### V7. Type 4 budget sum constraint requirement
**Where:** `task_agent.py` → Type 4 protocol section
**What:** Type 4 tasks must include a `sum` aggregation P-constraint:
```json
{
  "scope": "per_day",
  "condition": {},
  "aggregation": {"sum": "estimated_cost_local", "operator": "<=", "value": <budget>}
}
```
Add to validation: Type 4 must have at least one `sum` aggregation
P-constraint.
**Rationale:** Budget currently lives only in `query_resources.budget_per_day`
as a structural parameter. Making it a P-constraint enables proportional
scoring (agent that overspends by 10% gets partial credit, agent that
overspends by 100% gets none).

### W1. Wrong info density increase to 40%
**Where:** `generate_city_venues.py` → `WRONG_INFO_COUNT`
**What:** Change from `round(n_venues * 0.24)` to `round(n_venues * 0.40)`.
For 50 venues: 12 → 20 wrong-info venues.
Keep restriction: no wrong info on `traffic_tier=high` venues.
Mid-tier venues remain eligible (already are in current code).
**Rationale:** At 24%, models can avoid most traps by checking a few
official sites. At 40%, even thorough models must verify consistently.

### W2. Show wrong-info category to task generation agent
**Where:** `task_agent.py` → `_build_agent_system_prompt` pool listing
**What:** Currently pool listing shows `[WI]` flag (boolean). Change to
show the wrong-info *category* alongside: `[WI:temporal_decay]`,
`[WI:conditional]`, `[WI:propagation_error]`, `[WI:subjective]`.
Do NOT show the affected field or incorrect/correct values.
**Rationale:** Task agent can design personas that naturally interact with
the trap type (e.g. designing a Christmas Eve task knowing a venue has
conditional wrong info) without knowing the specific trap details.

### W3. Conditional wrong-info activation dates ⏸ deferred post-E5
**Where:** `scripts/generation/db.py`, `agent_tools.py`, `pool_utils.py`
**What:** For venues with `wrong_info_category="conditional"`, store and
surface the activation date range: `[WI:conditional, activates Dec 24-27]`.
Task agent can see *when* the conditional wrong info applies so it can
design tasks dated within that range.
**Rationale:** Conditional wrong info is the most powerful trap type
(requires date-aware verification) but requires task-date coordination
to trigger. Currently this coordination is accidental.
**Status:** READ side was written in `pool_utils.py` ahead of schema.
**Hotfix applied:** `pool_utils.load_venue_pool` was querying
`conditional_start_date` / `conditional_end_date` which don't exist in
the schema → crash on any city with `has_wrong_info_planned=1`.
Fixed (Option A): query now only reads `wrong_info_category`;
`wrong_info_activation` returns `[]` until W3 is fully implemented.
See bottom of this file for full W3 implementation spec.

### D1. Minimum source documents per venue
**Where:** `generate_city_venues.py` venue generation pipeline
**What:** Minimum 2 source documents per venue (all venues). Minimum 3
for `traffic_tier=high` venues and for wrong-info venues.
For wrong-info venues: at least one doc contains the wrong info, at least
one contains the social correction (the truth stated naturally in lived
experience — not as an explicit "CORRECTION").
**Rationale:** Wrong-info traps require both an incorrect source and a
discoverable correction. Single-doc venues make traps trivially solvable
(no cross-referencing needed) or unsolvable (correction doesn't exist).

---

## Still Under Discussion

### X1. Item 3 — Budget tradeoff via venue prominence design
The scenario (free museum vs paid museum, wrong-info on the paid one)
is sound for testing allocation reasoning. But it requires coordinating
venue doc prominence with task design at generation time, which may
need pipeline changes. Question: can this be achieved through source doc
generation directives alone, or does it need structural changes to how
venues and tasks are generated?
**Status:** Vic to evaluate pipeline feasibility.

### X2. Type 5 tag diversity vs P diversity
Is Type 5's constraint variety issue already solved by V1 (aggregation
diversity enforcement)? If Type 5 must have ≥3 P-constraints and at
least one non-`at_least`, the `all` universal pool filters + an inclusion
constraint on a different dimension would follow naturally.
**Status:** Likely resolved by V1+V3 together. Monitor after first
regeneration run.

### X3. Tradeoff evaluation standard (P7)
How to score tradeoff quality deterministically? Current ideas:
- Distribution ratio per day (codeable as `per_day` + `ratio`)
- Budget allocation efficiency (codeable as `sum` + B-score bonus)
- Wrong-info trap on the "obvious" venue (codeable as F-score failure)
Question: what constitutes a clear answer for tradeoff quality that
doesn't require LLM judge?
**Status:** Design discussion ongoing.

---

## Implementation Order

**Phase 5.5a — Validation changes (no pipeline changes):**
V1, V2, V3, V4, V5, V6, V7

**Phase 5.5b — Wrong-info scaling (generation pipeline):**
W1, W2 (W3 deferred post-E5 — see W3 section below)

**Phase 5.5c — Source doc requirements (generation pipeline):**
D1

**Phase 5.5d — Regenerate London test set:**
Re-run task generation with all 5.5a+b+c changes active.
Run all 6 models against new task set.
Compare score distributions to Phase 5.1 baseline.

---

---

## P20 — Epistemic Benchmark Redesign (📝 design — decisions pending)
**Subtask doc:** `docs/subtasks/P20_epistemic_benchmark_redesign.md`

**What:** Structural rethink of the evaluation framework following Phase 5
analysis showing P-score spread of only 9pt across all models. Core idea:
strip the system prompt of all hand-holding (source hierarchy, tool mandates,
cross-checking workflow, platform names) and replace the single-task eval with
a round-based learning curve — model passes its own notes forward across chunks
of 10 tasks, accuracy is measured against DB ground truth per chunk.

**Decisions still open (D1–D5):** task format (structured query vs bare
exploration), note persistence model, cost accuracy threshold, chunk size,
and whether to run after 5.5d or in parallel.

**Recommendation:** run 5.5d first. If P-score spread remains <15pt on test_70,
P20 becomes the immediate priority.

**Banked design decisions:**
- Strip: source hierarchy, tool call mandates, seasonal context injection,
  "common trap" example, tool call budget breakdown, platform names on tools
- Keep: transient persistent failures, coverage asymmetry, travel matrix nulls
  (already at 50%), wrong_info semantic layer (already built)
- Skip: field naming inconsistency (artificial), popularity score manipulation
  (not realistic, no discoverable correction)

*Created: May 2026*
*Based on: 6-model benchmark analysis (Sonnet 4.6, GPT-5.4, Gemini 3.1 Pro,
Gemini 3 Flash, DeepSeek Reasoner, DeepSeek Chat) on London test_50 dataset.*

---

## P22 — Evaluator Scoring Redesign (✅ C-score implemented — F-score changes pending)
**Subtask doc:** `docs/subtasks/P22_evaluator_scoring_redesign.md`
**Paused:** meeting prep — resume after

**Summary of agreed changes:**

C-score: only two hard gates remain (no plan, zero tool calls). All other issues
become proportional deductions: −0.04 per format error (unknown tool, missing param,
type error, bad time format, invalid activity_type etc.), −0.8/(days×4) per missing
activity slot (min 4 per day, exempt if task has time_ceiling constraint), −0.1 per
hallucinated venue_id. Delete B2/B3/B4 checks and their corresponding MUST lines
from runner.py. Delete W1/W2/W4/W5/W6 warnings.

F-score: activity time overlap → −0.1 deduction (was hard fail). Cost mismatch
moved from C → F at −0.1. New truth-carrier check: for each wrong-info venue in
plan, −0.05 if agent never found the truth-carrier doc. Fix `tool_log` → 
`tool_call_log` bug in sold-out check.

**Data finding:** 62% of days in current results have <4 activities. Type2/time-ceiling
tasks are legitimately sparse. All other task types are genuinely under-planned —
deduction is appropriate.

*Created: May 2026*
---

## AxA / BxB Full Audit — Bugs Found (May 2026)
**Affects: `generate_task.py`, `test_generate_tasks.py`, `constraint_engine_bxb.py`, `task_agent.py`**

### Previously documented (from P22)
- **Bug 3**: 4b SCOPE_THRESHOLD wrong denominator — uses `site_in_uni` (34) instead of scoped pool size (e.g. 15 museums) for category-scoped constraints
- **Bug 4**: Handbook vs code discrepancy — type3 INCL enforced at 0.60 but prompt says 0.25; type5 at 0.40/0.40/0.20 but prompt says 0.25/0.25/0.10

### New bugs from full audit

**Bug 5 — BxB absolute count check missing in validate_task_schema** (medium)
Design doc requires tension detected only when BOTH:
  `f ≤ threshold` AND `valid_count ≤ 2000 × days`
Code in validate_task_schema only checks the ratio bound. `vc_bxb` is computed and logged in
the detail string but never compared to `2000 * days`. By contrast, `_verify_task_solvable`
does check `vc_bxb < solvability_floor` for type1. For multi-day type3 tasks, the ratio alone
(10%) can pass when the absolute plan count is still in the millions — meaning BxB reports tension
on tasks that are not genuinely constrained.
Fix: add `and vc_bxb <= 2000 * task.get("days", 1)` to the BxB tension condition in `validate_task_schema`.

**Bug 6 — BxB ratio aggregation K_scope computed from m/P instead of slot fraction** (medium)
In `constraint_engine_bxb.py` `_estimate_single`:
```python
scope_frac = m / P  # WRONG: fraction of qualifying venues, not fraction of slots
K_scope = max(1, round(K * scope_frac))
```
`m/P` = fraction of venues qualifying, not fraction of plan slots in scope. For scope="activity_type=meal",
correct K_scope = K_food = days×2 (50% of total slots). Using m/P gives wildly different values.
Concrete: wheelchair ratio=0.6, m=45/P=68 → wrong K_scope=3, f=0.737. Correct K_scope=4, f=0.584.
Conservative direction (overestimates restriction → more false positives in tension detection).
Fix: map scope to group using `_constraint_group` and use the appropriate group K (K_food, K_site, K_total).

**Bug 7 — per_day scope estimated as global in BxB** (medium)
`_constraint_group` returns "universal" for scope="per_day". In `_compute_bxb_joint` the universal
group uses K_total=days×4. But `at_least:N per_day` means N per day, not N across all days.
`_estimate_single` then computes P(at least N in days×4 draws) instead of P(at least N in 4 draws)^days.
For a 3-day trip with `at_least:2 per_day`: wrong=P(2 in 12 draws), correct=P(2 in 4 draws)³.
Fix: detect scope="per_day" in `_estimate_single` and use `_f_at_least_days` with K_per_day=4.

**Bug 8 — validate_task_schema uses raw pool for BxB, _verify uses universal pool** (low-medium)
- `validate_task_schema`: `_compute_bxb_joint(bxb_pcs, pool, ...)` — raw 68-venue pool
- `_verify_task_solvable`: `_compute_bxb_joint(bxb_pcs, universal, ...)` — post-filter pool
If task has wheelchair universal filter (reduces pool to 45), BxB f is computed on different pool
sizes in validation vs solvability — inconsistent tension thresholds. The two should use the same
pool. Fix: in validate_task_schema, build the universal pool before calling BxB.
(Low priority since the discrepancy is usually small unless universal filters are very aggressive.)

**Bug 9 — Prompt says "Type 3: filtered pool must be ≤25 venues" — code explicitly removed this** (HIGH)
task_agent.py handbook (what generation agents read):
  `"Type 3: filtered pool must be ≤25 venues"`
`_verify_task_solvable` actual step 2:
  `# ── 2. Pool-size-max: only type 5`
  `# Type 3 does NOT have a pool-size-max. Its difficulty comes from competing`
  `# inclusion constraints (B×B tension f ≤ 10%), not from narrow pools.`
`_verify_task_solvable` docstring also still says "A6: pool-size-max for type 3 only" — outdated.
Agents are spending many SUBMIT attempts narrowing the type3 pool to ≤25 venues, which the
validator never actually checks. This is a direct cause of the 20+ SUBMIT attempt failures
documented in the P9 design doc. Fix: delete "Type 3: filtered pool must be ≤25 venues" from
prompt; update _verify_task_solvable docstring step 2 to say "pool-size-max: type 5 only".

**Bug 10 — Prompt says BxB threshold ≤5% for type3; code uses ≤10%** (HIGH, same category as Bug 4)
Prompt: `"Two counting constraints create type 3 tension when their combined valid-plan fraction is ≤5%"`
Code: `bxb_threshold = 0.10 if is_type3 else 0.05`
Agents trying to reach ≤5% constraint product when the validator passes at ≤10%. Half the work is
wasted fighting a threshold that doesn't exist. Fix: update prompt to say ≤10% for type3, ≤5% for type5.

**Bug 11 — Prompt description of AxA overlap is imprecise** (low)
Prompt: `"two constraints with ≤25% venue-set overlap"`
Code: `overlap <= 3 OR overlap/smaller <= 0.25`
The prompt omits the absolute fallback (≤3 venues regardless of ratio). A pair with sets of size 5
and 5 sharing 2 venues has ratio 2/5=40% > 25%, but absolute ≤ 3 → tension IS detected. Agent
wouldn't know this from the prompt. Not a critical bug (conservative direction — agents think it's
harder than it is) but adds unnecessary uncertainty.
Fix: update prompt to describe both conditions: "overlap ≤3 venues OR overlap/smaller_set ≤25%".

### Severity ranking

| Bug | Severity | Direct cause of failures | Fix complexity |
|-----|----------|--------------------------|----------------|
| 9 — type3 pool-size prompt | HIGH | Yes — 20+ SUBMIT attempts | Prompt edit only |
| 10 — BxB 5% vs 10% prompt | HIGH | Yes — forces over-narrowing | Prompt edit only |
| 3 — 4b denominator | HIGH | Yes — over-permissive scope bars | Code fix in generate_task.py |
| 4 — handbook vs code values | HIGH | Yes — wrong thresholds for type3/5 | Prompt edit only |
| 6 — ratio K_scope wrong | MEDIUM | Possible false positives | Code fix in bxb.py |
| 7 — per_day global not daily | MEDIUM | Wrong f estimates | Code fix in bxb.py |
| 5 — abs count check missing | MEDIUM | Multi-day BxB false positives | Code fix in validate |
| 8 — pool inconsistency | LOW | Small effect, usually minor | Code fix in validate |
| 11 — AxA prompt imprecise | LOW | Confusion only, not failures | Prompt edit only |

Bugs 9 and 10 are pure prompt fixes — no code change. Do them first. They directly explain
why type3 generation had 22+ failed SUBMIT attempts per task in the pre-P9 run.

*Added: May 2026*

---

## P22-F — F-Score Redesign (🔄 implementing)
**Affects: `eval/evaluator.py`, `agents/runner.py`, `scripts/generation/db.py`**

### Design decisions locked

**Structure:** 4 sections, deduction-based scoring, `max(0.0, 1.0 − Σ deductions)`.
No hard fails. No cap on deductions. Weather sensitivity moved to B-score (future).
Physical persona constraints (wheelchair, age, pet) remain in P-score for now —
documented as design debt in DESIGN_DECISIONS.md.

**F1 — Temporal structure**
- F1a: Activity overlap → −0.20 per overlapping pair
- F1b+F3a merged: Travel time infeasible (explicit transport duration OR implicit gap
  < matrix travel time for stated/walking mode) → −0.15 per pair

**F2 — Venue access**
- F2a: Opening hours violation → −0.15 per venue
- F2b: Ticket sold out → −0.15 per venue (double-penalty deleted — was a dead bug)
- F2c: Truth-carrier not retrieved (wrong-info venue, agent never got correction doc)
  → −0.05 per venue. Loaded via Option A: extend load_ground_truth to return
  `truth_carriers: {venue_id: [doc_id, ...]}` from doc_venue_roles table.
- F2d: Event capacity sold out (weekend_evening_sold_out active event) → −0.15

**F3 — Logistics**
- F3b: Travel buffer tiers (gap − travel, for implicit travel pairs only):
  < 5 min → −0.10 | 5–12 min → −0.06 | 12–15 min → −0.03
  15–30 min → no deduction (safe zone) | > 30 min → −0.02

**F4 — Scheduling quality**
- F4a: Visit duration — under-scheduled (<50% of recommended) → −0.05 per venue
       over-scheduled (>rec + max(60, rec×50%)) → −0.02 per venue

**Return dict:** `{score, has_critical_issues, deductions}`.
`has_critical_issues = any(d["amount"] >= 0.10 for d in deductions)`.

**Taxi mode:**
- Added "taxi" to VALID_TRANSPORT_MODES (C-score already done)
- For F-score time check: taxi_minutes derived as transit_minutes × 0.85
- DB schema: add `taxi_minutes REAL` to travel_matrix for future city generation
- city_config: add `transit_flat_fare_local`, `taxi_base_fare_local`,
  `taxi_per_km_local` for transport cost display (city planning step, not F-score)

**runner.py:**
- Add "taxi" to modes list
- Delete: "If the gap... less than 30 minutes, add a rest/leisure stop" sentence
  (agents should fold buffer into the activity or transport block naturally)

**Overnight gap fix:** add 1440 when `gap < 0` in buffer calculation.

**Composite deletion:** compute_composite(), F_WEIGHT, P_WEIGHT deleted.
  B_WEIGHT kept (B-score unchanged). Test section [5] in test_b1.py deleted.
  run_benchmark.py summary table "Final" column removed.


---

## CAT-A — Pool Satisfiability Check Aggregation Bugs (✅ implementing)
**Affects: `scripts/generation/constraint_engine.py` → `check_constraint_pool_satisfiability()`**
**Related: P22 evaluator redesign bug section (extends Bugs 1–3 with Bugs 4–6)**

### Root cause (design flaw)

`check_constraint_pool_satisfiability` runs `_evaluate_generic_constraint` on a synthetic
"plan" where every pool venue is one activity, then checks `score == 1.0`. This conflates:
- "Does the pool AS A WHOLE satisfy the constraint?" (what the check does)
- "Can a valid plan satisfying this constraint be built from this pool?" (what it should ask)

These are the same question only for `{at_least: N}` and `count_distinct`. For all other
aggregations they diverge, producing false rejections.

### Bug inventory

| # | Aggregation | Behaviour | Correct behaviour |
|---|-------------|-----------|-------------------|
| 1 (known) | `"all"` | Fails if any pool venue doesn't qualify | Skip — `_verify_task_solvable` lower bars handle existence |
| 2 (known) | `"none"` | Fails if any pool venue has the property | Skip — agent will simply avoid those venues |
| 3 (known) | `{at_most: N}` | Fails if pool has >N qualifying venues | Skip — plan-level ceiling, not a pool filter |
| 4 (new) | `{at_most_distinct: N}` | Fails if pool has >N distinct values | Skip — same design as Bug 3 |
| 5 (new) | `{ratio: R}` | Fails if pool ratio < R | Skip — agent selects from pool; ratio on plan ≠ ratio on pool |
| 6 (new) | `{exactly: N}` | Fails if pool doesn't have exactly N qualifying venues | Skip — plan-level count |

### Fix

In `check_constraint_pool_satisfiability`, add early-exit guards before running the engine:

```python
# agg="all" / "none": skip — existence verified by solvability lower bars
if agg in ("all", "none"):
    return True, "agg=all/none — pool check skipped (existence via solvability lower bars)"

# Plan-level ceilings/ratios/exact counts: skip entirely
if isinstance(agg, dict) and any(k in agg for k in
        ("at_most", "at_most_distinct", "ratio", "exactly")):
    return True, f"plan-level aggregation ({list(agg.keys())[0]}) — pool check skipped"

# Only {at_least: N} and {count_distinct: N} proceed to pool-level existence check
```

### Additional changes

- **constraint_engine.py:** Update docstring and error message to reflect which aggregations run pool check
- **task_agent.py handbook:** Clarify SOLVABILITY CHECKS section — which aggregations skip pool check
- **test_e3.py [13]:** Add coverage for all 6 aggregation types
- **P22 doc:** Extend bug section to document Bugs 4–6


---

## CAT-B — Counting Constraints Missing from Solvability Pipeline (✅ implementing)
**Affects: `scripts/generation/generate_task.py` → `_build_scope_pools()`**

### Root cause

`_build_scope_pools` sorts P-constraints into three buckets:
- `universal`:  `agg in ("all", "none")` — filters the whole pool
- `scoped`:     `agg == "all"` with non-trivial scope
- `inclusion`:  `isinstance(agg, dict) and "at_least" in agg` only

Aggregations `ratio` and `count_distinct` fall into **no bucket** and receive
no solvability check. Tasks using these can be published even if the pool
cannot satisfy them.

### Bug inventory

| # | Aggregation | Gap | Consequence |
|---|-------------|-----|-------------|
| 7 | `{count_distinct: N, field: F}` | Not in any pool bucket | Pool may have <N distinct values of F; unsolvable task published |
| 8 | `{ratio: R}` | Not in any pool bucket | Pool may have 0 qualifying venues; agent builds plan with 0% ratio |

### Fix

Extend the `inclusion_pools` loop in `_build_scope_pools` to handle both:

**ratio**: Add `(matched, 1)` — existence check only (at least 1 qualifying venue).
Pool ratio ≠ plan ratio (addressed in Cat-A), but zero qualifying venues IS a
hard solvability failure.

**count_distinct: N, field: F**: Build `distinct_reps` — one representative venue
per distinct value of F across qualifying venues. Add `(distinct_reps, N)`.
The existing inclusion lower bar (`len(ivenues) < min_n`) then correctly catches
cases where fewer than N distinct values exist.

Error message from lower bar reads "X qualifying venues, needs N" which is
slightly imprecise for count_distinct (X = distinct values, not venue count) but
directionally correct — no schema changes to lower bar logic needed.

### Additional changes

- **test_e3.py**: Add section [13+] tests for ratio and count_distinct unsolvable cases


---

## CAT-B — Counting Constraints Not Tracked in Solvability Pipeline (✅ implementing)
**Affects: `scripts/generation/generate_task.py` → `_build_scope_pools()`**

### Analysis (post-Cat-A)

After Cat-A fix, `check_constraint_pool_satisfiability` routes:
- `at_least`, `count_distinct` → pool existence check (runs correctly)
- `all`, `none`, `at_most`, `at_most_distinct`, `ratio`, `exactly` → skipped

`_build_scope_pools` / `_verify_task_solvable` only tracks `at_least` in `inclusion_pools`.
All other counting aggregations fall through unverified.

**Bug 7 (count_distinct): Already resolved by Cat-A.**
`check_constraint_pool_satisfiability` still runs the pool check for count_distinct.
Example: `{count_distinct: 3, field: "district"}` with 2 pool districts → fails correctly.
No additional fix needed in _build_scope_pools.

**Bug 8 (ratio): Active gap — no existence check.**
`ratio` skips check_constraint_pool_satisfiability (Cat-A fix) AND is not in inclusion_pools.
Result: a task with `{ratio: 0.6, scope: meal, condition: has_tag=vegan}` and 0 vegan
venues in the pool passes all validation. Constraint is completely unsolvable.

**at_least_days: documented limitation, not implemented.**
Rare aggregation; complex to validate without knowing per-day plan structure.

### Fix

In `_build_scope_pools`, add ratio constraints to `inclusion_pools` with `min_count=1`:
- Catches the 0-qualifying-venue case (complete unsolvability)
- Conservative: doesn't estimate required count from ratio × plan_size (unknown at generation time)
- Documents that ratio ≥ 1 qualifying venue is a floor, not a full solvability guarantee


---

## CAT-C — Schema Validation Silently Accepts Missing/Invalid Values (✅ implementing)
**Affects: `test_generate_tasks.py` → `validate_task_schema()`**

### Bug inventory

| # | Field | Old check | Problem | Fix |
|---|-------|-----------|---------|-----|
| 9  | `score_tier` | `not in ("P", None)` | `None` is explicitly allowed — missing field passes | `!= "P"` |
| 10 | `hop`        | `not in (1, 2, None)` | Same — missing field passes | `not in (1, 2)` |
| 11 | `check_method` | value never validated | Any string passes (e.g. `"magic"`) | must be `"code"` or `"llm"` |

All three are in `validate_task_schema` inside the per-constraint loop.
The required-fields check above them catches missing keys — but only as a separate
error, and only if the field is listed. These three value checks were too lenient
and let `None` / arbitrary strings through.

### Fix (one block)
```python
if c.get("score_tier") != "P":
    issues.append(f"[{cid}] score_tier must be 'P', got {c.get('score_tier')!r}")
if c.get("hop") not in (1, 2):
    issues.append(f"[{cid}] hop must be 1 or 2, got {c.get('hop')!r}")
if c.get("check_method") not in ("code", "llm"):
    issues.append(f"[{cid}] check_method must be 'code' or 'llm', got {c.get('check_method')!r}")
```

### Additional changes
- **test_e3.py**: Add section covering all 3 bugs (null values, wrong values, valid values)


---

## W3 — Conditional Wrong-Info Activation Dates (⏸ deferred — post E5)
**Affects: `scripts/generation/db.py`, `scripts/generation/agent_tools.py`, `scripts/generation/pool_utils.py`**

### Context

`wrong_info_category = "conditional"` already exists and works — it flags wrong info
that is only misleading during certain date ranges (e.g. expired summer pricing,
bank-holiday closure hours).

W3 was the next step: store *when* the activation window is so the task agent can see
`[activates:2026-06-01–2026-08-31]` in the venue pool and craft tasks that exploit the
seasonal timing.

The READ side was written (pool_utils.py query + task_agent.py annotation) but the
WRITE side was never added. `conditional_start_date` / `conditional_end_date` were
queried but never exist in the schema → crash in `load_venue_pool`.

### Option A fix applied (unblocks E5)

`pool_utils.py` now queries only `wrong_info_category` (which exists).
`wrong_info_activation` is always `[]` until W3 is completed.

### To complete W3

1. **`db.py`** — add to `wrong_info` table:
   ```sql
   conditional_start_date TEXT,   -- ISO date, NULL unless category='conditional'
   conditional_end_date   TEXT
   ```
   Add a `db_migrate_add_conditional_dates()` helper for existing DBs.

2. **`agent_tools.py`** — update `ADD_WRONG_INFO` to accept and write
   `conditional_start_date` / `conditional_end_date` when
   `wrong_info_category = "conditional"`.

3. **`handbook.py`** — add guidance: for `conditional` category, provide
   the date range during which the wrong info is active.

4. **`pool_utils.py`** — restore the full query (already written, just commented
   out in spirit; re-enable the date columns + activation_windows loop).

5. **Tests** — cover the full round-trip: INSERT with dates → load_venue_pool →
   activation field populated.

