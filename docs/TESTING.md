# TravelBench — Testing Instruction Sheet

Everything you can run today, in order from simplest to most complete.
Each section lists: what it tests, what you need, the exact command, and what a passing result looks like.

---

## 1. Environment check

**Tests:** Python dependencies are installed and data files are present.  
**Needs:** Nothing. No API key.

```bash
cd travelbench/
python3 -c "import anthropic, json, pathlib; print('deps OK')"
python3 scripts/generate_sources.py --stub
```

**Passing:** Prints `deps OK` then `✓ Stub data ready.`  
**If it fails:** Run `pip install anthropic` then retry.

---

## 2. Regression tests

**Tests:** The three core scoring scenarios that must pass after every code change.  
Scenario 1 — a perfect agent scores 100/100/100.  
Scenario 2 — scheduling outside opening hours gives F = 0%.  
Scenario 3 — a plan that violates romantic/vegan/fine-dining prefs scores P ≤ 45%.  
**Needs:** No API key. All scoring is deterministic (no LLM calls).

```bash
python3 scripts/regression_test.py
python3 scripts/regression_test.py --verbose   # shows each assertion
```

**Passing:** `✅ All regression tests passed.`  
**If any fail:** Do not proceed. The evaluator has a bug — check the last code change.

---

## 3. Demo run (no API key)

**Tests:** The full data pipeline end-to-end: stub generation → agent stub → C/F scoring.  
P-score code checks run. LLM judge is skipped (no API key, prints "pending").  
**Needs:** No API key.

```bash
python3 scripts/run_demo.py
```

**Passing:** Prints a result table with C-score, F-score, P-score (LLM portions show "pending"), composite score.  
**What to check:** C and F scores should be non-zero. No Python exceptions.

---

## 4. LLM judge test suite ← **Run this with your API key**

**Tests:** Whether the LLM judge correctly detects semantic preference violations.  
10 hand-crafted (user input, schedule) pairs, 5 constraint types, ~50% pass / 50% fail plans.  
Includes 3 adversarial cases designed to trip up a shallow judge.  
**Needs:** `ANTHROPIC_API_KEY`

```bash
python3 scripts/test_llm_judge.py --api-key YOUR_KEY
python3 scripts/test_llm_judge.py --api-key YOUR_KEY --verbose    # shows rationale + expected
python3 scripts/test_llm_judge.py --api-key YOUR_KEY --single 4  # run just test 4
```

**What each test checks:**

| # | Constraint type | Plan | Expected | Difficulty |
|---|----------------|------|----------|------------|
| 1 | Romantic coherence | Rooftop, fine dining, garden — all romantic | 1.0 | Easy |
| 2 | Romantic coherence | Crowded museums, casual meals, packed schedule | 0.0 | Easy |
| 3 | Local hidden gems | Hidden-gem and locals-fav venues throughout | 1.0 | Easy |
| 4 | Local hidden gems | Iconic/crowded venues — notes admit "well-known" | 0.0 | **Adversarial** — judge may give 0.5 |
| 5 | Nightlife focus | Cocktail bars, rooftop, late dinner — all evening | 1.0 | Easy |
| 6 | Nightlife focus | Daytime museums, ends by 17:30, "early starts" | 0.0 | Easy |
| 7 | Outdoor preference | Canal, parks, open-air market throughout | 1.0 | Easy |
| 8 | Outdoor preference | 1 token park among 5 indoor venues | 0.0 | **Adversarial** — judge may give 0.5 |
| 9 | Relaxed pace | 5-minute gaps between 5 activities, "tight transitions" | 0.0 | Easy |
| 10 | Architecture tour | 2 arch venues (rushed) + 3 unrelated venues | 0.5 | **Adversarial** — genuinely ambiguous |

**Verdict logic:**
- `✅ PASS` — judge matched expected exactly
- `⚠ SOFT` — judge was one grade off (0.5 vs 0.0 or 0.5 vs 1.0)
- `❌ FAIL` — judge was fully wrong (0.0 vs 1.0 or vice versa)

**What to watch for:**
- T04: judge might give 0.5 (sees "architecture" label on Pompidoux, reads charitably). If so, tighten the `judge_prompt_hint` for local_gems.
- T08: judge might give 0.5 (hint says "unless unavoidable", judge treats museums as culturally unavoidable). If so, remove "unless unavoidable" qualifier.
- T10: judge should land between 0.0–0.5 seeing the time stamps (09:30–10:15 = rushed). If it scores 1.0 it's ignoring timing entirely.

**If you get ≥ 7/10 pass:** Judge is calibrated well enough to use.  
**If you get < 6/10 pass:** Review the FAIL cases and adjust `judge_prompt_hint` fields in the relevant task JSON files.

---

## 5. Single agent run (full pipeline)

**Tests:** A real LLM agent planning a trip end-to-end: tool calls, plan generation, and full C/F/P scoring.  
**Needs:** `ANTHROPIC_API_KEY`

```bash
# Start with an easy task
python3 agents/runner.py --task par_easy_001 --api-key YOUR_KEY

# A medium task with cross-day reasoning
python3 agents/runner.py --task par_medium_001 --api-key YOUR_KEY

# The hardest task (photographer, no tourist traps, must-visit Sainte-Chapelle)
python3 agents/runner.py --task par_hard_001 --api-key YOUR_KEY
```

**What gets saved:** `results/par_easy_001__claude-sonnet-XXXX.json` — full tool call log + parsed plan.

**Then evaluate the saved result:**
```bash
python3 eval/evaluator.py --results-dir results/ --api-key YOUR_KEY
python3 eval/evaluator.py --results-dir results/ --api-key YOUR_KEY --summary
```

**What to look for in the result JSON:**
- `tool_call_log` — did the agent call all 4 tools? Did any calls have missing `city` param?
- `parsed_plan.days[].activities` — are all venue_ids real? Do times make sense?
- C-score issues list — which checks fired?
- F-score — any hard fails? Which one?
- P-score code_results — which patterns passed/failed?

---

## 6. Full demo with LLM judging

**Tests:** All 10 tasks run sequentially, fully scored including semantic P-score.  
**Needs:** `ANTHROPIC_API_KEY`

```bash
python3 scripts/run_demo.py --api-key YOUR_KEY

# With fast-fail enabled (skips P-score if F < 0.4)
python3 scripts/run_demo.py --api-key YOUR_KEY --c-threshold 0.5 --f-threshold 0.4

# With all constraints sent to LLM (slower but useful for calibration)
python3 scripts/run_demo.py --api-key YOUR_KEY --judge-method llm_all
```

**Passing:** All 10 tasks complete, summary table printed, results saved to `results/`.

---

## 7. Evaluator-only run (score a pre-saved result)

**Tests:** Useful for re-scoring an existing result after changing evaluator logic,
or for scoring a result produced by a different model.  
**Needs:** At least one `.json` file in `results/`. API key only if P-score has semantic constraints.

```bash
# Score all saved results
python3 eval/evaluator.py --results-dir results/ --api-key YOUR_KEY

# Score a single result file
python3 eval/evaluator.py --result results/par_easy_001__claude-sonnet-XXXX.json --api-key YOUR_KEY

# Code-only scoring (no LLM, no API key)
python3 eval/evaluator.py --results-dir results/ --no-llm
```

---

## Known limitations to be aware of when testing

**Split-service hours (par_r04 Saturday):** La Tabla Ibérica has a lunch window (12:00–15:00) and a dinner window (19:00–23:30) on Saturdays. The evaluator currently treats Saturday as a single window. A plan scheduling par_r04 at 16:00 Saturday will not be flagged as outside hours. This is a known gap — logged in TODO.

**P-score with no API key:** Code-pattern checks (time_threshold, label_required, etc.) run without an API key. Semantic constraints show as `pending` with no score. The composite score is re-normalised to exclude them.

**Judge timing:** Each API call for a semantic constraint costs 1–3 seconds. 10 tasks × 1–2 semantic constraints each = 15–25 seconds of LLM judge time per full run. This is normal.

**Stale hours traps:** Four venues have intentionally wrong hours on Yelp. If the agent's plan uses these venues and scores F = 0%, that's the benchmark working correctly, not a bug:
- par_r01 Brasserie Voltaire — Yelp says closes 22:00 Friday, actually 23:00
- par_s01 Musée de l'Imaginaire — Yelp says closes 18:00 Saturday, actually 19:00
- par_s02 Galerie du Temps — Yelp says closes 17:30 Friday, actually 18:00  
- par_r04 La Tabla Ibérica — Yelp misses the Saturday split service entirely

**Sold-out ticket trap:** par_s02 is sold out on 2025-03-08 (Saturday). The agent must call `get_official_site` to discover this. A plan including par_s02 on that date gets F = 0%.

---

## Scoring reference

| Score | Weight | Hard fail? | What it measures |
|-------|--------|-----------|-----------------|
| C-score | 0.25 | No — cumulative deductions | Did the agent use tools correctly? |
| F-score | 0.45 | Yes — any hard fail → 0.0 | Is the itinerary physically achievable? |
| P-score | 0.30 | No | Does it satisfy the user's stated preferences? |
| Composite | — | — | `C×0.25 + F×0.45 + P×0.30` |

| Composite | What it means |
|-----------|--------------|
| 90–100% | Excellent — correct, verified, personalised |
| 75–89% | Good — one class of failure |
| 55–74% | Partial — significant issues in one tier |
| 35–54% | Poor — fundamental process or feasibility failure |
| 0–34% | Failing — plan is not usable |

---

## Sprint 1 — source_required (added)

`source_required` field on personal_constraints forces agents to consult a specific
source type before credit is awarded. If the plan is correct but the source was never
consulted, the constraint score is halved (lucky guess penalty).

| Constraint | Task | source_required |
|---|---|---|
| vegan label_required | par_hard_002 pref_001 | blogs_and_forums |
| vegetarian label_required | par_medium_001 pref_003 | blogs_and_forums |
| halal label_required | par_medium_004 pref_001 | blogs_and_forums |
| photography_allowed regulation | par_hard_001 pref_001 | official_site |
| wheelchair_accessible regulation | par_easy_002 pref_002 | official_site |

**S6** — Agent produces correct vegan plan but never calls `search_blogs_and_forums`.
Expected: P-score < unsourced-perfect; `source_verified=False` on pref_001.

**Source index** is built at evaluator load from `data/sources/{city}/blogs_and_forums/`
and `data/sources/{city}/official_sites/`. Cached per city in `_source_index_cache`.

---

## Sprint 2 — Weather system (added)

New venue field: `outdoor_sensitivity: "none" | "partial" | "outdoor_only"`.
New venue field: `covered: bool` (for partial/outdoor_only venues).
New ground truth: `data/ground_truth/paris/weather_forecast.json`.
New tool: `get_weather_forecast(city, date)` — already in mock_tools.py.
New P-score pattern: `weather_aware`.

| Venue | outdoor_sensitivity | covered |
|---|---|---|
| par_s04 Jardin des Soupirs | outdoor_only | false |
| par_s06 Promenade des Canaux | outdoor_only | false |
| par_s10 Marché aux Puces | outdoor_only | false |
| par_r03 Le Comptoir du Nord | partial | false |
| par_r08 Marché Vivant Food Hall | partial | true |
| par_r10 Le Rooftop 360 | partial | true |

**Weather dates:** 2025-03-07 sunny | 2025-03-08 rainy | 2025-03-09 partly_cloudy |
2025-03-10 stormy | 2025-03-11 cloudy | 2025-03-12 sunny

**F-score weather rules:**
- outdoor_only + uncovered on rainy/stormy → hard fail
- outdoor_only + uncovered on cloudy → soft (0.5)
- partial + uncovered on rainy → soft (0.0)
- partial + covered on stormy → soft (0.5)

**C-score warning:** ≥2 outdoor_only venues in plan and no `get_weather_forecast` call.

**Task par_medium_005:** 2-day trip starting rainy Saturday (2025-03-08).
Agent must call weather forecast and avoid outdoor venues on day 1.

**S7** — Good agent: rainy day 1 = indoor venues, sunny day 2 = outdoor OK. F=100%, P=100%.
**S8** — Bad agent: outdoor-only venues on rainy day, no weather check. F=0%, P low.
**S9** — Indoor plan on rainy day 1 with outdoor on sunny day 2. F=100%, P=100%.
