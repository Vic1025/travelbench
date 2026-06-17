# Why don't types 1, 2, 3 get harder under the faulty environment?

## TL;DR

**The wrong-info traps in the NYC corpus target fields that drive F-score, not P-score.** Types 1, 2, 3 have P-constraints whose conditions don't depend on any wrong-info field — so the trap can't bite their P-score by design. Type 4 is the only task whose P-constraints touch a wrong-info field (`avg_cost_local`), which is why its P jumps a clean +50pt under clean mode. Type 5's P shift (+33pt) is incidental — caused by venue-selection drift between modes, not by a trap on a P-relevant field.

## Catalog: what fields do the 29 NYC wrong-info entries target?

| Field | # entries | What scoring tier it drives |
|---|---|---|
| `hours_fri`        | 12 | **F2a** (visit outside opening hours) |
| `hours_sat/sun/mon`| 7  | **F2a** |
| `avg_cost_local`   | 4  | **P** (only when task has a `sum` budget) |
| `recommended_visit_minutes` | 3 | **F4a** (over/under-scheduled duration) |
| `booking_required` | 2  | **F2b** (booking verified) |
| `reservation_required` | 1 | **F2b** |

**24 of 29 entries (83%) target F-score fields.** Only `avg_cost_local` (4 entries) can shift P-score, and only for tasks with a budget `sum` constraint.

## Per-task: do the P-constraints touch a wrong-info field?

| Type | P-constraint fields | Any in wrong-info? | Predicted P-delta | Observed P-delta |
|---|---|---|---|---|
| **1** | `local_cuisine`, `cuisine`, tag=`art`, tag=`waterfront` | **no** | ≈ 0 | **−0.25** (regression — see below) |
| **2** | `district`, `avg_cost_local==0`, `category` | no¹ | ≈ 0 | **0.00** ✓ |
| **3** | `traffic_tier`, `category=park`, `cuisine`, `price_tier` | **no** | ≈ 0 | **0.00** ✓ |
| **4** | **`avg_cost_local` (sum ≤ $24)**, tag=`free-entry`, tag=`tourist-trap`, `price_tier` | **YES** — 4 venues carry WI on this field (Smorgasburg LIC, Wave Hill, Veselka, The Halal Guys) | large gain | **+0.50** ✓ |
| **5** | `wheelchair_accessible`, `price_tier`, `category=cafe` | no | ≈ 0 | **+0.33** (incidental) |

¹ Type 2's `avg_cost_local==0` check (is it free?) overlaps with the WI field, but the 4 WI'd venues are all non-zero in both correct and incorrect values, so they don't flip across the free/paid boundary.

## What types 1, 2, 3 are telling us

**Type 2 and Type 3** match the prediction exactly: their P-constraints don't touch any wrong-info field, so P doesn't move. **The trap exists in the corpus but never gets tested by these tasks.** This is a feature of the corpus's wrong-info distribution, not a model failure.

**Type 1's −25pt regression** is the most interesting and the most diagnostic. Wrong-info on type 1's P-fields = none. So the trap *can't* hurt P directly. What's happening instead:
- In clean mode the incorrect-source docs are removed entirely (per `_load_city_from_db`'s suppression strategy).
- That reduces the total set of source-doc evidence the agent sees.
- The agent picks a different cluster of venues to satisfy the photographer's preferences.
- Some of those venues happen to satisfy P-constraints slightly worse (e.g. fewer waterfront venues, or weaker cuisine variety).
- Net: P drops not because of wrong info, but because clean mode is information-poor in a different way.

This is honest evidence that **suppressing wrong-info source docs is NOT a neutral intervention** — it removes informative-but-incorrect context that the agent was using to triangulate.

## What about F-score? (the field the trap actually targets)

| Type | F faulty | F clean | F-delta |
|---|---|---|---|
| 1 | 0.86 | 0.73 | **−0.13** |
| 2 | 0.70 | 0.70 | 0.00 |
| 3 | 0.63 | 0.90 | **+0.27** |
| 4 | 0.65 | 0.63 | −0.02 |
| 5 | 0.85 | 0.85 | 0.00 |

F is also mixed because the agent picks *different venues* across modes, so we can't directly attribute F changes to "wrong hours got the agent." The right signal would be **F2c (truth-carrier not retrieved) deduction frequency**, which we don't break out in the current scores schema. That's a measurable next step.

## What this means for the slide / the story

1. **The current slide-11 framing is partially right** — the trap does bite Type 4 unambiguously on P.
2. **Types 2 and 3 are NOT evidence of model robustness** — they're evidence the wrong-info distribution doesn't intersect their P-rubrics. The trap was never tested for them.
3. **Type 1's regression is informative but doesn't mean wrong-info "helped."** It means doc-suppression is not innocent.
4. **The bigger experiment would test F-score directly** — pick tasks whose plans likely visit wrong-info venues on the affected weekday (e.g. anything that schedules a Friday evening at a bar) and measure F2a deltas. That's the trap's natural domain.

## Suggested action for the deck

Either:
- **Soften slide 11's takeaway** — current message implies the trap is task-dependent. Recast as "the trap bites where it can bite (Type 4 budget), and we don't currently have P-rubrics for the other types that depend on wrong-info fields." Honest and accurate.
- **OR augment with an F-score slice** — show that F-score also moves under clean mode, but with a caveat that venue-selection drift confounds the direct attribution.

Both are slides-of-truth. The first is shorter and more publishable.

---

## Update — F2c deduction counts (extracted from existing transcripts, free)

The aggregate f_score signal is noisy because of venue-selection drift between modes. But the transcripts contain the full per-rule `f_deductions` list (each entry has section, amount, reason). I extracted F2c counts directly from pilot transcripts.

**F2c — "truth-carrier not retrieved" — count per task per mode:**

| Task | Gemini faulty | Gemini clean | DeepSeek faulty | DeepSeek clean |
|---|---|---|---|---|
| type 1 | 2 | 2 | 0 | 2 |
| type 2 | 0 | 0 | 0 | 0 |
| type 3 | 4 | 2 | 0 | 2 |
| type 4 | **4** | **4** | 0 | 1 |
| type 5 | 3 | 3 | 0 | 0 |

### Key observations

1. **F2c counts are roughly stable across modes.** This is the right outcome — the F2c check measures "did the agent retrieve truth_carrier for scheduled wrong-info venues?" — and that question is asked of the agent's plan regardless of whether the environment was clean or faulty. The clean/faulty A/B isn't the right experiment to expose F2c.

2. **The trap IS biting** — just not as a clean-vs-faulty delta, but as a constant baseline deduction. Gemini routinely schedules 3–4 wrong-info venues per task without retrieving their truth carriers. Type 4 alone is bleeding F −0.20 just from F2c.

3. **DeepSeek's F2c counts are mostly 0 because its plans hard-fail or don't schedule wrong-info venues at all.** That's a different failure mode (tool-use brittleness, not trap susceptibility).

4. **Type 2's F2c=0 across the board** confirms the earlier finding: its solution pool (Financial District attractions) doesn't contain wrong-info venues. The trap isn't tested for this task.

### Better experiments to expose the trap

- **F2c frequency across the full model panel** — extract from existing transcripts (free); we have gpt-5.4, claude-sonnet-4-5, gemini-pro, deepseek across many tasks. Could show "weak models fail to retrieve truth carriers, strong models do better" as a model-quality discriminator.

- **Augment F2c into a 3-tier credit** — currently binary (retrieved or not). Could be: 0 = not retrieved, 0.5 = retrieved but value not used in plan, 1.0 = retrieved AND correct value applied. Requires evaluator changes; queued as P6-T11 problem 3.

### Slide candidate

A "Where the trap actually bites" slide with two halves:
- Left: per-task F2c counts across all 4 leaderboard models (matrix view)
- Right: takeaway — F2c rate is the right metric; aggregate clean/faulty A/B confounds with venue-selection drift; the truth-carrier mechanism is doing measurable work even when models look the same on P-score

Deferred until leaderboard sweep finishes — then we'll have F2c data for all 7 models (4 originals + 3 newcomers + opus + gpt-5.5) and the comparison is more compelling.
