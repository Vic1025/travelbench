# TravelBench Phase 6 — TODO

## Overview

Phase 6 focuses on data quality and source-document coverage — the two
foundational issues revealed by reviewing the first generation run under
the new P-constraint framework.

---

## Done this phase

- **P6-T23** (2026-05-20) — Per-window weather notes + retire vestigial
  `city_config.task_dates` / `weather_notes`. Resolves the residual
  cleanup behind the (misclassified) P7-H finding.
  - **The original "0 events in task window" finding was a false alarm**:
    the audit compared events against `city_config.task_dates`, but task
    generation uses `seasonal_windows`. NYC events DO align perfectly
    with their windows (10/15/11/20 per window across NYE / St Patrick's
    / Independence Day / Thanksgiving). `pool_utils.load_pool` discards
    both `task_dates` and `weather_notes` when building the `cfg` dict
    passed to `task_agent.py` — both fields were vestigial.
  - **Real fix — per-window weather notes**: added `weather_notes` to
    each entry in `seasonal_windows`. Open-Meteo is called once per
    window's own dates, so weather reflects the actual season
    (verified live on NYC: spring 9.1°C, summer 26.5°C, winter -1.5°C).
    Implemented as `_enrich_windows_with_weather` in
    `populate_seasonal_windows.py`, called in both the LLM-window path
    and the static-CITY_WINDOWS path.
  - **Soft-deprecate `task_dates` and city-level `weather_notes`**:
    `research_city.py` no longer computes meaningful values — writes
    `[]` / `""` defaults to satisfy the legacy `NOT NULL` constraints.
    Columns retained for back-compat; existing London `test_70` / NYC
    `test_50` data is untouched. The `_fetch_weather_notes` helper
    stays in `research_city.py` as the shared utility used by the
    seasonal-windows enrichment.
  - **Task-agent prompt update**: `_build_agent_system_prompt` now
    appends `WINDOW WEATHER: <weather_notes>` to the window-context
    section when present. Pavement for the existing `weather_aware`
    constraint type that previously had no data to bind to. Additive —
    legacy windows without `weather_notes` get rendered identically to
    before.
  - **`inspect_db.py` update**: city overview now shows seasonal windows
    + their date ranges + per-window weather instead of the misleading
    city-level `task_dates`. Detail view also shows per-window weather.
  - **Phase 7 doc update**: P7-H annotated `(✕ NOT A BUG — misclassified)`
    with full re-investigation summary kept for historical context.
  - **Tests**: stub-windows enrichment verified live (NYC spring/summer/
    winter weather all correct). Full 25-suite sweep green; no test
    changes needed (additive field on JSON column, validator wasn't
    extended to require it).
  - **Forward-only**: existing seasonal_windows in London `test_70` and
    NYC `test_50` are NOT mutated. Future city research runs and
    re-population calls (`populate_windows` / `generate_and_store_windows`)
    will populate the new field.
- **P6-T22** (2026-05-20) — Atomic wrong_info workflow (resolves P7-G).
  Eliminates the orphan-wrong_info failure mode by collapsing the 3-call
  chain (ADD_WRONG_INFO + 2× REGISTER_DOC_REFS) into a single atomic
  call.
  - **Root cause** (audit on New_York `test_50`): 6 of 28 `wrong_info`
    entries (21%) had no `truth_carrier` or `incorrect_source` doc
    registered → 7 venues (~14% of corpus) stuck at `page_status='committed'`
    and never reached `verified`. Live evidence on venue `GxHUPgb`: the
    agent created 2 wrong_info entries, registered one completely
    (`zucSDjzh` with both roles linked) and abandoned the other
    (`UAJITdji` with zero `doc_venue_roles` rows). Characteristic
    failure mode: multi-wrong_info venues where the agent handles one
    entry then forgets registrations for the others.
  - **Three handoff failures contributed**: ADD_WRONG_INFO's old return
    `note` only mentioned `truth_carrier` (not incorrect_source); the
    system prompt taught doc TONE but not the 3-call sequence; and
    REGISTER_DOC_REFS' docstring didn't explain when `wrong_info_id` was
    required. The handbook's `wrong_info_rules` section did spell out
    the chain correctly but was buried — agents that called
    ADD_WRONG_INFO from prompt context may never have read it.
  - **Fix — atomic ADD_WRONG_INFO**: `tool_ADD_WRONG_INFO` now REQUIRES
    `incorrect_source_doc_id` and `truth_carrier_doc_id` parameters. The
    tool validates both docs exist + are committed + are distinct + are
    not already playing a wrong-info role for a different entry on this
    venue, then writes the wrong_info row + auto-creates
    `doc_venue_refs` if missing + writes both `doc_venue_roles` rows —
    all in a single transaction. Partial state is impossible: either
    all three writes succeed or none do.
  - **Tool schema, prompt, and handbook updates** (`generate_venue.py`,
    `handbook.py`): the two new required params; a worked-example block
    in the system prompt showing the new 4-step chain; FORMAT_SPECS for
    both ADD_WRONG_INFO and REGISTER_DOC_REFS rewritten; the
    `wrong_info_rules` "REGISTERING WRONG INFO IN THE SYSTEM" subsection
    rewritten to describe the atomic flow.
  - **Tests:** 19 new T22 cases covering missing params, conflicts,
    upgrades (`neutral` → `incorrect_source`), the NYC multi-wrong-info
    failure-mode regression, and VERIFY's truth_carrier/incorrect_source
    checks passing post-fix. Total `test_agent_tools.py`: 161/161.
    Full 25-suite sweep green.
  - **Forward-only.** Existing London/NYC corpora are not mutated; the
    new requirement only applies to NEW calls. VERIFY's existing checks
    (`_check_truth_carrier_registered`, `_check_incorrect_source_registered`)
    still fire on legacy orphan entries.
  - **Cascading downstream effect**: with no more orphans at the
    wrong_info layer, the "stuck at committed" pattern (7/51 venues on
    NYC `test_50`) should largely disappear in future regens. P7-H
    (events outside task window) and P7-E (semantic source-doc quality)
    remain the open Phase 7 items.
- **P6-T21** (2026-05-20) — Cross-border geographic-scope fix. Replaces
  the leaky 15-km bbox + hardcoded `admin_level=8` district fetch with
  a polygon-based approach.
  - **Root cause** (audit on New_York `test_50`): 47/51 venues ended up
    in NJ. Two compounding issues in `research_city.py:132`: the 15-km
    bbox around the Manhattan centre reached across the Hudson, and the
    hardcoded `admin_level=8` query happens to miss NYC's own boroughs
    (which sit at level 5/7) but catch every NJ municipality (level 8).
    Result: NYC's district pool ended up 100% NJ towns.
  - **Fix #1 — Children-of-city Overpass query.** Replaced the
    `relation["admin_level"="8"]({bbox})` query with `relation(osm_id);
    map_to_area; relation["admin_level"={lvl}](area)`, trying levels
    8→7→6→9 and picking the first that returns 4-60 named entities.
    Universal: London/Paris still get 33/20 borough-level entries at
    level 8; NYC now gets 5 boroughs (Manhattan, Brooklyn, Queens,
    The Bronx, Staten Island) at level 7. Verified live against both.
  - **Fix #2 — City polygon + point-in-polygon at venue COMMIT.**
    `_fetch_city_geodata` now passes `polygon_geojson=1` to Nominatim
    and stores the city's actual boundary as JSON in a new
    `city_config.boundary_geojson` column. `tool_COMMIT` for venue
    rejects lat/lng outside the polygon. Pure ray-casting helper in
    `agent_tools._point_in_polygon` (zero new deps, handles Polygon +
    MultiPolygon + holes). Legacy configs without a polygon bypass
    the check silently.
  - **Schema**: Added `boundary_geojson TEXT DEFAULT ''` to
    `city_config` with idempotent migration.
  - **Verification (live):**
    - NYC fetch returns the 5 NYC boroughs (was 39 NJ towns).
    - London fetch still returns the 32 London boroughs.
    - End-to-end probe: Manhattan/Brooklyn/Queens/Staten Island
      venues commit; Hoboken/Newark/Jersey City venues rejected with
      "OUTSIDE the boundary of New York".
  - **Tests:** 12 new T21 cases (point-in-polygon helper sanity + venue
    COMMIT respects boundary + legacy bypass). Full 25-suite sweep
    green; 142/142 cases in `test_agent_tools.py`.
  - **Phase 7 entries P7-G (wrong_info workflow gap) and P7-H (event
    window misalignment) remain open** — surfaced by the same New_York
    audit but deferred per user direction.
- **P6-T20** (2026-05-20) — Pipeline audit fixes after the two-pipeline
  sweep. Eight issues found and resolved across both data-generation
  pipelines (venue+sources gen, task gen).
  - **Fix #1 — T16 within-venue persona check (BUG)**: The check was
    silently non-functional in production. It joined `source_docs` to
    `doc_venue_refs` to find other docs on the venue, but
    `tool_REGISTER_DOC_REFS` populates `doc_venue_refs` AFTER COMMIT.
    Fixed by making `venue_id` REQUIRED at `CREATE_PAGE("source_doc")`,
    validating the FK, and pre-populating `doc_venue_refs` at creation
    time. The existing T16 COMMIT check is unchanged — it now has data
    to work on. Multi-venue docs are deliberately *not* persona-checked
    on secondary venues yet (those are added via REGISTER_DOC_REFS
    post-COMMIT and don't trigger T16).
  - **Fix #2 — Handbook FORMAT_SPECS contradiction (DOC DRIFT)**:
    `handbook.py:240-281` previously told the agent "omit venue_id for
    source_doc" — directly contradicting T16's design. Updated to say
    venue_id IS required for source_doc, with a note that
    REGISTER_DOC_REFS handles additional venues. Workflow Step 4 now
    shows `CREATE_PAGE("source_doc", venue_id=YOUR_VENUE_ID)`.
  - **Fix #3 — CREATE_PAGE tool schema description**: `generate_venue.py`
    venue_id description updated to include source_doc.
  - **Fix #4 — Off-vocab tag examples in task prompts (PROMPT DRIFT)**:
    11 `has_tag=<X>` examples in `task_agent.py` and
    `test_generate_tasks.py` referenced tags removed in T2-B (`outdoor`,
    `vegetarian`, `vegan`, `vegetarian-friendly`, `local-cuisine`).
    Replaced with current-vocab equivalents (`outdoor-seating`,
    `vegetarian-options`, `vegan-options`). For `local-cuisine` cases,
    rewrote examples to use the `local_cuisine` integer column directly
    via `field:"local_cuisine"` instead of a tag.
  - **Fix #5 — validate_city.py KEY_TAGS (SMELL)**: The Type 5
    intersection-task tag-diversity check listed 4 tags removed in T2-B
    (`dog-friendly`, `wheelchair-accessible`, `family-friendly`,
    `outdoor`) — would always show 0 count → spurious warnings on every
    fresh city run. Replaced with current-vocab tags
    (`halal`, `free-entry`, `live-music`, `vegetarian-options`,
    `vegan-options`, `step-free`, `hidden-gem`, `instagrammable`,
    `cocktails`).
  - **Fix #6 — `REQUIRED_SOURCE_DOC_FIELDS` drift**: `db.py` source-doc
    required list didn't include `persona`/`tone`, but
    `REQUIRED_ON_COMMIT` in agent_tools did. `tool_GET_STATUS` could
    therefore say "all filled" while COMMIT still failed. Added both
    fields to keep the two lists in sync.
  - **Fix #7 — CREATE_PAGE bogus venue_id (SMELL)**: Resolved
    automatically by Fix #1's FK validation. `CREATE_PAGE("source_doc",
    venue_id="BOGUS")` now returns a clear error instead of silently
    accepting and leaving an orphaned doc.
  - **Fix #8 — Task validator off-vocab `has_tag` (SMELL)**:
    `validate_task_schema` now emits a soft warning when a
    `has_tag`/`not_tag` condition value isn't in
    `UNIVERSAL_CORE_TAGS | UNIVERSAL_CUISINES`. Soft (not hard) so city
    extensions like London's `fish-and-chips` aren't false-positives.
  - **Tests**: 6 new T20 regressions including the real-flow Bug #1 case
    (CREATE_PAGE → FILL → COMMIT, no manual `doc_venue_refs` insert) that
    verifies T16 actually fires now. Test helper `_make_source_doc`
    cleaned up — removed the workaround that pre-inserted into
    doc_venue_refs (CREATE_PAGE handles it now), and added
    `_ensure_test_venue` for callers that don't care about venue scope.
    All 130/130 cases in `test_agent_tools.py` pass; full 25-suite sweep
    green.
  - **Verified clean during audit**: `mock_tools.py` doesn't read
    `persona`/`tone`/`mentioned_tags` (proper isolation); `evaluator.py`
    has zero references to the new fields; migrations on London `test_70`
    ran cleanly (237 docs migrated, all defaulted to empty persona/tone
    per the forward-only rule).
- **P6-T18 + P6-T19** (2026-05-20) — Audit tier-3 mechanical fixes:
  handle-shape diversity + body-length bounds. Companion to T14/T15/T16.
  - **P6-T18 — Handle-signature soft cap.** `_handle_signature` in
    `agent_tools.py` classifies each author into 7 mutually-exclusive
    shape bins: `with_digit`, `lower_snake`, `mixed_snake`, `multi_word`,
    `mixed_case_compound`, `single_lower`, `other`. COMMIT-time check
    rejects when the new doc's signature would push that bin above 40%
    of the city's committed/verified docs. A 10-doc floor skips the
    check on early-corpus runs to avoid trivial dominance. Audit baseline
    on London `test_70`: `lower_snake` is the dominant forum-handle bin
    (hackney_jim, shoreditch_dan, eastlondon_eater, southlondon_foodie);
    the new check pushes future generation toward varied shapes.
  - **P6-T19 — Body-length bounds + distribution.** Hard COMMIT-time
    bounds: blog 400–2500 chars; forum 300–2000 chars. Out-of-range
    bodies are rejected with a message explaining the range and pointing
    to the CREATE_PAGE distribution surface. `CREATE_PAGE("source_doc",
    venue_id=…)` response now includes `city_body_length_counts` (per
    doc_type, binned `short`/`medium`/`long`/`very_long`) and
    `body_length_bounds`, so the agent self-distributes across length
    brackets. Audit baseline on London `test_70`: 95 blogs are `long`
    vs only 4 `short` — the distribution surface lets future runs fill
    underused brackets.
  - **Tests:** 21 new T18 + T19 regressions in `test_agent_tools.py`
    covering classifier shape coverage, under-floor pass, at-floor cap,
    per-city scoping, bounds violations on both doc_types, and the
    CREATE_PAGE response shape. All 25 active suites green.
  - **Forward-only.** Existing London `test_70` data untouched; new
    generation gets the new rules.
  - **Phase 7 doc updated** (`docs/PHASE7_FUTURE_WORK.md`) with the new
    consolidated **P7-E — Semantic source-doc quality detection** entry,
    aggregating four audit-deferred items that need LLM-judge or
    sentence-embedding infrastructure: (1) negative-regulation
    lived-experience enforcement, (2) opener-cliché blacklist, (3)
    within-venue narrative-arc overlap, (4) cross-venue cliché
    detection. All share the same prerequisite (semantic / embedding
    layer), so they ship together when revisited.
- **P6-T16** (2026-05-20) — Declared persona+tone on source_docs +
  within-venue persona uniqueness + cross-venue tone-spread visibility.
  Tier-2 follow-up to the 2026-05-20 source-doc audit.
  - **Schema:** Added `persona TEXT NOT NULL DEFAULT ''` and
    `tone TEXT NOT NULL DEFAULT ''` to `source_docs` (idempotent migration
    in `_apply_migrations`). Both required at COMMIT.
  - **Persona is CLOSED vocab** — 7 canonical personas (`CANONICAL_PERSONAS`
    in `handbook.py`). COMMIT hard-rejects off-vocab values and enforces
    within-venue uniqueness via `doc_venue_refs` join (each persona may
    only appear ONCE per venue, matching the existing handbook rule that
    was previously unenforced).
  - **Tone is OPEN vocab** — 10 canonical buckets recommended
    (`CANONICAL_TONES`), expanded from the original 4 (talkative / terse /
    emotional / analytical) plus 6 new (wry / nostalgic / instructional /
    complaint-first / journalistic / confessional). Agent may invent a
    new tone descriptor when none fits naturally; non-canonical tones
    aggregate into the `_invented` bucket of city_tone_counts.
  - **CREATE_PAGE("source_doc") context payload:** when called with a
    `venue_id` kwarg, the response now includes `personas_used_for_venue`,
    `tones_used_for_venue`, `personas_still_available`, `city_tone_counts`
    (per-bucket city-wide running totals), `city_tone_target_per_bucket`
    (soft target ≈ ceil(n_venues × 3.2 / 11), giving 21/bucket for a
    70-venue corpus), and `city_total_docs_so_far`. This gives the agent
    immediate visibility into what's already taken so it picks fresh /
    underused values rather than guessing.
  - **Prompt updates:** `generate_venue.py` system prompt and
    `handbook.py` source_doc FORMAT_SPECS now document the persona-closed
    /tone-open dichotomy, the within-venue uniqueness rule, and how to
    interpret the city tone-spread counts.
  - **Tests:** 17 new T16 regressions covering valid commit, missing
    fields, off-vocab persona rejection, persona normalization,
    open-vocab tone acceptance, within-venue persona collision, per-venue
    isolation, CREATE_PAGE context dict shape, all 10 canonical buckets
    present, and `_invented` aggregation. All 25 active suites green.
  - **P6-T17 deferred** — Planned trigram opener-overlap detective check
    was scoped down to nothing this slice. Threshold tuning on the audit
    pairs (Borough Market `N70Rii6u`/`qFLgUIFh`, Greenwich Park
    `jiCHcYdg`/`nuzSj48f`, XOYO `ZVL2jabP`/`bLybAqqG`) showed they score
    0.12-0.39 on both opener and full-body trigram similarity — the same
    range as genuinely-diverse pairs. Character-level overlap doesn't
    catch "shared narrative arc, different words" which is what the audit
    found. The root-cause fix in T16 (persona+tone declared with
    within-venue uniqueness) handles the same problem preventively; if
    regenerated London data still shows arc overlap a semantic /
    embedding-based check can be designed in Phase 7.
  - **Deferred to Phase 7:** opener-cliché blacklist (whack-a-mole risk);
    within-venue semantic / arc-overlap detection (needs embeddings);
    cross-venue cliché detection (corpus-wide voice tics like
    "honestly" in 95/152 docs).
  - **Forward-only.** Existing London `test_70` source_docs have empty
    `persona`/`tone` strings (the schema default) and stay verified. New
    generation runs get the new rules.
- **P6-T14 + P6-T15** (2026-05-20) — Forum-doc shape validator + author
  reuse cap. Grounded in a 2026-05-20 source-doc audit of London `test_70`.
  - **P6-T14 — Forum shape**: COMMIT-time check for `doc_type='forum'`
    rejects bodies that don't contain ≥2 turn markers from ≥2 distinct
    speakers. Three accepted patterns documented in the handbook:
    `Reply from <handle>:` (markdown-bold OK), `<Handle>:` at start of line
    (handle must contain underscore / digit / mixed-case), and the formal
    `Reply from User '<handle>' on YYYY-MM-DD:`. Audit baseline: 68/81
    existing forum docs in London `test_70` would have been rejected under
    the new rule (only 13 contained real Q&A structure). Forward-only —
    existing committed docs stay; new generation gets the rule.
  - **P6-T15 — Author cap**: COMMIT-time check rejects a source_doc when
    the same author (case-insensitive, whitespace-stripped) already has ≥2
    committed/verified docs in the same city. Per-city scope so the same
    persona can recur across cities. Audit found "Marcus Chen" wrote 12
    London docs and "MumOnTheMove" wrote 6+ — 27 docs (~12% of the corpus)
    would have been rejected under the N=2 cap. Forward-only.
  - **Helpers** in `scripts/generation/agent_tools.py`:
    `_FORUM_HANDLE_PATTERNS`, `_FORUM_EXCLUDE`, `_looks_like_handle`,
    `_extract_forum_handles` — module-level so they can be unit-tested
    independently of the COMMIT flow.
  - **Handbook docs** — `source_doc` FORMAT_SPECS now has dedicated
    "Forum doc shape" and "Author reuse cap" sections, each with a worked
    example. The implicit forum=Q&A intent is now explicit (audit showed
    LLMs were missing it).
  - **Tests** — 18 new regressions in `test_agent_tools.py` covering all
    three handle patterns, the exclude-list (Note:/Update:/Thread title:
    false-match prevention), monologue rejection, same-handle-only
    rejection, blog-doc exemption, author cap basics, case-insensitive
    collision, per-city scoping, and the drafts-don't-count rule. All 25
    active suites green.
  - **Deliberately deferred** to Phase 7 / future audits:
    - Opener-cliché blacklist ("I finally made it", "There's something
      about the [light]") — needs prompt-engineering design vs. brittle
      n-gram whack-a-mole.
    - Within-venue persona/tone overlap check — needs an LLM-judge or
      sentence-bigram approach; significant additional design.
    - Forum-handle naming-variety rule (the `<district>_<noun>` pattern).
- **P6-T2 Issue B** (2026-05-19) — Hidden tags now surface in source docs
  via a structured channel.
  - **New column** `source_docs.mentioned_tags TEXT NOT NULL DEFAULT '[]'`
    (JSON array of tag strings the body addresses). Idempotent migration in
    `db._apply_migrations`. Confirmed on London `test_70` — all 237
    source_docs default to `'[]'`.
  - **Vocab cleanup** — 11 regulation-mirror tags removed from
    `UNIVERSAL_CORE_TAGS` (64 → 53): `photography-allowed`, `pet-friendly`,
    `family-friendly`, `wheelchair-accessible`, `booking-required`,
    `no-reservations`, `outdoor`, `dog-friendly`, `quiet`, `formal`,
    `budget-friendly`. Each concept lives only in its structured column
    (regulations, `price_tier`, `noise_level`, `dress_code`,
    `outdoor_sensitivity`, `booking_required`). Principle documented in
    `handbook.py` above the constant.
  - **COMMIT validation** in `tool_COMMIT` for source_docs: `mentioned_tags`
    must be a JSON array of strings, ≤2 entries, all in the city's combined
    vocab. Off-vocab regulation-mirror concepts get a targeted error
    pointing the agent to `mentioned_regulations`.
  - **VERIFY check** `_check_tag_visibility` (wired into `tool_VERIFY`):
    every `yelp_visible=0` tag on a venue must appear in `mentioned_tags`
    on ≥1 of the venue's source docs. Off-vocab legacy tags (e.g. on old
    London data) are exempt — forward-only enforcement. Hard reject on
    failure, matching the regulation-visibility pattern.
  - **FILL normalization** for `mentioned_tags` mirrors the existing
    `mentioned_regulations` path (list / JSON-string / comma-separated all
    accepted and stored as a JSON array).
  - **Handbook docs** — `source_doc` FORMAT_SPECS gains an explicit
    `mentioned_tags` entry and a new strict-coupling block; both-directions
    rule (positive mention OR restriction-hint) is now stated for both
    `mentioned_regulations` and `mentioned_tags` after an audit showed the
    convention was easy to misread when implicit. VERIFY checks list adds
    `tag_visibility`.
  - **Prompt cleanup** — `generate_venue.py`, `generate_city_venues.py`,
    `task_agent.py` example tags swapped from removed regulation-mirror
    tags to current-vocab equivalents (avoids steering the LLM into
    SET_TAGS rejection loops).
  - **Empirical grounding** — based on a 27-pair audit of the existing
    `mentioned_regulations` pattern showing 25 STRONG / 2 WEAK / 0
    WRONG_DIRECTION (93% clean). Pattern transplanted, not invented.
  - **Tests** — 11 new T2-B regressions in `test_agent_tools.py`
    (`_check_tag_visibility` coverage cases, COMMIT validation cases,
    legacy off-vocab exemption); vocab-cleanup regression in
    `test_handbook.py` (53-tag count, removed tags absent); schema
    assertion in `test_db.py`; migration test updated to use post-T2-B
    vocab targets. All 25 active suites green.
  - **Body-content richness** ("a single ramp mention counts as STRONG
    even though it's thin") deferred to Phase 7 — applies to both
    regulations and tags.
  - **Not touched** — no backfill of `mentioned_tags` on London `test_70`;
    no removal of legacy regulation-mirror tag rows in London (SET_TAGS
    blocks NEW such tags but old data remains as legacy).
- **Paris-JSON pipeline retired.** The Phase 1/2 stub generators
  (`scripts/generate_sources.py`, `generate_city.py`, `run_demo.py`,
  `regression_test.py`, `test_llm_judge.py`, `inspect_venue.py`,
  `analysis_report.py`, `probe_difficulty.py`, `scripts/migration/`) and the
  Paris JSON corpora under `data/sources/paris/`, `data/ground_truth/paris/`,
  `data/data1/sources/paris/`, `data/data1/ground_truth/paris/`, and the
  Paris task JSONs in `data/tasks/`, `data/data1/tasks/`,
  `data/data1/tasks/unfiltered/{model}/` were deleted. Dormant Paris branches
  in `server/mock_tools.py` (`_JSON_CITIES`, JSON fallback in `_load_city`),
  `eval/evaluator.py` (`_load_ground_truth_from_json`, legacy `data/tasks/`
  branch in `load_task`), and `scripts/generation/pool_utils.py`
  (`PARIS_DEFAULT_WINDOW`) were removed.
- **P6-T3** — SUBMIT handler now auto-maps `"type1"` → `"type1_cascading_requirements"`
  (and integer-string forms `"1"`–`"6"`) before the canonical-type validator
  fires. One wasted SUBMIT turn eliminated per run per model.
- **P6-T6** — K-target in `_verify_task_solvable` now multiplies by `days`
  when an `at_least` constraint has `scope=per_day`
  (`scripts/generation/generate_task.py:680–688`). Companion soft warning
  added to `validate_task_schema` for multi-day type2 + per_day at_least, and
  a one-liner in the Type 2 reasoning protocol pointing agents away from
  `scope=per_day` for global floors.
- **P6-T9 (both bullets)** —
  - **F4a:** `evaluate_f_score` now sums same-venue durations per day before
    the bounds check, so a split 90 + 90 min visit to a 180-min museum scores
    as on-target instead of double-deducting under-scheduled.
  - **`at_least` aggregation:** `_evaluate_generic_constraint` now counts
    distinct `venue_id`s instead of activity count. Revisiting the same art
    museum twice no longer satisfies `at_least: 2 art`. Partial-credit math
    and the rubric reason string updated to match.
- **P6-T12** —
  - SUBMIT handler auto-populates `public_input.query_resources.currency` from
    `get_city_currency(city)` when missing (won't overwrite an existing value).
  - Currency lookup migrated to Babel. `CITY_CURRENCY` + `_CURRENCY_SYMBOL`
    dicts replaced by a single `CITY_COUNTRY` map (city → ISO 3166-1 alpha-2);
    `get_city_currency` and `format_money` now call `babel.numbers.get_territory_currencies`
    and `get_currency_symbol`. Babel added to `requirements.txt`. A fallback
    table keeps the pipeline working on minimal installs.
- **P6-T5** — `_verify_task_solvable` now runs a scoped KNN pass after the
  universal-pool check. For each `at_least` constraint with a non-trivial
  subset scope (`category=X`, `has_tag=Y`, etc.), the geometry check re-runs
  on the scoped venue subset; tight ceilings that hide behind a permissive
  universal pool are now rejected (e.g. `at_least: 4 scope=category=museum`
  with only 5 spread-out museums).
- **P6-T7** — C-score Section D blank-time formula rewritten. The span-based
  `0.8 / expected × missing` with a -1 buffer was replaced by a gap-based
  formula: deduction `missing × 0.04` fires only when `actual_cnt < floor(expected)`
  AND `max_gap > 90 minutes` between consecutive activities. `day_ref` now
  uses `time_ceiling_minutes` for type2 (the active budget), not a 10-hour
  reference. Trailing gaps after the last activity are no longer penalised.
  Single-activity-all-day plans are correctly flagged via a special case.
- **P6-T9 Scenario A** — Added a soft *warning* (no score impact) in
  `evaluate_c_score` when the same `venue_id` appears more than once on the
  same day. F4a already sums durations for legitimate split visits; this
  warning surfaces the case to rubric readers without penalising it.
- **P6-T4b** — multi-mode time-ceiling schema + evaluator.
  - `query_resources` schema additions: `ceiling_mode: "contiguous" | "spread"`
    (default contiguous), `ceiling_scope: "total" | "per_day"` (default total),
    `start_time: "HH:MM"` (required when ceiling_mode=contiguous).
  - `validate_task_schema` enforces the rules: contiguous needs start_time;
    spread + start_time → soft warning; per_day on 1-day task → soft warning.
  - `_verify_task_solvable` computes `effective_total = ceiling × days` for
    `per_day` scope and feeds that into the geometry check.
  - F2e (in F-score) branches on mode:
    - contiguous: sum check across days + window check (`−0.15` per activity
      outside `[start_time, start_time + ceiling]`).
    - spread + total: sum check across days.
    - spread + per_day: per-day sum check.
  - Task agent's type2 reasoning protocol documents the two modes and the
    rule that **spread mode imposes no meal floor** — agents express any meal
    requirement via a P-constraint rather than relying on a structural minimum.
  - 8 new F2e regressions in test_e3 (window inside / outside / overrun /
    no-start-time / spread per_day over / spread per_day OK); 7 validator
    regressions in test_b4 (contiguous-without-start, spread-no-start-OK,
    spread+start warning, invalid mode value, invalid scope value,
    start_time wrong format). All 20 active suites green.
  - **Temporal-arc concern** (raised during this design) filed in new
    `docs/PHASE7_FUTURE_WORK.md` as P7-A. Three implementation options
    documented (already-expressible / new task type / B-score arc bonus);
    decision deferred to after a Phase 6 benchmark run.
- **P6-T4 Check A** — time-ceiling enforcement at solve time, **in F-score**.
  Reclassified from P-score (the original TODO placement) to F-score because
  the ceiling is a feasibility requirement, not a preference. New F2e
  section in `evaluate_f_score`, fires after the per-day loop:
  - **type2** tasks: sums (activity durations + travel time) across all
    days; one −0.30 deduction if the total exceeds `time_ceiling_minutes`.
  - **non-type2** tasks: per-day check; one −0.30 deduction per day that
    exceeds. Forward-looking — no non-type2 task carries the field today,
    but the branch is in place for T10 (active_hours beyond type2).
  - Reuses existing `_get_walk_minutes(v1, v2, matrix)` for implicit travel
    between consecutive non-same-venue activities; counts explicit
    transport activity durations too.
  - 9 new regressions in `test_e3.py` (within-ceiling, overrun, no-ceiling,
    non-type2 per-day, multi-day sum semantics). All 20 active suites
    remain green.
  - **Still open**: Check B (window-based check needing `start_time` field)
    and T4b (`ceiling_mode: contiguous|spread`, `ceiling_scope: total|per_day`).
- **P6-T1 / P6-T1b (Slice 3)** — London `test_70` migration script + vocab
  amendment.
  - **Vocab amendment**: 7 cross-city tags added to `UNIVERSAL_CORE_TAGS`
    in `handbook.py` (`budget-friendly`, `coffee`, `cocktails`, `wine`,
    `street-food`, `small-plates`, `park`). `UNIVERSAL_CORE_TAGS` now has 64
    tags (was 57). `format_vocab_by_axis` adds a "Venue type" row.
  - **Migration script**: `scripts/migration/london_canonicalize.py` with two
    modes:
    - `--plan` (read-only): inventories London tags, calls DeepSeek-chat once
      for the off-vocab tag → action mapping (rename / split / drop / extend),
      builds the cuisine backfill plan (direct map from cuisine-vocab tags
      when intersection==1; LLM cuisine-pick when 0 or 2+), and writes
      `scripts/migration/london_canonicalize_plan.json` for human review.
    - `--apply`: validates the (reviewed) JSON, backs up the DB, applies the
      plan atomically in a single transaction (rename / split / drop tag rows,
      update `city_config.tag_vocabulary`, backfill `venues.cuisine` on the 26
      restaurants), then runs a post-apply integrity check (all tags on-vocab,
      every restaurant has on-vocab cuisine).
  - **Tests**: `scripts/migration/test_london_canonicalize.py` exercises
    `_validate_plan`, `_apply_plan` (rename/split/drop/extend + cuisine
    backfill + city_extension), post-apply `_integrity_check`, and
    idempotency (32 assertions, all green). `test_handbook.py` extended to
    assert the 7 amended tags are present.
  - **Migration applied** (2026-05-19) against
    `data/cities/london/runs/test_70/travelbench.db`:
    - Off-vocab tags: 58 → 0
    - Distinct tags: 100 → 66
    - Tag-row instances: 637 → 606 (some drops + de-dup after rename)
    - Actions: 37 renames, 2 splits, 9 drops, 3 extends
    - London `tag_vocabulary`: `["cask-ale", "fish-and-chips", "pre-theatre"]`
    - All 24 verified restaurants now have `cuisine` set (23 directly from
      cuisine-vocab tag, 1 LLM/fusion fallback for Roti King).
    - Backup at `data/cities/london/runs/test_70/travelbench.bak.20260519_132334.db`.
    - Manual edits during the review gate: `malaysian → fusion` (LLM had
      proposed off-vocab `asian`); `cask-ale`, `pre-theatre`, `fish-and-chips`
      switched from rename to `extend` (preserve as London-specific).
    - Post-apply integrity check + 20-suite test sweep both clean.
- **P6-T1b (Slice 2)** — `venues.cuisine` structured field.
  - New `cuisine TEXT` column on `venues` (nullable; required at COMMIT for
    `category='restaurant'`, must be in `UNIVERSAL_CUISINES` if set on any
    category). Idempotent migration added.
  - `tool_COMMIT` enforces the rule: restaurant without cuisine →
    `status='incomplete'` with `missing_required=['cuisine']`; off-vocab
    cuisine → `status='error'` with vocab listed in the message.
  - `load_venue_pool` and `load_city_pool` now SELECT `cuisine` (guarded
    by existing-column check so old DBs degrade cleanly).
  - `_query_pool` supports `{"cuisine": "italian"}` filter alongside the
    existing category / tag / regulation / price_tier filters.
  - `_VALID_CD_FIELDS` (in the `count_distinct` error message) now lists
    `cuisine`; the task-agent handbook quick-reference shows the canonical
    `{count_distinct: 3, field: "cuisine"}` example; the longer protocol
    example was switched from `field:"district"` to `field:"cuisine"`.
  - Tests: schema regression on `venues.cuisine`; 6 COMMIT cases (restaurant
    accept/incomplete/off-vocab + cafe optional/accept/off-vocab);
    `count_distinct: cuisine` regression in test_e3 (3 distinct passes, 4
    fails, duplicate-cuisine fails); `_query_pool` cuisine filter regression
    in test_b1.
  - **Not yet done**: Slice 3 — one-shot migration over London `test_70` to
    backfill `cuisine` on all 24 restaurants (currently all NULL after the
    column was added by this slice's migration).
- **P6-T1 (Slice 1)** — Canonical tag vocabulary + SET_TAGS enforcement.
  - `handbook.py` exposes `UNIVERSAL_CORE_TAGS` (57 tags across practical /
    audience / dietary / accessibility / vibe / quality / characteristic /
    meal-slot axes), `UNIVERSAL_CUISINES` (18), and helpers
    `get_combined_vocab(city_ext)` and `format_vocab_by_axis(city_ext)`.
  - `city_config.tag_vocabulary` column added (`db.py` migration). Per-city
    extension generated at city-research time by a new focused DeepSeek call
    `_call_llm_tag_vocabulary` (alongside `_call_llm_cuisine`).
  - `tool_SET_TAGS` rewritten: hard-rejects off-vocab tags with "did you
    mean" suggestions from a single DeepSeek call (trigram-similarity fallback
    when no `api_key` is in scope). The dead "MAX_NEW_TAGS_PER_VENUE / 100-tag
    cap / trigram-warn" block is removed; `tool_CONFIRM_TAGS` kept as a no-op
    for back-compat.
  - Venue-agent loop in `generate_venue.py` now injects `api_key` + `model`
    into the dispatcher; `_build_assignment` shows the canonical vocab grouped
    by axis (with usage counts as guidance, no longer the only enforcement
    surface).
  - Tests: 8 new SET_TAGS regressions (in-vocab accept, off-vocab reject,
    canonicalisation, trigram-fallback "did you mean", city-extension respect),
    schema regression on `tag_vocabulary` column, and constants/helpers in
    `test_handbook.py`.
  - **Not yet done**: Slice 2 — `venues.cuisine` structured column wiring;
    Slice 3 — one-shot migration script over London `test_70`.

---

## P6-T1 — Tag Taxonomy Standardisation

**Status:** ✅ Done — All three slices landed and the London migration ran
cleanly (2026-05-19). See "Done this phase" block at top.
**Root cause:** Venue generation LLM invents tags freely per venue with no
reference to a canonical vocabulary. Result: 100 tags in London's pool with
severe quality problems.

### Problem categories

**Overly compound / awkward names** (look like concatenated category labels):
`architecture-city-skyline`, `history-royal-heritage`, `contemporary-modern-art`,
`fashion-vintage-shopping`, `multicultural-food-markets`, `theatre-performing-arts`,
`music-nightlife`

**Suspiciously specific / one-off** (venue IDs in disguise, no generalisation value):
`bombay-cafe` (1), `crown-jewels` (1), `popcorn` (1), `steamed-buns` (1),
`udon-noodles` (1), `soho` (2), `borough-market` (3)

**Near-duplicates** (should be one canonical tag):
- `photography-allowed` (31) vs `photography-friendly` (1)
- `lunch` (20) vs `lunch-spot` (4)
- `cheap-eats` (3) vs `budget-friendly` (20)
- `counter-seating` (3) vs `counter-dining` (4)

### Fix

1. Define a **canonical tag vocabulary** (≈60–80 tags) covering:
   - Cuisine: `british`, `italian`, `indian`, `japanese`, `thai`, `caribbean`, etc.
   - Venue vibe: `quiet`, `lively`, `romantic`, `trendy`, `casual`
   - Practical: `free-entry`, `booking-required`, `outdoor`, `rooftop`
   - Audience: `family-friendly`, `dog-friendly`, `popular-with-locals`
   - Quality signals: `hidden-gem`, `instagrammable`, `michelin-star`
   - Characteristic: `views`, `architecture`, `history`, `art`, `design`

2. Add the canonical list to `handbook.py` so venue generation agents
   **select from the list** rather than inventing. Include counts for
   reference — agents should not invent tags not on the list.

3. Run a **migration pass** over the London DB to rename/merge existing
   tags to the canonical form (one-time data fix script).

4. Update `test_e3.py` to validate that all tags on any generated venue
   exist in the canonical vocabulary — fail SUBMIT if agent uses an
   off-vocabulary tag.

---

## P6-T2 — Tag → Source Document Coverage

**Status:** ✅ Done (Issue B landed 2026-05-19). See "Done this phase" block at top.

### Issue A — ✅ Resolved (was misdiagnosed)

The bug described — `v.get("labels", [])` vs `v["tags"]` in
`generate_sources.py` — only ever existed in the retired Paris-JSON
path. The live SQLite pipeline (`server/mock_tools.py:100–125`) loads
tags directly from the `tags` table via SQL (`WHERE yelp_visible = 1`)
with no key mismatch. Confirmed by re-reading the loader against the
current London DB. The whole `generate_sources.py` file was deleted
as part of the Paris-JSON retirement (see "Done this phase").

### Issue B — Blog/forum posts don't explicitly surface tags

Blog and forum posts describe venues in natural language. Tag concepts
appear *implicitly* (e.g. "the architecture is stunning") but not as
discoverable structured signals. Agents querying
`search_blogs_and_forums("architecture city skyline")` may surface the
right docs, but this is brittle for unusual or compound tags.

Measured coverage (London DB):
| Tag | Docs with keyword | Risk |
|-----|-------------------|------|
| `hidden-gem` | 4 | 🔴 very sparse |
| `instagrammable` | 2 | 🔴 very sparse |
| `architecture-city-skyline` | 13 | 🟡 ok if fixed |
| `popular-with-locals` | 12 | 🟡 ok |
| `history-royal-heritage` | 14 | 🟡 ok |

**Fix:** In the doc generation prompt (`handbook.py` / doc_agent), add
a requirement: *"For each venue in the document, naturally mention its
key characteristics (from its tag list) in the body text — e.g. if a
venue is tagged `instagrammable`, describe what makes it photogenic."*
This ensures every tag has at least one source doc that naturally
surfaces the concept, making `search_blogs_and_forums` reliable for P-score
verification.

### Issue C — ✅ Resolved (was misdiagnosed)

Same status as Issue A: the `labels[:4]` cap exists only in
`scripts/generate_sources.py` (Paris-JSON path). The live SQLite path
in `server/mock_tools.py:124` indexes the full tag list with no slicing
(`"category_tags": tag_list`, where `tag_list` is every yelp-visible
tag from the `tags` table). File deleted with the Paris retirement.

### Implementation order

1. ~~Fix key mismatch (Issue A)~~ — n/a (Paris-only, file deleted)
2. ~~Fix index coverage (Issue C)~~ — n/a (Paris-only, file deleted)
3. ~~Doc generation prompt update (Issue B)~~ — ✅ Done 2026-05-19. Hidden
   tags now surface via a structured `mentioned_tags` field on `source_docs`,
   enforced by VERIFY (`_check_tag_visibility`). Forward-only — existing
   London `test_70` data untouched. See "Done this phase" block at top.

---

## P6-T3 — Structural Type Full String (Minor / Quick win)

**Status:** ✅ Done
**Root cause:** 100% of models wrote `structural_type: "type1"` instead of
`"type1_cascading_requirements"` on their first SUBMIT. The SUBMIT handler
auto-corrected `window_id` but rejected short structural-type strings.

**Fix landed:** `scripts/generation/task_agent.py` (just before line 1097's
canonical-type validator) now maps `"type1"` … `"type6"` and bare integer
strings `"1"` … `"6"` to the canonical form before validating. Already-canonical
input is untouched. One wasted turn per run × per model × per task type
eliminated.

---

---

## P6-T1b — `cuisine` as a Structured Field on Restaurants

**Status:** ✅ Done — Schema + COMMIT enforcement + query_pool +
count_distinct wiring landed in Slice 2; London backfill (24/24 restaurants)
landed in Slice 3 (2026-05-19). See "Done this phase" block at top.
**Depends on:** P6-T1 (tag vocabulary cleanup runs in same pass)

### Rationale

"Try 3 different cuisines over the weekend" is one of the most natural
travel planning constraints. It maps cleanly to `count_distinct: 3, field: cuisine`
but is currently impossible to express — cuisine is only in tags, and
`count_distinct` requires a structured field. The reason agents hallucinate
`cuisine_label` is that the field *should* exist.

### Scope

- **Restaurants only** — cuisine is the primary discriminating axis.
- **Cafes** — leave as tags (vibe/style matters more than cuisine; the
  Australian-vs-French-patisserie distinction rarely drives traveller decisions).
- **Bars** — leave as tags (`cocktails`, `wine`, `cask-ale`, `whisky-bar`
  already capture the relevant axis).

### Proposed vocabulary (~18 values)

`british`, `italian`, `french`, `indian`, `japanese`, `chinese`, `thai`,
`mexican`, `caribbean`, `middle-eastern`, `korean`, `american`, `spanish`,
`mediterranean`, `greek`, `turkish`, `vietnamese`, `fusion`

### Implementation

1. **`db.py`** — add `cuisine TEXT` column to `venues` table (nullable;
   non-null only for restaurants).

2. **`handbook.py`** — update venue generation prompt to require `cuisine`
   for every restaurant, selecting from the vocabulary.

3. **`pool_utils.py`** — include `cuisine` in `load_venue_pool` output.

4. **`agent_tools.py`** — add `cuisine` to `query_pool` filterable fields
   and update `_query_pool` to support `{"cuisine": "italian"}` filter.

5. **`task_agent.py`** — add `cuisine` to the valid `count_distinct` fields
   list in the handbook and the aggregation reference table.

6. **`generate_task.py`** — add `cuisine` to `_VALID_CD_FIELDS` in the
   count_distinct error message.

7. **Data** — run venue generation update pass over London to populate
   `cuisine` on all 24 restaurants.


---

## P6-T4 — Type2 Time Ceiling: Never Evaluated at Solve Time

**Status:** 🟡 Check A done (F2e in F-score); Check B (window check) deferred,
bundled with T4b's `start_time` / `ceiling_mode` schema work.
**Scope:** `eval/evaluator.py` (now F-score, not P-score — see "Done this phase"
block at top for the reclassification rationale)

The time ceiling (`query_resources.time_ceiling_minutes`) is validated at task
GENERATION time but **never checked when evaluating a solving agent's plan**.
`evaluate_p_score` has zero references to it. A plan that ignores the ceiling
entirely would receive full P-score.

### Two separate checks to implement

**Check A — Sum check (implement first, no schema change needed):**
Total `Σ(activity_durations + travel_times) ≤ time_ceiling_minutes` across all
activities in the plan. Implement as a built-in P-score check alongside the
constraint-level evaluations. Deduction: −0.3 per violation (major, it's the
whole point of type2).

**Check B — Window check (requires schema addition):**
Every activity must fall within `[start_time, start_time + ceiling]`.
Requires adding `start_time: "HH:MM"` to `query_resources` in the task format
so both task generators and solving agents know the reference window.
Without `start_time`, the sum check is the only enforceable form.

### Multi-day type2 ceiling scope

For multi-day type2 tasks, add optional `ceiling_scope: "total" | "per_day"`
to `query_resources`:
- `total` (default): ceiling applies to sum of all activities across all days
- `per_day`: ceiling applies independently each day (e.g. "only 3 hours per day")

Spring_weekend_2d (380 min total across 2 days) is a "total" case. Without
this field, solving agents and evaluators have no way to distinguish the two.

---

## P6-T5 — Type2 Geometry Check: Scoped KNN

**Status:** ✅ Done
**Scope:** `scripts/generation/generate_task.py` → `_verify_task_solvable()`

The type2 geometry check (KNN feasibility) picks K nearest venues from the
**entire universal pool**, not from the scoped subset of a constraint.

**Example failure:** `at_least: 4 scope=category=museum`. K=4. The check finds
4 nearby general venues (mix of cafes, parks, museums), computes a short
minimum schedule (~200 min), and passes a 330-min ceiling. But 4 museums
specifically need ~366 min minimum — ceiling should be rejected as too tight.

### Fix

For each `at_least: N` constraint with non-trivial scope (not `"all"`),
run a **separate KNN check on the scoped venue subset**:

```python
for c in personal_constraints:
    agg = c.get("aggregation", {})
    if isinstance(agg, dict) and "at_least" in agg:
        scope = c.get("scope", "all")
        n = agg["at_least"]
        if scope != "all":
            scoped = venues_matching(universal, scope, c.get("condition", {}))
            # run KNN on scoped with k_target=n
            # ceiling must be >= 1.1× min_time for this scoped subset
```

The ceiling must satisfy 1.1–1.3× for EACH scoped at_least constraint,
not just the full-pool estimate. The most constrained (tightest) subset wins.

---

## P6-T6 — Type2: at_least + scope=per_day K-target Undercounting

**Status:** ✅ Done
**Scope:** `scripts/generation/generate_task.py` (K-target calc),
           `scripts/generation/task_agent.py` (handbook warning)

**Problem:** `at_least: N scope=per_day` means N venues per day = N×days total.
The K-target calculator reads `at_least: N` and sets K=N regardless of days —
undercounting by days× for multi-day tasks. The ceiling gets sized for N venues
but the constraint requires N×days.

**Semantic issue:** `at_least + scope=per_day` = "minimum per day" semantics.
For type2, the lower bound should be global (`scope=all` or category-scoped)
to express "visit at least N of this type across the whole trip." `scope=per_day`
is appropriate for type1 daily caps (`at_most: 1 scope=per_day`), not for type2
global floor constraints.

### Fix

1. **K-target**: multiply by days for per_day at_least:
   ```python
   if c.get("scope") == "per_day" and "at_least" in agg:
       k_target = max(k_target, agg["at_least"] * days)
   ```

2. **Validation warning**: flag `at_least + scope=per_day` in type2 tasks:
   `"~ [pc_X] at_least with scope=per_day creates a per-day minimum (N×days total).
   For a global floor use scope='all' or a category scope instead."`
   Soft warning, not hard fail — for 1-day tasks the distinction is harmless.

3. **Protocol**: add to type2 REASONING PROTOCOL in `task_agent.py`:
   "For the at_least lower bound, use scope='all' or scope='category=X' — not
   scope='per_day'. per_day multiplies the requirement by number of days and
   conflicts with the time ceiling geometry check."


---

## P6-T4b — Type2 "Activity Spread" vs "Contiguous Window" — Task Mode Switch

**Status:** ✅ Done — schema additions, validator, evaluator, and protocol
all landed. Temporal-arc concern (raised during this design) filed in
`docs/PHASE7_FUTURE_WORK.md` as P7-A. See "Done this phase" block at top.
**Depended on:** P6-T4 Check A (time ceiling evaluation at solve time, F2e).

Type2 tasks currently assume a contiguous activity window (e.g., "I have 4.5 hours
Saturday afternoon"). But `london_spring_weekend_2d` revealed a different valid mode:
"6 hours spread across a 2-day trip" — an activity budget, not a time block.

These produce fundamentally different tasks and require different evaluation:

| Mode | Description | Evaluation |
|------|-------------|------------|
| `contiguous` | Activities must happen within a single time block | Check: all activities within [start_time, start_time + ceiling] |
| `spread` | Total activity time anywhere across the trip | Check: Σ(activity + travel) ≤ ceiling |

### Feature

Add `ceiling_mode: "contiguous" | "spread"` to `query_resources` in type2 tasks.

- **`contiguous`** (default): requires `start_time` in `query_resources`. All activities
  must fall within the window. Blank time check applies normally.
- **`spread`**: ceiling is a total budget across all days. Blank time check should use
  actual activity span per day, not full-day reference. F-score buffer check is lenient.

The type2 reasoning protocol, ceiling validator, and P-score evaluator all need to
branch on this field. Task generation agents should specify the mode explicitly.

---

## P6-T7 — Blank Time Formula Redesign

**Status:** ✅ Done
**Affects:** `eval/evaluator.py` → `evaluate_c_score` Section D

### Current formula problems

```python
# Current — span-based proxy
raw_span = max(ends) - min(starts)
day_ref  = min(raw_span, 600)
expected_cnt = day_ref / 120
if actual_cnt < floor(expected_cnt) - 1:          # -1 buffer
    deduction = 0.8 / expected_cnt * missing       # high and non-standard
```

**Problem 1 — Span conflates long visits with idle time.**  
British Museum (210 min) + lunch (60 min) + evening dinner (60 min) with a 5.5h gap
has span ≈ 600 min, expected = 5, actual = 3 → deduction fires even though the agent
spent 330 min productively. The span proxy cannot distinguish a long museum visit
from genuine idle time.

**Problem 2 — Deduction is disproportionately high.**  
`0.8 * missing/expected` gives 0.32 for 2 missing from 5 expected — equivalent to
8 × 0.04 per-issue C-score deductions. Not consistent with the rest of the score.

**Problem 3 — The -1 buffer is an arbitrary escape hatch.**  
It prevents single-slot misses but isn't principled.

### Ref count basis

`ref_count = day_ref / VENUE_TIME_COST` where:
- **Full-day tasks**: `day_ref = DEFAULT_DAY_MIN = 600` (10-hour reference day)
- **Type2 tasks**: `day_ref = time_ceiling_minutes` from `query_resources`
- This represents *total active time budget*, not the time-of-day span.

### Proposed formula

```python
sorted_acts = sorted(non_transport, key=lambda a: hhmm_to_min(a["time_start"]))

# max_gap: largest idle gap between consecutive activities.
# Special case: single activity has no consecutive pair — treat entire budget as gap
# so a 1-activity-all-day plan is correctly flagged.
if len(sorted_acts) == 1:
    max_gap = day_ref
else:
    gaps = [
        hhmm_to_min(sorted_acts[i+1]["time_start"]) - hhmm_to_min(sorted_acts[i]["time_end"])
        for i in range(len(sorted_acts) - 1)
    ]
    max_gap = max((g for g in gaps if g >= 0), default=0)

GAP_THRESHOLD = 90   # min — a genuine idle block, not a transit gap

if actual_cnt < math.floor(expected_cnt) and max_gap > GAP_THRESHOLD:
    missing   = math.floor(expected_cnt) - actual_cnt
    deduction = missing * 0.04
```

### Why no `remaining_budget` condition

`remaining_budget` (total unspent time) can exceed one venue slot even when
`max_gap < 90` — specifically when activities are clustered early with a long
trailing gap after the last one. But trailing gaps are ambiguous: the evaluator
cannot know if the trailing time is free or the person has other commitments.
Only visible *between-activity* idle gaps are unambiguously the agent's choice.

The single-activity special case handles the one scenario where remaining budget
would be large but max_gap is 0 (no consecutive pair to measure).

### Scenario table

| Plan | actual | ref | max_gap | fires? |
|------|--------|-----|---------|--------|
| 1 activity, 600-min day | 1 | 5 | 600 > 90 | ✅ correct |
| 1 activity, 90-min type2 | 1 | 0.75→0 | — | ❌ (ref<1) correct |
| 3 back-to-back, trailing gap | 3 | 5 | 5 < 90 | ❌ correct (agent chose morning) |
| Museum 9–12:30 + dinner 19–20:30 | 2 | 5 | 390 > 90 | ✅ correct |
| Full productive day, 5 venues | 5 | 5 | ~30 < 90 | ❌ correct |

Changes from current:
- **Gap check** replaces span-based proxy — only visible idle time triggers deduction
- **-1 buffer removed** — gap check is the principled guard
- **`missing × 0.04`** replaces `0.8 × missing/expected` — consistent per-issue deduction

---

## P6-T8 — recommended_visit_minutes: External Data Source

**Status:** 🔴 Not started  
**Current state:** Purely LLM-generated. Values are accurate for famous landmarks
(LLM has seen real data), but unreliable for obscure local venues.

### Preferred source: Foursquare Places API

Foursquare provides a `timeSpent` field (average minutes real users stay) per venue.
This is computed from actual check-in data — structurally equivalent to
`recommended_visit_minutes` and far more accurate than LLM estimation.

### Implementation

In the city generation pipeline, after the venue pool is finalized but before
source document generation:

1. For each venue with lat/lng, query Foursquare `GET /places/search?ll=lat,lng&name=name`
2. If a high-confidence match is found, use `timeSpent` as `recommended_visit_minutes`
3. If no match or no `timeSpent` data, fall back to LLM-generated value
4. Record the source: `visit_duration_source: "foursquare" | "llm"` for provenance

Google Places is a secondary option (good structured data but no explicit duration field).
Wikipedia/Wikidata covers major attractions cheaply but is sparse.


---

## P6-T9 — Revisit Same Venue: F4a and P-score Handling

**Status:** ✅ Done (all three: Scenario A C-score soft warning, Scenario B F4a per-venue sum, and the `at_least` distinct-venue counting fix).
**Scope:** `eval/evaluator.py` → F4a, `scripts/generation/constraint_engine.py` → at_least

No scoring tier currently detects or handles revisiting the same venue.
Two distinct scenarios require different treatment.

### Scenario A — Gratuitous duplicate (same restaurant twice for no reason)
Soft warning in C-score. No hard deduction — could be intentional.
Flag with: `~ Day N: venue_id 'X' appears more than once. If this is a split
visit (e.g. morning + afternoon at a museum), this is fine. Otherwise consider
whether this is intentional.`

### Scenario B — Split museum visit (morning visit → lunch → afternoon return)
Legitimate planning pattern for major venues. Current F4a evaluates each visit
independently against `recommended_visit_minutes`. 

**Fix:** When the same `venue_id` appears multiple times on the same day,
**sum their activity durations** before the F4a bounds check:

```python
# Group activities by venue_id per day
from collections import defaultdict
venue_day_acts = defaultdict(list)  # {venue_id: [activity, ...]}
for _, act in venue_acts:
    venue_day_acts[act["venue_id"]].append(act)

for vid, acts in venue_day_acts.items():
    total_min = sum(te_min - ts_min for ts_min, te_min in ...)
    # evaluate total_min against recommended_visit_minutes
    # (not each visit individually)
```

This correctly captures: 210 + 120 = 330 min total at British Museum (rec=210)
→ slightly over-scheduled (−0.02), which is a fair signal.

### P-score at_least — gameable by duplicate visits

**Current bug:** Visiting the same art museum twice satisfies `at_least: 2 art visit`.
The engine counts activities, not distinct venues.

**Verified:**
```
at_least: 2, scope=visit, condition=has_tag:art
→ museum visited twice: PASSES (2/2 matching)  ← incorrect
```

**Fix:** In `_evaluate_generic_constraint`, for `at_least` aggregation, 
count **distinct venue_ids** in matching activities, not total activity count:

```python
elif "at_least" in agg:
    distinct_vids = {a.get("venue_id") for a in satisfied if a.get("venue_id")}
    passed = len(distinct_vids) >= agg["at_least"]
```

`count_distinct` already correctly deduplicates (confirmed). Only `at_least` needs fixing.

Note: this makes `at_least: 2 italian meal` require 2 distinct italian restaurants,
not the same one twice. This is the right intent — the constraint tests whether the
agent found multiple qualifying venues, not whether it revisited one.

---

## P6-T10 — Non-Whole-Day Tasks Beyond Type2

**Status:** 💡 Idea to explore  
**Motivation:** Type2 already supports tasks with a partial-day time ceiling. The
concept should extend to other types. A type1 task could be:
  "I have 3 hours Friday evening — plan a dinner + drinks"
A type3 task could be:
  "Saturday afternoon only (14:00–19:00) — split between culture and food"

This is a spectrum from "whole day" (current default) to "specific time block"
(type2's explicit ceiling) with edge cases like type6's event-anchored windows.

### What this requires
- A general `active_hours` or `time_block` field in `query_resources` (not just type2)
- Blank time check should use `active_hours` as `day_ref` when present, not span
- F-score opening hours check should focus on the declared window
- P-score evaluator aware of partial-day context

For type2 specifically: also resolve `ceiling_mode: contiguous | spread`
(see P6-T4b) to distinguish "4 hours in one block" vs "4 hours total across the trip."

Low priority — current pipeline works well for full-day and type2 partial-day.
File for future architecture discussion.


---

## P6-T11 — Wrong-Info Evaluation: Three Distinct Problems (B11 Redesign)

**Status:** 🔴 Research + implementation  
**Origin:** B11 was framed as "binary → three-tier scoring." Analysis of the London
benchmark run revealed the problem is actually three separate failures at different
layers. Requires external consultation / literature review on fragile information
environment design before finalising scoring changes.

---

### Problem 1 — Data inconsistency: stale tags in authoritative source

**Fixed in this session (data fix):**
- `full_labels` in `get_official_site` contained stale booking tags (`reservation-required`
  for walk-in venues, `no-reservations` for booking-required venues) that contradicted
  the structured `booking_required` field in the same authoritative response.
- The note says "This data is authoritative" — but agents reading labels got a different
  answer than agents reading the structured field.
- **Fix applied:** `_load_city_from_db` now strips conflicting booking tags from
  `full_labels` based on the venue's actual `booking_required` value.
- **Moro fix:** Both Moro records had `page_status='committed'` in venues, yelp_listings,
  and official_site_docs tables — none were loaded as 'verified'. Fixed all three tables.
- **avg_cost_local added:** `get_official_site` now returns `avg_cost_local` so price
  wrong-info has at least one correction path.

**Remaining gap:** 11 WI venues have no official site. For these, blog search is the
ONLY correction path. The blog truth carriers ARE surfacing in top results for most
venues, but some are implicit (Smoking Goat: "I booked ahead" requires inference).

---

### Problems 2 & 3 + F2c redesign — superseded by VALID_DIFFICULTY_REDESIGN.md (2026-06)

The open design questions that were here — retrieval signal varies by field type
(Problem 2); does the agent USE the correct info (Problem 3); how to extend F2c into
a plan-value check — are now resolved in `docs/VALID_DIFFICULTY_REDESIGN.md` (branch
`valid-difficulty-redesign`). There: corruption is placed on the field a task's
binding constraint reads (so it bites the GT-based score directly), recoverability is
certified per-venue (detectability + repairability), and **F2c becomes a 3-tier
credit** (retrieved / retrieved-but-corrupt-value-used / retrieved-and-true-value-applied).
The "fragile/adversarial information environment" literature this called for is in
`Books and Papers/Papers/Internet-Noise-Benchmark/`. Stripped here to avoid a stale duplicate.


---

## P6-T12 — Currency via Open Package (not hardcoded)

**Status:** ✅ Done — SUBMIT auto-populates `query_resources.currency` from
`get_city_currency(city)`; `CITY_CURRENCY` + `_CURRENCY_SYMBOL` dicts have
been replaced with a single `CITY_COUNTRY` map (city → ISO 3166-1 alpha-2)
backed by Babel's `get_territory_currencies` and `get_currency_symbol`.
Babel added to `requirements.txt`. A fallback table preserves behaviour on
minimal installs without Babel. See the "Done this phase" block at the top
of this doc.
**Original state was:** Currency hardcoded in `pool_utils.py`:
```python
CITY_CURRENCY = {"london": "GBP", "paris": "EUR", "rio": "BRL", ...}
```
Adding a new city requires manually updating this dict.

**Fix:** Use `pycountry` (BSD-licensed) to map country → currency code,
or `babel` for full locale-aware currency formatting. Both are standard packages.

```python
import pycountry
def get_city_currency(city: str) -> str:
    country = CITY_TO_COUNTRY.get(city.lower())  # e.g. "london" → "GB"
    if country:
        currencies = pycountry.currencies.get(numeric=...)  # via country
        ...
```

Also: `query_resources.currency` is `None` in 3 of 4 type4 tasks because
agents don't fill it and there's no server-side auto-population. Add
auto-population in `_dispatch_task_tool` SUBMIT handler alongside the
existing `window_id` and `task_id` auto-corrections.

---

## P6-T13 — Per-Date Budget Support in Type 4

**Status:** 💡 Enhancement  
**Current state:** `budget_per_day` is a single value applied uniformly to all days.
There's no mechanism to express "Day 2 budget is loose (local friend paying), Days 1 and 3 are tight."

### Why useful
- More realistic scenarios: conference expenses only on work day, celebration
  dinner on birthday day only, host covers costs on one day
- More creative type4 tasks that don't all look like "tight budget + one splurge"

### Proposed schema addition
```json
"query_resources": {
  "budget_per_day": 30,
  "budget_overrides": [
    {"day": 2, "budget": 120, "reason": "host covering expenses"}
  ]
}
```
- `budget_per_day` remains the default for all days
- `budget_overrides` allows per-day exceptions
- Validator would check each day independently:
  for day in days:
    effective = override for that day, or budget_per_day
    check 1.1× ≤ effective ≤ 1.3×
- P-score evaluator would check per-day sum against per-day effective budget

Low priority — the current uniform budget already produces good type4 tasks.
File for future when we want more story diversity.

---

## P6-T14 — Forum-Doc Shape Validator

**Status:** ✅ Done (2026-05-20). See "Done this phase" block at top.

**Empirical evidence:** On London `test_70`, only **16 of 82** `doc_type='forum'`
source_docs contain any reply / turn marker. The other 66 are single-voice
narrative monologues with a forum `source_name` and forum metadata bolted on.
Working examples (target shape):
- `Z1Nq0u4F` / XOYO — OP block, then `---`, then `**Reply from <handle>:**` blocks
- `ybAmHHlg` / Brick Lane — `<Handle>:` at start of each turn (no "Reply from")
- `dkIZ46B6` / Borough Market — same `<Handle>:` pattern, multiple replies
- `nrz4fMLV` / Smoking Goat — `Reply from User '<handle>' on <date>:` (formal)

Mislabelled "forum" examples (single voice, no turns):
- `Tgjz5zwJ` / Volcano — opens "Been going to Volcano Coffee Works for about two years now"
- `zMgIqt8A` / Greenwich Park — "Posting in the London forum because I've been to both…"
- `S4iuLwJQ` / Saatchi — "Just a quick note for anyone planning a visit"

### Fix
Add a COMMIT-time check for `doc_type='forum'`:
1. Scan body lines for **handle markers**, accepting any of:
   - `Reply from <X>` lines (X is any word; permissive — anything after "Reply from")
   - `<H>:` lines where H is a plausible handle (contains underscore OR digit OR
     mixed-case beyond the first letter — excludes "Note:", "Update:",
     "Edit:", "Thread title:", "Summary:" etc.)
   - Optionally wrapped in markdown bold `**…**`
2. Hard reject COMMIT if the body has fewer than **2 distinct usernames** OR
   fewer than **2 handle-marker lines**. (Combined with the implicit OP, that's
   ≥3 turns total — matches the design intent stated in the handbook.)
3. Error message points the agent to the handbook section and a worked example.

Forward-only: existing committed forum docs stay; new venue generation gets the
new rule.

### Tests
- COMMIT regression on each of the 4 working patterns above → accepted
- COMMIT regression on a single-voice monologue → rejected
- COMMIT regression on a "Reply from <X>" doc where all replies use the same
  handle → rejected (need ≥2 distinct usernames)
- COMMIT regression on a blog doc with `Handle:` lines → accepted (rule only
  fires for `doc_type='forum'`)

---

## P6-T15 — Author Reuse Cap

**Status:** ✅ Done (2026-05-20). See "Done this phase" block at top.

**Empirical evidence:** On London `test_70`, a single author "Marcus Chen"
authored **12 docs** (5 of those open with the same "There's something about
the light…" cliché). "MumOnTheMove" authored **6 + variants**. Together
these two voices wrote ~12% of all London blog bodies, and their voice tics
have become *the* corpus voice. The audit also noted formulaic forum-handle
patterns (`<district>_<noun>`: `hackney_jim`, `shoreditch_dan`,
`eastlondon_eater`, `southlondon_foodie`).

### Fix
Add a COMMIT-time check for all source_docs:
1. Before committing, count existing committed/verified docs in the same city
   with the same author (case-insensitive, stripped).
2. If count ≥ 2, hard reject with message instructing the agent to pick a
   different author handle.
3. Apply uniformly to blog AND forum docs (forum reply handles INSIDE the body
   are not affected — this is the OP / author column only).

Cap value: **N = 2** docs per author per city. Audit data shows N=12 and N=6
in the corpus today; N=2 keeps each voice contained while still allowing the
"same blogger reviews two venues" pattern that does exist in real life.

Forward-only: existing committed docs stay; new generation gets the new rule.

### Tests
- COMMIT regression: 1st and 2nd doc by "Marcus Chen" → accepted; 3rd → rejected
- COMMIT regression: case-insensitive ("marcus chen" vs "Marcus Chen") and
  whitespace-stripped collide
- COMMIT regression: same author in a DIFFERENT city → accepted (per-city cap)
- COMMIT regression: 2 docs in `draft` status by same author don't block a
  3rd from being created (only `committed`/`verified` count toward the cap)