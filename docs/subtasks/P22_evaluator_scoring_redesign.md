# P22 — Evaluator Scoring Redesign
**Status: 📝 Agreed — implementation pending (paused for meeting prep)**
**Affects: `eval/evaluator.py`, `agents/runner.py`**

---

## Motivation

Phase 5.5d results and the P21 prompt-strip run both exposed that the current
C-score hard-gate architecture produces 0% for recoverable issues (tool name
hallucinations, missing parameters, skipped tool types) regardless of plan
quality. GLM-4-7-251222 is the clearest case: 37 valid tool calls, parseable
plan, scored 0% because of one typo in a tool name (`search_b_blogs_and_forums`).

Simultaneously, several C-score warnings and F-score checks are misplaced or
mis-weighted. This doc captures every agreed change before implementation.

---

## C-Score (`evaluate_c_score`) Changes

### Hard gates — only these two remain at score = 0.0

| Condition | Reason |
|-----------|--------|
| No `<final_plan>` tag / JSON parse failed (C1) | Nothing to score |
| Agent called zero tools but produced a plan (B1) | Plan is entirely hallucinated |

All other conditions become **deductions** from 1.0.

---

### Format deductions — **−0.04 per occurrence**

These were previously hard gates. They now reduce the score proportionally.

| ID | Condition |
|----|-----------|
| A1 | Unknown / hallucinated tool name (e.g. `search_b_blogs_and_forums`) |
| A2 | Missing required parameter (e.g. `to_venue_id` absent from `get_travel_time`) |
| A3 | Wrong parameter type (e.g. `top_k` passed as string) |
| A4 | `get_travel_time` param looks like a venue name, not an ID (contains spaces) |
| C2 | Top-level plan field missing (`task_id`, `city`, or `days`) — per field |
| C5 | `day_of_week` missing or invalid |
| C7 | Activity missing required field — per missing field |
| C8 | `time_start` or `time_end` not in HH:MM format |
| C9 | `time_end` ≤ `time_start` (non-overnight activity) |
| C10 | Invalid `activity_type` value |

---

### Richness deductions — **−(0.8 / (expected_days × 4)) per missing unit**

Formula: each unit of expected content (one activity slot in a standard
4-activity/day itinerary) is worth `0.8 / (expected_days × 4)` of the score.
Maximum total richness deduction is 0.8 (floor of 0.2 from this component alone).

| ID | Condition |
|----|-----------|
| C3 | `days` empty or not a list — counts as all expected activities missing |
| C4 | Fewer days than expected — each missing day = 4 missing activity slots |
| C6 | A day has zero activities — 4 missing slots |
| NEW | A day has fewer than 4 non-transport activities — (4 − n) missing slots each |

**"Unless specified" exemption:** If the task rubric contains a constraint with
`pattern == "time_ceiling"` (type2 tasks), the minimum-4 check is skipped for
that task entirely. Time-ceiling tasks are designed around venue selection within
a tight window; fewer activities is the correct behaviour.

---

### W3 — venue_id hallucination — **−0.1 per occurrence** (was −0.04)

| Condition |
|-----------|
| `venue_id` used in plan was never returned by any tool call in this session |

---

### Deleted from C-score entirely

| ID | Was | Reason for deletion |
|----|-----|---------------------|
| B2 | Issue: never called `search_yelp` | Not a hard failure; F-score checks plan quality |
| B3 | Issue: never called `search_blogs_and_forums` | Same; also removed from prompt |
| B4 | Issue: never called `get_travel_time` | Same; F-score checks transport adequacy |
| W1 | Warning: booking-required venue, no `get_official_site` | F-score already handles sold-out |
| W2 | Warning: any venue with official site, no `get_official_site` | Over-prescriptive; not plan quality |
| W4 | Warning: wrong-info venue not verified | Moved to F-score (see below) |
| W5 | Warning: active event, no `get_official_site` with date | Over-prescriptive |
| W6 | Warning: long walk leg, agent never used `mode=` | F-score validates transport duration |

**W7 (cost mismatch)** is moved to F-score at −0.1 deduction (see below).

---

### `agents/runner.py` — Scoring Requirements section

Delete MUST call mandates for the three deleted B-checks:

```
# DELETE these three lines from the Scoring Requirements section:
1. You MUST call search_yelp at least once to discover venues
2. You MUST call search_blogs_and_forums at least once to cross-check info
4. You MUST call get_travel_time for consecutive venue pairs in your plan

# KEEP:
3. You MUST call get_official_site for every venue in your plan that has one
5. You MUST wrap your final plan in <final_plan> tags with valid JSON
```

---

## F-Score (`evaluate_f_score`) Changes

### Activity time overlap — change from hard_fail to **−0.1 deduction**

Currently added to `hard_fails` list (counts as 0-score entry in averaged F).
Change to a `partial_result` entry with score `max(0, 1.0 - 0.1 * overlap_count)`.
Rationale: a 2-minute overlap on one day shouldn't zero the entire day's F-score.

### Cost mismatch — moved from C-score warnings, **−0.1 deduction**

`total_estimated_cost_local` differs by >10% or >£5 from sum of activity costs.
Add as a `partial_result` entry with score 0.9 (−0.1 deduction when triggered).

### Truth-carrier check — **new check, −0.05 per wrong-info venue missed**

For each venue in the submitted plan that has `has_wrong_info_planned = True`:
- If agent called `get_official_site(venue_id)` OR found a truth-carrier doc via
  `search_blogs_and_forums` (tool output contains a doc with `role = truth_carrier`
  for this venue): **no deduction**
- If neither: **−0.05 deduction** added as a `partial_result` entry

Rationale: F-score's hours check already catches plans that used the wrong info
(schedule fails the hours gate). This check separately rewards agents that found
the correct information, even if they didn't ultimately use it incorrectly.
The `found-but-ignored` case (agent found truth carrier but still scheduled
incorrectly) is caught by the hours check receiving score 0.0 as normal.

```python
# Implementation note: use result.get("tool_call_log", []) — NOT "tool_log"
# (existing bug in the sold-out double-check — fix both at the same time)
```

### `tool_log` → `tool_call_log` bug fix

In the sold-out check block:
```python
# BEFORE (bug):
tool_log_local = result.get("tool_log", [])

# AFTER:
tool_log_local = result.get("tool_call_log", [])
```

---

## Q5 — Activity count data finding

Checked against 207 days across 5 models (latest 5.5d run):
- 128/207 days (62%) have fewer than 4 non-transport activities
- Distribution: 1 act=7 days, 2 acts=50 days, 3 acts=71 days, 4 acts=60, 5=19
- Type2/time-ceiling tasks and `london_spring_museums_time_limit`,
  `london_carnival_cultural_170min` consistently produce 2–3 venues by design
- Long multi-day trips (4-day `london_easter_food_photographer_2026`) produce
  1-meal days 2–4 — this is genuinely thin and should be penalised

**Decision:** implement "unless specified" via `time_ceiling` rubric check.
Standard tasks (type1/3/4/6) get the min-4 check. Type2 / time-ceiling exempt.

---

## Implementation order (when resumed)

1. `eval/evaluator.py` — `evaluate_c_score()` full rewrite
2. `eval/evaluator.py` — `evaluate_f_score()` three targeted edits
3. `agents/runner.py` — delete 3 lines from Scoring Requirements
4. Re-run 6-model benchmark with P21 stripped prompt + P22 evaluator
5. Compare C/F/P distribution to May 3 baseline

---

*Created: May 2026*
*Implementation paused: meeting prep*
*Depends on: P21 (prompt strip), test_70 pool*

---

## BUG REPORT — check_constraint_pool_satisfiability is broken for 3 aggregation types

**Severity: High — directly degrades generated task quality**
**File: `scripts/generation/constraint_engine.py` → `check_constraint_pool_satisfiability()`**

### Root cause

`pool_as_activities()` converts all 68 pool venues into one synthetic 68-activity
plan, then runs the real aggregation evaluator against it. This is conceptually
wrong: the pool is a set of options, not a plan. An agent building a 12-venue
itinerary has full control over which venues to include. The check should ask
"can a valid plan be built?" — not "does the constraint pass if every venue
is scheduled?"

### Bug 1 — `agg="all"` (confirmed, active damage)

`agg="all"` requires n_satisfied == n_matching. With 68 mock activities, any
venue in the pool that doesn't meet the condition → score = 0 → constraint
rejected at SUBMIT time.

Evidence from generation logs:

```
Gemini 3 Flash (type3 failure log):
"PC_003 as 'all' is a pool filter. It filters to 45/68. But the validator
says it's not satisfiable? Wait... The system thinks the pool of 45 is too
small or too large? No, the 'Universal upper bar' check failed: 28/34 site
venues survive. I need to add a c..."
→ Agent genuinely confused, never resolved it within turn limit — task failed.
```

```
Type 5 generation (failure log):
"wheelchair_accessible + traffic_tier=low gave me exactly what I need:
food=5/34 (14%). But the validator is complaining the constraints aren't
satisfiable. I can't apply universal constraints to ALL venues when they
don't ALL have the property."
→ Agent explicitly identified the bug but could not work around it.
```

Additional confirmed case: "Museums should be at most moderate traffic tier"
— `agg="all"` on `scope=category=museum`, 10/15 museums qualify, 5 are
high-traffic → pool check rejects it. Agent correctly designed the constraint;
the check incorrectly rejects it.

**Forced workaround:** Agents must use `ratio: 0.6` where `agg="all"` is the
correct semantic. This produces a softer, less expressive constraint.
The food photographer's wheelchair requirement ("every venue we visit must be
accessible") is a hard requirement — not a soft 60%-preference.

### Bug 2 — `agg="none"` is silently fragile

`agg="none"` requires n_satisfied == 0. With 68 mock activities, if ANY venue
in the pool has the flagged property → fails. The one `none` constraint that
passed in the generated tasks ("no tourist trap venues") only passed because
the `tourist-trap` tag happens to not exist in the London pool. It was a
lucky pass, not a correct one.

These would all fail pool check despite being valid task constraints:
- "No loud venues" — noise_level=loud exists on some pool venues
- "Avoid high-traffic tourist spots" — traffic_tier=high on 18 venues
- "No booking-required venues" — booking_required=true on ~10 venues

### Bug 3 — `{at_most}` with non-`per_day` scope (latent; at_most_distinct is Bug 4)

Currently safe by accident: all at_most cases in the generated tasks use
`per_day` scope, which `_translate_scope_for_pool` translates to None →
pool check skipped. If any agent writes a whole-trip `at_most` constraint
with `scope="all"` (e.g. "at most 1 expensive meal across the 4-day trip"),
the pool check would compute n_satisfied=9 upscale restaurants > 1 → reject.

### Bugs 4–6 (extended May 2026)

**Bug 4 — `{at_most_distinct: N}` (same design flaw as Bug 3)**
Pool has 8 districts, constraint `{at_most_distinct: 2, field: "district"}`. Check: 8 ≤ 2 = False.
The constraint means "plan visits at most 2 districts" — the pool can have all 8.

**Bug 5 — `{ratio: R}` (pool ratio ≠ achievable plan ratio)**
Pool has 20/50 outdoor venues (40%). Constraint `{ratio: 0.6}`. Check: 40% ≥ 60% = False.
An agent can pick 10 outdoor + 6 non-outdoor = 62.5% ≥ 60%. Pool IS solvable.
The existing comment "ratio: keep existing logic — meaningful at pool level" is wrong.

**Bug 6 — `{exactly: N}` (pool size ≠ plan count)**
Pool has 8 museums, constraint `{exactly: 3}`. Check: 8 == 3 = False. Agent can visit exactly 3.

### Fix (updated to cover all 6 bugs)

```python
def check_constraint_pool_satisfiability(constraint, venue_pool):
    consequence = constraint.get("consequence", "p_score_full")
    if consequence == "b_score_bonus":
        return True, "b_score_bonus — pool check skipped"

    scope = constraint.get("scope", "all")
    agg   = constraint.get("aggregation", "all")

    translated = _translate_scope_for_pool(scope)
    if translated is None:
        return True, "time/day scope — pool check skipped"

    if isinstance(agg, dict) and ("at_least_days" in agg or "sum" in agg):
        return True, "temporal/sum aggregation — pool check skipped"

    # NEW: plan-level aggregations — skip pool check entirely
    if agg in ("all", "none"):
        return True, "agg=all/none — pool check skipped (existence via solvability lower bars)"
    if isinstance(agg, dict) and any(k in agg for k in
            ("at_most", "at_most_distinct", "ratio", "exactly")):
        k = next(k for k in ("at_most", "at_most_distinct", "ratio", "exactly") if k in agg)
        return True, f"plan-level aggregation ({k}) — pool check skipped"

    # Remaining: {at_least: N} and {count_distinct: N} — meaningful at pool level
    ...
```

### Task quality impact (all 6 bugs)

| Bug | Impact |
|-----|--------|
| `agg="all"` (1) | Agents forced from hard universal ("every venue accessible") to soft ratio — weaker |
| `agg="none"` (2) | Entire avoidance constraint category blocked when pool has the property |
| `{at_most: N}` (3) | Whole-trip ceiling constraints rejected at pool check |
| `{at_most_distinct: N}` (4) | Distinct-ceiling constraints rejected (pool has >N distinct values) |
| `{ratio: R}` (5) | Ratio constraints rejected when pool ratio < target despite plan being achievable |
| `{exactly: N}` (6) | Exact-count constraints rejected unless pool has exactly N qualifying venues |

**Implementation:** Fix `check_constraint_pool_satisfiability`. Add test coverage for all 6 types.
No schema changes needed. Also update: task_agent.py handbook SOLVABILITY CHECKS note,
constraint_engine.py docstring and error message.

---

*Bug added: May 2026*

---

## Sandbox Task Selection

### Primary example (food photographer — currently in slide)
`london_easter_food_photographer_2026` · type1 · Easter · 4 days · 18 turns
- 4 P-constraints, all pass at 1.0 in GPT-5.4 run
- Hit the agg="all" wheelchair bug during generation — agent confused (turns 12-17)
- Shows the bug clearly: agent notes "the validator says it's not satisfiable?" then
  switches to ratio≥0.6 instead of the correct universal constraint
- Good for demonstrating constraint diversity; generation process is pedagogically
  interesting for the bug discussion, not for showing a clean design

### Alternative example — clean generation, no confusion
`london_summer_carnival_budget_birthday` · type4 · Carnival 2026 · 2 days · 13 turns
**File:** `london/tasks/runs/test_70/london_carnival_2026/claude-sonnet-4-20250514/london_summer_carnival_budget_birthday.json`
**Generation log:** `...agent_logs/success_claude-sonnet-4-20250514_london_carnival_2026_type4_1777839950.json`

**Query:**
"I'm celebrating my birthday in London during Carnival weekend with my partner.
We have a budget of £30 per day and want a special birthday dinner at an upscale
restaurant. We love art and history, especially museums, and want to explore the
city without overspending."

**P-constraints (3 PCs):**
| ID | Hop | Source phrase | Constraint |
|----|-----|---------------|-----------|
| pc_001 | 1 | "budget of £30 per day" | Daily spend sum ≤ £30 (sum agg, per_day) |
| pc_002 | 2 | "special birthday dinner" | ≥1 upscale meal (at_least:1, meal scope) |
| pc_003 | 1 | "love art and history, museums" | ≥2 museums (at_least:2, category=museum) |

**Why the budget tension is real:** Upscale meals cost £30–85. The entire daily
budget equals the floor of one upscale dinner. The agent must find either a
budget-friendly upscale option or make the birthday dinner the day's only spend.

**Generation process (13 turns, 3 submits, 0 pool errors):**
- T1–4: HELP calls (handbook)
- T5: THINK — plans type4 budget approach
- T6: query_pool({}) → full pool scan, reads cost structure (cheapest: £4.5, free museums)
- T7: THINK — analyzes: cheapest viable plan = £24/day
- T8: query_pool(price_tier=upscale) → 7 venues, £30–85 range
- T9: THINK — designs task with £35/day initially
- T10: SUBMIT → ❌ structural_type typo (only non-bug error)
- T11: SUBMIT → ❌ "budget too loose: £35 = 1.46× cheapest viable plan (£24); need 1.1–1.3×"
  ← This is a CORRECT validator catch, not a false positive
- T12: THINK — recalculates: targets £26–31 range to hit 1.1–1.3× multiplier
- T13: SUBMIT → ✅ accepted with £30/day (1.25× cheapest = tight but solvable)

**Why this is the better sandbox demo:**
1. Only query_pool calls are purposeful: full scan for cost structure + upscale filter
2. The one validation failure (budget too loose) is the validator working correctly —
   catching a real design problem, not a false positive from the agg="all" bug
3. Agent's thinking is clean and rational — no confusion about why constraints fail
4. Type 4 budget tension is the most visually interesting story (constraint math shown)
5. The sum aggregation + per_day scope is the most sophisticated pattern in the task set

*Added: May 2026*

---

## C-Score Implementation (May 2026) — BFCL-Style Redesign

**Status: ✅ Implemented**

### Final decisions applied

**Hard gates** (only two, score=0, F and P still run):
- Zero tool calls made
- No `<final_plan>` produced / JSON parse failed

**Section A — Per-call AST validation** (BFCL-style, −0.04 each)

Draws directly from BFCL (Patil et al., 2025) AST evaluation pipeline:
Function Match → Parameters Present → No Unexpected Params → Types Match → Value Valid

| ID | Check |
|----|-------|
| A1 | Unknown tool name |
| A2 | Missing required parameter |
| A3 | Unexpected parameter (parameter hallucination) — **new** |
| A4 | Wrong parameter type |
| A5 | `city` param != task city — **new** |
| A6 | `venue_id` param contains spaces (name passed, not ID) |
| A7 | `venue_id` / `from_venue_id` / `to_venue_id` not in prior tool results — **sequential, new** |

Tool schema additions: `OPTIONAL_PARAMS` dict defines valid optional params per tool.
`VALID_TRANSPORT_MODES = {"walking", "transit", "cycling", "taxi"}`.

**Section B — DELETED.** Process completeness checks removed:
- Never called `search_yelp` / `search_blogs_and_forums` / `get_travel_time`
- Official site missing for booking_required / wrong_info venues
- Sprint 8 mode-param warning (now caught by A3)

Wrong-info unverified block also deleted from C — moves to F-score truth-carrier check (P22 F-score, separate implementation).

**Section C — Timetable format** (all now −0.04 deductions, no more hard fails)

| ID | Check |
|----|-------|
| C1 | `task_id` missing or doesn't match task — value match added |
| C2 | `city` missing or doesn't match task city — value match added |
| C3 | `days` missing or not a list |
| C4 | Fewer days than expected |
| C5 | More days than expected — **new** |
| C6 | `day_of_week` missing or invalid |
| C7 | Day has no activities |
| C8 | Activity missing required field (per field) |
| C9 | `time_start` or `time_end` not HH:MM |
| C10 | `time_end` ≤ `time_start` for non-overnight activity |
| C11 | Invalid `activity_type` |
| C12 | Invalid transport `mode` — **new** |
| C13 | `venue_id` not in any tool result (was warning, promoted to deduction) |

**Section D — Blank time coverage** (variable deduction, max 0.8 per day)

```
day_ref      = min(last_activity_end − first_activity_start, 600)  # cap at 10h
expected_cnt = day_ref / 120           # 2h per venue slot
deduction    = 0.8 / expected_cnt × (expected_cnt − actual_cnt)
               when actual_cnt < floor(expected_cnt) − 1  (1-venue slack)
```

Constants: `DEFAULT_DAY_MIN = 600` (10h), `VENUE_TIME_COST = 120` (2h).

Overnight activities handled: effective_end = end if end >= start else end + 1440.

**Passed field**: `passed = True` whenever both hard gates clear. F and P always run.
C-score format deductions reduce C-score proportionally but never block F/P evaluation.

**Return dict**: `deductions` replaces `issues`. `warnings` remains informational-only.

**Downstream changes**:
- `print_report()`: displays `deductions` as `−0.04 description` (was `✗ issue`)
- `evaluate()`: `c_passed` reads `c["passed"]` directly; gate fires only for hard gates
- `run_benchmark.py`: `c_issues` key reads from `c.get("deductions", [])`

**Regression tests**: 6 scenarios (S1–S6), 18 assertions, all passing.
New scenarios: S5 (parameter hallucination), S6 (blank time deduction).
JSON fallback added to `load_ground_truth` for Paris stub data.

