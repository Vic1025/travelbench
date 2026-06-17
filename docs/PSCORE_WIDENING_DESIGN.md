# P-score Spread Widening — Design Pass

**Date:** 2026-05-25
**Author:** Claude (auto-mode evolution work)
**Companion:** [`docs/PSCORE_BASELINE_ANALYSIS.md`](PSCORE_BASELINE_ANALYSIS.md)
**Goal (per Vic):** widen the best-vs-worst model gap on P-score. Previous runs cluster all models at 70–80%; aim for ~20+pt spread.

---

## Failure mode (from baseline analysis)

- Half of multi-model tasks are degenerate (every model scores the same).
- 7 of 28 sampled tasks are pure ceiling (every model at P=1.0).
- Type 2 tasks score P=0.938 with range=0.125 — systematically trivial.
- 35 of 51 P-constraints use the easiest aggregation primitive (`at_least`).
- Zero `check_method=llm` constraints in current data; LLM-judge pathway exists but is dormant.

This isn't a model capability problem. It's a **task authoring problem**: the current task-generation agent reaches for the easiest constraint shape by default, and the validator doesn't catch trivially-satisfied constraints.

---

## Anti-over-design audit (per [[feedback-avoid-overdesign]])

For each proposed lever:

| Lever | Measured failure? | Existing route? | Ship in this pass? |
|---|---|---|---|
| Diversify aggregation primitives | YES — 69% are `at_least` | YES — protocol/prompt change | ✅ |
| Activate LLM-judge for semantic constraints | YES — 0/51 in sample | YES — evaluator supports it | ✅ |
| Reject trivial P-constraints in validator | YES — 25% tasks at P=1.0 | YES — `validate_task_schema` extension | ✅ |
| Type 2 P-constraints must encode tension | YES — Type 2 P=0.938 | YES — protocol rule | ✅ |
| Truth-carrier 3-tier credit | Indirect signal | YES — F2c extension | 🟡 defer pending data |
| `avg_venue_difficulty` task metadata | Unmeasured leverage | NEW (small) | 🟡 defer — exists in design docs |
| Implausibility traps | Unmeasured | NEW (medium) | 🔴 defer to Phase 7 |
| Venue-info-difficulty axis | Unmeasured | NEW (large) | 🔴 defer to Phase 7 |
| Unsolvable tasks (Cheng-style) | Aligned with ceiling | NEW (medium) | 🟡 evaluate after the 4 ✅ ship |
| B-Score 3 dimensions | Unmeasured | NEW (huge) | 🔴 defer to Phase 7 |

The four ✅ are all **extensions of existing routes** with measured failures. Ship them, measure, then escalate to 🟡 only if the spread didn't widen enough.

---

## The four ship items

### P7-T1 — Aggregation primitive diversification (task-gen protocol)

**Failure mode:** 35 of 51 P-constraints in current multi-model tasks use `at_least`. Mean P-score on `at_least` is 0.691; on `ratio` it's 0.653 with **range 0.396** (highest discrimination). The task agent reaches for `at_least` by default because the protocol examples emphasize it.

**Existing route:** the constraint engine already supports `ratio`, `at_most`, `at_most_distinct`, `sum`, `count_distinct`, `exactly`. The protocol just needs to push the agent toward them when the persona naturally implies them.

**Fix:** add to `task_agent.py` REASONING PROTOCOL for each structural type a **primitive selection rubric**. Concretely:

| Persona signal | Reach for |
|---|---|
| "no more than N", caps, "at most" | `at_most`, `at_most_distinct` |
| "spread across", "mostly", "majority of" | `ratio` |
| "different cuisines", "variety", "N kinds" | `count_distinct` |
| "exactly N", "N each day" | `exactly` |
| "total spend / time / count" | `sum` with operator |
| "at least N" only when really an inclusion floor | `at_least` (LAST RESORT for new tasks) |

**Scenario table:**

| Query phrase | Bad authoring (current) | Good authoring (this fix) |
|---|---|---|
| "Try a few different cuisines" | `at_least: 2 has_tag=italian` (degenerate) | `count_distinct: 3 field=cuisine` |
| "Most of the trip should be quiet" | `at_least: 1 noise_level<=1` (trivial) | `ratio: 0.7 noise_level<=1` |
| "Don't visit more than one museum per day" | _no constraint authored_ | `at_most: 1 scope=per_day category=museum` |
| "Stay under £150 a day for meals" | `at_least: 1 price_tier<=mid` (irrelevant) | `sum: avg_cost_local operator=<= value=150 scope=per_day activity_type=meal` |

**Validator extension:** soft-warn (not hard-reject) when a Type 3 / Type 4 / Type 6 task uses only `at_least` aggregations. Tasks where the persona could naturally express a cap or ratio shouldn't only have floors.

**Tests:**
- New regressions in `test_b1.py` / `test_task_agent.py` covering each primitive selection from a persona-signal phrase.
- Existing tests for the constraint engine must still pass (no engine changes).

---

### P7-T2 — Activate LLM-judge pathway for semantic constraints

**Failure mode:** all 51 P-constraints in the multi-model sample use `check_method=code`. The LLM-judge pathway exists in the evaluator (`eval/evaluator.py` has the dispatch), is documented in `personalised_benchmark_design.md`, and contributes 0% of current discrimination.

**Existing route:** the evaluator supports `check_method=llm` constraints today; the gap is on the **generator** side — `task_agent.py` doesn't produce them.

**When to use LLM-judge:** for constraints where a deterministic check would be wrong:
- "atmosphere matches an anniversary mood"
- "schedule reads as locals-first, not tourist-trap-first"
- "the cluster of evening venues feels romantic without being formal"

These can't reduce to `has_tag` or field comparisons because the qualifying signal lives in the venue's source-doc bodies, not its structured fields.

**Fix:** add a section to each structural type's reasoning protocol: "AT MOST ONE LLM-judge constraint per task, only when no structured field or tag captures the qualifying signal."

**Constraint shape (already in the schema, restated for clarity):**
```json
{
  "id": "p_NNN",
  "score_tier": "P",
  "hop": 2,
  "pattern": "semantic_qualitative",
  "check_method": "llm",
  "scope": "all",
  "condition": {"qualitative": "venues collectively read as a quiet, walkable anniversary cluster"},
  "source_in_profile": "<exact phrase from query>"
}
```

**Anti-over-design guard:** cap at one LLM-judge constraint per task. The judge is the most expensive eval call; one is enough to discriminate and won't inflate eval cost.

**Tests:**
- Regression: a task with `check_method=llm` passes validator.
- Regression: a task with >1 LLM-judge constraint gets soft-warned.
- Live: confirm evaluator's LLM-judge call shape works on a sample task (no API needed for the validator test).

---

### P7-T3 — Reject trivially-satisfied P-constraints in validator

**Failure mode:** 7 of 28 multi-model tasks (25%) score P=1.0 for every model. Common pattern: `at_least: 2 category=museum` on a query whose surface preference is already "museums". The constraint doesn't test preference — it restates it.

**Existing route:** `validate_task_schema` already does semantic-coherence checks. Extending it with a "trivial constraint" detector fits the existing pattern.

**Detection heuristic (deterministic, no LLM):**
1. Extract surface-preference signals from `public_input.query` (a short keyword set: museum, food, drink, art, etc.) — already partially done by the existing query-parser in the validator.
2. For each `at_least:N` P-constraint:
    - If `scope=category=X` AND `X` matches a surface signal AND `N <= 2` → flag as trivial.
    - If `condition={has_tag: T}` AND `T` matches a surface signal → flag as trivial.
3. Soft-warn the agent: "this P-constraint is trivially satisfied by following the surface query — strengthen with a cap, ratio, or hop-2 inference."

**Anti-over-design guard:** soft-warn, not hard-reject. The agent can still SUBMIT after a warning; the goal is to nudge re-authoring, not to block all simple constraints. Hard-reject only if a task has **zero** non-trivial P-constraints (the whole rubric is degenerate).

**Tests:**
- Detection regression: known-trivial constraint shapes flagged.
- Detection regression: known-non-trivial shapes pass clean.
- End-to-end: a task with one trivial + two non-trivial constraints gets warned but allowed; a task with all trivial gets hard-rejected.

---

### P7-T4 — Type 2 P-constraints must encode tension within the time budget

**Failure mode:** Type 2 (subset selection under time ceiling) mean P=0.938, range=0.125. Type 2's *structural* tension is the time ceiling (F-score check). Its P-constraints in current authoring add nothing: they restate the category preference the query already implies.

**Existing route:** Type 2's reasoning protocol in `task_agent.py` is the lever. Add a rule.

**The rule:** Type 2's P-constraints must encode a **trade-off** that competes with the time ceiling — not just "include a thing the query already says you'll include." Concretely:

| Bad (current) | Good (new rule) |
|---|---|
| `at_least: 2 museums` on a "museum trip" query | `ratio: 0.5 traffic_tier<=mid` — "spend at least half your visits at less-touristy spots" |
| `at_least: 1 free-entry museum` | `count_distinct: 3 field=category` — "diversify, don't repeat the same kind" |
| (no P-constraint) | `at_most: 2 has_tag=tourist-trap` — explicit cap competing with iconic-venue pressure |

**Reasoning:** Type 2's time ceiling forces *which* venues fit. P-constraints should test *which subset of fitting venues the agent picks*. If the P-constraints align with the surface preference, the agent's natural choice satisfies them — no test.

**Validator extension:** Type 2 + only-at_least-P-constraints + scope matches surface signal → hard reject. The task isn't testing what Type 2 is supposed to test.

**Tests:**
- Regression: a Type 2 task with only `at_least: 2 category=museum` is rejected.
- Regression: a Type 2 task with `ratio` or `at_most` or `count_distinct` P-constraints passes.

---

## What this design intentionally does NOT do

1. **Doesn't introduce a new task type.** P-score widening fits inside existing types.
2. **Doesn't change the evaluator's scoring formula.** P22 already redesigned C and F. The P-score formula is fine — the problem is upstream in task authoring.
3. **Doesn't add new constraint patterns.** Engine primitives are sufficient.
4. **Doesn't introduce semantic infrastructure** (embeddings, custom LLM-judge harness beyond what exists). Phase 7 territory.
5. **Doesn't touch the venue corpora.** All four items are task-generation pipeline changes.

These are all **extensions** of existing routes, as Vic's anti-over-design rule demands.

---

## Measurement plan

**Before changes:** run the 4-model × 18-task baseline on the NYC 2026-05-21 task set. Capture per-task P-score and the model-spread metric (best mean − worst mean).

**Per change:** re-author ~5 sample tasks under each new rule (P7-T1 through P7-T4) and run the same 4-model sweep on the re-authored tasks. Compare P-score spread.

**Target:** model spread (best mean − worst mean) increases from ~10pt to ≥20pt. The exact target is calibrated against the baseline once it lands.

**Cost:** each 4×18 sweep is ~CAD 10 per pass. Budget allows several iterations.

---

## Sequencing

1. **Now:** run baseline (Task #4 in flight).
2. **After baseline:** start with P7-T3 (trivial-constraint detector) because it's the cheapest and most measurable — write the detector, run it as a *non-rejecting audit* across the existing NYC 2026-05-21 tasks to confirm the heuristic flags the right things.
3. **Then P7-T1 + P7-T4** (the two protocol-side changes) together — they share the task_agent.py edit.
4. **Then P7-T2** (LLM-judge activation) — needs a small evaluator-side smoke test before any task agent change.
5. **Sweep + measure** after all four land.

If the spread widens past 20pt, ship. If not, escalate to 🟡 levers (truth-carrier 3-tier, Cheng-style unsolvable tasks).

---

## External references applied

- **TravelPlanner** (Xie et al., 2402.01622) — constraint-tracking failure modes corroborate the "trivial constraint" pattern; their 60% GPT-4 success comes from harder constraint sets than current TravelBench.
- **DeepPlanning** (Alibaba Qwen, 2601.18137) — local/global constraint split. Type 2's P-constraints today are all "local" (per-venue category check). Adding `ratio`/`sum` constraints introduces the "global" dimension this paper highlights.
- **τ²-Bench** (Sierra, 2506.07982) — compositional task generation with controlled complexity. The trivial-constraint detector + primitive selection rubric is the same idea reframed for our authoring pipeline.
- **InfoDisorder.docx** (Vic, March 2026) — surfaces several other levers (implausibility traps, truth-carrier 3-tier, venue-info-difficulty); intentionally deferred per anti-over-design rule.

All entries logged in `docs/EXTERNAL_REFERENCES.md`.
