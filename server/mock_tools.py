"""
server/mock_tools.py

4 tools exposed to the agent.
Reads from per-city SQLite DBs at data/cities/{city}/travelbench.db
(populated by scripts/generation/generate_city_venues.py).

Search uses an inverted index (InvertedIndex class) built at load time.
- Free-text queries: tf*idf (BM25-lite) ranking
- Venue name lookup: exact substring (score 1.0) > all-token phrase match (0.6)
  Partial token overlap is excluded to prevent false positives.
"""

import json, re
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADER
# ─────────────────────────────────────────────────────────────────────────────

class CityIndex:
    """Holds all source documents for one city, loaded once."""
    def __init__(self):
        self.yelp:     dict[str, dict] = {}   # venue_id -> yelp doc
        self.posts:    list[dict]       = []   # all blog/forum posts
        self.official: dict[str, dict] = {}   # venue_id -> official site doc
        self.matrix:   dict[str, int]  = {}   # "v1_to_v2" -> minutes
        self.yelp_idx: "InvertedIndex | None" = None   # built after load
        self.post_idx: "InvertedIndex | None" = None   # built after load


# Cache key: (city, arm) → CityIndex.
# Keying on the arm lets us A/B/C-toggle between ablation arms within
# a single process without recomputing the other arm's load.
_city_cache: dict[tuple[str, str], CityIndex] = {}

# Per-city DB path — resolved dynamically per city lookup
def _get_db_path(city: str):
    from scripts.generation.db import get_city_db_path
    return get_city_db_path(city, run_name=_active_run_name)

# Module-level run_name — set by benchmark runner before first tool call
_active_run_name = None

# ─────────────────────────────────────────────────────────────────────────────
# ABLATION ARMS
# ─────────────────────────────────────────────────────────────────────────────
# mock_tools supports a controlled 3-arm ablation so we can isolate the effect
# of the cross-reference trap from the confound of "less context".
#
#   faulty (default)
#       Original noisy environment. incorrect_source docs present and yelp
#       structured fields carry the wrong values. Nothing is touched.
#
#   clean_delete (legacy "clean" mode)
#       1. Yelp structured fields: for every wrong_info row with
#          source_type='yelp', overwrite the corresponding yelp_listings column
#          (e.g. yelp_hours_fri, avg_cost_local) with correct_value BEFORE
#          building the in-memory index.
#       2. Source docs: DROP any source_doc with at least one doc_venue_roles
#          row of role='incorrect_source'. truth_carrier and neutral docs stay.
#       Confound: dropping docs also lowers doc count + total text volume, so
#       any score delta vs faulty mixes "trap removed" with "less context".
#
#   clean_equalvol (NEW — volume-controlled clean)
#       1. Yelp structured fields: healed to ground truth (same as clean_delete).
#       2. Source docs: instead of DROPPING each incorrect_source doc, REPLACE
#          its body with a length-matched NEUTRAL paragraph that removes the
#          false claim about the flawed field WITHOUT asserting the corrected
#          value (stays content-neutral so the doc never becomes a truth
#          carrier). doc_id, author, date, doc_type, title, and approximate
#          length/count are preserved. Net effect: doc count + total text
#          volume ≈ identical to faulty; the ONLY removed variable is the lie.
#       3. Official-site docs are authoritative by design, so untouched.
#
# Both clean arms heal yelp fields identically. The (city, arm) cache key lets
# one process serve all three arms from independently warmed caches.
# ─────────────────────────────────────────────────────────────────────────────
ARMS = ("faulty", "clean_delete", "clean_equalvol")
_arm = "faulty"

def set_run_name(run_name: str | None):
    """Set the active run name for DB path resolution.
    Call before dispatching any tools so _load_city finds the right DB."""
    global _active_run_name
    _active_run_name = run_name

def set_arm(arm: str) -> None:
    """
    Select the ablation arm. One of:
      - 'faulty'         : noisy environment, as originally generated (default).
      - 'clean_delete'   : heal yelp fields + DROP incorrect_source docs.
      - 'clean_equalvol' : heal yelp fields + REPLACE incorrect_source doc
                           bodies with length-matched neutral filler (preserves
                           doc count and text volume; removes only the lie).

    Call before dispatching any tools so _load_city builds the right index.
    """
    global _arm
    if arm not in ARMS:
        raise ValueError(f"Unknown arm '{arm}'. Expected one of {ARMS}.")
    _arm = arm

def get_arm() -> str:
    """Return current ablation arm (test helper)."""
    return _arm

def set_clean_environment(clean: bool) -> None:
    """
    DEPRECATED back-compat alias for set_arm.

    set_clean_environment(True)  -> set_arm('clean_delete')
    set_clean_environment(False) -> set_arm('faulty')

    Prefer set_arm(...) directly; this keeps older callers working unchanged.
    """
    set_arm("clean_delete" if clean else "faulty")

def get_clean_environment() -> bool:
    """Return True iff the active arm is a clean arm (test/back-compat helper)."""
    return _arm in ("clean_delete", "clean_equalvol")

def _load_city_from_db(city: str, db_path: Path, arm: str = "faulty") -> CityIndex:
    """Load city data from SQLite DB.

    Applies the wrong-info handling for the given ablation arm (see set_arm):
      - faulty:         no changes.
      - clean_delete:   heal yelp fields, drop incorrect_source docs.
      - clean_equalvol: heal yelp fields, replace incorrect_source doc bodies
                        with length-matched neutral filler.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    idx = CityIndex()

    clean = arm in ("clean_delete", "clean_equalvol")

    # Clean-mode preload: pull wrong_info corrections and incorrect-source doc
    # IDs into in-memory lookups so the per-row loaders below stay simple.
    yelp_corrections: dict[str, dict[str, str]] = {}   # vid -> {field: correct_value}
    incorrect_source_doc_ids: set[str] = set()
    # For equalvol: doc_id -> list of {affected_field, incorrect_value,
    # correct_value, source_type} so we can neutralise the right sentences.
    doc_flaw_specs: dict[str, list[dict]] = {}
    if clean:
        wi_rows = conn.execute("""
            SELECT venue_id, affected_field, correct_value, source_type
            FROM wrong_info
            WHERE source_type = 'yelp'
        """).fetchall()
        for r in wi_rows:
            d = dict(r)
            yelp_corrections.setdefault(d["venue_id"], {})[d["affected_field"]] = d["correct_value"]

        bad_doc_rows = conn.execute("""
            SELECT DISTINCT doc_id
            FROM doc_venue_roles
            WHERE role = 'incorrect_source'
        """).fetchall()
        incorrect_source_doc_ids = {r["doc_id"] for r in bad_doc_rows}

        if arm == "clean_equalvol":
            flaw_rows = conn.execute("""
                SELECT dvr.doc_id,
                       wi.affected_field, wi.incorrect_value,
                       wi.correct_value, wi.source_type
                FROM doc_venue_roles dvr
                JOIN wrong_info wi ON wi.wrong_info_id = dvr.wrong_info_id
                WHERE dvr.role = 'incorrect_source'
            """).fetchall()
            for r in flaw_rows:
                d = dict(r)
                doc_flaw_specs.setdefault(d["doc_id"], []).append({
                    "affected_field":  d["affected_field"],
                    "incorrect_value": d["incorrect_value"],
                    "correct_value":   d["correct_value"],
                    "source_type":     d["source_type"],
                })

    # ── Yelp listings from yelp_listings + venues + tags ─────────────────────
    yelp_rows = conn.execute("""
        SELECT y.*,
               v.name      AS v_name,
               v.category  AS v_category,
               v.district  AS v_district,
               v.lat, v.lng,
               v.booking_required, v.noise_level, v.traffic_tier,
               v.avg_cost_local, v.price_tier,
               v.total_results, v.yelp_popularity_score,
               v.has_official_site, v.has_wrong_info_planned,
               v.wheelchair_accessible, v.pet_friendly, v.family_friendly,
               v.photography_allowed, v.outside_food_allowed,
               v.reservation_required, v.parking_nearby,
               v.outdoor_sensitivity
        FROM yelp_listings y
        JOIN venues v ON y.venue_id = v.venue_id
        WHERE v.city = ? AND v.page_status = 'verified'
    """, (city,)).fetchall()

    for row in yelp_rows:
        r = dict(row)
        vid = r["venue_id"]

        # Clean-mode: heal yelp_* fields back to correct_value before any
        # downstream reads. wrong_info.affected_field uses the canonical venue
        # column name (e.g. 'hours_fri'); yelp_listings prefixes it (e.g.
        # 'yelp_hours_fri'). We try the prefixed column first, then the bare
        # field, so the heal works for both hours_* and any future non-hour
        # yelp-typed wrong-info entry.
        if clean and vid in yelp_corrections:
            for field, correct in yelp_corrections[vid].items():
                target_col = None
                if field.startswith("hours_") and f"yelp_{field}" in r:
                    target_col = f"yelp_{field}"
                elif field in r:
                    target_col = field
                if target_col is None:
                    continue
                # Cast numeric fields back from text since wrong_info stores
                # all values as TEXT.
                if field in ("avg_cost_local", "recommended_visit_minutes"):
                    try:
                        r[target_col] = (float(correct) if "." in str(correct)
                                         else int(correct))
                    except (TypeError, ValueError):
                        r[target_col] = correct
                elif field in ("booking_required", "reservation_required"):
                    r[target_col] = (int(correct) if str(correct).isdigit() else
                                     (1 if str(correct).lower() in ("true", "1", "yes") else 0))
                else:
                    r[target_col] = correct

        # Fetch tags for this venue
        tags = conn.execute(
            "SELECT tag FROM tags WHERE venue_id = ? AND yelp_visible = 1", (vid,)
        ).fetchall()
        tag_list = [t["tag"] for t in tags]

        # Parse hours JSON from yelp_listing
        hours_reg = {}
        for day in ["mon","tue","wed","thu","fri","sat","sun"]:
            col = f"yelp_hours_{day}"
            val = r.get(col)
            if val:
                # Format: "HH:MM-HH:MM"
                parts = val.split("-") if val else []
                hours_reg[day] = parts if len(parts) == 2 else None
            else:
                hours_reg[day] = None

        doc = {
            "venue_id":       vid,
            "name":           r["v_name"],
            "category":       r["v_category"],
            "district":       r["v_district"],
            "stars":          round(r.get("yelp_popularity_score", 0.5) * 5, 1),
            "review_count":   r.get("total_results", 0),
            "category_tags":  tag_list,
            "partial_labels": [],  # not a DB column — use category_tags from tags table
            "hours_registered": hours_reg,
            "price_tier":     r.get("price_tier"),
            "avg_cost_local":   r.get("avg_cost_local"),
            "last_activity_date": r.get("last_activity_date"),
            "booking_required":   bool(r.get("booking_required")),
            "has_official_site":  bool(r.get("has_official_site")),
            "has_wrong_info_planned": bool(r.get("has_wrong_info_planned")),
            "noise_level":        r.get("noise_level"),
            "wheelchair_accessible": bool(r.get("wheelchair_accessible")),
            "pet_friendly":       bool(r.get("pet_friendly")),
            "family_friendly":    bool(r.get("family_friendly")),
            "photography_allowed": bool(r.get("photography_allowed")),
            "outside_food_allowed": bool(r.get("outside_food_allowed")),
            "reservation_required": bool(r.get("reservation_required")),
            "parking_nearby":     bool(r.get("parking_nearby")),
            "outdoor_sensitivity": r.get("outdoor_sensitivity"),
        }
        idx.yelp[vid] = doc

    # ── Blog and forum posts from source_docs ─────────────────────────────────
    posts = conn.execute("""
        SELECT s.doc_id, s.doc_type, s.title, s.author, s.source_name,
               s.date, s.body, s.mentioned_regulations,
               s.likes, s.saves, s.view_count,
               GROUP_CONCAT(r.venue_id) as venue_ids
        FROM source_docs s
        JOIN doc_venue_refs r ON s.doc_id = r.doc_id
        JOIN venues v ON r.venue_id = v.venue_id
        WHERE v.city = ? AND s.page_status = 'verified'
        GROUP BY s.doc_id
    """, (city,)).fetchall()

    for row in posts:
        r = dict(row)
        body = r["body"] or ""
        if clean and r["doc_id"] in incorrect_source_doc_ids:
            if arm == "clean_delete":
                # Drop any post flagged as incorrect_source for at least one
                # venue. Truth-carrier and neutral docs are retained, so the
                # agent still sees ground-truth coverage of the venue.
                continue
            elif arm == "clean_equalvol":
                # Keep the doc (preserve count + volume) but neutralise the
                # body so it no longer carries the false claim — and does not
                # assert the corrected value either.
                body = _neutralise_doc_body(
                    body, doc_flaw_specs.get(r["doc_id"], []), r["doc_id"]
                )
        venue_ids = r["venue_ids"].split(",") if r["venue_ids"] else []
        post = {
            "doc_id":      r["doc_id"],
            "doc_type":    r["doc_type"],
            "title":       r["title"] or "",
            "author":      r["author"] or "",
            "source":      r["source_name"] or "",
            "date":        r["date"] or "",
            "content":     body,
            "likes":       r.get("likes", 0) or 0,
            "saves":       r.get("saves", 0) or 0,
            "view_count":  r.get("view_count", 0) or 0,
            "venue_ids":   venue_ids,
            "venue_id":    venue_ids[0] if venue_ids else None,
        }
        idx.posts.append(post)

    # ── Official site docs ────────────────────────────────────────────────────
    # Authority-suppression preload (b2): wrong_info rows with
    # suppress_authority=1 mean the official site must STOP being a free oracle
    # for that venue's trapped field. We build a per-venue map of
    #   field -> {"mode": "omit"|"stale", "incorrect_value": <str>}
    # and apply it in tool_get_official_site. When suppress_authority=0
    # everywhere (today's data) this map is empty and output is byte-identical.
    #
    # Mode rule (default = omit): a row is treated as "stale" (official site is
    # itself out of date, return the incorrect_value) when its flaw structure /
    # category signals staleness; otherwise the field is omitted entirely.
    authority_suppression: dict[str, dict[str, dict]] = {}
    try:
        wi_cols = {c[1] for c in conn.execute("PRAGMA table_info(wrong_info)").fetchall()}
    except Exception:
        wi_cols = set()
    if "suppress_authority" in wi_cols:
        has_structure = "structure" in wi_cols
        struct_sel = "structure" if has_structure else "NULL AS structure"
        sup_rows = conn.execute(f"""
            SELECT wi.venue_id, wi.affected_field, wi.incorrect_value,
                   wi.wrong_info_category, {struct_sel}
            FROM wrong_info wi
            JOIN venues v ON wi.venue_id = v.venue_id
            WHERE v.city = ? AND wi.suppress_authority = 1
        """, (city,)).fetchall()
        for sr in sup_rows:
            d = dict(sr)
            mode = "stale" if _authority_suppression_is_stale(
                d.get("structure"), d.get("wrong_info_category")
            ) else "omit"
            authority_suppression.setdefault(d["venue_id"], {})[d["affected_field"]] = {
                "mode":            mode,
                "incorrect_value": d.get("incorrect_value"),
            }

    official_rows = conn.execute("""
        SELECT o.*, v.name, v.category
        FROM official_site_docs o
        JOIN venues v ON o.venue_id = v.venue_id
        WHERE v.city = ? AND o.page_status = 'verified'
    """, (city,)).fetchall()

    for row in official_rows:
        r = dict(row)
        vid = r["venue_id"]

        hours = {}
        for day in ["mon","tue","wed","thu","fri","sat","sun"]:
            col = f"hours_{day}"
            val = r.get(col)
            if val:
                parts = val.split("-")
                hours[day] = parts if len(parts) == 2 else None
            else:
                hours[day] = None

        # Fetch full regulations from venue row
        v_row = conn.execute("SELECT * FROM venues WHERE venue_id=?", (vid,)).fetchone()
        v = dict(v_row) if v_row else {}

        full_regs = {
            "pet_friendly":       bool(v.get("pet_friendly", 0)),
            "wheelchair_accessible": bool(v.get("wheelchair_accessible", 1)),
            "parking_nearby":     bool(v.get("parking_nearby", 0)),
            "age_restriction":    v.get("age_restriction"),
            "dress_code":         v.get("dress_code"),
            "photography_allowed": bool(v.get("photography_allowed", 1)),
            "noise_level":        v.get("noise_level", "moderate"),
            "reservation_required": bool(v.get("reservation_required", 0)),
            "outside_food_allowed": bool(v.get("outside_food_allowed", 0)),
            "family_friendly":    bool(v.get("family_friendly", 1)),
        }

        # Fetch all tags for full_labels
        all_tags = conn.execute(
            "SELECT tag FROM tags WHERE venue_id = ?", (vid,)
        ).fetchall()

        # Sync full_labels with the authoritative structured fields.
        # Wrong-info planting can leave stale booking tags in the tags table
        # that contradict booking_required — remove the conflicting tag so
        # get_official_site doesn't return self-contradicting authoritative data.
        booking_req = bool(v.get("booking_required", 0))
        raw_labels = [t["tag"] for t in all_tags]
        if not booking_req:
            # Walk-in venue — strip any reservation-required tags
            raw_labels = [l for l in raw_labels if l != "reservation-required"]
        else:
            # Booking-required venue — strip any no-reservations tags
            raw_labels = [l for l in raw_labels if l != "no-reservations"]

        doc = {
            "venue_id":               vid,
            "name":                   r["name"],
            "url":                    r.get("url", ""),
            "hours":                  hours,
            "ticket_availability":    json.loads(r.get("ticket_availability") or "{}"),
            "booking_required":       booking_req,
            "full_regulations":       full_regs,
            "full_labels":            raw_labels,
            "recommended_visit_minutes": v.get("recommended_visit_minutes"),
            "avg_cost_local":         v.get("avg_cost_local"),
            # Event info (populated by A9)
            "active_event":           json.loads(v.get("active_event") or "null"),
            # b2: authority-suppression spec for this venue (empty when none).
            "_authority_suppression": authority_suppression.get(vid, {}),
        }
        idx.official[vid] = doc

    # ── Travel matrix from travel_matrix table ────────────────────────────────
    matrix_rows = conn.execute(
        "SELECT venue_id_a, venue_id_b, walk_minutes, transit_minutes, cycling_minutes "
        "FROM travel_matrix WHERE city=?",
        (city,)
    ).fetchall()
    for row in matrix_rows:
        r = dict(row)
        entry = {
            "walking": round(float(r["walk_minutes"] or 0), 1),
            "transit": round(float(r["transit_minutes"] or 0), 1),
            "cycling": round(float(r["cycling_minutes"] or 0), 1),
        }
        key = f"{r['venue_id_a']}_to_{r['venue_id_b']}"
        idx.matrix[key] = entry
        key2 = f"{r['venue_id_b']}_to_{r['venue_id_a']}"
        idx.matrix[key2] = entry

    conn.close()
    return idx


def _load_city(city: str) -> CityIndex:
    city = city.lower()
    cache_key = (city, _arm)
    if cache_key in _city_cache:
        return _city_cache[cache_key]

    _city_db = _get_db_path(city)
    if not _city_db.exists():
        raise FileNotFoundError(
            f"No city DB for '{city}' at {_city_db}. "
            f"Run: python scripts/generation/generate_city_venues.py --city {city}"
        )
    idx = _load_city_from_db(city, _city_db, arm=_arm)
    if not idx.yelp:
        raise FileNotFoundError(
            f"City DB for '{city}' contains no verified venues at {_city_db}. "
            f"Re-run venue generation."
        )
    _build_indexes(idx)
    _city_cache[cache_key] = idx
    return idx


# ─────────────────────────────────────────────────────────────────────────────
# NEUTRAL DOC-BODY REPLACEMENT (clean_equalvol arm)
# ─────────────────────────────────────────────────────────────────────────────
# Goal: produce a body that (a) no longer carries the false claim about the
# flawed field and (b) does NOT assert the corrected value either — while
# keeping length within ±10% of the original so total text volume matches the
# faulty arm. Fully deterministic (seeded from doc_id), no LLM, no DB writes.
#
# Strategy:
#   1. Split into sentences.
#   2. For each flaw, score every sentence on field-specific keyword cues
#      (hours -> open/close/until/am/pm/hours; cost -> $/price/cost/cheap;
#      booking/reservation -> reserve/walk-in/book) plus any literal occurrence
#      of the incorrect or correct value. Drop the highest-scoring sentence(s)
#      that actually carry a cue — these are the claim-bearing lines.
#   3. Re-pad to the original character length (±10%) with neutral,
#      venue-appropriate filler that never mentions the flawed field, picking
#      filler deterministically from a seed derived from the doc_id.
#   4. Final safety pass: if the incorrect or corrected value string still
#      appears anywhere, drop those sentences too.

_HOURS_CUES = re.compile(
    r"\b(open|opens|opening|close|closes|closing|until|till|til|hours?|"
    r"am|pm|a\.m\.|p\.m\.|o'?clock|midnight|noon|24[\s-]?hours?|"
    r"\d{1,2}\s*(?::\d{2})?\s*(?:am|pm)|\d{1,2}\s*(?::\d{2}))\b",
    re.IGNORECASE,
)
_COST_CUES = re.compile(
    r"(\$\s?\d|\bdollars?\b|\bbucks?\b|\bprice[ds]?\b|\bpricing\b|\bcost[s]?\b|"
    r"\bcheap\b|\bexpensive\b|\baffordable\b|\bvalue\b|\bspend\b|\bpay\b|"
    r"\bbudget\b|\bcharge[ds]?\b)",
    re.IGNORECASE,
)
_BOOKING_CUES = re.compile(
    r"\b(reserv\w*|book\w*|walk[\s-]?in|no[\s-]?reservation\w*|"
    r"first[\s-]?come|queue|line)\b",
    re.IGNORECASE,
)
_VISITMIN_CUES = re.compile(
    r"\b(minutes?|hours?|spend|takes?|allow|plan\w*|visit|tour|"
    r"\d+\s*(?:min|minute|hour|hr))\b",
    re.IGNORECASE,
)

_FIELD_CUES = {
    "hours_mon": _HOURS_CUES, "hours_tue": _HOURS_CUES, "hours_wed": _HOURS_CUES,
    "hours_thu": _HOURS_CUES, "hours_fri": _HOURS_CUES, "hours_sat": _HOURS_CUES,
    "hours_sun": _HOURS_CUES,
    "avg_cost_local": _COST_CUES,
    "booking_required": _BOOKING_CUES, "reservation_required": _BOOKING_CUES,
    "recommended_visit_minutes": _VISITMIN_CUES,
}

# Neutral, venue-appropriate filler. Deliberately says nothing about hours,
# price, booking, or visit length — so it cannot become a truth-carrier for any
# flawed field. Used to re-pad length after claim-bearing sentences are cut.
_NEUTRAL_FILLER = [
    "The atmosphere has that lived-in, unhurried quality you only find in places that have been around a while.",
    "It is the kind of spot locals fold into their routine without making a fuss about it.",
    "The staff move with an easy efficiency that comes from doing the same thing well, day after day.",
    "There is a steady hum of conversation that makes the room feel comfortable rather than crowded.",
    "Regulars and first-timers blend together here, and nobody seems out of place.",
    "The details are understated, which somehow makes the whole experience feel more genuine.",
    "You get the sense that the people here care more about getting it right than about appearances.",
    "It rewards the kind of visitor who is happy to slow down and take the place on its own terms.",
    "The neighbourhood around it adds to the charm, full of small shops and the ordinary rhythm of the day.",
    "Word of mouth has clearly kept this place going, and it is easy to see why once you have been.",
    "Nothing about it feels manufactured, and that authenticity is a big part of the appeal.",
    "It is the sort of place you end up recommending to friends almost before you realise you are doing it.",
]


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, preserving the trailing whitespace/newlines
    that follow each one so re-joining reproduces the original spacing."""
    # Keep the delimiter (.!?) and any following whitespace attached to the
    # sentence so a join of the kept pieces reads naturally.
    parts = re.findall(r".*?(?:[.!?]+\s*|\Z)", text, flags=re.DOTALL)
    return [p for p in parts if p != ""]


def _seeded_filler_order(doc_id: str) -> list[str]:
    """Deterministic shuffle of the filler bank seeded from doc_id, so docs
    don't all pad with the same opening sentence but reruns are reproducible."""
    import random
    rng = random.Random(doc_id)
    pool = list(_NEUTRAL_FILLER)
    rng.shuffle(pool)
    return pool


def _value_variants(value) -> list[str]:
    """Plain-text forms of a structured value worth scanning a sentence for.
    e.g. 18.0 -> ['18.0', '18'];  '06:00-18:00' -> the raw string + parts."""
    out = []
    if value is None:
        return out
    s = str(value).strip()
    if not s:
        return out
    out.append(s)
    # numeric: also try the int form (18.0 -> 18) since prose drops decimals
    try:
        f = float(s)
        if f.is_integer():
            out.append(str(int(f)))
    except (TypeError, ValueError):
        pass
    return out


def _neutralise_doc_body(body: str, flaw_specs: list[dict], doc_id: str) -> str:
    """Return a length-matched neutral version of `body` with claim-bearing
    sentences removed. Used only by the clean_equalvol arm.

    Deterministic: same (body, flaw_specs, doc_id) always yields the same text.
    """
    if not body or not flaw_specs:
        return body

    orig_len = len(body)
    sentences = _split_sentences(body)

    # ── 1. Score each sentence against every flaw and pick the ones to drop ──
    drop_idx: set[int] = set()
    for spec in flaw_specs:
        field = spec.get("affected_field", "")
        cue = _FIELD_CUES.get(field)
        literals = (_value_variants(spec.get("incorrect_value"))
                    + _value_variants(spec.get("correct_value")))

        scored = []
        for i, sent in enumerate(sentences):
            score = 0
            low = sent.lower()
            if cue and cue.search(sent):
                score += 2
            for lit in literals:
                if lit and lit.lower() in low:
                    score += 3
            if score > 0:
                scored.append((score, i))

        if not scored:
            continue
        # Drop the single best claim-bearing sentence for this flaw. If several
        # tie at the top score, drop all of them (the lie may be split across
        # adjacent lines, e.g. "It's $7. The $7 platter...").
        scored.sort(key=lambda x: (-x[0], x[1]))
        top_score = scored[0][0]
        for score, i in scored:
            if score == top_score:
                drop_idx.add(i)

    kept = [s for i, s in enumerate(sentences) if i not in drop_idx]

    # ── 2. Safety pass BEFORE padding: drop any kept sentence that still
    # contains an incorrect/correct value literal, so a stray prose mention of
    # the value can't survive. Done first so padding compensates for everything
    # removed and the ±10% length target holds.
    bad_literals = []
    for spec in flaw_specs:
        bad_literals += _value_variants(spec.get("incorrect_value"))
        bad_literals += _value_variants(spec.get("correct_value"))
    if bad_literals:
        kept = [
            s for s in kept
            if not any(lit and lit.lower() in s.lower() for lit in bad_literals)
        ]

    new_body = "".join(kept)

    # ── 3. Re-pad toward the original length with neutral filler ────────────
    filler_pool = _seeded_filler_order(doc_id)
    fi = 0
    # Aim within -10% of the original length (don't overshoot past +10%).
    target_lo = int(orig_len * 0.90)
    while len(new_body) < target_lo and filler_pool:
        sep = "" if (not new_body or new_body.endswith((" ", "\n"))) else " "
        candidate = sep + filler_pool[fi % len(filler_pool)]
        if len(new_body) + len(candidate) > int(orig_len * 1.10):
            break
        new_body += candidate
        fi += 1
        # Cycle the pool; after one full pass keep appending to reach length.
        if fi % len(filler_pool) == 0:
            filler_pool = _seeded_filler_order(doc_id + str(fi))

    return new_body.strip() + ("\n" if body.endswith("\n") else "")


def _build_indexes(idx: CityIndex) -> None:
    """Build inverted indexes for a loaded CityIndex."""
    idx.yelp_idx = InvertedIndex()
    for vid, doc in idx.yelp.items():
        # Build searchable text from name, category, tags, and regulations
        reg_tokens = []
        if doc.get("wheelchair_accessible"):
            reg_tokens.append("wheelchair accessible step-free")
        if doc.get("pet_friendly"):
            reg_tokens.append("pet friendly dog")
        if doc.get("family_friendly"):
            reg_tokens.append("family friendly children kids")
        if doc.get("photography_allowed"):
            reg_tokens.append("photography allowed photos")
        if doc.get("outside_food_allowed"):
            reg_tokens.append("outside food allowed picnic")
        if doc.get("reservation_required"):
            reg_tokens.append("reservation required booking")
        if doc.get("parking_nearby"):
            reg_tokens.append("parking nearby")
        if doc.get("noise_level"):
            reg_tokens.append(f"{doc['noise_level']} noise")
        if doc.get("outdoor_sensitivity"):
            reg_tokens.append(f"{doc['outdoor_sensitivity']} outdoor")

        text = " ".join(filter(None, [
            doc.get("name", "") or "",
            doc.get("category", "") or "",
            " ".join(t for t in (doc.get("category_tags") or []) if t),
            " ".join(t for t in (doc.get("partial_labels") or []) if t),
            " ".join(reg_tokens),
        ]))
        idx.yelp_idx.add(vid, text, doc)

    idx.post_idx = InvertedIndex()
    for post in idx.posts:
        text = f"{post.get('title','') or ''} {post.get('content','') or ''}"
        idx.post_idx.add(post["doc_id"], text, post)


# ─────────────────────────────────────────────────────────────────────────────
# INVERTED INDEX
# ─────────────────────────────────────────────────────────────────────────────

import math

STOP_WORDS = {
    "i","a","an","the","in","on","at","to","for","of","and","or","is","are",
    "was","what","how","any","that","this","with","it","its","be","do","did",
    "my","we","me","us","our","you","your","he","she","they","their","s","t"
}

def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()

def _tokenise(text: str) -> list[str]:
    return [t for t in _normalise(text).split() if t not in STOP_WORDS and len(t) > 1]


class InvertedIndex:
    """
    Token-level inverted index over a corpus of documents.
    Built once at city-load time; used by all search tools.

    Supports:
      ranked_search(query)    -- BM25-lite tf*idf for free-text queries
      venue_name_search(name) -- strict: exact substring > all-token match
                                 partial token overlap excluded (no false positives)
    """

    def __init__(self):
        self._index:  dict[str, set[str]] = {}
        self._docs:   dict[str, dict]     = {}
        self._n_docs: int = 0

    def add(self, doc_id: str, text: str, doc: dict):
        tokens = _tokenise(text)
        self._docs[doc_id] = {
            "tokens":    tokens,
            "token_set": set(tokens),
            "text_norm": _normalise(text),
            "doc":       doc,
        }
        self._n_docs += 1
        for tok in set(tokens):
            self._index.setdefault(tok, set()).add(doc_id)

    def ranked_search(self, query: str) -> list[tuple[float, str]]:
        q_tokens = _tokenise(query)
        if not q_tokens:
            return []
        candidates: set[str] = set()
        for tok in q_tokens:
            candidates |= self._index.get(tok, set())
        scores: dict[str, float] = {}
        for doc_id in candidates:
            entry = self._docs[doc_id]
            doc_tokens = entry["tokens"]
            doc_len = len(doc_tokens) or 1
            score = 0.0
            for tok in q_tokens:
                tf  = doc_tokens.count(tok) / doc_len
                df  = len(self._index.get(tok, {1}))
                idf = math.log((self._n_docs + 1) / (df + 1)) + 1
                score += tf * idf
            scores[doc_id] = score
        # Returns (score, doc_id) sorted descending
        return sorted(((s, did) for did, s in scores.items()), key=lambda x: -x[0])

    def venue_name_search(self, venue_name: str) -> list[tuple[float, str]]:
        name_norm   = _normalise(venue_name)
        name_tokens = set(_tokenise(venue_name))
        if not name_tokens:
            return []
        candidates: set[str] = set()
        for tok in name_tokens:
            candidates |= self._index.get(tok, set())
        scored = []
        for doc_id in candidates:
            entry = self._docs[doc_id]
            if name_norm in entry["text_norm"]:
                scored.append((1.0, doc_id))
            elif name_tokens.issubset(entry["token_set"]):
                scored.append((0.6, doc_id))
            # partial overlap only -> excluded
        # Returns (score, doc_id) sorted descending
        return sorted(scored, key=lambda x: -x[0])

    def get_doc(self, doc_id: str) -> Optional[dict]:
        entry = self._docs.get(doc_id)
        return entry["doc"] if entry else None


def _recency_boost(date_str: str) -> float:
    try:
        return 0.2 if int(date_str[:4]) >= 2024 else 0.0
    except Exception:
        return 0.0

def _pop_boost(post: dict) -> float:
    import math
    likes      = post.get("likes", 0)
    saves      = post.get("saves", 0)
    view_count = post.get("view_count", 0)
    # Log-scale each signal to compress range naturally.
    # Saves weighted 2x (strongest intent signal — bookmarked for actual use)
    # Likes weighted 1.5x (endorsement)
    # Views weighted 0.5x (reach signal, weaker quality indicator)
    raw = (
        2.0 * math.log1p(saves) +
        1.5 * math.log1p(likes) +
        0.5 * math.log1p(view_count)
    )
    # Cap at 0.30 — keeps popularity subordinate to BM25 text relevance
    return min(raw / 100, 0.30)


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 1: search_yelp
# ─────────────────────────────────────────────────────────────────────────────

def tool_search_yelp(query: str, city: str,
                     category: Optional[str] = None,
                     top_k: int = 6) -> dict:
    idx = _load_city(city)

    # Get scored candidates from inverted index
    if query.strip():
        scored_pairs = idx.yelp_idx.ranked_search(query)
    else:
        # No query — return all, sorted by stars
        scored_pairs = [(0.0, vid) for vid in idx.yelp]

    results = []
    for score, vid in scored_pairs:
        doc = idx.yelp[vid]
        # Apply category filter after scoring — matches venue category, not tags
        if category and doc.get("category", "").lower() != category.lower():
            continue
        results.append({
            "venue_id": vid,
            "name": doc.get("name", ""),
            "category": doc.get("category", ""),
            "district": doc.get("district", ""),
            "stars": doc.get("stars"),
            "review_count": doc.get("review_count", 0),
            "category_tags": doc.get("category_tags", []),
            "partial_labels": doc.get("partial_labels", []),
            "hours": doc.get("hours_registered", {}),
            "official_url": doc.get("official_url"),
            "note": doc.get("note", "")
        })
        if len(results) >= top_k:
            break

    return {
        "tool": "search_yelp",
        "query": query, "city": city, "category_filter": category,
        "count": len(results), "results": results
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 2: search_blogs_and_forums
# ─────────────────────────────────────────────────────────────────────────────

def tool_search_blogs_and_forums(query: str, city: str,
                                  venue_name: Optional[str] = None,
                                  top_k: int = 5) -> dict:
    """
    Posts live in data/sources/{city}/blogs_and_forums/ as individual files.
    Each post may mention multiple venues (venues_mentioned list).
    venue_name uses strict matching (exact substring > all-token phrase).
    Free-text query uses tf*idf ranking. Partial token overlap is excluded.
    """
    idx = _load_city(city)

    if venue_name:
        scored_pairs = idx.post_idx.venue_name_search(venue_name)
    else:
        scored_pairs = idx.post_idx.ranked_search(query)

    boosted = []
    for base_score, doc_id in scored_pairs:
        post = idx.post_idx.get_doc(doc_id)
        if post is None:
            continue
        final = base_score + _recency_boost(post.get("date","")) + _pop_boost(post)
        boosted.append((final, post))

    boosted.sort(key=lambda x: -x[0])
    results = []
    for _, post in boosted[:top_k]:
        results.append({
            "doc_id":           post.get("doc_id"),
            "doc_type":         post.get("doc_type", "blog"),
            "source":           post.get("source", ""),
            "author":           post.get("author", ""),
            "date":             post.get("date", ""),
            "likes":            post.get("likes", 0),
            "saves":            post.get("saves", 0),
            "title":            post.get("title", ""),
            "content":          post.get("content", ""),
            "venues_mentioned": post.get("venue_ids", post.get("venues_mentioned", []))
        })

    return {
        "tool": "search_blogs_and_forums",
        "query": query, "venue_name_filter": venue_name, "city": city,
        "count": len(results), "results": results
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 3: get_official_site
# ─────────────────────────────────────────────────────────────────────────────

# Set of full_regulations keys; an affected_field naming one of these is
# suppressed inside the nested full_regulations dict rather than at top level.
_REGULATION_FIELDS = frozenset({
    "pet_friendly", "wheelchair_accessible", "parking_nearby", "age_restriction",
    "dress_code", "photography_allowed", "noise_level", "reservation_required",
    "outside_food_allowed", "family_friendly",
})

_HOURS_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _authority_suppression_is_stale(structure, category) -> bool:
    """
    Decide the suppression MODE for a wrong_info row.

    Returns True  -> 'stale'  : the official site itself is out of date; it
                                 still lists the field, but with the (wrong)
                                 incorrect_value.
    Returns False -> 'omit'   : the official site simply doesn't list the field.

    Default is OMIT. We only treat a row as stale when its flaw structure /
    category signals temporal staleness (the natural "site is out of date"
    semantics): structure == 'stale_authority' or category == 'temporal_decay'.
    """
    s = (str(structure).strip().lower() if structure is not None else "")
    c = (str(category).strip().lower() if category is not None else "")
    return s == "stale_authority" or c == "temporal_decay"


def _parse_official_hours_value(raw):
    """Parse a stored 'HH:MM-HH:MM' hours string into the [open, close] list
    form used by the official-site response (mirrors index-build parsing)."""
    if not raw:
        return None
    parts = str(raw).split("-")
    return parts if len(parts) == 2 else None


def _apply_authority_suppression(response: dict, suppression: dict) -> None:
    """
    Apply b2 authority suppression to a get_official_site response in place.

    `suppression` maps affected_field -> {"mode": "omit"|"stale",
    "incorrect_value": <str>}. For each suppressed field:
      - omit:  remove the field from the response (hours day dropped, nested
               regulation key dropped, or top-level key dropped).
      - stale: replace the authoritative value with incorrect_value.

    Fields not present in `suppression` are untouched, so an empty map (today's
    data) leaves the response byte-identical.
    """
    if not suppression:
        return

    for field, spec in suppression.items():
        mode = spec.get("mode", "omit")
        incorrect = spec.get("incorrect_value")

        # Hours fields: hours_<day>
        if isinstance(field, str) and field.startswith("hours_"):
            day = field[len("hours_"):]
            if day in _HOURS_DAYS and isinstance(response.get("hours"), dict):
                if mode == "stale":
                    response["hours"][day] = _parse_official_hours_value(incorrect)
                else:  # omit — site doesn't list this day's hours
                    response["hours"].pop(day, None)
            continue

        # Regulation fields live in the nested full_regulations dict
        if field in _REGULATION_FIELDS and isinstance(response.get("full_regulations"), dict):
            if mode == "stale":
                response["full_regulations"][field] = _coerce_official_value(
                    field, incorrect, response["full_regulations"].get(field)
                )
            else:
                response["full_regulations"].pop(field, None)
            continue

        # Top-level scalar fields (booking_required, avg_cost_local,
        # recommended_visit_minutes, ...)
        if field in response:
            if mode == "stale":
                response[field] = _coerce_official_value(field, incorrect, response[field])
            else:
                response.pop(field, None)


def _coerce_official_value(field: str, raw, current):
    """Coerce a stored incorrect_value string toward the type of the field's
    current authoritative value, so a stale value reads naturally (bool/int/
    float/str). Falls back to the raw string when coercion is ambiguous."""
    if raw is None:
        return None
    if isinstance(current, bool):
        return str(raw).strip().lower() in ("1", "true", "yes", "y")
    if isinstance(current, int):
        try:
            return int(float(str(raw).strip()))
        except (ValueError, TypeError):
            return raw
    if isinstance(current, float):
        try:
            return float(str(raw).strip())
        except (ValueError, TypeError):
            return raw
    return raw


def tool_get_official_site(venue_id: str, city: str, date: str = None) -> dict:
    """
    Get official site data for a venue.

    Args:
        venue_id: venue identifier
        city:     city name
        date:     optional ISO date string (YYYY-MM-DD). When provided, returns
                  date-specific hours (holiday overrides), ticket availability
                  for that specific date, and any active event on that date.
                  Always provide date when planning for a specific window.
    """
    idx = _load_city(city)

    if venue_id not in idx.yelp:
        return {"error": f"Unknown venue_id: '{venue_id}'. Use search_yelp to find valid IDs."}

    name = idx.yelp[venue_id].get("name", venue_id)

    if venue_id not in idx.official:
        return {
            "venue_id": venue_id, "name": name,
            "has_official_site": False,
            "note": "This venue has no official website. "
                    "Use Yelp hours and blogs/forums for information."
        }

    doc = idx.official[venue_id]

    # b2: copy the mutable sub-dicts so authority suppression (and date
    # enrichment) can edit the response without corrupting the cached doc.
    suppression = doc.get("_authority_suppression") or {}

    # Base response
    response = {
        "tool":           "get_official_site",
        "venue_id":       venue_id,
        "name":           name,
        "has_official_site": True,
        "url":            doc.get("url", ""),
        "hours":          dict(doc.get("hours", {})),
        "ticket_availability": doc.get("ticket_availability", {}),
        "booking_required": doc.get("booking_required", False),
        "avg_cost_local":   doc.get("avg_cost_local"),
        "full_regulations": dict(doc.get("full_regulations", {})),
        "full_labels":    doc.get("full_labels", []),
        "recommended_visit_minutes": doc.get("recommended_visit_minutes"),
        "note": "This data is authoritative. "
                "Check ticket_availability for your specific travel dates.",
    }

    # b2: stop the official site from being a free oracle for trapped fields.
    # No-op (and copies above are content-identical) when nothing is suppressed.
    _apply_authority_suppression(response, suppression)

    # Date-specific enrichment
    if date:
        response["queried_date"] = date
        _enrich_with_date(response, venue_id, city, date)

    return response


def _enrich_with_date(response: dict, venue_id: str, city: str, date: str) -> None:
    """
    Enrich an official_site response with date-specific data from SQLite.
    Modifies response in place.
    """
    _city_db = _get_db_path(city)
    if not _city_db.exists():
        return

    try:
        import sqlite3
        conn = sqlite3.connect(_city_db)
        conn.row_factory = sqlite3.Row

        # Hours override for this specific date
        override = conn.execute("""
            SELECT override_type, hours, reason
            FROM hours_overrides
            WHERE venue_id = ? AND date = ?
        """, (venue_id, date)).fetchone()

        if override:
            o = dict(override)
            if o["override_type"] == "closed":
                response["date_specific_hours"] = None
                response["date_note"] = f"CLOSED on {date}: {o.get('reason', '')}"
            elif o["override_type"] == "modified":
                response["date_specific_hours"] = o["hours"]
                response["date_note"] = f"Modified hours on {date}: {o.get('reason', '')}"

        # Ticket availability for this date
        tickets = conn.execute("""
            SELECT slots_available, sold_out, price_local
            FROM ticket_availability
            WHERE venue_id = ? AND date = ?
        """, (venue_id, date)).fetchone()

        if tickets:
            t = dict(tickets)
            response["ticket_availability"] = {
                date: {
                    "slots_available": t["slots_available"],
                    "sold_out":        bool(t["sold_out"]),
                    "price_local":       t["price_local"],
                }
            }

        # Active event on this date
        event = conn.execute("""
            SELECT name, description, start_date, end_date,
                   affects_hours, affects_access, sold_out
            FROM events
            WHERE venue_id = ? AND start_date <= ? AND end_date >= ?
        """, (venue_id, date, date)).fetchone()

        if event:
            e = dict(event)
            response["active_event"] = {
                "name":           e["name"],
                "description":    e.get("description", ""),
                "dates":          f"{e['start_date']} – {e['end_date']}",
                "affects_hours":  bool(e.get("affects_hours", 0)),
                "affects_access": bool(e.get("affects_access", 0)),
                "sold_out":       bool(e.get("sold_out", 0)),
            }

        conn.close()
    except Exception:
        pass  # Don't break if DB query fails


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 4: get_travel_time
# ─────────────────────────────────────────────────────────────────────────────

def tool_get_travel_time(from_venue_id: str, to_venue_id: str, city: str) -> dict:
    idx = _load_city(city)
    all_ids = set(idx.yelp.keys())

    for vid, param in [(from_venue_id, "from_venue_id"), (to_venue_id, "to_venue_id")]:
        if vid not in all_ids:
            return {"error": f"Unknown venue_id for '{param}': '{vid}'. "
                             f"Use search_yelp to find valid venue IDs."}

    if from_venue_id == to_venue_id:
        return {"from_venue_id": from_venue_id, "to_venue_id": to_venue_id,
                "travel_time_minutes": 0, "mode": "walking"}

    key = f"{from_venue_id}_to_{to_venue_id}"
    if key not in idx.matrix:
        return {"error": f"No route data between '{from_venue_id}' and '{to_venue_id}'"}

    entry = idx.matrix[key]
    # entry is either dict {"walking": N, "transit": N, "cycling": N} or legacy int
    if isinstance(entry, dict):
        return {
            "tool": "get_travel_time",
            "from_venue_id": from_venue_id,
            "from_venue_name": idx.yelp[from_venue_id].get("name", from_venue_id),
            "to_venue_id": to_venue_id,
            "to_venue_name": idx.yelp[to_venue_id].get("name", to_venue_id),
            "walking_minutes": entry.get("walking", 0),
            "transit_minutes": entry.get("transit", 0),
            "cycling_minutes": entry.get("cycling", 0),
        }
    else:
        return {
            "tool": "get_travel_time",
            "from_venue_id": from_venue_id,
            "from_venue_name": idx.yelp[from_venue_id].get("name", from_venue_id),
            "to_venue_id": to_venue_id,
            "to_venue_name": idx.yelp[to_venue_id].get("name", to_venue_id),
            "walking_minutes": int(entry),
            "transit_minutes": 0,
            "cycling_minutes": 0,
        }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL SCHEMAS  (passed to the LLM)
# ─────────────────────────────────────────────────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "search_yelp",
        "description": (
            "Search the Yelp-style directory for venues in a city. "
            "The query matches against venue names, category tags (e.g. 'hidden-gem', "
            "'fine-dining', 'craft-beer', 'romantic'), and regulation keywords "
            "(e.g. 'wheelchair accessible', 'pet friendly', 'quiet'). "
            "Use the optional 'category' filter to restrict by venue type "
            "(restaurant, cafe, museum, etc.) — this filters by venue category, "
            "not tags. Returns names, stars, category tags, staff-registered hours, "
            "and official URL where available. "
            "Hours may occasionally be outdated — verify with get_official_site or "
            "search_blogs_and_forums for venues where accuracy matters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":    {"type": "string", "description": "Search terms — matches names, tags, and regulations. E.g. 'hidden gem restaurant', 'wheelchair accessible museum', 'craft beer'"},
                "city":     {"type": "string", "description": "City name e.g. 'london'"},
                "category": {"type": "string",
                             "enum": ["restaurant","cafe","museum","park","attraction","bar","neighbourhood"],
                             "description": "Optional category filter"},
                "top_k":    {"type": "integer", "default": 6, "description": "Max results"}
            },
            "required": ["query", "city"]
        }
    },
    {
        "name": "search_blogs_and_forums",
        "description": (
            "Search travel blog posts and forum threads. "
            "Posts cover multiple venues — a single post may mention several places from a trip. "
            "Use 'venue_name' to find all posts mentioning a specific place. "
            "Use 'query' for broader discovery. "
            "Forum posts often contain corrections to outdated hours or prices, "
            "and mention regulations like pet policies or dress codes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":      {"type": "string", "description": "Free text search e.g. 'hidden gem cafe London'"},
                "city":       {"type": "string"},
                "venue_name": {"type": "string", "description": "Venue name to find all posts mentioning it"},
                "top_k":      {"type": "integer", "default": 5}
            },
            "required": ["city"]
        }
    },
    {
        "name": "get_official_site",
        "description": (
            "Fetch official website data for a venue. "
            "Available for museums, major attractions, and bookable restaurants. "
            "Returns authoritative hours, ticket availability by date, "
            "full regulations, and full label list. "
            "Ticket availability is ONLY available here — "
            "always call this for venues requiring booking or timed entry. "
            "ALWAYS provide the date parameter when planning for a specific date — "
            "hours and availability may differ significantly on holidays, "
            "festival days, and seasonal events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "venue_id": {"type": "string", "description": "Exact venue ID from search results"},
                "city":     {"type": "string"},
                "date":     {"type": "string", "description": "ISO date string YYYY-MM-DD. Provide when planning for a specific date to get holiday hours, ticket availability, and active events."}
            },
            "required": ["venue_id", "city"]
        }
    },
    {
        "name": "get_travel_time",
        "description": (
            "Get travel time in minutes between two venues for walking, transit, and cycling. "
            "Returns all three modes so you can choose the best option. "
            "Requires exact venue_id values from search results — "
            "passing venue names will return an error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_venue_id": {"type": "string", "description": "Venue ID of starting point"},
                "to_venue_id":   {"type": "string", "description": "Venue ID of destination"},
                "city":          {"type": "string"}
            },
            "required": ["from_venue_id", "to_venue_id", "city"]
        }
    },
    {
        "name": "THINK",
        "description": (
            "Log your reasoning — use freely when uncertain, planning research strategy, "
            "or evaluating conflicting sources. Does NOT count toward your tool call budget. "
            "Returns immediately with no side effects."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "Your reasoning, plan, or decision rationale"
                }
            },
            "required": ["thought"]
        }
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_PARAMS = {
    "search_yelp":               ["query", "city"],
    "search_blogs_and_forums":   ["city"],
    "get_official_site":         ["venue_id", "city"],
    "get_travel_time":           ["from_venue_id", "to_venue_id", "city"],
    "THINK":                     ["thought"],
}

def dispatch_tool(tool_name: str, tool_input: dict) -> dict:
    handlers = {
        "search_yelp":             lambda i: tool_search_yelp(
            i["query"], i["city"], i.get("category"), i.get("top_k", 6)),
        "search_blogs_and_forums": lambda i: tool_search_blogs_and_forums(
            i.get("query", ""), i["city"], i.get("venue_name"), i.get("top_k", 5)),
        "get_official_site":       lambda i: tool_get_official_site(
            i["venue_id"], i["city"], i.get("date")),
        "get_travel_time":         lambda i: tool_get_travel_time(
            i["from_venue_id"], i["to_venue_id"], i["city"]),
    }

    # THINK: free reasoning tool — logged to think_log, not tool_call_log
    if tool_name == "THINK":
        return {"status": "logged", "message": "Reasoning recorded."}

    if tool_name not in handlers:
        return {"error": f"Unknown tool: '{tool_name}'. Available: {list(handlers.keys())}"}

    for param in REQUIRED_PARAMS.get(tool_name, []):
        if param not in tool_input:
            return {"error": f"Missing required parameter: '{param}' for tool '{tool_name}'"}

    try:
        return handlers[tool_name](tool_input)
    except FileNotFoundError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Tool execution error: {str(e)}"}