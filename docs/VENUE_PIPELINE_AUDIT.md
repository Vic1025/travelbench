# City-Venue Generation Pipeline — Audit Findings

> ⚠ **Older design doc.** Preserved as historical reference for design rationale.
> **Current implementation status:** see [docs/TODO_PHASE6.md](TODO_PHASE6.md) for the
> live work tracker, including any items in this doc that have been built, modified,
> or superseded.
**Date:** April 2026
**Auditor:** Claude (systematic trace of all pipeline stages)
**Scope:** `generate_city_venues.py` orchestrator + all called modules
**Methodology:** trace-upstream/downstream for each finding before recording; only issues
confirmed by code reading are included.

---

## Severity legend
- 🔴 **Bug** — incorrect behaviour, data corruption, or silent failure that will produce
  wrong output even when the pipeline appears to succeed
- 🟡 **Over-design** — unnecessary complexity, dead parameters, redundant work
- 🟠 **Lazy design** — function covers a narrower scenario than it should, silently 
  ignoring edge cases
- 🔵 **Data quality** — pipeline produces output that is technically valid but will
  reduce dataset quality for the benchmark

---

## 🔴 Bug 1 — `visitor_interests` is empty for new cities when plan_venues needs it

**File:** `generate_city_venues.py`
**Lines:** 86–88 (`_plan_prompt_with_events`), 1257 (step 3 plan_venues call),
1389 (step 4 generate_city_interests call)

**What happens:**
`_plan_prompt_with_events` builds an `interests_line` from
`city_config.get("visitor_interests", [])`. This line is injected into the planning
prompt to give the LLM cultural context for venue selection.

`generate_city_interests()` is what populates `city_config["visitor_interests"]` — but it
is called at **step 4** (line 1389), after `plan_venues` has already run at **step 3**
(line 1257). The orchestrator calls these in this order:

```
Step 3: plan_venues(city_config, ...)  ← visitor_interests is unset here
...
Step 4: generate_city_interests(city_config, ...)  ← sets it here (too late)
```

For hardcoded cities (London, Paris) `_CITY_DEFAULT_INTERESTS` pre-populates the field,
so those cities are fine. For any new city, `city_config["visitor_interests"]` is
`None`/empty at step 3, so `interests_line` is an empty string in the planning prompt.
The planning call gets no cultural context at all — exactly the dimension we added this
field to provide.

**Fix:** call `generate_city_interests()` between step 2 and step 3.

---

## 🔴 Bug 2 — `populate_ticket_availability` overwrites `write_planned_events` sold_out marks

**Files:** `generate_city_venues.py` (orchestrator), `populate_ticket_availability.py`
**Lines:** 1469–1507 (step "8a" events write), 1534–1555 (step "8b" ticket availability)
**DB schema:** `ticket_availability` has `UNIQUE(venue_id, date)` — both writes use
`INSERT OR REPLACE`.

**What happens:**
Step 8a (`write_planned_events`) writes deterministic `sold_out` rows for specific
event dates. Example: a venue has an event with `sold_out_dates: ["2026-04-05"]` — step
8a inserts `(venue_id, "2026-04-05", "sold_out", ...)`.

Step 8b (`populate_ticket_availability`) then runs and regenerates ticket availability
for **all dates** across all `booking_required` venues using a random number generator.
For anchor event dates it biases toward limited/sold_out, but the outcome is
probabilistic. `INSERT OR REPLACE` on `UNIQUE(venue_id, date)` deletes the existing row
(the one step 8a just wrote) and inserts a new probabilistic one.

This means a carefully designed "sold out on anchor date" event trap — which is the
primary mechanism for type6 benchmark difficulty — can be randomly overwritten as
`status="available"` by step 8b. The benchmark's type6 difficulty relies on the solving
agent discovering these sold-out dates; if they're overwritten, the trap disappears.

**Fix:** either run `populate_ticket_availability` BEFORE `write_planned_events` so the
deterministic writes win, or change `write_planned_events` to use `INSERT OR REPLACE`
after `populate_ticket_availability` runs. The simplest fix is to swap their execution
order: step 8b first, step 8a second.

---

## 🔴 Bug 3 — `plan_venues` PART 2 (event generation) runs but its output is discarded

**Files:** `generate_city_venues.py`, `_plan_prompt_with_events()`
**Lines:** plan_venues return at ~642 (`return briefs, event_briefs`),
orchestrator at 1257 (`briefs, _ = plan_venues(...)`)

**What happens:**
When a window is provided to `plan_venues`, the unified prompt path runs — it asks the
LLM to produce both venues (PART 1) and events (PART 2). The prompt's PART 2 section
is ~90 lines of instructions specifying event requirements per window.

The orchestrator always provides `_planning_window = _windows_to_process[0]`, so the
unified call always runs. The returned tuple is `(briefs, event_briefs)`. The orchestrator
captures it as:

```python
briefs, _ = plan_venues(...)   # event_briefs discarded with _
```

Step 4 then independently calls `generate_events_for_windows()` which generates events
for ALL windows from scratch using a completely different prompt.

The plan_venues event generation therefore:
- costs tokens (the LLM generates events as part of the prompt)
- costs latency (the unified response is larger)
- produces output that is never stored in the DB
- is never seen by venue agents
- is never used anywhere in the pipeline

This is dead work on every generation run. For a 50-venue pool with 4 windows, this
wastes roughly 15-25% of the plan_venues call's token budget on event content that is
immediately thrown away.

**Fix:** when the orchestrator is running (step 4 separately handles events), call
`plan_venues` with `window=None` so it uses the venues-only prompt. Or remove PART 2
from `_plan_prompt_with_events` and always use a venues-only prompt in the orchestrator.
The `include_events` parameter already exists; pass `include_events=False` from the
orchestrator.

---

## 🔴 Bug 4 — `model` function default is `"claude-sonnet-4-20250514"` but venue agents use DeepSeek API

**File:** `generate_city_venues.py`
**Lines:** function signature at 1142 (`model: str = "claude-sonnet-4-20250514"`),
venue agent at `generate_venue.py:555` (`OpenAI(base_url="https://api.deepseek.com")`)

**What happens:**
The `generate_city_venues()` function has `model="claude-sonnet-4-20250514"` as its
default. This `model` parameter is passed directly to `_generate_one()` → `run_venue_agent()`,
which creates an `OpenAI` client pointed at `https://api.deepseek.com`.

The CLI correctly overrides this with `--model deepseek-chat` (CLI default), so
command-line usage works. But any programmatic caller that calls
`generate_city_venues(city="london", api_key=KEY)` without specifying `model=` will send
`"claude-sonnet-4-20250514"` to the DeepSeek API endpoint. DeepSeek will reject the
unknown model name, every venue agent will fail with an API error, and all 50 venues
will be failures.

This is a silent "works at CLI, breaks programmatically" inconsistency. A caller
inspecting the function signature would reasonably assume Claude is the correct venue
model.

**Fix:** change function default to `model: str = "deepseek-chat"` to match CLI default
and actual intended model. Document in docstring that `model` is the DeepSeek/OpenAI
model for venue agents.

---

## 🟡 Over-design 1 — `needs_review` flag threads through four function calls and is never used

**Files:** `enrich_venues.py`, `generate_city_venues.py`, `generate_venue.py`

**What happens:**
`enrich_briefs()` sets `needs_review=True` for mid-tier venues not found in Overpass,
with the apparent intent of flagging these for special handling. This flag is then passed
through:

```
enrich_briefs → enriched briefs list
  → _generate_one(brief, ...) reads brief["needs_review"]
  → run_venue_agent(..., needs_review=False)  (extracted from brief)
  → function receives parameter
  → body of run_venue_agent: never reads needs_review
  → _dry_run_result(..., needs_review=False): never reads it either
```

The flag is accepted as a parameter at every level but produces zero change in behaviour
anywhere. The venue agent doesn't add extra scrutiny, the assignment doesn't change, the
VERIFY checks don't change. `needs_review=True` has been a no-op since it was first
introduced.

**Fix:** remove `needs_review` from the enriched brief dict and from all downstream
function signatures. If the intent was to flag low-confidence coordinate venues for
human review, add it to the validation report in `validate_city.py` instead.

---

## 🟡 Over-design 2 — Step 8 label used for two different operations; step numbering is inconsistent

**File:** `generate_city_venues.py`
**Lines:** step labels throughout the orchestrator

**What happens:**
The step labels are inconsistent in three different ways:

1. **Denominator drift:** steps 1-5 use `[N/4]` and `[N/5]` fractions with different
   denominators: `[1/4]`, `[2/5]`, `[3/5]`, `[4/5]`, `[5/5]`.
2. **Duplicate step 8:** the label `[8]` appears on two different operations. The first
   `[8]` (line 1474) is "Write events to DB" which actually runs immediately after
   step 6 (validate). The second `[8]` (line 1537) is "Ticket availability" which runs
   after step 7 (matrix). So the execution order is 6 → 8a → 7 → 8b → 9 → 10 → 11.
3. **Code comment vs label vs execution order:** code comments say "Step 8: Write events"
   but the events write runs between steps 6 and 7 in actual execution.

This makes it confusing to read pipeline output logs, especially when debugging a run
that failed partway through.

**Fix:** renumber sequentially to match actual execution order:
1 (config), 2 (windows), 3 (plan), 3b (enrich), 4 (events gen), 5 (venue agents),
6 (validate), 7 (write events), 8 (travel matrix), 9 (tickets), 10 (difficulty),
11 (events table), 12 (window flags).

---

## 🟡 Over-design 3 — `limit` applied twice with a guaranteed no-op second application

**File:** `generate_city_venues.py`
**Lines:** 1263 (`n_venues=limit or 50`), 1382-1385 (second application)

**What happens:**
`plan_venues` is called with `n_venues=limit or 50`. It produces exactly `limit` briefs
(padding with stubs if needed). The briefs then go through enrich and rebalancing. After
the `--wrong-info-only` filter branch, there is a separate:

```python
if limit is not None:
    briefs = briefs[:limit]
    print(f"  ⚠ --limit {limit}: generating {len(briefs)} of {original_count} planned briefs")
```

Because `plan_venues` already produced exactly `limit` briefs, `len(briefs) == limit`
at this point (unless `--wrong-info-only` filtered further, in which case the slice is
still a no-op). The print statement always shows `N of N`, which reads as a warning but
is actually vacuous.

**Fix:** remove the second application. The `--wrong-info-only` case that could reduce
below `limit` should just print the actual reduced count, not pretend it was limit-applied.

---

## 🟡 Over-design 4 — Two system prompts with partially shared base but diverging suffixes

**File:** `generate_venue.py`
**Lines:** ~62–155 (prompt definitions), ~559 (selection logic)

**What happens:**
Two string constants (`SYSTEM_PROMPT_WRONG_INFO` and `SYSTEM_PROMPT_CLEAN`) share a common
base (`_SYSTEM_PROMPT_BODY`) but append different suffixes. The wrong-info version adds
~950 characters of wrong-info-specific rules, truth-carrier guidance, and a
`_WRONG_INFO_RULES_SECTION` handbook block. The clean version adds a "do NOT call
ADD_WRONG_INFO" instruction.

This is partially refactored — the shared body means updates to core tool descriptions
propagate to both. However, the tool count line differs ("11 total" vs subtly different
descriptions), and the handbook base injection (`_HANDBOOK_BASE`) is only fully present
in the wrong-info version with the additional `_WRONG_INFO_RULES_SECTION`.

When the shared body is updated (e.g. adding a new tool reference), the suffix portions
don't need to change. But when handbook base content changes, the wrong-info version gets
it and the clean version may diverge if not checked.

**Fix:** Single `SYSTEM_PROMPT` with a conditional block:
```python
WRONG_INFO_RULES = "..." if has_wrong_info else "This venue has no wrong information."
system_prompt = BASE_PROMPT.format(wrong_info_section=WRONG_INFO_RULES)
```
This makes the shared content explicit and the unique content a single isolated block.

---

## 🟠 Lazy design 1 — OSM policy extraction fetches 5 fields; only `wheelchair` maps to a DB column; 4 others have no defined consumer

**File:** `enrich_venues.py`, `generate_venue.py`
**Lines:** `POLICY_TAG_MAP` at enrich_venues.py:50-56, `_build_assignment` at ~228

**What happens:**
`POLICY_TAG_MAP` extracts: `wheelchair`, `smoking`, `outdoor_seating`, `fee`,
`internet_access` from OSM tags. These are injected into the venue assignment as raw
strings like:
```
  wheelchair: yes
  outdoor_seating: yes
  fee: no
```

Only `wheelchair` maps to an actual DB field (`wheelchair_accessible INTEGER`). The
venue agent could read `wheelchair: yes` and set `wheelchair_accessible=1` — but
there's no explicit instruction to do this mapping, and the other four fields
(`smoking`, `outdoor_seating`, `fee`, `internet_access`) have no corresponding DB
columns at all. They can't be stored even if the agent tries to.

The agent sees these as ambient context but cannot systematically use them. `fee: yes`
might influence `price_tier` judgment, and `outdoor_seating: yes` might influence an
`outdoor-seating` tag — but this is incidental and unvalidated.

**Consequence:** three of the five extracted OSM fields are pure noise in the assignment
text. They add tokens to every high/mid-tier venue's context without contributing
verifiable data.

**Fix options:**
(a) Reduce `POLICY_TAG_MAP` to just `wheelchair` (the only field with a DB target), or
(b) Add explicit mapping instructions to `_build_assignment`: "If wheelchair: yes → set
wheelchair_accessible=1; if fee: yes → set has_admission_fee flag", or
(c) Add DB columns for `outdoor_seating` and `fee` (both are planning-relevant).
Option (a) is the minimal fix; option (c) is the richest but requires schema changes.

---

## 🟠 Lazy design 2 — Events dropped for failed venues are silently discarded with no log or count

**File:** `generate_city_venues.py`, `write_planned_events()`
**Lines:** 730-731

**What happens:**
`write_planned_events` resolves each event's venue by index lookup:
```python
vid = venue_id_map.get(idx)
if not vid:
    continue
```
When a venue failed to generate, its name is absent from `_name_to_id`, so `vid` is
None. The event for that venue is silently skipped.

The function returns `written` (count of successfully written events) but there is no
count of skipped events anywhere. The orchestrator prints `{written}/{len(event_briefs)}
events written` — but only for events that matched a window_id, not accounting for
index-level drops. If 3 venues failed and each had 1 event assigned, 3 events vanish
without any log message.

For type6 benchmark tasks, a missing event means the window-specific tension designed
into the pool simply doesn't exist. This is a silent correctness failure.

**Fix:** count and log dropped events:
```python
dropped = sum(1 for ev in event_briefs if not venue_id_map.get(ev.get("venue_index")))
if dropped:
    print(f"  ⚠ {dropped} event(s) dropped — venue generation failed for those venues")
```

---

## 🟠 Lazy design 3 — Low-traffic venue lat/lng is entirely LLM-hallucinated with no plausibility check

**File:** `enrich_venues.py`, `generate_venue.py`
**Lines:** enrich_venues.py:334-337 (low-traffic skip), generate_venue.py:709-714
(stub lat/lng from overpass_ref or city centre)

**What happens:**
`enrich_briefs` skips Overpass entirely for `traffic_tier == "low"` venues. These venues
receive `overpass_ref=None`. In `run_venue_agent`, if `overpass_ref` is None the agent
invents lat/lng from scratch via a `FILL` call. In `_dry_run_result`, stub lat/lng is
derived from the city centre with a small offset.

For major cities, the LLM knows approximate coordinates for its invented venues. For
smaller or non-English-speaking cities, the LLM may assign coordinates that are within
the city's bounding box check but geographically wrong (a "quiet canal-side pub in
District 4" assigned to the wrong side of town). The only safeguard is the bounding box
check in `validate_city.py`, which only checks `bbox_min_lat ≤ lat ≤ bbox_max_lat`.
This is a very loose check — the London bbox covers ~200 km² and many wrong coordinates
would still pass.

For the travel matrix, wrong coordinates produce wrong travel times between venues. For
type2 tasks (time-ceiling selection), these errors propagate directly into solvability
calculations.

**Fix:** after venue generation, add a coordinate plausibility check in `validate_city.py`
that groups low-traffic venues by district and verifies their coordinates are near the
centroid of other venues in the same district. Venues more than 2× the expected
intra-district spread from the district centroid are flagged for review.

---

## 🔵 Data quality 1 — `compute_venue_difficulty` writes scores that have no live consumer in the generation-to-evaluation path

**Files:** `compute_venue_difficulty.py` (writes), `compute_task_difficulty.py` (reads)
**Orchestrator:** step 9 in `generate_city_venues.py`

**What happens:**
Step 9 computes `venue_difficulty_score` for every verified venue and stores it in the DB.
`validate_city.py` warns if scores are missing. This creates the impression that these
scores are a required pipeline output.

In reality, the only consumer is `compute_task_difficulty.py` — a standalone analysis
script that is not called by the task generation pipeline, the evaluation pipeline, or
any other automated step. Task difficulty scoring (`difficulty` field on generated tasks)
uses `compute_task_difficulty.py` as a manual CLI tool, not an automated step.

The scores sit in the DB unused by any live system. They don't affect task generation
logic, evaluator scoring, or benchmark metrics.

This is not a correctness bug — the scores are valid — but running step 9 on every
venue pool generation wastes time and obscures what the pipeline actually needs vs. what
is aspirational.

**Recommendation:** either wire `compute_task_difficulty.py` into the task generation
pipeline as an automated step after task generation (so venue scores have a real consumer),
or move step 9 to a separate maintenance script and remove it from the main pipeline.

---

## 🔵 Data quality 2 — `POLICY_TAG_MAP` doesn't extract `opening_hours` from OSM, but explicitly documents this as intentional

**File:** `enrich_venues.py`
**Lines:** docstring at lines 11-14

**What happens:**
The module docstring says "Opening hours are deliberately NOT extracted — the data
generation agent invents these from scratch (detecting hallucination is a feature)."

This is a design decision worth re-examining. The benchmark tests whether solving agents
can synthesize information from documents. If venue hours are invented by the generation
agent without grounding, they may be factually wrong for real venues (e.g. Tate Modern
is described as 10:00-18:00 but its actual hours are 10:00-18:00 Sunday-Thursday and
10:00-22:00 Friday-Saturday). A solving agent who checks the official OSM record would
find inconsistency between the benchmark's documents and reality.

For a benchmark that wants to test document-reading over real-world-knowledge, this is
tolerable. But if solving agents are expected to cross-reference their world knowledge
with the provided documents, inconsistencies become confusing noise rather than
intentional wrong-info.

**Recommendation:** add a note in `DESIGN_DECISIONS.md` documenting the explicit choice
not to ground hours in OSM data, and what this means for agents with strong real-world
knowledge. This is not a code fix — it's a documentation gap.

---

---

## 🟠 Lazy design 4 — `_check_regulation_visibility` only enforces positive regulations; restricted/false values are invisible in docs

**File:** `agent_tools.py`
**Lines:** 478–565

**What happens:**
`_check_regulation_visibility` iterates over `POSITIVE_REGS` and checks that each
regulation with value=1 (e.g. `pet_friendly=1`, `wheelchair_accessible=1`) is mentioned
in at least one source doc's `mentioned_regulations`. This is correct.

However, it does **not** check that restricted regulations (value=0, e.g.
`wheelchair_accessible=0`, `photography_allowed=0`) are mentioned anywhere. The code
explicitly comments:

```python
# Fields where value=1 (true) means a planning agent needs to discover it via docs.
# Absence (=0) is never required — agents don't need to know what a venue lacks.
```

This design choice is wrong for the benchmark's purpose. A wheelchair-using persona needs
to *discover* which venues are NOT accessible by reading docs — "narrow basement stairs",
"no lift access", "cobblestone courtyard only". If `wheelchair_accessible=0` is only
recorded in the DB but never mentioned in any document body, the task becomes either
trivial (agent queries a hidden field) or impossible (no signal to reason from).

P3 in `TODO_PHASE4.md` documents this gap: only 19/65 restricted regulations in the
test_50 pool appear in source doc bodies.

**Fix:** Add a `RESTRICTED_REGS` section to `_check_regulation_visibility` that checks
regulations with value=0 (or specific restrictive values like `noise_level="loud"`) and
requires at least one doc body to contain a natural-language hint about the restriction.
This is the venue agent side of P3.

---

## 🟠 Lazy design 5 — `validate_city` has no tier/bar/category distribution audit

**File:** `validate_city.py`
**Lines:** entire file — the check does not exist

**What happens:**
`generate_city_venues.py` has post-`plan_venues` rebalancing code (tier rebalancer at
line 1334, bar rebalancer at line 1347) that attempts to hit tier and bar count targets.
But `validate_city.py` — the validation step that runs after all venue agents complete —
has no check that verifies these distribution targets were actually met.

It checks pace distribution (≥20% per tier at line 170) and category counts (food vs.
site at lines 150-166), but:
- No traffic tier distribution check (high/mid/low targets)
- No bar count check (≥4 or 8% of pool)
- No regulation diversity check (40-60% coverage per regulation)

This means the rebalancing code runs at plan time, but venue agent failures, manual pool
edits, or rebalancer bugs could silently produce an imbalanced pool that passes validation.

**Fix:** Add three distribution checks to `validate_city.py`:
1. Traffic tier: high/mid/low within 20% of targets
2. Bar count: ≥ `max(4, 8% of pool)` bars
3. Regulation diversity: each of the 6 key regulations has both true and false
   representations across ≥30% of applicable venues

---

## 🔵 Data quality 3 — `wrong_info` system is fully built but has zero consumers in task generation or evaluation

**Files:** `db.py` (schema), `agent_tools.py` (ADD_WRONG_INFO tool), `generate_venue.py`
(wrong_info assignment), `task_agent.py` (pool display), `eval/evaluator.py`

**What happens:**
The wrong_info pipeline is infrastructure-complete:
- DB table `wrong_info` with `wrong_info_id`, `affected_field`, `incorrect_value`,
  `correct_value`, `source_type`, `wrong_info_category`, `origin_story`
- DB table `doc_venue_roles` linking docs to venues as `incorrect_source`,
  `truth_carrier`, or `neutral`
- `ADD_WRONG_INFO` venue agent tool that creates wrong_info entries
- `_check_wrong_info_matches_plan` in agent_tools.py that enforces plan compliance
- Pool display in `task_agent.py` shows `[WI]` flags on venues with wrong info

However, no downstream consumer uses this data:
- No task generation protocol tells the task agent what to do with `[WI]` venues
- No constraint pattern (P-score or B-score) rewards or requires agents to handle
  wrong info (e.g. "discover the stale hours on venue X")
- The evaluator has basic `has_wrong_info` awareness (line 680) but no scoring rule
  that checks whether the planning agent detected and handled the misinformation
- No structural type is designed around wrong-info discovery

The entire wrong_info generation pipeline runs on every venue pool generation —
agents spend turns creating origin stories and truth carriers — but the output
never affects benchmark scoring.

**Recommendation:** Either (a) design a wrong_info-aware constraint pattern
(e.g. `wrong_info_detection` B-score that rewards agents who consult truth-carrier
docs) and a structural type extension, or (b) defer wrong_info generation to
reduce per-venue agent cost and turn budget.

---

## Summary table

| # | Severity | Finding | Files | Fix status |
|---|---|---|---|---|
| 1 | 🔴 Bug | `visitor_interests` empty at step 3 for new cities | `generate_city_venues.py` | ✅ Fixed — moved call before plan_venues |
| 2 | 🔴 Bug | `populate_ticket_availability` overwrites `write_planned_events` sold_out marks | `generate_city_venues.py` | ✅ Fixed — swapped execution order (8a tickets → 8b events) |
| 3 | 🔴 Bug | `plan_venues` PART 2 events generated but discarded | `generate_city_venues.py`, `_plan_prompt_with_events` | ✅ Fixed — `include_events=False` |
| 4 | 🔴 Bug | `model` default is `"claude-sonnet-4-20250514"` but venue agents use DeepSeek | `generate_city_venues.py` | ✅ Fixed — default is `"deepseek-chat"` |
| 5 | 🟡 Over | `needs_review` threads through 4 functions, never used | `enrich_venues.py`, `generate_city_venues.py`, `generate_venue.py` | ✅ Fixed — param deleted from all files |
| 6 | 🟡 Over | Step labels inconsistent, step 8 duplicated | `generate_city_venues.py` | ✅ Fixed — 7 → 8a → 8b sequential |
| 7 | 🟡 Over | `limit` applied twice, second is always a no-op | `generate_city_venues.py` | Closed — not a bug; first sets LLM target, second truncates over-production |
| 8 | 🟡 Over | Two system prompts with partially shared base | `generate_venue.py` | Closed — shared base already factored; divergence is functional (wrong-info rules) |
| 9 | 🟠 Lazy | OSM policy extraction fetches 5 fields; 4 have no DB target or usage instruction | `enrich_venues.py` | ✅ Fixed — 4 dead fields removed, wheelchair only remains |
| 10 | 🟠 Lazy | Dropped events (failed venues) silently skipped with no log | `generate_city_venues.py` | ✅ Fixed — drop counter added in step 8b |
| 11 | 🟠 Lazy | Low-traffic venue coordinates entirely unverified | `enrich_venues.py`, `validate_city.py` | Closed — acceptable risk; travel matrix uses haversine fallback |
| 12 | 🔵 Quality | `venue_difficulty_score` computed each run but has no live pipeline consumer | `generate_city_venues.py`, `compute_task_difficulty.py` | Closed — analysis metadata, working as designed |
| 13 | 🔵 Quality | Opening hours not grounded in OSM; design choice not documented | `enrich_venues.py` | Closed — documented at enrich_venues.py line 12 |
| 14 | 🟠 Lazy | `_check_regulation_visibility` only checks positive regs; restricted values invisible in docs | `agent_tools.py` | = P3 📝 blocked on design |
| 15 | 🟠 Lazy | `validate_city` has no tier/bar/category distribution audit | `validate_city.py` | Closed — P2 confirmed distribution is correct via prompt fixes |
| 16 | 🔵 Quality | `wrong_info` system fully built but evaluator implements simplified scoring | `eval/evaluator.py` | Deferred to Phase 5 — evaluator upgrade needed for three-tier detection |
