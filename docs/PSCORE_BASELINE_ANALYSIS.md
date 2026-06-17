# P-score Spread — Baseline Analysis

**Date:** 2026-05-25
**Source:** `results/scores.db` (250 score rows across 10 models, run timestamps 2026-04-27 / 2026-05-03)
**Goal:** Characterize why P-score spread is narrow (last runs clustered all models at 70–80%) so the widening approach is evidence-driven, not guessed.

---

## Headline finding

**Half of the multi-model tasks contribute zero discrimination.** Of 28 task IDs scored by 3+ models:

- **7 tasks (25%)** have every model scoring exactly P=1.0 — pure ceiling.
- **6 tasks (~21%)** have every model scoring the same non-1.0 value (0.33 / 0.50 / 0.67) — pure flat.
- **1 task** has every model at 0.0 — floor.
- Only **4 tasks (~14%)** discriminate with range > 0.4.

The 70–80% mean cluster across models is the algebraic consequence: when half the tasks are degenerate and contribute identical scores to every model, model differences get washed out.

---

## Per-model mean P-score (250 rows total)

| Model | n | mean P | std |
|---|---:|---:|---:|
| kimi-k2.6 | 3 | 0.889 | 0.19 |
| doubao-seed-2-0-pro | 23 | 0.786 | 0.25 |
| claude-sonnet-4-20250514 | 21 | 0.782 | 0.29 |
| gpt-5.4 | 36 | 0.742 | 0.27 |
| gemini-3.1-pro-preview | 29 | 0.722 | 0.29 |
| glm-4-7-251222 | 12 | 0.685 | 0.27 |
| gemini-3-flash-preview | 15 | 0.683 | 0.23 |
| deepseek-reasoner | 1 | 0.667 | — |

**Best-vs-worst gap** (excluding singleton kimi/deepseek): **doubao 0.786 − gemini-flash 0.683 = 10.3 pt.** Within-model std (≈0.25) far exceeds between-model gap — variance is dominated by task design, not model capability.

**Overall distribution:** p25=0.60, p50=0.75, p75=1.00, p90=1.00. About 40% of all score rows are exactly 1.000.

---

## Aggregation primitive → P-score effect

The dominant primitive `at_least` is also the easiest — and 35 of 51 P-constraints in the multi-model sample use it.

| Aggregation | n constraints | mean task P | mean range |
|---|---:|---:|---:|
| `count_distinct` | 2 | 1.000 | 0.000 |
| `at_least` | **35** | 0.691 | 0.270 |
| `ratio` | 4 | 0.653 | **0.396** |
| `at_most` | 3 | 0.551 | 0.233 |
| `sum` | 4 | 0.521 | 0.167 |

`ratio` shows the highest range (0.396) — tasks that include a `ratio` constraint discriminate models. `sum` (used in type4 budget) is hard but uniformly hard — every model misses similarly, no spread.

---

## Structural type → P-score effect

| Structural type | n tasks | mean P | mean range |
|---|---:|---:|---:|
| type2_subset_selection | 4 | **0.938** | **0.125** |
| type1_cascading | 3 | 0.746 | 0.494 |
| type3_competing | 4 | 0.680 | 0.221 |
| type4_precision_allocation | 4 | 0.521 | 0.167 |
| type6_context_window | 1 | 0.283 | 0.416 |

**Type 2 is the ceiling.** Almost every type2 task lands at P≈1.0 because the typical pattern is "at_least 2 museums" + "at_least 1 has_tag=X" — both trivially satisfied by an agent that already selects museums for a museum-themed query. The task isn't *testing* preference satisfaction, it's confirming the agent picked from the relevant category.

Type 1 and Type 6 are the natural discriminators — they pile on hop-2 inferences that conflict with surface preferences.

---

## Condition shape → P-score effect

| Condition | n | mean task P |
|---|---:|---:|
| `has_tag: X` | 15 | 0.761 |
| `field:traffic_tier` | 9 | 0.694 |
| empty (just scope filtering) | 14 | 0.686 |
| `field:category` | 2 | 0.685 |
| `field:wheelchair_accessible` | 1 | 0.625 |
| `field:price_tier` | 8 | 0.570 |
| `field:dress_code` | 1 | **0.283** |

Structured-field comparisons (especially `price_tier`, `dress_code`) are harder for models than tag-presence checks. Some of this is venue scarcity (few fine-dining places, few `dress_code=formal` venues); some is that models reason better about tags than about ordinal fields.

---

## check_method → unused infrastructure

**ALL 51 P-constraints in the multi-model sample use `check_method: code`.** Zero LLM-judge constraints in the sampled data. The LLM-judge pathway exists in the evaluator and is documented in `personalised_benchmark_design.md`, but is not being produced by the task generation agent in current runs. This is a dormant discriminator.

---

## Most discriminating tasks (range > 0.3)

| Range | mean P | Type | Aggregations used | task_id |
|---:|---:|---|---|---|
| 0.75 | 0.62 | type1 | **ratio**, at_least×3 | london_easter_food_photographer_2026 |
| 0.50 | 0.75 | type2 | at_least×2, **ratio** | london_spring_museums_time_limit |
| 0.42 | 0.28 | type6 | at_least×2, **at_most** | london_christmas_week_type6_001 |
| 0.40 | 0.71 | type1 | at_least×4, **at_most_distinct** | london_spring_anniversary_001 |
| 0.33 | 0.58 | type4 | **sum**, at_least×2 | london_summer_carnival_budget_birthday |

**Every single discriminating task contains at least one non-`at_least` aggregation** (`ratio`, `at_most`, `at_most_distinct`, or `sum`). Pure `at_least` stacks never discriminate.

---

## Why the spread is compressed — synthesis

Three reinforcing causes, in order of likely impact:

1. **`at_least`-dominant constraint authoring.** 69% of P-constraints are `at_least` — the easiest primitive. The task generation agent reaches for it by default, partly because the protocol examples lean on it.
2. **Type 2 tasks are systematically trivial.** Type 2's "museum + time ceiling" framing produces P-constraints that any reasonable agent satisfies. The structural design promises tension (subset selection under a budget) but the *P-constraints actually authored* don't test that tension — they restate the category preference.
3. **LLM-judge pathway is unused.** Semantic constraints that genuinely require interpretation (e.g. "stay at venues that feel local rather than touristy") don't exist in current runs. The evaluator supports them; the generator doesn't produce them.

Secondary contributors:
- **Hop count is not discriminating** on its own — hop-2 constraints score only marginally lower than hop-1 in current data. The win from hop-2 is which *types* of inference get encoded, not just the hop label.
- **Field-comparison conditions on rare structured values** (dress_code, price_tier=fine-dining) are harder but bottlenecked by venue scarcity in the pool.

---

## Implications for the widening design

(To be elaborated in the design pass — Task #5.)

The cheapest, most general moves likely live inside the **task-generation agent's authoring patterns**, not in evaluator changes:

- **Diversify the aggregation primitive distribution.** Push the agent toward `ratio`, `at_most`, `at_most_distinct`, `sum` where the persona naturally implies them. `at_least` should still be available, but not the default reach.
- **Tighten Type 2 P-constraint authoring.** Add a rule that Type 2's P-constraints must encode *trade-off* tension within the time budget, not just category preference. (Investigate: maybe the difficulty is that Type 2's structural mechanism — time ceiling — *already* discriminates via F-score, so P-constraints have nothing left to test. If so, Type 2 P-constraints may be inherently low-leverage.)
- **Activate the LLM-judge pathway.** Add explicit prompting examples for semantic-only constraints in domains where a tag doesn't exist (e.g. "atmosphere matches anniversary vibe").
- **Tasks that pass everyone are wasted slots.** A post-generation audit could flag tasks whose P-constraints are "trivially satisfied by following the surface query" and reject them.

These are **extensions of existing routes**, not new framework. No new task type, no new score tier, no new schema column required — at least until evidence shows the existing primitives can't carry the load.

---

## Caveats

- Sample is uneven: 41% of task IDs in `scores.db` are no longer on disk (older runs against retired task sets). The on-disk multi-model sample is 16 tasks, 51 P-constraints.
- New NYC 2026-05-21 task set hasn't been benchmarked yet — current evaluator changes (P22, all of Phase 6) may have shifted the picture. Task #4 will confirm.
- Some patterns may be London-specific (corpus has known pre-Phase 6 issues). NYC `test_70` is the cleaner reference for future analysis.
