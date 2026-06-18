# TravelBench — Locked Design Decisions

Tracks every design decision that has been discussed and confirmed.
Read this before any implementation session to avoid re-litigating settled questions.
Update when a new decision is made. Never remove entries — mark as SUPERSEDED if changed.

---

## VENUE SCHEMA

**outdoor_sensitivity** — `"indoor" | "outdoor"` only. Two values, no partial/covered complexity.
- indoor: unaffected by weather
- outdoor: hard F-score fail on rainy/stormy day

**suitable_occasions / max_group_size** — REMOVED. Occasion deduction is the planning agent's job.
The task declares the occasion; the agent reasons from regulations + noise_level + price_tier + labels.
Valid task occasions: `family | date | anniversary | honeymoon | business | birthday | retirement | school_trip`

**labels** — free-form. No fixed allowed list. Generation guidance examples in DATA_FORMATS.md but not enforced as a whitelist. Any label used in a task rubric must exist on the relevant venue — enforced at task generation time, not by schema restriction.

**local_cuisine** — bool on food/drink venues. True if venue serves the city's designated local cuisine. City config holds the cuisine label name (e.g. "french" for Paris). Venue stores only the boolean.

**venue_difficulty_score** — float 0.0–1.0. Written as null at generation time. Computed and filled after full source corpus is assembled.

**recommended_pace** — `"relaxed" | "moderate" | "intense"`. Assigned using category + booking_required + recommended_visit_minutes as signals at generation time. NOT derived mechanically — editorial judgment by the generation agent using those signals as guidance.

**price_tier** — `"budget" | "mid" | "upscale" | "fine-dining"`. Stored explicitly on venue, not recomputed from avg_cost_local at query time.

**lunch_cost_local / dinner_cost_local** — food/drink venues only. Null for all other categories.

**traffic_tier** — `"high" | "mid" | "low"`. Drives total_results count and source doc count. Set by generation agent based on venue character.

---

## SOURCE DOCUMENTS & DATABASE

**Storage** — SQLite database, not per-file JSON. Agent tools are SQL operations. Principle: anything filtered, sorted, joined, or checked individually is its own column — minimise JSON in DB.

---

### Table: `venues`
One row per venue. Ground truth only — never exposed to planning agent.
Columns: venue_id (7 random chars), city, name, category, district, address, lat, lng,
hours_mon/tue/wed/thu/fri/sat/sun (TEXT, "HH:MM-HH:MM" or null if closed, supports split service as "HH:MM-HH:MM,HH:MM-HH:MM"),
avg_cost_local, lunch_cost_local (nullable), dinner_cost_local (nullable), price_tier,
recommended_visit_minutes, booking_required, has_official_site, outdoor_sensitivity,
recommended_pace, traffic_tier, local_cuisine (nullable bool), total_results,
yelp_popularity_score, venue_difficulty_score (null until post-gen computation),
recommended_time_window_end (nullable), recommended_time_window_reason (nullable),
pet_friendly, wheelchair_accessible, parking_nearby, age_restriction (nullable int),
dress_code (nullable), photography_allowed, noise_level, reservation_required,
outside_food_allowed, family_friendly, food_available

---

### Table: `hours_overrides`
Date-specific hour exceptions. Evaluator checks this before weekday columns.
Columns: venue_id (FK), date (TEXT "YYYY-MM-DD"), override_type ("closed"|"modified"),
hours (nullable TEXT, only if modified), reason (e.g. "Christmas Day")

---

### Table: `tags`
Inverted index — tags as rows, venues as a list. Primary search index for both planning agent and auto-gen agent. Auto-gen agent queries this table for consistency ("what venues already have this tag") and inspiration ("what tags exist in this city"). 
Columns: tag (TEXT), city (TEXT), venue_id (TEXT FK), yelp_visible (INTEGER 0/1)
Query all tags for a venue: WHERE venue_id = X
Query what Yelp exposes: WHERE venue_id = X AND yelp_visible = 1  
Query all hidden-gem venues: WHERE tag = 'hidden-gem' AND city = X
A non-flat version of the full tag set is stored in venues ground truth JSON for reference only — all search goes through this table.

---

### Table: `ticket_availability`
Columns: venue_id (FK), date (TEXT), status ("available"|"limited"|"sold_out")

---

### Table: `yelp_listings`
One row per venue. Planning agent visible via search_yelp. Separate from venues so search_yelp never accidentally exposes ground truth fields.
Columns: venue_id (FK), city, name, category, district, stars, review_count,
yelp_hours_mon/tue/wed/thu/fri/sat/sun (potentially stale),
top_review_snippet, official_url (nullable), yelp_popularity_score
Labels exposed by Yelp: queried from tags table WHERE yelp_visible = 1.

**yelp_popularity_score** — derived from traffic_tier + small random jitter at generation time.
- high → 0.7–1.0, mid → 0.4–0.7, low → 0.1–0.4

**total_results** — on venue row. Simulates "2,847 results found" hotness signal. Set from traffic_tier at generation, consistent across all calls.

---

### Table: `source_docs`
Blogs, forums. Not official sites (separate table).
Columns: doc_id (8 random chars), city, doc_type ("blog"|"forum"), title, author,
source_name, date, likes, saves, view_count, body, page_status ("draft"|"committed"|"verified")

**page_status** tracks the git-style workflow state (see AUTO-GEN WORKFLOW below).

---

### Table: `doc_venue_refs`
Which venues does each doc mention. Simple flat join.
Columns: doc_id (FK), venue_id (FK)

---

### Table: `doc_venue_roles`
Role of each doc relative to each venue's wrong-info entry.
Columns: doc_id (FK), venue_id (FK), role ("incorrect_source"|"truth_carrier"|"neutral"),
wrong_info_id (nullable FK to wrong_info.wrong_info_id)
A single doc can be truth_carrier for venue A and neutral for venue B.

---

### Table: `wrong_info`
One row per wrong-info entry. Proper table (not JSON on venue) for clean foreign key from doc_venue_roles.
Venues with no wrong info simply have no rows here — no space wasted.
Columns: wrong_info_id (8 random chars), venue_id (FK), affected_field,
incorrect_value, correct_value, source_type ("yelp"|"blog"|"forum"),
wrong_info_category ("temporal_decay"|"propagation_error"|"conditional"|"subjective"),
origin_story

---

### Table: `official_site_docs`
One-to-one with venues where has_official_site=true. Always authoritative.
Columns: venue_id (FK as doc_id), city, url, retrieved_date, body,
hours_mon/tue/wed/thu/fri/sat/sun (correct), full_regulations (JSON — acceptable here since
evaluator reads it as a blob, no per-field querying needed at this layer),
full_labels (TEXT comma-separated), ticket_availability (JSON), active_event (JSON nullable)

Planning agent accesses this via fetch_url(url) — not by venue_id directly.

---

### Table: `city_config`
One row per city.
Columns: city, display_name, country, centre_lat, centre_lng, radius_km,
local_cuisine_label, task_dates (JSON array), weather_notes

---

**Ground truth fields never exposed to planning agent:**
role, truth_carrier, wrong_info entries, venue_difficulty_score, traffic_tier,
total_results raw value, all venues table columns, tags.yelp_visible=0 rows.

---

## AUTO-GEN WORKFLOW (git-style)

**Page lifecycle — mirrors git commit flow:**
1. `CREATE_PAGE(entity_type, venue_id?)` → creates empty DB row, returns page_id + list of all required fields with types and current values (all null). entity_type: "venue" | "yelp_listing" | "source_doc" | "official_site_doc"
2. `FILL(page_id, {field: value, ...})` → batch update fields, returns updated page state showing filled and remaining null fields. Accepts single-key dict for one field. Supports override — call FILL again on any field to change it.
3. `COMMIT(page_id)` → runs completeness check (all required fields filled, no nulls on required columns). Returns full page content for agent review. Sets page_status = "committed". Equivalent to git commit — local work is saved and reviewable.
4. `VERIFY(page_id | "all")` → runs consistency checks on committed page(s). Returns structured error report or pass. Sets page_status = "verified" on success.
5. `SUBMIT` → called only after all pages for this venue are verified. Marks venue as complete.

**On VERIFY failure:**
- Returns structured error report (see below)
- Deletes the failed page's row from DB (prevents half-written data accumulating)
- Agent receives error report, calls RETHINK, redrafts from the failed page

**VERIFY error report format:**
```json
{
  "passed": false,
  "errors": [
    {
      "check": "incorrect_hours_diff",
      "detail": "yelp_hours_fri matches ground truth hours_fri — no stale difference present",
      "affected": ["venue:x4kR9mQ", "yelp_listing:x4kR9mQ"]
    },
    {
      "check": "regulation_visibility", 
      "detail": "pet_friendly=false but no source doc body mentions this for venue x4kR9mQ",
      "affected": ["venue:x4kR9mQ"]
    }
  ]
}
```

**VERIFY checks (finite, well-defined):**
- incorrect_hours_diff: yelp hours differ from ground truth on the designated stale day
- regulation_visibility: every false/restricted regulation mentioned in at least one source doc body
- label_subset: all yelp_visible tags exist in venue's full tag set
- official_site_exists: if has_official_site=true, official_site_docs row exists
- truth_carrier_registered: every wrong_info entry has at least one doc_venue_roles row with role="truth_carrier"
- recommended_pace_assigned: recommended_pace is not null
- hours_override_coverage: task dates with known holidays have hours_overrides rows if venue is affected

**Agent can query current state at any time:**
`GET_STATUS(venue_id)` → returns list of all pages for this venue with their page_status, which fields are filled, which are null.

---

## TASK SCHEMA

**avg_venue_difficulty** — task metadata field. Computed by sampling 10 feasible venue sets (pure arithmetic, no LLM, no full evaluator). Algorithm: filter pool by hard constraints → randomly assemble sets where sum(recommended_visit_minutes) + flat travel estimate fits day hours → average venue_difficulty_score across samples → store on task row.

**Task occasions** — declared in task public_input / rubric. Valid values: `family | date | anniversary | honeymoon | business | birthday | retirement | school_trip`. No suitable_occasions field on venues — agent deduces fit from existing venue data.

---

## WRONG INFORMATION DESIGN

**Not every venue gets wrong info.** Decision is made by the generation agent based on venue type + traffic_tier + plausible origin story. High-traffic venues with active owners are unlikely candidates for stale Yelp hours.

**Source-content fit rule** — every wrong info entry must have a one-sentence origin story explaining how this specific mistake plausibly entered this specific source type. If it can't be written convincingly, the wrong info doesn't belong.

**Wrong info categories in scope for generation:**
- Category 1 (temporal decay) — generatable programmatically
- Category 2 (propagation error) — generatable, requires multi-source placement
- Category 3 (conditional/contextual) — generatable
- Category 4 (subjective threshold) — hand-designed per city, not auto-generated
- Category 5 (adversarial) — out of scope

**Explicit unusual claims are more credible than implicit unusual assumptions.** Wrong seasonal info should look like a source that lists the unusual day casually with no awareness — not a source that explicitly highlights the exception.

**Detection measurement** — evaluator checks whether planning agent's tool_call_log contains any call that returned a truth_carrier doc for that wrong-info entry. Three outcomes: found + used (full credit), found + ignored (partial), never found (lucky guess or error or correct uncertainty flag).

**Redesign (2026-06, `valid-difficulty-redesign` branch) — see `docs/VALID_DIFFICULTY_REDESIGN.md`.** A pilot showed the faulty environment did not add difficulty (corruption missed the rubric; truth was free; resolution was off the scoring path). The redesign keeps the categories above but reframes flaws as reusable, venue-intrinsic **structures** authored by the venue-gen agent, placed on the fields a task's binding constraint reads, with two difficulty axes (detectability, repairability) and a cheap **certifier plugin** for validity. Notable changes to the above list: Category 2 (propagation) becomes **auto-generable** (copying bloc); the mandatory truth-carrier becomes **optional** (empirical recoverability via the certifier); new structures added (minority-truth conflict, multi-truth omission, entity-resolution, no-truth control). Detection measurement → a **3-tier F2c credit** (not-retrieved / retrieved-but-corrupt-value-used / retrieved-and-true-value-applied). Flaw-type taxonomy folded into `handbook.py` (wrong_info_rules → "REDESIGN TARGET" block).

---

## SEARCH & RETRIEVAL

**search_blogs_and_forums returns** — top N docs by relevance (BM25 + recency boost), plus total_results count from venue row. Planning agent sees the hotness signal without needing thousands of real documents.

**search_yelp returns** — venues sorted by yelp_popularity_score descending, filtered by query params. Score derived from traffic_tier at generation, stored on yelp_listings row.

---

## GENERATION PIPELINE ORDER

1. RESEARCH_CITY — LLM auto-generates city config from city name alone
2. Per-venue agent loops (parallel) — venue pool + single-venue docs written to DB
3. validate_city.py partial — venue pool structure only
4. Task generation (Sprint B)
5. Multi-venue document generation — runs AFTER task generation, informed by task set
6. validate_city.py full — includes doc coverage + task-doc coherence
7. avg_venue_difficulty computation — stored in tasks table

**Multi-venue docs run after task generation** — so the document corpus deliberately supports the task set. A task asking for best bars should have at least one doc that compares bar options. Easy tasks can have docs that almost directly answer the question. Hard tasks have relevant info buried or requiring cross-referencing.

---

## CITY CONFIG

**Target: 40 venues per city** — 20 food/drink + 20 sights as independent pools (not combined total). Each pool needs 2x slack over 5-day usage independently.

**Human input = city name only.** RESEARCH_CITY tool auto-generates coordinates, districts, cuisine variety, weather, venue archetypes from LLM world knowledge.

**local_cuisine_label** — string on city config (e.g. "french", "japanese"). Venues store only a boolean.

**Venue pace distribution target** — ~30-35% intense / 35-40% moderate / 25-30% relaxed. City-specific mix, not a fixed template across cities.

---

## AUTO-GEN AGENT BEHAVIOUR

**Reading pattern** — agent receives its brief upfront (venue category, district, traffic tier, wrong-info decision). Drafts everything fresh without reading existing corpus. Only reads during VERIFY — reads back what it just wrote to check consistency. No general corpus exploration needed.

**VERIFY fail → delete + log** — on failed VERIFY: generate a structured log entry showing exactly which check failed and why → delete all incomplete/inconsistent rows for this venue from the DB → agent receives the log → RETHINK → redraft from the failed step. Prevents half-written data accumulating across failed attempts.

**Tools scope** — DRAFT/STORE tools for writing each entity, READ tools for VERIFY only, VERIFY, RETHINK, SUBMIT. Format reference via -h handbook queries.

---

## PLANNING AGENT TOOLS

**fetch_url(url) replaces get_official_site(venue_id)** — planning agent never gets a magic official-site lookup by venue ID. It finds a URL from a Yelp listing or blog post mention, then calls fetch_url(url) to retrieve the page content. The DB serves the official_site_docs content when the URL matches a known official site. This is more realistic and creates a natural difficulty gradient — venues whose URL only appears in a forum post are harder to verify than those whose URL is in the Yelp listing.

---

## EVALUATOR LLM JUDGE SCOPE

Deferred — exact scope of which P-score constraints require LLM judging vs deterministic code will be audited during the personalised task pipeline design (Sprint B). Principle: LLM judge only for genuinely semantic constraints that cannot be reduced to field checks.

---

## AGENT HANDBOOK & TOOL SET

**Handbook is a deterministic Python function** — not a file read or LLM call. Pattern-matches the query string and returns hardcoded documentation text. Fast, no token overhead beyond the response. When schema or tools change, update the handbook function — not the prompt.

**Query interface:**
- `-h function` — lists all available tool names with one-line descriptions
- `-h function <name>` — full spec for one tool: description, input parameters + types, return format + example
- `-h format` — lists all available format names
- `-h format <name>` — exact schema for that format: every field, type, allowed values, minimal filled example

**Complete tool set (8 tools):**

| Tool | Purpose |
|---|---|
| `HELP(query)` | Query the handbook — any `-h` style string |
| `CREATE_PAGE(entity_type, venue_id?)` | Start a new DB page, returns page_id + required fields |
| `FILL(page_id, {field: value, ...})` | Batch fill fields, supports override, single-key dict for one field |
| `COMMIT(page_id)` | Completeness check, sets page_status="committed", returns full page for review |
| `VERIFY(page_id / "all")` | Consistency checks, returns structured error report or pass |
| `GET_STATUS(venue_id)` | Returns all pages for this venue with page_status and null fields |
| `RETHINK(thought)` | Log correction plan after failed VERIFY, before redrafting |
| `SUBMIT` | Mark venue complete — only callable after all pages verified |

**What the prompt needs because of this design:**
Prompt can be short. It tells the agent: who it is, the workflow (CREATE_PAGE → FILL → COMMIT → VERIFY → SUBMIT), that HELP is available for all specs and formats, and the brief for this specific venue. Full schemas and tool specs stay out of the system prompt — agent queries them on demand.

**Prompt assignment block — orchestrator injects only:**
- City
- Venue category
- District
- Traffic tier

Wrong-info decision is entirely the agent's judgment — not provided by orchestrator.
Agent consults `-h format wrong_info_rules` and `-h format traffic_tier` before deciding.

**Two mandatory handbook pages beyond tool/format specs:**
- `-h format wrong_info_rules` — five wrong-info categories, source-content fit rules, example origin stories. Agent reads this before making any wrong-info decision.
- `-h format traffic_tier` — what each tier means practically: total_results range, source doc count, correction ecosystem strength, how prominent/buried wrong info should be relative to corrections.

**Quality principles in prompt are pointers, not explanations:**
```
Before making any wrong-info decisions, query:
  HELP("-h format wrong_info_rules")
  HELP("-h format traffic_tier")
```
Detail lives in handbook. Prompt stays short and stable.

---

## REMOVED / DEPRECATED FIELDS

**`family_friendly`** — removed from VERIFY regulation_visibility checks and REQUIRED_ON_COMMIT.
Rationale: ambiguous (doesn't distinguish "children banned" from "just not child-focused"),
overlaps with `age_restriction` (explicit, unambiguous) and tags like "adults-only", "late-night".
Generated false positives on venues like specialty cafes. Field stays in DB schema for Paris
backward compatibility but is not benchmark-critical going forward.
Use `age_restriction` for hard age constraints, tags for vibe signals.

---

## Tag canonicalization (P1, April 2026)

Tags are stored in hyphenated kebab-case: `free-entry`, `wheelchair-accessible`, `dog-friendly`.
Underscores (`free_entry`) and spaces (`free entry`) are never stored — `tool_SET_TAGS` in
`agent_tools.py` canonicalises at write time via `s.strip().lower().replace("_","-").replace(" ","-")`.

Query-side normalisation (both sides of tag comparisons) is applied as a defence layer in
`pool_utils.py` and `constraint_engine.py` to handle any legacy data that predates this rule.

The planning prompt in `generate_city_venues.py` also states the canonical form requirement
so LLM-generated `coverage_tags` arrive pre-normalised.
