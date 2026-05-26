# P15 — Venue generation quality improvements

**Status: 📝 design drafted, not blocking**
**Blocks: nothing, but its absence makes P13's validation pipeline over-reject tasks**
**Depends on: nothing**

---

## Context

P13's validation pipeline exposes real problems with the current pool. The 50/50/25 upper bars reject many filters that would be legitimate except for pool-composition bias. The validator is right to reject (we don't want cosmetic constraints masquerading as meaningful ones), but the underlying problem is the pool.

This subtask collects venue-generation improvements that are adjacent to but separable from P2 (low-tier/bar/regulation coverage). P2 is about hitting target distributions; P15 is about the richer pool-quality issues surfaced during Phase 4 analysis.

## The problems, backed by real data

Running analysis on `data/cities/london/runs/test_50/travelbench.db` (50 venues):

### Problem 1: Tag-repetition deficit (the one you asked about)

**374 unique tags across 50 venues. 287 of them (76%) appear on exactly ONE venue.** Only 40 tags (10%) appear on 3+ venues.

Top 15 most-used tags peak at just 8 venues each:
```
british           8 venues
cocktails         8 venues
free_entry        8 venues
dinner            7 venues
outdoor           7 venues
coffee            6 venues
educational       5 venues
historic          5 venues
landmark          5 venues
michelin          5 venues
```

Average 11.2 tags/venue, but tags are highly individual — `jollof_bowls`, `black-dal`, `fish_sauce_wings`, `pod_toilets`, `converted_toilet` each appear on one venue.

**Impact:** 
- `label_required` filters are nearly useless as inclusion criteria. If an agent writes `label_required: "jollof_bowls"`, the inclusion_pool is exactly 1 venue. The constraint is either unsolvable (lower bar fails) or forces exactly that specific venue (defeats the "benchmark multiple agent strategies" purpose).
- Creates lots of "false variety" — the pool LOOKS tag-rich (374 tags) but actual filterability is thin (40 reusable tags).
- Tag diversity saturates early: venue 50 adds the same high-level tag categories as venue 20.

### Problem 2: Common-regulation over-coverage (from P13 analysis)

(Already tracked in P2, but connecting it here for completeness)

```
wheelchair_accessible   92% FOOD, 100% SITE
photography_allowed    100% FOOD,  96% SITE
family_friendly         60% FOOD,  92% SITE
```

Three core regulations are essentially "everyone." No filter discrimination possible.

### Problem 3: Tag-category cross-usability is low

Of 374 tags:
- 29 (7%) appear on both food and site venues (genuinely cross-category)
- 161 (43%) appear on food venues only
- 184 (49%) appear on site venues only

Food and site venues barely share tag vocabulary. This is probably correct semantically (a restaurant and a park naturally differ), but it means tags like `hidden-gem` or `family_friendly` that SHOULD be universal often aren't actually used that way in the pool.

### Problem 4: Specific hot-tag overuse / identification tags

Some tags are location-specific identifiers rather than filterable properties:
```
shoreditch      5 venues  (district label, not a filter)
covent-garden   1 venue   (same)
mayfair         1 venue
soho            3 venues
```

These should be in `district` field, not `tags`. They pollute the tag index and can cause confused filter behavior if an agent writes `label_required: shoreditch`.

## Proposed fixes

### Fix 1: Inject existing city tags into the venue assignment block

The handbook currently says: *"If unsure whether a tag exists already, the orchestrator
can query: SELECT DISTINCT tag FROM tags WHERE city='...' ORDER BY tag"* — passive
guidance the agent rarely acts on.

**Change:** inject the full current city tag list directly into the venue's assignment
block (the brief passed to `_build_assignment` in `generate_venue.py`), so the agent
sees it before writing any tags:

```
EXISTING CITY TAGS — use these before creating new ones:
  artisan, botanical, brunch, casual, cocktails, coffee, craft-beer,
  date-night, dinner, family-friendly, free-entry, halal, hidden-gem,
  historic, iconic, indoor, instagrammable, intimate, landmark, ...
  [full list from SELECT DISTINCT tag FROM tags WHERE city=? ORDER BY tag]
Prefer reusing a tag from this list when the meaning fits your venue.
Only create a new tag if none of the existing ones accurately describes
a genuinely distinctive property of this venue.
```

Location: `generate_venue.py::_build_assignment` — query the DB at brief-build time and
append to the assignment string. Cost: one cheap DB read per venue (not per tag check).

### Fix 2: Hard limit on new tags per venue in SET_TAGS

In `agent_tools.py::tool_SET_TAGS`, after inserting, check how many tags are new to
the city's tag pool (didn't exist before this call):

```python
MAX_NEW_TAGS_PER_VENUE = 3   # configurable

existing_city_tags = {row["tag"] for row in conn.execute(
    "SELECT DISTINCT tag FROM tags WHERE city=? AND venue_id != ?",
    (city, venue_id)
)}
new_tags = [t["tag"] for t in inserted if t["tag"] not in existing_city_tags]

if len(new_tags) > MAX_NEW_TAGS_PER_VENUE:
    # Soft warning — don't hard-fail. Agent must acknowledge or revise.
    return {
        "status": "warning",
        "message": (
            f"You created {len(new_tags)} new tags not seen elsewhere in this city: "
            f"{new_tags}. The city tag limit per venue is {MAX_NEW_TAGS_PER_VENUE} new tags. "
            f"Review the existing city tags in your assignment block and reuse where possible. "
            f"Then either call SET_TAGS again with fewer new tags, "
            f"or call CONFIRM_TAGS(venue_id, confirmed_new_tags=[...]) to acknowledge "
            f"these are genuinely distinct concepts."
        ),
        "new_tags": new_tags,
        "existing_reused": [t["tag"] for t in inserted if t["tag"] in existing_city_tags],
    }
```

`CONFIRM_TAGS` is a new lightweight tool that records the agent's acknowledgement and
proceeds (no DB write — just changes the soft-warning to a pass):

```python
def tool_CONFIRM_TAGS(venue_id, confirmed_new_tags, **kwargs):
    """Agent confirms these new tags are genuinely distinct. Clears the warning."""
    return {"status": "ok", "confirmed": confirmed_new_tags,
            "message": f"New tags confirmed for {venue_id}. Proceed to VERIFY."}
```

### Fix 3: Semantic duplicate detection in SET_TAGS

When a tag is new to the city pool, check whether any existing tag is semantically
similar before accepting it. Two approaches — use both:

**3a. Character trigram similarity (free, no API call):**
```python
def _trigrams(s):
    s = s.lower().replace("-","").replace("_","")
    return {s[i:i+3] for i in range(len(s)-2)}

def _trigram_sim(a, b):
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb: return 0.0
    return len(ta & tb) / len(ta | tb)

SIMILARITY_THRESHOLD = 0.5   # catches converted_toilet vs converted_lavatory

for new_tag in new_tags:
    for existing in existing_city_tags:
        sim = _trigram_sim(new_tag, existing)
        if sim >= SIMILARITY_THRESHOLD:
            # Add to warning: suggest the existing tag
            similar_suggestions.append((new_tag, existing, sim))
```

**3b. Semantic embedding match (optional, one API call per new tag):**
If trigram similarity is below threshold but the new tag is longer than 10 chars
(compound concept like `east_african_coffee`), optionally call the embedding API.
Gate this behind a flag `use_semantic_dedup=True` — can be disabled for dry-runs
or when API key isn't available. Return the suggestion as a warning alongside the
trigram suggestion.

Warning message example:
```
"Tag 'east_african_coffee' may duplicate 'coffee' (trigram sim=0.4) or 'african'
(trigram sim=0.3). If this is a genuinely distinct concept, call CONFIRM_TAGS.
Otherwise, use one of the existing tags."
```

### Fix 4: City-level tag cap

Hard limit on total distinct tags per city. Configurable per city in city config.
Default: 100 tags for a 50-venue pool (2 tags/venue average new tags is reasonable).

```python
CITY_TAG_CAP = city_config.get("tag_cap", 100)

current_distinct = conn.execute(
    "SELECT COUNT(DISTINCT tag) FROM tags WHERE city=?", (city,)
).fetchone()[0]

truly_new = [t for t in new_tags
             if t not in existing_city_tags]
if current_distinct + len(truly_new) > CITY_TAG_CAP:
    return {
        "status": "error",
        "message": (
            f"City tag cap reached ({CITY_TAG_CAP} distinct tags). "
            f"You must reuse existing tags. The {len(truly_new)} new tags you tried "
            f"to create ({truly_new}) cannot be added. "
            f"Review the existing tag list in your assignment block and use the closest match."
        )
    }
```

This is a **hard error** (not soft warning) — once the city has 100 tags, no new
ones are allowed. Forces reuse. The cap is generous enough for 50 venues but tight
enough to prevent tag explosion.

### Fix 5: Per-venue tag count limit

Soft warning at 8 tags, hard cap at 15. Too many tags dilutes the signal and
indicates the agent is describing the venue in prose via tags.

```python
if len(inserted) > 15:
    return {"status": "error",
            "message": f"Too many tags ({len(inserted)}). Maximum 15 per venue. "
                       f"Tags should be filterable properties, not descriptive prose."}
if len(inserted) > 8:
    # soft warning but still succeeds
    result["warning"] = f"High tag count ({len(inserted)}). Consider pruning to 6-8 core tags."
```

### Fix 6: Handbook update

Update the `"tags"` section in `handbook.py`:

- Remove "Tags are free-form. There is no fixed allowed list."
- Add: tag limit per venue (max 15, soft warning at 8)
- Add: city tag cap (100 for London — agents will be told the current count in their
  assignment block)
- Add: new-tag limit (max 3 per venue) and CONFIRM_TAGS escape hatch
- Update the "Querying existing tags" section to say tags are injected into the
  assignment block automatically — no need to query manually
- Add: canonical form rule (hyphens only: `free-entry`, not `free_entry` or `free entry`)

### Fix 7: Location tag detection and migration

Scan the tag list at generate-time; if any tag matches a district name in `city_config["districts"]`,
warn the agent: *"Tag 'shoreditch' is a district name — use the district field, not tags."*
Add to `_check_label_subset` in VERIFY.

For existing pools: one-time migration script that moves district-name tags to a
comment/note and removes them from the tags table.

## Implementation scope

- `generate_venue.py::_build_assignment` — inject city tag list (~1h)
- `agent_tools.py::tool_SET_TAGS` — new-tag warning, city cap, per-venue limit (~2h)
- `agent_tools.py` — add `tool_CONFIRM_TAGS` (~0.5h)
- `agent_tools.py` — trigram similarity helper + warning (~1h)
- `agent_tools.py::_check_label_subset` in VERIFY — district tag detection (~0.5h)
- `scripts/generation/handbook.py` — update tags section (~1h)
- Analysis script: `scripts/analysis/venue_diversity_score.py` — tag-reuse metrics (~1h)
- Migration: one-time script for existing pools, district-tag removal (~1h)
- Test: generate 50-venue pool, verify ≥30% tags reused, ≤100 distinct city tags (~1h)

**Total:** ~9h

## Status

📝 design updated. Implementation pending P1 completion (canonical form at write-time
is a prerequisite — SET_TAGS needs to normalise before checking city tag pool).
