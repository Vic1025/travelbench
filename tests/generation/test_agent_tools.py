"""
scripts/generation/test_agent_tools.py

Tests for agent_tools.py — covers all 8 tools and all 7 VERIFY checks,
each with passing and failing cases.

Run: python scripts/generation/test_agent_tools.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, new_venue_id
from scripts.generation.db import get_connection, new_doc_id, new_page_id, new_wrong_info_id
from scripts.generation.agent_tools import (
    tool_HELP, tool_CREATE_PAGE, tool_FILL, tool_COMMIT,
    tool_VERIFY, tool_GET_STATUS, tool_THINK, tool_SUBMIT, dispatch
)

PASS = "✅"
FAIL = "❌"
results = []

def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    results.append((status, name, detail))
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    TEST_DB = Path(f.name)

# Seed city config
conn = init_db(TEST_DB)
conn.execute(
    "INSERT INTO city_config (city, display_name, country, centre_lat, centre_lng, "
    "radius_km, local_cuisine_label, task_dates) VALUES (?,?,?,?,?,?,?,?)",
    ("paris", "Paris", "France", 48.8566, 2.3522, 5.0, "french", '["2025-03-07"]')
)
conn.commit()
conn.close()

KW = {"db_path": TEST_DB}

# ─── Test HELP ────────────────────────────────────────────────────────────────
print("\n[1] HELP tool")
r = tool_HELP("-h function", **KW)
check("HELP status ok", r["status"] == "ok")
check("HELP content non-empty", len(r["content"]) > 0)
check("HELP content has FILL", "FILL" in r["content"])

r2 = tool_HELP("-h format venue", **KW)
check("HELP format venue", "venue_id" in r2["content"])

# ─── Test CREATE_PAGE — venue ─────────────────────────────────────────────────
print("\n[2] CREATE_PAGE — venue")
r = tool_CREATE_PAGE("venue", city="paris", **KW)
check("CREATE_PAGE status", r["status"] == "created")
check("CREATE_PAGE returns page_id", "page_id" in r)
check("CREATE_PAGE returns record_id", "record_id" in r)
check("CREATE_PAGE required_fields present", "required_fields" in r)
check("CREATE_PAGE name is null", r["required_fields"].get("name") is None)
page_id_venue = r["page_id"]
vid = r["record_id"]  # use auto-generated venue_id

# Bad entity type
r_bad = tool_CREATE_PAGE("nonexistent", city="paris", **KW)
check("CREATE_PAGE bad entity_type error", r_bad["status"] == "error")

# ─── Test CREATE_PAGE — yelp_listing ─────────────────────────────────────────
print("\n[3] CREATE_PAGE — yelp_listing (before venue exists is error)")
fake_vid = new_venue_id()
r = tool_CREATE_PAGE("yelp_listing", venue_id=fake_vid, city="paris", **KW)
check("yelp_listing without venue = error", r["status"] == "error")

# ─── Test FILL ────────────────────────────────────────────────────────────────
print("\n[4] FILL")
r = tool_FILL(page_id_venue, {
    "city": "paris",
    "name": "Test Bistro", "category": "restaurant", "district": "Le Marais",
    "lat": 48.856, "lng": 2.352, "avg_cost_local": 35.0, "price_tier": "mid",
    "recommended_visit_minutes": 90, "booking_required": 1, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "mid", "total_results": 340, "yelp_popularity_score": 0.55,
    "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 1,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
    "lunch_cost_local": 28.0, "dinner_cost_local": 42.0, "local_cuisine": 1,
    "cuisine": "french",  # P6-T1b — required for restaurants
    "city": "paris"
}, **KW)
check("FILL status ok", r["status"] == "ok")
check("FILL updated list", "name" in r["updated"])
check("FILL current_state has name", r["current_state"].get("name") == "Test Bistro")

# Override test
r2 = tool_FILL(page_id_venue, {"noise_level": "quiet"}, **KW)
check("FILL override noise_level", r2["current_state"].get("noise_level") == "quiet")

# Unknown field
r3 = tool_FILL(page_id_venue, {"nonexistent_field": "value"}, **KW)
check("FILL unknown field = error", r3["status"] == "error")

# ─── Test COMMIT ──────────────────────────────────────────────────────────────
print("\n[5] COMMIT")
r = tool_COMMIT(page_id_venue, **KW)
check("COMMIT status committed", r["status"] == "committed")
check("COMMIT returns content", "content" in r)
check("COMMIT content has name", r["content"].get("name") == "Test Bistro")

# Try committing a page with missing fields
r_new = tool_CREATE_PAGE("venue", city="paris", **KW)
page_id_incomplete = r_new["page_id"]
tool_FILL(page_id_incomplete, {"name": "Incomplete Venue"}, **KW)
r_commit_fail = tool_COMMIT(page_id_incomplete, **KW)
check("COMMIT incomplete = incomplete status", r_commit_fail["status"] == "incomplete")
check("COMMIT reports missing fields", len(r_commit_fail["missing_required"]) > 0)

# ─── Test GET_STATUS ──────────────────────────────────────────────────────────
print("\n[6] GET_STATUS")
r = tool_GET_STATUS(vid, **KW)
check("GET_STATUS returns venue_id", r["venue_id"] == vid)
check("GET_STATUS has pages", len(r["pages"]) > 0)
check("GET_STATUS venue page committed", r["pages"][0]["page_status"] == "committed")

r_unknown = tool_GET_STATUS("XXXXXXX", **KW)
check("GET_STATUS unknown venue", len(r_unknown["pages"]) == 0)

# ─── Test VERIFY checks ───────────────────────────────────────────────────────
print("\n[7] VERIFY — regulation_visibility check (pet_friendly=0, no mention in docs)")
# Venue has pet_friendly=0 but no source docs yet — should fail regulation_visibility
r_v = tool_VERIFY("all", venue_id=vid, **KW)
check("VERIFY fails regulation_visibility", not r_v["passed"])
check("VERIFY error check name", any(e["check"] == "regulation_visibility" for e in r_v.get("errors", [])))

# ─── Test 8: use a fresh venue that has a source doc from the start ──────────
print("\n[8] VERIFY — regulation_visibility passes when doc covers regulation")
# Create a new venue with pet_friendly=0, add a source doc before first VERIFY
vid3 = new_venue_id()
r_c = tool_CREATE_PAGE("venue", venue_id=vid3, city="paris", **KW)
pid3 = r_c["page_id"]
tool_FILL(pid3, {
    "name": "No Pets Cafe", "category": "cafe", "district": "Bastille",
    "lat": 48.853, "lng": 2.369, "avg_cost_local": 14.0, "price_tier": "budget",
    "recommended_visit_minutes": 45, "booking_required": 0, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "relaxed",
    "traffic_tier": "low", "total_results": 12, "yelp_popularity_score": 0.25,
    "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "quiet", "reservation_required": 0,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
    "city": "paris"
}, **KW)
tool_COMMIT(pid3, **KW)

# Add source doc mentioning no pets
from datetime import datetime, timezone
conn = get_connection(TEST_DB)
# Ensure the venue row exists (VERIFY may have cleaned up draft_pages but venue row persists)
existing = conn.execute("SELECT 1 FROM venues WHERE venue_id = ?", (vid3,)).fetchone()
if not existing:
    conn.execute("""
        INSERT OR IGNORE INTO venues (venue_id, city, name, category, district, lat, lng,
            avg_cost_local, price_tier, recommended_visit_minutes, booking_required,
            has_official_site, outdoor_sensitivity, recommended_pace, traffic_tier,
            total_results, yelp_popularity_score, pet_friendly, wheelchair_accessible,
            parking_nearby, photography_allowed, noise_level, reservation_required,
            outside_food_allowed, family_friendly, food_available, page_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (vid3, "paris", "Pet Friendly Cafe", "cafe", "Bastille",
          48.853, 2.369, 14.0, "budget", 45, 0, 0, "indoor", "relaxed",
          "low", 12, 0.25, 1, 0, 0, 0, "quiet", 0, 1, 0, 1, "draft"))
    conn.commit()
doc_id_test = new_doc_id()
conn.execute("""
    INSERT OR REPLACE INTO source_docs (doc_id, city, doc_type, title, author,
        source_name, date, body, mentioned_regulations, page_status)
    VALUES (?,?,?,?,?,?,?,?,?,?)
""", (doc_id_test, "paris", "blog", "Test blog", "Author", "TestBlog", "2024-01-01",
      "Great place but no pets allowed inside. No outside food or drinks permitted. The food was excellent.", '["pet_friendly", "outside_food_allowed"]', "committed"))
conn.execute(
    "INSERT OR REPLACE INTO doc_venue_refs (doc_id, venue_id) VALUES (?,?)",
    (doc_id_test, vid3)
)
# Register the source_doc page in draft_pages too
doc_pid = new_page_id()
conn.execute(
    "INSERT INTO draft_pages (page_id, venue_id, entity_type, page_status, created_at) "
    "VALUES (?,?,?,?,?)",
    (doc_pid, vid3, "source_doc", "committed", datetime.now(timezone.utc).isoformat())
)
conn.commit()
conn.close()

r_v2 = tool_VERIFY("all", venue_id=vid3, **KW)
reg_errors = [e for e in r_v2.get("errors", []) if e["check"] == "regulation_visibility"]
check("regulation_visibility passes with doc", len(reg_errors) == 0)

# ─── Test VERIFY — incorrect_hours_diff check (fresh venue) ─────────────────────
print("\n[9] VERIFY — incorrect_hours_diff check")
# Use a fresh venue to avoid interference from earlier tests
r_stale_v = tool_CREATE_PAGE("venue", city="paris", **KW)
vid_stale = r_stale_v["record_id"]
pid_stale = r_stale_v["page_id"]
tool_FILL(pid_stale, {
    "name": "Stale Hours Test", "category": "restaurant", "district": "Bastille",
    "lat": 48.853, "lng": 2.369, "avg_cost_local": 35.0, "price_tier": "mid",
    "recommended_visit_minutes": 90, "booking_required": 0, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "mid", "total_results": 100, "yelp_popularity_score": 0.5,
    "pet_friendly": 1, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
    "hours_fri": "12:00-22:30", "cuisine": "french",  # P6-T1b
    "city": "paris"
}, **KW)
tool_COMMIT(pid_stale, **KW)

conn = get_connection(TEST_DB)
# Add yelp listing with SAME hours as ground truth (this should fail stale check)
conn.execute("""
    INSERT OR REPLACE INTO yelp_listings (venue_id, city, name, category, district,
        stars, review_count, yelp_hours_fri, yelp_popularity_score, page_status)
    VALUES (?,?,?,?,?,?,?,?,?,?)
""", (vid_stale, "paris", "Stale Hours Test", "restaurant", "Bastille",
      4.2, 120, "12:00-22:30", 0.5, "committed"))
wi_id = new_wrong_info_id()
conn.execute("""
    INSERT OR REPLACE INTO wrong_info (wrong_info_id, venue_id, affected_field,
        incorrect_value, correct_value, source_type, wrong_info_category, origin_story)
    VALUES (?,?,?,?,?,?,?,?)
""", (wi_id, vid_stale, "hours_fri", "22:00", "22:30", "yelp", "temporal_decay",
      "Owner registered old hours."))
new_pid2 = new_page_id()
conn.execute(
    "INSERT OR REPLACE INTO draft_pages (page_id, venue_id, entity_type, page_status, created_at) "
    "VALUES (?,?,?,?,?)",
    (new_pid2, vid_stale, "yelp_listing", "committed", datetime.now(timezone.utc).isoformat())
)
conn.commit()
conn.close()

r_stale = tool_VERIFY("all", venue_id=vid_stale, **KW)
stale_errors = [e for e in r_stale.get("errors", []) if e["check"] == "incorrect_hours_diff"]
check("incorrect_hours_diff fires when yelp matches truth", len(stale_errors) > 0)
# Verify pages were NOT deleted
conn = get_connection(TEST_DB)
remaining_pages = conn.execute("SELECT COUNT(*) FROM draft_pages WHERE venue_id = ?", (vid_stale,)).fetchone()[0]
check("pages not deleted after VERIFY failure", remaining_pages > 0)
conn.close()

# ─── Test THINK ─────────────────────────────────────────────────────────────
print("\n[10] THINK")
r = tool_THINK("I need to fix the incorrect hours — will set yelp_hours_fri to 22:00 instead of 22:30.")
check("THINK status logged", r["status"] == "logged")
check("THINK empty thought = error", tool_THINK("")["status"] == "error")

# ─── Test SUBMIT ──────────────────────────────────────────────────────────────
print("\n[11] SUBMIT — fails if pages not verified")
r = tool_SUBMIT(vid, **KW)
# Should fail since verify deleted pages
check("SUBMIT with no pages = error", r["status"] == "error")

# ─── Test dispatch ────────────────────────────────────────────────────────────
print("\n[12] dispatch()")
r = dispatch("HELP", query="-h function")
check("dispatch HELP", r["status"] == "ok")
r_bad = dispatch("UNKNOWN_TOOL")
check("dispatch unknown tool = error", r_bad["status"] == "error")

# ─── Test no_wrong_info_on_high_traffic VERIFY check ─────────────────────────
print("\n[13] VERIFY — no wrong info on high traffic venues")
# Create a high-traffic venue with wrong info — should fail VERIFY
r_high = tool_CREATE_PAGE("venue", city="paris", **KW)
vid_high = r_high["record_id"]
pid_high = r_high["page_id"]
tool_FILL(pid_high, {
    "name": "Famous Museum", "category": "museum", "district": "Le Marais",
    "lat": 48.856, "lng": 2.352, "avg_cost_local": 25.0, "price_tier": "mid",
    "recommended_visit_minutes": 120, "booking_required": 1, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "high", "total_results": 2500, "yelp_popularity_score": 0.85,
    "pet_friendly": 1, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 1,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 0,
}, **KW)
tool_COMMIT(pid_high, **KW)

# Add wrong info to this high-traffic venue
from scripts.generation.agent_tools import tool_ADD_WRONG_INFO


# P6-T22: ADD_WRONG_INFO now requires two committed doc_ids. Helper to
# spin up a (incorrect_source, truth_carrier) doc pair for tests that
# exercise ADD_WRONG_INFO without focusing on the doc-creation flow.
# Defined here (before _make_source_doc is defined later in the file)
# to avoid forward-reference at call time.
_WI_PADDING = (
    " Filler prose to satisfy P6-T19 body-length bounds — this is a "
    "placeholder body used by the wrong-info-doc-pair helper, padded out "
    "so COMMIT accepts the doc. " * 4
)
_ALL_PERSONAS = (
    "travel photographer", "local regular", "food blogger",
    "parent with kids", "solo traveller", "accessibility visitor",
    "tourist on first visit",
)

# Rotating pool of handles in varied shapes — spread across all T18
# signature buckets so no single shape saturates the city-wide 40% cap.
# Mix of: multi_word, mixed_case_compound, mixed_snake, lower_snake.
_WI_HANDLE_POOL = [
    "Marcus Chen",       "AnikaPatel",     "Jordan_Smith",   "lina_costa",
    "Carlos Mendez",     "PriyaSingh",     "Ethan_Park",     "sophie_lee",
    "Mateo Diaz",        "YaraAhmed",      "Reza_Khan",      "ines_roque",
    "Theo Walsh",        "MayaIyer",       "Kai_Tanaka",     "nora_beck",
    "Oren Sato",         "LaylaOwens",     "Sam_Bailey",     "frida_lund",
    "Dylan Stone",       "AiyanaHart",     "Felix_Day",      "ada_sun",
]
_WI_HANDLE_IX = {"n": 0}

def _next_wi_handle():
    """Return a fresh multi-word handle from the rotating pool."""
    h = _WI_HANDLE_POOL[_WI_HANDLE_IX["n"] % len(_WI_HANDLE_POOL)]
    _WI_HANDLE_IX["n"] += 1
    return h


def _make_wi_doc_pair(venue_id_for_pair, prefix, city="paris"):
    """Returns (incorrect_source_doc_id, truth_carrier_doc_id) — both
    already committed and linked to venue_id_for_pair via doc_venue_refs
    (CREATE_PAGE for source_doc auto-populates that since P6-T20).

    Picks the next-two-available personas on this venue so T16's
    within-venue persona uniqueness check doesn't block consecutive
    helper calls on the same venue."""
    # Find already-used personas on this venue (committed/verified docs)
    c = get_connection(TEST_DB)
    used = {
        row["p"] for row in c.execute(
            "SELECT DISTINCT LOWER(TRIM(persona)) AS p FROM source_docs s "
            "JOIN doc_venue_refs r ON s.doc_id = r.doc_id "
            "WHERE r.venue_id = ? AND s.page_status IN ('committed','verified') "
            "AND TRIM(s.persona) != ''",
            (venue_id_for_pair,)
        ).fetchall()
    }
    c.close()
    available = [p for p in _ALL_PERSONAS if p.lower() not in used]
    if len(available) < 2:
        raise RuntimeError(
            f"Venue {venue_id_for_pair} has no 2 available personas left "
            f"(used: {used}). Tests need a fresh venue."
        )
    personas = available[:2]
    tones = ["emotional / sensory", "terse / factual"]

    rid_pair = []
    for ix in range(2):
        r = tool_CREATE_PAGE("source_doc", city=city,
                              venue_id=venue_id_for_pair, **KW)
        pid = r["page_id"]
        tool_FILL(pid, {
            "city": city, "doc_type": "blog", "title": f"{prefix}_doc_{ix}",
            "author": _next_wi_handle(), "source_name": "TestSite",
            "date": "2024-01-01",
            "body": f"{prefix} doc {ix} body. " + _WI_PADDING,
            "likes": 10, "saves": 5, "view_count": 100,
            "persona": personas[ix], "tone": tones[ix],
        }, **KW)
        r_c = tool_COMMIT(pid, **KW)
        assert r_c["status"] == "committed", f"helper commit failed: {r_c}"
        rid_pair.append(r_c["record_id"])
    return rid_pair[0], rid_pair[1]


_inc_high, _tc_high = _make_wi_doc_pair(vid_high, "high")
r_wi_high = tool_ADD_WRONG_INFO(
    venue_id=vid_high, affected_field="hours_fri",
    incorrect_value="09:00-20:00", correct_value="09:00-18:00",
    source_type="yelp", wrong_info_category="temporal_decay",
    origin_story="Owner registered Yelp years ago and never updated.",
    incorrect_source_doc_id=_inc_high, truth_carrier_doc_id=_tc_high,
    db_path=TEST_DB
)
check("ADD_WRONG_INFO succeeds on high traffic (tool allows it)", r_wi_high["status"] == "ok")

# VERIFY should reject it
r_v_high = tool_VERIFY("all", venue_id=vid_high, **KW)
check("VERIFY rejects wrong info on high-traffic venue", not r_v_high["passed"])
high_errors = [e for e in r_v_high.get("errors", []) if e["check"] == "no_wrong_info_on_high_traffic"]
check("correct check name fires", len(high_errors) > 0)

# ─── Test ADD_WRONG_INFO hours direction validation ──────────────────────────
print("\n[14] ADD_WRONG_INFO — hours direction validation")
from scripts.generation.agent_tools import tool_ADD_WRONG_INFO

# Harmless: incorrect close EARLIER than correct → should be rejected
# Agent leaves by 17:00, venue open till 18:00, schedule works fine
r_bad1 = tool_ADD_WRONG_INFO(
    venue_id=vid, affected_field="hours_fri",
    incorrect_value="07:30-17:00", correct_value="07:30-18:00",
    source_type="yelp", wrong_info_category="temporal_decay",
    origin_story="Owner didn't update Yelp after extending hours.",
    db_path=TEST_DB
)
check("incorrect close earlier than correct = rejected (harmless)", r_bad1["status"] == "error")

# Harmless: incorrect open LATER than correct → should be rejected
# Agent plans 09:00 visit, venue already open at 08:00, no problem
r_bad2 = tool_ADD_WRONG_INFO(
    venue_id=vid, affected_field="hours_sat",
    incorrect_value="09:00-18:00", correct_value="08:00-18:00",
    source_type="yelp", wrong_info_category="temporal_decay",
    origin_story="Owner didn't update Yelp after moving opening time earlier.",
    db_path=TEST_DB
)
check("incorrect open later than correct = rejected (harmless)", r_bad2["status"] == "error")

# Valid trap: incorrect close LATER than correct → should be accepted
# Agent plans 18:30 visit, venue actually closed at 18:00 → FAIL
_inc_g1, _tc_g1 = _make_wi_doc_pair(vid, "good1")
r_good1 = tool_ADD_WRONG_INFO(
    venue_id=vid, affected_field="hours_fri",
    incorrect_value="07:30-19:00", correct_value="07:30-18:00",
    source_type="yelp", wrong_info_category="temporal_decay",
    origin_story="Owner registered hours when cafe was open till 19:00, reduced to 18:00 without updating Yelp.",
    incorrect_source_doc_id=_inc_g1, truth_carrier_doc_id=_tc_g1,
    db_path=TEST_DB
)
check("incorrect close later than correct = accepted (trap)", r_good1["status"] == "ok")

# Valid trap: incorrect open EARLIER than correct → should be accepted
# Agent plans 07:30 visit, venue actually opens at 08:00 → FAIL
_inc_g2, _tc_g2 = _make_wi_doc_pair(vid, "good2")
r_good2 = tool_ADD_WRONG_INFO(
    venue_id=vid, affected_field="hours_sat",
    incorrect_value="07:00-18:00", correct_value="08:00-18:00",
    source_type="blog", wrong_info_category="temporal_decay",
    origin_story="Blogger visited when cafe had earlier opening, it was pushed back to 08:00 in winter.",
    incorrect_source_doc_id=_inc_g2, truth_carrier_doc_id=_tc_g2,
    db_path=TEST_DB
)
check("incorrect open earlier than correct = accepted (trap)", r_good2["status"] == "ok")

# Non-hours fields should not be direction-checked — use a fresh venue
r_fresh = tool_CREATE_PAGE("venue", city="paris", **KW)
vid_nh = r_fresh["record_id"]
tool_FILL(r_fresh["page_id"], {
    "name": "Price Test Venue", "category": "restaurant", "district": "Bastille",
    "lat": 48.853, "lng": 2.369, "avg_cost_local": 20.0, "price_tier": "budget",
    "recommended_visit_minutes": 60, "booking_required": 0, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "low", "total_results": 10, "yelp_popularity_score": 0.2,
    "pet_friendly": 1, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
}, **KW)
tool_COMMIT(r_fresh["page_id"], **KW)
_inc_nh, _tc_nh = _make_wi_doc_pair(vid_nh, "nh")
r_nonhours = tool_ADD_WRONG_INFO(
    venue_id=vid_nh, affected_field="price_tier",
    incorrect_value="mid", correct_value="budget",
    source_type="blog", wrong_info_category="temporal_decay",
    origin_story="Blogger wrote about the venue when it was pricier.",
    incorrect_source_doc_id=_inc_nh, truth_carrier_doc_id=_tc_nh,
    db_path=TEST_DB
)
check("non-hours field not direction-checked", r_nonhours["status"] == "ok")


# ─── P6-T1: SET_TAGS vocabulary enforcement ──────────────────────────────────
print("\n[SET_TAGS — P6-T1] vocabulary enforcement")

from scripts.generation.agent_tools import tool_SET_TAGS

# Create + commit a venue we can SET_TAGS on
r_v1 = tool_CREATE_PAGE("venue", city="paris", **KW)
vid_t1 = r_v1["record_id"]
tool_FILL(r_v1["page_id"], {
    "name": "Tag Vocab Test Venue", "category": "restaurant", "district": "Le Marais",
    "lat": 48.86, "lng": 2.35, "avg_cost_local": 25.0, "price_tier": "mid",
    "recommended_visit_minutes": 60, "booking_required": 1, "has_official_site": 1,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "mid", "total_results": 50, "yelp_popularity_score": 0.5,
    "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 1,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
}, **KW)

# In-vocab tags accepted
r_ok = tool_SET_TAGS(vid_t1, [
    {"tag": "free-entry", "yelp_visible": 1},
    {"tag": "italian", "yelp_visible": 1},
    {"tag": "romantic", "yelp_visible": 0},
], city="paris", **KW)
check("P6-T1: in-vocab tags (universal + cuisine) → accepted",
      r_ok["status"] == "ok", str(r_ok))
check("P6-T1: tags_set count correct", r_ok.get("tags_set") == 3)

# Canonicalisation still works
r_canon = tool_SET_TAGS(vid_t1, [
    {"tag": "Free Entry", "yelp_visible": 1},
    {"tag": "michelin_star", "yelp_visible": 1},
], city="paris", **KW)
check("P6-T1: canonicalisation (space/underscore) accepted as on-vocab",
      r_canon["status"] == "ok", str(r_canon))

# Off-vocab tag rejected with suggestions
r_off = tool_SET_TAGS(vid_t1, [
    {"tag": "bombay-cafe", "yelp_visible": 1},
    {"tag": "free-entry", "yelp_visible": 1},
], city="paris", **KW)
check("P6-T1: off-vocab tag → status=error",
      r_off["status"] == "error", str(r_off))
check("P6-T1: error message mentions vocabulary",
      "vocabulary" in r_off.get("message", "").lower(), r_off.get("message", "")[:120])
check("P6-T1: off_vocab list returned",
      r_off.get("off_vocab") == ["bombay-cafe"], r_off.get("off_vocab"))

# Trigram fallback fires when no api_key — suggestions for a close near-dupe.
# (P6-T2-B: photography-allowed was removed from vocab; use live-musical → live-music
# as the near-dupe target instead.)
r_dupe = tool_SET_TAGS(vid_t1, [
    {"tag": "live-musical", "yelp_visible": 1},  # close to live-music
], city="paris", **KW)
check("P6-T1: close-near-dupe → rejected with live-music in suggestion",
      r_dupe["status"] == "error"
      and "live-music" in str(r_dupe.get("suggestions", {})),
      str(r_dupe.get("suggestions", {})))

# City extension is respected — set up an extension and verify
conn = get_connection(TEST_DB)
conn.execute("UPDATE city_config SET tag_vocabulary = ? WHERE city = 'paris'",
             ('["brasserie", "patisserie"]',))
conn.commit()
conn.close()

r_ext_ok = tool_SET_TAGS(vid_t1, [
    {"tag": "brasserie", "yelp_visible": 1},
    {"tag": "italian", "yelp_visible": 1},
], city="paris", **KW)
check("P6-T1: city-extension tag (brasserie) accepted after vocab update",
      r_ext_ok["status"] == "ok", str(r_ext_ok))


# ─── P6-T1b: COMMIT cuisine enforcement ──────────────────────────────────────
print("\n[COMMIT — P6-T1b] cuisine enforcement")

_BASE_RESTAURANT = {
    "name": "Cuisine Test Venue", "category": "restaurant", "district": "Le Marais",
    "lat": 48.86, "lng": 2.35, "avg_cost_local": 25.0, "price_tier": "mid",
    "recommended_visit_minutes": 60, "booking_required": 0, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "mid", "total_results": 50, "yelp_popularity_score": 0.5,
    "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
    "city": "paris",
}

def _make_venue(category="restaurant", cuisine=None, name="x"):
    r = tool_CREATE_PAGE("venue", city="paris", **KW)
    pid = r["page_id"]; vid = r["record_id"]
    fields = dict(_BASE_RESTAURANT)
    fields["category"] = category
    fields["name"]     = name
    if cuisine is not None:
        fields["cuisine"] = cuisine
    tool_FILL(pid, fields, **KW)
    return pid, vid

# Case 1: restaurant with on-vocab cuisine → committed
pid_r1, _ = _make_venue("restaurant", "italian", "T1-Italian Bistro")
r1 = tool_COMMIT(pid_r1, **KW)
check("P6-T1b: restaurant with cuisine='italian' → committed",
      r1["status"] == "committed", str(r1))

# Case 2: restaurant without cuisine → incomplete
pid_r2, _ = _make_venue("restaurant", None, "T1b-No Cuisine")
r2 = tool_COMMIT(pid_r2, **KW)
check("P6-T1b: restaurant without cuisine → incomplete",
      r2["status"] == "incomplete"
      and "cuisine" in (r2.get("missing_required") or []),
      str(r2))

# Case 3: restaurant with off-vocab cuisine → error
pid_r3, _ = _make_venue("restaurant", "atlantis-cuisine", "T1b-Atlantis")
r3 = tool_COMMIT(pid_r3, **KW)
check("P6-T1b: restaurant with off-vocab cuisine → error",
      r3["status"] == "error" and "atlantis-cuisine" in r3.get("message", ""),
      str(r3))

# Case 4: cafe without cuisine → committed (cuisine optional for non-restaurants)
# (use a fresh-fields dict so we don't accidentally include cuisine)
_CAFE_FIELDS = dict(_BASE_RESTAURANT)
_CAFE_FIELDS["category"] = "cafe"
_CAFE_FIELDS["name"]     = "T1b-Cafe-NoCuisine"
r_c1 = tool_CREATE_PAGE("venue", city="paris", **KW)
tool_FILL(r_c1["page_id"], _CAFE_FIELDS, **KW)
r4 = tool_COMMIT(r_c1["page_id"], **KW)
check("P6-T1b: cafe without cuisine → committed (optional)",
      r4["status"] == "committed", str(r4))

# Case 5: cafe with on-vocab cuisine → committed
pid_c2, _ = _make_venue("cafe", "japanese", "T1b-JapaneseCafe")
r5 = tool_COMMIT(pid_c2, **KW)
check("P6-T1b: cafe with cuisine='japanese' → committed",
      r5["status"] == "committed", str(r5))

# Case 6: cafe with off-vocab cuisine → error
pid_c3, _ = _make_venue("cafe", "atlantis-cuisine", "T1b-CafeAtlantis")
r6 = tool_COMMIT(pid_c3, **KW)
check("P6-T1b: cafe with off-vocab cuisine → error",
      r6["status"] == "error" and "atlantis-cuisine" in r6.get("message", ""),
      str(r6))

# Case 7: park with cuisine → error (cuisine on non-food category)
pid_p1, _ = _make_venue("park", "italian", "T1b-ItalianPark")
r7 = tool_COMMIT(pid_p1, **KW)
check("P6-T1b: park with cuisine → rejected (non-food category)",
      r7["status"] == "error" and "park" in r7.get("message", "")
      and "cuisine" in r7.get("message", ""),
      str(r7))

# Case 8: attraction with cuisine → error
pid_a1, _ = _make_venue("attraction", "american", "T1b-AmericanAttraction")
r8 = tool_COMMIT(pid_a1, **KW)
check("P6-T1b: attraction with cuisine → rejected (non-food category)",
      r8["status"] == "error" and "cuisine" in r8.get("message", ""),
      str(r8))

# Case 9: park WITHOUT cuisine → committed (no cuisine is fine on non-food)
pid_p2, _ = _make_venue("park", None, "T1b-NoCuisineParK")
r9 = tool_COMMIT(pid_p2, **KW)
check("P6-T1b: park without cuisine → committed",
      r9["status"] == "committed", str(r9))

# Case 10: bar with on-vocab cuisine → committed (bar is a food/drink category)
pid_b1, _ = _make_venue("bar", "american", "T1b-AmericanBar")
r10 = tool_COMMIT(pid_b1, **KW)
check("P6-T1b: bar with on-vocab cuisine → committed (food/drink category)",
      r10["status"] == "committed", str(r10))


# ─── P6-T2-B: mentioned_tags COMMIT validation + _check_tag_visibility ───────
print("\n[P6-T2-B] mentioned_tags + tag_visibility check")

from scripts.generation.agent_tools import (
    _check_tag_visibility, tool_REGISTER_DOC_REFS, tool_SET_TAGS,
)

# Helper: build a fully-formed venue + tags + 2 docs for tag_visibility tests.
def _seed_tag_visibility_venue(tags, doc_specs, name="TagVis Venue"):
    """tags: list of {tag, yelp_visible}; doc_specs: list of mentioned_tags lists."""
    r = tool_CREATE_PAGE("venue", city="paris", **KW)
    pid_v = r["page_id"]; vid_v = r["record_id"]
    tool_FILL(pid_v, {
        "name": name, "category": "restaurant", "district": "Le Marais",
        "lat": 48.86, "lng": 2.35, "avg_cost_local": 25.0, "price_tier": "mid",
        "recommended_visit_minutes": 60, "booking_required": 0, "has_official_site": 0,
        "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
        "traffic_tier": "mid", "total_results": 50, "yelp_popularity_score": 0.5,
        "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
        "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
        "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
        "cuisine": "french", "city": "paris",
    }, **KW)
    tool_COMMIT(pid_v, **KW)
    tool_SET_TAGS(vid_v, tags, city="paris", **KW)
    # Insert docs directly to skip body realism; register refs.
    import json as _json
    conn = get_connection(TEST_DB)
    for i, mt in enumerate(doc_specs):
        did = new_doc_id()
        conn.execute(
            "INSERT INTO source_docs (doc_id, city, doc_type, title, author, "
            "source_name, date, likes, saves, view_count, body, "
            "mentioned_regulations, mentioned_tags, page_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, "paris", "blog", f"doc {i}", "auth", "src", "2024-01-01",
             50, 20, 500, "body text", "[]", _json.dumps(mt), "committed")
        )
        conn.execute("INSERT INTO doc_venue_refs (doc_id, venue_id) VALUES (?,?)",
                     (did, vid_v))
    conn.commit()
    return conn, vid_v

# Case 1: venue with no yelp_visible=0 tags → no errors
print("  Case 1: venue with no hidden tags")
conn_t, vid_t1c1 = _seed_tag_visibility_venue(
    tags=[{"tag": "free-entry", "yelp_visible": 1},
          {"tag": "italian", "yelp_visible": 1}],
    doc_specs=[[]],
    name="T2B-NoHiddenTags",
)
errs = _check_tag_visibility(conn_t, vid_t1c1)
check("T2-B Case 1: no hidden tags → no tag_visibility errors", len(errs) == 0,
      str(errs))
conn_t.close()

# Case 2: hidden tag covered (declared in mentioned_tags of one doc) → no errors
print("  Case 2: hidden tag declared in one of the docs")
conn_t, vid_t1c2 = _seed_tag_visibility_venue(
    tags=[{"tag": "free-entry", "yelp_visible": 1},
          {"tag": "instagrammable", "yelp_visible": 0}],
    doc_specs=[["instagrammable"], []],
    name="T2B-HiddenCovered",
)
errs = _check_tag_visibility(conn_t, vid_t1c2)
check("T2-B Case 2: hidden tag declared → no tag_visibility errors",
      len(errs) == 0, str(errs))
conn_t.close()

# Case 3: hidden tag NOT covered → error fires with tag in detail
print("  Case 3: hidden tag not declared anywhere")
conn_t, vid_t1c3 = _seed_tag_visibility_venue(
    tags=[{"tag": "free-entry", "yelp_visible": 1},
          {"tag": "instagrammable", "yelp_visible": 0}],
    doc_specs=[[], []],
    name="T2B-HiddenUncovered",
)
errs = _check_tag_visibility(conn_t, vid_t1c3)
check("T2-B Case 3: uncovered hidden tag → exactly 1 error",
      len(errs) == 1, str(errs))
check("T2-B Case 3: error mentions 'instagrammable'",
      errs and "instagrammable" in errs[0]["detail"], str(errs))
check("T2-B Case 3: check name is tag_visibility",
      errs and errs[0]["check"] == "tag_visibility", str(errs))
conn_t.close()

# Case 4: off-vocab legacy tag (yelp_visible=0 but NOT in current vocab) → exempt
print("  Case 4: off-vocab legacy hidden tag exempted")
conn_t, vid_t1c4 = _seed_tag_visibility_venue(
    tags=[{"tag": "free-entry", "yelp_visible": 1},
          {"tag": "instagrammable", "yelp_visible": 1}],
    doc_specs=[[], []],
    name="T2B-LegacyOffVocab",
)
# Insert a legacy off-vocab hidden tag directly (bypasses SET_TAGS vocab check)
conn_t.execute(
    "INSERT INTO tags (tag, city, venue_id, yelp_visible) VALUES (?,?,?,?)",
    ("photography-allowed", "paris", vid_t1c4, 0),  # removed in T2-B vocab cleanup
)
conn_t.commit()
errs = _check_tag_visibility(conn_t, vid_t1c4)
check("T2-B Case 4: legacy off-vocab hidden tag → no error (exempt)",
      len(errs) == 0, str(errs))
conn_t.close()

# Case 5: COMMIT source_doc with mentioned_tags containing >2 entries → reject
# (Note: P6-T20 made venue_id required; reuse the venue from Case 4.)
print("  Case 5: COMMIT source_doc with >2 mentioned_tags")
r_v5 = tool_CREATE_PAGE("source_doc", city="paris", venue_id=vid_t1c4, **KW)
pid_d5 = r_v5["page_id"]
tool_FILL(pid_d5, {
    "city": "paris", "doc_type": "blog", "title": "Test",
    "author": "AuthorA", "source_name": "TestSite", "date": "2024-01-01",
    "body": "Body text. " + ("Filler prose padding for P6-T19 length bounds. " * 12), "likes": 10, "saves": 5, "view_count": 100,
    "persona": "travel photographer", "tone": "emotional / sensory",
    "mentioned_tags": '["instagrammable", "rooftop", "hidden-gem"]',  # 3 tags
}, **KW)
r_c5 = tool_COMMIT(pid_d5, **KW)
check("T2-B Case 5: 3 mentioned_tags → error",
      r_c5["status"] == "error" and "caps at 2" in r_c5.get("message", ""),
      str(r_c5))

# Case 6: COMMIT source_doc with off-vocab entry in mentioned_tags → reject
print("  Case 6: COMMIT source_doc with off-vocab mentioned_tags")
r_v6 = tool_CREATE_PAGE("source_doc", city="paris", venue_id=vid_t1c4, **KW)
pid_d6 = r_v6["page_id"]
tool_FILL(pid_d6, {
    "city": "paris", "doc_type": "blog", "title": "Test",
    "author": "AuthorB", "source_name": "TestSite", "date": "2024-01-01",
    "body": "Body text. " + ("Filler prose padding for P6-T19 length bounds. " * 12), "likes": 10, "saves": 5, "view_count": 100,
    "persona": "food blogger", "tone": "analytical / measured",
    "mentioned_tags": '["instagrammable", "made-up-concept"]',
}, **KW)
r_c6 = tool_COMMIT(pid_d6, **KW)
check("T2-B Case 6: off-vocab in mentioned_tags → error",
      r_c6["status"] == "error" and "off-vocab" in r_c6.get("message", ""),
      str(r_c6))
check("T2-B Case 6: error names the off-vocab entry",
      "made-up-concept" in r_c6.get("message", ""), str(r_c6))

# Case 7: regulation-mirror concept (pet_friendly) in mentioned_tags → reject
# (Tags that mirror regulation columns are no longer in vocab after T2-B cleanup.)
print("  Case 7: regulation-mirror tag rejected by vocab check")
r_v7 = tool_CREATE_PAGE("source_doc", city="paris", venue_id=vid_t1c4, **KW)
pid_d7 = r_v7["page_id"]
tool_FILL(pid_d7, {
    "city": "paris", "doc_type": "blog", "title": "Test",
    "author": "AuthorC", "source_name": "TestSite", "date": "2024-01-01",
    "body": "Body text. " + ("Filler prose padding for P6-T19 length bounds. " * 12), "likes": 10, "saves": 5, "view_count": 100,
    "persona": "tourist on first visit", "tone": "wry / observational",
    "mentioned_tags": '["pet-friendly"]',  # removed from vocab in T2-B
}, **KW)
r_c7 = tool_COMMIT(pid_d7, **KW)
check("T2-B Case 7: regulation-mirror tag in mentioned_tags → error",
      r_c7["status"] == "error" and "off-vocab" in r_c7.get("message", ""),
      str(r_c7))

# Case 8: valid 2-tag commit accepted
print("  Case 8: valid 2-tag mentioned_tags commit")
r_v8 = tool_CREATE_PAGE("source_doc", city="paris", venue_id=vid_t1c4, **KW)
pid_d8 = r_v8["page_id"]
tool_FILL(pid_d8, {
    "city": "paris", "doc_type": "blog", "title": "Test",
    "author": "AuthorD", "source_name": "TestSite", "date": "2024-01-01",
    "body": "Body text. " + ("Filler prose padding for P6-T19 length bounds. " * 12), "likes": 10, "saves": 5, "view_count": 100,
    "persona": "local regular", "tone": "nostalgic / reflective",
    "mentioned_tags": ["instagrammable", "popular-with-locals"],  # list form
}, **KW)
r_c8 = tool_COMMIT(pid_d8, **KW)
check("T2-B Case 8: 2 valid mentioned_tags (list form) → committed",
      r_c8["status"] == "committed", str(r_c8))


# ─── P6-T14 + P6-T15: forum shape validator + author reuse cap ───────────────
print("\n[P6-T14 + P6-T15] forum shape + author cap")

from scripts.generation.agent_tools import _extract_forum_handles


_PADDING_FILLER = (
    " Filler prose to satisfy the P6-T19 body-length bounds — this is a "
    "placeholder body used in unit tests, padded out so the COMMIT-time "
    "length check accepts the doc. The content here is intentionally bland "
    "and not exercised by any other test. Pad pad pad pad pad pad pad pad "
    "pad pad pad pad pad pad pad pad pad pad pad pad pad pad pad pad pad."
) * 3   # ~1.2k chars; comfortably inside both blog and forum bounds.


_AUTO_VENUE_COUNTER = {"n": 0}

def _ensure_test_venue(city="paris"):
    """Create a throwaway test venue in the given city and return its venue_id.
    Used by `_make_source_doc` when the caller doesn't care about venue scope —
    each call creates a fresh venue so within-venue checks (T16 persona
    uniqueness) don't accidentally fire between unrelated tests."""
    _AUTO_VENUE_COUNTER["n"] += 1
    suffix = _AUTO_VENUE_COUNTER["n"]
    r = tool_CREATE_PAGE("venue", city=city, **KW)
    vid = r["record_id"]
    tool_FILL(r["page_id"], {
        "name": f"AutoTestVenue_{suffix}", "category": "restaurant", "district": "X",
        "lat": 48.86, "lng": 2.35, "avg_cost_local": 25.0, "price_tier": "mid",
        "recommended_visit_minutes": 60, "booking_required": 0, "has_official_site": 0,
        "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
        "traffic_tier": "mid", "total_results": 50, "yelp_popularity_score": 0.5,
        "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
        "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
        "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
        "cuisine": "french", "city": city,
    }, **KW)
    tool_COMMIT(r["page_id"], **KW)
    return vid


def _make_source_doc(author, doc_type, body, city="paris", mentioned_tags=None,
                     persona="travel photographer", tone="emotional / sensory",
                     venue_id=None, pad=True):
    """Create a source_doc page, FILL it with the given fields, return page_id.
    Defaults to a valid persona+tone so the doc is COMMIT-able without extra
    setup.

    Since P6-T20, CREATE_PAGE("source_doc") requires venue_id and pre-populates
    doc_venue_refs at creation. When the caller doesn't pass venue_id, this
    helper creates a fresh throwaway venue so the within-venue persona check
    (T16) doesn't fire accidentally across unrelated test calls. Tests that
    DO want to exercise within-venue uniqueness pass `venue_id=X` explicitly.

    By default short test bodies are auto-padded to satisfy the P6-T19
    body-length bounds (400+ for blog, 300+ for forum). Pass `pad=False` to
    keep the body exactly as given (for tests that explicitly target bounds)."""
    if pad:
        min_len = 400 if doc_type == "blog" else 300
        if body and len(body) < min_len:
            body = (body + _PADDING_FILLER)[:min_len + 200]
    if venue_id is None:
        venue_id = _ensure_test_venue(city)
    r = tool_CREATE_PAGE("source_doc", city=city, venue_id=venue_id, **KW)
    pid = r["page_id"]
    fields = {
        "city": city, "doc_type": doc_type, "title": "T",
        "author": author, "source_name": "SiteX", "date": "2024-01-01",
        "body": body, "likes": 10, "saves": 5, "view_count": 100,
        "persona": persona, "tone": tone,
    }
    if mentioned_tags is not None:
        fields["mentioned_tags"] = mentioned_tags
    tool_FILL(pid, fields, **KW)
    return pid


# ── _extract_forum_handles regex sanity ─────────────────────────────────────
print("  _extract_forum_handles regex")
h = _extract_forum_handles(
    "OP question.\n\n**Reply from shoreditch_dan:** answer\n\n"
    "**Reply from bass_kween:** another"
)
check("regex: 'Reply from X' bold markdown → 2 distinct",
      len({x.lower() for x in h}) >= 2, str(h))
h = _extract_forum_handles(
    "NorthLondonMum: hi\nLDN_Dad32: reply\nEastEnderSarah: another"
)
check("regex: '<Handle>:' style → 3 distinct",
      len({x.lower() for x in h}) == 3, str(h))
h = _extract_forum_handles(
    "Note: this is a tip.\nUpdate: revised.\nThread title: foo"
)
check("regex: 'Note:' / 'Update:' / 'Thread title:' excluded → 0",
      len(h) == 0, str(h))
h = _extract_forum_handles(
    "Reply from User 'shoreditch_dan' on 2024-11-03:\nanswer.\n"
    "Reply from User 'eastlondon_eater' on 2024-11-03:\nthanks!"
)
check("regex: formal \"Reply from User 'X' on date:\" → 2 distinct",
      len({x.lower() for x in h}) == 2, str(h))


# ── P6-T14.1 — Forum doc with proper 'Reply from X' pattern → accepted ──────
print("  P6-T14: forum docs")
good_forum_1 = (
    "Thinking of heading to a club Thursday night solo. Anyone got tips?\n\n"
    "---\n\n"
    "**Reply from shoreditch_dan:** Been loads of times solo, Thursdays are\n"
    "actually the best night for it. Door staff are fine if you're not rowdy.\n\n"
    "**Reply from bass_kween:** Agree with Dan. Sound system is incredible —\n"
    "actually better solo because you can just zone out near the booth."
)
pid_f1 = _make_source_doc("forum_op_lxndr", "forum", good_forum_1)
r_f1 = tool_COMMIT(pid_f1, **KW)
check("P6-T14.1: forum 'Reply from X' x2 distinct → committed",
      r_f1["status"] == "committed", str(r_f1)[:200])

# ── P6-T14.2 — Forum doc with '<Handle>:' bare pattern → accepted ───────────
good_forum_2 = (
    "Thread title: Bringing a toddler somewhere busy — manageable?\n\n"
    "NorthLondonMum: Thinking of taking our 3-year-old. Doable?\n\n"
    "LDN_Dad32: We took our 2-year-old last month. It's busy but you can\n"
    "dip in and out. Toilets are an issue though.\n\n"
    "EastEnderSarah: Went with my 4yo. Fine if you go early — aim for 10am."
)
pid_f2 = _make_source_doc("forum_op_NLM", "forum", good_forum_2)
r_f2 = tool_COMMIT(pid_f2, **KW)
check("P6-T14.2: forum '<Handle>:' 3 distinct → committed",
      r_f2["status"] == "committed", str(r_f2)[:200])

# ── P6-T14.3 — Single-voice monologue with doc_type='forum' → rejected ──────
bad_forum_monologue = (
    "Been going to this place for two years. It's a railway arch roastery,\n"
    "pretty stripped back, no frills. Good coffee. They roast onsite which\n"
    "you can smell as soon as you walk in. Worth the trip from anywhere."
)
pid_f3 = _make_source_doc("forum_op_solo", "forum", bad_forum_monologue)
r_f3 = tool_COMMIT(pid_f3, **KW)
check("P6-T14.3: forum monologue → rejected",
      r_f3["status"] == "error", str(r_f3)[:200])
check("P6-T14.3: error mentions ≥2 turn markers",
      "≥2" in r_f3.get("message", "") or "distinct" in r_f3.get("message", ""),
      str(r_f3)[:200])

# ── P6-T14.4 — All replies by SAME handle → rejected ────────────────────────
bad_forum_same_handle = (
    "OP asks a question here.\n\n"
    "Reply from shoreditch_dan: first answer.\n"
    "Reply from shoreditch_dan: actually one more thought.\n"
    "Reply from shoreditch_dan: and a third."
)
pid_f4 = _make_source_doc("forum_op_x", "forum", bad_forum_same_handle)
r_f4 = tool_COMMIT(pid_f4, **KW)
check("P6-T14.4: 3 replies all from one handle → rejected",
      r_f4["status"] == "error", str(r_f4)[:200])

# ── P6-T14.5 — Blog doc with single-voice narrative → accepted ──────────────
# (forum-shape rule only fires on doc_type='forum')
pid_f5 = _make_source_doc("blogger-prose-1", "blog", bad_forum_monologue)
r_f5 = tool_COMMIT(pid_f5, **KW)
check("P6-T14.5: blog with single-voice body → committed (rule is forum-only)",
      r_f5["status"] == "committed", str(r_f5)[:200])

# ── P6-T14.6 — Forum doc with only 'Note:' / 'Update:' labels → rejected ────
bad_forum_labels_only = (
    "Heading to a venue this weekend, here's what I learned:\n\n"
    "Note: closes early on Sundays.\n"
    "Update: prices went up last month.\n"
    "Edit: also no card payment.\n"
    "Tip: book ahead."
)
pid_f6 = _make_source_doc("forum_op_y", "forum", bad_forum_labels_only)
r_f6 = tool_COMMIT(pid_f6, **KW)
check("P6-T14.6: forum with only field-label ':' lines → rejected",
      r_f6["status"] == "error", str(r_f6)[:200])


# ── P6-T15: author reuse cap ────────────────────────────────────────────────
print("  P6-T15: author reuse cap")
# Use a fresh paris-scoped author for isolation from earlier tests.
pid_a1 = _make_source_doc("Cap Test Author", "blog",
                          "First post by this author.")
r_a1 = tool_COMMIT(pid_a1, **KW)
check("P6-T15.1a: 1st doc by 'Cap Test Author' → committed",
      r_a1["status"] == "committed", str(r_a1)[:200])

pid_a2 = _make_source_doc("Cap Test Author", "blog",
                          "Second post by this author.")
r_a2 = tool_COMMIT(pid_a2, **KW)
check("P6-T15.1b: 2nd doc by 'Cap Test Author' → committed",
      r_a2["status"] == "committed", str(r_a2)[:200])

pid_a3 = _make_source_doc("Cap Test Author", "blog",
                          "Third post by this author.")
r_a3 = tool_COMMIT(pid_a3, **KW)
check("P6-T15.1c: 3rd doc by 'Cap Test Author' → rejected",
      r_a3["status"] == "error", str(r_a3)[:200])
check("P6-T15.1c: error mentions author name and city",
      "Cap Test Author" in r_a3.get("message", "")
      and "paris" in r_a3.get("message", ""),
      r_a3.get("message", "")[:200])

# ── P6-T15.2 — Case-insensitive collision ───────────────────────────────────
pid_b1 = _make_source_doc("Casey Strip", "blog", "first.")
tool_COMMIT(pid_b1, **KW)
pid_b2 = _make_source_doc("CASEY STRIP", "blog", "second.")
tool_COMMIT(pid_b2, **KW)
pid_b3 = _make_source_doc("  casey strip  ", "blog", "third.")
r_b3 = tool_COMMIT(pid_b3, **KW)
check("P6-T15.2: case-insensitive + whitespace collision → 3rd rejected",
      r_b3["status"] == "error", str(r_b3)[:200])

# ── P6-T15.3 — Same author in DIFFERENT city → not blocked ──────────────────
# Seed a second city
conn = get_connection(TEST_DB)
conn.execute(
    "INSERT INTO city_config (city, display_name, country, centre_lat, centre_lng, "
    "radius_km, local_cuisine_label, task_dates) VALUES (?,?,?,?,?,?,?,?)",
    ("rome", "Rome", "Italy", 41.9, 12.5, 5.0, "italian", '["2025-04-01"]')
)
conn.commit()
conn.close()

# Already have 2 'Casey Strip' docs in paris; a 3rd in rome should pass.
pid_b4 = _make_source_doc("Casey Strip", "blog", "rome trip.", city="rome")
r_b4 = tool_COMMIT(pid_b4, **KW)
check("P6-T15.3: same author in different city → committed",
      r_b4["status"] == "committed", str(r_b4)[:200])

# ── P6-T15.4 — Draft docs don't count toward the cap ────────────────────────
# Create 2 drafts (no COMMIT) by a fresh author, then a COMMIT should still pass.
pid_c1 = _make_source_doc("Draft Author", "blog", "draft 1.")
pid_c2 = _make_source_doc("Draft Author", "blog", "draft 2.")
# Neither committed. Now create a 3rd and COMMIT it — should pass since drafts
# don't count.
pid_c3 = _make_source_doc("Draft Author", "blog", "real commit.")
r_c3 = tool_COMMIT(pid_c3, **KW)
check("P6-T15.4: drafts don't count toward author cap → 1st commit passes",
      r_c3["status"] == "committed", str(r_c3)[:200])


# ─── P6-T16: declared persona+tone + within-venue variety ──────────────────
print("\n[P6-T16] declared persona+tone + within-venue variety")

from scripts.generation.handbook import (
    CANONICAL_PERSONAS, CANONICAL_TONES, _normalize_persona, _normalize_tone,
)

# T16.1 — happy path: doc with valid persona+tone → committed
print("  T16.1: valid persona+tone → committed")
pid_t1 = _make_source_doc("T16 Author One", "blog", "body one.",
                           persona="travel photographer",
                           tone="emotional / sensory")
r_t1 = tool_COMMIT(pid_t1, **KW)
check("T16.1: valid persona+tone → committed",
      r_t1["status"] == "committed", str(r_t1)[:200])

# T16.2 — missing persona → incomplete
print("  T16.2: missing persona → incomplete")
_t2_vid = _ensure_test_venue("paris")
r_t2_create = tool_CREATE_PAGE("source_doc", city="paris", venue_id=_t2_vid, **KW)
pid_t2 = r_t2_create["page_id"]
tool_FILL(pid_t2, {
    "city": "paris", "doc_type": "blog", "title": "T",
    "author": "T16 Author Two", "source_name": "X", "date": "2024-01-01",
    "body": "Body." + _PADDING_FILLER[:500], "likes": 10, "saves": 5, "view_count": 100,
    "tone": "terse / factual",  # persona missing
}, **KW)
r_t2 = tool_COMMIT(pid_t2, **KW)
check("T16.2a: missing persona → incomplete",
      r_t2["status"] == "incomplete"
      and "persona" in r_t2.get("missing_required", []),
      str(r_t2)[:200])

# T16.2b — missing tone → incomplete
_t2b_vid = _ensure_test_venue("paris")
r_t2b_create = tool_CREATE_PAGE("source_doc", city="paris", venue_id=_t2b_vid, **KW)
pid_t2b = r_t2b_create["page_id"]
tool_FILL(pid_t2b, {
    "city": "paris", "doc_type": "blog", "title": "T",
    "author": "T16 Author Two B", "source_name": "X", "date": "2024-01-01",
    "body": "Body." + _PADDING_FILLER[:500], "likes": 10, "saves": 5, "view_count": 100,
    "persona": "local regular",  # tone missing
}, **KW)
r_t2b = tool_COMMIT(pid_t2b, **KW)
check("T16.2b: missing tone → incomplete",
      r_t2b["status"] == "incomplete"
      and "tone" in r_t2b.get("missing_required", []),
      str(r_t2b)[:200])

# T16.3 — invented persona (off-vocab) → error
print("  T16.3: invented persona → rejected")
pid_t3 = _make_source_doc("T16 Author Three", "blog", "body.",
                           persona="weird invented persona",
                           tone="terse / factual")
r_t3 = tool_COMMIT(pid_t3, **KW)
check("T16.3: off-vocab persona → error",
      r_t3["status"] == "error"
      and "canonical persona set" in r_t3.get("message", ""),
      str(r_t3)[:200])

# T16.4 — persona normalization (case + extra spaces)
print("  T16.4: persona normalization")
pid_t4 = _make_source_doc("T16 Author Four", "blog", "body.",
                           persona="  Travel Photographer  ",
                           tone="emotional / sensory")
r_t4 = tool_COMMIT(pid_t4, **KW)
check("T16.4: persona='  Travel Photographer  ' accepted",
      r_t4["status"] == "committed", str(r_t4)[:200])

# T16.5 — invented tone (open vocab) → accepted
print("  T16.5: invented tone accepted (open vocab)")
pid_t5 = _make_source_doc("T16 Author Five", "blog", "body.",
                           persona="parent with kids",
                           tone="wry observational")  # not in canonical set
r_t5 = tool_COMMIT(pid_t5, **KW)
check("T16.5: invented tone accepted",
      r_t5["status"] == "committed", str(r_t5)[:200])

# T16.6 — persona repeat WITHIN a venue → rejected
print("  T16.6: persona repeat within venue → rejected")
# Build a venue and link two docs with the same persona to it.
r_t6v = tool_CREATE_PAGE("venue", city="paris", **KW)
vid_t6 = r_t6v["record_id"]
tool_FILL(r_t6v["page_id"], {
    "name": "T16 Venue", "category": "restaurant", "district": "X",
    "lat": 48.86, "lng": 2.35, "avg_cost_local": 25.0, "price_tier": "mid",
    "recommended_visit_minutes": 60, "booking_required": 0, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "mid", "total_results": 50, "yelp_popularity_score": 0.5,
    "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
    "cuisine": "french", "city": "paris",
}, **KW)
tool_COMMIT(r_t6v["page_id"], **KW)

# First doc: travel photographer → committed
pid_t6a = _make_source_doc("T16 V Author A", "blog", "body a.",
                            persona="travel photographer",
                            tone="emotional / sensory",
                            venue_id=vid_t6)
r_t6a = tool_COMMIT(pid_t6a, **KW)
check("T16.6a: first doc on venue → committed",
      r_t6a["status"] == "committed", str(r_t6a)[:200])

# Second doc on same venue, SAME persona → should reject
pid_t6b = _make_source_doc("T16 V Author B", "blog", "body b.",
                            persona="travel photographer",   # repeat!
                            tone="analytical / measured",
                            venue_id=vid_t6)
r_t6b = tool_COMMIT(pid_t6b, **KW)
check("T16.6b: persona repeat on same venue → rejected",
      r_t6b["status"] == "error"
      and "already used by source_doc" in r_t6b.get("message", ""),
      str(r_t6b)[:200])

# Second doc with a DIFFERENT persona → accepted
pid_t6c = _make_source_doc("T16 V Author C", "blog", "body c.",
                            persona="local regular",
                            tone="terse / factual",
                            venue_id=vid_t6)
r_t6c = tool_COMMIT(pid_t6c, **KW)
check("T16.6c: different persona on same venue → committed",
      r_t6c["status"] == "committed", str(r_t6c)[:200])

# T16.7 — same persona on a DIFFERENT venue → accepted (variety is per-venue)
print("  T16.7: same persona on different venue → accepted")
r_t7v = tool_CREATE_PAGE("venue", city="paris", **KW)
vid_t7 = r_t7v["record_id"]
tool_FILL(r_t7v["page_id"], {
    "name": "T16 Venue 2", "category": "restaurant", "district": "X",
    "lat": 48.86, "lng": 2.35, "avg_cost_local": 25.0, "price_tier": "mid",
    "recommended_visit_minutes": 60, "booking_required": 0, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "mid", "total_results": 50, "yelp_popularity_score": 0.5,
    "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
    "cuisine": "french", "city": "paris",
}, **KW)
tool_COMMIT(r_t7v["page_id"], **KW)

pid_t7 = _make_source_doc("T16 V2 Author A", "blog", "body.",
                           persona="travel photographer",
                           tone="emotional / sensory",
                           venue_id=vid_t7)
r_t7 = tool_COMMIT(pid_t7, **KW)
check("T16.7: same persona on different venue → committed",
      r_t7["status"] == "committed", str(r_t7)[:200])

# T16.8 + T16.9 + T16.10 — CREATE_PAGE returns context dict
print("  T16.8/9/10: CREATE_PAGE source_doc returns context dict")
r_t8 = tool_CREATE_PAGE("source_doc", city="paris", venue_id=vid_t6, **KW)
check("T16.8a: response includes personas_used_for_venue",
      "personas_used_for_venue" in r_t8, str(r_t8.keys()))
check("T16.8b: personas_used_for_venue lists both committed personas",
      set(_normalize_persona(p) for p in r_t8.get("personas_used_for_venue", []))
        >= {_normalize_persona("travel photographer"),
            _normalize_persona("local regular")},
      str(r_t8.get("personas_used_for_venue")))
check("T16.8c: response includes personas_still_available",
      "personas_still_available" in r_t8
      and len(r_t8["personas_still_available"]) == 5,  # 7 - 2 used
      str(r_t8.get("personas_still_available")))
check("T16.9a: response includes city_tone_counts",
      "city_tone_counts" in r_t8 and isinstance(r_t8["city_tone_counts"], dict),
      str(r_t8.get("city_tone_counts"))[:200])
check("T16.9b: city_tone_counts has all 10 canonical + _invented buckets",
      set(r_t8["city_tone_counts"].keys()) ==
        CANONICAL_TONES | {"_invented"},
      str(r_t8["city_tone_counts"].keys()))
check("T16.9c: city_tone_target_per_bucket > 0",
      r_t8.get("city_tone_target_per_bucket", 0) > 0,
      str(r_t8.get("city_tone_target_per_bucket")))

# T16.10 — Invented tones aggregate into _invented bucket
# T16.5 above used "wry observational" (no slash) which is NOT in canonical
# (canonical has "wry / observational" with the slash). Confirm it landed in
# the _invented bucket on the city counter.
counts = r_t8["city_tone_counts"]
canonical_match_for_wry = next(
    (t for t in CANONICAL_TONES if _normalize_tone(t) == _normalize_tone("wry observational")),
    None,
)
if canonical_match_for_wry:
    # No-op if the normalizer happens to merge them
    print(f"  (normalization merged 'wry observational' → {canonical_match_for_wry})")
else:
    check("T16.10: 'wry observational' aggregates into _invented bucket",
          counts.get("_invented", 0) >= 1, str(counts))


# ─── P6-T18: handle-signature soft cap ──────────────────────────────────────
print("\n[P6-T18] handle-signature soft cap")

from scripts.generation.agent_tools import _handle_signature

# T18.1 — signature classifier sanity
print("  T18.1: signature classifier")
sig_cases = [
    ("hackney_jim",       "lower_snake"),
    ("shoreditch_dan",    "lower_snake"),
    ("LDN_Dad32",         "with_digit"),
    ("Laura_83",          "with_digit"),
    ("Marcus Chen",       "multi_word"),
    ("MumOnTheMove",      "mixed_case_compound"),
    ("northLondonMum",    "mixed_case_compound"),
    ("East_London_Mum",   "mixed_snake"),
    ("marcus",            "single_lower"),
    ("",                  "other"),
]
for handle, expected in sig_cases:
    actual = _handle_signature(handle)
    check(f"T18.1: signature('{handle}') == '{expected}'",
          actual == expected, f"got '{actual}'")

# T18.2 — under floor (<10 docs), no cap fires
print("  T18.2: under 10-doc floor, no cap fires")
# Use a fresh city to isolate (rome was seeded earlier in T15.3)
# Seed 6 lower_snake handles. None should reject.
under_floor_ok = True
for i, h in enumerate(["a_b", "c_d", "e_f", "g_h", "i_j", "k_l"]):
    p = _make_source_doc(h, "blog", f"body for {h}", city="rome")
    r = tool_COMMIT(p, **KW)
    if r["status"] != "committed":
        under_floor_ok = False
        print(f"    unexpected reject for handle '{h}' under floor: {r.get('message','')[:120]}")
        break
check("T18.2: 6 lower_snake handles in fresh city all accepted (floor=10)",
      under_floor_ok)

# T18.3 — at floor, cap kicks in
print("  T18.3: at 10-doc floor, lower_snake dominance triggers cap")
# Add 4 more lower_snake handles to reach 10 total.
for h in ["m_n", "o_p", "q_r", "s_t"]:
    p = _make_source_doc(h, "blog", f"body for {h}", city="rome")
    tool_COMMIT(p, **KW)
# Now city has 10 lower_snake handles, all 10 of that signature → 100% > 40%.
# The next lower_snake handle should be rejected.
p_cap = _make_source_doc("u_v", "blog", "body for u_v", city="rome")
r_cap = tool_COMMIT(p_cap, **KW)
check("T18.3a: 11th lower_snake handle rejected (>40% of 10)",
      r_cap["status"] == "error"
      and "lower_snake" in r_cap.get("message", ""),
      str(r_cap)[:200])

# A different shape on the same city should still pass.
p_alt = _make_source_doc("Marcus Smith", "blog", "alt shape", city="rome")
r_alt = tool_COMMIT(p_alt, **KW)
check("T18.3b: different shape (multi_word) accepted",
      r_alt["status"] == "committed", str(r_alt)[:200])

# T18.4 — sig cap is per-city
print("  T18.4: per-city scope")
# Paris already has many docs of various shapes. Confirm a fresh lower_snake
# author in paris is NOT blocked by rome's lower_snake saturation.
p_par = _make_source_doc("paris_local", "blog", "paris", city="paris")
r_par = tool_COMMIT(p_par, **KW)
check("T18.4: lower_snake in paris not blocked by rome saturation",
      r_par["status"] == "committed", str(r_par)[:200])


# ─── P6-T19: body-length bounds + CREATE_PAGE distribution ──────────────────
print("\n[P6-T19] body-length bounds + distribution surface")

# T19.1 — blog body BELOW 400 → reject
print("  T19.1: blog body below 400 chars → rejected")
p_short = _make_source_doc("T19 Author Short", "blog", "x" * 50,
                            city="paris", pad=False)
r_short = tool_COMMIT(p_short, **KW)
check("T19.1: blog body 50 chars → rejected",
      r_short["status"] == "error"
      and "outside the expected range" in r_short.get("message", ""),
      str(r_short)[:200])

# T19.2 — blog body ABOVE 2500 → reject
print("  T19.2: blog body above 2500 chars → rejected")
p_long = _make_source_doc("T19 Author Long", "blog", "x" * 3000,
                           city="paris", pad=False)
r_long = tool_COMMIT(p_long, **KW)
check("T19.2: blog body 3000 chars → rejected",
      r_long["status"] == "error"
      and "outside the expected range" in r_long.get("message", ""),
      str(r_long)[:200])

# T19.3 — forum body BELOW 300 → reject
print("  T19.3: forum body below 300 chars → rejected")
forum_body = (
    "OP: short.\n\n"
    "Reply from Alice_99: also short.\n"
    "Reply from BoBob_22: too short.\n"
)
p_fshort = _make_source_doc("T19 Forum Short", "forum", forum_body,
                             city="paris", pad=False)
r_fshort = tool_COMMIT(p_fshort, **KW)
check("T19.3: forum body <300 chars → rejected",
      r_fshort["status"] == "error"
      and "outside the expected range" in r_fshort.get("message", ""),
      str(r_fshort)[:200])

# T19.4 — body just inside bounds → accepted
print("  T19.4: blog body 450 chars → accepted")
p_ok = _make_source_doc("T19 Author OK", "blog", "x" * 450,
                         city="paris", pad=False)
r_ok = tool_COMMIT(p_ok, **KW)
check("T19.4: blog body 450 chars (inside 400-2500) → committed",
      r_ok["status"] == "committed", str(r_ok)[:200])

# T19.5 — CREATE_PAGE response includes body-length distribution
print("  T19.5: CREATE_PAGE response includes city_body_length_counts")
_t19_5_vid = _ensure_test_venue("paris")
r_cp = tool_CREATE_PAGE("source_doc", city="paris", venue_id=_t19_5_vid, **KW)
check("T19.5a: response has city_body_length_counts",
      "city_body_length_counts" in r_cp, str(r_cp.keys()))
check("T19.5b: counts has blog + forum keys with all 4 bins",
      set(r_cp["city_body_length_counts"].keys()) == {"blog", "forum"}
      and set(r_cp["city_body_length_counts"]["blog"].keys()) ==
            {"short", "medium", "long", "very_long"},
      str(r_cp.get("city_body_length_counts")))
check("T19.5c: response has body_length_bounds",
      "body_length_bounds" in r_cp
      and r_cp["body_length_bounds"]["blog"]["min"] == 400
      and r_cp["body_length_bounds"]["forum"]["max"] == 2000,
      str(r_cp.get("body_length_bounds")))


# ─── P6-T20: pipeline audit fixes ────────────────────────────────────────────
print("\n[P6-T20] pipeline audit fixes")

# T20.1 — CREATE_PAGE("source_doc") without venue_id → error
print("  T20.1: CREATE_PAGE source_doc without venue_id → error")
r_t20_1 = tool_CREATE_PAGE("source_doc", city="paris", **KW)
check("T20.1: missing venue_id rejected",
      r_t20_1["status"] == "error" and "venue_id required" in r_t20_1.get("message", ""),
      str(r_t20_1)[:200])

# T20.2 — CREATE_PAGE("source_doc", venue_id="BOGUS") → error (FK)
print("  T20.2: CREATE_PAGE source_doc with bogus venue_id → error")
r_t20_2 = tool_CREATE_PAGE("source_doc", city="paris", venue_id="BOGUS_VID", **KW)
check("T20.2: bogus venue_id rejected",
      r_t20_2["status"] == "error" and "not found" in r_t20_2.get("message", ""),
      str(r_t20_2)[:200])

# T20.3 — CREATE_PAGE with valid venue_id → doc_venue_refs row immediately exists
print("  T20.3: CREATE_PAGE with valid venue_id pre-populates doc_venue_refs")
_t20_3_vid = _ensure_test_venue("paris")
r_t20_3 = tool_CREATE_PAGE("source_doc", city="paris", venue_id=_t20_3_vid, **KW)
_t20_3_did = r_t20_3["record_id"]
_conn = get_connection(TEST_DB)
_ref_count = _conn.execute(
    "SELECT COUNT(*) FROM doc_venue_refs WHERE doc_id = ? AND venue_id = ?",
    (_t20_3_did, _t20_3_vid),
).fetchone()[0]
_conn.close()
check("T20.3: doc_venue_refs row exists after CREATE_PAGE",
      _ref_count == 1, f"count={_ref_count}")

# T20.4 — REAL-FLOW regression for Bug #1 — T16 persona check actually fires
# Production flow: CREATE_PAGE(venue_id=X) → FILL → COMMIT (no manual ref insert).
# Before P6-T20 Fix #1 this was a false-green; now it should reject the 2nd
# doc with the same persona on the same venue.
print("  T20.4: real-flow T16 within-venue persona check actually fires")
_vid_rf = _ensure_test_venue("paris")

_r_rf1 = tool_CREATE_PAGE("source_doc", city="paris", venue_id=_vid_rf, **KW)
tool_FILL(_r_rf1["page_id"], {
    "city": "paris", "doc_type": "blog", "title": "T",
    "author": "RF Author One", "source_name": "S", "date": "2024-01-01",
    "body": _PADDING_FILLER[:500], "likes": 10, "saves": 5, "view_count": 100,
    "persona": "food blogger", "tone": "analytical / measured",
}, **KW)
_r_rf1_c = tool_COMMIT(_r_rf1["page_id"], **KW)
check("T20.4a: 1st doc on venue (real flow) → committed",
      _r_rf1_c["status"] == "committed", str(_r_rf1_c)[:200])

_r_rf2 = tool_CREATE_PAGE("source_doc", city="paris", venue_id=_vid_rf, **KW)
tool_FILL(_r_rf2["page_id"], {
    "city": "paris", "doc_type": "blog", "title": "T2",
    "author": "RF Author Two", "source_name": "S", "date": "2024-01-02",
    "body": _PADDING_FILLER[:500], "likes": 10, "saves": 5, "view_count": 100,
    "persona": "food blogger",                       # REUSED on same venue
    "tone": "wry / observational",
}, **KW)
_r_rf2_c = tool_COMMIT(_r_rf2["page_id"], **KW)
check("T20.4b: 2nd doc reusing persona on same venue (real flow) → rejected",
      _r_rf2_c["status"] == "error"
      and "already used by source_doc" in _r_rf2_c.get("message", ""),
      str(_r_rf2_c)[:200])

# Same venue, different persona → committed
_r_rf3 = tool_CREATE_PAGE("source_doc", city="paris", venue_id=_vid_rf, **KW)
tool_FILL(_r_rf3["page_id"], {
    "city": "paris", "doc_type": "blog", "title": "T3",
    "author": "RF Author Three", "source_name": "S", "date": "2024-01-03",
    "body": _PADDING_FILLER[:500], "likes": 10, "saves": 5, "view_count": 100,
    "persona": "local regular",                      # different
    "tone": "terse / factual",
}, **KW)
_r_rf3_c = tool_COMMIT(_r_rf3["page_id"], **KW)
check("T20.4c: different persona on same venue (real flow) → committed",
      _r_rf3_c["status"] == "committed", str(_r_rf3_c)[:200])


# ─── P6-T21: city-boundary venue check ──────────────────────────────────────
print("\n[P6-T21] city-boundary venue check (point-in-polygon)")

from scripts.generation.agent_tools import _point_in_polygon

# T21.1 — point-in-polygon helper sanity
print("  T21.1: point-in-polygon helper")
# Simple square Polygon
sq = {"type": "Polygon", "coordinates": [[
    [-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0], [-1.0, -1.0]
]]}
check("T21.1a: inside square", _point_in_polygon(0.5, 0.5, sq) is True)
check("T21.1b: outside square", _point_in_polygon(2.0, 2.0, sq) is False)
# Polygon with a hole
hole_poly = {"type": "Polygon", "coordinates": [
    [[-2.0, -2.0], [2.0, -2.0], [2.0, 2.0], [-2.0, 2.0], [-2.0, -2.0]],  # outer
    [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5], [-0.5, -0.5]],   # hole
]}
check("T21.1c: point inside outer but in hole → False",
      _point_in_polygon(0.0, 0.0, hole_poly) is False)
check("T21.1d: point inside outer outside hole → True",
      _point_in_polygon(1.0, 1.0, hole_poly) is True)
# MultiPolygon
mp = {"type": "MultiPolygon", "coordinates": [
    sq["coordinates"],
    [[[10.0, 10.0], [12.0, 10.0], [12.0, 12.0], [10.0, 12.0], [10.0, 10.0]]],
]}
check("T21.1e: point in first poly of MultiPolygon",
      _point_in_polygon(0.0, 0.0, mp) is True)
check("T21.1f: point in second poly of MultiPolygon",
      _point_in_polygon(11.0, 11.0, mp) is True)
check("T21.1g: point in neither → False",
      _point_in_polygon(5.0, 5.0, mp) is False)
# Empty / None → True (legacy bypass)
check("T21.1h: empty geojson → True (legacy bypass)",
      _point_in_polygon(0.0, 0.0, {}) is True)
check("T21.1i: None geojson → True (legacy bypass)",
      _point_in_polygon(0.0, 0.0, None) is True)

# T21.2 — venue COMMIT rejects out-of-polygon coords
print("  T21.2: venue COMMIT respects city boundary")
import json as _json
# Seed paris with a small square polygon around its centre
_paris_poly = _json.dumps({"type": "Polygon", "coordinates": [[
    [2.30, 48.84], [2.40, 48.84], [2.40, 48.90], [2.30, 48.90], [2.30, 48.84]
]]})
conn = get_connection(TEST_DB)
conn.execute("UPDATE city_config SET boundary_geojson = ? WHERE city = 'paris'",
             (_paris_poly,))
conn.commit()
conn.close()

# In-polygon venue commits cleanly
_r_in = tool_CREATE_PAGE("venue", city="paris", **KW)
tool_FILL(_r_in["page_id"], {
    "name": "InsideVenue", "category": "restaurant", "district": "X",
    "lat": 48.86, "lng": 2.35, "avg_cost_local": 25.0, "price_tier": "mid",
    "recommended_visit_minutes": 60, "booking_required": 0, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "mid", "total_results": 50, "yelp_popularity_score": 0.5,
    "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
    "cuisine": "french", "city": "paris",
}, **KW)
_r_in_c = tool_COMMIT(_r_in["page_id"], **KW)
check("T21.2a: venue inside polygon → committed",
      _r_in_c["status"] == "committed", str(_r_in_c)[:200])

# Out-of-polygon venue is rejected
_r_out = tool_CREATE_PAGE("venue", city="paris", **KW)
tool_FILL(_r_out["page_id"], {
    "name": "OutsideVenue", "category": "restaurant", "district": "X",
    "lat": 49.00, "lng": 2.50, "avg_cost_local": 25.0, "price_tier": "mid",
    "recommended_visit_minutes": 60, "booking_required": 0, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "mid", "total_results": 50, "yelp_popularity_score": 0.5,
    "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
    "cuisine": "french", "city": "paris",
}, **KW)
_r_out_c = tool_COMMIT(_r_out["page_id"], **KW)
check("T21.2b: venue outside polygon → rejected",
      _r_out_c["status"] == "error" and "OUTSIDE" in _r_out_c.get("message", ""),
      str(_r_out_c)[:200])

# T21.3 — legacy city (no polygon) is not blocked
print("  T21.3: legacy city with no polygon is not blocked")
conn = get_connection(TEST_DB)
conn.execute("UPDATE city_config SET boundary_geojson = '' WHERE city = 'paris'")
conn.commit()
conn.close()
_r_legacy = tool_CREATE_PAGE("venue", city="paris", **KW)
tool_FILL(_r_legacy["page_id"], {
    "name": "LegacyVenue", "category": "restaurant", "district": "X",
    "lat": 49.00, "lng": 2.50, "avg_cost_local": 25.0, "price_tier": "mid",
    "recommended_visit_minutes": 60, "booking_required": 0, "has_official_site": 0,
    "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
    "traffic_tier": "mid", "total_results": 50, "yelp_popularity_score": 0.5,
    "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
    "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
    "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
    "cuisine": "french", "city": "paris",
}, **KW)
_r_legacy_c = tool_COMMIT(_r_legacy["page_id"], **KW)
check("T21.3: out-of-bbox venue in city w/o polygon → committed (legacy bypass)",
      _r_legacy_c["status"] == "committed", str(_r_legacy_c)[:200])


# ─── P6-T22: atomic wrong_info workflow ─────────────────────────────────────
print("\n[P6-T22] atomic wrong_info workflow")


def _t22_make_venue(name_suffix):
    """Fresh restaurant venue for T22 tests."""
    r = tool_CREATE_PAGE("venue", city="paris", **KW)
    tool_FILL(r["page_id"], {
        "name": f"T22 Venue {name_suffix}", "category": "restaurant", "district": "X",
        "lat": 48.86, "lng": 2.35, "avg_cost_local": 25.0, "price_tier": "mid",
        "recommended_visit_minutes": 60, "booking_required": 0, "has_official_site": 0,
        "outdoor_sensitivity": "indoor", "recommended_pace": "moderate",
        "traffic_tier": "low", "total_results": 50, "yelp_popularity_score": 0.5,
        "pet_friendly": 0, "wheelchair_accessible": 1, "parking_nearby": 0,
        "photography_allowed": 1, "noise_level": "moderate", "reservation_required": 0,
        "outside_food_allowed": 0, "family_friendly": 1, "food_available": 1,
        "cuisine": "french", "city": "paris",
    }, **KW)
    tool_COMMIT(r["page_id"], **KW)
    return r["record_id"]


_t22_base_args = dict(
    affected_field="hours_fri",
    incorrect_value="20:00", correct_value="22:00",
    source_type="blog", wrong_info_category="temporal_decay",
    origin_story="Owner registered hours years ago and never updated.",
    db_path=TEST_DB,
)

# T22.1 — missing incorrect_source_doc_id → error
print("  T22.1: missing incorrect_source_doc_id → error")
_v = _t22_make_venue("t22-1")
_inc, _tc = _make_wi_doc_pair(_v, "t22_1")
r = tool_ADD_WRONG_INFO(venue_id=_v, truth_carrier_doc_id=_tc, **_t22_base_args)
check("T22.1: missing incorrect_source_doc_id rejected",
      r["status"] == "error" and "incorrect_source_doc_id is required" in r.get("message", ""),
      str(r)[:200])

# T22.2 — missing truth_carrier_doc_id → error
print("  T22.2: missing truth_carrier_doc_id → error")
r = tool_ADD_WRONG_INFO(venue_id=_v, incorrect_source_doc_id=_inc, **_t22_base_args)
check("T22.2: missing truth_carrier_doc_id rejected",
      r["status"] == "error" and "truth_carrier_doc_id is required" in r.get("message", ""),
      str(r)[:200])

# T22.3 — same doc_id for both roles → error
print("  T22.3: same doc_id for both roles → error")
r = tool_ADD_WRONG_INFO(venue_id=_v,
                        incorrect_source_doc_id=_inc, truth_carrier_doc_id=_inc,
                        **_t22_base_args)
check("T22.3: same doc_id for both roles rejected",
      r["status"] == "error" and "DIFFERENT" in r.get("message", ""),
      str(r)[:200])

# T22.4 — non-existent doc_id → error (specific message)
print("  T22.4: non-existent doc_id → error")
r = tool_ADD_WRONG_INFO(venue_id=_v,
                        incorrect_source_doc_id="DOES_NOT_EXIST",
                        truth_carrier_doc_id=_tc,
                        **_t22_base_args)
check("T22.4: non-existent doc_id rejected",
      r["status"] == "error"
      and "DOES_NOT_EXIST" in r.get("message", "")
      and "not found" in r.get("message", ""),
      str(r)[:200])

# T22.5 — draft (uncommitted) doc_id → error
print("  T22.5: draft (uncommitted) doc_id → error")
_v5 = _t22_make_venue("t22-5")
# CREATE_PAGE source_doc but don't COMMIT
_draft_pg = tool_CREATE_PAGE("source_doc", city="paris", venue_id=_v5, **KW)
_draft_did = _draft_pg["record_id"]
_inc5, _tc5 = _make_wi_doc_pair(_v5, "t22_5")
r = tool_ADD_WRONG_INFO(venue_id=_v5,
                        incorrect_source_doc_id=_draft_did,
                        truth_carrier_doc_id=_tc5,
                        **_t22_base_args)
check("T22.5: draft doc_id rejected",
      r["status"] == "error"
      and "must be 'committed' or 'verified'" in r.get("message", ""),
      str(r)[:200])

# T22.6 — happy path: creates wrong_info + both roles atomically
print("  T22.6: happy path")
_v6 = _t22_make_venue("t22-6")
_inc6, _tc6 = _make_wi_doc_pair(_v6, "t22_6")
r = tool_ADD_WRONG_INFO(venue_id=_v6,
                        incorrect_source_doc_id=_inc6,
                        truth_carrier_doc_id=_tc6,
                        **_t22_base_args)
check("T22.6a: happy path returns status=ok",
      r["status"] == "ok", str(r)[:200])
check("T22.6b: response includes both doc ids",
      r.get("incorrect_source_doc_id") == _inc6
      and r.get("truth_carrier_doc_id") == _tc6,
      str(r)[:200])

# Verify DB state: wrong_info row + 2 doc_venue_roles rows
_wi_id = r["wrong_info_id"]
conn = get_connection(TEST_DB)
n_wi = conn.execute("SELECT COUNT(*) FROM wrong_info WHERE wrong_info_id=?", (_wi_id,)).fetchone()[0]
n_roles = conn.execute(
    "SELECT COUNT(*) FROM doc_venue_roles WHERE wrong_info_id=?", (_wi_id,)
).fetchone()[0]
n_inc_role = conn.execute(
    "SELECT COUNT(*) FROM doc_venue_roles WHERE wrong_info_id=? AND role='incorrect_source'",
    (_wi_id,)
).fetchone()[0]
n_tc_role = conn.execute(
    "SELECT COUNT(*) FROM doc_venue_roles WHERE wrong_info_id=? AND role='truth_carrier'",
    (_wi_id,)
).fetchone()[0]
conn.close()
check("T22.6c: wrong_info row written", n_wi == 1)
check("T22.6d: 2 doc_venue_roles written", n_roles == 2)
check("T22.6e: incorrect_source role exists", n_inc_role == 1)
check("T22.6f: truth_carrier role exists", n_tc_role == 1)

# T22.7 — doc already truth_carrier for a DIFFERENT wrong_info → reject
print("  T22.7: doc already plays a wrong_info role → reject")
# _tc6 is now truth_carrier for the wrong_info created in T22.6.
# Try to use it again for a NEW wrong_info on the same venue.
_inc7, _tc7 = _make_wi_doc_pair(_v6, "t22_7")
r = tool_ADD_WRONG_INFO(
    venue_id=_v6, affected_field="hours_sat",
    incorrect_value="20:00", correct_value="22:00",
    source_type="blog", wrong_info_category="temporal_decay",
    origin_story="Owner registered hours years ago and never updated.",
    incorrect_source_doc_id=_inc7,
    truth_carrier_doc_id=_tc6,    # already a truth_carrier!
    db_path=TEST_DB,
)
check("T22.7: doc reuse across wrong_info entries rejected",
      r["status"] == "error" and "already registered" in r.get("message", ""),
      str(r)[:200])

# T22.8 — upgrade: doc was 'neutral' → becomes incorrect_source
print("  T22.8: neutral → incorrect_source upgrade")
_v8 = _t22_make_venue("t22-8")
_inc8, _tc8 = _make_wi_doc_pair(_v8, "t22_8")
# Register the future incorrect_source as neutral first
tool_REGISTER_DOC_REFS(_inc8, _v8, "neutral", **KW)
# Now call ADD_WRONG_INFO with that doc as incorrect_source
r = tool_ADD_WRONG_INFO(venue_id=_v8,
                        incorrect_source_doc_id=_inc8,
                        truth_carrier_doc_id=_tc8,
                        **_t22_base_args)
check("T22.8: neutral doc upgraded to incorrect_source",
      r["status"] == "ok", str(r)[:200])
conn = get_connection(TEST_DB)
final_role = conn.execute(
    "SELECT role FROM doc_venue_roles WHERE doc_id=? AND venue_id=?",
    (_inc8, _v8)
).fetchone()
conn.close()
check("T22.8b: role updated to incorrect_source", final_role["role"] == "incorrect_source")

# T22.9 — Two wrong_info entries on same venue (the NYC failure mode)
print("  T22.9: two wrong_info entries on same venue both atomic")
_v9 = _t22_make_venue("t22-9")
_inc9a, _tc9a = _make_wi_doc_pair(_v9, "t22_9a")
_inc9b, _tc9b = _make_wi_doc_pair(_v9, "t22_9b")
r1 = tool_ADD_WRONG_INFO(venue_id=_v9, affected_field="hours_fri",
                         incorrect_value="20:00", correct_value="22:00",
                         source_type="blog", wrong_info_category="temporal_decay",
                         origin_story="Owner registered hours years ago.",
                         incorrect_source_doc_id=_inc9a, truth_carrier_doc_id=_tc9a,
                         db_path=TEST_DB)
r2 = tool_ADD_WRONG_INFO(venue_id=_v9, affected_field="hours_sat",
                         incorrect_value="20:00", correct_value="22:00",
                         source_type="blog", wrong_info_category="temporal_decay",
                         origin_story="Owner registered hours years ago.",
                         incorrect_source_doc_id=_inc9b, truth_carrier_doc_id=_tc9b,
                         db_path=TEST_DB)
check("T22.9a: 1st wrong_info → ok", r1["status"] == "ok")
check("T22.9b: 2nd wrong_info → ok", r2["status"] == "ok")
# Both wrong_info entries should have both roles registered (no orphans)
conn = get_connection(TEST_DB)
n_orphan = conn.execute("""
    SELECT COUNT(*) FROM wrong_info w WHERE w.venue_id = ? AND (
       NOT EXISTS (SELECT 1 FROM doc_venue_roles WHERE wrong_info_id = w.wrong_info_id AND role = 'truth_carrier')
       OR NOT EXISTS (SELECT 1 FROM doc_venue_roles WHERE wrong_info_id = w.wrong_info_id AND role = 'incorrect_source')
    )
""", (_v9,)).fetchone()[0]
conn.close()
check("T22.9c: zero orphaned wrong_info entries on venue (NYC failure mode prevented)",
      n_orphan == 0)

# T22.10 — VERIFY's truth_carrier/incorrect_source_registered checks pass
print("  T22.10: VERIFY checks pass for atomically-registered venue")
from scripts.generation.agent_tools import (
    _check_truth_carrier_registered, _check_incorrect_source_registered,
)
conn = get_connection(TEST_DB)
errs_tc = _check_truth_carrier_registered(conn, _v9)
errs_inc = _check_incorrect_source_registered(conn, _v9)
conn.close()
check("T22.10a: _check_truth_carrier_registered passes", len(errs_tc) == 0, str(errs_tc))
check("T22.10b: _check_incorrect_source_registered passes", len(errs_inc) == 0, str(errs_inc))


# ─── Cleanup ─────────────────────────────────────────────────────────────────
TEST_DB.unlink()

passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("✅ All A3+A4 tests passed — Agent tools and VERIFY checks ready")
else:
    print("❌ Some tests failed — fix before proceeding to A5")
    sys.exit(1)
