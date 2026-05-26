# TravelBench — Phase 3 Ending TODO

> ⚠ **Archived phase TODO.** Content below preserved for historical record.
> **Current source of truth:** [docs/TODO_PHASE6.md](TODO_PHASE6.md) (Phase 6 work tracker).
> **Phase 7 candidates:** [docs/PHASE7_FUTURE_WORK.md](PHASE7_FUTURE_WORK.md).
*The 5 things that need to be done before Phase 3 is complete and scaling can begin.*
*Each item has a dedicated subtask doc in `docs/subtasks/` with full detail and live status.*

Status key: ✅ done  🔄 in progress  ⬜ not started

---

## E1 — Auto-window + event generation pipeline ✅
**Subtask doc:** `docs/subtasks/E1_auto_windows.md`

**What:** Two gaps fixed together, since they share the same dependency chain.

*Window generation:* `populate_seasonal_windows.py` is hardcoded for London, Hokkaido, and Rio only. Need a single LLM call per city that generates 2-4 culturally significant windows (dates, anchor events, character, wrong-info hints) and writes them to DB automatically.

*Event generation restructure:* events currently run after venue generation, so the venue agent never knows about events and source docs never mention them. Restructured pipeline moves event generation (Step 4) before venue agents (Step 5), so each venue receives its assigned events and weaves them into source docs naturally. One source doc mention per event is enforced by VERIFY.

**Why it blocks everything else:** windows unblock events, ticket availability, and Type 6 tasks. The event restructure is bundled here because it touches the same steps.

**Status:** ✅ complete — 56/56 tests passing (`scripts/generation/test_e1.py`).

---

## E2 — Wire travel matrix + ticket availability into the orchestrator ✅
**Subtask doc:** `docs/subtasks/E2_orchestrator_wiring.md`

**What:** Five parts: (A) wire build_travel_matrix + populate_ticket_availability into orchestrator as steps 7+8; (B) add lat/lng + distance formula to task gen prompts; (C) fix solvability gate with real geometry for Type 2; (D) add ticket availability to task gen prompt + evaluator; (E) retire Paris JSON, rewrite load_ground_truth as pure DB function.

**Status:** ✅ complete — 51/51 tests passing (`scripts/generation/test_e2.py`).

---

## E2.5 — Two-Phase Task Generation ✅
**Subtask doc:** `docs/subtasks/E2.5_two_phase_task_generation.md`

**What:** Full agent loop replacing single-shot task generation. Agent uses tools (HELP, THINK, query_pool, get_venue, estimate_travel, SUBMIT) to explore the venue pool, reason about feasibility, and produce a validated task JSON. SUBMIT runs `validate_task_schema` + `_verify_task_solvable` — loop only terminates on clean pass. Per-type reasoning protocols for all 6 types. Multi-model (Anthropic, OpenAI, DeepSeek, Gemini). `--single-shot` flag preserves old path.

**Status:** ✅ complete — 99/99 tests passing (`scripts/generation/test_e2_5.py`).

---

## E3 — Schema validation improvements + B5 difficulty scores ✅
**Subtask doc:** `docs/subtasks/E3_solvability_and_difficulty.md`

**What:** Two concerns: (A) schema validation gaps — `source_in_profile` tracing, python_script dry-run, required_venue_ids check, pool-level generic constraint engine, filtered pool > 25 hard fail, shared `constraint_engine.py` module; (B) F-score hard fail #3 for Type 6 anchor date trap; (C) B5 difficulty scoring redesign — three axes (constraint_complexity 50% + avg_venue_difficulty 25% + pool_size_difficulty 25%), `compute_venue_difficulty` wired as step 9 in orchestrator, `avg_venue_difficulty` simplified to mean over filtered pool (no random sampling).

**Note:** No separate save gate — E2.5 agent loop enforces validation via SUBMIT.

**Status:** ✅ complete — 58/58 tests passing (`scripts/generation/test_e3.py`).

---

## E4 — A10 multi-venue document generation ✅
**Subtask doc:** `docs/subtasks/E4_multi_venue_docs.md`

**What:** the current corpus has only per-venue documents — each venue has its own Yelp listing, a handful of blog mentions, and an official site. There are no cross-venue documents: no "best restaurants in Shoreditch" listicles, no trip diary blogs covering a full day across 4–5 venues, no multi-venue forum threads. The benchmark currently only tests whether agents can look up individual venues, not whether they can synthesize information across a corpus.

Multi-venue docs matter because:
- Agents should discover venue relationships (e.g. "blog says X and Y are near each other and both close early on Sundays")
- Wrong-info traps become more interesting when contradictions appear across documents, not just within one
- Task-doc coherence: if a task requires a vegetarian restaurant + a photography-allowed museum on the same day, at least one blog should mention both in context

**Design:** two-phase — planning call (agent loop with query_pool/get_venue/estimate_travel tools) produces ~20 doc briefs (type + angle + venue_ids + wrong-info roles), then batched single-call generation by type (3–4 docs per call, 7 types). Task-informed planning but task-agnostic output. Per-city, ~20 docs total. Full wrong-info role participation. All in DB. Standalone script.

Done alongside E4: extract `pool_utils.py` with 10 shared pool utility functions from task_agent.py, generate_task.py, and test_generate_tasks.py — so doc_agent.py and task_agent.py share a stable utility layer from day one.

**Status:** ✅ complete — 116/116 tests passing (`scripts/generation/test_e4.py`).

---

## Dependency order

```
E1 (auto-windows)
    ↓
E2 (orchestrator wiring)   E3 (solvability gate + difficulty)   E4 (multi-venue docs)
```

E3 and E4 can be built in parallel. E2 requires E1 to be meaningful. E5 is independent — run after any city generation.

---

## E5 — Venue and task diversity saturation analysis ✅
**Subtask doc:** `docs/subtasks/E5_scaling_analysis.md`

**What:** Two manual analysis scripts — no pipeline wiring, invoked after generation runs.
(1) `venue_diversity_score.py` — walks the venue pool in generation order, tracks
marginal new (category, district, tier) combinations, finds saturation point.
(2) `task_diversity_score.py` — for a city+window task set, computes pairwise
constraint Jaccard similarity, venue coverage fraction, and structural type entropy
as each task is added. Finds the knee in the diversity curve.

Results inform the per-city venue target and per-window task target going forward.

**Blocks:** Nothing — run manually after first London agent-loop generation run.

**Status:** Part A ✅ complete — per-city DB structure implemented, all 418 tests passing.
              Part B ⬜ blocked on London agent-loop generation run.
