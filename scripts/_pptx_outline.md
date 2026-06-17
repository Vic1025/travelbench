# TravelBench deck — new outline (with real NYC content)

**Status:** Draft. Awaiting Vic signoff before building pptx.
**Data source:** NYC `test_70`, files mtime 2026-05-15 to 2026-05-25 (latest wave).

---

## ACT 1 — What is TravelBench

### Slide 1 — Title
Same as current. Hero "TravelBench". Subtitle. Meta: "NYC · 71 venues · 30+ tasks · 6 models".

### Slide 2 — Overview
Same as current slide 2 (now fitted within page bounds). Pipeline + 4+1 highlight cards.

---

## ACT 2 — The venue / city world (NEW)

### Slide 3 — City + venue resources overview (hybrid)
- **Top half — pipeline:** Anthropic plans 50 venue briefs → DeepSeek agent per venue with 11 tools → Overpass/Nominatim grounds geography → events + travel matrix + tickets enrich
- **Bottom half — NYC snapshot:** 71 venues, 237 source docs, 33 wrong-info pairs, 57 official-site pages, 4 seasonal windows (NYE, St Pat's, Independence Day, Thanksgiving). Category breakdown chips. Sample venue names.

### Slide 4 — Single venue: polished result
**Venue: Corkbuzz Chelsea Market** (wine bar, Chelsea Market, NYC)
- Yelp listing card (4.0★, 47 reviews, hours including the wrong Friday midnight)
- Forum post excerpt: NYC_Emily84 (2025-02-18) — "they close at 10pm now, not midnight like it used to be. Some old blog posts and the Yelp listing still say later but they tightened up after the pandemic."
- Official-site card with corrected Friday hours 12:00–22:00
- Hook label at bottom: "How did this get built?"

### Slides 5–9 — Single-venue generation sandbox (5 steps, compressed from 7)
Layout: persistent left sidebar = the 11 venue-agent tools (CREATE_PAGE, FILL, COMMIT, ADD_WRONG_INFO, REGISTER_DOC_REFS, VERIFY, …). Right pane = current step.

- **Step 1 — Plan the trap (THINK)**
  Agent reasoning: "wrong info planned: YES. traffic_tier=low allows wrong info. Will seed Yelp with old midnight hours (last_activity 2021), then introduce a 2025 forum post that corrects it. Temporal_decay is the natural pattern — post-pandemic hours cuts are everywhere."

- **Step 2 — Venue row + Yelp listing**
  CREATE_PAGE → FILL venue M4uBkLL with canonical fields (Friday 12:00–22:00).
  CREATE_PAGE yelp_listing → FILL with **incorrect** Friday "12:00–0:00" + last_activity 2021-03-15 (the staleness signal). COMMIT both.

- **Step 3 — Official site = ground truth carrier**
  CREATE_PAGE official_site_doc → FILL with corrected hours + venue copy + regulations (not wheelchair accessible, no photography, pet-friendly indoor patio). COMMIT.

- **Step 4 — Source docs + atomic wrong-info wiring**
  Two source docs created:
    - Blog (doc H5l6EyK5, 2021): tourist Maggie Collins says "open until midnight on Fridays" — the incorrect_source
    - Forum (doc L1QYEAH5, 2025): NYC_Emily84 corrects naturally inside her reply — the truth_carrier
  ADD_WRONG_INFO(M4uBkLL, hours_fri, "12:00-0:00", "12:00-22:00", source_type=yelp, category=temporal_decay, **incorrect_source_doc_id**=H5l6EyK5, **truth_carrier_doc_id**=L1QYEAH5)
  → wrong_info row + both doc_venue_roles written atomically (P6-T22 fix).

- **Step 5 — VERIFY + SUBMIT**
  11 checks: venue structure ✓ · tag visibility (hidden tags all surface in docs) ✓ · regulation visibility ✓ · wrong info pairing ✓ · truth_carrier_registered ✓ · incorrect_source_registered ✓ · doc count ≥3 ✓ · SUBMIT.

### Slide 10 — Truth-carrier mechanism (NEW)
After-the-fact explanation page.
- **The pair:** every wrong_info entry must register two docs — `incorrect_source` (where the agent will trip) and `truth_carrier` (where the agent must look to recover).
- **Atomic write:** ADD_WRONG_INFO commits all three tables in one transaction (P6-T22; previously 21% of wrong_info entries orphaned on NYC test_50).
- **Why it matters:**
  - Forces the corpus to *contain* the correction — no unanswerable wrong info.
  - F-score F2c penalizes models that don't retrieve the carrier doc when scheduling a wrong-info venue (−0.05/venue).
  - Distinguishes "I trusted the first source" from "I cross-checked".
- **4 wrong-info categories:** temporal_decay (Corkbuzz: old hours), propagation_error (a wrong fact echoed across sources), conditional (right under one assumption, wrong under another), subjective (a claim that depends on whose review you read).

### Slide 11 — Ground-truth vs faulty-environment comparison (NEW, conditional)
**This slide is gated on the pilot experiment running successfully.** Plan:
- Build `--clean-environment` flag on `run_benchmark.py`: at dispatch time, substitute `correct_value` for any wrong_info field in tool responses.
- Pilot: 3 models × 5 NYC tasks × {clean, faulty} = 30 runs.
- If P/F delta is meaningful (e.g. ≥ 8pt), full slide showing per-model bars.
- If delta is tiny, reframe as "models already handle the noise well — here's our F2c-deduction frequency analysis from existing data".

Estimated cost: CAD 25–40 (per project_api_budget memory's 5× multiplier).

---

## ACT 3 — The task side

### Slide 12 — P-score engine + real-life examples (NEW)
- **One-paragraph intro:** the engine takes a query phrase + constraint primitive + scope and evaluates whether a submitted plan satisfies that preference. Primitives: at_least, at_most, ratio, count_distinct, sum, exactly, at_most_distinct, all/none.
- **Three real NYC examples:**
  - "explore authentic local restaurants" → `ratio: 0.6 scope=activity_type=meal condition: local_cuisine=1`
  - "different cuisines" → `count_distinct: 3 field=cuisine scope=category=restaurant`
  - "balance both our styles" → `at_most: 1 scope=per_day condition: recommended_pace=intense`
- Why these matter: each phrase can't be expressed by `at_least` without losing the meaning. The engine is doing semantic work, not just inclusion checks.

### Slide 13 — Six structural types with current best NYC examples
Six cards, each replacing the placeholder quote with the real best NYC task (May 21 wave):
| Type | Best example | Highlight |
|---|---|---|
| 1 | Architecture photographer, July 4 weekend, 3 days | ratio + count_distinct + 2 at_least |
| 2 | Sat afternoon 14:00–15:30 in Financial District, free-majority | scope=district + ratio 0.5 free |
| 3 | "My partner insists on famous, I prefer hidden" + park/day + cuisine variety | 4 P-constraints, real tension |
| 4 | "$25/day, free visits maximiser, ≤1 museum/day" | sum + all + at_most stacked |
| 5 | Wheelchair + budget + cafe-lover | two `all` stacked filters → tiny pool |
| 6 | Calm July 4 trip, avoid waterfront-on-fireworks, avoid rooftop | `none` aggregations + time_window |
Each card shows: query (short), 1-line constraint summary, measured P-score from scores.db.

### Slide 14 — TravelBench vs other paper datasets (NEW, citation page)
Three short comparisons sourced from `docs/EXTERNAL_REFERENCES.md`:
- vs **TravelPlanner (Xie et al., 2402.01622):** their constraints are mostly hard filters (hotel rating ≥X, budget ≤Y). Ours adds soft preference primitives (ratio, count_distinct) — different difficulty axis.
- vs **DeepPlanning (Alibaba Qwen, 2601.18137):** they emphasise local/global constraint split. Our `ratio` constraints are exactly the "global" dimension they highlight; `at_least`/`at_most` cover local.
- vs **τ²-Bench (Sierra, 2506.07982):** compositional task generation. Ours follows the same pattern but adds wrong-info source-doc layer they don't have.

### Slides 15–21 — Task generation sandbox (7 steps, EXISTING flow, swapped example)
Use the Type 4 NYC task `claude_sonnet_4_5_new_york_type4_20260521_1779375721.json` (the $25/day budget maximiser). The existing 7-step trajectory (HELP → THINK persona → query_pool scan → query_pool upscale → SUBMIT fail → SUBMIT fail → SUBMIT ✅) can be re-instantiated against this task — same structure, real NYC content.

---

## ACT 4 — Is the task solvable?

### Slide 22 — Opener: "How do we verify at least one valid plan exists?" (NEW)
- Big question on the slide.
- Three answers stacked: (a) hard constraint check — does any venue satisfy each `all`-aggregated filter? (b) tension partner check — do the two P-constraints in tension still have intersecting feasible plans? (c) **upper-bar verification** — agent must submit a candidate plan that passes the solvability check before the task is published.
- Closes with: "A×A and B×B are how we check (a) and (b) cheaply."

### Slide 23 — What A×A and B×B mean, why those two are enough (NEW)
- A-constraints = pool-filter primitives (any aggregation that produces a venue set: `all`, `at_least N has_tag=X`).
- B-constraints = plan-structure primitives (counting / spread across venues, not filtering the pool: `ratio`, `count_distinct`, `at_most N scope=per_day`).
- **A×A test:** for each pair of A-constraints, do their venue sets overlap enough that the plan can satisfy both with the same selections? (low overlap → tension)
- **B×B test:** for each pair of B-constraints, does their independence-product narrowing collapse the valid-plan count below threshold?
- Why these two are sufficient: any pair of P-constraints belongs to one of these classes (or is trivially compatible — covered by the upper-bar verifier).

### Slide 24 — A×A detailed example
Pull a real Type 3 NYC pair from the May 21 file: `traffic_tier=high (4 at_least)` vs `traffic_tier=low (4 at_least)`. Show the Venn-style diagram (low overlap = real tension). Formula box: tension iff overlap ≤ 3 OR overlap / min(|A|,|B|) ≤ 0.25. Worked numbers from NYC pool.

### Slide 25 — B×B detailed example
Pull a real Type 4 NYC pair: `sum(cost) ≤ $25 per_day` × `at_most: 1 museum per_day`. Show stacked-narrowing bar chart with real plan-count numbers. Formula box: tension iff r_A × r_B ≤ 0.10 AND valid_count ≤ 2000 × days.

---

## ACT 5 — Evaluation

### Slide 26 — Eval sandbox: what C/F/P check (NEW expansion of current scoring slide)
Same three-tier card layout but with more depth per tier:
- **C (process):** BFCL-style deduction. Two hard gates (zero tool calls, no plan tag). Section A per-call AST deductions. Section C timetable format. Section D blank-time coverage.
- **F (feasibility):** P22-F deduction-based. F1a overlap, F1b travel infeasible, F2a hours, F2b ticket sold-out, F2c truth-carrier not retrieved (callback to slide 10), F2d event capacity, F3b buffer tiers, F4a visit duration.
- **P (preference):** generic constraint engine (callback to slide 12) + LLM-judge pathway for semantic constraints.

### Slides 27–32 — Solving sandbox: GPT vs Gemini paired by phase (KEEP existing 14–19)
Refresh the data — re-anchor on a current NYC Type 1 task (the architecture photographer), pick the actual best and worst model from scores.db, walk through their tool traces phase-by-phase.

### Slide 33 — Leaderboard
Refresh from scores.db. **Caveat:** current scores.db only has 6 models × ~12 tasks. The previous "16 tasks per model" table was overstated. Need to either re-run the missing models or honestly show smaller N.

---

## ACT 6 — Where next

### Slide 34 — Three future directions (NEW)

**(a) Long-horizon: workflow agency**
Testing the agent's ability to build, maintain, and refine its own workflow across many tasks. Does it remember which sources it learned to trust? Does it save useful intermediate results (a venue's verified hours, a district's wheelchair patterns) and reach for them in later tasks? The instinct to maintain useful resources is the metric, not raw per-task accuracy.

**(b) Real-API transfer: ground-truth at scale**
Same methodology, but the environment is a real web/API stack (Google Places, Yelp public, Foursquare, OpenStreetMap). Open problem: how do we get ground truth? Three sub-paths: (i) cross-source agreement majority vote; (ii) curated authoritative source (e.g. official municipal data); (iii) human-in-the-loop verification on a subset, then propagate trust.

**(c) Generalize beyond travel**
The same methodology — hidden ground truth + faulty source corpus + structured constraint engine + multi-tier scoring — applies to any consumer decision under noisy information:
  - Small: which shampoo to pick, how to lay out a weekly time schedule
  - Medium: which laptop to buy, what doctor to see
  - Large: which school to apply to, which job to take

The travel domain is a test bed; the framework is a decision-evaluation pattern.

---

## Slide count: **34 slides**

(was 22)

---

## Build plan once outline confirmed

1. Wire the new content into `scripts/html_to_pptx.py` — add new slide functions, swap data, drop slide_multi_principal.
2. **In parallel** (separate agent): build `--clean-environment` flag in `run_benchmark.py` + run the pilot.
3. After pilot returns: fill in slide 11 with real numbers.
4. Regenerate pptx. Sanity check.

Locked decisions:
- Slide 11: ship even if weak. Will take time (new flag + tests + pilot run). Report result honestly.
- Slide 33: re-run with more models + tasks against latest evaluator (post-F2c-fix). Cost question still open.
- Slide 14: compare against DeepPlanning (2601.18137) + TravelBench-Cheng (2512.22673) + VitaBench (2509.26490). All in `docs/travelbench_paper/`.
