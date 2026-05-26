# P9 — Type 3 tension logic redesign (small-overlap, not narrowing-threshold)
**Status: 📝 design drafted, awaiting implementation**
**Blocks: nothing**
**Depends on: nothing (can land independently of P10/P11)**

---

## The current problem

Type 3 validation (the "no constraint tension detected" rejection) is the
single biggest false-positive in benchmark generation. In the last full run:
- Claude-sonnet-4-5: 2/4 type3 success (50% — all 2 failures were this check)
- Deepseek-reasoner: 1/4 type3 (25%)
- Gemini: 0/4 type3 (100% failure, mix of this + API errors)
- gpt-5.4: 0/4 type3 (100% — all API errors driven by context bloat from
  repeatedly trying to pass the tension check)

Across these models, we observed **22 distinct SUBMIT attempts** per type 3
failure case, all with real, English-legitimate "competing requirements"
pairs, every single one rejected by the validator:
- `regulation_required:wheelchair + label_required:outdoor`
- `label_excluded:tourist_spot + label_required:iconic`
- `price_tier_required:upscale + label_required:hidden-gem`
- `noise_level_max:moderate + category_count_minimum:cafe`
- `hidden_gem_required + category_count_minimum:museum`
- `regulation_required:wheelchair + label_required:hidden-gem`

Every one of these is a legitimate type 3 tension in everyday terms, and
every one was rejected.

## Root cause — the validator uses the wrong metric

Looking at `test_generate_tasks.py:1725-1732`:
```python
weaker    = min(len(set_i), len(set_j))
intersect = len(set_i & set_j)
extra_narrowing = (weaker - intersect) / n_pool
if extra_narrowing >= 0.12:
    tension_found = True
```

This measures **intersection shrinkage vs. the whole pool** — i.e. "how much
smaller is the intersection than the weaker set, as a fraction of the pool?"

That's **Type 5 logic**, not Type 3 logic. Type 5 = "both filters compound to
a narrower set." Type 3 = "the two sets point at *different* venues — the
agent must interleave, not stack."

The semantic for type 3 is the opposite:
- Set A = all venues satisfying requirement A (substantial subset of pool)
- Set B = all venues satisfying requirement B (substantial subset of pool)
- **Small overlap between A and B** = real tension (agent must pick some
  from each separate group, can't satisfy both with the same venue)

## Proposed fix — small-overlap metric

Replace the `extra_narrowing` check (both-filter case, lines 1723-1732) with:

```python
# Type 3 tension: two requirements point at mostly *different* venues.
# - Both sets must be substantial (≥ MIN_SIDE_COUNT venues on each side)
# - Their overlap must be small (≤ MAX_OVERLAP venues OR ≤ MAX_OVERLAP_RATIO of the smaller set)
MIN_SIDE_COUNT     = 3        # each side needs real presence in pool
MAX_OVERLAP_ABS    = 3        # absolute: intersection cap
MAX_OVERLAP_RATIO  = 0.25     # OR relative: intersection ≤ 25% of smaller set

if len(set_i) >= MIN_SIDE_COUNT and len(set_j) >= MIN_SIDE_COUNT:
    overlap = len(set_i & set_j)
    smaller = min(len(set_i), len(set_j))
    if overlap <= MAX_OVERLAP_ABS or (overlap / smaller) <= MAX_OVERLAP_RATIO:
        tension_found = True
        tension_detail.append(
            f"{pat_i}+{pat_j} (type3: {len(set_i)}|{len(set_j)}, overlap={overlap})"
        )
```

**Why both thresholds (absolute AND ratio):**
- Absolute cap handles small sets: if set A has 5 venues and set B has 5
  venues and they share 2 — that's genuine tension (ratio is 40% but the
  agent still has to pick from different halves)
- Ratio cap handles large sets: if set A has 30 venues and set B has 25
  and they share 10 — the overlap is "large" in absolute terms but the
  agent still has 20+ venues on each side that DON'T overlap, so it's
  tension. (Ratio 10/25 = 40%, still above threshold — not tension.)

## Retire `_SEMANTIC_TENSION_PAIRS`

The hardcoded whitelist is a design smell — it tried to enumerate "known good
tension pairs" but misses the general case AND has no principle for why those
specific pairs. Replace with the plan-space-narrowing-product metric for B×B
(see below) and the small-overlap metric for A×A.

## Recategorize A vs B (closed review before implementation)

After discussion, the final classification is:

**Category A (pool filters — have venue sets, narrow `_apply_pool_filters` pool):**
- `label_required`, `label_excluded`
- `regulation_required`
- `hidden_gem_required`
- `noise_level_max`
- `price_tier_required`
- `pace_relaxed`           ← promoted from Category B
- `max_visit_duration`     ← promoted from Category B

Both `pace_relaxed` and `max_visit_duration` match discrete per-venue fields
(`recommended_pace`, `recommended_visit_minutes`). They currently lack pool-filter
branches in `_apply_pool_filters`; this must be added as part of P9 so solvability
checks reflect these filters narrowing the pool.

**Category B (plan-level patterns — no venue sets, constrain schedule structure):**
- `category_count_minimum`        (K of N slots to category C)
- `cuisine_diversity_minimum`     (≥ K distinct cuisines across meal slots)
- `local_cuisine_preference`      (X% of meals local)
- `district_count_max`            (venues within K districts)
- `numeric_aggregate`             (sum of field under/over threshold — usually cost/day)
- `time_threshold`                (schedule timing window — `time_start >= T`, `time_end <= T`)

Plus `required_venue_ids` on the rubric (not a pattern, but a plan-level
narrowing axis — forces specific venues into the plan).

## Drop A×B tension branch entirely

Current code has three branches: A×A (both have venue sets), A×B (one has
a venue set, one doesn't), B×B (both plan-level, whitelist lookup). The
A×B branch only tests whether the A side narrows ≥ 12%, which the B side
doesn't factor into at all — it's a rubber-stamp pass.

**A×B pairs are not genuinely tension — they're compounding** (Type 1
territory). Delete the A×B branch. If an agent pairs an A pattern with a
B pattern for Type 3, the task won't pass the tension check and the agent
will receive a clear error.

## Proposed fix — small-overlap metric (A×A case)

Replace the `extra_narrowing` check (both-filter case, lines 1723-1732) with:

```python
# Type 3 tension: two requirements point at mostly *different* venues.
# - Both sets must be substantial (≥ MIN_SIDE_COUNT venues on each side)
# - Their overlap must be small (≤ MAX_OVERLAP venues OR ≤ MAX_OVERLAP_RATIO of the smaller set)
MIN_SIDE_COUNT     = 3        # each side needs real presence in pool
MAX_OVERLAP_ABS    = 3        # absolute: intersection cap
MAX_OVERLAP_RATIO  = 0.25     # OR relative: intersection ≤ 25% of smaller set

if len(set_i) >= MIN_SIDE_COUNT and len(set_j) >= MIN_SIDE_COUNT:
    overlap = len(set_i & set_j)
    smaller = min(len(set_i), len(set_j))
    if overlap <= MAX_OVERLAP_ABS or (overlap / smaller) <= MAX_OVERLAP_RATIO:
        tension_found = True
        tension_detail.append(
            f"{pat_i}+{pat_j} (type3: {len(set_i)}|{len(set_j)}, overlap={overlap})"
        )
```

**Why both thresholds (absolute AND ratio):**
- Absolute cap handles small sets: if set A has 5 venues and set B has 5
  venues and they share 2 — that's genuine tension (ratio is 40% but the
  agent still has to pick from different halves)
- Ratio cap handles large sets: if set A has 30 venues and set B has 25
  and they share 10 — the overlap is "large" in absolute terms but the
  agent still has 20+ venues on each side that DON'T overlap, so it's
  tension. (Ratio 10/25 = 40%, still above threshold — not tension.)

## Proposed fix — plan-space-narrowing-product metric (B×B case)

Each B pattern narrows the baseline plan space by a multiplicative factor.
Two B patterns together narrow by (roughly) the product of their factors.
Tension exists when the stacked narrowing collapses the plan space below
a threshold.

```python
BXB_TENSION_THRESHOLD = 0.05  # surviving plans ≤ 5% of baseline = tension

def _plan_space_narrowing(pc, pool, schedule_shape):
    """Return fraction of baseline plans still valid under this constraint alone.
    Returns 1.0 for non-constraining, 0.0 for eliminates-all.
    Schedule shape: (days, meals_per_day=2, sites_per_day=2) default."""
    # per-pattern math:
    # - category_venue_count(pool_filter=F, op, value): fraction of plans where the
    #   count of F-matching activities satisfies the operator against value.
    #   Computed from pool fraction of F and schedule slot count.
    # - district_count_max(K): fraction = C(D,K)/C(D,used_districts_if_unconstrained)
    # - cuisine_diversity_minimum(K): 1 - P(all-same-cuisine pairings)
    # - local_cuisine_preference(ratio): (local/total)^(ratio * meals_per_day * days)
    # - indoor_outdoor_balance(tag, ratio): multiplicative from tag pool fraction
    # - numeric_aggregate(cost/day ≤ X): fraction estimated from cost distribution
    # - numeric_aggregate(travel_minutes/day ≤ X): same, from travel-time distribution
    # - time_threshold: fraction of plans where schedule fits the window
    ...

BXB_TENSION_RATIO_MAX        = 0.05   # surviving plans ≤ 5% of baseline
BXB_TENSION_ABS_MAX_PER_DAY  = 2000   # AND surviving plans ≤ 2000 × days

def _check_bxb_tension(pc_i, pc_j, pool, days, schedule_shape):
    r_i = _plan_space_narrowing(pc_i, pool, schedule_shape)
    r_j = _plan_space_narrowing(pc_j, pool, schedule_shape)
    baseline = _compute_baseline(days, schedule_shape, pool)
    # Independence product is a conservative upper bound (correlated constraints
    # narrow more, never less). No false positives from this approximation.
    stacked_ratio = r_i * r_j
    surviving = baseline * stacked_ratio
    abs_cap = BXB_TENSION_ABS_MAX_PER_DAY * days
    # Tension requires BOTH: relative narrowing AND absolute small plan space.
    return (stacked_ratio <= BXB_TENSION_RATIO_MAX) and (surviving <= abs_cap)
```

**Why this is the right approach:**
- **Principled, no hardcoded whitelist.** Adding a new B pattern requires
  writing one narrowing function; no whitelist entries needed.
- **Parameter-sensitive.** `category_count_minimum:1` barely narrows;
  `category_count_minimum:4` collapses. Same pattern pair (category_count × district)
  creates tension or not depending on the specific K values.
- **Pool-aware.** Same constraint pair can be tension on one city's pool
  and not on another. Which is correct — real benchmarks should reflect pool composition.
- **Consistent with A×A in spirit.** Both branches detect "collapse after
  stacking below threshold." Different math, same philosophy.
- **Conservative bound.** Independence product overestimates surviving plans
  (correlated constraints narrow more), so no false positives on tension detection.

**Threshold calibration — 5% ratio + 2000/day absolute floor:**

The ratio threshold alone is insufficient for multi-day trips because baseline
plan space grows multiplicatively. For a 1-day trip, 5% of 170k = 8,500
plans — a meaningful squeeze. For a 3-day trip, 5% of 5×10^15 = 2.5×10^14 —
still astronomical. Need both:

1. **Ratio bound (5%)** — surviving plans / baseline plans ≤ 0.05. Handles
   1-day tension cleanly and gives a constant-factor notion of "narrowing."
2. **Absolute bound (2000 × days)** — surviving plans ≤ 2000 × days. Handles
   multi-day cases where the ratio alone accepts too many plans.

Tested against 45 candidate B×B pairs across the 10 B patterns on a typical London pool:

| Bounds | 1-day pairs accepted | 3-day pairs accepted | Notes |
|---|---|---|---|
| ratio 1% only | 18/45 | 40/45 | Too strict on 1-day, absurd on 3-day |
| **ratio 5% + abs 2000/day** | **26/45** | **18/45** | **Tight across all day counts** |
| ratio 5% only | 26/45 | 37/45 | Loose on multi-day |
| ratio 10% only | 31/45 | 41/45 | Way too permissive |
| ratio 20% only | 36/45 | 43/45 | Defeats the point |

**Why 2000/day:**
- A "real tension" task should leave the agent with a genuinely small feasible plan set — hundreds to low thousands of plans, not millions.
- 2000/day ≈ 2000 combinations for a single day's 2r+2s slots, which corresponds to ~45 venue picks per slot × some ordering — enough choice to avoid triviality but squeezed enough to require real tradeoff navigation.
- Per-day scaling reflects multiplicative plan-space growth: multi-day plans should have proportionally more valid combinations.

Both bounds are named constants (`BXB_TENSION_RATIO_MAX = 0.05`, `BXB_TENSION_ABS_MAX_PER_DAY = 2000`) — tunable from one place if calibration shifts after seeing real tasks.

With the independence assumption being a conservative upper bound (correlated constraints narrow MORE than the product), false negatives (missing real tensions via correlation) are more common than false positives. The dual bound protects against both: ratio catches under-narrowed pairs, absolute catches multi-day inflation.

## Test additions for E3

Add a `[6e]` block with these cases:
- `wheelchair_required + outdoor_label` on a pool where 5 venues are
  wheelchair + 4 are outdoor and only 1 overlaps → should detect tension
- `iconic + upscale` on a pool where most upscale venues are iconic
  (high overlap) → should NOT detect tension
- `hidden-gem + category:museum` with 8 hidden gems, 6 museums, 1 overlap
  → should detect tension
- Two plan-level patterns that happen to be in the old whitelist pair
  (e.g. `pace_relaxed + cuisine_diversity_minimum`) → should still detect
  tension via the plan-level fallback

## Edge cases

- If a P-constraint has `params` that can't be resolved to a venue set
  (unknown pattern, malformed params), skip the pair silently — same as
  current behaviour
- If the agent has only ONE P-constraint, tension requirement doesn't fire
  (same as current)
- The per-type exemption (type 1/2/4/6 exempt) stays as-is — this fix
  affects HOW tension is detected for types 3/5, not WHETHER it's required

---

# P10 — Type 5 solvability redesign (viable-schedule, not per-category minimum)
**Status: 📝 design drafted, awaiting implementation**
**Blocks: nothing**
**Depends on: nothing**

---

## The current problem

Type 5 is defined as:
> "The combination of constraints eliminates most of the venue pool, leaving
> only a narrow viable set — potentially 0-3 venues. The difficulty isn't
> optimising among many options but discovering which options even exist."

But the validator rejects Type 5 tasks for producing **exactly** that
narrow viable set. Claude made 8 consecutive Type 5 SUBMIT attempts with
`regulation_required` stacks that correctly narrowed the pool to 3-5
venues; every single one was rejected with variants of:
- "Pool gap: only 0/2 museum venues after filters"
- "Pool gap: only 0/1 restaurant venues after filters"
- "Constraints eliminate all venues from pool"

## Root cause — wrong solvability metric

Current Type 5 pipeline in `_verify_task_solvable`:
1. Apply all filters → `filtered` (correct, produces the narrow pool)
2. If `filtered == []` → reject "Constraints eliminate all venues from pool"
   → this IS correct for truly zero-venue intersection, but only barely
3. For each `category_count_minimum` P-constraint, verify `filtered` has
   ≥ `min_count` venues of that exact category → **this is where Type 5
   dies**

Step 3's per-category minimum is a Type 1/4/6 heuristic applied uniformly.
Type 5 needs a different solvability criterion: **can the agent assemble
at least one valid daily schedule from the narrow filtered pool?**

## Proposed fix — viable-schedule check for Type 5

For Type 5 specifically, replace the per-category minimum with a
"minimum-viable-schedule" check:

```python
is_type5 = "type5" in structural_type
if is_type5:
    # Type 5 exempts the per-category check — instead verify a schedule exists
    # Minimum viable schedule per day: 2 restaurants + 1 site
    SITE_CATS = {"museum", "attraction", "park", "neighbourhood"}
    MIN_RESTAURANTS_PER_DAY = 2
    MIN_SITES_PER_DAY       = 1

    days = task.get("days", 1)
    rest_count = sum(1 for v in filtered if v.get("category") == "restaurant")
    site_count = sum(1 for v in filtered if v.get("category") in SITE_CATS)
    need_r = MIN_RESTAURANTS_PER_DAY * days
    need_s = MIN_SITES_PER_DAY * days

    if rest_count < need_r:
        return False, (
            f"Type 5 schedule infeasible: need ≥{need_r} restaurants across "
            f"{days} day(s), filtered pool has {rest_count}. "
            f"Relax one filter or reduce days."
        )
    if site_count < need_s:
        return False, (
            f"Type 5 schedule infeasible: need ≥{need_s} sites (museum/attraction/park/"
            f"neighbourhood), filtered pool has {site_count}. Relax one filter."
        )
    # Skip the per-category_count_minimum check entirely for type 5 —
    # filter narrowness IS the task
else:
    # existing per-category_count_minimum check
    ...
```

## Also exempt Type 5 from the pool-size-max (POOL_SIZE_MAX = 25) check

Currently type 5 is NOT in the exempt list for this check. Add it. Type 5's
explicit purpose is "narrowness IS the task" — there's no such thing as a
type 5 task with a 26+ filtered pool. That would be a misclassified Type 1.

Move the check from "applies to type3, type5" to "applies to type3 only."
For type 3, the pool should narrow via tension; for type 5, the pool should
narrow via stacking — both narrow, but type 5's narrow is EXTREME (intended
1-5 venues) and would trivially pass ">25" but that's never what fails it.

## Test additions for E3

Add cases for the new Type 5 logic:
- Filtered pool with 4 venues (2 restaurants + 2 sites) for a 1-day trip
  → passes (need 2r+1s, has 2r+2s)
- Filtered pool with 3 venues (1 restaurant + 2 sites) for a 1-day trip
  → fails "need 2 restaurants, have 1"
- Filtered pool with 4 venues (4 restaurants, 0 sites) for a 1-day trip
  → fails "need 1 site"
- Filtered pool with 0 venues → still fails "eliminate all venues" (from step 1)
- Type 5 with 30-venue filtered pool → passes new viable-schedule check
  AND no longer hits the pool-size-max check (which is now type-3-only)

---

# P11 — B-score generation pipeline overhaul
**Status: 📝 design drafted, awaiting implementation**
**Blocks: nothing**
**Depends on: nothing**

---

## The problem: zero B-score constraints across 60 real tasks

Not a single task in our 60-task SOTA run included a `b_score_constraint`.
Every model (claude, deepseek-reasoner, gemini, gpt-5.4, gpt-5.4-mini)
left `b_score_constraints: []`. The field is entirely unused.

This is a benchmark gap: B-score constraints are supposed to encode the
"quality of solution" dimension — not "did the plan satisfy hard rules"
but "did the plan handle the constraint *well*?" Without B-scores, every
plan that passes validation scores identically; there's no way to
differentiate a mediocre plan from a thoughtful one.

## Three contributing root causes

### Cause 1 — "OPTIONAL" escape hatch in the prompt

In `scripts/generation/task_agent.py:1088`:
```
B-score constraints are OPTIONAL. If unsure, leave b_score_constraints as [].
```

Every model, correctly reading instructions, takes this out. The line was
added defensively to prevent agents from producing malformed B-score JSON,
but it's too permissive — models ALWAYS default to empty list.

### Cause 2 — Only `python_script` format is documented

The current B-score schema example shows only:
```
"pattern":      "python_script",
"check_method": "python_script",
"params":       {"script_code": "def evaluate(plan, task, venues):\n    ..."}
```

This is intimidating: the model has to write working Python inside a JSON
string, escape all newlines as `\n`, and get the `evaluate(plan, task, venues)`
signature exact. When models TRY to comply (Gemini did on one type 3 task),
they hit the JSON size limit and get the task_json truncated mid-`script_code`,
leading to "[bsc_001] python_script missing 'script_code'" errors.

But the codebase supports OTHER B-score check methods:
- `llm_semantic` — prose description, evaluated by judge LLM at scoring time
- `doc_appeared` — used by type 6 (did the agent consult the right source)
- Generic pattern-based checks already used for P-score

None of these alternatives are presented to the model.

### Cause 3 — No examples tied to the structural types

The prompt describes B-score shapes abstractly in the structural types doc
but the agent prompt doesn't surface them. A good type 3 B-score might be
"did the schedule alternate between the two tensions rather than clustering
them all at the end?" — but the model has no idea that's the kind of thing
to write.

## Proposed fixes

### Fix 1: Replace the "OPTIONAL" escape hatch

Change:
```
B-score constraints are OPTIONAL. If unsure, leave b_score_constraints as [].
```

To:
```
B-score constraints capture the QUALITY dimension of a good plan —
"did the agent handle this well?" not "did the agent satisfy the minimum?"

REQUIREMENT BY TYPE:
  - Type 1 (cascading): 1 B-score is recommended, capturing the deepest hop-3 signal
  - Type 2 (time ceiling): 1 B-score recommended — did the schedule use time efficiently?
  - Type 3 (competing): 1 B-score REQUIRED — did the schedule alternate/balance the two sides?
  - Type 4 (budget allocation): 1 B-score REQUIRED — did the agent shift budget toward highest-value use?
  - Type 5 (hard feasibility): 0-1 B-score — optional since narrowness is the point
  - Type 6 (context window): 1 B-score REQUIRED — did the agent actually verify the
    context issue (use get_official_site with the anchor date)?

If the task has no natural quality dimension, leave b_score_constraints as [] — but
this should be the exception, not the default.
```

### Fix 2: Add `llm_semantic` as the preferred simple format

Add a second B-score example in the agent handbook showing the MUCH simpler
`llm_semantic` format:

```
EXAMPLE B-SCORE (llm_semantic — preferred when quality is judgment-based):
{
  "id":                "bsc_001",
  "score_tier":        "B",
  "hop":               3,
  "pattern":           "llm_semantic",
  "check_method":      "llm_semantic",
  "source_in_profile": "<verbatim phrase from query>",
  "description":       "Does the plan alternate between hidden-gem and iconic venues across the day, rather than clustering one type and then the other?",
  "params":            {}
}
```

No Python escaping, no evaluate() signature. The description is the prompt
the judge LLM uses at scoring time. This should be the DEFAULT B-score
format; python_script is for cases where the quality check is genuinely
programmatic (e.g. "ratio of free-entry venues ≥ 50%").

### Fix 3: Per-type B-score templates

Add a section to each reasoning protocol with a concrete B-score example for
that structural type. E.g. for Type 3:

```
TYPE 3 B-SCORE TEMPLATE (include in b_score_constraints):
{
  "id":"bsc_001","score_tier":"B","hop":3,"pattern":"llm_semantic",
  "check_method":"llm_semantic",
  "source_in_profile":"<whichever phrase captures the tension>",
  "description":"Does the schedule interleave the two sides of the tension (e.g. alternating hidden-gem and iconic venues) rather than satisfying one side first and then the other? A balanced plan spreads the competing requirements across the day/trip."
}
```

Each type gets one or two templates. Models copy-paste-modify. Much higher
chance of non-empty B-scores.

### Fix 4: Don't auto-reject for truncated script_code

If a B-score has `pattern: python_script` but `params.script_code` is empty
or truncated, the current behaviour is "fail validation entirely." Change to:
downgrade to `llm_semantic` using the `description` as the prompt, and emit
a warning. This way truncation doesn't kill the whole task for a B-score
that's a minor enhancement.

## Test additions for E3

- Task with `llm_semantic` B-score and all fields → passes validation
- Task with `python_script` B-score and valid `script_code` → passes
- Task with `python_script` B-score and empty `script_code` → passes
  with warning, downgraded to `llm_semantic` internally
- Task with B-score missing required field (no `hop`, no `pattern`) → fails

## Follow-up: B-score scoring pipeline

Out of scope for this TODO, but noting: if we land P11 and models start
producing B-scores, the evaluator needs to consume them. Verify the
existing `llm_semantic` pathway in `eval/evaluator.py` actually runs the
judge LLM on B-constraints — if it doesn't, we've generated metadata
nobody reads.

---

## Priority order

1. **P9 (Type 3 logic)** — highest impact, unblocks the single biggest
   false-positive rejection source. Estimated ~3 hours.
2. **P10 (Type 5 logic)** — second biggest source of failures for the
   hardest type. Estimated ~2 hours.
3. **P11 (B-score pipeline)** — biggest quality improvement for the
   benchmark's scoring dimension, but doesn't affect success rate.
   Estimated ~4 hours (prompt rewrite + examples + test cases +
   verification that B-scores actually get used downstream).

All three can land in a single session. Full test suite (~430+ tests)
should remain green throughout.
