# TravelBench — Phase 4 TODO

> ⚠ **Archived phase TODO.** Content below preserved for historical record.
> **Current source of truth:** [docs/TODO_PHASE6.md](TODO_PHASE6.md) (Phase 6 work tracker).
> **Phase 7 candidates:** [docs/PHASE7_FUTURE_WORK.md](PHASE7_FUTURE_WORK.md).
*Quality improvements and parallelism for the first end-to-end scaling runs.*
*Each item has a dedicated subtask doc in `docs/subtasks/` with full detail and live status.*

Status key: ✅ done  🔄 in progress  ⬜ not started

---

## Context

Phase 3 ended with a working 50-venue London generation + 5-model task generation pipeline. Two test_50 runs surfaced concrete quality and debugging gaps:
- Tag coverage validation undercounted by 80%+ because the venue agent writes tags inconsistently (`free_entry` vs `free-entry` vs `free entry`).
- Low-traffic venues and bar-category venues are severely underrepresented in planning output.
- Restricted regulations (not-wheelchair-accessible, no-photography, dress codes) rarely appear in source doc bodies, so agents can't discover them.
- DeepSeek-chat and other non-reasoner models often reply with prose instead of calling SUBMIT, causing the loop to silently give up on turn 2 of 20.
- When failures happen, there's no record of what the agent actually did — no way to diagnose a mid-run `'int' is not iterable` crash without re-running.
- Task generation runs sequentially model-by-model; a 120-task run (5 models × 4 windows × 6 types) takes hours when it could parallelize trivially across providers.

Phase 4 addresses these five concerns as independent workstreams.

---

## P1 — Tag format consistency ✅
**Subtask doc:** `docs/subtasks/P1_tag_format_consistency.md`

**What:** Tags canonicalised to hyphenated kebab-case (`free-entry`, `wheelchair-accessible`) at three enforcement points:
1. `tool_SET_TAGS` in `agent_tools.py` — normalises at write time via `s.strip().lower().replace("_","-").replace(" ","-")`
2. `_apply_pool_filters` in `pool_utils.py` — normalises both sides of tag comparisons (defence for legacy data)
3. `constraint_engine.py` — normalises `has_tag` condition checks and `has_tag=` scope matches
Planning prompt in `generate_city_venues.py` updated: "use hyphens only — never underscores or spaces".
Canonical form decision documented in `DESIGN_DECISIONS.md`.

**Status:** ✅ Complete. 611/611 tests passing.

---

## P2 — Venue diversity and distribution ✅
**Subtask doc:** `docs/subtasks/P2_low_tier_and_bar_underrepresentation.md`

**What:** Venue generation skewed toward high/mid traffic, underrepresented bars, and
concentrated geographically (e.g. London had 0 venues in Westminster, City of London,
Kensington). Root cause: narrow district list + biased high-traffic examples in prompt.

**Completed:**
1. **District coverage** (via P17): `research_city.py` prompt now requires "FULL geographic
   spread — tourist areas, landmark zones, food/arts areas, residential." London stub
   expanded from 10 to 14 districts (Westminster, City of London, Kensington, Hackney added).
2. **High-traffic tier rewritten**: "globally iconic venues — the ones every visitor knows
   by name... landmark monuments, famous museums, iconic public spaces" replaces the old
   South Bank-biased examples.
3. **venue_archetypes removed**: dead field that was never injected into the planning prompt.
   Replaced by `city_interests` as the cultural context signal.
4. **Prompt enforcement**: `~` soft targets replaced with "exactly N" hard counts.
5. **Tier rebalancing** (safety net): mid→low downgrade when LLM under-produces low-tier.
6. **Bar rebalancing removed**: the cafe→bar conversion hack was wrong (relabelling a cafe
   doesn't make it a bar). With proper district coverage (Soho, Shoreditch, Hackney), bars
   appear naturally at the right frequency.
7. **Regulation diversity** in prompt: 40-60% coverage targets with character hint examples.
8. **Per-city configs**: rejected — prompt-level fixes work for any city without pre-generated
   per-city distribution targets.

**Remaining:** None — confirmed by live test runs (n=50 and n=60) that the prompt-level
fixes produce correct tier/district distributions without any rebalancing. validate_city
distribution audit is optional backstop for future cities; not blocking.

**Status:** ✅ Complete. Confirmed by test: n=50 produces perfect 13/21/16 tier split,
Westminster 6 venues, City of London 5, bars 4-8 naturally.

---

## P3 — Regulation visibility in source docs 📝
**Subtask doc:** `docs/subtasks/P3_regulation_visibility.md`

**What:** Only 19/65 restricted regulations in the test_50 pool appear in source doc bodies.
If a venue is not wheelchair-accessible, source docs should hint at it (stairs, narrow entry,
basement-level seating). Currently the restriction is encoded only in the venue DB — agents
can't discover it through documents.

**Why it matters:** The benchmark tests whether agents can synthesize information across
documents. A wheelchair-using persona needs to *discover* which venues are accessible by
reading docs, not by querying a hidden regulation field.

**Complexity assessment:** Simple keyword matching won't work — "intimate basement wine bar
with spiral staircase" implies wheelchair_accessible=0, but "basement-level gallery with
full lift access" does not. This is a context-dependent judgment that requires either:
(a) LLM-based verification at VERIFY time (expensive, adds an LLM call per venue), or
(b) Requiring the venue agent to explicitly flag which regulations are hinted at in each
    doc via `mentioned_regulations` (structured, but the agent might not produce natural hints).
Design not yet settled.

**Status:** 📝 Problem well-understood, implementation blocked on design decision.

---

## P4 — Agent failure diagnostics + turn exhaustion fix ✅
**Subtask doc:** `docs/subtasks/P4_agent_failure_diagnostics.md`

**What:** Twelve related problems surfaced across five iterative live-run cycles, all landed:

1. *Misleading "exhausted turns" message.* All three provider loops now track `stop_reason` and `turns_used` explicitly. ✅

2. *Silent termination on prose-only responses.* Loops now nudge instead of breaking; give up only after 2 consecutive prose-only turns. ✅

3. *Agent transcript logging on failure.* Every failed agent run dumps a JSON transcript with full per-turn tool calls, results, and nudge triggers. Log path surfaced inline in failure output. ✅

4. *Handbook + system prompt clarity.* Prominent "use tool calls, not prose" section, worked SUBMIT example. ✅

5. *Constraint schema knowledge gap (first live run).* Agent was inventing pattern names. System prompt now inlines the 7 valid pattern values with params shapes; `MAX_TURNS` raised 15 → 20. ✅

6. *Superseded: `'int' is not iterable` crash.* Ultimately fixed in items 8 and 12. ✅

7. *Pool-filter AND semantics (second live run).* "HOW CONSTRAINTS APPLY" section with ✗/✓ examples; type 3 protocol tightened. ✅

8. *Defensive type guards in constraint processors (third live run).* Guards in `_query_pool`, `_apply_pool_filters`, `_get_constraint_cluster`, `_get_main_trait_key`, `_venues_passing`. ✅

9. *B-score constraint schema gap (third live run).* New "B-SCORE CONSTRAINT SCHEMA" section with worked example; "CHARACTER TRAIT RULES" colocated with schema. ✅

10. *Turn-budget strategy (third live run).* Explicit three-phase budget (explore/draft/fix); `URGENCY_TURNS` raised 5 → 8. ✅

11. *Dispatch + API error guards (fourth live run).* Two layers of defensive try/except in all three provider loops: around `_dispatch_task_tool` (tool crashes become `tool_error` results the agent can adapt to) and around API client calls (network errors become `api_error` transcript entries). Every failure mode now produces a transcript. ✅

12. *Root-cause `'int' is not iterable` + protocol tightening (fifth live run).* Transcripts finally captured the full traceback for all 8 int-crash failures across runs 1-4: `"type5" in structural_type` raises TypeError when LLM submits `structural_type: 5` as an integer. Fixed by coercing to string. Also tightened type 4 protocol (no budget_ceiling pattern exists — budget goes in query text, cost tension needs two opposing pool-filters) and added a city-agnostic verification workflow (query → narrow → intersect → compare min_count) the agent should run before every SUBMIT. Deliberately did NOT bake city-specific "safe/dangerous combo" lists into the prompt — those would overfit to one city's pool. ✅

**Why it matters:** Fifth run was the payoff for items 1-11 — every failure produced a transcript, and the transcripts pointed at a single one-line bug + two prompt-level refinements. Without the infrastructure built across runs 1-4, finding the `"type5" in 5` typo would have required reading code, not logs. The sequential discovery itself demonstrates that the diagnostic loop works.

**Status:** ✅ complete — 12 subitems landed, 380/380 tests still passing. Fifth run: 8/24 tasks succeeded (33%). Sixth run expected: 15-20/24 (~65-80%).

---

## P5 — Model-level parallelism for task generation ✅
**Subtask doc:** `docs/subtasks/P5_model_parallelism.md`

**What:** The per-window model loop (`for model in models_to_run:`) was sequential. Each model calls a different provider (Anthropic, OpenAI, Google, DeepSeek) so rate limits don't contend. Wrapped in `concurrent.futures.ThreadPoolExecutor` with `threading.Lock()` around shared-state mutations (`used_counts`, `registry`, `save_registry`). Per-model output buffered via `StringIO` and flushed in completion order to keep logs readable.

**Landed:**
- `_run_models_for_window` refactored with inner `_run_one_model` closure returning `(model, result, captured_output)`
- `state_lock` (per-window `threading.Lock()`) protects the `used_counts` snapshot at entry and the post-generation registry + counter mutations
- `--parallel-models` (default on) and `--no-parallel-models` (debug opt-out) CLI flags
- Dry-run verified on 5 stub models × 4 windows — no race conditions, output blocks stay contiguous per model

**Why it matters:** A 120-task multi-model run that took ~30 min now takes ~6-8 min. Iteration on SOTA model comparisons (Opus / GPT-5 / Gemini 2.5 Pro / Sonnet 4.5 / DeepSeek) becomes practical.

**Status:** ✅ complete — 415/415 tests passing, dry-run both modes verified.

---

## P6 — Validation logic audit against task_structural_types.md ✅
**Subtask doc:** `docs/subtasks/P6_validation_logic_audit.md`

**What:** Full audit of every validation rule in `validate_task_schema` and `_verify_task_solvable`
against the "Validity guarantee" clauses in `docs/task_structural_types.md`.

**All items resolved:**
1. ✅ Tension-check exemption: type 2 added to exempt set
2. ✅ Pool-size check exemption: type 2 added
3. ✅ E3 tests: updated during engine migration — exemption tests verify all 4 exempt types
   (type 1/2/4/6) pass pool-size checks that type 3/5 correctly fail
4. ✅ Type 2 time-budget solvability: implemented in P7
5. ✅ Type 4 budget-feasibility: implemented in P7
6. ✅ All validation logic rebuilt on engine primitives (engine migration Phases 1-5)

**Status:** ✅ Complete.

---

## P8 — Currency consistency rename (local currency throughout) ✅
**Subtask doc:** `docs/subtasks/P8_currency_consistency.md`

**What:** Renamed all cost columns to drop the `_usd` suffix: `avg_cost_usd → avg_cost_local`, `lunch_cost_usd → lunch_cost_local`, `dinner_cost_usd → dinner_cost_local`, `price_usd → price_local`, `estimated_cost_usd → estimated_cost_local`. 31 files touched across `scripts/`, `eval/`, `server/`, and all E-tests. Added `CITY_CURRENCY` registry (21 cities mapped to ISO codes) plus `get_city_currency(city)` and `format_money(amount, city)` helpers in `pool_utils.py`. Updated handbook prompts to tell the venue-gen agent to use local currency without conversion.

**Why it matters:** The `_usd`-labelled columns were being populated with local-currency values in practice (Sketch stored as `$250` was really ~£180-£225 London pricing), so the naming lied. P7 depends on this — budget-feasibility comparisons are cleaner with a single canonical currency per city.

**Status:** ✅ complete — 390/390 tests pass. P7 now unblocked.

---

## P7 — Type 2 time-ceiling & Type 4 budget-ceiling solvability checks ✅
**Subtask doc:** `docs/subtasks/P7_time_and_budget_feasibility.md`

**What:** Both solvability gaps from the P6 audit now have concrete numeric feasibility checks with a uniform 1.1–1.3× difficulty band.

**Landed:**
1. Schema field `public_input.query_resources = {time_ceiling_minutes, budget_per_day}` — hard schema failure if missing on type2/type4 tasks respectively (budget in local currency)
2. `query_pool` opt-in `include=["price","duration","coords"]` parameter — agent can pull `avg_cost_local`, `recommended_visit_minutes`, and `lat/lng/district` in one call instead of per-venue roundtrips
3. Type 2 algorithm: K-nearest venues, 50% of `recommended_visit_minutes` + nearest-neighbour travel, 1.1–1.3× ratio band, 1-day cap
4. Type 4 algorithm: cheapest `RESTAURANTS_PER_DAY × days + SITES_PER_DAY × days` from filtered pool by `avg_cost_local`, plus premium add for `required_venue_ids` above 1.5× their category median, same 1.1–1.3× band, error messages via `format_money(city)`
5. Type 2 & Type 4 reasoning protocols updated with step-0 `query_resources` requirement and `include=[...]` recommendations
6. Handbook `-h tools` entry documents `include` parameter with worked examples
7. 25 new E3 tests covering all P7 behavior; 1 E2 test updated to use new schema

**Status:** ✅ complete — 415/415 tests passing.

---

## Dependency order

```
P1 (tag normalization)    P2 (low-tier/bar)    P3 (reg visibility)    P4 (agent diagnostics)
                                                                             ↓
                                                                        P5 (parallelism)
```

P1, P2, P3, P4 are independent. P5 depends on P4 — without reliable per-model failure attribution (log paths, stop reasons), parallel runs produce noise that's hard to diagnose.

Recommended order: finish P4 first (unblocks diagnosis of everything else), then do P1 and P5 in parallel (both small), then P2 and P3 together (both touch the venue generation prompt).

---

## P9 — Type 3 tension logic redesign (small-overlap, not narrowing-threshold) ✅
**Subtask doc:** `docs/subtasks/P9_P10_P11_type3_type5_bscore.md`

**What:** Current Type 3 validation uses an "intersection shrinkage vs pool size" metric that's actually Type 5 logic. Type 3 wants two requirements pointing at *different* venues (small overlap between their satisfying sets); the current logic fails every legitimate type 3 tension pair the agents produce.

**Core change:** Replace `_SEMANTIC_TENSION_PAIRS` whitelist + `extra_narrowing` threshold with a principled small-overlap check:
- Both sides ≥ 3 venues (substantial presence)
- Overlap ≤ 3 venues OR ≤ 25% of smaller set (sets point different directions)

**Status:** ✅ Complete — implemented via engine migration (see `docs/ENGINE_MIGRATION_TODO.md`).
`_SEMANTIC_TENSION_PAIRS` and `_venues_passing` deleted. Replaced with:
- A×A overlap metric via `venues_matching` (Engine Phase 4, Step 1)
- B×B plan-space tension as alternative path, f ≤ 5% (Engine Phase 5)
- Both wired into `validate_task_schema` tension block in `test_generate_tasks.py`

---

## P10 — Type 5 solvability redesign (viable-schedule, not per-category minimum) ✅
**Subtask doc:** `docs/subtasks/P9_P10_P11_type3_type5_bscore.md`

**What:** Type 5 is defined as "narrow pool IS the task" but the validator rejects Type 5 for producing narrow pools. Per-category `min_count` check is inappropriate for Type 5 — should instead verify one viable schedule exists (2 restaurants + 1 site minimum per day).

**Core change:** Add `is_type5` branch in `_verify_task_solvable` that:
- Skips per-category_count_minimum check
- Replaces with "viable schedule exists" (2r + 1s per day minimum)
- Also exempts Type 5 from the `POOL_SIZE_MAX=25` check (narrowness IS the task)

**Status:** ✅ Complete — implemented via engine migration (see `docs/ENGINE_MIGRATION_TODO.md`).
- Type 5 exempt from per-category lower bars; uses viable-schedule check instead (Engine Phase 4, Steps 2-3)
- Type 5 upper bars use tighter 25/25/10 thresholds (Engine Phase 4, Step 4)
- Tightness verified via A×A (≤25% food AND ≤25% site) OR B×B (f ≤ 2%) (Engine Phase 5)
- `_bxb_tight` / `_axa_tight` OR logic in `_verify_task_solvable` lines 555-566

---

## P11 — B-score pipeline overhaul ❌ discarded
**Subtask doc:** `docs/subtasks/P9_P10_P11_type3_type5_bscore.md` (design preserved)

**What was proposed:** Overhaul B-score generation — replace "OPTIONAL" escape hatch,
add per-type templates, wire `llm_semantic` pathway.

**Decision:** Discarded. B-score generation removed from the task agent entirely.
- B-SCORE CONSTRAINT SCHEMA section deleted from handbook
- `b_score_constraints` must be `[]` (validation rejects non-empty)
- Type 6 grounding now uses time_window= or venue_id= P-constraints only
- Evaluator's B-score scoring kept intact (route_efficiency always runs;
  doc_appeared/python_script/llm_semantic dormant without constraints)
- Design doc preserved in subtask file for potential future revival

**Rationale:** Zero real tasks ever produced a B-score constraint. The evaluator's
route_efficiency component runs unconditionally regardless. The hop-3 "agent
investigates external state" concept needs rethinking at the design level before
more implementation effort is invested.

---

## P13 — Category A filter scope model (universal / scoped / inclusion) ✅
**Subtask doc:** `docs/subtasks/P13_scope_model.md`

**What:** Current `_apply_pool_filters` applies every P-constraint universally, which is wrong for most patterns. `price_tier_required: upscale` narrows the whole pool to upscale-only (cafes/parks/museums disappear). `label_required: vegetarian` applied to sites makes no sense. `hidden_gem_required` intent is "include one" but pool filter narrows to gems-only.

**Core change:** Every P-constraint declares `scope_mode: "universal" | "scoped" | "inclusion"` and scope target if scoped. Pipeline splits into 5 stages:
1. Universal pool (whole-plan filters narrow this)
2. Scoped sub-pools per (activity_type/time_window/category)
3. Inclusion candidate sets (at-least-K matches)
4. Three separate **lower-bound** solvability checks — viable-schedule on universal pool, ≥1 candidate per scoped pool, ≥min_count per inclusion pool
5. Three separate **upper-bound** meaningfulness checks — universal ≤ 70% of original, scoped ≤ 70% of slot pool, inclusion ≤ 50% of universal pool (reject cosmetic filters)

**Also adds:** hardcoded `TAG_CATEGORY_AFFINITY` table enforced by validator — `label_required: vegetarian` must scope to restaurant/cafe; `label_required: landmark` must scope to sites; unknown tags allowed safely.

**Blocks:** P9 (tension detection needs to know which pool to compare), P10 (viable-schedule check becomes stage-1 universal-pool check), P12 (category_venue_count is inherently inclusion mode).

**Status:** ✅ Complete — implemented via engine migration (see `docs/ENGINE_MIGRATION_TODO.md`).
- `_build_scope_pools` in `generate_task.py` classifies constraints as universal/scoped/inclusion
  from `(scope, aggregation)` pairs — no `scope_mode` field needed (Engine Phase 4, Step 2)
- `_apply_pool_filters` in `pool_utils.py` rewritten to use `venues_matching` (Engine Phase 3a)
- `_verify_task_solvable` rebuilt on three-pool model (Engine Phase 4, Steps 2-4)
- Upper bars: type-specific thresholds (50/50/25 for t1/3/6; 25/25/10 for t5; exempt for t2/4)
- Lower bars: viable-schedule on universal_pool, ≥1 per scoped, ≥min_count per inclusion
- Tag-category affinity: `_tag_affinity` computed from pool at validation time (Engine Phase 4, Step 5)
- Note: `scope_mode` field NOT added to schema; mode derived from `(scope, aggregation)` at runtime

---

## Priority ordering for P9-P13 (updated)

**Blocking path (updated — engine migration completed P9, P10, P12, P13):**
1. ~~**P13 first**~~ ✅ scope model — done via engine migration Phase 4
2. ~~**P9**~~ ✅ tension redesign — done via engine migration Phase 4 + Phase 5
3. ~~**P10**~~ ✅ type 5 solvability — done via engine migration Phase 4 + Phase 5
4. ~~**P12**~~ ✅ plan-level constraints — engine migration + handbook + travel_time_budget implementation

**Independent:**
- **P11** — B-score pipeline. On hold per user direction, needs more design thought.

**Remaining active work:** 0h — P9, P10, P12, P13 all complete.


## P12 — Plan-level constraint vocabulary enrichment ✅

**What:** After reclassifying `pace_relaxed` and `max_visit_duration` as Category A (venue filters) per P9, the Category B (plan-level) vocabulary is thin. This TODO enriches it with two new capabilities, one rename+generalization, and one latent scoring-bug fix.

### 1. Rename + generalize: `category_count_minimum` → `category_venue_count` ✅

**Status:** ✅ Resolved by engine migration. The old `_handle_category_count_minimum` handler
(which silently ignored the `category` param) was deleted along with all 21 Bucket A handlers.
All 66 tasks were migrated to generic schema where "at least 2 museums" becomes:
```
{scope: "category=museum", condition: {}, aggregation: {at_least: 2}}
```
The engine evaluates this correctly per-venue. The scoring bug is eliminated.

Dead code remains: `pattern == "category_count_minimum"` check in `test_generate_tasks.py:1257`
and old pattern-name references in type description comments. Cleanup tracked separately.

### 2. New pattern: `indoor_outdoor_balance` — tag-ratio constraint ✅

**Status:** ✅ Complete. Engine already supports `{ratio: R}` with `has_tag` condition.
Handbook entry added to `task_agent.py` with worked example showing `{scope: "all",
condition: {has_tag: "outdoor"}, aggregation: {ratio: 0.5}}`. Tests added to `test_b2.py`.

### 3. New pattern: `travel_time_budget` via `sum` aggregation ✅

"Keep travel under 60 min/day", "I don't mind long commutes".

**Target schema:**
```
{scope: "per_day", condition: {}, aggregation: {sum: "travel_minutes", operator: "<=", value: 60}}
```

**Implementation (3 steps, all complete):**

1. **Evaluator enrichment** (`eval/evaluator.py`): In `evaluate()`, before `evaluate_p_score`
   call, loops through each day's activities and sets `activity["travel_minutes"]` from the
   travel matrix via `_get_walk_minutes`. Last activity per day gets 0. Uses same 15-min
   default as F-score for missing matrix entries.

2. **Engine `per_day` + `sum`** (`scripts/generation/constraint_engine.py`): Added `sum`
   case to the `per_day` scope block in `_evaluate_generic_constraint`. Sums the field
   value across each day's matched activities, compares to threshold via operator.

3. **Handbook entry** (`scripts/generation/task_agent.py`): Documented with worked example
   in the ADDITIONAL GENERIC SCHEMA EXAMPLES section.

**Tests:** 2 new tests in `test_b2.py` (per_day sum pass + fail).

### 4. B×B tension thresholds ✅

**Status:** ✅ Fully implemented in engine migration Phase 5.
- `constraint_engine_bxb.py`: per-type narrowing factors, joint hypergeometric computation
- f ≤ 5% for type 3 tension, f ≤ 2% for type 5 tightness, misclassification gate for type 1
- 40 tests in `test_bxb.py`, 651/651 green

### Patterns held off (per user decision)

- **`venue_count_exact`** — subsumed by generic `{aggregation: {at_least/at_most/exactly}}`.
- **`category_sequence`, `meal_time_pattern`** — covered by `time_threshold` with scoping.
- **`repeat_day_allowed`, `price_variance`** — niche, revisit only if signal appears.

### Status: ✅ Complete

| Sub-item | Status |
|----------|--------|
| `category_count_minimum` bug fix | ✅ Resolved by engine migration (handler deleted) |
| B×B tension thresholds | ✅ Engine Phase 5 |
| `indoor_outdoor_balance` | ✅ Handbook entry + tests added |
| `travel_time_budget` | ✅ Enrichment + engine per_day sum + handbook + tests |

---

## P14 — Type 2 required-venue logic (deferred from P7) ⬜

**What:** Type 4 has `required_venue_ids` + premium-cost-add logic. Type 2 doesn't — if agent specifies "must visit Borough Market and the British Museum in 5 hours," the time-feasibility check doesn't account for the required venues being potentially far from the pool centroid, nor for their specific visit durations.

**Fix scope:**
- In P7's type 2 algorithm, replace "K nearest to centroid" with "K must include required_venue_ids + nearest-to-pool-centroid to fill up to K"
- Travel time computation must include legs to/from required venues even if they're far from centroid
- Visit durations must sum ACTUAL `recommended_visit_minutes` for required venues (not half, since user explicitly wants them)

**Depends on:** nothing urgent. P7 works for tasks without required_venue_ids. Gap becomes visible if we generate type 2 tasks with "must visit X."

**Estimated effort:** ~2 hours.

**Status:** ⬜ deferred from P7, not blocking anything.


---

## P15 — Venue generation quality (tag reuse, caps, semantic dedup, handbook) ✅

**Subtask doc:** `docs/subtasks/P15_venue_generation_quality.md`

**What:** Five fixes implemented:
1. **City tag injection** — `_build_assignment` in `generate_venue.py` now queries `SELECT DISTINCT tag FROM tags WHERE city=?` at each call and injects the full current city tag list into the venue assignment block. Agent sees existing tags before SET_TAGS.
2. **SET_TAGS new-tag warning** — if >3 tags not in the city pool are added, returns `status="warning"` with trigram similarity suggestions for possible duplicates (≥40% overlap). Agent must revise or call CONFIRM_TAGS.
3. **CONFIRM_TAGS tool** — new lightweight escape hatch. Agent calls this to acknowledge new tags are genuinely distinct. Registered in tool dispatch table.
4. **Hard tag cap** — >15 tags per venue returns `status="error"`. Soft warning at 12+.
5. **City tag cap** — once the city pool reaches 100 distinct tags, new tags are blocked with a hard error.
6. **Handbook update** — `handbook.py` tags section rewritten: canonical form rule, all limits, CONFIRM_TAGS docs, reuse guidance.

**Depends on:** P1 (canonical form at write-time, now complete).

**Status:** ✅ Complete. 611/611 tests passing.


---

## Pre-BxB Fix Queue — from London task run analysis

*Items from the April 2026 London test_50 run analysis. All must land before Phase 5 (B×B).*
*611/611 tests passing as of this session.*

---

### PRE-1 — Tag-category affinity: hard error (was soft warning) ✅
**Files:** `test_generate_tasks.py`
**Status:** ✅ Done this session.

`scope="all"` + `aggregation="all"` + a food-specific tag (e.g. `historic`, `michelin`, `brunch`) eliminates ALL site venues from the pool and always fails the viable-schedule lower bar. Previously a soft `~ ` warning — the agent would ignore it and burn 10+ turns discovering the same error via the solvability check. Now a hard error with an explicit fix instruction ("change scope to `activity_type=meal`"). The food/site affinity threshold is ≥80% concentration in one group.

---

### PRE-2 — Type 3 reasoning protocol rewrite ✅
**Files:** `scripts/generation/task_agent.py`
**Status:** ✅ Done this session.

The old protocol said "encode ONE side as the filter" but gave no guidance on *why* two-universal-filter type 3 designs fail. Rewrite explains:
- `scope="all"` + `agg="all"` is a full-pool filter — applies to food AND site alike
- Food-specific tags with universal scope eliminate all site venues (links to PRE-1)
- Correct type 3 design: tension across groups (one signal on food side, one on site side, or universal regulation + scoped tag)
- Safe scope rules table in the protocol
- Concrete GOOD/BAD examples with query_pool verification step

Also rewrote type 5 protocol with same scope rules, progressive query_pool steps, and both-group survival check.

---

### PRE-3 — Type 6 grounding check ✅
**Files:** `test_generate_tasks.py`
**Status:** ✅ Done this session.

Type 6 tasks labelled "context-window tension" but containing only generic pool constraints were silently passing validation. New check: a type 6 task must have at least one of:
- A `doc_appeared` B-score constraint (agent must read official site for anchor date)
- A P-constraint with `time_window=` scope (schedule timing tied to window)
- A P-constraint with `venue_id=` scope (specific window-affected venue required)

Without one of these, the task is rejected: "Type 6 task is not grounded in the seasonal window... reclassify as type1 or add a window-specific element."

Verified: both successful type 6 tasks from the London run pass this check (both had `doc_appeared`).

---

### PRE-4 — Type 5 pool stats injection ✅
**Files:** `scripts/generation/task_agent.py` (`_build_task_handbook`)
**Status:** ✅ Complete — was already implemented before this session's analysis.

Per-filter survival statistics are computed dynamically at loop-start from the live pool and injected into the type 5 protocol section as a table (▓=tight ≤15%, ░=medium 16-35%). Top tags by frequency, all traffic/price tiers, key regulations, and top districts are included. The agent picks the combination freely from the table — no prescriptions that would cause all type 5 tasks to be identical.

---

### PRE-5 — Type 4 budget calibration ✅
**Files:** `scripts/generation/task_agent.py` (`_build_task_handbook`)
**Status:** ✅ Complete — was already implemented before this session's analysis.

Cheapest 2-food + 2-site/day cost computed from live pool and injected into type 4 protocol as "POOL COST REFERENCE (City): Cheapest ... ≈ £XX. Your budget_per_day must be £YY–£ZZ." The valid band (1.1–1.3×) is shown with the actual floor and ceiling numbers so the agent targets the right range before its first SUBMIT.

---

### PRE-6 — Stub task save gate + thread crash fix ✅
**Files:** `test_generate_tasks.py`
**Status:** ✅ Complete this session.

**Thread crash fix:** `buf.getvalue()` was called on `None` in the "no API key → skip model" early-return path when running in sequential mode (`capture=False`). Fixed to `buf.getvalue() if buf is not None else ""`. This was causing 8 thread_crash logs per run for non-deepseek models.

**Stub save gate:** Dry-run stub tasks now carry `_is_stub=True`. The task-save block skips writing stubs to the output directory unless `--dry-run` is explicitly set. Real runs no longer pollute the task directory with 120 identical stub files that fail all validation checks. Failures are already recorded in `agent_logs/failure_*.json`.

---

### PRE-7 — P16: Type 6 validation design (deferred) 📝
**Subtask doc:** `docs/subtasks/P16_type6_validation_design.md`
**Status:** 📝 Problem documented. Implementation deferred until design is settled.

The grounding check (PRE-3) prevents the worst case (no window connection). But deeper validation — verifying the persona actually conflicts with this specific window, and that the conflict is discoverable via documents — requires design decisions not yet made. Three options documented in the subtask doc. Recommendation: implement Option C (anchor event name must appear in B-score description) as the next incremental step.

---

## Dependency order for Pre-BxB queue

```
PRE-1 ✅  tag affinity → hard error
PRE-2 ✅  type 3 + 5 protocol rewrites
PRE-3 ✅  type 6 grounding check
PRE-4 ✅  type 5 pool stats injection (was already done)
PRE-5 ✅  type 4 cheapest plan injection (was already done)
PRE-6 ✅  stub save gate + thread crash fix
PRE-7 📝  type 6 deeper validation (deferred — design not settled)

P1   ✅  tag canonicalization (write-time + query-side + planning prompt)
P2   ✅  prompt enforcement + code rebalancing (tier + bar + regulation diversity)
P15  ✅  city tag injection + SET_TAGS limits + CONFIRM_TAGS + handbook
```

**Phase 5 (B×B) is now unblocked.**

Test suite: 611/611 passing throughout all changes.

Files changed this session:
- `test_generate_tasks.py` — PRE-1, PRE-3, PRE-6
- `scripts/generation/task_agent.py` — PRE-2
- `scripts/generation/agent_tools.py` — P1, P15
- `scripts/generation/pool_utils.py` — P1
- `scripts/generation/constraint_engine.py` — P1
- `scripts/generation/generate_city_venues.py` — P2
- `scripts/generation/generate_venue.py` — P15
- `scripts/generation/handbook.py` — P15
- `docs/DESIGN_DECISIONS.md` — P1


---

## P17 — Ground research_city in open data (Overpass + Nominatim + Open-Meteo) ✅
**Subtask doc:** `docs/subtasks/P17_research_city_grounding.md`

**What:** Replace the monolithic `research_city.py` LLM call with a two-phase approach.
Phase 1: fetch `centre_lat/lng`, `districts`, `radius_km`, `weather_notes`, `task_dates`
from Nominatim, Overpass, and Open-Meteo (all free, no key). Phase 2: a focused LLM call
producing only `local_cuisine_label` and `cuisine_variety` — the two fields that genuinely
need cultural knowledge. `venue_archetypes` is removed entirely: the planning prompt's tier
description already handles iconic venues, and `city_interests` (already generated by
`generate_city_interests()`) provides mid/low cultural flavour as a single injected line.

**Why it matters:** The LLM-generated district list is the single biggest driver of
geographic coverage. London's test_50 pool had 0 venues in Westminster, City of London,
and Kensington because those districts simply weren't in the LLM's output. Grounding
districts in OSM admin boundaries eliminates this class of error for all future cities.

**Status:** ✅ Complete. `research_city.py` fully rewritten with two-phase approach:
Nominatim for coords, Overpass for districts, Open-Meteo for weather, LLM for cuisine only.

---

## P18 — Venue field completeness: dress_code, reservation_required, opening hours ✅
**Subtask doc:** `docs/subtasks/P18_venue_field_completeness.md`

**What:** Three fields are systematically empty in generated venues:
- `dress_code` (0/82 set): "Optional" in handbook with no examples — agents always skip.
  Fix: richer documentation with venue-type examples; soft-required for restaurant/bar/attraction.
- `reservation_required` (all 0 from DB default, never deliberate): DB column has
  `DEFAULT '0'`, so SQLite applies it at CREATE_PAGE time before the agent runs.
  COMMIT's `if val is None` check passes silently. Fix: remove `DEFAULT '0'` from schema;
  remove from `bool_fields` exclusion in FILL so agents see it in `still_null`.
- Opening hours (4/82 missing, no warning): no VERIFY check exists for null hours.
  Fix: `_check_hours_coverage` in VERIFY — hard error for restaurants/cafes/bars/museums
  with no hours; soft warning (confirm it's always-open) for parks/neighbourhoods.

**Status:** ✅ Complete. `DEFAULT '0'` removed from DB schema for `reservation_required`;
`reservation_required` removed from `bool_fields` in agent_tools.py; `_check_hours_coverage`
implemented in agent_tools.py with mandatory/flexible category distinction;
`dress_code` documented in handbook with venue-type guidance.

---

## P19 — Duplicate venue deduplication at plan_venues output ✅
**Subtask doc:** `docs/subtasks/P19_venue_plan_dedup.md`

**What:** The `plan_venues` LLM call sometimes lists the same real-world venue twice with
minor name variations (punctuation, apostrophes, subtitle differences). Each duplicate
goes through the full agent pipeline independently — double cost, two venue IDs for the
same place in the pool. Fix: a `_dedup_briefs()` pass using Jaccard token similarity
(threshold 0.75) + same-category + same-district guard, applied immediately after
plan_venues output is parsed, before any agent is dispatched.

**Status:** ✅ Complete. `_dedup_briefs()` implemented at line 537 of `generate_city_venues.py`,
called at line 666 after plan_venues parsing. Uses Jaccard similarity ≥ 0.75 with
same-category + same-district guards. Logs each removal with similarity score.
