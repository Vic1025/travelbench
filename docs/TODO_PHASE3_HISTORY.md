# TravelBench — Phase 3 History TODO

> ⚠ **Archived phase TODO.** Content below preserved for historical record.
> **Current source of truth:** [docs/TODO_PHASE6.md](TODO_PHASE6.md) (Phase 6 work tracker).
> **Phase 7 candidates:** [docs/PHASE7_FUTURE_WORK.md](PHASE7_FUTURE_WORK.md).
*This is the full historical sprint record for Phase 3 development.*
*For what still needs to be done, see TODO_PHASE3_ENDING.md.*
*For subtask detail, see docs/subtasks/E1–E5.*

---

# TravelBench — TODO

Last updated: April 2026 — Sprint B complete, task gen quality complete
Status key: ✅ done  🔄 in progress  ⬜ not started

Design docs:
  docs/seasonal_windows_design.md
  docs/personalised_benchmark_design.md
  docs/score_tier_definitions.md
  docs/generic_constraint_schema.md
  docs/task_structural_types.md

---

## PHASE 1 & 2 — COMPLETE ✅
Paris dataset (23 venues), evaluator, 8 sprints, 63 regression tests passing.

---

## PHASE 3 — CITY DATA GENERATION + TASK & BENCHMARK PIPELINE

### Sprint A — City Data Generation Pipeline

#### A1–A7.5, A11 — COMPLETE ✅
All tests passing. See previous entries for details.

**Existing generated DB (London sample run):**
data/travelbench.db — London venue pool from test runs. Marked as sample data.
Do not alter. Use as test case for B4 task generation agent:
  - Provides real venue briefs (name, category, district, traffic_tier, character, tags)
  - Provides has_wrong_info flags
  - Missing: events (A9 pending), window annotations (A6.5 script ready to run)
  - Missing: travel matrix (A7.6 script ready to run against this DB)
Run annotate_venues_for_window.py + build_travel_matrix.py on this DB before B4 testing.

#### A6.5 — Venue geographic spread + seasonal annotation ✅
- [x] validate_city: coordinate spread check — high-traffic venues must not
      all cluster within 1km of city centre
- [x] Tag diversity check: multiple venues per key tag (dog-friendly,
      wheelchair, halal, free-entry, live-music etc.)
      Type 5 tasks require viable intersections to exist in the pool
- [x] scripts/generation/annotate_venues_for_window.py
      Post-generation pass per seasonal window:
      Rio Carnival: bloco route proximity / road closure zone flag
      Sapporo Snow Festival: Odori Park festival zone flag
      London Christmas: confirmed holiday closure flag
      Enables Type 6 B-score geographic/timing checks

#### A7.6 — Travel matrix ✅
*Season-independent. One matrix per city / per distinct seasonal pool.*
- [x] scripts/generation/build_travel_matrix.py
- [x] DB table: travel_matrix (venue_id_a, venue_id_b, walk_minutes,
      transit_minutes, cycling_minutes)
- [x] OpenRouteService matrix API (falls back to haversine without key)
- [x] Hokkaido: pool param reserved for B0 seasonal schema
- [x] Test: spot-check known pairs (42/42 passing)

#### A8 — Ticket availability ✅
*Unblocked after B0 (seasonal windows schema)*
- [x] Populate ticket_availability for booking_required=1 venues
- [x] Slots per task date within each seasonal window
- [x] Sold-out on anchor event dates (F-score trap)
- [x] Returned by get_official_site(venue_id, city, date)

#### A9 — Events system ✅
*Unblocked after B0*
- [x] LLM auto-generation: plausible events per venue per window
- [x] Event overrides: hours, ticket price, capacity, sold-out flags
- [x] Source docs referencing events
- [x] Returned by get_official_site(venue_id, city, date)

#### A10 — Multi-venue document generation ⬜
*Unblocked after Sprint B task pipeline is complete*
- [ ] City-level pass with full venue pool visibility
- [ ] Doc types: trip diary blogs, listicles, multi-venue forum threads
- [ ] Role per doc-venue pair: incorrect_source / truth_carrier / neutral
- [ ] Date ordering enforced
- [ ] VERIFY_MULTI_DOC: cross-venue consistency, date ordering, truth carrier
- [ ] Task-aware: corpus supports the specific task set per window

---

### Sprint B — Task & Personalised Benchmark Pipeline

*Build order is dependency-driven. Items marked [BLOCKS] must complete
before the items they block can start.*

#### B0 — Seasonal windows + city_config schema [BLOCKS: A8, A9, B4] ✅
*Design: docs/seasonal_windows_design.md*
- [x] Add seasonal_windows array to city_config DB schema
      Per window: window_id, label, dates[], anchor_events[],
      character, conditional_wrong_info_hints[], venue_pool_id
- [x] Populate London (4 windows): Easter Apr 2-8, Late Spring May 23-29,
      Carnival Aug 20-26, Christmas Dec 23-29
- [x] Populate Hokkaido (2 windows): Snow Festival Feb 5-11, Lavender Jul 16-22
- [x] Populate Rio (3 windows): Carnival Feb 12-18, Festas Juninas Jun 11-17,
      Summer Peak Jan 8-14
- [x] Update PLAN_VENUES: accept window parameter, inject window character
- [x] Hokkaido: run PLAN_VENUES per window (window param ready)
      ⬜ Cross-match Sapporo urban venues — post-generation pass, needs LLM run first
- [x] Cross-window venue flagging schema defined
      ⬜ Assignment block injection — pending cross-match implementation

#### B1 — Tool infrastructure + task schema [BLOCKS: everything else] ✅
*Design: docs/score_tier_definitions.md — Tool Set, Final Scoring Formula*
*Must complete before any new city evaluation can run.*

**Tool infrastructure:**
- [x] server/mock_tools.py: SQLite DB reading for new cities
      search_yelp, search_blogs_and_forums, get_official_site, find_nearby_venues
      Fallback to JSON for Paris (backward compatible)
- [x] get_official_site(venue_id, city, date=None):
      With date: window-specific hours, ticket availability, active events
      Without date: current behaviour unchanged
- [x] Paris regression suite: all 63 tests still pass after changes

**Task schema extension:**
- [x] Add score_tier field to constraints: "F" | "P" | "B"
- [x] Add hop field to P/B constraints: 1 | 2 | 3
- [x] Add b_score_constraints array alongside personal_constraints
- [x] Add source_in_profile field on all derived constraints
- [x] Add explicit_physical flag for F-score persona constraints
- [x] Backward compatible: existing Paris tasks require no changes

**Evaluator updates:**
- [x] C-score: source_required is now a hard fail (was partial credit)
- [x] F-score: evaluate explicit_physical persona constraints in F pass
- [x] F hard fail → final_score = 0 (same as C)
- [x] Composite: 0.7 × F_partial + 0.3 × P + 0.2 × B
- [x] Dashboard: show theoretical max 1.2, mark B clearly as bonus

#### B2 — Generic constraint schema engine [BLOCKS: B3, B6] ✅
*Design: docs/generic_constraint_schema.md*
- [x] Generic engine in evaluator alongside legacy handlers
      Handles: scope, condition, aggregation, consequence, relative_to
- [x] New handlers as schema instances (not named functions):
      hidden_gem_required, local_cuisine_preference,
      cuisine_diversity_minimum, pace_relaxed, max_visit_duration
- [x] Consecutive-pair check type: adjacent activity pairwise properties
      (tag alternation, pace variety, noise level variation) — O(n)
- [x] relative_to parameter: timing relative to anchor events and other
      activity types (for Type 6 and dependency_chain patterns)

#### B3 — B-score engine [BLOCKS: B4, B6] ✅
*Design: docs/generic_constraint_schema.md — Route Efficiency, Consecutive-Pair,
Python Script sections*
- [x] Route efficiency: MST per day via travel matrix
      Threshold: actual ≤ 1.5× MST → bonus earned
- [x] Consecutive-pair checks: O(n) loop over adjacent activities
- [x] Doc-appearance awareness: did_consult(venue_id, date) via tool call log
      Extends existing source_required mechanism into B-score
- [x] Budget allocation quality (Type 4):
      Hard-code cost per schedule, check distribution vs price_tier
- [x] Python script runner:
      Task agent writes evaluate(plan, task, venues) → float
      Sandboxed: restricted import whitelist, 5s timeout, no I/O
      Whitelist: benchmark.plan, benchmark.venues, benchmark.travel,
                 benchmark.docs, benchmark.schedule
- [x] Hop-3 LLM judge: schedule arc, rest window, narrative coherence
- [x] B-score on dashboard, clearly marked as bonus

#### B4 — Task generation agent [BLOCKS: B5, B7] ✅
*Design: docs/personalised_benchmark_design.md*
*Design: docs/task_structural_types.md*
*Design: docs/seasonal_windows_design.md*
*Requires: B0 (seasonal windows), B1 (task schema), B2 (handlers), B3 (B-score)*
- [x] scripts/generation/generate_task.py
- [x] Agent inputs:
      City + seasonal window character + anchor events + planning traps
      Venue pool for that window (name, category, district, traffic_tier,
        character, tags, has_wrong_info flag, seasonal_windows, event info)
      NOT full source doc bodies — basic venue info only
      Persona taxonomy (personalised_benchmark_design.md) as reference
      Structural types (task_structural_types.md) as reference
      Constraint schema (generic_constraint_schema.md) as reference
      Paris tasks as format examples
- [x] Persona construction:
      2-3 signals from different categories
      At least one Category 1 (composition) or Category 7 (occasion)
      Mix of hop-1 and hop-2/3 signals
- [x] Structural type selection:
      One primary type per task, at most one compatible secondary
      Valid combos: Type1+4, Type2+3, Type5+6
      Avoid: 3+ structural types simultaneously
      Cross-reference persona signals WITH window character → identify Type 6
- [x] Rubric generation:
      F constraints (hop-1 Case A impossibility) → hard_constraints
      P constraints (hop-1 Case B + hop-2) → personal_constraints
      B constraints (hop-3 + structural) → b_score_constraints
        B-score checks expressed as schema JSON or Python script
      source_in_profile on every derived constraint
- [x] public_input.query and user_profile: natural language only
      Never state derived constraints explicitly
- [x] Target: 10-15 tasks per seasonal window per city
- [x] Wrong info routing: NOT deliberate — wrong info encountered naturally
      Task agent knows has_wrong_info flag per venue (boolean only)
      Does not know incorrect value or truth carrier doc

#### B4.5 — Task solvability verification [BLOCKS: task publication] ✅
*Design: docs/score_tier_definitions.md — Task Solvability Guarantee*
- [x] scripts/generation/verify_task_solvable.py
- [x] Filter pool by hard constraints → check ≥ N viable venues remain
- [x] Greedy schedule: sum(visit_minutes) + sum(travel_minutes) ≤ available
- [x] Selection problem: ≥ N venues of required type in time window (Type 2/5)
- [x] Type 5 check: constraint intersection yields ≥ 1 venue (not 0)
- [x] On failure: task agent retries with relaxed constraint (logged)
- [x] Gate: task not published until solvability passes
- [x] Integrate as post-generation step in generate_task.py

#### B5 — Difficulty scores ✅
*Design: TravelBench InfoDisorder Discussion — Section 10*
*Requires: B4 (task set), A7.6 (travel matrix)*
- [x] scripts/generation/compute_venue_difficulty.py
      venue_difficulty_score (0.0–1.0): total_results, doc count,
      correction availability, wrong info presence
- [x] scripts/generation/compute_task_difficulty.py
      avg_venue_difficulty: N=10 feasible venue set samples → mean score
      Pure arithmetic, no LLM
- [x] Two difficulty axes per task: constraint_complexity + avg_venue_difficulty

#### B6 — Constraint calibration ✅
*Requires: B2 (schema engine), B3 (B-score engine)*
- [x] Probe pairs (good/bad plans) for all new schema handlers
- [x] Probe pairs for B-score route efficiency and consecutive-pair checks
- [x] Probe pairs for Python script B-score examples
- [x] Mean delta ≥ 0.8 across all handlers before task generation runs

#### B7 — validate_city full mode completion ✅
*Requires: B4 (task set), B5 (difficulty scores)*
- [x] avg_venue_difficulty populated check
- [x] Seasonal window coverage: ≥ N tasks per window per city
- [x] Structural type coverage: each type represented across task set

#### B8 — Multi-venue doc generation + task-doc coherence ⬜
*Merged into A10 — A10 is the implementation, B8 is the task-coherence layer on top*
*Depends on: B4 task set, A10 multi-venue doc generation*
- [ ] TBD: coherence check between task constraints and available corpus
      (A10 handles generation; B8 handles ensuring docs actually support the tasks)


#### Task Generation Data Quality ✅
*All quality improvements complete as of April 2026*

**A — Query-only public_input** ✅
- [x] Remove user_profile field from schema, system prompt, and validator
- [x] query is the single natural-language input (2-4 sentences, first person)
- [x] Validator warns (not errors) if user_profile is present (backwards compat)

**B/C — Pool inventory in prompt** ✅
- [x] build_pool_inventory() injected into generation prompt
      Shows: category counts, usable tags (≥2 venues), regulation coverage,
      price tiers — prevents label hallucination at source
- [x] [WC] wheelchair flag added to venue pool listing in prompt
- [x] System prompt: regulation_required vs label_required distinction made explicit

**E — Constraint quality enforcement** ✅
- [x] hop-2 rule: every task must have ≥1 hop-2 P-constraint (validator + prompt)
      hop-2 = requires reasoning about traveller situation, not direct label read
- [x] Constraint tension rule: at least one pair of P-constraints that interact
      to narrow the venue pool (validator + prompt + solvability verifier)
- [x] Validator checks both as hard errors (task fails gate until fixed)

**F — Solvability failure diagnostics** ✅
- [x] label doesn't exist → shows similar tags + regulation suggestion if applicable
- [x] Pool gap (too few venues of type) → shows available category counts
- [x] Filter conflict (label exists but filtered out by hard constraints) → says so

**D — Persona diversity at scale** ✅
- [x] Cat 1 composition quota system — percentage-based, scales with n_tasks
      13 compositions with quotas (13%/10%/7%/3%) computed as round(pct × n_tasks)
      Common: solo, couple, friends_small, child (13% each)
      Moderate: siblings, retirees, teenagers (10% each)
      Less common: solo_woman, single_parent, multi_gen (7% each)
      Rare: friends_large, dog, colleagues (3% each)
      5% free slots (no composition assigned)
- [x] COMPOSITION SLOT block injected into each per-type prompt
      Shows available compositions with [used/quota] counts live during run
      Exhausted compositions clearly marked; model sees what's still open
- [x] --cat1 flag: override all tasks to a specific composition
- [x] _detect_composition(): reads saved task queries, maps to composition key
- [x] End-of-run composition usage table with over-quota warnings
- [x] Type-compatibility matrix: restricts which compositions fit each structural type
- [x] Cat 1 diversified in system prompt and per-type persona examples
      Removed child-heavy examples from Type 1 and Type 5 guidance

**H — 6-type distributed generation pipeline** ✅
- [x] --mode 6type (default): one task per structural type per model call
      Each type gets its own specialized prompt with only relevant content
- [x] --mode 2task: legacy 2-task free-choice mode preserved
- [x] --types flag: generate only specific types (e.g. --types type1 type5)
- [x] Per-type specialized prompts: only show relevant persona categories,
      example query with full constraint cascade, B-score shape, constraints
- [x] Type 1 renamed: type1_cascading_requirements (was "hidden requirements")
      All hop-2/3 constraints must have matching natural signal in query
      Hop-2/3 signal rule applies to ALL types across all problem structures

**I — Constraint tension detection (pool-based)** ✅
- [x] Replaced hardcoded TENSION_PAIRS lookup with pool-based measurement
      For venue-level constraints: measures actual extra narrowing (≥12% threshold)
      For plan-level constraints: semantic tension pairs (pace_relaxed + cuisine_diversity etc.)
      Mixed case: venue-level narrowing from one constraint + plan-level orthogonal signal
- [x] Type 6 window bypass: holiday context IS the tension, no P-constraint pair needed
- [x] Within-task Cat 2 cluster rule: at most 1 Cat 2 signal per task (Type 5 exempt)
- [x] Same-cluster stacking blocked: vegetarian + gluten-free = same dietary cluster

**J — City visitor interests** ✅
- [x] generate_city_interests() in generate_city_venues.py
      10 city-specific visitor interest labels per city
      Paris: Impressionist art, Architecture, Gastronomy, Wine, Fashion, etc.
      London: hardcoded; other cities: LLM call
- [x] Injected into prompts for types 1/2/3/6 only (not type 4/5)
- [x] Cat 6 marked OPTIONAL in system prompt
      "Wanting a good dinner is NOT a Cat 6 interest — it is a universal travel need"
      Cat 6 only used when it generates a specific venue filter constraint
- [x] Interest word counter: tracks appearances per run, cap ≤2 per interest (test mode)

**K — Trait registry** ✅
- [x] paris_trait_registry.json: tracks used trait keys across all saved tasks
      Prevents repeating same trait (vegetarian, wheelchair, hidden gems) across run
- [x] Registry prompt block shows blocklist + what's still available
- [x] scan_existing_tasks(): rebuilds registry from saved task files

**L — Data quality sweep + Overpass fix (April 2026)** ✅
- [x] Deleted all par_spring_* stub tasks (36 files) — pre-hop-2-rule artifacts
- [x] Deleted root par_gen_001/002 (old schema using type vs pattern)
- [x] Removed claude-haiku-4-5 from ALL_MODELS (produced 0 real tasks)
- [x] 30 passing tasks remain: 5 models × 6 types each
- [x] DB migration fix: get_connection() applies idempotent schema migrations
      window_flags, local_cuisine, events table, ticket_availability columns
- [x] plan_venues() fixed: was using OpenAI SDK at DeepSeek URL
      Now uses Anthropic client with claude-sonnet-4-6
- [x] Overpass enrichment fixed for non-Latin cities (Istanbul, Shanghai, etc.)
      Turkish dotless-i + diacritic normalization (_normalize())
      Queries name:en, name:tr, name:ar, name:zh OSM tags alongside name
      Nominatim fallback for venues Overpass misses (rate-limited 1 req/sec)

**G — Corpus gap suggestions (semi-automatic)** ⬜
- [ ] Task generation optionally outputs suggested_docs: [{type, topic, covers_venue_ids, reason}]
      Example: bar-hopping task → suggest "local bar crawl blog covering par_r07, par_b02..."
- [ ] Suggestions fed to A9/A10 doc generation as inputs
- [ ] Second LLM pass plays devil's advocate on suggested doc: flags factual claims
      about specific venues that could mislead agents (bad advice filter)
- [ ] Suggested docs enter corpus with needs_human_review: true by default

---

## PHASE 4 — SCALE, EXPERIMENTS, B-SCORE TUNING

### Prerequisite: auto-generate windows for new cities ⬜
*Currently seasonal windows are hardcoded for London, Hokkaido, Rio only.*
*Any new city (Istanbul, Tokyo, Shanghai, etc.) returns unknown_city and gets no events.*
- [ ] LLM call to generate window definitions for any city:
      Given city name + cultural calendar, produce 3-4 windows with anchor events,
      dates[], character, and conditional_wrong_info_hints
      Write to CITY_WINDOWS dict or directly to DB
- [ ] Wire travel matrix + ticket availability into the main orchestrator
      (currently separate manual scripts — add as steps 6 and 7 after events)

### Multi-city runs ⬜
*Prerequisite above must be done first for non-London cities.*
- [ ] London — full pipeline: venue generation + all 4 windows + tasks
- [ ] Hokkaido — separate pools per window + cross-match + tasks
- [ ] Rio — base pool + Carnival override + tasks
- [ ] Istanbul, Tokyo, NYC (new cities — require auto-window)

### Experiments (run on Paris first)
- [ ] E1 — Per-constraint difficulty table
- [ ] E3 — Source ablation (1/2/3/4 tools)
- [ ] E4 — Stale-hours trap detection rate
- [ ] E5 — LLM judge bias (Claude vs GPT-4o vs Gemini)
- [ ] E6 — Turn count distribution
- [ ] E2+E8 — Blog length / presentation effects
- [ ] E9 — Working memory vs constraint complexity

### Global B-score bonus marks ⬜
*Does not block Sprint B task generation pipeline*
- [ ] Design: what constitutes a globally good plan independent of persona?
      Candidates: proactive official_site calls, weather awareness without
      constraint, uncertainty flagging on low-doc venues, wrong info detection
      without being told a trap exists
- [ ] Decide: separate global B-score vs folded into per-task B-score
- [ ] Decide: interaction with composite score formula

### LLM quality review
- [ ] review_venue.py — post-generation LLM judge
- [ ] Score: origin story plausibility, source naturalness, trap difficulty
- [ ] Auto-set needs_review=1 below threshold

---

## VENUE POOL TARGETS (reference)
40 venues: 20 food/drink + 20 sights
Tiers: 10 high / 18 mid / 12 low
Wrong info: 8 venues (mid/low only)
Pace: ~25% relaxed / 40% moderate / 35% intense
Food: 10 restaurant, 5 cafe, 5 bar
Sights: 7 museum, 6 attraction, 4 park, 3 neighbourhood
Cuisine: ≥8 distinct, local on ≥6 food venues
Districts: ≥8 distinct, no district >8 venues
