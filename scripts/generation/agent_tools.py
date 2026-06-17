"""
scripts/generation/agent_tools.py

All 8 tools available to the auto-generation agent.
Each tool returns a dict that the agent sees as its tool result.

Tools:
  HELP(query)                     → handbook lookup
  CREATE_PAGE(entity_type, ...)   → create empty DB row, return required fields
  FILL(page_id, fields)           → batch update fields
  COMMIT(page_id)                 → completeness check + lock page
  VERIFY(target)                  → consistency checks
  GET_STATUS(venue_id)            → page state summary
  THINK(thought)                → log correction plan
  SUBMIT(venue_id)                → mark venue complete
"""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import (
    get_connection, new_page_id, new_doc_id, new_venue_id,
    get_null_fields, REQUIRED_FIELDS, DB_PATH
)
from scripts.generation.handbook import query_handbook


# ─────────────────────────────────────────────────────────────────────────────
# P6-T14 — Forum doc shape helpers
# ─────────────────────────────────────────────────────────────────────────────
# Used by tool_COMMIT to verify that doc_type='forum' bodies contain real
# Q&A structure (≥2 distinct speakers, ≥2 turn markers). The audit on London
# test_70 found 66/82 forum docs were single-voice monologues — this catches
# that pattern at COMMIT time going forward.

_FORUM_HANDLE_PATTERNS = [
    # "Reply from <X>" — permissive; matches "Reply from User 'X' on YYYY-MM-DD:"
    # and "**Reply from <X>:**" markdown-bold variant.
    re.compile(
        r"^\s*(?:\*\*)?\s*Reply\s+from\s+(?:User\s+['\"]?)?([A-Za-z][\w.\-]{1,40})",
        re.IGNORECASE,
    ),
    # "<Handle>:" at start of line — handle must be plausible (see _looks_like_handle)
    # so we don't false-match "Note:", "Update:", "Thread title:" etc.
    re.compile(
        r"^\s*(?:\*\*)?([A-Za-z][\w.\-]{2,40})['\"]?\s*:"
    ),
]
_FORUM_EXCLUDE = {
    "note", "update", "edit", "edit2", "thread", "thread title", "title",
    "tldr", "tl;dr", "summary", "answer", "question", "reply", "from",
    "op", "post", "comment", "moderator", "mod", "warning", "tip",
}


def _looks_like_handle(s: str) -> bool:
    """A bare `<Handle>:` only counts as a turn marker if the handle looks
    like a username — contains underscore, digit, or mixed-case beyond the
    first letter. Excludes common English words used as field labels."""
    if not s:
        return False
    if s.lower() in _FORUM_EXCLUDE:
        return False
    if "_" in s or any(ch.isdigit() for ch in s):
        return True
    return any(ch.isupper() for ch in s[1:]) and any(ch.islower() for ch in s)


def _extract_forum_handles(body: str) -> list[str]:
    """Return all speaker-marker handles found in a forum body, in order.
    First pattern (`Reply from <X>`) trusts whatever follows; second pattern
    (`<Handle>:`) requires _looks_like_handle to pass."""
    hits: list[str] = []
    if not body:
        return hits
    for raw in body.splitlines():
        for idx, pat in enumerate(_FORUM_HANDLE_PATTERNS):
            m = pat.match(raw)
            if not m:
                continue
            cand = m.group(1)
            if idx == 0 or _looks_like_handle(cand):
                hits.append(cand)
                break
    return hits


# P6-T17 was originally planned as a trigram-overlap detective check on doc
# openers within the same venue. Threshold tuning on the 2026-05-20 audit
# pairs showed character-level similarity doesn't separate the audit's
# "shared narrative arc, different words" cases from genuinely diverse pairs
# (both score ~0.12-0.39 with no clean threshold). The root-cause fix in
# P6-T16 (declared persona+tone + within-venue persona uniqueness +
# cross-venue tone-spread visibility) handles the same problem preventively.
# T17 is deferred — if regenerated London data still shows arc overlap, a
# semantic / embedding-based check can be designed in Phase 7.


# ─────────────────────────────────────────────────────────────────────────────
# P6-T18 — Author-handle structural-signature soft cap
# ─────────────────────────────────────────────────────────────────────────────
# Audit on London test_70 found forum handles cluster heavily on one shape
# (lowercase `<word>_<word>`: hackney_jim, shoreditch_dan, eastlondon_eater,
# southlondon_foodie, ...). Author cap (T15) limits reuse of an exact handle
# but doesn't catch shape-level conformity. T18 adds a structural signature
# and caps any single signature at 40% of the city's committed/verified docs
# (with a 10-doc floor to avoid early-corpus thrashing).

_HANDLE_SIG_BINS = (
    "with_digit",
    "lower_snake",
    "mixed_snake",
    "multi_word",
    "mixed_case_compound",
    "single_lower",
    "other",
)


def _handle_signature(handle: str) -> str:
    """Classify an author handle into one of seven mutually-exclusive shape
    buckets. First matching rule wins (top-down)."""
    if not handle:
        return "other"
    h = handle.strip()
    if not h:
        return "other"
    if any(c.isdigit() for c in h):
        return "with_digit"
    has_underscore = "_" in h
    has_space = " " in h
    has_upper = any(c.isupper() for c in h)
    has_lower = any(c.islower() for c in h)
    if has_underscore and not has_upper and not has_space:
        return "lower_snake"
    if has_underscore and has_upper:
        return "mixed_snake"
    if has_space:
        return "multi_word"
    if has_upper and has_lower and not has_underscore:
        return "mixed_case_compound"
    if has_lower and not has_upper and not has_underscore and not has_space:
        return "single_lower"
    return "other"


# ─────────────────────────────────────────────────────────────────────────────
# P6-T19 — Body-length bounds + cross-corpus distribution
# ─────────────────────────────────────────────────────────────────────────────
# Audit on London test_70 found body lengths cluster suspiciously: blog
# medians 1100-1500 chars, forum medians 700-900, with several forum docs at
# identical lengths (952/950/948) across unrelated venues. Bounds at COMMIT
# catch truly out-of-range bodies; distribution surfaced at CREATE_PAGE lets
# the agent self-distribute across length brackets.

_BODY_LENGTH_BOUNDS = {
    "blog":  (400, 2500),
    "forum": (300, 2000),
}

# Length bins shared between blog and forum. The bin a doc lands in goes
# into city_body_length_counts surfaced at CREATE_PAGE.
_BODY_LENGTH_BINS = (
    ("short",     (0, 700)),
    ("medium",    (700, 1200)),
    ("long",      (1200, 2000)),
    ("very_long", (2000, 10_000)),
)


def _body_length_bin(n: int) -> str:
    """Return the bin label for a body length n (chars)."""
    for label, (lo, hi) in _BODY_LENGTH_BINS:
        if lo <= n < hi:
            return label
    return "very_long"


# ─────────────────────────────────────────────────────────────────────────────
# P6-T21 — Point-in-city-polygon check
# ─────────────────────────────────────────────────────────────────────────────
# Used at venue COMMIT to reject venues whose lat/lng falls outside the
# city's actual OSM boundary polygon. Fixes the NJ-instead-of-NY problem
# surfaced by the New_York test_50 audit. Polygon comes from
# city_config.boundary_geojson (populated by research_city.py).

def _point_in_ring(lat: float, lng: float, ring: list) -> bool:
    """Ray-casting point-in-polygon for a single linear ring.
    Ring is a list of [lng, lat] pairs (geojson convention).
    Returns True if the point is inside the ring."""
    if not ring or len(ring) < 3:
        return False
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        try:
            xi, yi = ring[i][0], ring[i][1]   # lng, lat
            xj, yj = ring[j][0], ring[j][1]
        except (TypeError, IndexError):
            j = i
            continue
        # Standard ray-casting: count edge crossings from (lat, lng) going east.
        if ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def _point_in_polygon(lat: float, lng: float, geojson: dict) -> bool:
    """Test if (lat, lng) is inside a geojson Polygon or MultiPolygon.
    Polygon: coordinates = [outer_ring, hole1, hole2, ...]
    MultiPolygon: coordinates = [polygon1, polygon2, ...]
    """
    if not geojson:
        return True   # no boundary → don't reject (legacy configs)
    coords = geojson.get("coordinates") or []
    typ = geojson.get("type")
    if typ == "Polygon":
        polys = [coords]
    elif typ == "MultiPolygon":
        polys = coords
    else:
        return True   # unknown type → don't reject
    for poly in polys:
        if not poly:
            continue
        # First ring is the outer boundary; subsequent rings are holes.
        outer = poly[0]
        if _point_in_ring(lat, lng, outer):
            # Inside outer ring — now check it's not in a hole.
            in_hole = False
            for hole in poly[1:]:
                if _point_in_ring(lat, lng, hole):
                    in_hole = True
                    break
            if not in_hole:
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# P6-T16 — Source-doc context payload for CREATE_PAGE
# ─────────────────────────────────────────────────────────────────────────────
def _source_doc_context(conn, city: str, venue_id: str | None) -> dict:
    """Build the persona/tone context block that goes into CREATE_PAGE's
    response when creating a source_doc.

    - personas_used_for_venue / personas_still_available: requires venue_id;
      empty/full when venue_id is None.
    - tones_used_for_venue: requires venue_id; empty when None.
    - city_tone_counts: always populated (per-city across all committed/verified
      docs); _invented bucket aggregates anything not in CANONICAL_TONES.
    - city_tone_target_per_bucket: soft target = ceil(expected_total / 11).
    """
    from scripts.generation.handbook import (
        CANONICAL_PERSONAS, CANONICAL_TONES, N_TONE_BUCKETS,
        _normalize_persona, _normalize_tone,
        _CANONICAL_PERSONAS_NORM, _CANONICAL_TONES_NORM,
    )

    out: dict = {}

    # Within-venue personas/tones already used (via doc_venue_refs)
    if venue_id:
        rows = conn.execute(
            """SELECT DISTINCT s.persona, s.tone FROM source_docs s
               JOIN doc_venue_refs r ON s.doc_id = r.doc_id
               WHERE r.venue_id = ?
                 AND s.page_status IN ('committed','verified')
                 AND (TRIM(s.persona) != '' OR TRIM(s.tone) != '')""",
            (venue_id,),
        ).fetchall()
        used_personas = sorted({
            r["persona"].strip() for r in rows if (r["persona"] or "").strip()
        })
        used_tones = sorted({
            r["tone"].strip() for r in rows if (r["tone"] or "").strip()
        })
        used_personas_norm = {_normalize_persona(p) for p in used_personas}
        still_available = sorted(
            p for p in CANONICAL_PERSONAS
            if _normalize_persona(p) not in used_personas_norm
        )
        out["personas_used_for_venue"] = used_personas
        out["tones_used_for_venue"] = used_tones
        out["personas_still_available"] = still_available

    # City-wide tone counts (no venue_id required)
    tone_counts: dict[str, int] = {t: 0 for t in CANONICAL_TONES}
    tone_counts["_invented"] = 0
    rows = conn.execute(
        """SELECT tone FROM source_docs
           WHERE city = ?
             AND page_status IN ('committed','verified')
             AND TRIM(tone) != ''""",
        (city,),
    ).fetchall()
    total_with_tone = 0
    for r in rows:
        norm = _normalize_tone(r["tone"])
        if not norm:
            continue
        total_with_tone += 1
        # Map back to the canonical surface form (not the normalized form)
        canonical_hit = next(
            (t for t in CANONICAL_TONES if _normalize_tone(t) == norm), None
        )
        if canonical_hit is not None:
            tone_counts[canonical_hit] += 1
        else:
            tone_counts["_invented"] += 1
    out["city_tone_counts"] = tone_counts

    # Soft target = ceil(expected_total / N_TONE_BUCKETS), where expected_total
    # is derived from current venue count × 3.2 (avg docs/venue across tiers).
    n_venues = conn.execute(
        "SELECT COUNT(*) FROM venues WHERE city = ? "
        "AND page_status IN ('committed','verified','draft')",
        (city,),
    ).fetchone()[0]
    expected_total = int(n_venues * 3.2) if n_venues > 0 else 0
    out["city_tone_target_per_bucket"] = max(
        1, (expected_total + N_TONE_BUCKETS - 1) // N_TONE_BUCKETS
    ) if expected_total > 0 else 0
    out["city_total_docs_so_far"] = total_with_tone

    # P6-T19: body-length distribution per doc_type, binned as
    # short / medium / long / very_long. The agent should pick a length that
    # fills underused brackets to keep the corpus naturally varied.
    body_rows = conn.execute(
        """SELECT doc_type, LENGTH(body) AS n FROM source_docs
           WHERE city = ?
             AND page_status IN ('committed','verified')
             AND body IS NOT NULL
             AND TRIM(body) != ''""",
        (city,),
    ).fetchall()
    body_dist: dict[str, dict[str, int]] = {
        "blog":  {label: 0 for label, _ in _BODY_LENGTH_BINS},
        "forum": {label: 0 for label, _ in _BODY_LENGTH_BINS},
    }
    for r in body_rows:
        dt = (r["doc_type"] or "").strip().lower()
        if dt in body_dist:
            body_dist[dt][_body_length_bin(r["n"])] += 1
    out["city_body_length_counts"] = body_dist
    out["body_length_bounds"] = {
        dt: {"min": lo, "max": hi}
        for dt, (lo, hi) in _BODY_LENGTH_BOUNDS.items()
    }

    return out

# ─────────────────────────────────────────────────────────────────────────────
# TABLE + COLUMN METADATA
# ─────────────────────────────────────────────────────────────────────────────

ENTITY_TABLE = {
    "venue":            ("venues",            "venue_id"),
    "yelp_listing":     ("yelp_listings",     "venue_id"),
    "source_doc":       ("source_docs",       "doc_id"),
    "official_site_doc":("official_site_docs","venue_id"),
}

# Fields that must not be null on COMMIT (entity-specific)
REQUIRED_ON_COMMIT = {
    "venue": [
        "name", "category", "district", "lat", "lng",
        "avg_cost_local", "price_tier", "recommended_visit_minutes",
        "booking_required", "has_official_site", "outdoor_sensitivity",
        "recommended_pace", "traffic_tier", "total_results",
        "yelp_popularity_score", "pet_friendly", "wheelchair_accessible",
        "parking_nearby", "photography_allowed", "noise_level",
        "reservation_required", "outside_food_allowed", "family_friendly",
        "food_available",
    ],
    "yelp_listing": [
        "name", "category", "district", "stars",
        "review_count", "yelp_popularity_score",
    ],
    "source_doc": [
        "doc_type", "title", "author", "source_name", "date", "body",
        "persona", "tone",   # P6-T16
    ],
    "official_site_doc": [
        "url", "retrieved_date", "body",
    ],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_page(conn: sqlite3.Connection, page_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM draft_pages WHERE page_id = ?", (page_id,)
    ).fetchone()


def _get_record(conn: sqlite3.Connection, entity_type: str, record_id: str) -> sqlite3.Row | None:
    table, id_col = ENTITY_TABLE[entity_type]
    return conn.execute(
        f"SELECT * FROM {table} WHERE {id_col} = ?", (record_id,)
    ).fetchone()


def _record_id_for_page(conn: sqlite3.Connection, page_id: str) -> str | None:
    """Return the primary key value (venue_id or doc_id) for a page."""
    page = _get_page(conn, page_id)
    if page is None:
        return None
    entity_type = page["entity_type"]
    _, id_col = ENTITY_TABLE[entity_type]
    # For source_docs the id is stored separately since it's not venue_id
    if entity_type == "source_doc":
        # doc_id is stored in draft_pages as a separate column if needed
        # We store the record_id in venue_id column for simplicity (nullable)
        return page["venue_id"]  # reused as record_id for source_docs
    return page["venue_id"]


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: HELP
# ─────────────────────────────────────────────────────────────────────────────

def tool_HELP(query: str, **kwargs) -> dict:
    """Query the handbook."""
    result = query_handbook(query)
    return {"status": "ok", "content": result}


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: CREATE_PAGE
# ─────────────────────────────────────────────────────────────────────────────

def tool_CREATE_PAGE(entity_type: str, venue_id: str = None,
                     db_path: Path = DB_PATH, **kwargs) -> dict:
    """Create an empty DB row and return the page_id + record_id + required fields."""
    if entity_type not in ENTITY_TABLE:
        return {
            "status": "error",
            "message": f"Unknown entity_type '{entity_type}'. "
                       f"Valid: {list(ENTITY_TABLE.keys())}"
        }

    city = kwargs.get("city", "")
    if not city:
        return {
            "status": "error",
            "message": "city is required but was not provided. This is injected automatically — if you see this error something is wrong with the runner."
        }

    conn = get_connection(db_path)

    # Verify city exists in DB
    city_row = conn.execute("SELECT 1 FROM city_config WHERE city = ?", (city,)).fetchone()
    if city_row is None:
        conn.close()
        return {
            "status": "error",
            "message": f"City '{city}' not found in city_config. "
                       f"Run research_city first."
        }

    table, id_col = ENTITY_TABLE[entity_type]
    page_id = new_page_id()

    try:
        if entity_type == "venue":
            # Always auto-generate venue_id — agent should not specify it
            record_id = new_venue_id()
            conn.execute(
                f"INSERT INTO {table} ({id_col}, city, page_status) VALUES (?, ?, ?)",
                (record_id, city, "draft")
            )

        elif entity_type == "yelp_listing":
            if not venue_id:
                conn.close()
                return {"status": "error",
                        "message": "venue_id required for yelp_listing. "
                                   "Use the venue_id returned by CREATE_PAGE for the venue."}
            record_id = venue_id
            if not conn.execute("SELECT 1 FROM venues WHERE venue_id = ?",
                                (venue_id,)).fetchone():
                conn.close()
                return {"status": "error",
                        "message": f"Venue '{venue_id}' not found. "
                                   "Create and COMMIT the venue page first, then create the yelp_listing."}
            conn.execute(
                f"INSERT OR IGNORE INTO {table} ({id_col}, city, name, category, "
                f"district, stars, review_count, yelp_popularity_score, page_status) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record_id, city, "", "", "", 0.0, 0, 0.0, "draft")
            )

        elif entity_type == "source_doc":
            # P6-T20 Fix #1: venue_id is required for source_doc so the
            # within-venue persona uniqueness check (T16) has data at COMMIT
            # time. The agent passes the venue_id of the doc's PRIMARY venue;
            # additional venue refs can still be added post-COMMIT via
            # REGISTER_DOC_REFS.
            if not venue_id:
                conn.close()
                return {"status": "error",
                        "message": "venue_id required for source_doc. Pass the "
                                   "venue_id of the doc's primary venue (the one "
                                   "this doc is mainly about) — additional venues "
                                   "can still be linked post-COMMIT via "
                                   "REGISTER_DOC_REFS."}
            if not conn.execute("SELECT 1 FROM venues WHERE venue_id = ?",
                                (venue_id,)).fetchone():
                conn.close()
                return {"status": "error",
                        "message": f"Venue '{venue_id}' not found. Create and "
                                   f"COMMIT the venue page first, then create "
                                   f"its source_docs."}
            record_id = new_doc_id()
            conn.execute(
                f"INSERT INTO {table} ({id_col}, city, doc_type, title, author, "
                f"source_name, date, body, page_status) VALUES (?,?,?,?,?,?,?,?,?)",
                (record_id, city, "", "", "", "", "", "", "draft")
            )
            # P6-T20 Fix #1: pre-populate doc_venue_refs so T16's within-venue
            # persona check at COMMIT sees the doc's primary venue.
            conn.execute(
                "INSERT INTO doc_venue_refs (doc_id, venue_id) VALUES (?, ?)",
                (record_id, venue_id),
            )

        elif entity_type == "official_site_doc":
            if not venue_id:
                conn.close()
                return {"status": "error",
                        "message": "venue_id required for official_site_doc. "
                                   "Use the venue_id returned by CREATE_PAGE for the venue."}
            record_id = venue_id
            if not conn.execute("SELECT 1 FROM venues WHERE venue_id = ?",
                                (venue_id,)).fetchone():
                conn.close()
                return {"status": "error",
                        "message": f"Venue '{venue_id}' not found. "
                                   "Create and COMMIT the venue page first."}
            conn.execute(
                f"INSERT OR IGNORE INTO {table} ({id_col}, city, url, retrieved_date, "
                f"body, page_status) VALUES (?,?,?,?,?,?)",
                (record_id, city, "", "", "", "draft")
            )

        # Register in draft_pages
        conn.execute(
            "INSERT INTO draft_pages (page_id, venue_id, entity_type, page_status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (page_id, record_id, entity_type, "draft", _now())
        )
        conn.commit()

        # Return required fields with current values
        record = _get_record(conn, entity_type, record_id)
        required = REQUIRED_ON_COMMIT.get(entity_type, [])
        required_state = {f: dict(record)[f] if record and f in dict(record).keys() else None
                         for f in required}

        response = {
            "status": "created",
            "page_id": page_id,
            "entity_type": entity_type,
            "record_id": record_id,
            "next_step": f"Call FILL('{page_id}', {{field: value, ...}}) to fill in the fields. "
                         f"Use '{record_id}' as the venue_id for all subsequent pages (yelp_listing, source_doc, official_site_doc).",
            "required_fields": required_state,
        }

        # P6-T16: surface persona+tone context for source_doc creation so the
        # agent can pick fresh / underused values instead of guessing.
        if entity_type == "source_doc":
            response.update(_source_doc_context(conn, city, venue_id))

        return response

    except sqlite3.IntegrityError as e:
        conn.rollback()
        conn.close()
        return {"status": "error", "message": f"Database integrity error: {e}. "
                "Check that all foreign key references exist (city, venue_id)."}
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: FILL
# ─────────────────────────────────────────────────────────────────────────────

def tool_FILL(page_id: str, fields: dict, db_path: Path = DB_PATH, **kwargs) -> dict:
    """Batch-fill fields on a page. Supports override."""
    if not fields:
        return {"status": "error", "message": "fields dict cannot be empty"}

    conn = get_connection(db_path)
    page = _get_page(conn, page_id)
    if page is None:
        conn.close()
        return {"status": "error", "message": f"Page '{page_id}' not found."}

    entity_type = page["entity_type"]
    record_id = page["venue_id"]
    table, id_col = ENTITY_TABLE[entity_type]

    # Get valid columns for this table
    valid_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    # Filter out protected columns
    protected = {id_col, "page_status"}
    unknown_fields = [f for f in fields if f not in valid_cols or f in protected]
    if unknown_fields:
        conn.close()
        return {
            "status": "error",
            "message": f"Unknown or protected fields: {unknown_fields}. "
                       f"Query '-h format {entity_type}' for valid field names."
        }

    # Validate enum fields
    ENUM_CONSTRAINTS = {
        "recommended_pace":    {"relaxed", "moderate", "intense"},
        "outdoor_sensitivity": {"indoor", "outdoor"},
        "traffic_tier":        {"high", "mid", "low"},
        "price_tier":          {"budget", "mid", "upscale", "fine-dining"},
        "noise_level":         {"quiet", "moderate", "loud"},
        "doc_type":            {"blog", "forum"},
        "role":                {"neutral", "incorrect_source", "truth_carrier"},
        "wrong_info_category": {"temporal_decay", "propagation_error", "conditional", "subjective"},
        "source_type":         {"yelp", "blog", "forum"},
        "override_type":       {"closed", "modified"},
    }
    enum_errors = []
    for field, value in fields.items():
        if field in ENUM_CONSTRAINTS and value is not None:
            if str(value) not in ENUM_CONSTRAINTS[field]:
                enum_errors.append(
                    f"'{field}' value '{value}' is invalid. "
                    f"Must be one of: {sorted(ENUM_CONSTRAINTS[field])}"
                )
    if enum_errors:
        conn.close()
        return {"status": "error", "message": "Invalid enum values: " + "; ".join(enum_errors)}

    # Date format validation
    import re as _re
    DATE_FIELDS = {"date", "retrieved_date", "last_activity_date", "last_updated"}
    date_errors = []
    for f, val in fields.items():
        if f in DATE_FIELDS and val is not None:
            if not _re.match(r'\d{4}-\d{2}-\d{2}$', str(val)):
                date_errors.append(f"'{f}' must be in YYYY-MM-DD format, got '{val}'")
    if date_errors:
        conn.close()
        return {"status": "error", "message": "Date format error(s): " + "; ".join(date_errors)}

    # Normalize mentioned_regulations to a JSON array string before storing
    import json as _json
    if "mentioned_regulations" in fields:
        raw = fields["mentioned_regulations"]
        if isinstance(raw, list):
            fields["mentioned_regulations"] = _json.dumps(raw)
        elif isinstance(raw, str):
            try:
                parsed = _json.loads(raw)
                # If it parsed to a bare string, wrap it in a list
                if isinstance(parsed, str):
                    fields["mentioned_regulations"] = _json.dumps([parsed])
                # Else it's already a valid list — keep serialized form
                elif isinstance(parsed, list):
                    fields["mentioned_regulations"] = _json.dumps(parsed)
            except _json.JSONDecodeError:
                # Plain string or comma-separated — normalize to array
                items = [v.strip() for v in raw.split(",") if v.strip()]
                fields["mentioned_regulations"] = _json.dumps(items)

    # P6-T2-B: same normalization for mentioned_tags
    if "mentioned_tags" in fields:
        raw = fields["mentioned_tags"]
        if isinstance(raw, list):
            fields["mentioned_tags"] = _json.dumps(raw)
        elif isinstance(raw, str):
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, str):
                    fields["mentioned_tags"] = _json.dumps([parsed])
                elif isinstance(parsed, list):
                    fields["mentioned_tags"] = _json.dumps(parsed)
            except _json.JSONDecodeError:
                items = [v.strip() for v in raw.split(",") if v.strip()]
                fields["mentioned_tags"] = _json.dumps(items)

    try:
        set_clause = ", ".join(f"{f} = ?" for f in fields)
        values = list(fields.values()) + [record_id]
        conn.execute(f"UPDATE {table} SET {set_clause} WHERE {id_col} = ?", values)
        # Reset page_status to draft on any fill (must re-commit)
        conn.execute("UPDATE draft_pages SET page_status = 'draft' WHERE page_id = ?", (page_id,))
        conn.commit()

        # Return current state
        record = _get_record(conn, entity_type, record_id)
        record_dict = dict(record) if record else {}
        required = REQUIRED_ON_COMMIT.get(entity_type, [])
        still_null = [f for f in required if record_dict.get(f) in (None, "", 0)
                      and f not in ["booking_required", "has_official_site",
                                    "pet_friendly", "wheelchair_accessible",
                                    "parking_nearby", "photography_allowed",
                                    "outside_food_allowed",
                                    "family_friendly", "food_available"]]

        return {
            "status": "ok",
            "page_id": page_id,
            "updated": list(fields.keys()),
            "still_null": still_null,
            "current_state": record_dict
        }

    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: COMMIT
# ─────────────────────────────────────────────────────────────────────────────

def tool_COMMIT(page_id: str, db_path: Path = DB_PATH, **kwargs) -> dict:
    """Completeness check, then lock the page."""
    conn = get_connection(db_path)
    page = _get_page(conn, page_id)
    if page is None:
        conn.close()
        return {"status": "error", "message": f"Page '{page_id}' not found."}

    entity_type = page["entity_type"]
    record_id = page["venue_id"]

    record = _get_record(conn, entity_type, record_id)
    if record is None:
        conn.close()
        return {"status": "error", "message": f"Record '{record_id}' not found in DB."}

    record_dict = dict(record)
    required = REQUIRED_ON_COMMIT.get(entity_type, [])

    # Check required fields — allow 0 for integer booleans
    bool_fields = {"booking_required", "has_official_site", "pet_friendly",
                   "wheelchair_accessible", "parking_nearby", "photography_allowed",
                   "outside_food_allowed",
                   "family_friendly", "food_available"}
    missing = []
    for f in required:
        val = record_dict.get(f)
        if val is None:
            missing.append(f)
        elif f not in bool_fields and isinstance(val, str) and val.strip() == "":
            missing.append(f)

    if missing:
        conn.close()
        return {
            "status": "incomplete",
            "page_id": page_id,
            "missing_required": missing,
            "message": f"Fill {len(missing)} required field(s) before committing."
        }

    # source_doc: require likes/saves/view_count >= 1 (default 0 is not acceptable)
    if entity_type == "source_doc":
        missing_engagement = [
            f for f in ("likes", "saves", "view_count")
            if (record_dict.get(f) or 0) < 1
        ]
        if missing_engagement:
            conn.close()
            return {
                "status": "incomplete",
                "page_id": page_id,
                "missing_required": missing_engagement,
                "message": (
                    f"Fields {missing_engagement} must each be set to a non-zero value before committing. "
                    "Set them to reflect the doc's credibility and the venue's traffic_tier: "
                    "high-traffic → likes 50–400, saves 20–150, views 500–5000; "
                    "mid → likes 15–100, saves 5–40, views 100–1000; "
                    "low → likes 3–40, saves 1–15, views 20–200. "
                    "Stale/doubting sources should have lower engagement than truth-carrier docs."
                )
            }

        # P6-T15: author reuse cap — N=2 committed/verified docs per author
        # per city. Case-insensitive on the author string.
        author_raw = (record_dict.get("author") or "").strip()
        city_for_doc = (record_dict.get("city") or "").strip()
        if author_raw and city_for_doc:
            existing_author_n = conn.execute(
                """SELECT COUNT(*) FROM source_docs
                   WHERE city = ?
                     AND LOWER(TRIM(author)) = LOWER(?)
                     AND page_status IN ('committed','verified')
                     AND doc_id != ?""",
                (city_for_doc, author_raw, record_id),
            ).fetchone()[0]
            if existing_author_n >= 2:
                conn.close()
                return {
                    "status": "error",
                    "page_id": page_id,
                    "message": (
                        f"Author '{author_raw}' already has {existing_author_n} "
                        f"committed/verified source_docs in city '{city_for_doc}'. "
                        f"The cap is 2 per author per city — pick a different "
                        f"author handle for this doc so the corpus voice stays "
                        f"diverse. (See HELP('-h format source_doc') for the "
                        f"author-reuse rule.)"
                    ),
                }

        # P6-T19: body-length bounds per doc_type. Outside-bounds bodies
        # often signal a misformed doc (truncated, runaway expansion, or
        # template-fill stuck at one length).
        dt = (record_dict.get("doc_type") or "").strip().lower()
        body_for_len = record_dict.get("body") or ""
        body_len = len(body_for_len)
        bounds = _BODY_LENGTH_BOUNDS.get(dt)
        if bounds is not None:
            lo, hi = bounds
            if body_len < lo or body_len > hi:
                conn.close()
                return {
                    "status": "error",
                    "page_id": page_id,
                    "message": (
                        f"Body length {body_len} chars is outside the "
                        f"expected range for doc_type='{dt}' "
                        f"({lo}-{hi} chars). Bodies should feel naturally "
                        f"sized — too short reads as truncated, too long "
                        f"reads as overcooked. Vary lengths across the "
                        f"corpus (CREATE_PAGE surfaces "
                        f"`city_body_length_counts` so you can see which "
                        f"length brackets are underused)."
                    ),
                }

        # P6-T18: handle-signature soft cap. Once the city has ≥10 committed/
        # verified docs, no single structural signature can claim >40% of them.
        if author_raw and city_for_doc:
            sig = _handle_signature(author_raw)
            sig_counts = conn.execute(
                """SELECT author FROM source_docs
                   WHERE city = ?
                     AND page_status IN ('committed','verified')
                     AND doc_id != ?
                     AND TRIM(author) != ''""",
                (city_for_doc, record_id),
            ).fetchall()
            total = len(sig_counts)
            if total >= 10:
                n_sig = sum(
                    1 for r in sig_counts
                    if _handle_signature(r["author"]) == sig
                )
                if (n_sig + 1) / (total + 1) > 0.40:
                    conn.close()
                    return {
                        "status": "error",
                        "page_id": page_id,
                        "message": (
                            f"Author handle '{author_raw}' has shape "
                            f"`{sig}`, which already accounts for {n_sig}/"
                            f"{total} ({100 * n_sig / total:.0f}%) of "
                            f"committed source_docs in '{city_for_doc}'. "
                            f"Cap is 40% per shape — pick a handle with a "
                            f"different structural shape. Shapes: "
                            f"with_digit (Laura_83, LDN_Dad32), "
                            f"lower_snake (hackney_jim), "
                            f"mixed_snake (East_London_Mum), "
                            f"multi_word (Marcus Chen), "
                            f"mixed_case_compound (MumOnTheMove), "
                            f"single_lower (marcus)."
                        ),
                    }

        # P6-T16: persona must be one of 7 canonical (closed vocab);
        # tone is open (any non-empty string).
        from scripts.generation.handbook import (
            CANONICAL_PERSONAS, _normalize_persona,
            _CANONICAL_PERSONAS_NORM,
        )
        persona_raw = (record_dict.get("persona") or "").strip()
        tone_raw    = (record_dict.get("tone")    or "").strip()
        if not persona_raw:
            conn.close()
            return {
                "status": "incomplete",
                "page_id": page_id,
                "missing_required": ["persona"],
                "message": (
                    f"`persona` is required on every source_doc. Pick one of "
                    f"the 7 canonical personas (each may only appear ONCE per "
                    f"venue): {', '.join(sorted(CANONICAL_PERSONAS))}."
                ),
            }
        if not tone_raw:
            conn.close()
            return {
                "status": "incomplete",
                "page_id": page_id,
                "missing_required": ["tone"],
                "message": (
                    "`tone` is required on every source_doc. Use one of the "
                    "10 canonical tones (see handbook), or invent a new "
                    "descriptor (e.g. 'wry observational', 'reluctant nostalgia') "
                    "when none fits naturally — tone is open vocabulary."
                ),
            }
        persona_norm = _normalize_persona(persona_raw)
        if persona_norm not in _CANONICAL_PERSONAS_NORM:
            conn.close()
            return {
                "status": "error",
                "page_id": page_id,
                "message": (
                    f"persona '{persona_raw}' is not in the canonical persona "
                    f"set. Pick exactly one of: "
                    f"{', '.join(sorted(CANONICAL_PERSONAS))}."
                ),
            }
        # Within-venue persona uniqueness via doc_venue_refs.
        used_personas = conn.execute(
            """SELECT LOWER(TRIM(s.persona)) AS p, s.doc_id
               FROM source_docs s
               JOIN doc_venue_refs r ON s.doc_id = r.doc_id
               WHERE r.venue_id IN (
                   SELECT venue_id FROM doc_venue_refs WHERE doc_id = ?
               )
               AND s.page_status IN ('committed','verified')
               AND s.doc_id != ?
               AND TRIM(s.persona) != ''""",
            (record_id, record_id),
        ).fetchall()
        conflict = next(
            (row for row in used_personas
             if _normalize_persona(row["p"]) == persona_norm),
            None,
        )
        if conflict:
            conn.close()
            return {
                "status": "error",
                "page_id": page_id,
                "message": (
                    f"persona '{persona_raw}' is already used by source_doc "
                    f"'{conflict['doc_id']}' for this venue. Each persona may "
                    f"appear ONCE per venue — pick a different persona so the "
                    f"docs feel like genuinely different authors."
                ),
            }

        # P6-T14: forum docs must contain real Q&A structure. ≥2 turn markers
        # from ≥2 distinct speakers. Blog docs are not affected.
        if (record_dict.get("doc_type") or "").lower() == "forum":
            forum_handles = _extract_forum_handles(record_dict.get("body") or "")
            distinct_forum_handles = {h.lower() for h in forum_handles}
            if len(forum_handles) < 2 or len(distinct_forum_handles) < 2:
                conn.close()
                return {
                    "status": "error",
                    "page_id": page_id,
                    "message": (
                        f"Forum doc body must contain real Q&A structure: "
                        f"≥2 turn markers from ≥2 distinct speakers. "
                        f"Found {len(forum_handles)} marker(s) from "
                        f"{len(distinct_forum_handles)} distinct handle(s). "
                        f"Use one of these patterns at the start of each turn:\n"
                        f"  - 'Reply from <handle>:'  (markdown bold OK)\n"
                        f"  - '<Handle>:'  (handle must contain underscore, "
                        f"digit, or mixed-case — e.g. NorthLondonMum, "
                        f"LDN_Dad32, Cheerio_Lad)\n"
                        f"  - \"Reply from User '<handle>' on YYYY-MM-DD:\"\n"
                        f"A single-voice narrative does not belong in a forum "
                        f"doc — use doc_type='blog' instead. See HELP("
                        f"'-h format source_doc')."
                    ),
                }

        # P6-T2-B: validate mentioned_tags JSON array, cap, and vocab membership.
        raw_mt = record_dict.get("mentioned_tags")
        if raw_mt in (None, ""):
            mt_list: list = []
        else:
            try:
                mt_list = json.loads(raw_mt) if isinstance(raw_mt, str) else list(raw_mt or [])
            except (TypeError, json.JSONDecodeError):
                conn.close()
                return {
                    "status": "error",
                    "page_id": page_id,
                    "message": (
                        "mentioned_tags must be a JSON array of tag strings, e.g. "
                        "'[\"instagrammable\", \"popular-with-locals\"]'."
                    ),
                }
        if not isinstance(mt_list, list) or not all(isinstance(t, str) for t in mt_list):
            conn.close()
            return {
                "status": "error",
                "page_id": page_id,
                "message": "mentioned_tags must be a JSON array of tag strings.",
            }
        if len(mt_list) > 2:
            conn.close()
            return {
                "status": "error",
                "page_id": page_id,
                "message": (
                    f"mentioned_tags caps at 2 entries per doc (got {len(mt_list)}). "
                    f"Spread the venue's hidden tags across multiple docs so each doc "
                    f"surfaces at most 2 tag concepts naturally."
                ),
            }
        if mt_list:
            from scripts.generation.handbook import get_combined_vocab
            city_for_doc = record_dict.get("city", "") or ""
            cc_row = conn.execute(
                "SELECT tag_vocabulary FROM city_config WHERE city = ?",
                (city_for_doc,)
            ).fetchone()
            try:
                city_ext = json.loads(cc_row["tag_vocabulary"] or "[]") if cc_row else []
            except Exception:
                city_ext = []
            vocab = get_combined_vocab(city_ext)
            off = [t for t in mt_list if t not in vocab]
            if off:
                conn.close()
                return {
                    "status": "error",
                    "page_id": page_id,
                    "message": (
                        f"mentioned_tags contains off-vocab entries: {off}. Every entry "
                        f"must be in the city's combined tag vocabulary (universal core "
                        f"+ cuisines + city extension). Concepts that mirror regulation "
                        f"columns (pet_friendly, wheelchair_accessible, booking_required, "
                        f"etc.) belong in mentioned_regulations — not mentioned_tags."
                    ),
                }

    # P6-T1b: cuisine enforcement on venues.
    # - Restaurants MUST have cuisine, and the value must be in UNIVERSAL_CUISINES.
    # - Cafes/bars MAY set cuisine (food/drink venues); if set, value must be on-vocab.
    # - Everything else (museum/attraction/park/neighbourhood/etc.) must NOT have
    #   cuisine — cuisine on a non-food venue is a data-quality bug that poisons
    #   count_distinct + ratio constraints scoped to category=restaurant.
    if entity_type == "venue":
        from scripts.generation.handbook import UNIVERSAL_CUISINES
        FOOD_CATEGORIES = {"restaurant", "cafe", "bar"}
        category = (record_dict.get("category") or "").strip().lower()
        cuisine_val = record_dict.get("cuisine")
        if isinstance(cuisine_val, str):
            cuisine_val = cuisine_val.strip().lower() or None
        if category == "restaurant":
            if not cuisine_val:
                conn.close()
                return {
                    "status": "incomplete",
                    "page_id": page_id,
                    "missing_required": ["cuisine"],
                    "message": (
                        f"Restaurant venue requires 'cuisine'. FILL with one of: "
                        f"{', '.join(sorted(UNIVERSAL_CUISINES))}."
                    ),
                }
            if cuisine_val not in UNIVERSAL_CUISINES:
                conn.close()
                return {
                    "status": "error",
                    "page_id": page_id,
                    "message": (
                        f"cuisine='{cuisine_val}' is not in the canonical cuisine vocabulary. "
                        f"Valid values: {', '.join(sorted(UNIVERSAL_CUISINES))}."
                    ),
                }
        elif cuisine_val and category not in FOOD_CATEGORIES:
            conn.close()
            return {
                "status": "error",
                "page_id": page_id,
                "message": (
                    f"cuisine='{cuisine_val}' set on category='{category}', but cuisine "
                    f"is only meaningful for food/drink venues "
                    f"({', '.join(sorted(FOOD_CATEGORIES))}). "
                    f"Clear the field (leave NULL) for {category} venues."
                ),
            }
        elif cuisine_val and cuisine_val not in UNIVERSAL_CUISINES:
            # cafe / bar with cuisine set — must be on-vocab.
            conn.close()
            return {
                "status": "error",
                "page_id": page_id,
                "message": (
                    f"cuisine='{cuisine_val}' is not in the canonical cuisine vocabulary. "
                    f"Either clear it (cafes/bars don't require cuisine) "
                    f"or set it to one of: {', '.join(sorted(UNIVERSAL_CUISINES))}."
                ),
            }

        # P6-T21: venue lat/lng must be inside the city's OSM boundary
        # polygon. This catches cross-border venues (NJ instead of NY etc.)
        # at COMMIT time. Skipped silently when no polygon is stored
        # (legacy configs, or research_city couldn't fetch one).
        venue_city = (record_dict.get("city") or "").strip()
        venue_lat  = record_dict.get("lat")
        venue_lng  = record_dict.get("lng")
        if venue_city and venue_lat is not None and venue_lng is not None:
            cc_row = conn.execute(
                "SELECT boundary_geojson, display_name FROM city_config WHERE city = ?",
                (venue_city,)
            ).fetchone()
            if cc_row and cc_row["boundary_geojson"]:
                try:
                    poly = json.loads(cc_row["boundary_geojson"])
                except (TypeError, json.JSONDecodeError):
                    poly = None
                if poly and not _point_in_polygon(
                    float(venue_lat), float(venue_lng), poly
                ):
                    display = cc_row["display_name"] or venue_city
                    conn.close()
                    return {
                        "status": "error",
                        "page_id": page_id,
                        "message": (
                            f"Venue coordinates ({venue_lat}, {venue_lng}) are "
                            f"OUTSIDE the boundary of {display}. Pick a venue "
                            f"whose actual address is within {display} — "
                            f"cross-border picks (e.g. Hoboken/Jersey City "
                            f"when generating for New York) won't be accepted."
                        ),
                    }

    # All good — lock page
    conn.execute(
        "UPDATE draft_pages SET page_status = 'committed' WHERE page_id = ?", (page_id,)
    )
    conn.execute(
        f"UPDATE {ENTITY_TABLE[entity_type][0]} SET page_status = 'committed' "
        f"WHERE {ENTITY_TABLE[entity_type][1]} = ?", (record_id,)
    )
    conn.commit()
    conn.close()

    return {
        "status": "committed",
        "page_id": page_id,
        "entity_type": entity_type,
        "record_id": record_id,
        "content": record_dict,
        "message": "Page committed. Review content above, then call VERIFY('all') when all pages are ready."
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: VERIFY
# ─────────────────────────────────────────────────────────────────────────────

def _check_incorrect_hours_diff(conn, venue_id) -> list[dict]:
    """Wrong info entry with source_type=yelp must differ from ground truth."""
    errors = []
    wi_rows = conn.execute(
        "SELECT * FROM wrong_info WHERE venue_id = ? AND source_type = 'yelp'",
        (venue_id,)
    ).fetchall()
    for wi in wi_rows:
        field = wi["affected_field"]  # e.g. "hours_fri"
        yelp_field = f"yelp_{field}"  # e.g. "yelp_hours_fri"
        venue_row = conn.execute("SELECT * FROM venues WHERE venue_id = ?", (venue_id,)).fetchone()
        yelp_row = conn.execute("SELECT * FROM yelp_listings WHERE venue_id = ?", (venue_id,)).fetchone()
        if venue_row is None or yelp_row is None:
            continue
        gt_val = dict(venue_row).get(field)
        yelp_val = dict(yelp_row).get(yelp_field)
        if gt_val is not None and yelp_val is not None and gt_val == yelp_val:
            errors.append({
                "check": "incorrect_hours_diff",
                "detail": f"{yelp_field} matches ground truth {field} — no incorrect difference present",
                "affected": [f"venue:{venue_id}", f"yelp_listing:{venue_id}"]
            })
    return errors


def _check_regulation_visibility(conn, venue_id) -> list[dict]:
    """
    Positive regulations (venue HAS the property) must be mentioned in at least
    one source doc. Absence of a regulation (value=0) does not need to be documented.
    Uses structured mentioned_regulations list — no keyword scanning.
    """
    import json as _json
    errors = []
    venue_row = conn.execute("SELECT * FROM venues WHERE venue_id = ?", (venue_id,)).fetchone()
    if venue_row is None:
        return errors
    v = dict(venue_row)

    # Fields where value=1 (true) means a planning agent needs to discover it via docs.
    # Absence (=0) is never required — agents don't need to know what a venue lacks.
    POSITIVE_REGS = [
        "pet_friendly",
        "wheelchair_accessible",
        "family_friendly",
        "outside_food_allowed",
        "reservation_required",
        "parking_nearby",
    ]

    # Collect all mentioned_regulations from source docs for this venue
    docs = conn.execute(
        "SELECT s.mentioned_regulations FROM source_docs s "
        "JOIN doc_venue_refs d ON s.doc_id = d.doc_id "
        "WHERE d.venue_id = ?", (venue_id,)
    ).fetchall()
    # Also check official site
    official = conn.execute(
        "SELECT mentioned_regulations FROM official_site_docs WHERE venue_id = ?",
        (venue_id,)
    ).fetchone()

    def _parse_mentioned_regs(raw):
        """Parse mentioned_regulations from DB — handles JSON arrays and legacy plain strings."""
        if not raw:
            return set()
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, list):
                return set(parsed)
            if isinstance(parsed, str):
                return {parsed}
        except _json.JSONDecodeError:
            pass
        # Fallback: comma-separated plain string
        return {r.strip() for r in raw.split(",") if r.strip()}

    all_mentioned = set()
    for d in docs:
        all_mentioned.update(_parse_mentioned_regs(d["mentioned_regulations"]))
    if official:
        all_mentioned.update(_parse_mentioned_regs(official["mentioned_regulations"]))

    # Check each positive regulation
    for field in POSITIVE_REGS:
        if v.get(field) == 1:
            if field not in all_mentioned:
                errors.append({
                    "check": "regulation_visibility",
                    "detail": (
                        f"{field}=1 (venue has this property) but no source doc lists "
                        f"'{field}' in its mentioned_regulations. "
                        f"At least one doc body should mention this and include it in mentioned_regulations."
                    ),
                    "affected": [f"venue:{venue_id}"]
                })

    # age_restriction: any non-null value must be mentioned (always notable)
    if v.get("age_restriction") is not None:
        if "age_restriction" not in all_mentioned:
            errors.append({
                "check": "regulation_visibility",
                "detail": (
                    f"age_restriction={v['age_restriction']} but no source doc "
                    f"lists 'age_restriction' in its mentioned_regulations."
                ),
                "affected": [f"venue:{venue_id}"]
            })

    # Negative regulation visibility — soft warnings (~ prefix = non-blocking).
    # Restrictions (value=0) are harder to discover if never hinted at in docs.
    # The agent should naturally embed restriction hints in at least one doc.
    NEGATIVE_REGS = [
        "wheelchair_accessible",
        "pet_friendly",
        "family_friendly",
        "outside_food_allowed",
    ]
    for field in NEGATIVE_REGS:
        if v.get(field) == 0:
            if field not in all_mentioned:
                errors.append({
                    "check": "regulation_visibility",
                    "detail": (
                        f"~ {field}=0 (venue LACKS this property) but no source doc "
                        f"hints at this restriction. Consider adding a natural hint in "
                        f"one doc body — e.g. for wheelchair_accessible=0: 'steep stairs "
                        f"down to the bar'. Then list '{field}' in mentioned_regulations."
                    ),
                    "affected": [f"venue:{venue_id}"]
                })

    return errors


def _check_tag_visibility(conn, venue_id) -> list[dict]:
    """
    P6-T2 Issue B: every yelp_visible=0 tag on the venue must appear in
    `mentioned_tags` on at least one source doc for that venue. Mirrors
    _check_regulation_visibility but for free-form tags.

    Off-vocab legacy tags (yelp_visible=0 but not in the current combined
    vocab) are exempt — this matters during the transition from old London
    data where the universal vocab still contained regulation-mirror tags.
    """
    import json as _json
    errors = []
    venue_row = conn.execute(
        "SELECT city FROM venues WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    if venue_row is None:
        return errors
    city = venue_row["city"]

    from scripts.generation.handbook import get_combined_vocab
    cc_row = conn.execute(
        "SELECT tag_vocabulary FROM city_config WHERE city = ?", (city,)
    ).fetchone()
    try:
        city_ext = _json.loads(cc_row["tag_vocabulary"] or "[]") if cc_row else []
    except Exception:
        city_ext = []
    vocab = get_combined_vocab(city_ext)

    # Hidden tags (yelp_visible=0) for this venue, restricted to in-vocab.
    hidden = [
        r["tag"] for r in conn.execute(
            "SELECT tag FROM tags WHERE venue_id = ? AND yelp_visible = 0",
            (venue_id,)
        ).fetchall()
        if r["tag"] in vocab
    ]
    if not hidden:
        return errors

    # Collect mentioned_tags across all source docs that reference this venue.
    docs = conn.execute(
        """SELECT s.doc_id, s.mentioned_tags
           FROM source_docs s
           JOIN doc_venue_refs r ON s.doc_id = r.doc_id
           WHERE r.venue_id = ?""",
        (venue_id,)
    ).fetchall()
    surfaced: set[str] = set()
    for d in docs:
        raw = d["mentioned_tags"] or "[]"
        try:
            parsed = _json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            parsed = []
        if isinstance(parsed, list):
            surfaced.update(t for t in parsed if isinstance(t, str))

    for tag in hidden:
        if tag not in surfaced:
            errors.append({
                "check": "tag_visibility",
                "detail": (
                    f"Hidden tag '{tag}' on venue {venue_id} is not listed in "
                    f"`mentioned_tags` on any source doc. Every yelp_visible=0 tag "
                    f"must surface in at least one doc — FILL the relevant "
                    f"source_doc with mentioned_tags=[\"{tag}\"] (max 2 per doc) "
                    f"and naturally weave the concept into the body. If the tag "
                    f"is too subtle to surface naturally, either make it "
                    f"yelp_visible=1 or drop it."
                ),
                "affected": [f"venue:{venue_id}", f"tag:{tag}"],
            })
    return errors


def _check_label_subset(conn, venue_id) -> list[dict]:
    """
    Venue must have at least some tags, and at least one yelp_visible tag.
    Yelp listings are the primary discovery surface — a venue with no visible tags
    cannot be found by the planning agent's search.
    """
    errors = []
    total = conn.execute(
        "SELECT COUNT(*) FROM tags WHERE venue_id = ?", (venue_id,)
    ).fetchone()[0]
    yelp_count = conn.execute(
        "SELECT COUNT(*) FROM tags WHERE venue_id = ? AND yelp_visible = 1", (venue_id,)
    ).fetchone()[0]
    if total == 0:
        errors.append({
            "check": "label_subset",
            "detail": (
                f"No tags found for venue {venue_id}. "
                f"Call SET_TAGS with at least 3–5 tags (mix of yelp_visible and hidden)."
            ),
            "affected": [f"venue:{venue_id}"]
        })
    elif yelp_count == 0:
        errors.append({
            "check": "label_subset",
            "detail": (
                f"Venue {venue_id} has {total} tag(s) but none are yelp_visible=1. "
                f"At least 2–4 tags must be yelp_visible so the planning agent can discover this venue."
            ),
            "affected": [f"venue:{venue_id}"]
        })
    return errors


def _check_official_site_exists(conn, venue_id) -> list[dict]:
    """has_official_site=1 → official_site_docs row must exist and be committed."""
    errors = []
    venue_row = conn.execute(
        "SELECT has_official_site FROM venues WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    if venue_row and venue_row["has_official_site"] == 1:
        official = conn.execute(
            "SELECT page_status FROM official_site_docs WHERE venue_id = ?", (venue_id,)
        ).fetchone()
        if official is None:
            errors.append({
                "check": "official_site_exists",
                "detail": (
                    f"has_official_site=1 but no official_site_docs row for venue {venue_id}. "
                    f"Create it with CREATE_PAGE('official_site_doc', venue_id='{venue_id}'), "
                    f"FILL, and COMMIT before calling VERIFY."
                ),
                "affected": [f"venue:{venue_id}"]
            })
        elif official["page_status"] == "draft":
            errors.append({
                "check": "official_site_exists",
                "detail": f"official_site_doc exists but is still in draft status — COMMIT it first.",
                "affected": [f"venue:{venue_id}"]
            })
    return errors


def _check_minimum_doc_count(conn, venue_id: str) -> list[dict]:
    """D1: Enforce minimum source document counts.
    - All venues: ≥2 source docs
    - high traffic_tier venues: ≥3 source docs
    - wrong_info venues: ≥3 source docs (need incorrect_source + truth_carrier + at least one more)
    """
    errors = []

    n_docs = conn.execute(
        """SELECT COUNT(DISTINCT s.doc_id)
           FROM source_docs s
           JOIN doc_venue_roles r ON s.doc_id = r.doc_id
           WHERE r.venue_id = ? AND s.page_status IN ('committed','verified')""",
        (venue_id,)
    ).fetchone()[0]

    # Get venue metadata for threshold determination
    v_row = conn.execute(
        "SELECT traffic_tier, has_wrong_info_planned FROM venues WHERE venue_id = ?",
        (venue_id,)
    ).fetchone()
    if not v_row:
        return errors

    traffic_tier = v_row["traffic_tier"]
    has_wi       = bool(v_row["has_wrong_info_planned"])

    min_docs = 2  # baseline for all venues
    reason   = ""
    if traffic_tier == "high" and n_docs < 3:
        min_docs = 3
        reason = "high-traffic venues need ≥3 source docs (discovery breadth)"
    elif has_wi and n_docs < 3:
        min_docs = 3
        reason = ("wrong-info venues need ≥3 source docs: "
                  "at least one incorrect_source, one truth_carrier, and one more")

    if n_docs < min_docs:
        errors.append({
            "check": "minimum_doc_count",
            "detail": (
                f"Venue has only {n_docs} committed source_doc(s), "
                f"need ≥{min_docs}. {reason} "
                f"Call CREATE_PAGE('source_doc') to add more blog/forum documents."
            )
        })

    return errors


def _check_truth_carrier_registered(conn, venue_id) -> list[dict]:
    """Every wrong_info entry must have ≥1 doc_venue_roles row as truth_carrier."""
    errors = []
    wi_rows = conn.execute(
        "SELECT wrong_info_id FROM wrong_info WHERE venue_id = ?", (venue_id,)
    ).fetchall()
    for wi in wi_rows:
        wid = wi["wrong_info_id"]
        carrier = conn.execute(
            "SELECT 1 FROM doc_venue_roles WHERE wrong_info_id = ? AND role = 'truth_carrier'",
            (wid,)
        ).fetchone()
        if carrier is None:
            errors.append({
                "check": "truth_carrier_registered",
                "detail": f"wrong_info entry '{wid}' has no truth_carrier document registered",
                "affected": [f"venue:{venue_id}", f"wrong_info:{wid}"]
            })
    return errors


def _check_incorrect_source_registered(conn, venue_id) -> list[dict]:
    """Every wrong_info entry must have ≥1 doc_venue_roles row as incorrect_source.
    Without an incorrect_source doc, no document in the corpus actually carries the
    wrong value — the wrong_info entry is abstract and untestable.
    """
    errors = []
    wi_rows = conn.execute(
        "SELECT wrong_info_id FROM wrong_info WHERE venue_id = ?", (venue_id,)
    ).fetchall()
    for wi in wi_rows:
        wid = wi["wrong_info_id"]
        incorrect = conn.execute(
            "SELECT 1 FROM doc_venue_roles WHERE wrong_info_id = ? AND role = 'incorrect_source'",
            (wid,)
        ).fetchone()
        if incorrect is None:
            errors.append({
                "check": "incorrect_source_registered",
                "detail": (
                    f"wrong_info entry '{wid}' has no incorrect_source document registered. "
                    f"At least one source doc must carry the incorrect value so an agent can "
                    f"actually encounter the wrong information. Register a doc with "
                    f"role='incorrect_source' and wrong_info_id='{wid}'."
                ),
                "affected": [f"venue:{venue_id}", f"wrong_info:{wid}"]
            })
    return errors


def _check_source_doc_tag_limit(conn, venue_id) -> list[dict]:
    """Each source doc for this venue may mention at most 2 regulations in mentioned_regulations."""
    import json as _json
    errors = []
    docs = conn.execute(
        "SELECT s.doc_id, s.mentioned_regulations FROM source_docs s "
        "JOIN doc_venue_refs d ON s.doc_id = d.doc_id "
        "WHERE d.venue_id = ?", (venue_id,)
    ).fetchall()
    for row in docs:
        raw = row["mentioned_regulations"]
        if not raw:
            continue
        try:
            regs = _json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            regs = [x.strip() for x in str(raw).split(",") if x.strip()]
        if isinstance(regs, list) and len(regs) > 2:
            errors.append({
                "check": "source_doc_tag_limit",
                "detail": (
                    f"Source doc '{row['doc_id']}' lists {len(regs)} regulations in "
                    f"mentioned_regulations — maximum is 2. Pick the 1–2 that feel most "
                    f"natural for this doc's voice and perspective; cover others in different docs."
                ),
                "affected": [f"source_doc:{row['doc_id']}", f"venue:{venue_id}"]
            })
    return errors


def _check_recommended_pace(conn, venue_id) -> list[dict]:
    """recommended_pace must not be null."""
    errors = []
    row = conn.execute(
        "SELECT recommended_pace FROM venues WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    if row and row["recommended_pace"] is None:
        errors.append({
            "check": "recommended_pace_assigned",
            "detail": f"recommended_pace is null for venue {venue_id}",
            "affected": [f"venue:{venue_id}"]
        })
    return errors


def _check_hours_override_coverage(conn, venue_id) -> list[dict]:
    """
    If the city has known holiday task dates, venues affected by closures
    should have hours_overrides rows.
    This is a soft reminder check — returns a warning, not a hard error.
    """
    # For now: check that outdoor venues have at least considered overrides
    # (full implementation requires city holiday calendar — placeholder)
    return []


def _check_wrong_info_matches_plan(conn, venue_id) -> list[dict]:
    """
    Enforce wrong_info plan in both directions:
    - has_wrong_info_planned=1 → must have ≥1 wrong_info entry (ADD_WRONG_INFO required)
    - has_wrong_info_planned=0 → must have 0 wrong_info entries
    """
    errors = []
    venue_row = conn.execute(
        "SELECT has_wrong_info_planned FROM venues WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    if not venue_row:
        return errors

    planned  = venue_row["has_wrong_info_planned"]
    wi_count = conn.execute(
        "SELECT COUNT(*) FROM wrong_info WHERE venue_id = ?", (venue_id,)
    ).fetchone()[0]

    if planned == 1 and wi_count == 0:
        errors.append({
            "check": "wrong_info_matches_plan",
            "detail": (
                f"Venue {venue_id} has has_wrong_info_planned=1 (your assignment said "
                f"'Wrong info: YES') but has no wrong_info entries. "
                f"You must call ADD_WRONG_INFO before VERIFY for this venue."
            ),
            "affected": [f"venue:{venue_id}"]
        })
    elif planned == 0 and wi_count > 0:
        errors.append({
            "check": "wrong_info_matches_plan",
            "detail": (
                f"Venue {venue_id} has has_wrong_info_planned=0 (your assignment said "
                f"'Wrong info: NO') but has {wi_count} wrong_info entry/entries. "
                f"This venue must have clean data. Do not call ADD_WRONG_INFO for this venue."
            ),
            "affected": [f"venue:{venue_id}"]
        })
    return errors


def _check_no_wrong_info_on_high_traffic(conn, venue_id) -> list[dict]:
    """High traffic venues must not have any wrong_info entries."""
    errors = []
    venue_row = conn.execute(
        "SELECT traffic_tier FROM venues WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    if venue_row and venue_row["traffic_tier"] == "high":
        wi_count = conn.execute(
            "SELECT COUNT(*) FROM wrong_info WHERE venue_id = ?", (venue_id,)
        ).fetchone()[0]
        if wi_count > 0:
            errors.append({
                "check": "no_wrong_info_on_high_traffic",
                "detail": (
                    f"Venue {venue_id} is traffic_tier='high' but has {wi_count} wrong_info "
                    f"entry/entries. High-traffic venues have active owners and dense correction "
                    f"ecosystems — wrong info does not persist there. "
                    f"Remove the wrong_info entry/entries for this venue."
                ),
                "affected": [f"venue:{venue_id}"]
            })
    return errors


def _check_event_mentions(conn, venue_id: str, event_briefs: list[dict]) -> list[dict]:
    """
    For each assigned event, verify that at least one committed source_doc body
    mentions the event name. Hard fail per event with zero mentions.
    """
    if not event_briefs:
        return []

    # Fetch all committed/verified source doc bodies for this venue
    rows = conn.execute(
        """SELECT s.body FROM source_docs s
           JOIN doc_venue_refs r ON s.doc_id = r.doc_id
           WHERE r.venue_id = ?
             AND s.page_status IN ('committed', 'verified')""",
        (venue_id,)
    ).fetchall()
    all_bodies = " ".join((r["body"] or "").lower() for r in rows)

    errors = []
    for ev in event_briefs:
        event_name = ev.get("name", "")
        if not event_name:
            continue
        if event_name.lower() not in all_bodies:
            errors.append({
                "check":    "event_mention",
                "severity": "error",
                "message":  (
                    f"Event '{event_name}' has no mention in any source doc. "
                    f"At least one source doc body must contain this event name naturally. "
                    f"Official site doc must also mention it and its booking status."
                ),
                "affected": [f"venue:{venue_id}", f"event:{event_name}"],
            })
    return errors


def _check_hours_coverage(conn, venue_id) -> list[dict]:
    """
    Check that opening hours are set on BOTH venues table AND yelp_listings.

    Categories where hours are mandatory (all 7 days must be non-null):
      restaurant, cafe, bar, museum, attraction

    Categories where hours are expected but flexible
    (at least one day must be set, or agent must confirm always-open):
      park, neighbourhood, university, and any other category

    Also checks: if venues.hours_* is set, yelp_listings.yelp_hours_* must
    also be set (matching ground truth, or incorrect for wrong_info venues).
    """
    MANDATORY_HOURS = {"restaurant", "cafe", "bar", "museum", "attraction"}
    ALWAYS_OPEN_EXAMPLES = ("park, neighbourhood walk, university campus, "
                            "public square, open market")

    venue_row = conn.execute(
        "SELECT category, hours_mon, hours_tue, hours_wed, hours_thu, "
        "hours_fri, hours_sat, hours_sun FROM venues WHERE venue_id = ?",
        (venue_id,)
    ).fetchone()
    if venue_row is None:
        return []

    v = dict(venue_row)
    category = v.get("category", "")
    day_cols  = ["hours_mon", "hours_tue", "hours_wed", "hours_thu",
                 "hours_fri", "hours_sat", "hours_sun"]
    set_days  = [c for c in day_cols if v.get(c) is not None]

    errors = []

    if not set_days:
        # All 7 days are null on venue
        if category in MANDATORY_HOURS:
            errors.append({
                "check":  "hours_coverage",
                "detail": (
                    f"Opening hours missing for all 7 days. "
                    f"'{category}' venues must have hours set for every day of the week. "
                    f"Call FILL with hours_mon through hours_sun before VERIFY will pass. "
                    f"Use 'closed' for days the venue is shut."
                ),
                "affected": [f"venue:{venue_id}"],
            })
        else:
            errors.append({
                "check":  "hours_coverage",
                "detail": (
                    f"No opening hours set on any day. "
                    f"If this venue is always accessible (e.g. {ALWAYS_OPEN_EXAMPLES}), "
                    f"confirm by setting at least hours_mon to '24 hours' or 'always open'. "
                    f"Otherwise set specific hours for each relevant day before VERIFY passes."
                ),
                "affected": [f"venue:{venue_id}"],
            })
        return errors

    # Venue has hours set — check that yelp_listings also has hours
    yelp_row = conn.execute(
        "SELECT yelp_hours_mon, yelp_hours_tue, yelp_hours_wed, yelp_hours_thu, "
        "yelp_hours_fri, yelp_hours_sat, yelp_hours_sun "
        "FROM yelp_listings WHERE venue_id = ?",
        (venue_id,)
    ).fetchone()
    if yelp_row is None:
        return errors  # no yelp listing — skip

    y = dict(yelp_row)
    yelp_day_cols = ["yelp_hours_mon", "yelp_hours_tue", "yelp_hours_wed",
                     "yelp_hours_thu", "yelp_hours_fri", "yelp_hours_sat",
                     "yelp_hours_sun"]
    yelp_set = [c for c in yelp_day_cols if y.get(c) is not None]

    if not yelp_set and set_days:
        # Venue has hours but yelp has none — agent forgot to fill yelp hours
        # Check if wrong_info affects hours — if so, mention that
        wi = conn.execute(
            "SELECT affected_field FROM wrong_info WHERE venue_id = ? "
            "AND affected_field LIKE 'hours_%'", (venue_id,)
        ).fetchall()
        wi_note = ""
        if wi:
            wi_fields = [r[0] for r in wi]
            wi_note = (f" Note: this venue has wrong_info on {wi_fields} — "
                       f"set those to the INCORRECT value and others to match ground truth.")

        errors.append({
            "check": "yelp_hours_coverage",
            "detail": (
                f"Venue has opening hours set but yelp_listing has NO hours. "
                f"FILL the yelp_listing page with yelp_hours_mon through yelp_hours_sun "
                f"matching the venue's ground truth hours (use 'Closed' for closed days).{wi_note}"
            ),
            "affected": [f"yelp_listing:{venue_id}"],
        })

    return errors


def tool_VERIFY(target: str, venue_id: str = None,
                event_briefs: list[dict] = None,
                db_path: Path = DB_PATH, **kwargs) -> dict:
    """Run all consistency checks. target should be 'all'."""
    conn = get_connection(db_path)

    # Find venue_id — either passed directly or inferred from draft_pages
    if venue_id is None:
        # Try to find from context — get the most recently created venue in draft
        row = conn.execute(
            "SELECT venue_id FROM draft_pages WHERE entity_type = 'venue' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row:
            venue_id = row["venue_id"]

    if venue_id is None:
        conn.close()
        return {"status": "error", "message": "venue_id required for VERIFY"}

    # Check all pages for this venue are committed
    # Auto-commit any pages left in needs_repair state so agent can re-verify
    pages = conn.execute(
        "SELECT * FROM draft_pages WHERE venue_id = ?", (venue_id,)
    ).fetchall()
    uncommitted = [p["page_id"] for p in pages
                   if p["page_status"] not in ("committed", "verified", "needs_repair")]
    if uncommitted:
        conn.close()
        return {
            "status": "error",
            "message": (
                f"These pages are not yet committed: {uncommitted}. "
                f"Call COMMIT on each page_id before calling VERIFY. "
                f"Use GET_STATUS to see all page IDs and their current status."
            ),
            "uncommitted": uncommitted
        }
    # needs_repair pages: treat as committed for verification
    # (agent must have called FILL or CREATE_PAGE to address the issue)
    conn.execute(
        "UPDATE draft_pages SET page_status = 'committed' "
        "WHERE venue_id = ? AND page_status = 'needs_repair'",
        (venue_id,)
    )
    conn.commit()

    # Run all checks
    errors = []
    errors += _check_no_wrong_info_on_high_traffic(conn, venue_id)
    errors += _check_wrong_info_matches_plan(conn, venue_id)
    errors += _check_incorrect_hours_diff(conn, venue_id)
    errors += _check_regulation_visibility(conn, venue_id)
    errors += _check_tag_visibility(conn, venue_id)
    errors += _check_label_subset(conn, venue_id)
    errors += _check_official_site_exists(conn, venue_id)
    errors += _check_truth_carrier_registered(conn, venue_id)
    errors += _check_incorrect_source_registered(conn, venue_id)
    errors += _check_source_doc_tag_limit(conn, venue_id)
    errors += _check_recommended_pace(conn, venue_id)
    errors += _check_hours_override_coverage(conn, venue_id)
    errors += _check_hours_coverage(conn, venue_id)
    errors += _check_event_mentions(conn, venue_id, event_briefs or [])
    errors += _check_minimum_doc_count(conn, venue_id)  # D1

    # Separate soft warnings (~ prefix in detail) from hard errors
    hard_errors = [e for e in errors if not e.get("detail", "").startswith("~")]
    soft_warnings = [e for e in errors if e.get("detail", "").startswith("~")]

    if hard_errors:
        # Mark pages as needs_repair — do NOT delete them
        # Agent should fix the specific issue and re-VERIFY
        conn.execute(
            "UPDATE draft_pages SET page_status = 'needs_repair' WHERE venue_id = ?",
            (venue_id,)
        )
        conn.commit()
        conn.close()
        result = {
            "passed": False,
            "venue_id": venue_id,
            "errors": hard_errors,
            "action_required": (
                "Fix the specific issues above, then call VERIFY again. "
                "Do NOT create a new venue — your existing pages are still here. "
                "To fix regulation_visibility: the venue has a positive regulation (value=1) "
                "that no source doc mentions. FILL an existing source doc so its body "
                "naturally references that property, and set mentioned_regulations to a "
                "JSON array, e.g. [\"pet_friendly\"]. Then COMMIT and ensure the doc is "
                "REGISTER_DOC_REFS'd for this venue. "
                "mentioned_regulations must be a JSON array string — plain strings will NOT be recognised. "
                "To fix incorrect_hours_diff: use FILL on the yelp_listing page to set "
                "the incorrect value, then COMMIT it again."
            )
        }
        if soft_warnings:
            result["warnings"] = soft_warnings
        return result

    # All passed — mark draft_pages AND real entity tables as verified
    conn.execute(
        "UPDATE draft_pages SET page_status = 'verified' WHERE venue_id = ?", (venue_id,)
    )
    # Propagate to real tables (COMMIT sets them to 'committed'; VERIFY must promote to 'verified')
    for real_table, id_col in [
        ("venues",            "venue_id"),
        ("yelp_listings",     "venue_id"),
        ("official_site_docs","venue_id"),
    ]:
        conn.execute(
            f"UPDATE {real_table} SET page_status = 'verified' WHERE {id_col} = ?",
            (venue_id,)
        )
    # source_docs are linked via doc_venue_refs — mark all committed docs for this venue
    conn.execute("""
        UPDATE source_docs SET page_status = 'verified'
        WHERE doc_id IN (
            SELECT doc_id FROM doc_venue_refs WHERE venue_id = ?
        ) AND page_status = 'committed'
    """, (venue_id,))
    conn.commit()
    conn.close()

    return {
        "passed": True,
        "venue_id": venue_id,
        "checks_run": 11,
        "warnings": soft_warnings if soft_warnings else [],
        "message": "All checks passed. Call SUBMIT to complete this venue."
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: GET_STATUS
# ─────────────────────────────────────────────────────────────────────────────

def tool_GET_STATUS(venue_id: str, db_path: Path = DB_PATH, **kwargs) -> dict:
    """Return current state of all pages for this venue."""
    conn = get_connection(db_path)
    pages = conn.execute(
        "SELECT * FROM draft_pages WHERE venue_id = ?", (venue_id,)
    ).fetchall()

    if not pages:
        conn.close()
        return {
            "venue_id": venue_id,
            "pages": [],
            "ready_to_verify": False,
            "reason": "No pages found for this venue_id"
        }

    page_summaries = []
    for p in pages:
        entity_type = p["entity_type"]
        record_id = p["venue_id"]
        null_fields = get_null_fields(conn, entity_type, record_id) if p["page_status"] == "draft" else []
        page_summaries.append({
            "page_id": p["page_id"],
            "entity_type": entity_type,
            "page_status": p["page_status"],
            "null_required_fields": null_fields
        })

    all_committed = all(p["page_status"] in ("committed", "verified") for p in pages)
    any_not_committed = [p["entity_type"] for p in pages if p["page_status"] == "draft"]

    conn.close()
    return {
        "venue_id": venue_id,
        "pages": page_summaries,
        "ready_to_verify": all_committed,
        "reason": None if all_committed else f"{len(any_not_committed)} page(s) not yet committed: {any_not_committed}"
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: THINK
# ─────────────────────────────────────────────────────────────────────────────

def tool_THINK(thought: str, **kwargs) -> dict:
    """Log reasoning, decisions, or correction plans. Use freely at any point.
    Mandatory after failed VERIFY before redrafting. No DB writes — audit trail only."""
    if not thought or not thought.strip():
        return {"status": "error", "message": "thought cannot be empty"}
    # In a real system this would write to a log table
    # For now just confirm and return
    return {
        "status": "logged",
        "message": "Correction plan recorded. Proceed with CREATE_PAGE to redraft the failed page."
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: SET_TAGS
# ─────────────────────────────────────────────────────────────────────────────

def _canon_tag(raw: str) -> str:
    """Canonicalise a tag to hyphenated kebab-case at write time."""
    return raw.strip().lower().replace("_", "-").replace(" ", "-")


def _trigrams(s: str) -> set:
    """Character-trigram set used by the trigram-similarity fallback."""
    s = s.replace("-", "")
    return {s[i:i+3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}


def _trig_sim(a: str, b: str) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _suggest_did_you_mean(
    off_vocab_tags: list[str],
    vocab: frozenset,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, list[str]]:
    """
    For each off-vocab tag, return up to 3 closest existing vocab entries.

    Uses a single DeepSeek-chat call when api_key is available; falls back to
    trigram similarity over the vocab when not (tests, dry-run).
    Returns {off_vocab_tag: [suggestion1, ...]}.
    """
    vocab_list = sorted(vocab)
    # Trigram fallback — always works
    trigram_picks: dict[str, list[str]] = {}
    for tag in off_vocab_tags:
        scored = [(v, _trig_sim(tag, v)) for v in vocab_list]
        scored.sort(key=lambda x: x[1], reverse=True)
        trigram_picks[tag] = [v for v, s in scored[:3] if s > 0]

    if not api_key:
        return trigram_picks

    try:
        from openai import OpenAI as _OpenAI
    except ImportError:
        return trigram_picks

    try:
        client = _OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        prompt = (
            f"Vocabulary (canonical tag list): {', '.join(vocab_list)}\n\n"
            f"Off-vocab tags submitted: {', '.join(off_vocab_tags)}\n\n"
            f"For each off-vocab tag, return up to 3 closest entries from the "
            f"vocabulary by meaning (NOT by string similarity). If nothing fits, "
            f"return an empty list for that tag. Return ONLY valid JSON of the "
            f"form: {{\"off_vocab_tag\": [\"vocab1\", \"vocab2\", \"vocab3\"]}}."
        )
        resp = client.chat.completions.create(
            model=model or "deepseek-chat", max_tokens=400, stream=False,
            messages=[
                {"role": "system", "content": "Return only a valid JSON object."},
                {"role": "user",   "content": prompt},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        import re as _re
        raw = _re.sub(r"<think>.*?</think>", "", raw, flags=_re.DOTALL).strip()
        raw = _re.sub(r"^```(?:json)?\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"\s*```$", "", raw.strip(), flags=_re.MULTILINE)
        parsed = json.loads(raw.strip())
        if isinstance(parsed, dict):
            out: dict[str, list[str]] = {}
            for tag in off_vocab_tags:
                v = parsed.get(tag)
                # Sanity: every suggestion must actually be in the vocab.
                if isinstance(v, list):
                    out[tag] = [s for s in v if s in vocab][:3]
                else:
                    out[tag] = trigram_picks.get(tag, [])
            return out
    except Exception:
        pass
    return trigram_picks


def tool_SET_TAGS(venue_id: str, tags: list, db_path: Path = DB_PATH, **kwargs) -> dict:
    """
    Set tags for a venue. Replaces all existing tags for this venue.
    Each tag is a dict with 'tag' (str) and 'yelp_visible' (0 or 1).

    P6-T1: every tag must be in the city's combined vocabulary
    (universal core + universal cuisines + city_config.tag_vocabulary).
    Off-vocab tags hard-reject with "did you mean" suggestions.
    """
    if not venue_id:
        return {"status": "error", "message": "venue_id required"}
    if not tags or not isinstance(tags, list):
        return {"status": "error", "message": "tags must be a non-empty list of {tag, yelp_visible} dicts"}

    city = kwargs.get("city", "")
    api_key = kwargs.get("api_key")
    model = kwargs.get("model")
    conn = get_connection(db_path)

    # Check venue exists
    if not conn.execute("SELECT 1 FROM venues WHERE venue_id = ?", (venue_id,)).fetchone():
        conn.close()
        return {"status": "error",
                "message": f"Venue '{venue_id}' not found. Create and COMMIT venue page first."}

    # Get city from venue if not injected
    if not city:
        row = conn.execute("SELECT city FROM venues WHERE venue_id = ?", (venue_id,)).fetchone()
        city = row["city"] if row else ""

    try:
        # Canonicalise tags first so vocab lookup matches DB write form.
        canon_pairs = []   # list of (tag, yelp_visible)
        for t in tags:
            if not isinstance(t, dict) or "tag" not in t:
                continue
            canon_pairs.append((_canon_tag(str(t["tag"])), int(t.get("yelp_visible", 0))))

        if not canon_pairs:
            conn.close()
            return {"status": "error",
                    "message": "No valid {tag, yelp_visible} entries in tags list."}

        # ── P6-T1: Vocabulary enforcement ────────────────────────────────────
        from scripts.generation.handbook import get_combined_vocab
        cc_row = conn.execute(
            "SELECT tag_vocabulary FROM city_config WHERE city = ?", (city,)
        ).fetchone()
        try:
            city_extension = json.loads(cc_row["tag_vocabulary"] or "[]") if cc_row else []
        except (TypeError, json.JSONDecodeError):
            city_extension = []
        vocab = get_combined_vocab(city_extension)

        off_vocab = [tag for tag, _ in canon_pairs if tag not in vocab]
        if off_vocab:
            suggestions = _suggest_did_you_mean(off_vocab, vocab, api_key, model)
            sug_lines = []
            for ov in off_vocab:
                sug = suggestions.get(ov, [])
                if sug:
                    sug_lines.append(f"  '{ov}' → did you mean: {', '.join(sug)}")
                else:
                    sug_lines.append(f"  '{ov}' → no close vocab entry; pick a different concept")
            conn.close()
            return {
                "status": "error",
                "off_vocab": off_vocab,
                "suggestions": suggestions,
                "message": (
                    f"Tags not in this city's canonical vocabulary ({len(off_vocab)} of "
                    f"{len(canon_pairs)} rejected):\n"
                    + "\n".join(sug_lines)
                    + "\n\nThe canonical vocabulary is the universal core + universal "
                      "cuisines + this city's extension (see your assignment block). "
                      "Replace each off-vocab tag with a canonical one and call SET_TAGS again."
                ),
            }

        # ── Per-venue tag limit (15 hard, 12 warn) — vocab-validated tags only
        MAX_TAGS_HARD = 15
        MAX_TAGS_WARN = 12
        if len(canon_pairs) > MAX_TAGS_HARD:
            conn.close()
            return {
                "status": "error",
                "message": (
                    f"Too many tags ({len(canon_pairs)}). Maximum {MAX_TAGS_HARD} per venue. "
                    f"Tags should be filterable properties, not descriptive prose. "
                    f"Pick the {MAX_TAGS_HARD} most important ones and call SET_TAGS again."
                ),
            }

        # All tags in-vocab and within count limit — commit to DB.
        conn.execute("DELETE FROM tags WHERE venue_id = ?", (venue_id,))
        inserted = []
        for tag_val, yelp_vis in canon_pairs:
            conn.execute(
                "INSERT OR IGNORE INTO tags (tag, city, venue_id, yelp_visible) VALUES (?,?,?,?)",
                (tag_val, city, venue_id, yelp_vis),
            )
            inserted.append({"tag": tag_val, "yelp_visible": yelp_vis})
        conn.commit()

        yelp_tags = [t["tag"] for t in inserted if t["yelp_visible"]]
        hidden_tags = [t["tag"] for t in inserted if not t["yelp_visible"]]
        result = {
            "status": "ok",
            "venue_id": venue_id,
            "tags_set": len(inserted),
            "yelp_visible": yelp_tags,
            "hidden": hidden_tags,
        }
        if len(inserted) > MAX_TAGS_WARN:
            result["warning"] = (
                f"High tag count ({len(inserted)}). "
                f"Consider pruning to {MAX_TAGS_WARN} core filterable tags."
            )
        return result
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: CONFIRM_TAGS
# ─────────────────────────────────────────────────────────────────────────────

def tool_CONFIRM_TAGS(venue_id: str, confirmed_new_tags: list,
                      db_path: Path = DB_PATH, **kwargs) -> dict:
    """
    DEPRECATED (P6-T1): originally the escape hatch for the soft "3 new tags"
    warning. With canonical-vocabulary enforcement at SET_TAGS time, there is
    no longer a path where SET_TAGS returns status='warning' — off-vocab tags
    are hard-rejected with suggestions, and on-vocab tags need no confirmation.
    Kept as a no-op for back-compat with any agent that still calls it.
    """
    if not venue_id:
        return {"status": "error", "message": "venue_id required"}
    if not confirmed_new_tags or not isinstance(confirmed_new_tags, list):
        return {"status": "error", "message": "confirmed_new_tags must be a non-empty list of tag strings"}
    return {
        "status": "ok",
        "venue_id": venue_id,
        "confirmed": confirmed_new_tags,
        "message": (
            f"New tags confirmed as distinct for {venue_id}: {confirmed_new_tags}. "
            f"Proceed to VERIFY."
        )
    }




def tool_REGISTER_DOC_REFS(doc_id: str, venue_id: str,
                            role: str = "neutral", wrong_info_id: str = None,
                            db_path: Path = DB_PATH, **kwargs) -> dict:
    """
    Register that a source_doc mentions a venue, and set its role.
    Call after COMMITting a source_doc page.
    role: neutral | incorrect_source | truth_carrier
    """
    if not doc_id or not venue_id:
        return {"status": "error", "message": "doc_id and venue_id both required"}
    valid_roles = {"neutral", "incorrect_source", "truth_carrier"}
    if role not in valid_roles:
        return {"status": "error", "message": f"role must be one of {valid_roles}"}

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO doc_venue_refs (doc_id, venue_id) VALUES (?,?)",
            (doc_id, venue_id)
        )
        conn.execute(
            "INSERT OR REPLACE INTO doc_venue_roles (doc_id, venue_id, role, wrong_info_id) "
            "VALUES (?,?,?,?)",
            (doc_id, venue_id, role, wrong_info_id)
        )
        conn.commit()
        return {
            "status": "ok",
            "doc_id": doc_id,
            "venue_id": venue_id,
            "role": role,
            "wrong_info_id": wrong_info_id
        }
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: ADD_WRONG_INFO
# ─────────────────────────────────────────────────────────────────────────────

def tool_ADD_WRONG_INFO(venue_id: str, affected_field: str,
                        incorrect_value: str, correct_value: str,
                        source_type: str, wrong_info_category: str,
                        origin_story: str,
                        incorrect_source_doc_id: str = None,
                        truth_carrier_doc_id: str = None,
                        db_path: Path = DB_PATH, **kwargs) -> dict:
    """
    P6-T22: Atomic wrong_info workflow.

    Creates a wrong_info entry AND registers both required source-doc roles
    (incorrect_source + truth_carrier) in one transactional call. Replaces
    the old 3-call chain (ADD_WRONG_INFO + 2× REGISTER_DOC_REFS), which had
    a ~14% failure rate on multi-wrong-info venues — the agent often
    forgot the registrations for one of multiple entries.

    Both doc_ids are REQUIRED. They must reference committed/verified
    source_docs that already exist (create + COMMIT the docs first). The
    tool auto-creates doc_venue_refs rows for the venue if missing, then
    writes both doc_venue_roles rows + the wrong_info row atomically.
    """
    valid_sources = {"yelp", "blog", "forum"}
    valid_categories = {"temporal_decay", "propagation_error", "conditional", "subjective"}

    if source_type not in valid_sources:
        return {"status": "error", "message": f"source_type must be one of {valid_sources}"}
    if wrong_info_category not in valid_categories:
        return {"status": "error", "message": f"wrong_info_category must be one of {valid_categories}"}
    if not origin_story or len(origin_story.strip()) < 20:
        return {"status": "error", "message": "origin_story must be a meaningful sentence explaining how this mistake entered this source"}
    if not incorrect_value or str(incorrect_value).strip().lower() in ("null", "none", "", "n/a"):
        return {"status": "error", "message": f"incorrect_value cannot be null or empty. It must be the actual wrong value that appears in the incorrect source. Got: '{incorrect_value}'"}
    if not correct_value or correct_value.strip().lower() in ("null", "none", "", "n/a"):
        return {"status": "error", "message": f"correct_value cannot be null or empty. It must be the actual ground truth value. Got: '{correct_value}'"}

    # Validate hours wrong info direction.
    # temporal_decay: incorrect must be wider on at least one end — opens earlier, closes later, or both.
    #   Reject only if incorrect is strictly harmless in both directions (opens later AND closes earlier).
    # conditional: no direction restriction — incorrect can be narrower, different, or venue fully closed.
    import re as _re
    if "hours" in affected_field and wrong_info_category == "temporal_decay" and incorrect_value and correct_value:
        def _parse_times(s):
            """Return (open_hhmm, close_hhmm) as integers for comparison, or None."""
            m = _re.match(r'(\d{2}):(\d{2})-(\d{2}):(\d{2})', s.strip())
            if not m:
                return None
            return (int(m.group(1)) * 60 + int(m.group(2)),
                    int(m.group(3)) * 60 + int(m.group(4)))
        incorrect_times = _parse_times(incorrect_value)
        correct_times = _parse_times(correct_value)
        if incorrect_times and correct_times:
            incorrect_open, incorrect_close = incorrect_times
            correct_open, correct_close = correct_times
            # At least one end must be wider (harmful to the agent):
            #   incorrect_open  < correct_open  — incorrect opens earlier (agent plans early visit, venue closed)
            #   incorrect_close > correct_close — incorrect closes later  (agent plans late visit, venue closed)
            # Either alone is fine; both together is also fine.
            # Only reject when incorrect is harmless on both ends simultaneously.
            #
            # Valid:   incorrect 16:00-23:30 vs correct 17:00-23:00  (opens earlier AND closes later)
            # Valid:   incorrect 16:00-23:00 vs correct 17:00-23:00  (opens earlier, same close)
            # Valid:   incorrect 17:00-23:30 vs correct 17:00-23:00  (closes later, same open)
            # Invalid: incorrect 18:00-22:00 vs correct 17:00-23:00  (opens later AND closes earlier — harmless)
            opens_earlier = incorrect_open < correct_open
            closes_later  = incorrect_close > correct_close
            if not opens_earlier and not closes_later:
                return {
                    "status": "error",
                    "message": (
                        f"Invalid hours wrong info: incorrect ({incorrect_value}) is not wider than correct "
                        f"({correct_value}) on either end. "
                        f"The incorrect value must open earlier, close later, or both — "
                        f"e.g. incorrect 16:00-23:00 vs correct 17:00-23:00 (opens earlier). "
                        f"(For conditional/seasonal hours use wrong_info_category='conditional' instead.)"
                    )
                }

    conn = get_connection(db_path)
    venue_row = conn.execute("SELECT * FROM venues WHERE venue_id = ?", (venue_id,)).fetchone()
    if not venue_row:
        conn.close()
        return {"status": "error", "message": f"Venue '{venue_id}' not found."}

    # Guardrail: high-traffic venues should almost never have wrong info
    traffic_tier = venue_row["traffic_tier"]
    existing_wi = conn.execute(
        "SELECT COUNT(*) FROM wrong_info WHERE venue_id = ?", (venue_id,)
    ).fetchone()[0]
    if traffic_tier == "high" and existing_wi >= 1:
        conn.close()
        return {
            "status": "error",
            "message": (
                "High-traffic venues should have at most 1 wrong info entry — "
                "their active online presence means errors get corrected quickly. "
                "This venue already has one. Do not add more."
            )
        }
    if existing_wi >= 2:
        conn.close()
        return {
            "status": "error",
            "message": (
                f"This venue already has {existing_wi} wrong info entries. "
                "Maximum 2 per venue — more than that is unrealistic. "
                "Focus on quality over quantity."
            )
        }

    # P6-T22 validation: both doc_ids are required + must reference real,
    # committed docs that are distinct.
    if not incorrect_source_doc_id or not str(incorrect_source_doc_id).strip():
        conn.close()
        return {
            "status": "error",
            "message": (
                "incorrect_source_doc_id is required. Pass the doc_id of a "
                "committed source_doc whose body carries the WRONG value "
                "(the agent encounters this and may believe it). "
                "Create + COMMIT the doc first, then call ADD_WRONG_INFO."
            ),
        }
    if not truth_carrier_doc_id or not str(truth_carrier_doc_id).strip():
        conn.close()
        return {
            "status": "error",
            "message": (
                "truth_carrier_doc_id is required. Pass the doc_id of a "
                "committed source_doc whose body carries the CORRECTION "
                "(embedded naturally in experience prose). Create + COMMIT "
                "the doc first, then call ADD_WRONG_INFO."
            ),
        }
    incorrect_source_doc_id = str(incorrect_source_doc_id).strip()
    truth_carrier_doc_id = str(truth_carrier_doc_id).strip()
    if incorrect_source_doc_id == truth_carrier_doc_id:
        conn.close()
        return {
            "status": "error",
            "message": (
                "incorrect_source_doc_id and truth_carrier_doc_id must be "
                "DIFFERENT docs. One doc can't both carry the wrong value AND "
                "the correction — pick two distinct source_docs."
            ),
        }

    # Verify both docs exist and are committed/verified.
    for label, did in (("incorrect_source", incorrect_source_doc_id),
                       ("truth_carrier",    truth_carrier_doc_id)):
        drow = conn.execute(
            "SELECT page_status FROM source_docs WHERE doc_id = ?", (did,)
        ).fetchone()
        if drow is None:
            conn.close()
            return {
                "status": "error",
                "message": (
                    f"{label} doc_id '{did}' not found in source_docs. "
                    f"Create + COMMIT the doc first."
                ),
            }
        if drow["page_status"] not in ("committed", "verified"):
            conn.close()
            return {
                "status": "error",
                "message": (
                    f"{label} doc '{did}' is in status '{drow['page_status']}' "
                    f"— must be 'committed' or 'verified' before linking to "
                    f"wrong_info. COMMIT the doc first."
                ),
            }

    # Conflict check: neither doc can already be incorrect_source OR
    # truth_carrier for a DIFFERENT wrong_info on this venue (the schema's
    # (doc_id, venue_id) PK on doc_venue_roles only allows one role per
    # doc per venue).
    for label, did in (("incorrect_source", incorrect_source_doc_id),
                       ("truth_carrier",    truth_carrier_doc_id)):
        existing = conn.execute(
            "SELECT role, wrong_info_id FROM doc_venue_roles "
            "WHERE doc_id = ? AND venue_id = ?",
            (did, venue_id),
        ).fetchone()
        if existing and existing["role"] in ("incorrect_source", "truth_carrier"):
            if existing["wrong_info_id"]:
                conn.close()
                return {
                    "status": "error",
                    "message": (
                        f"doc '{did}' is already registered as "
                        f"'{existing['role']}' for a different wrong_info "
                        f"({existing['wrong_info_id']}) on venue '{venue_id}'. "
                        f"A doc can only play one wrong-info role per venue — "
                        f"create a fresh doc for this wrong_info entry."
                    ),
                }

    from scripts.generation.db import new_wrong_info_id
    wi_id = new_wrong_info_id()
    try:
        # Atomic write: wrong_info row + auto-create doc_venue_refs + 2 role
        # registrations. All-or-nothing via a single transaction.
        conn.execute("""
            INSERT INTO wrong_info
                (wrong_info_id, venue_id, affected_field, incorrect_value, correct_value,
                 source_type, wrong_info_category, origin_story)
            VALUES (?,?,?,?,?,?,?,?)
        """, (wi_id, venue_id, affected_field, incorrect_value, correct_value,
              source_type, wrong_info_category, origin_story.strip()))
        # Auto-create doc_venue_refs rows if they don't exist (parallel to
        # the P6-T20 CREATE_PAGE flow which also auto-populates this table).
        for did in (incorrect_source_doc_id, truth_carrier_doc_id):
            conn.execute(
                "INSERT OR IGNORE INTO doc_venue_refs (doc_id, venue_id) "
                "VALUES (?, ?)",
                (did, venue_id),
            )
        # Write both role rows.
        conn.execute(
            "INSERT OR REPLACE INTO doc_venue_roles "
            "(doc_id, venue_id, role, wrong_info_id) VALUES (?,?,?,?)",
            (incorrect_source_doc_id, venue_id, "incorrect_source", wi_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO doc_venue_roles "
            "(doc_id, venue_id, role, wrong_info_id) VALUES (?,?,?,?)",
            (truth_carrier_doc_id, venue_id, "truth_carrier", wi_id),
        )
        conn.commit()
        return {
            "status": "ok",
            "wrong_info_id": wi_id,
            "venue_id": venue_id,
            "affected_field": affected_field,
            "incorrect_source_doc_id": incorrect_source_doc_id,
            "truth_carrier_doc_id": truth_carrier_doc_id,
            "note": (
                "wrong_info row + both doc role registrations committed "
                "atomically. VERIFY's truth_carrier_registered and "
                "incorrect_source_registered checks will pass for this "
                "entry — no separate REGISTER_DOC_REFS calls needed."
            ),
        }
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# TOOL: SUBMIT
# ─────────────────────────────────────────────────────────────────────────────

def tool_SUBMIT(venue_id: str, db_path: Path = DB_PATH, **kwargs) -> dict:
    """Mark venue complete. All pages must be verified."""
    conn = get_connection(db_path)
    pages = conn.execute(
        "SELECT * FROM draft_pages WHERE venue_id = ?", (venue_id,)
    ).fetchall()

    if not pages:
        conn.close()
        return {"status": "error", "message": f"No pages found for venue '{venue_id}'"}

    unverified = [p["page_id"] for p in pages if p["page_status"] != "verified"]
    if unverified:
        conn.close()
        return {
            "status": "error",
            "message": f"Cannot submit: {len(unverified)} page(s) not yet verified.",
            "unverified": unverified
        }

    # Mark venue as submitted in venues table
    conn.execute(
        "UPDATE venues SET page_status = 'verified' WHERE venue_id = ?", (venue_id,)
    )
    # Clean up draft_pages
    conn.execute("DELETE FROM draft_pages WHERE venue_id = ?", (venue_id,))
    conn.commit()
    conn.close()

    return {
        "status": "submitted",
        "venue_id": venue_id,
        "pages_submitted": len(pages),
        "message": "Venue package complete and saved. This venue is now locked."
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL DISPATCH MAP (for agent runner)
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = {
    "HELP":                tool_HELP,
    "CREATE_PAGE":         tool_CREATE_PAGE,
    "FILL":                tool_FILL,
    "COMMIT":              tool_COMMIT,
    "VERIFY":              tool_VERIFY,
    "GET_STATUS":          tool_GET_STATUS,
    "THINK":             tool_THINK,
    "SET_TAGS":            tool_SET_TAGS,
    "CONFIRM_TAGS":        tool_CONFIRM_TAGS,
    "REGISTER_DOC_REFS":   tool_REGISTER_DOC_REFS,
    "ADD_WRONG_INFO":      tool_ADD_WRONG_INFO,
    "SUBMIT":              tool_SUBMIT,
}


def dispatch(tool_name: str, **kwargs) -> dict:
    """Dispatch a tool call by name. Returns tool result dict."""
    if tool_name not in TOOLS:
        return {
            "status": "error",
            "message": f"Unknown tool '{tool_name}'. Query HELP('-h function') for available tools."
        }
    try:
        return TOOLS[tool_name](**kwargs)
    except TypeError as e:
        return {
            "status": "error",
            "message": f"Tool call error for {tool_name}: {e}. Check required arguments with HELP('-h function {tool_name}')."
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Unexpected error in {tool_name}: {e}"
        }