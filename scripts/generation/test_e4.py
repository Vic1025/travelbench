"""
scripts/generation/test_e4.py

Tests for E4 — multi-venue document generation.

Covers:
  pool_utils: all 10 functions importable and correct
  doc_agent:  _validate_briefs (pass + 5 failure modes)
              _dispatch_doc_tool for each tool
              run_doc_planning_agent dry-run (no API key → None)
  generate_multi_venue_docs:
              _assign_popularity ranges
              _assign_date stale vs fresh
              _write_doc_to_db writes correct rows + roles
              _build_phase2_prompt structure
  mock_tools: likes/saves/view_count present in loaded posts

Run: python scripts/generation/test_e4.py
"""

import sys
import json
import math
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))
        FAIL += 1


def make_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    init_db(db)
    return db


def insert_city(conn, city="london"):
    conn.execute("""
        INSERT OR IGNORE INTO city_config
            (city, display_name, country, centre_lat, centre_lng,
             radius_km, local_cuisine_label, task_dates)
        VALUES (?,?,?,?,?,?,?,?)
    """, (city, city.title(), "UK", 51.5, -0.1, 10.0, "British", "[]"))
    conn.commit()


def insert_venue(conn, city, vid, name, category="museum", lat=51.5, lng=-0.1,
                  traffic_tier="mid", has_wrong_info=0, tags=None):
    conn.execute("""
        INSERT OR IGNORE INTO venues
            (venue_id, city, name, category, district, lat, lng,
             avg_cost_local, price_tier, recommended_visit_minutes,
             booking_required, has_official_site, outdoor_sensitivity,
             recommended_pace, traffic_tier, total_results, yelp_popularity_score,
             pet_friendly, wheelchair_accessible, parking_nearby,
             photography_allowed, noise_level, reservation_required,
             outside_food_allowed, family_friendly, food_available,
             has_wrong_info_planned, page_status,
             hours_mon, hours_tue, hours_wed, hours_thu,
             hours_fri, hours_sat, hours_sun)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (vid, city, name, category, "Shoreditch", lat, lng,
          20.0, "mid", 90, 0, 1, "indoor", "moderate",
          traffic_tier, 100, 0.5, 1, 1, 0, 1, "moderate", 0, 0, 1,
          1 if category in ("restaurant","cafe","bar") else 0,
          has_wrong_info, "verified",
          "09:00-18:00","09:00-18:00","09:00-18:00","09:00-18:00",
          "09:00-18:00","10:00-17:00",None))
    if tags:
        for tag in tags:
            conn.execute(
                "INSERT OR IGNORE INTO tags (tag, city, venue_id, yelp_visible) VALUES (?,?,?,?)",
                (tag, city, vid, 1))
    conn.commit()


TEST_POOL = [
    {"venue_id": "v01", "name": "Tate Modern",   "category": "museum",
     "district": "South Bank", "traffic_tier": "high", "lat": 51.507, "lng": -0.099,
     "tags": ["art","iconic","free-entry"], "has_wrong_info": False,
     "price_tier": "free", "avg_cost_local": 0.0, "recommended_visit_minutes": 120,
     "recommended_pace": "moderate", "booking_required": False,
     "wheelchair_accessible": 1, "pet_friendly": 0},
    {"venue_id": "v02", "name": "Borough Market","category": "restaurant",
     "district": "South Bank", "traffic_tier": "high", "lat": 51.505, "lng": -0.091,
     "tags": ["local-food","iconic"], "has_wrong_info": True,
     "price_tier": "mid", "avg_cost_local": 18.0, "recommended_visit_minutes": 60,
     "recommended_pace": "relaxed", "booking_required": False,
     "wheelchair_accessible": 1, "pet_friendly": 0},
    {"venue_id": "v03", "name": "The Anchor Pub","category": "bar",
     "district": "Shoreditch", "traffic_tier": "low", "lat": 51.522, "lng": -0.077,
     "tags": ["hidden-gem","craft-beer"], "has_wrong_info": False,
     "price_tier": "budget", "avg_cost_local": 7.0, "recommended_visit_minutes": 60,
     "recommended_pace": "relaxed", "booking_required": False,
     "wheelchair_accessible": 0, "pet_friendly": 1},
    {"venue_id": "v04", "name": "Halal Kitchen", "category": "restaurant",
     "district": "Shoreditch", "traffic_tier": "mid", "lat": 51.521, "lng": -0.075,
     "tags": ["halal","locals-favourite"], "has_wrong_info": False,
     "price_tier": "budget", "avg_cost_local": 14.0, "recommended_visit_minutes": 75,
     "recommended_pace": "moderate", "booking_required": False,
     "wheelchair_accessible": 1, "pet_friendly": 0},
    {"venue_id": "v05", "name": "Southwark Cathedral","category": "museum",
     "district": "South Bank", "traffic_tier": "mid", "lat": 51.506, "lng": -0.090,
     "tags": ["history","free-entry"], "has_wrong_info": False,
     "price_tier": "free", "avg_cost_local": 0.0, "recommended_visit_minutes": 45,
     "recommended_pace": "relaxed", "booking_required": False,
     "wheelchair_accessible": 1, "pet_friendly": 0},
]
POOL_MAP       = {v["venue_id"]: v for v in TEST_POOL}
WRONG_INFO_IDS = {v["venue_id"] for v in TEST_POOL if v.get("has_wrong_info")}

VALID_BRIEFS = [
    {"doc_type": "trip_diary",        "angle": "South Bank afternoon",
     "venue_ids": ["v01","v02","v05"], "wrong_info_venues": ["v02"], "stale": False},
    {"doc_type": "forum_qa",          "angle": "Halal spots near Shoreditch",
     "venue_ids": ["v04"],            "wrong_info_venues": []},
    {"doc_type": "listicle",          "angle": "Best free museums",
     "venue_ids": ["v01","v05"],      "wrong_info_venues": []},
    {"doc_type": "comparison",        "angle": "Tate vs Southwark",
     "venue_ids": ["v01","v05"],      "wrong_info_venues": []},
    {"doc_type": "itinerary_guide",   "angle": "South Bank cluster",
     "venue_ids": ["v01","v02","v05"],"wrong_info_venues": []},
    {"doc_type": "review_aggregator", "angle": "Borough Market reviews",
     "venue_ids": ["v02"],            "wrong_info_venues": ["v02"], "stale": True},
    {"doc_type": "city_memoir",       "angle": "A week in London",
     "venue_ids": ["v01","v03","v04"],"wrong_info_venues": []},
    # Extra briefs to reach minimum count
    {"doc_type": "trip_diary",        "angle": "Shoreditch evening",
     "venue_ids": ["v03","v04"],      "wrong_info_venues": []},
    {"doc_type": "listicle",          "angle": "Hidden gem bars",
     "venue_ids": ["v03"],            "wrong_info_venues": []},
]


# ─────────────────────────────────────────────────────────────────────────────
# [1] pool_utils — all 10 functions importable
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] pool_utils — all 10 functions importable")

from scripts.generation.pool_utils import (
    load_venue_pool, load_city_pool, load_pool, load_unavailable_dates,
    get_window_for_city, build_pool_inventory, _city_centre_from_pool,
    _query_pool, _get_venue_detail, _apply_pool_filters,
)
check("load_venue_pool importable", callable(load_venue_pool))
check("load_city_pool importable",  callable(load_city_pool))
check("load_pool importable",       callable(load_pool))
check("load_unavailable_dates importable", callable(load_unavailable_dates))
check("get_window_for_city importable", callable(get_window_for_city))
check("build_pool_inventory importable", callable(build_pool_inventory))
check("_city_centre_from_pool importable", callable(_city_centre_from_pool))
check("_query_pool importable", callable(_query_pool))
check("_get_venue_detail importable", callable(_get_venue_detail))
check("_apply_pool_filters importable", callable(_apply_pool_filters))


# ─────────────────────────────────────────────────────────────────────────────
# [2] pool_utils — original callers still work
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] pool_utils — original callers unchanged")

import inspect
from scripts.generation import generate_task, task_agent, compute_task_difficulty
from test_generate_tasks import load_city_pool as tgt_lcp, build_pool_inventory as tgt_bpi

check("generate_task.load_venue_pool still callable",
      callable(generate_task.load_venue_pool))
check("generate_task._apply_pool_filters still callable",
      callable(generate_task._apply_pool_filters))
check("task_agent._city_centre_from_pool still callable",
      callable(task_agent._city_centre_from_pool))
check("task_agent._query_pool still callable",
      callable(task_agent._query_pool))
check("test_generate_tasks.load_city_pool still callable",
      callable(tgt_lcp))
check("test_generate_tasks.build_pool_inventory still callable",
      callable(tgt_bpi))


# ─────────────────────────────────────────────────────────────────────────────
# [3] _validate_briefs — pass case
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] _validate_briefs — pass case")

from scripts.generation.doc_agent import _validate_briefs, DOC_TYPES

errors = _validate_briefs(VALID_BRIEFS, POOL_MAP, WRONG_INFO_IDS)
check("valid briefs pass validation", len(errors) == 0, str(errors[:2]))


# ─────────────────────────────────────────────────────────────────────────────
# [4] _validate_briefs — failure modes
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] _validate_briefs — failure modes")

import copy

# Empty briefs
errors_empty = _validate_briefs([], POOL_MAP, WRONG_INFO_IDS)
check("empty briefs rejected", len(errors_empty) > 0)

# Too few briefs
errors_few = _validate_briefs(VALID_BRIEFS[:2], POOL_MAP, WRONG_INFO_IDS)
check("too few briefs (<8) rejected", any("few" in e.lower() or "8" in e for e in errors_few))

# Unknown venue_id
bad_vid = copy.deepcopy(VALID_BRIEFS)
bad_vid[0]["venue_ids"] = ["v01", "v99_nonexistent"]
errors_vid = _validate_briefs(bad_vid, POOL_MAP, WRONG_INFO_IDS)
check("unknown venue_id rejected", any("v99_nonexistent" in e for e in errors_vid))

# wrong_info_venue not in venue_ids
bad_wi_not_in = copy.deepcopy(VALID_BRIEFS)
bad_wi_not_in[0]["wrong_info_venues"] = ["v05"]  # v05 not in venue_ids of brief 0
bad_wi_not_in[0]["venue_ids"] = ["v01", "v02"]
errors_wi1 = _validate_briefs(bad_wi_not_in, POOL_MAP, WRONG_INFO_IDS)
check("wrong_info_venue not in venue_ids rejected",
      any("not in venue_ids" in e for e in errors_wi1))

# wrong_info_venue but has_wrong_info=False
bad_wi_clean = copy.deepcopy(VALID_BRIEFS)
bad_wi_clean[0]["wrong_info_venues"] = ["v01"]  # v01 has no wrong info
errors_wi2 = _validate_briefs(bad_wi_clean, POOL_MAP, WRONG_INFO_IDS)
check("venue without wrong info rejected as wrong_info_venue",
      any("no wrong info" in e.lower() or "wrong_info" in e.lower() for e in errors_wi2))

# Missing doc type
bad_missing_type = [b for b in VALID_BRIEFS if b["doc_type"] != "city_memoir"]
errors_type = _validate_briefs(bad_missing_type, POOL_MAP, WRONG_INFO_IDS)
check("missing doc type rejected", any("city_memoir" in e for e in errors_type))


# ─────────────────────────────────────────────────────────────────────────────
# [5] _dispatch_doc_tool — each tool
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] _dispatch_doc_tool — each tool")

from scripts.generation.doc_agent import _dispatch_doc_tool, _build_doc_handbook

handbook = _build_doc_handbook(TEST_POOL, WRONG_INFO_IDS)

def dispatch(tool_name, tool_input):
    return _dispatch_doc_tool(
        tool_name, tool_input, TEST_POOL, POOL_MAP, WRONG_INFO_IDS, handbook
    )

# HELP
r, term, briefs = dispatch("HELP", {"query": "-h doc_types"})
check("HELP: status ok", r["status"] == "ok")
check("HELP: not terminating", not term)
check("HELP doc_types: mentions trip_diary", "trip_diary" in r["content"])

r2, _, _ = dispatch("HELP", {"query": "-h tags"})
check("HELP tags: pool tags present", "art" in r2["content"] or "iconic" in r2["content"])

r3, _, _ = dispatch("HELP", {"query": "-h wrong_info"})
check("HELP wrong_info: v02 listed", "v02" in r3["content"])

# THINK
r, term, _ = dispatch("THINK", {"thought": "I will write a trip diary about South Bank."})
check("THINK: status ok", r["status"] == "ok")
check("THINK: not terminating", not term)

# query_pool — doc_agent defaults show_venues=True (always returns venue list)
r, term, _ = dispatch("query_pool", {"filters": {"district": "South Bank"}})
check("query_pool South Bank: status ok", r["status"] == "ok")
check("query_pool South Bank: 3 venues", r["count"] == 3)
check("query_pool South Bank: venues returned by default", len(r["venues"]) == 3)
check("query_pool: not terminating", not term)

# show_venues=False suppresses the list
r_c, _, _ = dispatch("query_pool", {"filters": {"district": "South Bank"}, "show_venues": False})
check("query_pool show_venues=False: venues empty", r_c["venues"] == [])
check("query_pool show_venues=False: count still correct", r_c["count"] == 3)

r2, _, _ = dispatch("query_pool", {"filters": {"tag": "halal"}})
check("query_pool halal: 1 venue", r2["count"] == 1)

# get_venue
r, term, _ = dispatch("get_venue", {"venue_id": "v02"})
check("get_venue v02: status ok", r["status"] == "ok")
check("get_venue: not terminating", not term)
check("get_venue v02: has_wrong_info=True", r["venue"]["has_wrong_info"] is True)
check("get_venue v02: has tags", len(r["venue"].get("tags", [])) > 0)

r_bad, _, _ = dispatch("get_venue", {"venue_id": "v99"})
check("get_venue unknown: status error", r_bad["status"] == "error")

# estimate_travel
r, term, _ = dispatch("estimate_travel", {"venue_id_a": "v01", "venue_id_b": "v02"})
check("estimate_travel v01→v02: status ok", r["status"] == "ok")
check("estimate_travel: not terminating", not term)
check("estimate_travel: distance_km ~0.5-1.5km",
      0.3 < r["distance_km"] < 2.0, f"{r['distance_km']:.2f}km")
check("estimate_travel: has result_minutes", "result_minutes" in r)

# estimate_travel no coords
pool_no_coord = TEST_POOL + [{"venue_id": "nc1", "name": "No Coord",
                               "category": "bar", "district": "X",
                               "traffic_tier": "mid", "lat": None, "lng": None,
                               "tags": [], "has_wrong_info": False}]
pool_map_nc = {v["venue_id"]: v for v in pool_no_coord}
r_nc, _, _ = _dispatch_doc_tool("estimate_travel", {"venue_id_a": "v01", "venue_id_b": "nc1"},
                                  pool_no_coord, pool_map_nc, WRONG_INFO_IDS, handbook)
check("estimate_travel no coords: status error", r_nc["status"] == "error")

# SUBMIT pass
r, term, accepted = dispatch("SUBMIT", {"briefs": VALID_BRIEFS})
check("SUBMIT valid: accepted", r["status"] == "accepted")
check("SUBMIT valid: terminates", term)
check("SUBMIT valid: briefs returned", accepted is not None and len(accepted) == len(VALID_BRIEFS))

# SUBMIT fail
bad = copy.deepcopy(VALID_BRIEFS[:2])
r_fail, term_fail, _ = dispatch("SUBMIT", {"briefs": bad})
check("SUBMIT invalid: validation_failed", r_fail["status"] == "validation_failed")
check("SUBMIT invalid: not terminating", not term_fail)


# ─────────────────────────────────────────────────────────────────────────────
# [6] run_doc_planning_agent — no API key → None
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] run_doc_planning_agent — no API key")

from scripts.generation.doc_agent import run_doc_planning_agent

result = run_doc_planning_agent(
    city="london", pool=TEST_POOL, tasks=[],
    model="claude-sonnet-4-20250514", api_key=None,
)
check("no API key returns None", result is None)


# ─────────────────────────────────────────────────────────────────────────────
# [7] _assign_popularity ranges
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] _assign_popularity ranges")

from scripts.generation.generate_multi_venue_docs import _assign_popularity

for dt in ["trip_diary", "forum_qa", "listicle", "comparison",
           "itinerary_guide", "review_aggregator", "city_memoir"]:
    pop = _assign_popularity({"doc_type": dt, "stale": False})
    check(f"{dt}: likes > 0", pop["likes"] > 0)
    check(f"{dt}: saves > 0", pop["saves"] > 0)
    check(f"{dt}: view_count > 0", pop["view_count"] > 0)

# Stale gets higher ranges
pop_stale  = _assign_popularity({"doc_type": "listicle", "stale": True})
pop_fresh  = _assign_popularity({"doc_type": "listicle", "stale": False})
from scripts.generation.doc_agent import STALE_POPULARITY, DOC_TYPE_POPULARITY
# Stale range should be non-trivially high — mean should exceed forum_qa range
stale_mean_likes = (STALE_POPULARITY[0] + STALE_POPULARITY[1]) / 2
forum_mean_likes = (DOC_TYPE_POPULARITY["forum_qa"][0] + DOC_TYPE_POPULARITY["forum_qa"][1]) / 2
check("stale mean likes > forum_qa mean likes (stale docs are popular)",
      stale_mean_likes > forum_mean_likes,
      f"stale_mean={stale_mean_likes} forum_mean={forum_mean_likes}")


# ─────────────────────────────────────────────────────────────────────────────
# [8] _assign_date
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] _assign_date")

from scripts.generation.generate_multi_venue_docs import _assign_date

# Explicit date passthrough
check("explicit YYYY returns YYYY-MM-DD", _assign_date({"date": "2023"}).startswith("2023-"))
check("explicit YYYY-MM returns YYYY-MM-15", _assign_date({"date": "2024-03"}) == "2024-03-15")
check("explicit full date passthrough", _assign_date({"date": "2024-06-15"}) == "2024-06-15")

# Stale → pre-2024
for _ in range(5):
    d = _assign_date({"stale": True})
    check(f"stale date pre-2024: {d}", int(d[:4]) <= 2023)

# Fresh → 2024+
for _ in range(5):
    d = _assign_date({"stale": False})
    check(f"fresh date 2024+: {d}", int(d[:4]) >= 2024)


# ─────────────────────────────────────────────────────────────────────────────
# [9] _write_doc_to_db — DB writes and roles
# ─────────────────────────────────────────────────────────────────────────────
print("\n[9] _write_doc_to_db — DB writes and roles")

from scripts.generation.generate_multi_venue_docs import _write_doc_to_db

db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
insert_venue(conn, "london", "v01", "Tate Modern", has_wrong_info=0)
insert_venue(conn, "london", "v02", "Borough Market", category="restaurant", has_wrong_info=1)
insert_venue(conn, "london", "v05", "Southwark Cathedral", has_wrong_info=0)

brief = {
    "doc_type": "trip_diary",
    "angle": "South Bank afternoon",
    "venue_ids": ["v01", "v02", "v05"],
    "wrong_info_venues": ["v02"],
    "stale": False,
}
generated = {
    "title":   "An afternoon on the South Bank",
    "author":  "Jane Doe",
    "content": "We started at Tate Modern, free entry and worth every minute...",
    "venues_mentioned": ["v01", "v02", "v05"],
}

doc_id = _write_doc_to_db(conn, "london", brief, generated)

# Verify source_docs row
row = conn.execute("SELECT * FROM source_docs WHERE doc_id = ?", (doc_id,)).fetchone()
check("source_docs row written", row is not None)
check("doc_id has expected prefix", doc_id.startswith("lon_mv_"))
check("page_status = 'verified'", row["page_status"] == "verified")
check("doc_subtype = 'trip_diary'", row["doc_subtype"] == "trip_diary")
check("likes > 0", (row["likes"] or 0) > 0)
check("saves > 0", (row["saves"] or 0) > 0)
check("view_count > 0", (row["view_count"] or 0) > 0)
check("title correct", row["title"] == "An afternoon on the South Bank")

# Verify doc_venue_refs
refs = {r["venue_id"] for r in conn.execute(
    "SELECT venue_id FROM doc_venue_refs WHERE doc_id = ?", (doc_id,)
).fetchall()}
check("doc_venue_refs has all 3 venues", refs == {"v01","v02","v05"})

# Verify doc_venue_roles
roles = {r["venue_id"]: r["role"] for r in conn.execute(
    "SELECT venue_id, role FROM doc_venue_roles WHERE doc_id = ?", (doc_id,)
).fetchall()}
check("v02 is incorrect_source", roles.get("v02") == "incorrect_source")
check("v01 is neutral", roles.get("v01") == "neutral")
check("v05 is neutral", roles.get("v05") == "neutral")

conn.close()
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [10] _build_phase2_prompt structure
# ─────────────────────────────────────────────────────────────────────────────
print("\n[10] _build_phase2_prompt structure")

from scripts.generation.generate_multi_venue_docs import _build_phase2_prompt

batch = [
    {"doc_type": "trip_diary", "angle": "South Bank afternoon",
     "venue_ids": ["v01","v02"], "wrong_info_venues": ["v02"]},
    {"doc_type": "trip_diary", "angle": "Hidden Shoreditch",
     "venue_ids": ["v03","v04"], "wrong_info_venues": []},
]
venue_details = {
    "v01": {"name": "Tate Modern", "category": "museum", "district": "South Bank",
            "traffic_tier": "high", "avg_cost_local": 0.0, "tags": ["art","iconic"],
            "regulations": {"wheelchair_accessible": True},
            "recommended_pace": "moderate", "recommended_visit_minutes": 120,
            "lat": 51.507, "lng": -0.099, "has_wrong_info": False},
    "v02": {"name": "Borough Market", "category": "restaurant", "district": "South Bank",
            "traffic_tier": "high", "avg_cost_local": 18.0, "tags": ["local-food"],
            "regulations": {"wheelchair_accessible": True},
            "recommended_pace": "relaxed", "recommended_visit_minutes": 60,
            "lat": 51.505, "lng": -0.091, "has_wrong_info": True},
    "v03": {"name": "The Anchor Pub", "category": "bar", "district": "Shoreditch",
            "traffic_tier": "low", "avg_cost_local": 7.0, "tags": ["hidden-gem"],
            "regulations": {}, "recommended_pace": "relaxed",
            "recommended_visit_minutes": 60, "lat": 51.522, "lng": -0.077,
            "has_wrong_info": False},
    "v04": {"name": "Halal Kitchen", "category": "restaurant", "district": "Shoreditch",
            "traffic_tier": "mid", "avg_cost_local": 14.0, "tags": ["halal"],
            "regulations": {}, "recommended_pace": "moderate",
            "recommended_visit_minutes": 75, "lat": 51.521, "lng": -0.075,
            "has_wrong_info": False},
}

prompt = _build_phase2_prompt("london", batch, venue_details)
check("prompt mentions city", "london" in prompt.lower() or "London" in prompt)
check("prompt has WRONG INFO flag for v02", "WRONG INFO" in prompt)
check("prompt requests JSON array output", "JSON array" in prompt or "json array" in prompt.lower())
check("prompt has 2 doc specs", prompt.count("DOC 1") == 1 and prompt.count("DOC 2") == 1)
check("prompt has differentiation instruction", "distinct" in prompt.lower())
check("prompt has both venues from doc 1", "v01" in prompt and "v02" in prompt)


# ─────────────────────────────────────────────────────────────────────────────
# [11] mock_tools — likes/saves/view_count in loaded posts
# ─────────────────────────────────────────────────────────────────────────────
print("\n[11] mock_tools — popularity fields in loaded posts")

from scripts.generation.generate_multi_venue_docs import _write_doc_to_db

db2 = make_db()
conn2 = get_connection(db2)
insert_city(conn2, "london")
insert_venue(conn2, "london", "v01", "Tate Modern")
insert_venue(conn2, "london", "v02", "Borough Market", category="restaurant")

brief2 = {"doc_type": "listicle", "angle": "Best museums",
           "venue_ids": ["v01","v02"], "wrong_info_venues": [], "stale": False}
gen2   = {"title": "Best of London", "author": "Test",
          "content": "A great guide...", "venues_mentioned": ["v01","v02"]}
doc_id2 = _write_doc_to_db(conn2, "london", brief2, gen2)
conn2.close()

# Now load via _load_city_from_db and check popularity fields
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent.parent / "server"))
from server.mock_tools import _load_city_from_db
from scripts.generation.db import DB_PATH as _DB_PATH
import scripts.generation.db as _gen_db
_orig = _gen_db.DB_PATH
_gen_db.DB_PATH = db2

try:
    city_idx = _load_city_from_db("london", db2)
    mv_posts = [p for p in city_idx.posts if p.get("doc_id") == doc_id2]
    check("multi-venue doc loaded by mock_tools", len(mv_posts) == 1)
    if mv_posts:
        p = mv_posts[0]
        check("loaded post has 'likes' field", "likes" in p)
        check("loaded post has 'saves' field", "saves" in p)
        check("loaded post has 'view_count' field", "view_count" in p)
        check("loaded post likes > 0", p.get("likes", 0) > 0)
        check("loaded post saves > 0", p.get("saves", 0) > 0)
except Exception as e:
    check("mock_tools loads multi-venue doc", False, str(e))
finally:
    _gen_db.DB_PATH = _orig
    db2.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [12] Prior test suites still pass (import check)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[12] Prior imports still intact")

from test_generate_tasks import validate_task_schema, generate_all_types
from scripts.generation.generate_task import _apply_pool_filters, _verify_task_solvable
from scripts.generation.task_agent import run_task_agent
from scripts.generation.compute_task_difficulty import annotate_task_difficulty
check("validate_task_schema importable",  callable(validate_task_schema))
check("generate_all_types importable",    callable(generate_all_types))
check("_apply_pool_filters importable",   callable(_apply_pool_filters))
check("_verify_task_solvable importable", callable(_verify_task_solvable))
check("run_task_agent importable",        callable(run_task_agent))
check("annotate_task_difficulty importable", callable(annotate_task_difficulty))


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All E4 tests passed ({PASS}/{total})")
else:
    print(f"❌ {FAIL}/{total} tests failed")
import sys as _sys2
_sys2.exit(0 if FAIL == 0 else 1)
