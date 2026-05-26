# TravelBench Phase 4 — End-of-Phase Record

> ⚠ **Archived phase TODO.** Content below preserved for historical record.
> **Current source of truth:** [docs/TODO_PHASE6.md](TODO_PHASE6.md) (Phase 6 work tracker).
> **Phase 7 candidates:** [docs/PHASE7_FUTURE_WORK.md](PHASE7_FUTURE_WORK.md).

**Date:** April 2026  
**Codebase:** `travelbench_phase4_clean/`  
**Test suite:** 652/652 passing  
**Active city:** London (82-venue pool, 4 seasonal windows)  
**Generated tasks on record:** 11 (type2 ×4, type4 ×4, type6 ×2, type5 ×1; type1 and type3 still 0 successes)

---

## Open Items

### 1. P2 — Venue diversity and distribution (✅ complete)

Root cause was narrow district list + biased high-traffic examples. All fixes landed
and confirmed by live test runs (n=50: perfect 13/21/16 tier split, Westminster 6 venues,
City of London 5, bars 4-8 naturally — zero rebalancing needed).

- ✅ District coverage, high-traffic tier rewrite, venue_archetypes removed
- ✅ Bar rebalancing hack removed (districts produce bars naturally)
- ✅ Tier rebalancing kept as safety net (didn't fire in test runs)

---

### 2. Bucket C special handlers — decision pending

Four special-pattern P-constraints remain in the system prompt and evaluator: `weather_aware`, `opening_time_required`, `dependency_chain`, `consecutive_pairs`. None have been used in any generated task. They remain callable but are effectively dead from the agent side.

**Options:**
- (a) Delete from prompt to reduce noise — agents never reach for them
- (b) Keep as documented escape hatch for future use
- (c) Investigate whether they're worth building tasks around explicitly

No decision made. Currently: present in prompt, wired in evaluator, never triggered.

---

### 3. B-score generation — ✅ retired

B-score generation removed from the task agent. `b_score_constraints` must be `[]`.
Type 6 grounding now uses time_window= or venue_id= P-constraints only (doc_appeared
removed as grounding option). Evaluator's B-score scoring kept intact — route_efficiency
runs unconditionally; doc_appeared/python_script/llm_semantic dormant without constraints.
Design doc preserved in `docs/subtasks/P9_P10_P11_type3_type5_bscore.md`.

---

### 4. Weather system — blocked, deferred

**What's missing:**
- No weather forecast data for London (`data/ground_truth/london/` directory does not exist; only Paris has `weather_forecast.json`)
- No `get_weather_forecast` tool in the task generation agent (so agents cannot check weather while designing tasks)
- The evaluator has full `weather_aware` logic (lines 807–914) that gracefully no-ops when no forecast exists

**What needs to happen to unblock:**
1. Generate weather forecast data for London windows (can use stub or LLM via `generate_city.py`'s `_weather_prompt`)
2. Decide whether `get_weather_forecast` should be added as a task generation tool (like `get_official_site` was added this phase)
3. Decide whether to keep or retire `weather_aware` Bucket C pattern (linked to item 2 above)

**Deferred** until B-score/weather decision is made.

---

### 5. `wrong_info` system — observation noted, no decision

The pipeline has a `wrong_info` table and `[WI]` flags appear on venues in the pool listing shown to the task generation agent. The venue generation pipeline can write wrong_info via `ADD_WRONG_INFO`. But:

- No task generation protocol tells agents what to do with `[WI]` venues
- No constraint type or B-score check rewards/requires agents to handle wrong info
- The evaluator has no `wrong_info` check

**Status:** Infrastructure exists (DB table, generation pipeline, pool flag). Completely unused at task generation and evaluation level.  
**Decision needed:** Design the wrong_info task type (likely a new structural type, or an extension to type6) and wire it into the validator and evaluator.

---

### 6. Type 6 — data now wired, re-run needed

Type 6 tasks require venue-level window data (`window_flags`, `events` table) to be genuinely useful. As of this phase:

- `generate_events.py` and `annotate_venues_for_window.py` are now **wired into `generate_city_venues.py`** as steps 10 and 11
- Running `generate_city_venues` on London from this point forward will populate the `events` table (134 events, 29 sold-out, 73 affects-access) and set `window_flags` on 83 venues (5 in Carnival closure zone)
- The task generation agent now has `get_official_site(venue_id, date)` as tool #7

**However:** The two existing type6 tasks in the record were generated without this data. They are borderline — structurally valid (`doc_appeared` grounds them), but the conflict they describe is not backed by actual DB data. They should be regenerated against a freshly populated DB.

**Next step:** Run `generate_events` + `annotate_venues_for_window` against the London test_50 DB, then re-run type6 task generation.

---

### 7. Type 1 success rate — threshold adjusted, re-run needed

Type 1 was 0/20 in the test_50 run. Root cause: the 50% upper bar was too aggressive for a task type whose design intent is "cascading constraints that improve the plan." The bar was borrowed from type3 (competitive pressure) and didn't fit.

**Fix applied this phase:** Upper-bar thresholds are now type-differentiated:

| Type | Universal food/site | Inclusion |
|------|---------------------|-----------|
| type1 | ≤75% | ≤50% |
| type3/6 | ≤50% | ≤25% |
| type5 | ≤25% | ≤10% |

The `query_pool` response note and type1 system prompt reflect the new 75% target.

Analysis of the 5 failure logs showed agents were producing correctly designed tasks (coherent persona, valid cascading constraints) — the validator was rejecting them. With 75%, 3 of the 5 would have passed. The remaining 2 had genuine zero-venue issues (wrong tag/scope combinations) that the new diagnostic error messages now explain precisely.

**Next step:** Re-run type1 task generation to validate.

---

### 8. Type 3 success rate — tension detection still blocking

Type 3 was 0/8 in the test_50 run. Issues:

1. Agents build counting constraints (`at_least`, `ratio`) instead of pool-filter constraints (two `aggregation="all"` filters with disjoint venue sets). The confusion stems from "competing requirements" being interpreted as B×B counting, not A×A filtering.
2. Even when agents get the form right (two `agg="all"` universal filters), they can't easily verify the venue-set overlap before SUBMIT.

**Fixes applied this phase:**
- Tension error message now uses plain-English GOOD/BAD examples instead of "A×A" jargon
- `query_pool` response now shows food/site breakdown + ceiling numbers so agents can check combined filter impact before SUBMIT
- Type3 protocol step 3 now explicitly says to stack filters into a single `query_pool` call to simulate the combined constraint

**Not yet re-run.** Expected improvement but not guaranteed — the overlap verification still requires two separate `query_pool` calls + manual comparison.

---

### 9. `generate_multi_venue_docs.py` — not yet fired

This script generates multi-venue cross-reference documents (blog posts and forum threads that mention multiple venues together). It is designed to run **after** task generation (once target venues are known), so it correctly has not been run yet.

**Next step:** Run after the London task generation pass completes.

---

### 10. P13.1 `scope_mode` schema — dead code, intentionally left

`_check_scope_declarations()` in `test_generate_tasks.py` implements a stricter structured scope schema (`scope_mode: "universal" | "scoped" | "inclusion"` with `scope` as a dict). It was designed as P13.5 but never wired into `validate_task_schema`.

The active schema uses string-based scopes (`"all"`, `"activity_type=meal"`, `"category=museum"`) which work correctly end-to-end. The P13.1 function and its E3 test coverage remain as dead code.

**Decision:** Left as-is. The string schema is working and consistent. Migrating to the dict schema would be a breaking change across agents, evaluator, and constraint engine for no current benefit.

---

### 11. P3 — Regulation visibility in source docs (⬜ not started)

`_check_regulation_visibility` in `agent_tools.py` only checks positive regulations (value=1: pet_friendly, wheelchair_accessible, etc.). Restricted regulations (value=0: not wheelchair accessible, no photography allowed) are never checked for source doc visibility.

In the test_50 pool, only 19/65 restricted regulations appear in any source doc body. A wheelchair-using persona needs to *discover* venue inaccessibility by reading docs — "narrow basement stairs", "no lift access" — not by querying a hidden DB field. Without this, benchmark tasks testing regulation awareness are either trivially solvable or impossible.

**Fix scope:**
- Add `RESTRICTED_REGS` check in `_check_regulation_visibility` (agent_tools.py)
- Require at least one doc body to contain a natural-language hint about each false regulation
- Related: `docs/VENUE_PIPELINE_AUDIT.md` item 14

**Priority:** High for benchmark validity — directly affects whether regulation-based P-constraints are fair tests.

---

## Next Run Checklist

Before the next London task generation run:

1. **Populate events + window flags**:
   ```bash
   python scripts/generation/generate_events.py --city london
   python scripts/generation/annotate_venues_for_window.py --city london
   ```
   (Or just run `generate_city_venues.py` — steps 10/11 now call these automatically.)

2. **Re-run task generation for all 6 types** against the freshly populated DB to validate:
   - type1 improvement (75% threshold)
   - type3 improvement (clearer tension error + query_pool food/site breakdown)
   - type5 improvement (food/site stats table in handbook)
   - type6 genuine data (window_flags, events, get_official_site)

3. **Run `generate_multi_venue_docs.py`** after tasks are generated.

4. **Evaluate type6 quality** — check if `get_official_site` is being called, if `window_flags` are being used, if the tasks describe real seasonal conflicts vs. generic ones.

---

## Codebase State

**Files changed this phase (primary):**

| File | Key changes |
|------|-------------|
| `scripts/generation/task_agent.py` | `get_official_site` tool (tool #7); food/site breakdown on every `query_pool`; type-aware upper-bar targets in query note; type_key threaded through all loops; handbook audit (noise_level ordering, per_day scope, aggregation list, B-score schema, Cat2 list, tension message jargon removed, solvability per-type thresholds); dead inline type5 protocol removed |
| `scripts/generation/generate_task.py` | Type-differentiated upper-bar thresholds (75/50/25 per type); `_diagnose_zero_venues` helper for tag/scope mismatch; `has_universal` bug fix (scoped agg="all" no longer triggers universal bar); `pcs`/`pc_by_id` hoisted before early-return paths; enriched "eliminates all venues" error; scoped/inclusion upper bar messages rewritten |
| `scripts/generation/generate_city_venues.py` | Steps 10 and 11: `generate_events` and `annotate_venues_for_window` wired as post-generation steps |
| `test_generate_tasks.py` | Dead pool satisfiability check fixed (was using `pattern=="generic"` which never matched); hop-2 example fixed (bad field reference removed); tension message jargon removed; pool satisfiability skips empty conditions |
