# TravelBench — Phase 7 Future Work

Captures benchmark concepts that are real and well-formed but intentionally
**not** in Phase 6's scope. These will be revisited after Phase 6 closes.

---

## P7-A — Temporal arc as a reasoning skill

**Origin:** Surfaced during P6-T4b design (Phase 6 wrap-up). Noted by Vic
when reviewing the `ceiling_mode: spread` semantics:

> When the active activity budget is short enough (e.g. 4 activities across
> 3 days), the agent has to think deeply about *which time slot* gives the
> best experience for each activity — dawn dinner for romance, noon for
> indoor visits, cool night for a city walk. The challenge is no longer
> "what fits" but "when does each thing belong".

This is a *different* difficulty axis from type2 subset selection. Type2 asks
"what to include"; temporal arc asks "when to do each thing". A type2-spread
task with a small budget naturally produces both pressures simultaneously,
which is why the concept surfaced here — but temporal arc deserves its own
framing rather than being collapsed into type2.

### Concrete examples

- *Anniversary dinner*: late-evening; not midday.
- *Photography of stained glass*: morning light; not afternoon.
- *Rooftop bar*: sunset / dusk; not lunchtime.
- *Outdoor canal walk*: cool morning or evening in summer; midday is bad.
- *Markets*: peak character early in the day; late afternoon is winding down.
- *Museums in summer*: midday indoor refuge from heat is a genuine reason.

A plan that schedules these in arbitrary slots may be feasible and on-budget
but loses the experience. The benchmark currently does not reward this kind
of reasoning beyond what fits in `time_window=HH:MM-HH:MM` scoped
P-constraints.

### Three implementation options

These are deliberately not chosen yet — Phase 7 work picks one.

**Option (a) — already-expressible.** The current schema permits
`time_window=HH:MM-HH:MM` as a scope on any P-constraint. A task author
already *can* write three time-window-scoped P-constraints to express
"morning museum, midday lunch, sunset rooftop". The cheapest path is to
treat temporal arc as a *usage pattern* of the existing schema and surface
it via the task agent's reasoning protocol — agents would just author more
time-windowed P-constraints when the persona naturally implies them.
- Pros: no new code, no new schema.
- Cons: relies on the task agent voluntarily reaching for the pattern; no
  benchmark-level guarantee that temporal-arc tasks exist in a run.

**Option (b) — new structural type (`type7_temporal_arc` or similar).**
Make temporal arc a first-class benchmark difficulty. Would need:
- A reasoning protocol in `task_agent.py` similar to types 1–6.
- A validator that ensures the task has ≥N P-constraints with
  `time_window` scope, and that each has a natural-language signal in the
  query.
- A solvability check that distinguishes "tight time-of-day requirements
  that all fit" from "contradictory requirements".
- Likely a B-score schema feature for *narrative arc quality* — does the
  schedule build to a peak the persona would actually enjoy?
- Roughly the same engineering scope as adding type6 was.
- Pros: forces the benchmark to cover this reasoning skill explicitly.
- Cons: significant new work; needs design discussion about what makes a
  good temporal-arc task vs. a generic "three time-windowed constraints".

**Option (c) — B-score "schedule arc" feature.**
Cross-cutting bonus available to *any* task. Rewards plans where the
temporal sequence reads naturally for the persona — early-morning park,
midday lunch, afternoon museum (heat refuge), sunset rooftop, late dinner.
LLM judge likely, since "natural" is semantic.
- Pros: smaller footprint than (b); doesn't define a new structural type.
- Cons: doesn't define new difficulty — just rewards thoughtful sequencing
  on top of whatever the task already tests. Less leverage than (b) for
  research purposes.

### Decision criteria

The right option depends on what the benchmark is for. If we want a
**research-grade** instrument that exposes temporal-arc reasoning as a
distinguishable skill, (b) is the answer. If we just want to **reward**
agents that produce well-arced schedules incidentally, (c) suffices. If
neither pressure is strong, (a) is the no-op default.

### When to revisit

After at least one Phase 6 benchmark run completes against London (cleaned
tag vocab + cuisine column + F2e ceiling enforcement). If model scores
cluster tightly because temporal arc isn't tested, that's evidence for (b)
or (c). If models already differentiate well on the existing axes, (a) is
fine.

---

## P7-B — `recommended_visit_minutes` enrichment from external API

**Origin:** P6-T8 audit (Phase 6).

The Phase 6 audit found that LLM-generated `recommended_visit_minutes`
values are mostly accurate (88% plausible on London `test_70`), F4a never
fires (0 of 365 benchmark runs), and the most realistic external sources
(Foursquare Visits) are enterprise-tier paid. Free-tier APIs (Foursquare
Places standard, Google Places) don't publish dwell-time data.

T8 was therefore deferred. Revisit only if:
- A future benchmark run shows F4a firing frequently *and* the failures
  cluster on low-traffic venues (which is where LLM estimates would be
  least reliable).
- A free/cheap dwell-time source emerges.

---

## P7-C — Wrong-info three-layer redesign (Problems 2 + 3)

**Origin:** P6-T11. Problem 1 (data inconsistency: stale tags in
authoritative source) was fixed in Phase 6. Problems 2 (retrieval signal
quality varies by field type) and 3 (does the agent USE the retrieved info?)
remain.

The TODO doc explicitly calls out that these require literature review on
fragile information environment design (misinformation detection,
fact-checking benchmarks, conflicting-source resolution) before scoring
changes can be finalised. Not a code-now item.

---

## P7-D — `budget_overrides` per-day support in type4

**Origin:** P6-T13. Schema sketched but interaction with P22-F evaluator
and the existing `budget_per_day` validator wasn't worked out. Low priority
— current uniform budget produces good type4 tasks.

---

## P7-E — Semantic source-doc quality detection

**Origin:** 2026-05-20 source-doc audit on London `test_70`, plus T17
deferral from P6-T16 design. Four distinct semantic-level audit findings
that the Phase 6 mechanical rules (T14 forum shape, T15 author cap,
T16 declared persona+tone, T18 handle-signature cap, T19 length bounds)
cannot detect. All four share the property that they need either an
LLM-judge call or a sentence-embedding similarity check — neither of
which exists in the current code path.

### Component findings (deferred, all part of the same workstream)

**(1) Negative-regulation lived-experience enforcement.**
Audit example: `wMvYbjpO` (Greenwich Park) wrote
> "Regulation-wise the park has a few rules but they're all standard.
> Dogs on leads near the deer. No photography inside the historic rooms
> of the observatory. Park closes at dusk so don't get caught out."

The handbook rule says negative regulations should be hinted via
experience ("steep stairs down to the bar"), not enumerated as a policy
checklist. Detecting "this paragraph reads as a rule-list rather than
lived experience" requires semantic judgement. The audit found only one
clear instance but it's a high-cost failure mode (breaks the natural
reading experience of the corpus).

**(2) Opener-cliché blacklist (audit Direction C, rejected for Phase 6
as whack-a-mole).**
Audit found:
- "I finally made it" in 34/152 blogs
- "There's something about the [light/moment/way]…" opens 8 distinct blogs
- "I'd been meaning to" in 6+ docs

A literal regex blacklist *would* catch these, but it's brittle and
treats symptoms. A semantic check ("does this opener match any of N
canonical 'first-time-visit excited blogger' templates?") is more robust
but needs LLM-judge or an embedding-based shape detector.

**(3) Within-venue narrative-arc overlap (T17 dropped).**
Audit found Borough Market's three docs follow the same entry → stalls →
paella → accessibility → departure arc with different words. Trigram
similarity on opener (or full body) gives 0.12-0.39 — same range as
truly diverse pairs — so character-level overlap does not separate the
audit's "shared arc, different words" cases. Needs sentence-embedding
similarity (e.g. via OpenAI text-embedding-3-small, or DeepSeek if it
exposes embeddings) computed over each doc's sentence sequence to detect
arc-template reuse.

**(4) Cross-venue cliché detection.**
Audit found "honestly" in 95/152 docs, "I'd been meaning to" across
unrelated venues. These are corpus-wide voice tics that no per-venue
check can catch. Mechanically: track frequency of common phrases across
all committed docs in the city; reject (or warn) when the new doc would
push any phrase above a frequency threshold.

### Why one consolidated entry

All four need the same infrastructure: a semantic / similarity layer
that doesn't exist today. Building it once unlocks all four. Picking off
any single one in isolation is bad value.

### Pre-requisites

- Add a sentence-embedding dependency (e.g. `openai>=1.0` for
  text-embedding-3-small via the existing API key, OR a small
  sentence-transformer model with `sentence-transformers`).
- Or build an LLM-judge harness with explicit rubric + temperature=0
  scoring for the policy-statement vs lived-experience distinction.
- Decide whether semantic checks run at COMMIT (per-doc, slower) or as a
  post-generation audit pass (city-wide, cheaper but no per-doc
  rejection).

### When to revisit

After the next London regeneration with Phase 6's mechanical rules
applied (T14/T15/T16/T18/T19). If the regenerated corpus still shows the
audit's symptom patterns, build P7-E. If the mechanical rules alone are
enough (the declared persona+tone pulls docs into genuinely different
voices, the handle-signature cap diversifies authors, etc.), P7-E may
not be needed and can be dropped.

---

## P7-F — Cross-border district pool contamination + admin-level country bias  *(✅ RESOLVED in P6-T21)*

**Status:** Resolved 2026-05-20 via P6-T21 (children-of-city Overpass query +
city polygon + venue COMMIT boundary check). Kept here for historical
context.

**Origin:** 2026-05-20 New_York `test_50` corpus audit. The OSM
Overpass-based district fetch in `scripts/generation/research_city.py:132`
returned **39 NJ municipalities and 0 NYC entries** for the New York
city run. As a result, 47 of 51 generated venues are physically in New
Jersey, not New York.

### Root cause (compound, two layers)

1. **Bounding-box too wide.** `_fetch_districts_overpass` uses a 15-km
   bbox around the Nominatim-derived city centre. For NYC (centre at
   Battery Park) this bbox crosses the Hudson and pulls in the entire
   NJ side of the river. The same bbox also drives `radius_km`
   (clamped to 4-12 km, ended up 8.25 km for NYC), which then governs
   the geographic scope the venue agent treats as "in this city".

2. **Admin-level=8 assumption is UK/EU-centric.** The hardcoded query
   `relation["admin_level"="8"]["name"](bbox)` works perfectly for
   London (33 boroughs) and Paris (20 arrondissements) because those
   are the ground-truth city subdivisions at that level. For NYC:
   - NYC's 5 boroughs (Manhattan, Brooklyn, Queens, Bronx, Staten
     Island) sit at OSM `admin_level=5`.
   - NJ's municipalities (Hoboken, Jersey City, Newark, Bayonne, etc.)
     sit at OSM `admin_level=8`.
   - So `admin_level=8` inside NYC's bbox returns the NJ towns and
     **silently misses NYC's own boroughs entirely**.

The two layers interact: even if we tightened the bbox to 4 km, the
admin_level=8 filter would still return ~0 entities (because NYC's
admin_level=8 entries inside Manhattan are sparse/nonexistent), and the
LLM fallback in `_fetch_full_geodata_llm` (lines 383+) would kick in.
Acceptable as a workaround for NYC specifically, but masks the deeper
bug for other cross-border / non-EU cities (Hong Kong, Singapore,
Mumbai, Toronto, etc.).

### Fix options (deferred — needs design + careful testing per city)

- **(a) Country-aware admin-level lookup.** Maintain a small map of
  country code → admin_level that holds the city-subdivision level.
  US → 5 (with admin_level=7 or 9 for neighbourhoods), JP → 7, GB → 8,
  FR → 8, etc. Requires research per country.
- **(b) Cross-border filter via reverse-geocode.** Take Overpass
  results, reverse-geocode each district centroid, drop any whose
  country/state doesn't match the city centre's. Adds ~N Nominatim
  calls and rate-limit handling.
- **(c) Use Nominatim's `addresslookup` on the city itself** to get the
  city's OSM ID, then query Overpass for children of that specific
  admin entity. Cleanest but requires switching query strategy.
- **(d) Tighter bbox + state filter.** Shrink bbox to 5 km and add an
  `is_in:State=NY` Overpass filter. Verify Overpass supports the
  `is_in` predicate reliably.

This problem is the single biggest blocker for new-city generation on
US coastal/border cities. Recommend tackling early in Phase 7.

---

## P7-G — Wrong-info workflow gap (orphan wrong_info entries)  *(✅ RESOLVED in P6-T22)*

**Status:** Resolved 2026-05-20 via P6-T22 (atomic ADD_WRONG_INFO that
requires both `incorrect_source_doc_id` and `truth_carrier_doc_id` upfront
and writes all three tables transactionally). Kept here for historical
context.

**Origin:** New_York `test_50` audit. 6 of 28 `wrong_info` entries
(21%) had no `truth_carrier` AND no `incorrect_source` doc registered.
7 venues ended up stuck at `page_status='committed'` (never reached
`verified`) because of this. Affects ~14% of the corpus.

### Root cause

The required sequence is:
1. `ADD_WRONG_INFO(venue_id, ...)` → creates a wrong_info row.
2. `REGISTER_DOC_REFS(doc_id, venue_id, role='incorrect_source', wrong_info_id=...)` → links one doc as the carrier of the wrong value.
3. `REGISTER_DOC_REFS(doc_id, venue_id, role='truth_carrier', wrong_info_id=...)` → links another doc as the correction.

The agent often calls step 1 and then forgets steps 2-3. VERIFY's
`_check_truth_carrier_registered` and `_check_incorrect_source_registered`
fail; the agent gets the error message but rather than fixing the link
it gives up.

### Fix options

- **(a) Stronger workflow enforcement at the tool boundary.** Make
  `ADD_WRONG_INFO` return a payload that includes "next required call"
  metadata: `{ "status": "incomplete", "next_step":
  "REGISTER_DOC_REFS(...) for role=incorrect_source AND truth_carrier" }`.
  Or accept the role-doc IDs as arguments to `ADD_WRONG_INFO` directly
  and have it auto-create the role rows.
- **(b) Prompt-level workflow scaffolding.** Embed a worked example
  showing the full chain in the handbook, and add a one-line reminder
  to the VERIFY error messages.
- **(c) Make wrong_info creation transactional.** Refuse to commit
  `ADD_WRONG_INFO` unless the carrier-doc and truth-carrier-doc IDs are
  also provided. Hardest change but eliminates the failure mode at the
  data-model level.

---

## P7-H — Events generated outside the task window  *(✕ NOT A BUG — misclassified)*

**Status:** Re-investigated 2026-05-20. The original audit was a misreading of
the architecture; events DO align correctly with `seasonal_windows`. The
real (much smaller) issue — two vestigial city_config columns — was
addressed in P6-T23. Kept here for historical context.

**Re-investigation summary:**

The audit compared events against `city_config.task_dates` (Jul 18-23 for
NYC) and found 0 overlap. But task generation actually uses
`seasonal_windows`, not `task_dates`. NYC's events DO match their
windows perfectly:

| Window | Dates | Events |
|---|---|---|
| NYE | Dec 31 – Jan 6 | 10 |
| St Patrick's | Mar 14 – 20 | 15 |
| Independence Day | Jul 1 – 7 | 11 |
| Thanksgiving | Nov 22 – 28 | 20 |

`pool_utils.load_pool` builds the `cfg` dict passed to `task_agent.py`;
it explicitly excludes `task_dates` and `weather_notes`. Both fields are
read from city_config only to be JSON-decoded and discarded — vestigial.

**Real issue addressed in P6-T23:** weather was computed once per city
for an arbitrary 6-day window (the misnamed `task_dates`) but never
consumed. Replaced with per-window `weather_notes` inside
`seasonal_windows`, so each window has weather relevant to its own
season (NYC spring 9°C / summer 26°C / winter -1°C). City-level
`task_dates` and `weather_notes` are now empty defaults (soft
deprecation; columns retained for back-compat).

**Origin:** New_York `test_50` audit. Of 56 generated events, **0
overlapped the actual task window** (Jul 18-23, 2026). Events
clustered around four seasonal anchors throughout the year: NYE
(Dec 31-Jan 6), St Patrick's (Mar 14-20), Independence Day (Jul 1-7),
and late autumn (Nov 22-29). None inside the task_dates window.

### Root cause

The event-generation step picks seasonal anchors as a "yearly calendar"
view of the city, but the venue agent's task_dates window may not
overlap any of them. There's no constraint that at least one anchor's
window contains the task_dates. Result: the entire events table is
generated but unusable for evaluating any task in this corpus —
event-conditional constraints (Type 5/6) effectively have nothing to
bind to.

### Fix options

- **(a) Anchor selection follows task_dates.** Before generating events,
  pick the seasonal anchor whose window contains `task_dates`. Generate
  events for that anchor only.
- **(b) Generate events across all anchors AND ensure task_dates falls
  inside one of them.** Validate at config-creation time that the
  6-day task_dates window overlaps ≥1 generated anchor; if not, either
  shift task_dates or add a "general summer" anchor centred on the
  window.
- **(c) Auto-shift task_dates to land inside an existing anchor.** If
  no anchor covers the proposed task_dates, snap task_dates forward
  or back to the nearest anchor. Less honest about the original window
  but cheapest.

Likely (a) is the right answer for the next regen.

## P7-I — OSM-grounded noise calibration (visibility gradient, omission, ghost listings)

**Origin:** 2026-06-16 literature review (the "fragile information environment"
review that P7-C said was a prerequisite). Anchor paper: Klinkhardt et al. 2023,
*Quality Assessment of OpenStreetMap's Points of Interest with Large-Scale Real
Data* (TRR) — POI data quality benchmarked against 49 German field surveys.
Supporting: Haklay 2010 (OSM vs Ordnance Survey), Rahm & Do 2000 (dirty-data
taxonomy). PDFs in `Books and Papers/Papers/Internet-Noise-Benchmark/`.

**Key external finding:** crowd-sourced place-data quality is *conditioned on
venue visibility/popularity* — highly visible storefronts were ~73% complete,
hidden venues ~22%. Our `traffic_tier` gating of wrong-info already re-derives
this independently, which is good validation. Three refinements follow.

### (1) Replace the binary tier gate with a calibrated gradient
Today wrong-info is forbidden on high-traffic venues (zero error) and applied
flat to ~40% of mid/low (`generate_city_venues.py:58,427`;
`agent_tools._check_no_wrong_info_on_high_traffic`). Klinkhardt shows even
highly visible POIs are ~27% inaccurate — *perfectly clean iconic venues is an
unrealistic assumption.* Make P(wrong-info) monotonic in tier:
high ≈ 10–15% (small but **nonzero**), mid ≈ 40%, low ≈ 60–75%. Smallest diff,
immediately more defensible, and citable.

### (2) Omission / completeness as a first-class noise type *(the real gap)*
Every venue currently exists in the DB; noise only *corrupts attributes*. The
single largest OSM finding is **missingness** — a real venue entirely absent
from a source, conditioned on visibility. Model a venue that is real (resolvable
via `fetch_url`/official site) but **does not appear in `search_yelp`**, or
appears only in a forum thread. P(absent from a surface) rises as `traffic_tier`
falls. This directly exercises the README's "multi-source synthesis" thesis,
which is currently *asserted but not tested*: no task today requires discovering
a venue that no single tool lists. New field at venue-gen (e.g. `listed_in_yelp`)
+ filtering in `mock_tools.search_yelp`.

### (3) Ghost / commission errors (closed-but-still-listed)
Klinkhardt: 9 of 49 areas listed *more* venues than exist — closed businesses
still shown as active (closure lag, COVID-amplified). We model date-*conditional*
closures (`override_type=closed`), but a **permanently-closed venue still shown
as a normal active Yelp listing**, with the correction buried three replies deep
in a forum, is a stronger, very realistic trap. Add as a `temporal_decay`
commission variant or a venue-level `ghost_listing` flag. Pairs with type5
(hard feasibility) and type6 (closed-venue tension).

**Does NOT transfer:** positional-coordinate jitter and contributor-density
indicators — geospatial-specific, irrelevant to a planning benchmark.

---

## P7-J — Tool-level difficulty levers

**Origin:** same 2026-06-16 review. Theme: difficulty currently lives in the
*data* (wrong_info rows); the *tools* themselves are clean, reliable oracles.
Real internet surfaces are not. Grounding: OSM intrinsic-quality indicators
(Senaratne et al. 2017 review; Barron/Neis/Zipf 2014) — quality can be *inferred
from metadata* like recency and edit count ("many eyes"/Linus's Law, Haklay 2010).

- **Expose reliability metadata the agent must learn to weigh.** Have tools
  return `last_updated`, `review_count`, and a corroboration/`mention_count`
  signal. Concentrate wrong-info on low-metadata results. A capable agent should
  learn to distrust stale, low-engagement listings — meta-reasoning we don't
  currently reward. (Engagement exists internally per `agent_tools.py:805` but
  isn't surfaced as an agent-usable signal.)
- **Make `fetch_url` unreliable, not an oracle.** Sometimes 404 / "site
  unavailable", sometimes return a stale *cached* version that is itself wrong.
  Removes the "official site = ground truth" shortcut and forces triangulation.
- **Partial / paginated / truncated results.** Tools return top-N with more
  behind pagination, or silently cap. Forces the agent to decide *when it has
  searched enough* — the completeness/"when to stop" skill, and a natural pair
  with P7-I(2).
- **Intra-source contradiction.** A single Yelp listing whose `hours` field
  disagrees with its own review snippets (Rahm & Do *single-source* problem,
  distinct from our current cross-source contradictions). Tests reading past the
  structured field to the unstructured text.

---

## P7-K — Structure-level difficulty levers

**Origin:** same review. These change *what skill the benchmark tests*, not just
how hard the existing skill is. Highest ceiling, highest build cost.

- **Entity resolution across tools (Ditto / Rahm & Do multi-source).** Today a
  venue has one canonical identity across surfaces. On the real internet the same
  venue appears under name variants ("The Ivy" / "Ivy West St" / "The Ivy
  Restaurant"), and duplicate listings exist. Make the agent *match* that a
  blog's "Joe's on 5th" is Yelp's "Joe's Cafe (5th Ave)". Add near-duplicate
  venues + name variants across tools, plus **adversarial split traps**: two
  genuinely different venues with confusingly similar names it must *not*
  conflate. This is a distinct, very "internet-level" skill (entity matching) and
  currently absent. Refs: Li et al. 2020 (Ditto, entity matching); the classic
  Fodors–Zagat restaurant-matching benchmark.
- **Latent source reliability + copying traps (truth discovery).** Source
  trust is currently a fixed-ish hierarchy (official > yelp > forum). Li et al.
  2016 (*A Survey on Truth Discovery*): real reliability is *inferred* from
  cross-source agreement. Two extensions: (a) **field-conditioned reliability** —
  a food blogger is trustworthy on food, useless on hours; an official site
  reliable on hours, silent on vibe. (b) **Correlated/copying sources** — two
  sources agree only because one copied the other (our existing
  `propagation_error` category is exactly this), so naive majority voting fails;
  the agent must detect the dependence. For some fields, remove the authoritative
  source entirely so truth is *only* recoverable by corroboration across ≥3
  independent sources.
- **A detectability/difficulty dial (BART).** Arocena et al. 2015 (*Messing Up
  with BART*) — the contribution is *controlling how detectable an injected error
  is*. Parametrize wrong-info detectability: is there a corroborating second
  source? how buried is the correction (top comment vs 3 replies deep)? is the
  truth-carrier high- or low-engagement? Then generate the *same* task at
  easy/medium/hard detectability and **report scores as a function of the dial** —
  turns a single difficulty point into a difficulty *curve* and makes our hardness
  claims quantitative. Directly resolves the open scoring question in **P7-C**.
- **Non-i.i.d. error clustering (Haklay).** OSM error clusters spatially. Analog:
  let noise rate depend on district churn and category (new restaurants churn
  fast → stale hours; museums are stable), not `traffic_tier` alone. Makes the
  noise *learnable* the way real data is, and rewards an agent that picks up the
  pattern.

**Relationship to existing entries:** P7-I/J/K supply the literature P7-C was
waiting on, and the detectability dial (P7-K) is the missing piece for P7-C's
scoring redesign. P7-E (semantic source-doc quality) is complementary —
generation-side quality, vs the agent-difficulty axes here.

### References (PDFs in `Books and Papers/Papers/Internet-Noise-Benchmark/`)
- Klinkhardt et al. 2023 — OSM POI quality vs. field surveys (TRR).
- Haklay 2010 — How good is VGI? OSM vs Ordnance Survey.
- Senaratne et al. 2017 — review of VGI quality assessment methods.
- Rahm & Do 2000 — Data Cleaning: Problems and Current Approaches (taxonomy).
- Arocena et al. 2015 — Messing Up with BART (controllable error generation).
- Li et al. 2020 — Ditto (deep entity matching); Li et al. 2016 — Truth Discovery survey.

---

*This document is intentionally small and adds entries only when a real
design concern is surfaced during Phase 6 work. Not a wishlist.*
