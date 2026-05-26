# E4 — Multi-venue document generation
**Status: ✅ complete**

---

## Problem

The current corpus is per-venue only. Each venue has a Yelp listing, 1–3 blog/forum
mentions, and an official site. No document puts two venues in relation to each other.

This matters because `search_blogs_and_forums` is supposed to reward agents who
synthesise across sources — but right now every venue lookup is independent. Agents
don't need to reason across documents at all.

Multi-venue docs also create richer wrong-info traps: a popular 2022 trip diary says
venue X closes at 21:00, the official site now says 22:00 — the agent must weigh
recency against popularity to find the truth.

---

## Design

### Two-phase generation

**Phase 1 — Agent loop (doc_agent.py):**

The planning phase uses a full agent loop — not a single call. The agent needs space
to reason about geographic clustering (especially for itinerary guides), inspect
specific venues, and verify coherence before committing to a set of 20 briefs.

Tools available to the Phase 1 agent:
- `query_pool(filters)` — find venue subsets by category/tag/district/tier
- `get_venue(venue_id)` — full detail including coords, tags, wrong_info flag
- `estimate_travel(venue_id_a, venue_id_b)` — haversine check for itinerary coherence
- `THINK(thought)` — log reasoning steps
- `SUBMIT(briefs)` — validate and terminate loop

Input to agent: full venue pool + all tasks across all windows (tasks are context
only — output is task-agnostic and reusable if task set changes).

Output (on SUBMIT pass): ~20 doc briefs. Each brief specifies:
- `doc_type` — one of the 7 types below
- `angle` — short topic description ("hidden gem pub crawl in Shoreditch")
- `venue_ids` — list of venue_ids to include
- `wrong_info_venues` — subset of venue_ids carrying wrong info in this doc
  (only valid if `has_wrong_info_planned=True` for that venue in DB)
- `date` — approximate publication date (older for stale wrong-info docs)
- `stale` — boolean, True if this is an older doc (2021-2023) with potentially outdated info
  (popularity numbers are assigned by code in Phase 2 using traffic-tier ranges, not by the agent)

SUBMIT validation: venue_ids exist in pool, wrong_info_venues ⊆
has_wrong_info_planned venues, at least one brief per doc type, no brief with
zero venues, no two briefs with identical venue_ids + doc_type.

Target ~20 docs total per city (≈ tasks×¼ ≈ venues×⅓, both converge near 20).
No forced coverage — LLM picks organically. High-traffic venues appear more often
(realistic). Low-traffic venues appear when they fit a coherent narrative.

**Phase 2 — Batched single calls (generate_multi_venue_docs.py):**

Same-type batching: all trip diaries in one call, all listicles in one call, etc.
Each call receives: city context, full venue detail for the specific venues in those
docs, and 3–4 doc briefs from Phase 1. The prompt explicitly requests distinct
tone/angle/voice across docs in the same batch to prevent repetition.

No agent loop needed for Phase 2 — the brief is fully specified, no iterative
reasoning required.

---

### Doc types (7)

**1. Trip diary blog**
First-person narrative of a full day or evening. Venues visited in sequence with
times and personal reactions. Most natural wrong-info vehicle — "the hours on Yelp
said open till 22:00 but they were already closing at 21:00". 3–6 venues.

**2. Recommendation Q&A forum thread**
Someone asks "looking for halal restaurants near Tate Modern" or "wheelchair-
accessible places to take my mum". Answers reference 2–4 venues, may contradict
each other. Natural vehicle for conflicting accessibility claims.

**3. Ranking / listicle**
"Best vegetarian restaurants in Shoreditch", "Museums you can do in half a day",
"Hidden bars tourists don't know about". One venue per item, brief description.
Older listicles (2022–2023) are natural stale wrong-info carriers.

**4. Comparison piece**
Explicit head-to-head or neighbourhood guide: "Borough Market vs Maltby Street —
where to eat on a budget". Makes inter-venue relationships explicit. 2–4 venues.

**5. Itinerary guide**
Structured day plan: "Day 2 in London: 10am Tate Modern → lunch at X → afternoon
at Y → dinner at Z". Prescriptive rather than narrative. Encodes geographic
clustering and sequencing. Phase 1 agent uses `estimate_travel` to verify clusters
are geographically coherent before committing.

**6. Review aggregator snippet**
Short paragraph synthesising what "people say" about a venue or neighbourhood.
"Reviewers consistently mention the early closing time on weekends as a surprise."
Not first-person. Natural vehicle for authoritative-sounding wrong info. 1–3 venues.

**7. City memoir / travel vignette**
Loose, literary account of time spent in the city. Venues mentioned incidentally —
"we ended up at this little place in Bermondsey, the kind of spot where locals
actually go." Medium-post or personal newsletter style. Hardest for agents to
extract structured info from. 3–6 venues, soft mentions.

---

### Wrong-info in multi-venue docs

Multi-venue docs participate fully in the wrong-info role system. A trip diary can
be an `incorrect_source` for a venue's wrong info, with the truth carrier already
registered in the per-venue docs. No extra engineering needed —
`has_wrong_info_planned` in DB is the source of truth. The Phase 1 agent decides
whether to surface wrong info based on that flag and the doc's date/popularity.

Role per (doc, venue) pair: `truth_carrier` | `incorrect_source` | `neutral`.
Stored in `doc_venue_refs` table (same as per-venue docs).

---

### Popularity signal assignment

Popularity is assigned in code during Phase 2 DB write (`_assign_popularity()` in
`generate_multi_venue_docs.py`), not by the Phase 1 agent. The agent sets `stale=true`
for older docs; the code then picks from the stale range. This keeps the agent focused
on planning, not numbers.

Ranges used:

| Doc type / venue tier    | likes    | saves   | view_count  | date      |
|--------------------------|----------|---------|-------------|-----------|
| Trip diary, high-traffic | 200–500  | 80–200  | 2000–8000   | 2024–2025 |
| Listicle, mixed tiers    | 50–150   | 20–60   | 500–2000    | 2022–2025 |
| Forum thread, niche      | 10–40    | 5–20    | 80–400      | 2023–2025 |
| City memoir              | 100–300  | 40–120  | 1000–4000   | 2024–2025 |
| Stale wrong-info doc     | 150–400  | 60–150  | 1500–5000   | 2021–2023 |

Ranking formula in `server/mock_tools.py` (already updated in E4 design session):
```
final = bm25_score
      + recency_boost (0.2 if date ≥ 2024, else 0.0)
      + min((2.0×log1p(saves) + 1.5×log1p(likes) + 0.5×log1p(views)) / 100, 0.30)
```

Stale wrong-info docs rank competitively on popularity but lose on recency — the
agent must weigh both signals to find the truth.

---

### Storage (all in DB)

`search_blogs_and_forums` already reads blogs/posts from `source_docs` +
`doc_venue_refs` via `_load_city_from_db` in `mock_tools.py` for DB cities. No
tool changes needed — multi-venue docs slot directly into the existing schema.

New fields on `source_docs`:
```
doc_subtype  TEXT  -- "trip_diary"|"listicle"|"forum_thread"|"comparison"|
                   --  "itinerary"|"aggregator"|"city_memoir"
window_id    TEXT  -- NULL for city-level docs; set for window-specific trip diaries
```

---

### New file: `scripts/generation/doc_agent.py`

Phase 1 agent loop. Imports shared utilities from `pool_utils.py` (extracted
alongside E4 — see prerequisite below). Owns its own:
- Tool schemas (subset of task_agent tools: query_pool, get_venue, estimate_travel,
  THINK, SUBMIT)
- System prompt (doc planning context, 7 doc type specs, popularity ranges)
- SUBMIT handler (brief validation, terminates on clean pass)
- Multi-model routing (same Anthropic/OpenAI/Gemini pattern as task_agent.py)

Does NOT import from task_agent.py — uses pool_utils.py for shared utilities.

### Existing file: `scripts/generation/generate_multi_venue_docs.py`

Standalone script. Runs after both venue gen and task gen.

```
python scripts/generation/generate_multi_venue_docs.py --city london
```

Steps:
1. Load venue pool + all tasks for city from pool_utils
2. Run Phase 1 agent loop (doc_agent.py) → 20 validated briefs
3. Group briefs by doc_type
4. Phase 2: one call per type group (3–4 docs per call, 7 type prompts)
5. For each generated doc: write to `source_docs`, register all
   (doc, venue) pairs in `doc_venue_refs` with correct roles
6. Print coverage report: venues mentioned, wrong-info traps planted,
   type distribution

Future: city-level wrapper calls venue gen → task gen → multi-venue doc gen.
For now, standalone.

---

## Prerequisite: `pool_utils.py` (done alongside E4, before doc_agent.py)

Extract shared pool utilities into a stable module so `doc_agent.py` and
`task_agent.py` both import from the same clean layer from day one.

**`scripts/generation/pool_utils.py`** — new file:

| Function | Moved from |
|---|---|
| `load_venue_pool` | `generate_task.py` |
| `load_unavailable_dates` | `generate_task.py` |
| `load_city_pool` | `test_generate_tasks.py` |
| `load_pool` | `test_generate_tasks.py` |
| `get_window_for_city` | `test_generate_tasks.py` |
| `build_pool_inventory` | `test_generate_tasks.py` |
| `_city_centre_from_pool` | `task_agent.py` |
| `_query_pool` | `task_agent.py` |
| `_get_venue_detail` | `task_agent.py` |
| `_apply_pool_filters` | `generate_task.py` |

Each original file keeps a thin import alias so no callers outside those files
need to change. `constraint_engine.py` stays as its own module (E3, stable).
`agent_tools.py` DB write tools stay in agent_tools (venue agent's tool layer).

---

## Tasks

### pool_utils.py (prerequisite)
- [x] Create `pool_utils.py`, move 10 functions, add import aliases in source files
- [x] Verify all existing tests still pass after extraction

### DB schema
- [x] Add `doc_subtype` + `window_id` columns to `source_docs`

### doc_agent.py (Phase 1 agent loop)
- [x] Tool schemas: query_pool, get_venue, estimate_travel, THINK, SUBMIT(briefs)
- [x] SUBMIT handler: brief validation (venue_ids exist, wrong_info consistency,
      type coverage, no duplicate briefs)
- [x] System prompt: doc planning context, 7 type specs, popularity ranges,
      coordinate-based reasoning for itinerary guides
- [x] Multi-model routing (Anthropic, OpenAI/DeepSeek, Gemini)
- [x] `run_doc_planning_agent(city, pool, tasks, model, api_key, ...) → list[brief]`

### generate_multi_venue_docs.py (Phase 2 + orchestration)
- [x] Phase 2 prompts: one per doc type (7), with differentiation instructions
      for same-type batches
- [x] Batch generation loop: group briefs by type, call LLM, parse output
- [x] DB write: `source_docs` + `doc_venue_refs` with correct roles
- [x] Popularity signal assignment from brief's likes/saves/view_count
- [x] Coverage report: venues mentioned, wrong-info traps, type distribution
- [x] `--dry-run` flag: plan only, skip generation and DB write
- [x] `--skip-planning` flag: load existing briefs from file, skip agent loop

### Tests
- [x] pool_utils: all 10 functions importable, existing callers unchanged
- [x] SUBMIT validation: rejects bad briefs (missing venue_ids, wrong wrong_info
      assignment, duplicate briefs, missing doc types)
- [x] Phase 2 batch generation: correct DB writes, role assignment
- [x] Coverage report: correct counts
