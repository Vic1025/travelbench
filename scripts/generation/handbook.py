"""
scripts/generation/handbook.py

Deterministic handbook for the auto-generation agent.
Called via HELP(query) tool. Pattern-matches -h style queries and returns
documentation text. No LLM, no file reads — hardcoded content.

Query interface:
  -h function              → lists all 8 tools with one-line descriptions
  -h function <name>       → full spec for one tool
  -h format                → lists all format names
  -h format <name>         → exact schema + filled example
  -h format wrong_info_rules → wrong-info categories + source-content fit rules
  -h format traffic_tier   → tier definitions + practical implications
"""

# ─────────────────────────────────────────────────────────────────────────────
# P6-T1 — Canonical tag vocabulary
# ─────────────────────────────────────────────────────────────────────────────
# Universal tag whitelist enforced at SET_TAGS time. Combined with each city's
# optional `city_config.tag_vocabulary` extension to form the per-city
# allow-list. See get_combined_vocab() below.
#
# IMPORTANT — Tags must NOT mirror information already carried by structured
# fields. The following concepts already have dedicated columns or enum values
# and MUST NOT appear in the tag vocabulary:
#   regulations.photography_allowed, regulations.pet_friendly,
#   regulations.family_friendly, regulations.wheelchair_accessible,
#   regulations.reservation_required, booking_required,
#   price_tier ("budget"), noise_level ("quiet"), dress_code ("formal"),
#   outdoor_sensitivity ("outdoor"), cuisine (column).
# Agents filter on these via query_pool's regulation/price_tier/etc. branches
# directly — listing them as tags would (1) duplicate the same info on two
# axes, (2) force source docs to surface the same concept under both
# mentioned_regulations AND mentioned_tags, and (3) waste tag slots under the
# 15-tag-per-venue cap. If a candidate tag would just restate a column value,
# drop it.

UNIVERSAL_CORE_TAGS: frozenset[str] = frozenset({
    # Practical (8) — concepts with no dedicated column
    "free-entry",
    "outdoor-seating", "rooftop", "waterfront",
    "live-music", "late-night", "cash-only", "cashless",
    "counter-seating", "communal-seating",
    # Audience (4)
    "popular-with-locals",
    "couples", "solo-friendly", "groups-welcome",
    # Dietary (5)
    "vegetarian-options", "vegan-options", "halal", "kosher", "gluten-free",
    # Accessibility (1) — wheelchair-accessible mirrors regulation column
    "step-free",
    # Vibe (6) — "quiet"/"formal" dropped (mirror noise_level/dress_code)
    "lively", "romantic", "trendy", "casual",
    "cosy", "intimate",
    # Quality signals (6)
    "hidden-gem", "instagrammable", "michelin-star",
    "iconic", "tourist-trap", "speciality",
    # Characteristic (11) — added "park" (Slice 3 vocab amendment)
    "views", "architecture", "history", "art", "design",
    "contemporary", "traditional", "garden", "market", "viewpoint",
    "park",
    # Meal slot (5)
    "breakfast", "brunch", "lunch", "dinner", "afternoon-tea",
    # Venue-type + serving-style (5) — Slice 3 vocab amendment
    # (cross-city concepts heavily used in London pre-canonicalisation).
    # "budget-friendly" dropped (mirrors price_tier).
    "coffee", "cocktails", "wine",
    "street-food", "small-plates",
})   # 53 total (P6-T2-B: 11 regulation-mirror tags removed)

# Universal cuisine vocabulary. Used both as tag values (for cafes/bars) and
# as the value range for the venues.cuisine column (Slice 2).
UNIVERSAL_CUISINES: frozenset[str] = frozenset({
    "british", "italian", "french", "indian", "japanese", "chinese",
    "thai", "mexican", "caribbean", "middle-eastern", "korean",
    "american", "spanish", "mediterranean", "greek", "turkish",
    "vietnamese", "fusion",
})   # 18 total


# ─────────────────────────────────────────────────────────────────────────────
# P6-T16 — Source-doc persona + tone vocabularies
# ─────────────────────────────────────────────────────────────────────────────
# Personas are CLOSED — exactly one of these 7 strings must appear in each
# source_doc's `persona` column. The same persona may not repeat within the
# docs of a single venue (enforced at COMMIT via doc_venue_refs join).
CANONICAL_PERSONAS: frozenset[str] = frozenset({
    "tourist on first visit",
    "local regular",
    "travel photographer",
    "food blogger",
    "parent with kids",
    "solo traveller",
    "accessibility visitor",
})   # 7 total

# Tones are OPEN — these 10 are the recommended bucket set the agent should
# spread across, but the agent is free to invent a new tone descriptor when
# none fits naturally. Anything not in this set lands in the `_invented`
# bucket of city_tone_counts. Expanded from 4 to 10 in P6-T16 so the agent
# has meaningfully different voice options across the corpus.
CANONICAL_TONES: frozenset[str] = frozenset({
    "talkative / rambling",
    "terse / factual",
    "emotional / sensory",
    "analytical / measured",
    "wry / observational",
    "nostalgic / reflective",
    "instructional / how-to",
    "complaint-first / grouchy",
    "journalistic / detached",
    "confessional / personal",
})   # 10 total

# +1 for the `_invented` aggregate bucket used by city_tone_target_per_bucket.
N_TONE_BUCKETS = len(CANONICAL_TONES) + 1


def _normalize_persona(s: str) -> str:
    """Lowercase, collapse internal whitespace, strip leading/trailing
    punctuation and whitespace. Used for canonical-match comparison and
    deduplication within a venue."""
    if not s:
        return ""
    return " ".join(s.lower().strip().strip(".,;:!?").split())


def _normalize_tone(s: str) -> str:
    """Same shape as _normalize_persona — case- and whitespace-insensitive."""
    if not s:
        return ""
    return " ".join(s.lower().strip().strip(".,;:!?").split())


# Pre-computed normalized sets so callers don't re-normalize on every check.
_CANONICAL_PERSONAS_NORM: frozenset[str] = frozenset(
    _normalize_persona(p) for p in CANONICAL_PERSONAS
)
_CANONICAL_TONES_NORM: frozenset[str] = frozenset(
    _normalize_tone(t) for t in CANONICAL_TONES
)


def get_combined_vocab(city_tag_vocabulary: list[str] | None = None) -> frozenset[str]:
    """
    Combined tag whitelist for a city: universal core + universal cuisines
    + per-city extension. Used by SET_TAGS to validate that every tag is
    on-vocab before writing to the DB.
    """
    if city_tag_vocabulary is None:
        city_tag_vocabulary = []
    return UNIVERSAL_CORE_TAGS | UNIVERSAL_CUISINES | frozenset(city_tag_vocabulary)


def format_vocab_by_axis(city_tag_vocabulary: list[str] | None = None) -> str:
    """Render the combined vocab grouped by axis — for use in venue agent
    assignment blocks and handbook help text."""
    cuisines  = ", ".join(sorted(UNIVERSAL_CUISINES))
    practical = ", ".join(sorted({
        "free-entry","outdoor-seating",
        "rooftop","waterfront","live-music","late-night",
        "cash-only","cashless","counter-seating","communal-seating"}))
    audience  = ", ".join(sorted({
        "popular-with-locals",
        "couples","solo-friendly","groups-welcome"}))
    dietary   = ", ".join(sorted({
        "vegetarian-options","vegan-options","halal","kosher","gluten-free"}))
    access    = ", ".join(sorted({"step-free"}))
    vibe      = ", ".join(sorted({
        "lively","romantic","trendy","casual","cosy","intimate"}))
    quality   = ", ".join(sorted({
        "hidden-gem","instagrammable","michelin-star","iconic",
        "tourist-trap","speciality"}))
    char      = ", ".join(sorted({
        "views","architecture","history","art","design","contemporary",
        "traditional","garden","market","viewpoint","park"}))
    meal      = ", ".join(sorted({"breakfast","brunch","lunch","dinner","afternoon-tea"}))
    venue_type = ", ".join(sorted({"coffee","cocktails","wine","street-food","small-plates"}))
    city_ext  = ", ".join(sorted(city_tag_vocabulary or [])) or "(none for this city)"
    return (
        "  Cuisine:        " + cuisines + "\n"
        "  Practical:      " + practical + "\n"
        "  Audience:       " + audience + "\n"
        "  Dietary:        " + dietary + "\n"
        "  Accessibility:  " + access + "\n"
        "  Vibe:           " + vibe + "\n"
        "  Quality signal: " + quality + "\n"
        "  Venue type:     " + venue_type + "\n"
        "  Characteristic: " + char + "\n"
        "  Meal slot:      " + meal + "\n"
        "  City-specific:  " + city_ext
    )

# ─────────────────────────────────────────────────────────────────────────────
# FUNCTION DOCS
# ─────────────────────────────────────────────────────────────────────────────

FUNCTION_INDEX = """Available tools (8 total):

  HELP(query)                     Query this handbook
  CREATE_PAGE(entity_type, ...)   Start a new DB page, get required fields
  FILL(page_id, {field: value})   Batch fill fields on a page
  COMMIT(page_id)                 Lock page after completeness check
  VERIFY("all")                   Run consistency checks on all committed pages
  GET_STATUS(venue_id)            Check state of all pages for this venue
  THINK(thought)                Log correction plan after failed VERIFY
  SUBMIT                          Mark venue complete — all pages must be verified

Query any tool for full spec: -h function <name>
"""

FUNCTION_SPECS = {

"HELP": """HELP(query: str) → str

Queries this handbook. Returns documentation text for the given query.

Parameters:
  query  str  A -h style query string (see examples below)

Returns:
  str — documentation text, or an error message if query not recognised

Examples:
  HELP("-h function")
  HELP("-h function FILL")
  HELP("-h format")
  HELP("-h format venue")
  HELP("-h format wrong_info_rules")
  HELP("-h format traffic_tier")
""",

"CREATE_PAGE": """CREATE_PAGE(entity_type: str, venue_id: str | None) → dict

Creates an empty row in the database for the given entity type.
Returns the new page_id and the list of required fields with their current values (all null).

IMPORTANT: Do NOT pass city — it is injected automatically from your assignment.
IMPORTANT: Do NOT pass a custom venue_id for venues — leave it out and one will be
           auto-generated for you. Pass venue_id for yelp_listing, source_doc, and
           official_site_doc to link them to an existing venue.

Parameters:
  entity_type  str   One of: "venue" | "yelp_listing" | "source_doc" | "official_site_doc"
  venue_id     str   For yelp_listing, source_doc, and official_site_doc: pass the
                     venue_id returned by CREATE_PAGE("venue"). For venue: omit entirely.
                     For source_doc: this is the doc's PRIMARY venue. Additional venues
                     can still be linked post-COMMIT via REGISTER_DOC_REFS.

Returns:
  {
    "status": "created",
    "page_id": "aBc1dEfG",     <- use this in FILL("aBc1dEfG", {...}) and COMMIT("aBc1dEfG")
    "entity_type": "venue",
    "record_id": "xK4mR9q",    <- THIS IS THE VENUE_ID. Save it. Use it for all linked pages.
    "next_step": "Call FILL('aBc1dEfG', {field: value, ...}) to fill fields.",
    "required_fields": {
      "name": null,
      "category": null,
      "district": null,
      ...
    }
  }

CRITICAL — record_id is your venue_id:
  When CREATE_PAGE returns for entity_type="venue", the record_id field IS the
  auto-generated venue_id. You must save this value and pass it as venue_id when
  creating yelp_listing and official_site_doc pages. Do NOT invent your own IDs.

Notes:
  - Always call CREATE_PAGE before FILL — you need a page_id first.
  - Correct page creation order:
      Step 1: CREATE_PAGE("venue")               -> save record_id as YOUR_VENUE_ID
      Step 2: FILL + COMMIT the venue page
      Step 3: CREATE_PAGE("yelp_listing", venue_id=YOUR_VENUE_ID)
      Step 4: CREATE_PAGE("source_doc", venue_id=YOUR_VENUE_ID)   -> for each blog/forum post
              (pass YOUR_VENUE_ID — the doc's PRIMARY venue. The CREATE_PAGE response
               surfaces personas_used_for_venue + city_tone_counts so you pick a
               persona/tone that fills underused buckets. Additional venue refs can
               be added post-COMMIT via REGISTER_DOC_REFS.)
              MINIMUM DOC COUNTS (enforced by VERIFY):
              • All venues:           ≥2 source_docs
              • high traffic_tier:   ≥3 source_docs  (discovery breadth)
              • wrong_info venues:   ≥3 source_docs  (need incorrect_source +
                                     truth_carrier + at least one neutral doc)
      Step 5: CREATE_PAGE("official_site_doc", venue_id=YOUR_VENUE_ID)  [only if needed]
""",

"FILL": """FILL(page_id: str, fields: dict) → dict

Batch-fills fields on an existing page. Supports override — call FILL again
on any field to change it. Accepts a single-key dict to update one field.

Parameters:
  page_id  str   The page_id returned by CREATE_PAGE
  fields   dict  Field names and values to set. All fields in one call is fine.
                 Use -h format <entity_type> to see all field names and types.

Returns:
  {
    "page_id": "aBc1dEfG",
    "updated": ["name", "category", "district"],
    "still_null": ["lat", "lng", "avg_cost_local", ...],
    "current_state": { ... all field values ... }
  }

Examples:
  FILL("aBc1dEfG", {"name": "Café des Artistes", "category": "cafe", "district": "Saint-Germain"})
  FILL("aBc1dEfG", {"recommended_pace": "relaxed"})   # single field override

Notes:
  - FILL does not validate values — COMMIT runs the completeness check.
  - You can call FILL as many times as needed before COMMIT.
  - Override any field freely — last value wins.
""",

"COMMIT": """COMMIT(page_id: str) → dict

Runs a completeness check on the page (all required fields filled, no nulls
on required columns). If it passes, locks the page and returns its full content
for you to review before VERIFY.

Parameters:
  page_id  str  The page_id to commit

Returns (success):
  {
    "status": "committed",
    "page_id": "aBc1dEfG",
    "entity_type": "venue",
    "content": { ... all field values ... }
  }

Returns (failure):
  {
    "status": "incomplete",
    "missing_required": ["lat", "lng", "recommended_pace"],
    "page_id": "aBc1dEfG"
  }

Notes:
  - COMMIT does not run consistency checks — that is VERIFY's job.
  - Review the returned content carefully before calling VERIFY.
  - After COMMIT, you can still call FILL to override fields — this resets
    page_status back to "draft" and you must COMMIT again.
""",

"VERIFY": """VERIFY(target: str) → dict

Runs consistency checks across all committed pages for this venue.
Pass "all" to check everything at once (recommended before SUBMIT).

Parameters:
  target  str  "all" (checks all committed pages for this venue)

Returns (pass):
  {
    "passed": true,
    "checks_run": 11,
    "venue_id": "xK4mR9q"
  }

Returns (failure):
  {
    "passed": false,
    "errors": [
      {
        "check": "incorrect_hours_diff",
        "detail": "yelp_hours_fri matches ground truth hours_fri — no incorrect difference present",
        "affected": ["venue:xK4mR9q", "yelp_listing:xK4mR9q"]
      },
      {
        "check": "regulation_visibility",
        "detail": "pet_friendly=1 (venue has this property) but no source doc lists 'pet_friendly' in its mentioned_regulations",
        "affected": ["venue:xK4mR9q"]
      }
    ]
  }

Checks run:
  wrong_info_matches_plan        if assignment said "Wrong info: NO", venue must have zero wrong_info entries
  no_wrong_info_on_high_traffic  traffic_tier=high venues must have zero wrong_info entries
  incorrect_hours_diff           Yelp hours differ from ground truth when hours are incorrect (if wrong_info exists)
  regulation_visibility          Every positive regulation (value=1) must appear in ≥1 source doc's mentioned_regulations
  tag_visibility                 Every yelp_visible=0 tag on the venue must appear in ≥1 source doc's mentioned_tags
  label_subset                   All yelp_visible tags exist in venue's full tag set
  official_site_exists           has_official_site=true → official_site_docs row exists
  truth_carrier_registered       Every wrong_info entry has ≥1 doc_venue_roles row as truth_carrier
  incorrect_source_registered    Every wrong_info entry has ≥1 doc_venue_roles row as incorrect_source
  source_doc_tag_limit           Each source doc's mentioned_regulations has at most 2 entries
                                 (mentioned_tags has its own 2-entry cap enforced at COMMIT time)
  recommended_pace_assigned      recommended_pace is not null
  hours_override_coverage        Known holiday dates have hours_overrides rows if venue affected

On failure:
  - Your pages are NOT deleted — they are marked needs_repair and kept intact.
  - Do NOT create a new venue or new pages.
  - Call THINK with your analysis and fix plan, then FILL the specific failing field,
    COMMIT the affected page, and call VERIFY again.
  - Only fix the specific failing check — do not redraft everything.
""",

"GET_STATUS": """GET_STATUS(venue_id: str) → dict

Returns the current state of all pages for this venue, including which
fields are still null on draft pages.

Parameters:
  venue_id  str  The venue_id to check

Returns:
  {
    "venue_id": "xK4mR9q",
    "pages": [
      {
        "page_id": "aBc1dEfG",
        "entity_type": "venue",
        "page_status": "committed",
        "null_fields": []
      },
      {
        "page_id": "pR7nKx2m",
        "entity_type": "yelp_listing",
        "page_status": "draft",
        "null_fields": ["stars", "review_count", "top_review_snippet"]
      }
    ],
    "ready_to_verify": false,
    "reason": "1 page(s) not yet committed"
  }

Notes:
  - ready_to_verify is true only when all pages are in "committed" status.
  - Call this any time to check your progress.
""",

"THINK": """THINK(thought: str) → dict

Log your reasoning at any point where it helps to think out loud:
  - Before making a wrong info decision
  - When choosing between design options
  - After a failed VERIFY (mandatory — log your correction plan before redrafting)
  - Whenever you feel uncertain about a decision

There is no cost to calling THINK. Use it freely.

Parameters:
  thought  str  A detailed explanation of what went wrong and exactly
                how you plan to fix it. Reference the specific error
                from the VERIFY report.

Returns:
  {
    "status": "logged",
    "message": "Thought recorded. Proceed with CREATE_PAGE to redraft."
  }

Example:
  THINK("VERIFY failed on incorrect_hours_diff: my yelp_hours_fri matches ground
  truth hours_fri. I need to set yelp_hours_fri to the incorrect value (one hour
  earlier than truth) to create the intended information asymmetry. I will
  CREATE_PAGE yelp_listing again and FILL with the correct incorrect value.")
""",

"SET_TAGS": """SET_TAGS(venue_id: str, tags: list) → dict

Set all tags for a venue. Tags do NOT go in FILL — always use this tool.

Parameters:
  venue_id  str   The venue_id
  tags      list  List of tag objects: [{"tag": "cozy", "yelp_visible": 1}, ...]
                  yelp_visible: 1 = shown on Yelp, 0 = ground truth only

Returns:
  {"status": "ok", "tags_set": 5, "yelp_visible": [...], "hidden": [...]}

Example:
  SET_TAGS("xK4mR9q", [
    {"tag": "specialty-coffee", "yelp_visible": 1},
    {"tag": "cozy",             "yelp_visible": 1},
    {"tag": "wifi",             "yelp_visible": 0},
    {"tag": "hidden-gem",       "yelp_visible": 0},
    {"tag": "locals-favourite", "yelp_visible": 0}
  ])

Notes:
  - Call this after COMMITting the venue page, before VERIFY.
  - yelp_visible=1: tags the owner would highlight (selling points).
  - yelp_visible=0: true but not advertised (hidden-gem, locals-favourite, etc).
  - VERIFY checks that all yelp_visible tags exist in the full tag set.
""",

"REGISTER_DOC_REFS": """REGISTER_DOC_REFS(doc_id, venue_id, role, wrong_info_id?) → dict

Register that a source_doc mentions a venue and assign its role.
Call this after COMMITting each source_doc page that should be linked to a venue.

P6-T22 NOTE: wrong-info doc roles (incorrect_source + truth_carrier) are now
handled ATOMICALLY by ADD_WRONG_INFO — you do NOT need to call this tool for
those roles. Use REGISTER_DOC_REFS only for role="neutral" links (additional
venue references for a doc that doesn't carry a wrong-info value).

Parameters:
  doc_id          str   The record_id returned by CREATE_PAGE("source_doc")
  venue_id        str   The venue this doc mentions
  role            str   neutral | incorrect_source | truth_carrier
                        (Use "neutral" in normal flow. For incorrect_source/
                        truth_carrier, pass the doc_ids directly to
                        ADD_WRONG_INFO instead — it registers both atomically.)
  wrong_info_id   str   Optional. Only used if you're re-attaching a wrong-info
                        role manually (rare). Normal wrong-info flow goes
                        through ADD_WRONG_INFO.

Returns:
  {"status": "ok", "doc_id": ..., "venue_id": ..., "role": ...}

Roles:
  neutral          — mentions venue but doesn't touch the wrong-info fields.
                     Use REGISTER_DOC_REFS with role="neutral".
  incorrect_source — handled by ADD_WRONG_INFO (atomic). Don't call here.
  truth_carrier    — handled by ADD_WRONG_INFO (atomic). Don't call here.

Example (neutral doc — the standard usage):
  REGISTER_DOC_REFS("pR7nKx2m", "xK4mR9q", "neutral")
""",

"ADD_WRONG_INFO": """ADD_WRONG_INFO(venue_id, affected_field, incorrect_value, correct_value,
               source_type, wrong_info_category, origin_story,
               incorrect_source_doc_id, truth_carrier_doc_id) → dict

ATOMIC wrong-info workflow (P6-T22). Creates the wrong_info entry AND registers
both required source-doc roles (incorrect_source + truth_carrier) in ONE
transactional call. You do NOT need to call REGISTER_DOC_REFS separately for
these roles — this single call does it all.

Prerequisite: both source_docs (the one carrying the wrong value, the one
carrying the correction) must already be COMMITTED before calling this.

Parameters:
  venue_id                   str  The venue
  affected_field             str  Which field is wrong: "hours_fri", "hours_mon", etc.
  incorrect_value            str  The wrong value shown in the incorrect source e.g. "20:00"
  correct_value              str  The correct ground truth value e.g. "22:00"
  source_type                str  yelp | blog | forum
  wrong_info_category        str  temporal_decay | propagation_error | conditional | subjective
  origin_story               str  One sentence: how did this mistake enter this source?
  incorrect_source_doc_id    str  doc_id of the COMMITted source_doc whose body carries
                                  the wrong value (the agent encounters this and may
                                  believe it — no awareness it's wrong). REQUIRED.
  truth_carrier_doc_id       str  doc_id of the COMMITted source_doc whose body carries
                                  the correction (embedded naturally in experience prose).
                                  REQUIRED. Must differ from incorrect_source_doc_id.

Returns (success):
  {
    "status": "ok",
    "wrong_info_id": "wi_abc123",
    "venue_id": "xK4mR9q",
    "affected_field": "hours_fri",
    "incorrect_source_doc_id": "<doc_id>",
    "truth_carrier_doc_id": "<doc_id>",
    "note": "wrong_info row + both doc role registrations committed atomically..."
  }

Example:
  ADD_WRONG_INFO(
    venue_id="xK4mR9q",
    affected_field="hours_fri",
    incorrect_value="20:00",
    correct_value="22:00",
    source_type="yelp",
    wrong_info_category="temporal_decay",
    origin_story="Owner registered hours when the cafe closed at 20:00 but extended to 22:00 in 2024 without updating Yelp.",
    incorrect_source_doc_id="aBcD1234",   # blog post showing 20:00 close
    truth_carrier_doc_id="eFgH5678",      # blog post mentioning the change to 22:00
  )

Common errors:
  - Either doc_id missing → error (both required).
  - Same doc_id for both roles → error (one doc can't carry both).
  - doc_id refers to a draft (uncommitted) doc → error.
  - doc_id already plays a different wrong-info role on this venue → error.

Note: If source_type="yelp", you must ALSO call FILL on the yelp_listing page
to set yelp_<field> = "incorrect_value" so it surfaces in search_yelp output.
""",

"SUBMIT": """SUBMIT() → dict

Marks this venue as complete. Can only be called after VERIFY("all") has
passed — all pages must be in "verified" status.

Parameters:
  None

Returns (success):
  {
    "status": "submitted",
    "venue_id": "xK4mR9q",
    "pages_submitted": 3,
    "message": "Venue package complete and saved."
  }

Returns (failure):
  {
    "status": "error",
    "message": "Cannot submit: 2 page(s) not yet verified.",
    "unverified": ["aBc1dEfG", "pR7nKx2m"]
  }

Notes:
  - After SUBMIT, the venue is locked and cannot be modified.
  - SUBMIT is your final action for this venue.
""",
}


# ─────────────────────────────────────────────────────────────────────────────
# FORMAT DOCS
# ─────────────────────────────────────────────────────────────────────────────

FORMAT_INDEX = """Available formats:

  venue              Ground truth venue record
  yelp_listing       Yelp listing (planning agent visible)
  source_doc         Blog or forum post
  official_site_doc  Official website document
  wrong_info_rules   Wrong information categories + source-content fit rules
  traffic_tier       Traffic tier definitions + practical implications
  tags               Tag conventions and yelp_visible rules
  hours              Hours format for all day columns

Query any format for full schema + example: -h format <name>
"""

FORMAT_SPECS = {

"venue": """VENUE — Ground truth record (never exposed to planning agent)

Required fields:
  venue_id          TEXT    7-char random ID (auto-generated by CREATE_PAGE)
  city              TEXT    City key e.g. "paris"
  name              TEXT    Full venue name
  category          TEXT    restaurant | cafe | bar | museum | attraction | park | neighbourhood
  district          TEXT    Neighbourhood name e.g. "Le Marais"
  lat               REAL    Decimal latitude
  lng               REAL    Decimal longitude
  avg_cost_local      REAL    Average cost per person in the city's local currency
                              (GBP for London, JPY for Tokyo, EUR for Paris, etc.)
                              Use the actual local amount charged — do NOT convert to USD.
  price_tier        TEXT    budget | mid | upscale | fine-dining
  recommended_visit_minutes  INTEGER  Typical visit duration in minutes
  booking_required  INTEGER 0 or 1
  has_official_site INTEGER 0 or 1
  outdoor_sensitivity TEXT  indoor | outdoor
  recommended_pace  TEXT    relaxed | moderate | intense
  traffic_tier      TEXT    high | mid | low
  total_results     INTEGER Simulated search result count (from traffic_tier)
  yelp_popularity_score REAL 0.1–1.0 (derived from traffic_tier + jitter)
  pet_friendly      INTEGER 0 or 1
  wheelchair_accessible INTEGER 0 or 1
  parking_nearby    INTEGER 0 or 1
  photography_allowed INTEGER 0 or 1
  noise_level       TEXT    quiet | moderate | loud
  reservation_required INTEGER 0 = walk-in OK, 1 = advance booking essential.
                               You MUST set this field — it has no default.
                               Set to 1 for: Michelin / tasting-menu restaurants,
                               popular attractions with timed entry (major museums
                               with ticketed slots), private members clubs.
                               Set to 0 for: casual restaurants, cafes, bars,
                               parks, and anywhere walk-in is the norm.
  outside_food_allowed INTEGER 0 or 1
  family_friendly   INTEGER 0 or 1
  food_available    INTEGER 0 or 1

Optional fields:
  address           TEXT    Street address
  hours_mon/tue/wed/thu/fri/sat/sun  TEXT  See -h format hours
  lunch_cost_local    REAL    Food venues only
  dinner_cost_local   REAL    Food venues only
  local_cuisine     INTEGER 0 or 1, food venues only
  cuisine           TEXT    REQUIRED for restaurants; optional elsewhere.
                            Must be one of the universal cuisine values:
                              british, italian, french, indian, japanese,
                              chinese, thai, mexican, caribbean, middle-eastern,
                              korean, american, spanish, mediterranean, greek,
                              turkish, vietnamese, fusion.
                            Powers `count_distinct: cuisine` task constraints.
                            For cafes/bars, set ONLY if cuisine is a defining
                            property (a Japanese cafe, a Spanish tapas bar).
  age_restriction   INTEGER Minimum age (null if none)
  dress_code        TEXT    null if no entry policy; otherwise a short description.
                            Set based on the venue's character — do not default to null.
                            Examples by venue type:
                              Michelin restaurant / fine-dining club → "smart casual"
                              Rooftop bar with door policy           → "smart casual"
                              Formal hotel restaurant                → "formal"
                              Jacket-required private members club   → "jacket required"
                              Casual bar, cafe, street food, park    → null (no policy)
                            When dress_code is set, at least one source doc body must
                            mention the dress code naturally (e.g. "jackets preferred",
                            "smart casual only", "no shorts at dinner").
  recommended_time_window_end    TEXT  "HH:MM" or null
  recommended_time_window_reason TEXT  Explanation or null
  venue_difficulty_score  REAL  Set post-generation, leave null during creation

Example (filled):
  venue_id: "xK4mR9q"
  city: "paris"
  name: "Café des Artistes"
  category: "cafe"
  district: "Saint-Germain"
  lat: 48.8540, lng: 2.3376
  avg_cost_local: 12.0
  price_tier: "budget"
  recommended_visit_minutes: 45
  booking_required: 0
  has_official_site: 0
  outdoor_sensitivity: "indoor"
  recommended_pace: "relaxed"
  traffic_tier: "mid"
  total_results: 340
  yelp_popularity_score: 0.52
  pet_friendly: 1
  wheelchair_accessible: 0
  noise_level: "quiet"
  food_available: 1
  ...
""",

"yelp_listing": """YELP_LISTING — Planning agent visible via search_yelp

Required fields:
  venue_id          TEXT    FK to venues table (same ID)
  city              TEXT    City key
  name              TEXT    Venue name (same as ground truth)
  category          TEXT    Same as ground truth
  district          TEXT    Same as ground truth
  stars             REAL    1.0–5.0
  review_count      INTEGER Number of reviews
  yelp_popularity_score  REAL  Same as venues table

Required fields (hours):
  yelp_hours_mon/tue/wed/thu/fri/sat/sun  TEXT  Must match venue ground truth hours.
                                                Use 'Closed' for closed days.
                                                If venue has wrong_info on hours, set that
                                                day to the INCORRECT value instead.

Optional fields:
  top_review_snippet  TEXT  Short quote from a review
  last_activity_date TEXT   "YYYY-MM-DD" — when owner last updated listing or last review posted.
                             Critical age signal: old date + wrong hours = plausible temporal decay.
                             Set this to reflect the venue's actual online neglect level.
  official_url      TEXT    URL if venue has official site (used by planning agent to fetch_url)

Notes on Yelp labels:
  Labels exposed by Yelp are stored in the tags table with yelp_visible=1.
  Do NOT store labels on the yelp_listing row itself.
  Choose labels that reflect how an owner would present their venue —
  selling points, eye-catching features. Not a random subset.

Notes on incorrect hours:
  If this venue has a wrong_info entry with source_type="yelp", set the
  affected yelp_hours_* column to the incorrect (wrong) value.
  All other day columns should match ground truth.
  See -h format wrong_info_rules for guidance on when to introduce wrong info.

Example (filled):
  venue_id: "xK4mR9q"
  city: "paris"
  name: "Café des Artistes"
  category: "cafe"
  district: "Saint-Germain"
  stars: 4.3
  review_count: 127
  yelp_hours_mon: "08:00-18:00"
  yelp_hours_fri: "08:00-19:00"   ← incorrect if wrong_info affects friday
  top_review_snippet: "Perfect morning spot, the almond croissant is unmissable."
  official_url: null
""",

"source_doc": """SOURCE_DOC — Blog or forum post (planning agent visible)

Required fields:
  doc_id        TEXT    8-char random ID (auto-generated by CREATE_PAGE)
  city          TEXT    City key
  doc_type      TEXT    blog | forum
  title         TEXT    Post title
  author        TEXT    Author name or username
  source_name   TEXT    Platform name e.g. "TravelTalk Forums", "ParisWeekender.blog"
  date          TEXT    "YYYY-MM-DD"
  body          TEXT    Full post text (natural human prose)
  persona       TEXT    One of 7 canonical personas (closed vocab) — see
                        "Persona" section below. Each persona may appear
                        ONLY ONCE per venue (COMMIT hard-rejects repeats).
  tone          TEXT    One of 10 canonical tones (see "Tone" section) OR
                        a new tone descriptor you invent — tone is OPEN
                        vocabulary. Aim for tone variety across the docs of
                        the same venue and across the corpus (CREATE_PAGE
                        returns city-wide tone counts to help you balance).

Optional fields:
  likes                  INTEGER Default 0
  saves                  INTEGER Default 0
  view_count             INTEGER Default 0
  mentioned_regulations  TEXT    JSON array of regulation field names explicitly mentioned
                                 in this doc. Used by VERIFY to check regulation_visibility.
                                 Example: '["pet_friendly", "family_friendly"]'
                                 Strict coupling: list a tag here if and only if the body
                                 text addresses it. If the body says nothing about pets,
                                 do NOT list pet_friendly. If the body mentions wheelchair
                                 access, you MUST list wheelchair_accessible here.
                                 Valid values: "pet_friendly", "wheelchair_accessible",
                                 "family_friendly", "outside_food_allowed",
                                 "reservation_required", "parking_nearby", "age_restriction"
                                 List a regulation here for BOTH positive AND negative values:
                                 - If the venue IS pet_friendly (value=1), the body should
                                   mention it naturally ("dog-friendly patio", "our spaniel loved it").
                                 - If the venue is NOT pet_friendly (value=0), the body should
                                   hint at the restriction naturally — through experience, not
                                   as a policy statement. Examples:
                                     wheelchair_accessible=0 → "steep spiral staircase down to the bar"
                                     pet_friendly=0 → "had to tie up the dog outside unfortunately"
                                     family_friendly=0 → "definitely an adults-only vibe, over-18s only"
                                     outside_food_allowed=0 → "they're strict about no outside food"
                                   A restriction hint in the body counts as mentioning the regulation.
                                   List it in mentioned_regulations so VERIFY can track coverage.
  mentioned_tags         TEXT    JSON array of tag strings the body addresses.
                                 Example: '["instagrammable", "popular-with-locals"]'
                                 Used by VERIFY to check tag_visibility — every
                                 yelp_visible=0 tag on the venue MUST appear in
                                 `mentioned_tags` on at least one of the venue's source docs.
                                 Mirrors mentioned_regulations exactly:
                                 - Strict coupling: list a tag IF AND ONLY IF the body
                                   addresses the concept. Don't drop a tag concept into
                                   the body without listing it. Don't list a tag the body
                                   doesn't actually address.
                                 - BOTH directions count as "addressing":
                                     positive  → "the wallpaper is gorgeous — perfect
                                                  for photos" surfaces `instagrammable`.
                                     restriction-hint → "the place was packed with
                                                  regulars from the neighbourhood" surfaces
                                                  `popular-with-locals`, even if the venue
                                                  isn't formally Yelp-marketed as such.
                                 - Max 2 tags per doc. Spread the venue's hidden tags
                                   across multiple docs.
                                 - Every entry must be in the city's combined vocabulary
                                   (universal core + cuisines + city extension).
                                   SET_TAGS' validation rules apply.
                                 - Tags that mirror regulation columns or other structured
                                   fields are NOT in the vocab any more (P6-T2-B). Use the
                                   mentioned_regulations side for `pet_friendly`,
                                   `wheelchair_accessible`, `family_friendly`,
                                   `reservation_required` (booking_required), etc. — never
                                   surface those concepts via mentioned_tags.

After COMMIT, register venue references and roles:
  The orchestrator will prompt you to fill doc_venue_refs (which venues are mentioned)
  and doc_venue_roles (the role of this doc for each venue: incorrect_source | truth_carrier | neutral).

Notes on body content:
  - Must read as natural human writing — not structured, not robotic.
  - Forum posts: conversational Q&A, 3-5 turns, embedded corrections feel natural.
  - Blog posts: narrative prose, personal experience, not a fact sheet.
  - Incorrect source docs: casual mention of wrong info, NO awareness it might be wrong.
  - Truth carrier docs: correction embedded in experience — "went last week and noticed..."
  - likes/saves/view_count should reflect traffic_tier: high-traffic venues get popular posts.

Forum doc shape (P6-T14) — ENFORCED AT COMMIT:
  doc_type='forum' MUST contain real Q&A structure. COMMIT will hard-reject any
  forum doc whose body does not include ≥2 turn markers from ≥2 distinct
  speakers. Accepted speaker-marker patterns at the start of each turn:
    - "Reply from <handle>:"                      (markdown bold OK)
    - "Reply from User '<handle>' on YYYY-MM-DD:"
    - "<Handle>:"  where the handle contains an underscore, digit, or
      mixed-case beyond the first letter (so NorthLondonMum, LDN_Dad32,
      Cheerio_Lad, Laura_83 count; "Note:", "Update:", "Thread title:" do not)

  If your post is a single-author monologue, set doc_type='blog' instead.
  Forum-style metadata bolted onto a blog post will fail review.

  Worked example (good):
    Thread title: Brick Lane with a toddler — manageable?

    NorthLondonMum: Thinking of taking our 3-year-old to Brick Lane market this Sunday…
    LDN_Dad32: We took our 2-year-old last month. It's busy but it's a street not
      an enclosed space so you can dip in and out…
    EastEnderSarah: Went with my 4yo and 18mo. Honestly it's fine if you go early —
      aim for 10am before the real crowds hit…

Author reuse cap (P6-T15) — ENFORCED AT COMMIT:
  Each author may write AT MOST 2 committed/verified source_docs per city
  across the entire corpus. The cap is case-insensitive on the author string.
  Once the cap is hit, COMMIT will reject the 3rd doc and ask you to pick a
  different handle.
  Why: keeping a small handful of authors from dominating the corpus is what
  keeps voice/tone diverse. No single blogger should be writing 10+ London
  reviews — the audit found exactly that pattern collapsed the whole corpus
  into one voice.
  Tip: when generating multiple docs for the same venue or run, vary the
  author handle by persona archetype — a parent voice ("MumOnTheMove") and a
  photographer voice ("Anika Patel") can each appear at most twice across the
  entire city's source_docs.

Source doc diversity — IMPORTANT (P6-T16):
  Each venue typically has 2–4 source docs. Persona AND tone must both differ
  across docs — two docs sharing a persona, or sharing a tone, are not
  diverse enough even if the facts differ. The persona and tone are now
  required fields on every source_doc, declared during FILL.

  Persona (CLOSED vocab — exactly one per doc, no repeat within a venue.
  COMMIT will hard-reject if a persona is already used by another committed
  source_doc on the same venue):
    tourist on first visit   Over-explains obvious things, no local knowledge, wide-eyed
    local regular            Casual and assuming, references past visits, neighbourhood slang
    travel photographer      Visual/sensory focus, mentions light and atmosphere, composition
    food blogger             Grades dishes granularly, compares to other places, opinionated
    parent with kids         Practicalities: kids' menu, noise, toilets, stroller access
    solo traveller           Notes staff warmth, solo-dining ease, getting a table alone
    accessibility visitor    Specifically notices ramps, door width, table spacing, staff help

  Tone (OPEN vocab — these 10 are the recommended buckets but you may invent
  a new descriptor when none fits naturally; aim for tone variety across
  docs of the same venue and across the city as a whole):
    talkative / rambling      Long sentences, tangents, lots of "and also..." and "oh and..."
    terse / factual           Short clipped sentences, minimal adjectives, checklist energy
    emotional / sensory       Atmosphere and feeling: "there was this golden afternoon light..."
    analytical / measured     Hedges claims ("probably", "seemed like"), weighs pros and cons
    wry / observational       Dry humour, ironic distance, "not what I expected"
    nostalgic / reflective    Looks back, references previous visits or how the place used to be
    instructional / how-to    Second-person guidance: "if you go, do X first"
    complaint-first / grouchy Opens with frustration or skepticism, may warm up later
    journalistic / detached   Third-person voice, press-release / travel-magazine feel
    confessional / personal   Frames the visit around the author's mood: "had a rough week so I..."
    (or coin your own, e.g. "reluctant nostalgia", "deadpan factual")

  Cross-venue tone spread: CREATE_PAGE("source_doc", venue_id=...) returns a
  `city_tone_counts` dict and a `city_tone_target_per_bucket` soft target.
  Treat it as a nudge — prefer tones well below the target, avoid tones well
  above. The `_invented` bucket aggregates all non-canonical tones combined.

mentioned_regulations ↔ body content (strict coupling) — IMPORTANT:
  The body must mention a regulation if and only if it appears in mentioned_regulations.
    - wheelchair_accessible IS in mentioned_regulations  →  body must reference it naturally
    - pet_friendly is NOT in mentioned_regulations       →  body must NOT mention pets at all
  Do not casually drop a regulation fact into the body without listing it in mentioned_regulations.
  Do not list a tag in mentioned_regulations that the body does not actually address.
  This applies to BOTH positive (value=1) and negative (value=0) regulations.

mentioned_tags ↔ body content (strict coupling) — IMPORTANT:
  Same rule as mentioned_regulations, applied to free-form tags:
    - A tag IS in mentioned_tags  →  the body must address the concept naturally
    - A tag is NOT in mentioned_tags →  the body must NOT surface that concept
  Do not drop a tag concept into the body without declaring it in mentioned_tags.
  Do not declare a tag the body does not actually address.
  BOTH directions count as "addressing":
    - positive  → "every wall is a photo-op" surfaces `instagrammable`.
    - restriction-hint → "every face at the bar looked like a regular" surfaces
                          `popular-with-locals` even when the venue isn't pitched
                          that way on Yelp.
  Coverage rule (enforced by VERIFY):
    Every yelp_visible=0 tag on the venue MUST appear in `mentioned_tags` on at
    least one of the venue's source docs. If a hidden tag is genuinely too subtle
    to surface in any doc, either make it yelp_visible=1 (so it surfaces via
    search_yelp) or drop the tag — it isn't adding benchmark-detectable value.

Tag / regulation coverage — IMPORTANT:
  - Each source doc may mention at most 2 regulations in mentioned_regulations
    AND at most 2 tags in mentioned_tags (separate caps, applied per doc).
    VERIFY will fail if a doc lists more than 2 of either (source_doc_tag_limit check).
  - Spread the venue's regulations across different docs — each doc covers a different
    1–2 regulations that fit naturally into that author's voice and perspective.
  - NEGATIVE regulation coverage: if a venue has wheelchair_accessible=0, pet_friendly=0,
    or family_friendly=0, at least one source doc SHOULD hint at that restriction.
    Use the "parent with kids" persona to naturally surface family issues, or the
    "accessibility visitor" persona to surface wheelchair issues. The restriction
    should be embedded in experience, not stated as policy:
      ✓ "The entrance is down a narrow staircase — gorgeous inside but no lift"
      ✗ "Note: this venue is not wheelchair accessible"
  - Highly avoid repeating a regulation tag that already appeared in another source doc
    for the same venue, unless it is highly central to the venue and enriches that doc.
  - Do NOT write run-on sentences that list every regulation at once. This pattern is
    unnatural and will fail review:
      BAD: "The venue is wheelchair accessible via a lift, and photography is allowed
           (flash discouraged). It's 18+, casual dress, and definitely not for the
           faint-hearted – the atmosphere is loud, crowded, and utterly alive."
      GOOD: One doc mentions accessibility naturally because the author uses a wheelchair.
            A different doc mentions the age restriction because the author got carded.

Example (blog):
  doc_id: "pR7nKx2m"
  city: "paris"
  doc_type: "blog"
  title: "A perfect morning in Saint-Germain"
  author: "Sophie M."
  source_name: "QuietTravelBlog.com"
  date: "2024-11-20"
  likes: 312
  body: "I always start my Paris mornings at Café des Artistes on Rue Bonaparte.
         It opens at eight and the almond croissants are gone by ten, so don't
         sleep in. The owner is lovely — she remembered my order from last trip..."
""",

"official_site_doc": """OFFICIAL_SITE_DOC — Authoritative venue website (planning agent via fetch_url)

Only create this if the venue's has_official_site = 1.
Planning agent accesses this by calling fetch_url(url) — never by venue_id.

Required fields:
  venue_id        TEXT    FK to venues (same ID)
  city            TEXT    City key
  url             TEXT    Full URL e.g. "https://cafe-des-artistes-paris.fr"
  retrieved_date  TEXT    "YYYY-MM-DD" (set to a recent date)
  body            TEXT    Full page text — authoritative, always correct

Optional fields:
  hours_mon/tue/wed/thu/fri/sat/sun  TEXT  Always correct (matches ground truth)
  full_regulations  TEXT    JSON string of all regulation fields
  full_labels       TEXT    Comma-separated full label list
  ticket_availability TEXT  JSON string of date→status dict
  active_event      TEXT    JSON string or null

Notes:
  - Body should feel like a real venue website — opening hours, about section,
    booking info, policies. Not just a data dump.
  - If this is an incorrect-hours venue, the official site ALWAYS has the correct hours.
    The incorrect value only lives in the Yelp listing and/or blog posts.
  - full_regulations and full_labels can include information not shown on Yelp,
    giving the planning agent more detail if it checks the official site.
""",

"wrong_info_rules": """WRONG INFORMATION — Generation rules

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD RULE: No wrong info on high-traffic venues
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
traffic_tier=high venues must have zero wrong_info entries. VERIFY will
reject any high-traffic venue with wrong info. High-traffic venues have
active owners and dense correction ecosystems — wrong info does not persist.
If your assignment says "Wrong info: YES" and the traffic_tier is "high",
that is a conflict — do not add wrong info; the brief is overridden by this rule.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRIMARY GATE: Origin story first
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Before writing any wrong info entry, write one sentence explaining how a
real person or system produced this specific mistake in this specific source.
If that sentence cannot be written convincingly — do not add wrong info.

Wrong info is a design decision, not a default. Not every venue needs it.
When your assignment says "Wrong info: NO" — do not call ADD_WRONG_INFO.
When it says "Wrong info: YES" — add at least one entry. You may add more
if the venue character supports it, but quality beats quantity (see below).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
QUALITY OVER QUANTITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
One well-designed trap is worth more than three obvious ones.

A well-designed trap:
  - Is reasonable on the surface — the wrong value is plausible for this venue type
  - Is delivered through sources that feel independent but share the same error
  - Requires the agent to notice a subtle inconsistency or find a buried correction
  - Follows common sense — nothing about it should feel implausible to a human reader

A poorly designed trap:
  - Stacks multiple wrong_info entries on obvious fields (hours, hours, hours)
  - Makes each incorrect source visibly suspect (outdated dates, contradictory prose)
  - Is trivially correctable from the first document the agent reads

Multiple entries are appropriate for a venue that has genuinely deteriorated —
an abandoned-feeling place with a decade-old website, a forum thread from 2014
still cited as current, reviews that contradict each other on basic facts. In
that case, the accumulation of errors IS the signal. But each entry still needs
its own believable origin story, and collectively they should paint a coherent
picture of neglect — not feel like a list of random mistakes.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FIELDS THAT CAN HAVE WRONG INFO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Structured fields (stored in DB, validated by ADD_WRONG_INFO):
  hours_*                    Closing/opening time changed; split service misread
  avg_cost_local / price_tier  Price was higher or lower when source was written
  booking_required           Policy changed: walk-in ↔ reservation-required
  recommended_visit_minutes  Blogger estimated from one short visit

Tag/label fields (appear in blog and forum prose, not ADD_WRONG_INFO):
  Any tag or regulation can be wrong when reported in blog or forum text.
  The source says the venue is dog-friendly, wheelchair accessible, has live
  music, is vegan — but ground truth says otherwise, or the condition has changed.
  These are handled through doc body text and mentioned_regulations, NOT through
  ADD_WRONG_INFO (which is for structured fields only).
  Common examples: pet_friendly, wheelchair_accessible, photography_allowed,
  noise_level ("intimate and quiet" when it's actually loud on weekends),
  any tag that is plausible but inaccurate for this specific venue.

DO NOT use wrong info on:
  name, category, district, lat, lng — too easy to verify, no believable origin
  Official site content — authoritative by design, venue controls it directly

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOURS DIRECTION RULE (temporal_decay)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A trap only works when the agent's plan FAILS because of wrong info.
For temporal_decay, the incorrect value must be wider on at least one end:
  incorrect opens EARLIER than correct  — agent plans early visit, venue is closed
  incorrect closes LATER than correct   — agent plans late visit, venue is closed
  Both together is also fine.

  VALID:
    incorrect="16:00-23:30", correct="17:00-23:00"  ← opens earlier AND closes later
    incorrect="16:00-23:00", correct="17:00-23:00"  ← opens earlier, same close
    incorrect="17:00-23:30", correct="17:00-23:00"  ← closes later, same open

  INVALID (rejected — harmless on both ends):
    incorrect="18:00-22:00", correct="17:00-23:00"  ← opens later AND closes earlier

For conditional/seasonal hours (wrong_info_category="conditional"):
  No direction restriction. The incorrect source shows normal hours with no
  awareness of the exception (holiday closure, reduced winter hours, etc.).
  Use correct_value to capture what actually applies on the affected day.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THE FIVE CATEGORIES — and how they show in documents
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Categories are not just labels — each one demands a specific documentary
fingerprint. A claimed category must be visible in the source content.
A wrong_info entry can also be a combination of categories (e.g. a propagation
error that has also decayed — miscopied AND now outdated).

CATEGORY 1 — Temporal decay
  True when written, false now. The world changed; the source did not.
  Documentary fingerprint: the incorrect source must show age.
    - Old publish date (1–5+ years ago)
    - No awareness of recent changes
    - Tone of someone reporting current fact, not hedging
  Source fit: Yelp (owner never logged back in), old blog posts, old forum replies.
  Not for: high-traffic venues with active owners.
  Example: "Owner registered Yelp when cafe closed at 19:00; extended to 20:00
            in 2024 without logging back in."

CATEGORY 2 — Propagation error (hand-designed only, do not auto-generate)
  Wrong from the start — copied source to source, error reinforced by repetition.
  This category requires subtle craft: the similarity across sources must be
  detectable to a careful reader but not immediately obvious. Auto-generation
  reliably produces either too-obvious or too-invisible propagation patterns.
  Reserved for editorial additions per city alongside Category 4.
  If you want to suggest a Category 2 candidate, note it in your THINK log
  but do not implement it — leave it for hand-design.

CATEGORY 3 — Conditional / seasonal
  Correct in general, wrong under specific conditions the source ignores.
  Documentary fingerprint: the source shows NO awareness of the condition.
    - Lists hours or policy as universal with no seasonal caveat
    - Written in a season or context where the condition did not apply
    - A careful reader would notice the author never considered exceptions
  KEY RULE: Casual, not highlighted. A source that says "open including
  Christmas!" is LESS suspicious — the explicitness signals awareness.
  The trap is the source that just says "open daily 10:00-18:00" with no
  awareness that this is untrue in winter.
  Example: "Blogger visited in July; winter hours (reduced 90 min) apply
            Nov–Feb but the post shows zero awareness of this."

CATEGORY 5 — Adversarial / incentive-distorted
  Deliberately misleading, not accidentally wrong. Owner-submitted inflated
  claims, fake review content, SEO content farms scraped by aggregators.
  Documentary fingerprint: suspiciously promotional tone, no experiential grounding,
  claims that serve the venue's interest rather than the traveller's.
  Use sparingly — requires a source that reads as promotional rather than genuine.
  Example: "Venue lists itself as 'always available, no booking needed' on its own
            listing during peak season, when it is actually booked out weeks ahead."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REDESIGN TARGET (valid-difficulty branch) — flaw STRUCTURES  [pending, not yet wired]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
See docs/VALID_DIFFICULTY_REDESIGN.md §3. The five categories above stay; the
redesign adds STRUCTURE (how evidence is laid out across sources) and two difficulty
axes, all authored HERE in the venue-gen agent (ADD_WRONG_INFO + the doc body you
write). Target structures:

  - Minority-truth conflict: wrong value in j of K sources; truth present but NOT
    the majority. (single-truth fields: avg_cost, cuisine, price_tier)
  - Copying bloc: the MAJORITY of sources share one COPIED wrong value + a shared
    fingerprint on another venue; the honest minority is right. This UPGRADES
    Category 2 (propagation) to auto-generable — naive majority-vote now fails,
    yet a careful reader who spots the shared fingerprint recovers truth.
  - Omission / recall trap (multi-truth): the majority OMIT a true list element
    (amenity, wheelchair, a day's hours) rather than assert a false one.
  - Entity-resolution trap: a near-duplicate venue / name variant the agent must
    match — or two similar-named venues it must NOT conflate.
  - Stale + authority: one stale source, recovery via an authoritative recent
    source — the easy floor / calibration anchor.
  - No-truth / ambiguous control: the correct careful answer is "unknown".

  Direction: bias FALSE-POSITIVE (a violating venue looks satisfying).
  Difficulty per flaw = (detectability = # independent sources contradicting the
  lie; repairability = true value's share of the candidate set). Validity: a
  certifier admits iff detectability >= 1 AND 0 < repairability < 1. The mandatory
  truth-carrier becomes OPTIONAL (recoverability is tested, not structurally forced).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SOURCE-CONTENT FIT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Each source type has a characteristic error profile. The wrong info must
feel natural for the source it appears in.

  Yelp:
    Plausible: hours, price range, outdated tags.
    Mechanism: owner self-registration, no audit, low update rate.
    NOT plausible: a restaurant saying "no reservation required" when it
    actually does — they would lose business, they would never publish it.

  Blog:
    Plausible: one-visit observation wrongly generalised.
      "No reservation needed" — walked in on a quiet Tuesday.
      "Dog-friendly terrace" — true in summer; terrace closes in winter.
    Mechanism: single data point, honestly but incompletely reported.

  Forum:
    Plausible: old experience stated as current fact; second-hand info.
      "Went two years ago, closes at 22:00" — temporal distance.
      "My friend said open Sundays" — second-hand, unverified.
    Mechanism: time gap or transmission, not carelessness.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW WRONG INFO SHOWS IN DOCUMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A well-designed trap gives the agent reason to be suspicious before it
finds the correction. Wrong info must feel believable to a casual reader
but detectable to a careful one. Three mechanisms create that tension:

─── A. Social correction (the correction exists but must be found) ─────────
  A forum post or recent blog embeds the correct info in lived experience.
  The agent must retrieve and actually read it — not just skim the headline.
  Write it as a natural aside, not an announcement:
    RIGHT: "Went last Tuesday — just so you know, they've changed their hours.
            Closes at 21:00 now, not 20:00 like it still says on Yelp."
    WRONG: "CORRECTION: The Yelp hours are out of date."

─── B. Implausibility (the agent should feel something is off) ──────────────
  The wrong value contradicts how venues of this type normally operate, or
  contradicts other things stated in the same or nearby documents.
  Implausibility should be layered in as a secondary signal so the agent has
  reason to look harder before it finds the social correction.

  Operational impossibility — claims that contradict how this venue type works:
    - A Michelin-starred restaurant: "walk-ins always welcome, no booking needed"
    - A small museum with one curator: "open daily 07:00-23:00"
    - A basement jazz bar: "great for families, brought my 10-year-old nephew
      and the staff were lovely about it" — plausible as a single experience,
      but contradicts the venue's age restriction in ground truth. Agent should
      cross-check rather than trust one visit.

  Regulatory / cultural impossibility — contradicts known norms for the city:
    - Central London venue: "free parking right outside"
    - A licensed bar in the UK: "all ages welcome, no ID checks ever"
    - A venue in a residential zone: "live music until 4am, never any complaints"
    These are most effective in blog/forum prose, reported as personal experience.
    The author is not lying — they observed something real (the nephew was let in
    once, the staff didn't card that night) and generalised incorrectly.

  Self-contradiction within or across docs — no world knowledge needed:
    - Same doc: "quiet and intimate" but "hosts DJ nights Thursday–Sunday"
    - Same doc: "strictly vegan kitchen" but "our famous duck confit is a must"
    - Across docs: Yelp says closes 18:00 Friday; a blog describes arriving at
      18:30 on a Friday and being seated. Both retrieved, contradiction visible.

─── C. Documentary age and neglect signals ──────────────────────────────────
  The source itself signals unreliability through how it looks and what it
  references. Use these in doc body text and publish date metadata.

  For Category 1 (temporal decay) — the source looks old:
    - Publish date 3–8+ years ago
    - References things that no longer exist: "their original Shoreditch
      location", "the old owner used to do live music on Fridays"
    - Broken link in passing: "see their full menu at [URL]" where the URL
      looks obviously dead or redirects to a generic page
    - Style markers: "check out their Facebook page for updates" (outdated
      as a primary channel recommendation)

  For Category 3 (conditional / seasonal) — no awareness of the condition:
    States the wrong value as universal with zero caveat. The author visited
    in summer and never knew winter hours were different:
      RIGHT: "Open daily 10:00-18:00, great for a weekend visit."
      WRONG: "Open daily 10:00-18:00 year-round (though hours may vary)."
    The hedge makes the wrong info less of a trap. Remove all hedging.

  For multi-error neglected venues — signals compound into a picture:
    Each wrong info entry is plausible on its own. Together they signal a
    venue that has stopped maintaining its online presence:
    - Forum thread where top replies are years old; newer replies just ask
      "is this place still open?" with no authoritative answer
    - Official site footer says "© 2009" or shows placeholder image text
    - Yelp listing last activity years ago, photos look dated
    - Blog references something that has clearly changed ("the chef who
      founded this place" — implies continuity that may no longer exist)

  For Category 5 (adversarial) — reads promotional, not experiential:
    - No author name, no visit date, no specific detail about the experience
    - Only superlatives, nothing negative ("the absolute best in London!")
    - Claims serve venue interest, not traveller interest
    - Generic phrasing that could apply to any venue of this category

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INCORRECT SOURCE vs TRUTH CARRIER — writing style
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Incorrect source: NO awareness the info might be wrong. Casual, factual tone.
  WRONG: "According to Yelp they close at 20:00 (but worth checking)."
  RIGHT: "Great spot — just make sure to arrive before 8pm when they close."

Truth carrier: correction embedded naturally in lived experience.
  Cross-referencing another source is fine. Pointing out the discrepancy is fine.
  Keep it to one or two sentences — the author has no need to prove themselves
  right or explain the backstory. Prefer subtle signals where possible (e.g. a
  recent post date speaks for itself without stating "as of last month").

  WRONG (over-elaborates, explains origin, proves themselves right):
    "Important note: I paid £20, which seems lower than what I'd read online.
     When I mentioned this to staff, they said they reduced prices in late 2023
     to encourage visitors post-pandemic. Some older blogs still list it at £28,
     but it's definitely £20 now."

  RIGHT (brief cross-reference, no backstory):
    "Entry was £20 when I visited — some older posts have it higher but it's
     dropped since."

  RIGHT (direct, no cross-reference needed):
    "Went last Tuesday — great dinner. They close at 21:00 so plan accordingly."

  RIGHT (Yelp cross-reference, one sentence):
    "They close at 21:00 now; Yelp still shows 20:00."

Likes, recency, and source credibility:
  Believable source  → high likes (50+), recent date, first-person experience,
                       specific details — agent has strong reason to trust it
  Doubting source    → low likes (3–10), older date, no visit date stated,
                       vague or second-hand — agent should weight it less
  Incorrect source   → likes can be high if the post is old and was once
                       popular; high likes on an old post reflects past
                       reputation, not current reliability — a deliberate trap

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGISTERING WRONG INFO IN THE SYSTEM (P6-T22)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ADD_WRONG_INFO is now ATOMIC. It creates the wrong_info entry AND registers
both required source-doc roles (incorrect_source + truth_carrier) in ONE call.
You do NOT need to call REGISTER_DOC_REFS for the wrong-info roles — pass the
two doc_ids directly to ADD_WRONG_INFO.

Steps (in order):
  1. CREATE_PAGE + FILL + COMMIT for the INCORRECT SOURCE doc.
     Body embeds the wrong value with NO AWARENESS it might be wrong.
  2. CREATE_PAGE + FILL + COMMIT for the TRUTH CARRIER doc.
     Body embeds the correction in 1-2 sentences of natural experience prose.
  3. (If source_type="yelp") FILL the yelp_listing's yelp_<field> with the
     incorrect value so it surfaces in search_yelp output.
  4. ADD_WRONG_INFO(venue_id, affected_field, incorrect_value, correct_value,
                   source_type, wrong_info_category, origin_story,
                   incorrect_source_doc_id=<step 1 doc>,
                   truth_carrier_doc_id=<step 2 doc>)
     — atomic. Validates both docs exist + are committed + are distinct,
     then writes wrong_info + both doc_venue_roles rows in a single
     transaction. wrong_info_id is returned for reference.

Both incorrect_source and truth_carrier are mandatory — a wrong_info entry
with no incorrect source doc is untestable (no agent can encounter the wrong
value), and one with no truth carrier means the planning agent has no path
to the correction.

Multi-wrong-info venues: call ADD_WRONG_INFO once per entry, each with its
OWN pair of doc_ids. A single doc can't be the truth_carrier (or
incorrect_source) for two different wrong_info entries on the same venue —
the schema's (doc_id, venue_id) PK only allows one role per doc per venue.
""",

"traffic_tier": """TRAFFIC TIER — Venue fame and information ecosystem

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HIGH — Famous, well-documented venue
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Examples: iconic museum, well-known restaurant, popular tourist attraction.
total_results: 1000–5000 (for blogs/forums search)
Source docs to generate: 4–6 (multiple blog mentions, forum threads, official site likely)
Information ecosystem: Dense. Errors get corrected quickly. Wrong info is
unlikely to persist — someone will have posted a correction within weeks.
Wrong info guidance: Use sparingly. If you add wrong info, a prominent
truth_carrier doc with high likes (100+) should exist.
Yelp popularity score: 0.7–1.0

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MID — Well-regarded but not famous
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Examples: popular neighbourhood restaurant, mid-size gallery, local park.
total_results: 100–500
Source docs to generate: 2–4 (one or two blog mentions, a forum thread or two)
Information ecosystem: Moderate. Corrections exist but require searching.
Wrong info guidance: Reasonable to add. Truth carrier should exist but may
have lower engagement (20–80 likes).
Yelp popularity score: 0.4–0.7

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOW — Hidden gem, sparse online presence
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Examples: neighbourhood cafe, locals-only bar, quiet garden.
total_results: 4–30
Source docs to generate: 1–2 (Yelp listing + maybe one blog mention)
Information ecosystem: Thin. Errors may have NO correction anywhere.
Wrong info guidance: Most natural fit for wrong info — owner neglect is
plausible, and the planning agent may find no correction at all. If no
truth_carrier exists, the correct behaviour is for the agent to flag
uncertainty rather than trust the only source.
Yelp popularity score: 0.1–0.4

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EFFECT ON BENCHMARK DIFFICULTY:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
High-traffic venues are easier for the planning agent to schedule correctly —
information is abundant and reliable. Low-traffic venues are harder because
the agent must either verify aggressively or flag uncertainty.
A task asking for "hidden gems" will use low-traffic venues — the benchmark
rewards agents that correctly flag uncertainty on poorly-documented venues.
""",

"tags": """TAGS — Canonical vocabulary, limits, format

TAG FORMAT — mandatory:
  Hyphenated kebab-case ONLY: free-entry, dog-friendly, wheelchair-accessible.
  NEVER underscores (free_entry) or spaces (free entry).
  SET_TAGS canonicalises on write, but use hyphens to avoid confusion.

VOCABULARY (you MUST pick from this list — SET_TAGS hard-rejects off-vocab):

""" + format_vocab_by_axis() + """

The city-specific extension (if any) is shown in your assignment block.

LIMITS:
  Per-venue: max 15 tags hard limit; soft warning at 12+.
  No "new tag" mechanism — every tag must already be in the combined vocab
  (universal core + cuisines + city extension). If you think a concept is
  missing from the vocab, that's a separate (rare) decision; SET_TAGS will
  reject and surface "did you mean" suggestions.

coverage_tags (assigned by planner — MUST include):
  Your assignment block lists coverage_tags if the pool-level planner assigned
  benchmark-critical tags to this venue. All coverage_tags are on-vocab by
  construction (they are explicit subsets of the universal core). Add them
  first as yelp_visible=1.

  Coverage tags used by the benchmark for Type 5 constraint tasks:
    halal, dog-friendly, wheelchair-accessible, free-entry, live-music,
    family-friendly, outdoor, vegetarian-options

yelp_visible rule:
  Set yelp_visible=1 for tags the owner would highlight on Yelp:
  selling points, eye-catching features, most searched-for attributes.
  Set yelp_visible=0 for tags that are true but not prominently advertised:
  hidden-gem, popular-with-locals, quiet (owner wouldn't market themselves as quiet).

District names (shoreditch, mayfair, soho...) belong in the district field, not tags.
""",

"hours": """HOURS FORMAT

All hour columns use the string format: "HH:MM-HH:MM"
  Closed day: null
  Normal day: "12:00-22:30"
  Split service (lunch + dinner): "12:00-15:00,19:00-23:00"
  Late night crossing midnight: "20:00-02:00" (close time is next day)

Applies to: venues.hours_*, yelp_listings.yelp_hours_*, official_site_docs.hours_*

For hours_overrides:
  override_type "closed" → hours column is null
  override_type "modified" → hours column has the modified hours string
""",
}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

def query_handbook(query: str) -> str:
    """
    Main entry point for the HELP tool.
    Takes a -h style query string and returns documentation text.
    """
    q = query.strip()

    # Normalise: remove leading -h if present
    if q.startswith("-h"):
        q = q[2:].strip()

    parts = q.split(None, 1)  # split into at most 2 parts

    if not parts:
        return _unknown(query)

    section = parts[0].lower()
    name = parts[1].strip() if len(parts) > 1 else None

    # -h function or -h function <name>
    if section == "function":
        if name is None:
            return FUNCTION_INDEX
        upper_name = name.upper()
        if upper_name in FUNCTION_SPECS:
            return FUNCTION_SPECS[upper_name]
        return f"Unknown function '{name}'. Query '-h function' to see all available tools."

    # -h format or -h format <name>
    if section == "format":
        if name is None:
            return FORMAT_INDEX
        lower_name = name.lower()
        if lower_name in FORMAT_SPECS:
            return FORMAT_SPECS[lower_name]
        return f"Unknown format '{name}'. Query '-h format' to see all available formats."

    return _unknown(query)


def _unknown(query: str) -> str:
    return (
        f"Unknown query: '{query}'\n\n"
        "Valid queries:\n"
        "  -h function              List all tools\n"
        "  -h function <name>       Full spec for one tool\n"
        "  -h format                List all formats\n"
        "  -h format <name>         Schema + example for one format\n"
    )


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(query_handbook(" ".join(sys.argv[1:])))
    else:
        print(query_handbook("-h function"))