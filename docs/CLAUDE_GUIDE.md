# TravelBench — Claude Working Instructions
*Place in system prompt / instructions area so Claude sees it at session start.*

---

## What Is TravelBench

TravelBench is a benchmark for evaluating LLM travel planning agents. The agent receives a natural-language trip request, uses 4 mock tools to research venues, and produces a structured day-by-day itinerary. The benchmark measures whether the agent discovered the right venues, built a feasible schedule, and satisfied the traveller's personal constraints.

**Project phases:**
- **Phase 1–2** — Paris dataset hand-crafted. Evaluator built. C/F/P scoring formula defined.
- **Phase 3** — Auto-generation pipeline for any city. New task schema with B-score bonus tier. Multi-model task generation with quality controls. All E-series items (E1–E5) complete.
- **Phase 4** — Venue/task quality improvements, validation logic audit, constraint vocabulary enrichment, parallelism. Active city locked to London (50-venue pool, 4 seasonal windows).
- **Phase 5** — Full benchmark loop (task → solving agent → tools → plan → evaluator). Transport activities added as explicit activity type with multi-mode travel. Per-city SQLite DBs at `data/cities/{city}/travelbench.db`.
- **Phase 5.5** — Constraint diversity enforcement (V1–V7), wrong-info scaling to 40% (W1–W2), minimum doc count per venue (D1), AxA/BxB tension audit (11 bugs catalogued), CAT-A/B/C constraint engine + validator fixes.
- **Phase 5.10** — P21 prompt strip, P22 evaluator scoring redesign: C-score is now BFCL-style deduction-based (only two hard gates); F-score (P22-F) is now deduction-based with no hard fails; composite formula deleted.
- **Phase 6 (current, ~95% complete)** — Data quality and source-document coverage. Items P6-T1…T23 mostly landed; tracked in `docs/TODO_PHASE6.md`. Highlights:
  - **Tag taxonomy + cuisine column** (T1, T1b, T2-B) — universal vocab with city extensions; tags must not mirror structured columns; hidden tags must surface in `mentioned_tags` on a source doc.
  - **Time-ceiling enforcement at solve time** (T4 / T4b) — F2e in F-score; `ceiling_mode` (contiguous|spread) × `ceiling_scope` (total|per_day).
  - **Source-doc quality stack** (T14–T19) — forum-shape validator, author-reuse cap (N=2 per city), declared persona+tone with within-venue uniqueness, handle-signature soft cap (40% per shape), body-length bounds.
  - **Workflow hardening** (T20) — CREATE_PAGE source_doc requires venue_id + auto-populates doc_venue_refs (fixed the silently-non-functional T16 check); off-vocab tag examples cleaned out of task prompts; validator soft-warns off-universal `has_tag`.
  - **Geographic scope fix** (T21) — Replaced `admin_level=8` Overpass query with children-of-city-relation; added city polygon + point-in-polygon venue COMMIT guard. Fixes "47/51 NYC venues in NJ" failure mode.
  - **Atomic wrong-info workflow** (T22) — `ADD_WRONG_INFO` now requires both `incorrect_source_doc_id` + `truth_carrier_doc_id` and writes all three tables transactionally. Fixes the orphan-wrong_info failure mode (was 6/28 entries on NYC `test_50`).
  - **Per-window weather notes** (T23) — Weather now lives in each seasonal_window dict, computed for its own dates. Vestigial city-level `task_dates` / `weather_notes` retired (soft deprecation).
  - **Paris-JSON pipeline fully retired** at the start of this phase — only the SQLite per-city path remains.
- **Phase 7 (planned)** — Deferred items tracked in `docs/PHASE7_FUTURE_WORK.md` (semantic source-doc quality detection, temporal-arc reasoning, wrong-info problems 2+3, budget overrides).

---

## Always Do First

```
1. Read docs/TODO_PHASE6.md            ← current source of truth — Phase 6 work items + done log
2. Read docs/PHASE7_FUTURE_WORK.md     ← deferred items (with resolution annotations)
3. Read docs/DESIGN_DECISIONS.md       ← locked design facts — never re-litigate
4. Read docs/DATA_FORMATS.md           ← all agreed schemas
5. Read docs/TODO_PHASE5_5.md          ← recent context (P22-F redesign, CAT bugs, V/W/D)
```

Historical context (read only when relevant):
- `docs/TODO_PHASE5.md` — Phase 5 benchmark pipeline upgrade record (B1–B14)
- `docs/TODO_PHASE4_END.md` — Phase 4 end-of-phase open items
- `docs/TODO_PHASE3_HISTORY.md` / `TODO_PHASE3_ENDING.md` — Phase 3 archive
- `docs/subtasks/P22_evaluator_scoring_redesign.md` — full P22 design + bug log
- `docs/subtasks/E5_scaling_analysis.md` — per-city DB restructure + diversity scripts

Keep TODO docs updated as you work. If something in code contradicts `docs/DATA_FORMATS.md`, flag it.

**Construction rule:** for anything marked 🔄 or 📝 in TODO docs or any new component — propose design first, wait for explicit confirmation, then build.

---

## Directory Map

```
travelbench/
│
├── CLAUDE_INSTRUCTIONS.md      ← this file
├── DATA_FORMATS.md             ← all agreed schemas
├── README.md
│
├── docs/                       ← design + tracking documents
│   ├── DESIGN.md                        ← feature implementation design (Phases 1-2)
│   ├── DESIGN_DECISIONS.md              ← locked design facts — authoritative
│   ├── TODO_PHASE6.md                   ← CURRENT — Phase 6 items (P6-T1 … P6-T13)
│   ├── TODO_PHASE5_5.md                 ← Phase 5.5 + P22 evaluator redesign + CAT bugs
│   ├── TODO_PHASE5.md                   ← Phase 5 benchmark pipeline upgrade
│   ├── TODO_PHASE4_END.md               ← Phase 4 end-of-phase items
│   ├── TODO_PHASE4.md                   ← Phase 4 item tracker (archival)
│   ├── TODO_PHASE3_HISTORY.md           ← Phase 3 sprint history (archival)
│   ├── TODO_PHASE3_ENDING.md            ← Phase 3 ending items E1–E5 (all ✅)
│   ├── VENUE_PIPELINE_AUDIT.md          ← venue generation pipeline audit
│   ├── VALIDATION_DESIGN.md             ← validation system design
│   ├── ENGINE_MIGRATION_DESIGN.md       ← constraint engine migration design
│   ├── ENGINE_MIGRATION_TODO.md         ← constraint engine migration tracker
│   ├── personalised_benchmark_design.md ← persona taxonomy, hop depths, scoring tiers
│   ├── task_structural_types.md         ← the 6 structural types (type1–type6)
│   ├── score_tier_definitions.md        ← F/P/B scoring mechanics (pre-P22; see P22 doc for current)
│   ├── generic_constraint_schema.md     ← constraint pattern reference
│   ├── seasonal_windows_design.md       ← window schema, city window configs
│   ├── type6_misplacement_redesign.md   ← Type 6 validation redesign
│   ├── TASK_REVIEW_ROUND1_REPORT.md     ← task review notes
│   └── subtasks/                        ← per-item design docs (P1–P22, E1–E5)
│
├── data/
│   └── cities/{city}/                      ← PRIMARY — per-city corpora
│       ├── travelbench.db                      production DB
│       ├── runs/<run_name>/travelbench.db      per-run DBs (test_70, test_50, etc.)
│       ├── runs/<run_name>/logs/               per-venue agent logs (gitignored)
│       └── tasks/runs/<run_name>/<window>/     generated task JSON output
│
├── server/
│   └── mock_tools.py           ← THE 4 AGENT TOOLS (search_yelp, search_blogs_and_forums,
│                                  get_official_site, get_travel_time) + dispatch_tool()
│
├── eval/
│   └── evaluator.py            ← SCORING ENGINE — P22 BFCL-style C, P22-F deduction-based F,
│                                  evaluate(result) → {c_score, f_score, p_score, b_score}
│
├── agents/
│   ├── runner.py               ← multi-provider agent loop (Anthropic/OpenAI/Gemini)
│   ├── base_runner.py          ← shared tool dispatch + plan parsing
│   ├── gpt_runner.py           ← OpenAI-specific runner
│   └── gemini_runner.py        ← Gemini-specific runner
│
├── run_benchmark.py            ← BENCHMARK ENTRY POINT — run tasks against a model
├── test_generate_tasks.py      ← TASK GENERATION ENTRY POINT — generate tasks for a city
│
└── scripts/
    ├── analysis/               ← venue_diversity_score.py, task_diversity_score.py (E5)
    └── generation/             ← all generation pipeline modules (see below)
```

---

## Pipeline Entry Points

### 1. Generate a city dataset
```bash
python scripts/generation/generate_city_venues.py --city london --api-key sk-deepseek-... --anthropic-key sk-ant-...
```
**What it does (actual execution order):**
- Step 1: `research_city.py` — open-data city config (Nominatim + Overpass + Open-Meteo) + focused LLM call for cuisine fields
- Step 2: `populate_seasonal_windows.py` — ensure windows exist in DB (LLM-generated or hardcoded)
- Step 3: `plan_venues()` — one Anthropic LLM call returns 50 venue briefs
- Step 3b: `enrich_venues.py:enrich_briefs()` — Overpass API to add lat/lng coordinates
- Step 4: `generate_events.py` — generate events for all windows (separate from plan_venues)
- Step 5: `generate_venue.py:run_venue_agent()` — per-venue DeepSeek agent loop (parallel workers)
- Step 6: `validate_city.py:validate_city()` — pool validation checks
- Step 7: `write_planned_events()` — write events to DB (venue IDs now resolved)
- Step 8: `build_travel_matrix.py` — pairwise walk/transit/cycling/taxi times
- Step 9: `populate_ticket_availability.py` — ticket availability per window
- Step 10: `compute_venue_difficulty.py` — venue difficulty scores
- Step 11: `generate_events.py` — populate events table
- Step 12: `annotate_venues_for_window.py` — venue window flags

City DB lives at `data/cities/{city}/travelbench.db` (per-city, created automatically).

**Key flags:**
- `--api-key` — DeepSeek API key for venue agents (step 5)
- `--anthropic-key` — Anthropic key for planning + events (steps 1–4)
- `--model deepseek-chat` — venue agent model (default: deepseek-chat)
- `--planning-model` — model for plan_venues LLM call
- `--workers N` — parallel venue agent workers (default 5)
- `--limit N` — generate only N venues
- `--dry-run` — stub all LLM calls
- `--db PATH` — override per-city DB path (for tests)

### 2. Generate benchmark tasks
```bash
python test_generate_tasks.py --city london --api-key sk-ant-... --mode 6type
```
**What it does:**
- Loads venue pool + events from per-city SQLite DB
- Per-window, per-model agent loop with tools (HELP, THINK, query_pool, get_venue, get_official_site, estimate_travel, SUBMIT)
- SUBMIT runs `validate_task_schema` + `_verify_task_solvable` — loop terminates on clean pass
- SUBMIT auto-corrects `window_id`, `task_id`, `difficulty`; rejects (does not auto-correct) `structural_type` — see P6-T3
- Per-type reasoning protocols for all 6 structural types

**Key flags:**
- `--mode 6type` (default) — one task per structural type per model
- `--types type1 type5` — generate only specific types
- `--cat1 retirees` — force all tasks to use this composition
- `--model claude-sonnet-4-5` — single model instead of all 4 (`gemini-3.1-pro-preview`, `claude-sonnet-4-5`, `gpt-5.4`, `deepseek-reasoner`)
- `--parallel-models` (default on) — run models concurrently
- `--single-shot` — legacy single-call path (no agent loop)

### 3. Run benchmark evaluation
```bash
python run_benchmark.py --city london --window london_carnival_2026 --model claude-sonnet-4-6 --api-key sk-ant-...
python run_benchmark.py --tasks lon_gen_001 lon_gen_002 --dry-run
```

---

## Generation Pipeline — Module by Module

```
scripts/generation/
│
├── db.py                    ← SQLite schema, get_connection(), init_db(), _apply_migrations()
│                               get_city_db_path(city) → data/cities/{city}/travelbench.db
│                               IMPORTANT: _apply_migrations() auto-backfills missing columns
│
├── research_city.py         ← RESEARCH_CITY: two-phase (open data + focused LLM)
│
├── generate_city_venues.py  ← MAIN ORCHESTRATOR for city generation (~1700 lines)
│   ├── _plan_prompt_with_events()  ← builds planning prompt
│   ├── plan_venues()               ← LLM call → venue briefs
│   ├── _dedup_briefs()             ← Jaccard dedup of near-duplicate venue names
│   ├── generate_city_interests()   ← 10 city-specific visitor interests
│   ├── write_planned_events()      ← writes events to DB after venue IDs resolved
│   └── generate_city_venues()      ← full orchestration: Steps 1–12
│
├── enrich_venues.py         ← OVERPASS ENRICHMENT: adds lat/lng to high/mid venues
│                               ⚠ Fails for non-Latin city names
│
├── generate_venue.py        ← PER-VENUE AGENT: runs one DeepSeek agent per venue brief
│                               Agent uses 11 tools (CREATE_PAGE, FILL, COMMIT, VERIFY, etc.)
│                               Produces: venue row + yelp_listing + source_docs + official_site
│
├── agent_tools.py           ← ALL 11 VENUE AGENT TOOLS + VERIFY checks (~1500 lines)
│                               Tag canonicalization (P1), but NO whitelist — see P6-T1
│
├── handbook.py              ← AGENT HANDBOOK: HELP() system for the venue agent (~1150 lines)
│
├── generate_events.py       ← Event generation per window (LLM-based)
│
├── populate_seasonal_windows.py  ← Window configs (LLM auto-gen + hardcoded fallbacks)
│
├── populate_ticket_availability.py  ← Ticket availability per window
│
├── annotate_venues_for_window.py   ← Window flags (e.g. carnival route)
│
├── build_travel_matrix.py   ← Pairwise walk/transit/cycling/taxi times
│
├── compute_venue_difficulty.py  ← Per-venue difficulty score (0.0–1.0)
│
├── compute_task_difficulty.py   ← Per-task difficulty (standalone analysis script)
│
├── task_agent.py            ← TASK GENERATION AGENT: tools, protocols, handbook (~2400 lines)
│                               Tools: HELP, THINK, query_pool, get_venue, get_official_site,
│                               estimate_travel, SUBMIT
│                               Per-type reasoning protocols for all 6 structural types
│                               _dispatch_task_tool auto-corrects window_id/task_id/difficulty
│
├── generate_task.py         ← TASK VALIDATION: validate_task_schema + _verify_task_solvable (~980 lines)
│                               Type-differentiated upper-bar thresholds, solvability checks
│                               KNN geometry on universal pool only — see P6-T5
│
├── pool_utils.py            ← SHARED POOL UTILITIES (~620 lines)
│                               _apply_pool_filters, CITY_CURRENCY, format_money, tag normalization
│
├── constraint_engine.py     ← CONSTRAINT SCHEMA ENGINE (~600 lines)
│                               P-constraint evaluation, has_tag conditions, scope matching
│                               CAT-A: pool-level aggregation skips added (May 2026)
│                               at_least counts activities, not distinct venues — see P6-T9
│
├── constraint_engine_bxb.py ← B×B TENSION DETECTION (active for type3/type5)
│
├── doc_agent.py             ← MULTI-VENUE DOC AGENT (~780 lines)
│
├── generate_multi_venue_docs.py  ← Multi-venue doc orchestrator (~590 lines)
│
├── trait_registry.py        ← Tracks character trait usage across tasks in a run
│
├── validate_city.py         ← Pool validation: category counts, tag coverage, pace distribution
│
└── calibrate_constraints.py ← Constraint calibration utilities

scripts/                     (top-level, NOT in generation/)
└── analysis/
    ├── venue_diversity_score.py  ← combination uniqueness curve (E5)
    └── task_diversity_score.py   ← Jaccard + coverage + entropy (E5)
```

---

## Scoring System (post-P22 / P22-F)

```
C-score (P22 BFCL-style)  →  deduction-based, two hard gates
  Hard gates (score = 0, F/P/B skipped):
    - Zero tool calls
    - No <final_plan> tag / JSON parse failed
  Otherwise: max(0.0, 1.0 − Σ deductions)
    Section A — Per-call AST validation (−0.04 each):
      A1 unknown tool, A2 missing param, A3 unexpected param,
      A4 wrong type, A5 wrong city, A6 venue_id with spaces,
      A7 venue_id not in prior tool results
    Section C — Timetable format (−0.04 each):
      task_id/city mismatch, days missing, fewer/more days,
      day_of_week invalid, activity missing fields, time format,
      activity_type invalid, transport mode invalid, hallucinated venue_id
    Section D — Blank time coverage (variable, max 0.8/day):
      deduction = 0.8 / expected_cnt * (expected_cnt − actual_cnt)
      when actual_cnt < floor(expected_cnt) − 1   (-1 buffer — see P6-T7)
  Section B (process completeness MUST-call checks): DELETED

F-score (P22-F)  →  deduction-based, no hard fails
  F = max(0.0, 1.0 − Σ deductions)
    F1a Overlap −0.20 / pair
    F1b Travel infeasible −0.15 / pair
    F2a Hours violation −0.15 / venue
    F2b Ticket sold-out −0.15 / venue
    F2c Truth-carrier not retrieved −0.05 / wrong-info venue
    F2d Event capacity sold-out −0.15 / venue
    F3b Buffer tiers: <5m −0.10 | 5–12m −0.06 | 12–15m −0.03 | >30m −0.02
    F4a Visit duration: under-scheduled −0.05 | over-scheduled −0.02 / venue
  has_critical_issues = any deduction ≥ 0.10

P-score  →  preference satisfaction (0.0–1.0)
  Generic constraint engine + 20+ code handlers (label_required, regulation_required,
  noise_level_max, district_count_max, etc.)
  LLM judge for semantic constraints
  Hop-1 direct + hop-2 inferred constraints

B-score  →  bonus only (0.0–1.0, never penalises)
  Route efficiency (MST ratio) — always computed
  doc_appeared, python_script, llm_semantic — evaluator supports but
  task generation no longer produces B-score constraints (P11 discarded).

Composite formula: DELETED (P22-F). evaluate() returns {c_score, f_score, p_score, b_score}.
run_benchmark.py summary "Final" column removed.
```

---

## Task Schema — Key Fields

```json
{
  "task_id": "lon_gen_001",
  "city": "london",
  "structural_type": "type2_subset_selection",
  "public_input": {
    "query": "Natural language trip request — ONLY field shown to agent",
    "query_resources": {"time_ceiling_minutes": 480, "budget_per_day": 120}
  },
  "rubric": {
    "hard_constraints":     [{"id":"hc_001","type":"hours_check",...}],
    "partial_constraints":  [{"id":"pc_001","type":"travel_time_buffer",...}],
    "personal_constraints": [{"id":"p_001","score_tier":"P","hop":2,"pattern":"district_count_max",...}],
    "b_score_constraints":  []
  }
}
```

**Critical constraint fields:**
- `pattern` — NOT `type` (validator will reject `type`)
- `hop` — 1 (direct), 2 (requires inference). Hop 3 retired with B-score generation.
- `score_tier` — must be exactly `"P"` (CAT-C: `None` no longer silently accepted)
- `check_method` — must be `"code"` or `"llm"` (CAT-C: arbitrary strings rejected)
- `source_in_profile` — exact phrase from query that implies this constraint
- `scope` — string: `"all"`, `"activity_type=meal"`, `"category=museum"`, `"per_day"`, `"time_window=HH:MM-HH:MM"`, `"venue_id=…"`, or list compound
- `aggregation` — `"all"` | `"none"` | `{at_least:N}` | `{at_most:N}` | `{count_distinct:N,field:F}` | `{ratio:R}` | `{sum:F,operator:OP,value:V}` | `{exactly:N}` | `{at_most_distinct:N}` | `{at_least_days:N}`

---

## The 6 Structural Types

| Type | Mechanism | Key P-patterns |
|------|-----------|----------------|
| `type1_cascading_requirements` | Stated signals cascade to 4–5 constraints via hop-2 | max_visit_duration, noise_level_max, pace_relaxed |
| `type2_subset_selection` | Time ceiling forces choosing best subset | time_threshold, category_count_minimum, label_required |
| `type3_competing_requirements` | Two preferences pull at different pool subsets | hidden_gem_required + label_required:iconic |
| `type4_precision_allocation` | Budget forces intelligent distribution | numeric_aggregate + price_tier_required (sum required, V7) |
| `type5_hard_feasibility_reduction` | Stacking ≥3 filters narrows pool to 1–3 venues | regulation_required + label_required + district_count_max |
| `type6_context_window_tension` | Persona preference conflicts with window anchor event | time_threshold + label_excluded + time_window/venue_id scope |

**Hop-2/3 signal rule (ALL types):** every hop-2 or hop-3 constraint must have a matching natural signal in the query. The agent reasons from what the user said — never guesses.

---

## Cat 1 Composition Quota System

13 compositions with percentage-based quotas (computed at runtime from n_tasks):

| Tier | Compositions | Quota |
|------|-------------|-------|
| Common (13%) | solo, couple, friends_small, child | max = round(0.13 × n) |
| Moderate (10%) | siblings, retirees, teenagers | max = round(0.10 × n) |
| Less common (7%) | solo_woman, single_parent, multi_gen | max = round(0.07 × n) |
| Rare (3%) | friends_large, dog, colleagues | max = round(0.03 × n) |
| Free (5%) | unassigned — model's choice | max = round(0.05 × n) |

Each task prompt shows `COMPOSITION SLOT` with live `[used/quota]` counts.

---

## Active Models (ALL_MODELS in test_generate_tasks.py)

```python
["gemini-3.1-pro-preview", "claude-sonnet-4-5", "gpt-5.4", "gpt-5.4-mini", "deepseek-reasoner"]
```
Claude Haiku removed — produced only stubs.

---

## Known Issues / Watchpoints (Phase 6 entry state)

**Phase 6 active gaps — see `docs/TODO_PHASE6.md` for the full list:**
- **P6-T1** — No canonical tag vocabulary. Agents invent tags freely; London pool has near-duplicates (`photography-allowed` vs `photography-friendly`) and one-offs (`bombay-cafe`).
- **P6-T2** — Issues A & C resolved (existed only in retired Paris-JSON path; live `mock_tools.py:100–125` already loads full yelp-visible tag list). Issue B (blog/forum natural-language tag surfacing) remains open.
- **P6-T3** — ✅ Fixed. SUBMIT now auto-maps `"type1"` → `"type1_cascading_requirements"` (and `"1"`–`"6"`) before validating.
- **P6-T4** — Time ceiling (`time_ceiling_minutes`) is validated at task generation but **never checked at evaluation time**. Zero references in `eval/evaluator.py`.
- **P6-T4b** — `ceiling_mode: contiguous | spread` doesn't exist in code.
- **P6-T5** — KNN geometry check picks K nearest from the entire universal pool, not the scoped subset (e.g. museums-only for `at_least: 4 scope=category=museum`).
- **P6-T6** — K-target calculator reads bare `at_least:N` without multiplying by days for `scope=per_day`.
- **P6-T7** — Current blank-time formula `0.8 × missing / expected_cnt` with `-1` buffer is span-based — conflates long visits with idle time, deduction disproportionate.
- **P6-T8** — `recommended_visit_minutes` is purely LLM-generated. No Foursquare/Google Places integration yet.
- **P6-T9** — F4a evaluates each visit independently (no per-venue sum); `at_least` aggregation counts activities, not distinct venues (gameable by duplicates). `count_distinct` is correct.
- **P6-T11** — F2c truth-carrier check measures retrieval only, not whether the agent applied the correct info. Partial data fixes already landed: `full_labels` strips conflicting booking tags; `get_official_site` now returns `avg_cost_local`.

**Other known watchpoints:**

**Overpass enrichment fails for non-Latin cities** (Istanbul, Shanghai, etc.)
- Root cause: LLM generates English transliteration; OSM stores local script
- Not blocking: enrichment is additive — venues without coords still generate fine

**Type 1 and Type 3 task generation: 0% success rate (pre-fixes baseline)** — Type 1 threshold adjusted (75%). Type 3 tension detection bugs catalogued in TODO_PHASE5_5 AxA/BxB audit (Bugs 5–11).

**B-score generation disabled (P11 discarded)** — task agent no longer produces `b_score_constraints`. Evaluator's `route_efficiency` still runs unconditionally. `doc_appeared` / `python_script` / `llm_semantic` scoring paths dormant.

**wrong_info system** — DB table, generation pipeline, `[WI:category]` pool flag wired (W2). Density at 40% (W1). F2c retrieval check active. P6-T11 problem 3 (plan-level application check) still unmeasured.

**Currency hardcoded** — `CITY_CURRENCY` dict in `pool_utils.py`. P6-T12 proposes `pycountry`. `query_resources.currency` not auto-populated in SUBMIT handler.

**DB migrations** — `get_connection()` calls `_apply_migrations()` which auto-adds missing columns. Safe to run on any existing DB.

**Per-city DB paths** — never hardcode `data/travelbench.db`. Always use `get_city_db_path(city)` from `db.py`. Paris remains JSON-only.

---

## Communication Expectations

- Update TODO docs when items complete
- Summarise what changed after completing a major section
- Flag design concerns rather than silently implementing
- Ask before implementing anything marked 🔄 or 📝 in TODO docs
