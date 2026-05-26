# P6 — Validation logic audit against task_structural_types.md
**Status: 🟡 in progress (exemption fixes landed, E3 tests still need updating, time/budget solvability checks pending discussion)**
**Blocks: next full SOTA run (wrong exemptions falsely reject valid tasks)**

---

## Background

Run 6 of `test_50` with deepseek-chat hit 9/24 success. Most failures were agent task-design judgment, but 7 of 15 failures were actually **false positives from validation rules that applied where the spec says they shouldn't**. The fifth-run transcripts surfaced this when walking through individual failure causes against `docs/task_structural_types.md`.

The core issue: validation rules were added without careful cross-referencing against each structural type's "Validity guarantee" clause. Several rules assume P-P pool-filter interaction is the universal difficulty mechanism, but the spec describes per-type difficulty sources:

| Type | Difficulty source per spec |
|---|---|
| Type 1 | Cascaded constraints (inference, not filter narrowing) — "always satisfiable" |
| Type 2 | Time/logistics ceiling in query text — "≥ N viable venues" (not ≤ N) |
| Type 3 | P-P tension (definitional) |
| Type 4 | Budget ceiling in query text — "at least one combo fits budget" |
| Type 5 | Constraint stacking narrows pool (narrowness IS the task) |
| Type 6 | Seasonal-window tension — "always satisfiable" |

Only type 3 and type 5 have difficulty that comes from P-P filter dynamics. The rest have their difficulty expressed through **query text + hard constraints + seasonal window**, which our validator can't see from the rubric alone.

---

## Audit table

| Rule | T1 | T2 | T3 | T4 | T5 | T6 | Correct? |
|---|---|---|---|---|---|---|---|
| Missing top-level fields | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ universal |
| P-constraint schema checks | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ universal |
| ≥1 hop-2 constraint | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ per spec line 24 |
| source_in_profile tracing (A1) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ universal per spec |
| Max trait categories (2, or 4 for t5) | ✓ | ✓ | ✓ | ✓ | – | ✓ | ✅ exempts t5 |
| Max 1 Cat2 signal | ✓ | ✓ | ✓ | ✓ | – | ✓ | ✅ exempts t5 |
| Same-cluster stacking | ✓ | ✓ | ✓ | ✓ | – | ✓ | ✅ exempts t5 |
| "Constraints eliminate all venues" | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ universal solvability |
| category_count reachability | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ universal solvability |
| label_required pool membership | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ universal solvability |
| hidden_gem_required min_count | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✅ universal solvability |
| **"No constraint tension"** | – | **–** | ✓ | – | ✓ | – | ⚠ needs exempting t1/t2/t4/t6 |
| **Pool too large (>25)** | – | **–** | ✓ | – | ✓ | – | ⚠ needs exempting t1/t2/t4/t6 |
| Time-budget feasibility | — | ⬜ | — | — | — | — | 🟡 **GAP** for t2 (defer) |
| Query-budget feasibility | — | — | — | ⬜ | — | — | 🟡 **GAP** for t4 (defer) |

**Legend:** ✓ correctly applies, – correctly exempt, ⚠ needs change, ⬜ gap (not yet implemented), 🟡 pending discussion.

---

## Fixes landed

1. **`test_generate_tasks.py:1697` tension exemption** extended to cover type1, type2, type4, type6. Previously tension was required for all types ≥ 2 P-constraints — but per spec only type3 and type5 actually need it. Type 2's selection pressure comes from the time ceiling in query text (hard constraint `travel_time_hard`), not P-P interaction.

2. **`scripts/generation/generate_task.py:790` pool-size exemption** extended to cover type1, type2, type4, type6. Previously the `POOL_SIZE_MAX=25` "too easy" rule applied to all types — but per spec:
   - Type 1: always satisfiable, narrowness isn't the point
   - Type 2: selection pressure is time ceiling, not pool size
   - Type 4: budget in query text narrows, not rubric filters
   - Type 6: always satisfiable

   The rule now only applies to type3 (tension narrows) and type5 (narrowness IS the task).

---

## Gaps to discuss before implementation

### Type 2 — Time-budget solvability check (⬜ pending)

Per spec: "Must verify ≥ N viable venues exist within the ceiling before publishing. Core solvability check."

Our current validator doesn't check that the filtered pool actually has enough venues to reach `category_count_minimum` within the query's stated time budget. A task with `min_count: 3 museums` and a 2-hour time window would pass validation even though no 3-museum plan fits in 2 hours including travel.

**Open questions:**
- Time budget lives in query text ("only 5.5 hours"), not in a rubric field. How should the validator know it?
- Possible approaches:
  - (a) Parse time from query with regex — brittle
  - (b) Require the agent to put time in a structured field — schema change
  - (c) Estimate minimum plan time from hard constraint `travel_time_hard` + `hours_check` — but those don't carry the query's user-stated ceiling either
  - (d) Punt to B-score `python_script` — consistent with how we already handle budget optimality for type 4

### Type 4 — Query-budget solvability check (⬜ pending)

Per spec: "Must verify at least one valid combination of venues exists within the budget ceiling. Free-entry venues are essential for tight-budget tasks."

Same structural issue as type 2 but for money instead of time. The budget lives in the query text as a number (e.g. "£40/day"), not in the rubric. Same options as above.

**My inclination:** defer both of these to B-score python_script evaluation rather than try to parse query text in the validator. The validator's job is schema + structural validity; per-task quality and feasibility under natural-language constraints is what the B-score evaluator exists for.

---

## Next steps

1. ✅ Land the two exemption fixes (tension + pool-size for t1/t2/t4/t6)
2. ⬜ Update E3 test suite — the existing "pool of 30 hard fail" test asserts type 2 IS subject to pool-size; update to reflect corrected exemption list
3. ⬜ Add E3 test coverage: tension check does NOT fire for t1/t2/t4/t6 with stacking P-constraints
4. ⬜ Add E3 test coverage: pool>25 check does NOT fire for t1/t2/t4/t6
5. 🗣 **Discuss with user** before implementing time-budget check (t2) and budget-feasibility check (t4)
6. ⬜ Export repo
7. ⬜ Run fresh deepseek-chat generation cycle — expected lift from 9/24 → 15-20/24 just from the exemptions

---

## Why this matters

Without these fixes, the agent is being penalised for following the spec correctly. The more SOTA the model, the more likely it is to produce spec-aligned tasks that our validator rejects for the wrong reasons. Every false rejection wastes ~20 agent turns + ~$0.05 of API cost per task. At scale (multiple cities × multiple models × hundreds of tasks), false-positive validation is the dominant cost sink.
